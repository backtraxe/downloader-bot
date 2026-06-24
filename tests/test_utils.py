# -*- coding: utf-8 -*-
"""utils.py 纯函数单元测试"""
import os
import threading

import pytest

from utils import (
    guess_extension,
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
