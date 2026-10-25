# -*- coding: utf-8 -*-
"""1024_downloader.py 纯函数/可注入会话单元测试（无网络依赖）"""

import importlib
import json
import os

import pytest

from core.result import DownloadStatus

mod = importlib.import_module("core.1024_downloader")


# ---------------- _is_block_status ----------------


class TestIsBlockStatus:
    @pytest.mark.parametrize("code", [555, 556, 557, 567, 429, 500, 502, 503])
    def test_retryable(self, code):
        # WAF 非标拦截码与常见 5xx/限流应判定为可重试
        assert mod._is_block_status(code) is True

    @pytest.mark.parametrize("code", [200, 301, 400, 401, 403, 404])
    def test_not_retryable(self, code):
        assert mod._is_block_status(code) is False


# ---------------- _is_decorative_name ----------------


class TestIsDecorativeName:
    @pytest.mark.parametrize(
        "name",
        [
            "header-logo.png",
            "69_avatar_middle.jpg",
            "weixin_qrcode.png",
            "coolapk_emotion_1017_houhouhou.png",
            "beian.png",
            "app_icon.png",
            "LOGO.PNG",  # 大小写不敏感
        ],
    )
    def test_decorative(self, name):
        assert mod._is_decorative_name(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "38813669_98682f5d_6765_3739_311@2494x3325.jpg.m.jpg",
            "photo.jpg",
            "IMG_2024.png",
            "video.mp4",
            # 装饰关键词作为更长单词的子串不得误杀（icon→iconic、avatar→avatare）
            "iconic_photo.jpg",
            "icondesign_2024.jpg",
            "avatare.jpg",
        ],
    )
    def test_content(self, name):
        assert mod._is_decorative_name(name) is False


# ---------------- _looks_like_page_url ----------------


class TestLooksLikePageUrl:
    """A 标签里以媒体后缀结尾的 URL 可能是网页：下载只会得到 HTML。"""

    @pytest.mark.parametrize(
        "url",
        [
            "https://commons.wikimedia.org/wiki/File:Example.jpg",
            "https://commons.wikimedia.org/wiki/File_talk:Example.jpg",
            "https://commons.wikimedia.org/wiki/Special:WhatLinksHere/File:Example.jpg",
            "https://commons.wikimedia.org/w/index.php?title=Special:CreateAccount&returnto=File%3AExample.jpg",
            "https://example.com/article?title=photo.jpg",
        ],
    )
    def test_page_urls(self, url):
        assert mod._looks_like_page_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://cdn.example.com/photo.jpg",
            "https://a.com/direct.png",
            "https://a.com/path/with:colon/photo.jpg",  # 冒号不在末段
            "https://a.com/photo.jpg?v=2",
        ],
    )
    def test_direct_media(self, url):
        assert mod._looks_like_page_url(url) is False


# ---------------- download_file 装饰小图过滤 ----------------


class _FakeImageResp:
    def __init__(self, body, content_type="image/jpeg"):
        self.status_code = 200
        self.headers = {"Content-Type": content_type}
        self._body = body

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=8192):
        yield self._body


