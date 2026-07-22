# -*- coding: utf-8 -*-
"""downloader.py 纯函数单元测试（无网络依赖）"""
import os

import pytest

from downloader import build_ydl_opts, resolve_uploader


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
        from sites import cookie_file_for
        monkeypatch.setattr("downloader.cookie_file_for", lambda s: str(tmp_path / "absent.txt"))
        opts = build_ydl_opts("bilibili")
        assert opts["cookiefile"] is None

    def test_merge_output_format_mp4(self):
        assert build_ydl_opts("youtube")["merge_output_format"] == "mp4"


# ---------------- dispatch_url 路由 ----------------

class TestDispatchUrl:
    """dispatch_url 按 site_name 分发到对应下载器，不触网。"""

    def test_xiaohongshu_routes_to_xhs(self, monkeypatch):
        import downloader

        calls = []
        monkeypatch.setattr("sites.load_cookie_for_url", lambda url: "fake_cookie")
        monkeypatch.setattr(
            "xhs_downloader.download_xhs_media",
            lambda url, cookie: calls.append(("xhs", url, cookie)),
        )
        downloader.dispatch_url("https://www.xiaohongshu.com/explore/abc")
        assert calls == [("xhs", "https://www.xiaohongshu.com/explore/abc", "fake_cookie")]

    def test_xiaohongshu_no_cookie_skips_download(self, monkeypatch):
        import downloader

        calls = []
        monkeypatch.setattr("sites.load_cookie_for_url", lambda url: None)
        monkeypatch.setattr(
            "xhs_downloader.download_xhs_media",
            lambda url, cookie: calls.append(("xhs", url, cookie)),
        )
        # Cookie 为空时不应调用 xhs 下载
        downloader.dispatch_url("https://xhslink.com/abc")
        assert calls == []

    @pytest.mark.parametrize("url", [
        "https://www.youtube.com/watch?v=abc",
        "https://www.bilibili.com/video/BV1xx",
        "https://www.douyin.com/video/123",
        "https://www.instagram.com/p/Cabc/",
        "https://x.com/user/status/123",
    ])
    def test_ytdlp_sites_route_to_download_url(self, url, monkeypatch):
        import downloader

        calls = []
        monkeypatch.setattr("downloader.download_url", lambda u: calls.append(u))
        downloader.dispatch_url(url)
        assert calls == [url]

    def test_unknown_site_routes_to_1024(self, monkeypatch):
        import downloader

        calls = []
        monkeypatch.setattr(
            "1024_downloader.extract_general_media",
            lambda url: calls.append(url),
        )
        downloader.dispatch_url("https://example.com/some/page")
        assert calls == ["https://example.com/some/page"]


class TestDiagnoseError:
    """diagnose_error 区分登录、瞬时、未知三类。"""

    def test_login_hint(self):
        from downloader import diagnose_error
        kind, _ = diagnose_error("Login required to access this video")
        assert kind == "need_login"

    def test_transient_ssl_eof(self):
        from downloader import diagnose_error
        kind, _ = diagnose_error("Got error: SSL: UNEXPECTED_EOF_WHILE_READING EOF occurred in violation of protocol")
        assert kind == "transient"

    def test_transient_connection_reset(self):
        from downloader import diagnose_error
        kind, _ = diagnose_error("Connection reset by peer")
        assert kind == "transient"

    def test_transient_timeout(self):
        from downloader import diagnose_error
        kind, _ = diagnose_error("Read timed out")
        assert kind == "transient"

    def test_unknown(self):
        from downloader import diagnose_error
        kind, msg = diagnose_error("some weird error")
        assert kind == "unknown"
        assert msg == "some weird error"


# ---------------- resolve_short_link ----------------

class TestResolveShortLink:
    """resolve_short_link 对非短链直接返回，对短链用 http_client 展开。"""

    def test_non_short_link_returned_as_is(self):
        from downloader import resolve_short_link
        # 非短链域名不产生请求，原样返回
        assert resolve_short_link("https://www.bilibili.com/video/BV1xx") == \
            "https://www.bilibili.com/video/BV1xx"

    def test_non_short_link_youtube(self):
        from downloader import resolve_short_link
        assert resolve_short_link("https://www.youtube.com/watch?v=abc") == \
            "https://www.youtube.com/watch?v=abc"

    def test_short_link_resolved(self, monkeypatch):
        from downloader import resolve_short_link

        # 模拟 http_client.get 返回一个跟随重定向后的响应
        class FakeResp:
            url = "https://www.bilibili.com/video/BV1WmgC6xEic"
            status_code = 200
            headers = {}

        monkeypatch.setattr("http_client.get", lambda url, **kw: FakeResp())
        result = resolve_short_link("https://b23.tv/wR0HJVE")
        assert result == "https://www.bilibili.com/video/BV1WmgC6xEic"

    def test_short_link_302_manual_redirect(self, monkeypatch):
        from downloader import resolve_short_link

        # 模拟系统 curl 降级模式：返回 302 + Location
        class FakeResp:
            url = "https://b23.tv/wR0HJVE"
            status_code = 302
            headers = {"location": "https://www.bilibili.com/video/BV1xx"}

        monkeypatch.setattr("http_client.get", lambda url, **kw: FakeResp())
        result = resolve_short_link("https://b23.tv/wR0HJVE")
        assert result == "https://www.bilibili.com/video/BV1xx"

    def test_short_link_resolve_failure_returns_original(self, monkeypatch):
        from downloader import resolve_short_link

        # http_client.get 抛异常时回退原 URL
        def boom(url, **kw):
            raise ConnectionError("DNS failed")

        monkeypatch.setattr("http_client.get", boom)
        result = resolve_short_link("https://b23.tv/wR0HJVE")
        assert result == "https://b23.tv/wR0HJVE"

    def test_short_link_same_url_returns_original(self, monkeypatch):
        from downloader import resolve_short_link

        # 短链但最终 URL 没变（无重定向）
        class FakeResp:
            url = "https://b23.tv/wR0HJVE"
            status_code = 200
            headers = {}

        monkeypatch.setattr("http_client.get", lambda url, **kw: FakeResp())
        result = resolve_short_link("https://b23.tv/wR0HJVE")
        assert result == "https://b23.tv/wR0HJVE"
