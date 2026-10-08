# -*- coding: utf-8 -*-
"""utils.py 纯函数单元测试"""
import os
import threading

import pytest

from utils import (
    build_download_dir,
    cookie_header_to_netscape,
    extract_url_numeric_id,
    get_image_dimensions,
    guess_extension,
    init_useragent,
    normalize_url,
    sanitize_filename,
    unique_path,
)


# ---------------- get_image_dimensions ----------------

def _make_image(w, h, fmt):
    """用 Pillow 生成指定宽高的真实图片字节。"""
    import io as _io
    from PIL import Image
    buf = _io.BytesIO()
    Image.new("RGB", (w, h), (120, 80, 40)).save(buf, format=fmt)
    return buf.getvalue()


def _make_png(w, h):
    return _make_image(w, h, "PNG")


class TestGetImageDimensions:
    def test_png(self):
        assert get_image_dimensions(_make_png(1080, 1440)) == (1080, 1440)

    def test_jpeg(self):
        assert get_image_dimensions(_make_image(640, 480, "JPEG")) == (640, 480)

    def test_gif(self):
        assert get_image_dimensions(_make_image(32, 16, "GIF")) == (32, 16)

    def test_webp(self):
        assert get_image_dimensions(_make_image(200, 100, "WEBP")) == (200, 100)

    def test_too_short(self):
        assert get_image_dimensions(b"\x89PNG") is None
        assert get_image_dimensions(b"") is None

    def test_not_image(self):
        assert get_image_dimensions(b"<html><body>x</body></html>") is None


# ---------------- normalize_url ----------------

class TestNormalizeUrl:
    def test_protocol_relative(self):
        assert normalize_url("//cdn.example.com/a.jpg", "https://example.com") == "https://cdn.example.com/a.jpg"

    def test_already_absolute(self):
        assert normalize_url("https://x.com/a.jpg", "https://example.com") == "https://x.com/a.jpg"

    def test_relative_path(self):
        assert normalize_url("/img/a.jpg", "https://example.com/page") == "https://example.com/img/a.jpg"

    def test_relative_no_slash(self):
        assert normalize_url("a.jpg", "https://example.com/p/") == "https://example.com/p/a.jpg"

    def test_data_uri_unchanged(self):
        assert normalize_url("data:image/png;base64,xx", "https://example.com") == "data:image/png;base64,xx"

    def test_none_or_empty(self):
        assert normalize_url("", "https://example.com") == ""
        assert normalize_url(None, "https://example.com") == ""


# ---------------- sanitize_filename ----------------

class TestSanitizeFilename:
    def test_removes_path_separators(self):
        assert sanitize_filename("a/b\\c") == "abc"

    def test_keeps_chinese(self):
        assert sanitize_filename("我的笔记") == "我的笔记"

    def test_strips_illegal(self):
        assert sanitize_filename('a:*?"<>|b') == "ab"

    def test_empty_returns_default(self):
        assert sanitize_filename("") == "untitled"
        assert sanitize_filename("   ") == "untitled"

    def test_collapses_whitespace_only(self):
        assert sanitize_filename("  标题  ") == "标题"

    def test_truncates_long(self):
        long_name = "字" * 300
        out = sanitize_filename(long_name, max_len=50)
        assert len(out) <= 50


# ---------------- guess_extension ----------------

