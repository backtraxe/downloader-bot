# -*- coding: utf-8 -*-
"""core.downloader.py 纯函数单元测试（无网络依赖）"""
import os

import pytest

from core.downloader import build_ydl_opts, resolve_uploader


# ---------------- resolve_uploader ----------------

class TestResolveUploader:
    def test_normal(self):
        assert resolve_uploader({"uploader": "某UP主"}) == "某UP主"

    def test_missing_returns_empty(self):
        # uploader 缺失 → 空串（调用方据此不加作者层）
        assert resolve_uploader({}) == ""
        assert resolve_uploader(None) == ""

    def test_empty_returns_empty(self):
        assert resolve_uploader({"uploader": ""}) == ""
        assert resolve_uploader({"uploader": None}) == ""

    def test_strips_illegal_chars(self):
        # 作者名含路径分隔符与 Windows 保留字符，应被清洗
        assert resolve_uploader({"uploader": "../etc"}) == "..etc"
        assert resolve_uploader({"uploader": 'a:*?"b'}) == "ab"


# ---------------- build_ydl_opts (author 层) ----------------

class TestBuildYdlOptsAuthor:
    def test_outtmpl_no_author(self):
        opts = build_ydl_opts("bilibili")
        assert opts["outtmpl"] == os.path.join("download", "bilibili", "%(title).200B.%(ext)s")

    def test_outtmpl_empty_author_falls_back(self):
        # author="" → 退化为无作者层，不产生 NA/ 或 unknown/
        opts = build_ydl_opts("bilibili", author="")
        assert opts["outtmpl"] == os.path.join("download", "bilibili", "%(title).200B.%(ext)s")

    def test_outtmpl_with_author(self):
        opts = build_ydl_opts("bilibili", author="某UP")
        assert opts["outtmpl"] == os.path.join(
            "download", "bilibili", "某UP", "%(title).200B.%(ext)s")

    def test_cookiefile_none_when_no_cookie_file(self, tmp_path, monkeypatch):
        # cookie 文件不存在 → 不传 cookiefile，避免 yt-dlp 警告
        from core.sites import cookie_file_for
        monkeypatch.setattr("core.downloader.cookie_file_for", lambda s: str(tmp_path / "absent.txt"))
        opts = build_ydl_opts("bilibili")
        assert opts["cookiefile"] is None

    def test_merge_output_format_mp4(self):
        assert build_ydl_opts("youtube")["merge_output_format"] == "mp4"


# ---------------- dispatch_url 路由 ----------------

class TestDispatchUrl:
    """dispatch_url 按 site_name 分发到对应下载器，不触网。"""

    def test_xiaohongshu_routes_to_xhs(self, monkeypatch):
        import core.downloader as downloader

        calls = []
        monkeypatch.setattr("core.sites.load_cookie_for_url", lambda url: "fake_cookie")
        monkeypatch.setattr(
            "core.xhs_downloader.download_xhs_media",
            lambda url, cookie: calls.append(("xhs", url, cookie)),
        )
        downloader.dispatch_url("https://www.xiaohongshu.com/explore/abc")
        assert calls == [("xhs", "https://www.xiaohongshu.com/explore/abc", "fake_cookie")]

    def test_xiaohongshu_no_cookie_skips_download(self, monkeypatch):
        import core.downloader as downloader

        calls = []
        monkeypatch.setattr("core.sites.load_cookie_for_url", lambda url: None)
        monkeypatch.setattr(
            "core.xhs_downloader.download_xhs_media",
            lambda url, cookie: calls.append(("xhs", url, cookie)),
        )
        # Cookie 为空时不应调用 xhs 下载
        downloader.dispatch_url("https://xhslink.com/abc")
        assert calls == []

    @pytest.mark.parametrize("url", [
        "https://www.youtube.com/watch?v=abc",
        "https://www.bilibili.com/video/BV1xx",
        "https://www.instagram.com/p/Cabc/",
        "https://x.com/user/status/123",
        "https://www.douyin.com/user/MS4wLjA",  # douyin 非作品页仍兜底 yt-dlp
    ])
    def test_ytdlp_sites_route_to_download_url(self, url, monkeypatch):
        import core.downloader as downloader

        calls = []
        monkeypatch.setattr("core.downloader.download_url", lambda u: calls.append(u))
        downloader.dispatch_url(url)
        assert calls == [url]

    def test_unknown_site_routes_to_1024(self, monkeypatch):
        import core.downloader as downloader

        calls = []
        monkeypatch.setattr(
            "core.1024_downloader.extract_general_media",
            lambda url: calls.append(url),
        )
        downloader.dispatch_url("https://example.com/some/page")
        assert calls == ["https://example.com/some/page"]