class TestDownloadFileDecorFilter:
    def _fake_session(self, body, content_type="image/jpeg"):
        class S:
            def get(self, url, **kw):
                return _FakeImageResp(body, content_type)

        return S()

    def test_small_image_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        target = str(tmp_path / "tiny.png")
        result = mod.download_file(
            self._fake_session(b"x" * 100), "http://img/tiny.png", target, "http://page"
        )
        assert "[跳过]" in result
        assert not os.path.exists(target)

    def test_large_image_kept(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        target = str(tmp_path / "big.jpg")
        result = mod.download_file(
            self._fake_session(b"x" * 30000),
            "http://img/big.jpg",
            target,
            "http://page",
        )
        assert "[成功]" in result
        assert os.path.exists(target)

    def test_small_video_not_filtered(self, tmp_path, monkeypatch):
        # 视频不做体积过滤——短视频也可能很小
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        target = str(tmp_path / "clip.mp4")
        result = mod.download_file(
            self._fake_session(b"x" * 100, "video/mp4"),
            "http://img/clip.mp4",
            target,
            "http://page",
        )
        assert "[成功]" in result
        assert os.path.exists(target)

    def test_small_dimensions_skipped(self, tmp_path, monkeypatch):
        # 尺寸可解析时按尺寸过滤：100x100 头像即使文件大于 20KB 也是装饰图
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        from tests.test_utils import _make_png

        png = _make_png(100, 100) + b"x" * 30000  # 填充使其超过 20KB
        target = str(tmp_path / "small-dims.png")  # 中性文件名，确保走尺寸判定
        result = mod.download_file(
            self._fake_session(png, "image/png"),
            "http://img/photo.png",
            target,
            "http://page",
        )
        assert "[跳过]" in result
        assert not os.path.exists(target)

    def test_existing_file_skipped(self, tmp_path, monkeypatch):
        # 重下幂等：目标文件已存在直接跳过，不再产生 _1 副本也不发请求
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        target = str(tmp_path / "001.jpg")
        with open(target, "wb") as f:
            f.write(b"old")

        class S:
            calls = 0

            def get(self, url, **kw):
                self.calls += 1
                raise AssertionError("不应发起网络请求")

        sess = S()
        result = mod.download_file(sess, "http://img/001.jpg", target, "http://page")
        assert sess.calls == 0
        assert "[跳过]" in result
        with open(target, "rb") as f:
            assert f.read() == b"old"  # 原文件未被动

    def test_large_dimensions_tiny_file_kept(self, tmp_path, monkeypatch):
        # 反向用例：1080x1440 照片即使是高压缩小文件（<20KB）也不该删
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        from tests.test_utils import _make_png

        png = _make_png(1080, 1440)  # 只有几十字节
        target = str(tmp_path / "photo.png")
        result = mod.download_file(
            self._fake_session(png, "image/png"),
            "http://img/photo.png",
            target,
            "http://page",
        )
        assert "[成功]" in result
        assert os.path.exists(target)


# ---------------- 装饰图跳过标记 ----------------


class TestDownloadFileSkipMarker:
    """被删除的装饰图没有产物，需用隐藏标记避免每次重跑都重新下载。"""

    def _fake_session(self, body, content_type="image/jpeg"):
        class S:
            calls = 0

            def get(self, url, **kw):
                self.calls += 1
                return _FakeImageResp(body, content_type)

        return S()

    def test_small_image_marker_written_and_rerun_skips(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        target = str(tmp_path / "001.jpg")
        sess = self._fake_session(b"x" * 100)
        result = mod.download_file(sess, "http://img/001.jpg", target, "http://page")
        assert "[跳过]" in result
        assert not os.path.exists(target)
        marker = tmp_path / ".001.skip"
        assert marker.read_text(encoding="utf-8") == "http://img/001.jpg"

        class NoRequest:
            calls = 0

            def get(self, url, **kw):
                self.calls += 1
                raise AssertionError("不应发起网络请求")

        sess2 = NoRequest()
        result = mod.download_file(sess2, "http://img/001.jpg", target, "http://page")
        assert "此前已判定为装饰图" in result
        assert sess2.calls == 0

    def test_skip_marker_for_other_url_retries(self, tmp_path, monkeypatch):
        # 同一序号换了新图：旧标记失效，应重新下载并清除标记
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        target = str(tmp_path / "001.jpg")
        marker = tmp_path / ".001.skip"
        marker.write_text("http://img/old.jpg", encoding="utf-8")
        sess = self._fake_session(b"x" * 30000)
        result = mod.download_file(sess, "http://img/new.jpg", target, "http://page")
        assert "[成功]" in result
        assert os.path.exists(target)
        assert not marker.exists()

    def test_force_ignores_skip_marker_and_clears_it(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        target = str(tmp_path / "001.jpg")
        marker = tmp_path / ".001.skip"
        marker.write_text("http://img/001.jpg", encoding="utf-8")
        sess = self._fake_session(b"x" * 30000)
        result = mod.download_file(
            sess, "http://img/001.jpg", target, "http://page", force=True
        )
        assert "[成功]" in result
        assert os.path.exists(target)
        assert not marker.exists()

    def test_force_decorative_updates_marker_url(self, tmp_path, monkeypatch):
        # force 重下后仍是装饰图：标记应更新为当前 URL，避免旧 URL 误命中
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        target = str(tmp_path / "001.jpg")
        marker = tmp_path / ".001.skip"
        marker.write_text("http://img/old.jpg", encoding="utf-8")
        sess = self._fake_session(b"x" * 100)
        result = mod.download_file(
            sess, "http://img/new.jpg", target, "http://page", force=True
        )
        assert "[跳过]" in result
        assert marker.read_text(encoding="utf-8") == "http://img/new.jpg"


# ---------------- download_file --force ----------------


class TestDownloadFileForce:
    """--force 强制重下：跳过幂等判断、覆盖旧文件、失败不破坏旧产物。"""

    def _fake_session(self, body, content_type="image/jpeg"):
        class S:
            def get(self, url, **kw):
                return _FakeImageResp(body, content_type)

        return S()

    def _boom_session(self):
        class S:
            def get(self, url, **kw):
                raise RuntimeError("boom")

        return S()

    def test_force_overwrites_existing_no_dup(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        target = str(tmp_path / "001.jpg")
        with open(target, "wb") as f:
            f.write(b"old")
        result = mod.download_file(
            self._fake_session(b"new" * 10000),
            "http://img/001.jpg",
            target,
            "http://page",
            force=True,
        )
        assert "[成功]" in result
        with open(target, "rb") as f:
            assert f.read() == b"new" * 10000
        # 无 .part 残留、无 _1 副本，目录里只有覆盖后的那一个文件
        assert sorted(p.name for p in tmp_path.iterdir()) == ["001.jpg"]

    def test_force_failure_preserves_existing(self, tmp_path, monkeypatch):
        # 强制重下中途失败：旧文件保持原样（.part 临时文件被清理）
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        target = str(tmp_path / "001.jpg")
        with open(target, "wb") as f:
            f.write(b"old")
        result = mod.download_file(
            self._boom_session(),
            "http://img/001.jpg",
            target,
            "http://page",
            force=True,
        )
        assert "[失败]" in result
        with open(target, "rb") as f:
            assert f.read() == b"old"
        assert not os.path.exists(target + ".part")

    def test_stale_part_not_treated_as_downloaded(self, tmp_path, monkeypatch):
        # 强制重下被强杀后残留的 .part 不应让普通重跑误判"已下载"
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        target = str(tmp_path / "001.jpg")
        with open(target + ".part", "wb") as f:
            f.write(b"partial")
        result = mod.download_file(
            self._fake_session(b"x" * 30000),
            "http://img/001.jpg",
            target,
            "http://page",
        )
        assert "[成功]" in result
        assert os.path.getsize(target) == 30000


class TestDownloadFileTransientRetry:
    """图床瞬断按瞬时网络错误退避重试——2026-10 Termux 实测：23img.com
    的 TLS 握手被 CDN 间歇性拒绝（curl 35 Connection closed abruptly），
    5 并发下约 8 成图片单次尝试即放弃；瞬时错误重试即可恢复。"""

    _SSL_ERR = RuntimeError(
        "Failed to perform, curl: (35) BoringSSL SSL_connect: "
        "Connection closed abruptly (SSL_ERROR_SYSCALL; error queue empty) "
        "in connection to 23img.com:443"
    )

    def test_transient_ssl_error_retried_until_success(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        err = self._SSL_ERR
        calls = []

        class S:
            def get(self, url, **kw):
                calls.append(url)
                if len(calls) < 3:
                    raise err
                return _FakeImageResp(b"x" * 30000, "image/jpeg")

        target = str(tmp_path / "001.jpg")
        result = mod.download_file(S(), "http://img/001.jpg", target, "http://page")
        assert "[成功]" in result
        assert len(calls) == 3
        assert os.path.exists(target)

    def test_permanent_error_fails_without_retry(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        calls = []

        class S:
            def get(self, url, **kw):
                calls.append(url)
                raise RuntimeError("disk quota exceeded")

        target = str(tmp_path / "001.jpg")
        result = mod.download_file(S(), "http://img/001.jpg", target, "http://page")
        assert "[失败]" in result
        assert len(calls) == 1

    def test_partial_file_removed_when_all_retries_fail(self, tmp_path, monkeypatch):
        # 流式写入中途断开：重试耗尽后不留半截文件，否则下次重跑会被
        # 幂等判断误当成"已存在"跳过，产物永久缺一张
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        calls = []

        class _BrokenStream:
            def __init__(self):
                self.status_code = 200
                self.headers = {"Content-Type": "image/jpeg"}

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size=8192):
                yield b"x" * 8192
                raise TestDownloadFileTransientRetry._SSL_ERR

        class S:
            def get(self, url, **kw):
                calls.append(url)
                return _BrokenStream()

        target = str(tmp_path / "001.jpg")
        result = mod.download_file(S(), "http://img/001.jpg", target, "http://page")
        assert "[失败]" in result
        assert len(calls) == 5  # 默认重试 5 次
        assert list(tmp_path.iterdir()) == []  # 无半截文件、无 _1 副本


# ---------------- make_page_dir_name ----------------


class TestMakePageDirName:
    def test_with_numeric_id(self):
        # 目录 = 标题_ID，撞名时可区分、可溯源
        out = mod.make_page_dir_name(
            "发点存货", "https://www.coolapk.com/feed/74208176?s=x"
        )
        assert out == "发点存货_74208176"

    def test_without_numeric_id(self):
        out = mod.make_page_dir_name("某页面", "https://example.com/about")
        assert out == "某页面"

    def test_empty_title_falls_back(self):
        out = mod.make_page_dir_name("", "https://example.com/x")
        assert out == "未命名网页"

    def test_long_title_not_hard_truncated(self):
        # 不得再用 [:20] 硬截断（旧行为会把 30 字标题砍半）
        t = "这是一个非常非常长的页面标题" * 3
        out = mod.make_page_dir_name(t, "https://example.com/x")
        assert len(out) > 20
        assert len(out) <= 80  # 走 sanitize_filename 默认 80 上限


# ---------------- collect_media_urls ----------------


class TestCollectMediaUrls:
    def _soup(self, html):
        from bs4 import BeautifulSoup

        return BeautifulSoup(html, "html.parser")

    def test_order_preserved_and_dedup(self):
        soup = self._soup(
            '<img src="https://a.com/1.jpg">'
            '<img data-src="https://a.com/2.jpg">'
            '<img src="https://a.com/1.jpg">'  # 重复应去重且保持首次位置
        )
        items = mod.collect_media_urls(soup, "https://a.com/page")
        assert [u for u, _ in items] == ["https://a.com/1.jpg", "https://a.com/2.jpg"]

    def test_video_in_tag_and_href(self):
        soup = self._soup(
            '<img src="/p.png">'
            '<video src="https://a.com/v.mp4"></video>'
            '<a href="https://a.com/direct.jpg">x</a>'
        )
        items = mod.collect_media_urls(soup, "https://a.com/page")
        urls = [u for u, _ in items]
        assert urls[0] == "https://a.com/p.png"
        assert ("https://a.com/v.mp4", ".mp4") in items
        assert ("https://a.com/direct.jpg", ".jpg") in items

    def test_a_href_page_urls_skipped(self):
        # MediaWiki 的 /wiki/File:xxx.jpg 与动作页 URL 以媒体后缀结尾，
        # 实际是网页；保留真正的 CDN 直链。
        soup = self._soup(
            '<a href="https://commons.wikimedia.org/wiki/File:Example.jpg">x</a>'
            '<a href="https://commons.wikimedia.org/wiki/File_talk:Example.jpg">x</a>'
            '<a href="https://commons.wikimedia.org/w/index.php?title=Special:CreateAccount'
            '&returnto=File%3AExample.jpg">x</a>'
            '<a href="https://cdn.example.com/photo.jpg">x</a>'
        )
        items = mod.collect_media_urls(
            soup, "https://commons.wikimedia.org/wiki/File:Example.jpg"
        )
        assert items == [("https://cdn.example.com/photo.jpg", ".jpg")]

    def test_a_href_query_param_not_media(self):
        # 查询参数里出现 foo.jpg 不代表路径是媒体直链
        soup = self._soup('<a href="https://a.com/page?image=foo.jpg">x</a>')
        assert mod.collect_media_urls(soup, "https://a.com/") == []

    def test_background_image(self):
        soup = self._soup('<div style="background: url(https://a.com/bg.webp)"></div>')
        items = mod.collect_media_urls(soup, "https://a.com/page")
        assert items == [("https://a.com/bg.webp", ".webp")]

    def test_skips_base64_placeholder(self):
        soup = self._soup('<img src="data:image/gif;base64,R0lGOD">')
        assert mod.collect_media_urls(soup, "https://a.com/page") == []

    def test_ignores_video_poster(self):
        # video poster 是低清首帧封面；有视频本体时属重复内容，不应下载
        soup = self._soup(
            '<video poster="https://a.com/cover.jpg" src="https://a.com/v.mp4"></video>'
            '<img src="https://a.com/photo.jpg">'
        )
        items = mod.collect_media_urls(soup, "https://a.com/page")
        urls = [u for u, _ in items]
        assert "https://a.com/cover.jpg" not in urls
        assert "https://a.com/v.mp4" in urls

    def test_skips_decorative_names(self):
        # 名称过滤必须在扫描阶段基于原始 URL 文件名进行——落盘被重命名为
        # <标题>_NN 后，download_file 里的名称检查已拿不到 logo/emotion 等关键词
        soup = self._soup(
            '<img src="https://a.com/header-logo.png">'
            '<img src="https://a.com/emotion_1024.png">'
            '<img src="https://a.com/photo.jpg">'
        )
        items = mod.collect_media_urls(soup, "https://a.com/page")
        assert items == [("https://a.com/photo.jpg", ".jpg")]

    # ---- og:meta / JSON-LD 兜底（2026-10 新增）----

    def test_og_meta_image_and_video(self):
        # OpenGraph/Twitter meta：SPA 与图床站常把主图/主视频只放 meta 里
        # （gallery-dl 在 12+ 站点模块内联使用 og:image 兜底，验证过高价值）
        soup = self._soup(
            "<html><head>"
            '<meta property="og:image" content="https://a.com/cover.jpg">'
            '<meta property="og:video:secure_url" content="https://a.com/clip.mp4">'
            '<meta property="og:image:width" content="1200">'
            '<meta name="twitter:image" content="https://a.com/tw.jpg">'
            "</head><body></body></html>"
        )
        items = mod.collect_media_urls(soup, "https://a.com/page")
        assert ("https://a.com/cover.jpg", ".jpg") in items
        assert ("https://a.com/clip.mp4", ".mp4") in items
        assert ("https://a.com/tw.jpg", ".jpg") in items
        assert len(items) == 3  # og:image:width 的数值型 content 被过滤

    def test_og_meta_dedup_with_dom(self):
        # og:image 与正文 img 同 URL：保持 DOM 顺序只留一份
        soup = self._soup(
            '<img src="https://a.com/1.jpg">'
            '<meta property="og:image" content="https://a.com/1.jpg">'
        )
        assert mod.collect_media_urls(soup, "https://a.com/page") == [
            ("https://a.com/1.jpg", ".jpg")
        ]

    def test_json_ld_image_and_video(self):
        # schema.org 结构化数据：ImageObject/VideoObject 的 contentUrl
        ld = json.dumps(
            {
                "@graph": [
                    {"@type": "ImageObject", "contentUrl": "https://a.com/x/img.png"},
                    {
                        "@type": "VideoObject",
                        "contentUrl": "https://a.com/x/clip.mp4",
                        "url": "https://a.com/page",  # 页面自身 URL：contentUrl 优先，不重复取
                    },
                ]
            }
        )
        soup = self._soup(f'<script type="application/ld+json">{ld}</script>')
        items = mod.collect_media_urls(soup, "https://a.com/page")
        assert ("https://a.com/x/img.png", ".png") in items
        assert ("https://a.com/x/clip.mp4", ".mp4") in items
        assert len(items) == 2

    def test_json_ld_broken_script_ignored(self):
        # 坏 JSON 的 ld+json script 直接跳过，不影响其余扫描
        soup = self._soup(
            '<script type="application/ld+json">{"@type": "ImageObject" oops</script>'
            '<img src="https://a.com/1.jpg">'
        )
        items = mod.collect_media_urls(soup, "https://a.com/page")
        assert [u for u, _ in items] == ["https://a.com/1.jpg"]

    def test_json_ld_url_dict_not_media(self):
        # url 字段为嵌套对象（非字符串）不取——避免把网页自身 URL 当媒体
        ld = json.dumps(
            {
                "@type": "VideoObject",
                "contentUrl": "https://a.com/v.mp4",
                "url": {"@id": "page-identity"},
            }
        )
        soup = self._soup(f'<script type="application/ld+json">{ld}</script>')
        assert mod.collect_media_urls(soup, "https://a.com/page") == [
            ("https://a.com/v.mp4", ".mp4")
        ]


# ---------------- build_media_tasks ----------------


class TestBuildMediaTasks:
    def test_sequential_names(self):
        # 目录名已带标题与 ID，文件只要 001.xxx 序号即可
        items = [("https://a.com/1.jpg", ".jpg"), ("https://a.com/v.mp4", ".mp4")]
        tasks = mod.build_media_tasks(items, "dl/site/标题_123")
        assert [os.path.basename(p) for _, p in tasks] == ["001.jpg", "002.mp4"]
        assert all(
            p.startswith(os.path.join("dl", "site", "标题_123")) for _, p in tasks
        )

    def test_urls_kept_aligned(self):
        items = [("https://a.com/a.png", ".png")]
        tasks = mod.build_media_tasks(items, "d")
        assert tasks == [("https://a.com/a.png", os.path.join("d", "001.png"))]


# ---------------- extract_page_author ----------------

_COOLAPK_HTML = """
<html><head><title>发点存货[哦吼吼] 来自 葵花点菜手 - 酷安</title></head>
<body><div class="username-item" style="margin-left: 8px;">
<p style="color: #212121;">葵花点菜手</p><p>1天前</p></div></body></html>
"""


class TestExtractPageAuthor:
    def _soup(self, html):
        from bs4 import BeautifulSoup

        return BeautifulSoup(html, "html.parser")

    def test_coolapk_dom(self):
        soup = self._soup(_COOLAPK_HTML)
        author, title = mod.extract_page_author(
            soup,
            "发点存货[哦吼吼] 来自 葵花点菜手 - 酷安",
            "https://www.coolapk.com/feed/74208176",
        )
        assert author == "葵花点菜手"
        # 标题里的 "来自 X - 站点" 尾巴应剥掉，避免作者信息在目录名里重复
        assert title == "发点存货[哦吼吼]"

    def test_title_pattern_fallback(self):
        # DOM 选择器失效（页面改版）时，用标题"来自 X - Y"模式兜底
        soup = self._soup("<html><body><p>no author node</p></body></html>")
        author, title = mod.extract_page_author(
            soup, "某帖子 来自 某用户 - 酷安", "https://www.coolapk.com/feed/1"
        )
        assert author == "某用户"
        assert title == "某帖子"

    def test_no_author_returns_empty_and_keeps_title(self):
        soup = self._soup("<html><body></body></html>")
        author, title = mod.extract_page_author(
            soup, "普通页面标题", "https://example.com/page"
        )
        assert author == ""
        assert title == "普通页面标题"

    def test_false_positive_guarded_by_site(self):
        # 正文本身含"来自"且尾巴站点与当前域名无关——不得误剥
        soup = self._soup("<html><body></body></html>")
        author, title = mod.extract_page_author(
            soup, "我 来自 上海 - 记录生活", "https://example.com/page"
        )
        assert author == ""
        assert title == "我 来自 上海 - 记录生活"

    def test_dom_hit_strips_only_matching_tail(self):
        # DOM 提取到作者，但标题尾巴站点不匹配时，标题保持原样
        soup = self._soup(_COOLAPK_HTML)
        author, title = mod.extract_page_author(
            soup, "来自 远方的朋友 - 随笔", "https://www.coolapk.com/feed/1"
        )
        assert author == "葵花点菜手"
        assert title == "来自 远方的朋友 - 随笔"


# ---------------- fetch_page ----------------


class _FakeResp:
    def __init__(self, status_code, url="http://x"):
        self.status_code = status_code
        self.url = url
        self.text = ""
        self.headers = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code} for {self.url}")


class _FakeSession:
    """按队列返回响应/异常，记录调用次数"""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    def get(self, url, **kwargs):
        item = self._outcomes[min(self.calls, len(self._outcomes) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item


class TestFetchPage:
    def test_success_first_try(self, monkeypatch):
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        sess = _FakeSession([_FakeResp(200)])
        resp = mod.fetch_page(sess, "http://x", {})
        assert resp.status_code == 200
        assert sess.calls == 1

    def test_retries_on_waf_567(self, monkeypatch):
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        # 前两次被 EdgeOne 拦截（567），第三次放行
        sess = _FakeSession([_FakeResp(567), _FakeResp(567), _FakeResp(200)])
        resp = mod.fetch_page(sess, "http://x", {})
        assert resp is not None and resp.status_code == 200
        assert sess.calls == 3

    def test_gives_up_status_error(self, monkeypatch):
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        sess = _FakeSession([_FakeResp(567)])
        assert mod.fetch_page(sess, "http://x", {}) is None
        assert sess.calls == 3  # 默认重试 3 次后放弃

    def test_retries_on_network_exception(self, monkeypatch):
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        sess = _FakeSession([Exception("connection reset"), _FakeResp(200)])
        resp = mod.fetch_page(sess, "http://x", {})
        assert resp is not None and resp.status_code == 200
        assert sess.calls == 2

    def test_gives_up_network_exception(self, monkeypatch):
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        sess = _FakeSession([Exception("read timed out")])
        assert mod.fetch_page(sess, "http://x", {}) is None
        assert sess.calls == 3

    def test_client_error_not_retried(self, monkeypatch):
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        # 404 是确定性错误，重试无益，直接失败
        sess = _FakeSession([_FakeResp(404)])
        assert mod.fetch_page(sess, "http://x", {}) is None
        assert sess.calls == 1


# ---------------- 固定 UA 策略 ----------------

# 独立来源的标准 Chrome110 UA 字面值（与 _UA150 的写法风格一致，补丁位归零）
_EXPECTED_CHROME110_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"
)


class TestFixedChrome110UA:
    """1024 对外请求的 UA 必须是固定 Chrome110 串：fake_useragent 上游仓库已
    归档、随机 UA 数据冻结在 Chrome≤133，随机 UA 与 impersonate="chrome110"
    的 TLS 指纹版本错配是本仓库已实证的反爬风险（xhs/douyin 因同类错配被
    302 /login、验证码中间页拦过）。页面与媒体请求统一用固定 UA。"""

    def test_page_headers_ua_fixed(self):
        first = mod.get_headers()["User-Agent"]
        # 多次调用结果不变：UA 不再随机
        assert all(mod.get_headers()["User-Agent"] == first for _ in range(10)), (
            "UA 应为固定值，不应随调用随机变化"
        )
        # 与模块常量、标准 Chrome110 串一致
        assert first == mod._UA110
        assert first == _EXPECTED_CHROME110_UA
        assert "Chrome/110" in first

    def test_download_file_sends_fixed_ua(self, tmp_path, monkeypatch):
        # 媒体下载请求（img_headers）同样发固定 Chrome110 UA，与页面请求一致
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        seen = {}

        class S:
            def get(self, url, **kw):
                seen.update(kw.get("headers") or {})
                return _FakeImageResp(b"x" * 30000)

        target = str(tmp_path / "001.jpg")
        result = mod.download_file(S(), "http://img/001.jpg", target, "http://page")
        assert "[成功]" in result
        assert seen["User-Agent"] == _EXPECTED_CHROME110_UA
        assert seen["User-Agent"] == mod.get_headers()["User-Agent"]
        assert "Chrome/110" in seen["User-Agent"]


# ---------------- extract_general_media 结果契约 ----------------


class _PageResp:
    def __init__(self):
        self.text = "<html><head><title>Test</title></head><body></body></html>"
        self.status_code = 200
        self.url = "http://x/page"


class _DummySession:
    def close(self):
        pass


class TestExtractGeneralMediaResult:
    """extract_general_media 必须返回 DownloadResult，供上层区分失败/重复/无媒体。"""

    def _patch_common(self, monkeypatch, tmp_path, media_items):
        monkeypatch.setattr(mod, "Session", lambda: _DummySession())
        monkeypatch.setattr(mod, "fetch_page", lambda *a, **k: _PageResp())
        monkeypatch.setattr(mod, "build_download_dir", lambda *a, **k: str(tmp_path))
        monkeypatch.setattr(mod, "collect_media_urls", lambda *a, **k: media_items)

    def test_fetch_failure_returns_failed(self, monkeypatch):
        monkeypatch.setattr(mod, "Session", lambda: _DummySession())
        monkeypatch.setattr(mod, "fetch_page", lambda *a, **k: None)
        result = mod.extract_general_media("http://x/page")
        assert result.status is DownloadStatus.FAILED
        assert "获取页面失败" in result.message

    def test_no_media_returns_no_media(self, monkeypatch, tmp_path):
        self._patch_common(monkeypatch, tmp_path, [])
        result = mod.extract_general_media("http://x/page")
        assert result.status is DownloadStatus.NO_MEDIA

    def test_success_returns_downloaded(self, monkeypatch, tmp_path):
        self._patch_common(monkeypatch, tmp_path, [("http://img/1.jpg", ".jpg")])

        def fake_download(session, url, filepath, page_url, force=False):
            with open(filepath, "wb") as f:
                f.write(b"x")
            return "  [成功] -> 001.jpg"

        monkeypatch.setattr(mod, "download_file", fake_download)
        result = mod.extract_general_media("http://x/page")
        assert result.status is DownloadStatus.DOWNLOADED
        assert "新增 1 项" in result.message

    def test_failed_only_returns_failed(self, monkeypatch, tmp_path):
        self._patch_common(monkeypatch, tmp_path, [("http://img/1.jpg", ".jpg")])
        monkeypatch.setattr(
            mod, "download_file", lambda *a, **k: "  [失败] 001.jpg 报错: x"
        )
        result = mod.extract_general_media("http://x/page")
        assert result.status is DownloadStatus.FAILED

    def test_existing_skip_returns_skipped(self, monkeypatch, tmp_path):
        self._patch_common(monkeypatch, tmp_path, [("http://img/1.jpg", ".jpg")])
        (tmp_path / "001.jpg").write_bytes(b"old")
        monkeypatch.setattr(
            mod,
            "download_file",
            lambda *a, **k: "  [跳过] 001.jpg 已存在，无需重复下载",
        )
        result = mod.extract_general_media("http://x/page")
        assert result.status is DownloadStatus.SKIPPED

    def test_filtered_only_returns_no_media(self, monkeypatch, tmp_path):
        self._patch_common(monkeypatch, tmp_path, [("http://img/1.jpg", ".jpg")])
        monkeypatch.setattr(
            mod,
            "download_file",
            lambda *a, **k: "  [跳过] 001.jpg 尺寸 20x20 过小，判定为装饰图",
        )
        result = mod.extract_general_media("http://x/page")
        assert result.status is DownloadStatus.NO_MEDIA
        assert "装饰图" in result.message

    def test_completion_log_includes_counts(self, monkeypatch, tmp_path, caplog):
        # 收尾日志要带 新增/跳过/失败/被过滤 计数（与 xhs 口径一致）：
        # 计数只进 DownloadResult 的话，看日志的人不知道总量和成败占比
        import logging as _logging

        self._patch_common(
            monkeypatch,
            tmp_path,
            [("http://img/1.jpg", ".jpg"), ("http://img/2.jpg", ".jpg")],
        )
        (tmp_path / "001.jpg").write_bytes(b"old")

        def fake_download(session, url, filepath, page_url, force=False):
            if "1.jpg" in url:
                return "  [跳过] 001.jpg 已存在，无需重复下载"
            return "  [失败] 002.jpg 报错: x"

        monkeypatch.setattr(mod, "download_file", fake_download)
        with caplog.at_level(_logging.INFO, logger="downloader"):
            mod.extract_general_media("http://x/page")
        summary = [
            r.getMessage() for r in caplog.records if "下载完成" in r.getMessage()
        ]
        assert summary, "收尾日志应包含'下载完成'汇总行"
        msg = summary[0]
        assert "0 新增" in msg
        assert "1 跳过" in msg
        assert "1 失败" in msg
        assert "被过滤" in msg
        assert "共 2 项" in msg
        assert "耗时" in msg


class TestHostConcurrencyLimit:
    """同一图床的并发上限：23img.com 等 CDN 会间歇拒绝突发 TLS 握手
    （curl 35，5 并发时 2026-10 Termux 实测约 8 成图片失败）——
    同 host 内限流排队，跨 host 仍并行。"""

    def _run(self, monkeypatch, tmp_path, media_items):
        import threading
        import time as _time

        lock = threading.Lock()
        state = {"active": 0, "max": 0}

        def fake_download(session, url, filepath, page_url, force=False):
            with lock:
                state["active"] += 1
                state["max"] = max(state["max"], state["active"])
            _time.sleep(0.1)
            with lock:
                state["active"] -= 1
            return "  [成功] -> x"

        monkeypatch.setattr(mod, "download_file", fake_download)
        monkeypatch.setattr(mod, "Session", lambda: _DummySession())
        monkeypatch.setattr(mod, "fetch_page", lambda *a, **k: _PageResp())
        monkeypatch.setattr(mod, "build_download_dir", lambda *a, **k: str(tmp_path))
        monkeypatch.setattr(mod, "collect_media_urls", lambda *a, **k: media_items)
        result = mod.extract_general_media("http://x/page")
        assert result.status is DownloadStatus.DOWNLOADED
        return state["max"]

    def test_same_host_capped(self, monkeypatch, tmp_path):
        # 同一图床 6 个文件：并发被压到 2 路
        items = [(f"http://23img.com/{i:03d}.jpg", ".jpg") for i in range(6)]
        assert self._run(monkeypatch, tmp_path, items) == 2

    def test_different_hosts_still_parallel(self, monkeypatch, tmp_path):
        # 不同图床的两个文件：不受同域限流约束，可同时进行
        items = [("http://a.com/001.jpg", ".jpg"), ("http://b.com/002.jpg", ".jpg")]
        assert self._run(monkeypatch, tmp_path, items) == 2
