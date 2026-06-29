# -*- coding: utf-8 -*-
"""utils.py 纯函数单元测试"""
import os
import threading

import pytest

from utils import (
    build_download_dir,
    cookie_header_to_netscape,
    guess_extension,
    init_useragent,
    normalize_url,
    sanitize_filename,
    unique_path,
)


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