class TestDiagnoseError:
    """diagnose_error 区分登录、瞬时、未知三类。"""

    def test_login_hint(self):
        from core.downloader import diagnose_error
        kind, _ = diagnose_error("Login required to access this video")
        assert kind == "need_login"

    def test_transient_ssl_eof(self):
        from core.downloader import diagnose_error
        kind, _ = diagnose_error("Got error: SSL: UNEXPECTED_EOF_WHILE_READING EOF occurred in violation of protocol")
        assert kind == "transient"

    def test_transient_connection_reset(self):
        from core.downloader import diagnose_error
        kind, _ = diagnose_error("Connection reset by peer")
        assert kind == "transient"

    def test_transient_timeout(self):
        from core.downloader import diagnose_error
        kind, _ = diagnose_error("Read timed out")
        assert kind == "transient"

    def test_unknown(self):
        from core.downloader import diagnose_error
        kind, msg = diagnose_error("some weird error")
        assert kind == "unknown"
        assert msg == "some weird error"

    def test_douyin_argus_block_not_cookie_expired(self):
        # yt-dlp DouyinIE 调 aweme/v1/web/aweme/detail/ 无 a_bogus 签名被
        # Argus 风控 403 时抛 "Fresh cookies ... are needed"，文案像 Cookie
        # 过期，实际是视频详情接口的系统性拦截——必须翻译成明确提示，
        # 防止用户白刷 Cookie（2026-10 实测，刷新 Cookie 无效）
        from core.downloader import diagnose_error
        kind, msg = diagnose_error(
            "[Douyin] 7556303777536740671: Fresh cookies (not necessarily "
            "logged in) are needed; please report this issue on "
            "https://github.com/yt-dlp/yt-dlp/issues"
        )
        assert kind == "blocked"
        assert "Cookie 过期" in msg or "风控" in msg


# ---------------- resolve_short_link ----------------

class TestResolveShortLink:
    """resolve_short_link 对非短链直接返回，对短链用 http_client 展开。"""

    def test_non_short_link_returned_as_is(self):
        from core.downloader import resolve_short_link
        # 非短链域名不产生请求，原样返回
        assert resolve_short_link("https://www.bilibili.com/video/BV1xx") == \
            "https://www.bilibili.com/video/BV1xx"

    def test_non_short_link_youtube(self):
        from core.downloader import resolve_short_link
        assert resolve_short_link("https://www.youtube.com/watch?v=abc") == \
            "https://www.youtube.com/watch?v=abc"

    def test_short_link_resolved(self, monkeypatch):
        from core.downloader import resolve_short_link

        # 模拟 http_client.get 返回一个跟随重定向后的响应
        class FakeResp:
            url = "https://www.bilibili.com/video/BV1WmgC6xEic"
            status_code = 200
            headers = {}

        monkeypatch.setattr("core.http_client.get", lambda url, **kw: FakeResp())
        result = resolve_short_link("https://b23.tv/wR0HJVE")
        assert result == "https://www.bilibili.com/video/BV1WmgC6xEic"

    def test_short_link_302_manual_redirect(self, monkeypatch):
        from core.downloader import resolve_short_link

        # 模拟系统 curl 降级模式：返回 302 + Location
        class FakeResp:
            url = "https://b23.tv/wR0HJVE"
            status_code = 302
            headers = {"location": "https://www.bilibili.com/video/BV1xx"}

        monkeypatch.setattr("core.http_client.get", lambda url, **kw: FakeResp())
        result = resolve_short_link("https://b23.tv/wR0HJVE")
        assert result == "https://www.bilibili.com/video/BV1xx"

    def test_short_link_resolve_failure_returns_original(self, monkeypatch):
        from core.downloader import resolve_short_link

        # http_client.get 抛异常时回退原 URL
        def boom(url, **kw):
            raise ConnectionError("DNS failed")

        monkeypatch.setattr("core.http_client.get", boom)
        result = resolve_short_link("https://b23.tv/wR0HJVE")
        assert result == "https://b23.tv/wR0HJVE"

    def test_short_link_same_url_returns_original(self, monkeypatch):
        from core.downloader import resolve_short_link

        # 短链但最终 URL 没变（无重定向）
        class FakeResp:
            url = "https://b23.tv/wR0HJVE"
            status_code = 200
            headers = {}

        monkeypatch.setattr("core.http_client.get", lambda url, **kw: FakeResp())
        result = resolve_short_link("https://b23.tv/wR0HJVE")
        assert result == "https://b23.tv/wR0HJVE"


