# -*- coding: utf-8 -*-
"""1024_downloader.py 纯函数/可注入会话单元测试（无网络依赖）"""
import importlib
import os

import pytest

mod = importlib.import_module("1024_downloader")


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
    @pytest.mark.parametrize("name", [
        "header-logo.png", "69_avatar_middle.jpg", "weixin_qrcode.png",
        "coolapk_emotion_1017_houhouhou.png", "beian.png", "app_icon.png",
        "LOGO.PNG",  # 大小写不敏感
    ])
    def test_decorative(self, name):
        assert mod._is_decorative_name(name) is True

    @pytest.mark.parametrize("name", [
        "38813669_98682f5d_6765_3739_311@2494x3325.jpg.m.jpg",
        "photo.jpg", "IMG_2024.png", "video.mp4",
    ])
    def test_content(self, name):
        assert mod._is_decorative_name(name) is False


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
        result = mod.download_file(self._fake_session(b"x" * 100),
                                   "http://img/tiny.png", target, "http://page")
        assert "[跳过]" in result
        assert not os.path.exists(target)

    def test_large_image_kept(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        target = str(tmp_path / "big.jpg")
        result = mod.download_file(self._fake_session(b"x" * 30000),
                                   "http://img/big.jpg", target, "http://page")
        assert "[成功]" in result
        assert os.path.exists(target)

    def test_small_video_not_filtered(self, tmp_path, monkeypatch):
        # 视频不做体积过滤——短视频也可能很小
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        target = str(tmp_path / "clip.mp4")
        result = mod.download_file(self._fake_session(b"x" * 100, "video/mp4"),
                                   "http://img/clip.mp4", target, "http://page")
        assert "[成功]" in result
        assert os.path.exists(target)

    def test_small_dimensions_skipped(self, tmp_path, monkeypatch):
        # 尺寸可解析时按尺寸过滤：100x100 头像即使文件大于 20KB 也是装饰图
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        from tests.test_utils import _make_png
        png = _make_png(100, 100) + b"x" * 30000  # 填充使其超过 20KB
        target = str(tmp_path / "small-dims.png")  # 中性文件名，确保走尺寸判定
        result = mod.download_file(self._fake_session(png, "image/png"),
                                   "http://img/photo.png", target, "http://page")
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
        result = mod.download_file(self._fake_session(png, "image/png"),
                                   "http://img/photo.png", target, "http://page")
        assert "[成功]" in result
        assert os.path.exists(target)


# ---------------- make_page_dir_name ----------------

class TestMakePageDirName:
    def test_with_numeric_id(self):
        # 目录 = 标题_ID，撞名时可区分、可溯源
        out = mod.make_page_dir_name("发点存货", "https://www.coolapk.com/feed/74208176?s=x")
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


# ---------------- build_media_tasks ----------------

class TestBuildMediaTasks:
    def test_sequential_names(self):
        # 目录名已带标题与 ID，文件只要 001.xxx 序号即可
        items = [("https://a.com/1.jpg", ".jpg"), ("https://a.com/v.mp4", ".mp4")]
        tasks = mod.build_media_tasks(items, "dl/site/标题_123")
        assert [os.path.basename(p) for _, p in tasks] == ["001.jpg", "002.mp4"]
        assert all(p.startswith(os.path.join("dl", "site", "标题_123")) for _, p in tasks)

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
            soup, "发点存货[哦吼吼] 来自 葵花点菜手 - 酷安",
            "https://www.coolapk.com/feed/74208176")
        assert author == "葵花点菜手"
        # 标题里的 "来自 X - 站点" 尾巴应剥掉，避免作者信息在目录名里重复
        assert title == "发点存货[哦吼吼]"

    def test_title_pattern_fallback(self):
        # DOM 选择器失效（页面改版）时，用标题"来自 X - Y"模式兜底
        soup = self._soup("<html><body><p>no author node</p></body></html>")
        author, title = mod.extract_page_author(
            soup, "某帖子 来自 某用户 - 酷安",
            "https://www.coolapk.com/feed/1")
        assert author == "某用户"
        assert title == "某帖子"

    def test_no_author_returns_empty_and_keeps_title(self):
        soup = self._soup("<html><body></body></html>")
        author, title = mod.extract_page_author(
            soup, "普通页面标题", "https://example.com/page")
        assert author == ""
        assert title == "普通页面标题"

    def test_false_positive_guarded_by_site(self):
        # 正文本身含"来自"且尾巴站点与当前域名无关——不得误剥
        soup = self._soup("<html><body></body></html>")
        author, title = mod.extract_page_author(
            soup, "我 来自 上海 - 记录生活",
            "https://example.com/page")
        assert author == ""
        assert title == "我 来自 上海 - 记录生活"

    def test_dom_hit_strips_only_matching_tail(self):
        # DOM 提取到作者，但标题尾巴站点不匹配时，标题保持原样
        soup = self._soup(_COOLAPK_HTML)
        author, title = mod.extract_page_author(
            soup, "来自 远方的朋友 - 随笔",
            "https://www.coolapk.com/feed/1")
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
