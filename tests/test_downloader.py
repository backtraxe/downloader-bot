# -*- coding: utf-8 -*-
"""downloader.py 纯函数单元测试（无网络依赖）"""
import os

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