class TestDispatchDouyinNoteRouting:
    """douyin 图文笔记（/note/）应路由到 douyin_downloader 手写解析，不进 yt-dlp。"""

    def test_note_url_routes_to_douyin_downloader(self, monkeypatch):
        import core.downloader as downloader

        calls = []
        monkeypatch.setattr("core.sites.load_cookie_for_url", lambda url: "raw_cookie")
        monkeypatch.setattr(
            "core.douyin_downloader.download_douyin_note",
            lambda url, cookie: calls.append(("note", url, cookie)),
        )
        downloader.dispatch_url("https://www.douyin.com/note/7666381907030529443")
        # 原始文件内容（可能是 Netscape）经 cookie_text_to_header 转换为请求头
        assert calls == [("note", "https://www.douyin.com/note/7666381907030529443", "raw_cookie")]

    def test_note_url_without_cookie_skips_download(self, monkeypatch):
        import core.downloader as downloader

        calls = []
        monkeypatch.setattr("core.sites.load_cookie_for_url", lambda url: None)
        monkeypatch.setattr(
            "core.douyin_downloader.download_douyin_note",
            lambda url, cookie: calls.append(("note", url, cookie)),
        )
        downloader.dispatch_url("https://www.douyin.com/note/7666381907030529443")
        assert calls == []

    def test_video_url_routes_to_video_parser(self, monkeypatch):
        # /video/ 走签名 detail API 的手写视频解析器
        # （yt-dlp DouyinIE 2026-10 被 Argus 风控签名拦截，详见 README）
        import core.downloader as downloader

        video_calls, note_calls, ytdlp_calls = [], [], []
        monkeypatch.setattr("core.sites.load_cookie_for_url", lambda url: "raw_cookie")
        monkeypatch.setattr(
            "core.douyin_downloader.download_douyin_video",
            lambda url, cookie: video_calls.append((url, cookie)),
        )
        monkeypatch.setattr(
            "core.douyin_downloader.download_douyin_note",
            lambda url, cookie: note_calls.append(url),
        )
        monkeypatch.setattr("core.downloader.download_url", lambda u: ytdlp_calls.append(u))
        downloader.dispatch_url("https://www.douyin.com/video/7666381907030529443")
        assert video_calls == [
            ("https://www.douyin.com/video/7666381907030529443", "raw_cookie")]
        assert note_calls == [] and ytdlp_calls == []

    def test_video_url_without_cookie_skips_download(self, monkeypatch):
        import core.downloader as downloader

        video_calls = []
        monkeypatch.setattr("core.sites.load_cookie_for_url", lambda url: None)
        monkeypatch.setattr(
            "core.douyin_downloader.download_douyin_video",
            lambda url, cookie: video_calls.append(url),
        )
        downloader.dispatch_url("https://www.douyin.com/video/7666381907030529443")
        assert video_calls == []


class TestResolveShortLinkWithRetry:
    """v.douyin.com 偶发返回 200 而非 302，展开需带重试（21:19 案例）。"""

    def test_retries_until_expanded(self, monkeypatch):
        from core.downloader import resolve_short_link_with_retry

        calls = iter([
            "https://v.douyin.com/abc/",                      # 第 1 次无重定向
            "https://v.douyin.com/abc/",                      # 第 2 次仍无
            "https://www.douyin.com/note/123",                # 第 3 次成功
        ])
        monkeypatch.setattr("core.downloader.resolve_short_link", lambda u: next(calls))
        monkeypatch.setattr("core.downloader.time.sleep", lambda s: None)
        out = resolve_short_link_with_retry("https://v.douyin.com/abc/")
        assert out == "https://www.douyin.com/note/123"

    def test_gives_up_after_max_attempts(self, monkeypatch):
        from core.downloader import resolve_short_link_with_retry

        calls = []
        def fake_resolve(u):
            calls.append(u)
            return u
        monkeypatch.setattr("core.downloader.resolve_short_link", fake_resolve)
        monkeypatch.setattr("core.downloader.time.sleep", lambda s: None)
        out = resolve_short_link_with_retry("https://v.douyin.com/abc/")
        # 当作未处理 URL 原样返回（由调用方降级）
        assert out == "https://v.douyin.com/abc/"
        assert len(calls) == 3

    def test_non_short_link_no_request(self, monkeypatch):
        from core.downloader import resolve_short_link_with_retry

        calls = []
        monkeypatch.setattr("core.downloader.resolve_short_link", lambda u: calls.append(u) or u)
        out = resolve_short_link_with_retry("https://www.douyin.com/note/123")
        assert out == "https://www.douyin.com/note/123"
        assert calls == []