class TestGuessExtension:
    def test_from_url_png(self):
        assert guess_extension("https://x.com/a.png") == ".png"

    def test_from_url_webp(self):
        assert guess_extension("https://x.com/a.webp?x=1") == ".webp"

    def test_from_url_mp4(self):
        assert guess_extension("https://x.com/v.mp4") == ".mp4"

    def test_from_content_type_jpeg(self):
        assert guess_extension("https://x.com/noext", content_type="image/jpeg") == ".jpg"

    def test_content_type_overrides_url_default(self):
        # URL 无扩展名，靠 content_type
        assert guess_extension("https://x.com/abc", content_type="image/png") == ".png"

    def test_url_ext_beats_content_type_html(self):
        # URL 明确是 .png，content_type 是 text/html（被拦截），仍按 URL 给 .png
        assert guess_extension("https://x.com/a.png", content_type="text/html") == ".png"

    def test_fallback_default(self):
        assert guess_extension("https://x.com/abc") == ".jpg"
        assert guess_extension("https://x.com/abc", default=".mp4") == ".mp4"

    def test_heic_content_type(self):
        # 小红书部分原图是 HEIF：无扩展名 URL + image/heic 应得 .heic
        assert guess_extension("https://ci.xiaohongshu.com/1040g008", content_type="image/heic") == ".heic"
        assert guess_extension("https://ci.xiaohongshu.com/1040g008", content_type="image/heif") == ".heic"

    def test_heic_from_url(self):
        assert guess_extension("https://x.com/a.heic") == ".heic"

    def test_heif_pillow_unsupported_returns_none(self):
        # 纯 HEIF 字节 Pillow 默认解不出，解析失败应回退 None（改由体积兜底）
        heif = b"\x00\x00\x00\x20ftypheic" + b"\x00" * 64
        assert get_image_dimensions(heif) is None


# ---------------- unique_path ----------------

class TestUniquePath:
    def test_no_conflict(self, tmp_path):
        p = tmp_path / "a.jpg"
        out = unique_path(str(p))
        assert out == str(p)

    def test_existing_gets_suffix(self, tmp_path):
        p = tmp_path / "a.jpg"
        p.write_bytes(b"x")
        out = unique_path(str(p))
        assert out != str(p)
        assert out.startswith(str(tmp_path / "a"))
        assert out.endswith(".jpg")

    def test_concurrent_no_overwrite(self, tmp_path):
        """多线程同时获取同名 path，不应产生重复路径"""
        p = tmp_path / "dup.jpg"
        p.write_bytes(b"x")
        results = []
        barrier = threading.Barrier(5)

        def worker():
            barrier.wait()
            results.append(unique_path(str(p)))

        ts = [threading.Thread(target=worker) for _ in range(5)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        # 原文件 + 5 个不同后缀 = 6 个唯一路径（原文件本身不会被返回给任一线程，
        # 5 个线程应得到 5 个互不相同的路径）
        assert len(results) == 5
        assert len(set(results)) == 5, f"撞名了: {results}"

    def test_creates_placeholder(self, tmp_path):
        """unique_path 返回的路径应可被独占创建，再次调用得到新路径"""
        p = tmp_path / "b.jpg"
        p.write_bytes(b"x")
        out1 = unique_path(str(p))
        assert not os.path.exists(out1) or os.path.getsize(out1) == 0
        # 模拟写入
        with open(out1, "wb") as f:
            f.write(b"data")
        out2 = unique_path(str(p))
        assert out2 != out1


# ---------------- cookie_header_to_netscape ----------------

class TestCookieHeaderToNetscape:
    def test_basic_conversion(self):
        header = "SID=abc123; LOGIN_INFO=xyz; __Secure-1PSID=tok"
        out = cookie_header_to_netscape(header, domain=".youtube.com")
        assert out.startswith("# Netscape HTTP Cookie File")
        lines = [l for l in out.splitlines() if l and not l.startswith("#")]
        assert len(lines) == 3
        for line in lines:
            parts = line.split("\t")
            assert len(parts) == 7
            assert parts[0] == ".youtube.com"

    def test_strips_empty_pairs(self):
        header = "a=1;; ;b=2;"
        out = cookie_header_to_netscape(header, ".x.com")
        lines = [l for l in out.splitlines() if l and not l.startswith("#")]
        assert len(lines) == 2

    def test_empty_input(self):
        out = cookie_header_to_netscape("", ".x.com")
        assert out.startswith("# Netscape HTTP Cookie File")
        # 仅头注释，无 cookie 行
        assert len([l for l in out.splitlines() if l and not l.startswith("#")]) == 0

    def test_value_with_equals_sign(self):
        # value 内含 = 不应被错误拆分
        header = "token=abc==def"
        out = cookie_header_to_netscape(header, ".x.com")
        lines = [l for l in out.splitlines() if l and not l.startswith("#")]
        assert len(lines) == 1
        name, value = lines[0].split("\t")[5], lines[0].split("\t")[6]
        assert name == "token"
        assert value == "abc==def"

    def test_writes_file(self, tmp_path):
        header = "SESSDATA=abc"
        out_file = tmp_path / "cookies.txt"
        cookie_header_to_netscape(header, ".bilibili.com", str(out_file))
        content = out_file.read_text()
        assert ".bilibili.com" in content
        assert "SESSDATA" in content


# ---------------- init_useragent ----------------

class TestInitUseragent:
    def test_returns_usable_ua(self):
        """fake_useragent 2.x 用 os=["Windows"]（数据里 OS 值首字母大写），
        随机 UA 应能取到非 fallback 的真实 UA。"""
        import logging
        ua, fallback = init_useragent(logging.getLogger("test"))
        # 只要 fake_useragent 装了且内置数据可用，ua 就不应是 None
        if ua is None:
            pytest.skip("fake_useragent 不可用（环境问题），已降级固定 UA")
        # 连续取若干次，至少应有一次不是 fallback（排除极小概率恰好抽到 fallback）
        samples = [ua.random for _ in range(20)]
        assert any(s != fallback for s in samples), "随机 UA 全是 fallback，OS 传参可能错了"


# ---------------- extract_url_numeric_id ----------------

class TestExtractUrlNumericId:
    @pytest.mark.parametrize("url,want", [
        ("https://www.coolapk.com/feed/74208176", "74208176"),
        ("https://www.coolapk.com/feed/74208176?s=Y2MwN2E", "74208176"),  # query 不影响
        ("https://weibo.com/2442426521/QB3tPc8lZ", None),  # 数字在中间段而非末段
        ("https://example.com/post/12345/", "12345"),  # 末尾斜杠
    ])
    def test_extract(self, url, want):
        assert extract_url_numeric_id(url) == want

    @pytest.mark.parametrize("url", [
        "https://example.com/",                    # 无路径
        "https://example.com/about",               # 末段非纯数字
        "https://example.com/2024/01/01",          # 末段太短（<5 位，多为日期）
        "https://example.com/post/999",            # 3 位数字，不够置信
        "https://example.com/a.jpg",               # 末段是文件
    ])
    def test_no_id(self, url):
        assert extract_url_numeric_id(url) is None


# ---------------- build_download_dir ----------------

class TestBuildDownloadDir:
    def test_normal_path(self):
        assert build_download_dir("bilibili", "某视频") == os.path.join("download", "bilibili", "某视频")

    def test_xhs_site(self):
        assert build_download_dir("xiaohongshu", "笔记") == os.path.join("download", "xiaohongshu", "笔记")

    def test_empty_title_falls_back(self):
        # 空标题兜底 untitled，不落到 download/<site>//
        assert build_download_dir("youtube", "") == os.path.join("download", "youtube", "untitled")

    def test_whitespace_title_falls_back(self):
        assert build_download_dir("youtube", "   ") == os.path.join("download", "youtube", "untitled")

    def test_path_traversal_blocked(self):
        # 标题含路径分隔符，经 sanitize_filename 清洗后不得越层逃出 download/<site>/
        out = build_download_dir("bilibili", "../../etc/passwd")
        # 结果必须仍在 download/bilibili/ 之下，且不含 ..
        assert out.startswith(os.path.join("download", "bilibili") + os.sep)
        assert ".." not in out.split(os.sep)

    def test_custom_download_dir(self):
        assert build_download_dir("x", "t", download_dir="out") == os.path.join("out", "x", "t")

    def test_strips_illegal_chars_from_title(self):
        # 标题含 Windows 保留字符与路径分隔符，应被清洗
        out = build_download_dir("x", 'a:*?"<>|b')
        assert out.endswith("ab")
        assert os.path.dirname(out) == os.path.join("download", "x")

    # ---- 作者层 ----

    def test_with_author(self):
        assert build_download_dir("bilibili", "视频", author="某UP") == \
            os.path.join("download", "bilibili", "某UP", "视频")

    def test_author_empty_falls_back_to_no_author_layer(self):
        # 作者为空串 → 退化为无作者层，不产生 unknown/ 或 NA/
        assert build_download_dir("bilibili", "视频", author="") == \
            os.path.join("download", "bilibili", "视频")

    def test_author_none_falls_back_to_no_author_layer(self):
        # author=None（默认）→ 原行为不变
        assert build_download_dir("bilibili", "视频") == \
            os.path.join("download", "bilibili", "视频")

    def test_author_whitespace_falls_back(self):
        # 纯空白 author → 退化为无作者层，不产生 unknown/ 占位
        assert build_download_dir("bilibili", "视频", author="   ") == \
            os.path.join("download", "bilibili", "视频")

    def test_author_traversal_blocked(self):
        # 作者名含路径分隔符，清洗后不得逃出 download/<site>/ 之下
        out = build_download_dir("bilibili", "视频", author="../../etc")
        assert out.startswith(os.path.join("download", "bilibili") + os.sep)
        assert ".." not in out.split(os.sep)

    def test_author_strips_illegal_chars(self):
        out = build_download_dir("x", "t", author='a:*?"b')
        assert out == os.path.join("download", "x", "ab", "t")



# ---------------- extract_urls ----------------

class TestExtractUrls:
    """从任意文本提取 URL 的测试：支持分享文案、多 URL、去重等。"""

    def test_pure_url(self):
        from utils import extract_urls
        assert extract_urls("https://www.xiaohongshu.com/explore/abc") == \
            ["https://www.xiaohongshu.com/explore/abc"]

    def test_url_in_share_text(self):
        from utils import extract_urls
        text = "6 发布了一篇小红线笔记 http://xhslink.com/o/abc 快来看"
        assert extract_urls(text) == ["http://xhslink.com/o/abc"]

    def test_strips_trailing_punctuation(self):
        from utils import extract_urls
        assert extract_urls("看看 https://www.youtube.com/watch?v=abc。") == \
            ["https://www.youtube.com/watch?v=abc"]

    def test_strips_trailing_cjk(self):
        from utils import extract_urls
        # 中文紧贴 URL 尾部，应被剔除
        assert extract_urls("http://xhslink.com/o/abc好棒") == \
            ["http://xhslink.com/o/abc"]

    def test_strips_trailing_fullwidth_punctuation(self):
        from utils import extract_urls
        assert extract_urls("https://example.com/a）") == ["https://example.com/a"]
        assert extract_urls("https://example.com/a》") == ["https://example.com/a"]

    def test_multiple_urls(self):
        from utils import extract_urls
        text = "https://x.com/a/status/1 https://twitter.com/b/status/2"
        assert extract_urls(text) == [
            "https://x.com/a/status/1",
            "https://twitter.com/b/status/2",
        ]

    def test_deduplicates_urls(self):
        from utils import extract_urls
        text = "重复 https://example.com/a 再来 https://example.com/a"
        assert extract_urls(text) == ["https://example.com/a"]

    def test_no_url_returns_empty(self):
        from utils import extract_urls
        assert extract_urls("今天天气真好") == []
        assert extract_urls("没有链接的纯文本") == []

    def test_empty_input(self):
        from utils import extract_urls
        assert extract_urls("") == []
        assert extract_urls(None) == []

    def test_url_with_query_and_fragment(self):
        from utils import extract_urls
        url = "https://www.xiaohongshu.com/explore/abc?xsec_token=CBc%3D&share=1#detail"
        assert extract_urls(url) == [url]

    def test_multiline_text(self):
        from utils import extract_urls
        text = "https://example.com/a\nhttps://example.com/b"
        assert extract_urls(text) == ["https://example.com/a", "https://example.com/b"]

    def test_http_protocol(self):
        from utils import extract_urls
        assert extract_urls("http://example.com/a") == ["http://example.com/a"]

    def test_preserves_order(self):
        from utils import extract_urls
        text = "https://b.com/2 https://a.com/1"
        assert extract_urls(text) == ["https://b.com/2", "https://a.com/1"]
