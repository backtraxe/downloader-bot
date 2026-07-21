# -*- coding: utf-8 -*-
"""http_client.py 纯函数单元测试（无网络）。"""
import pytest

from http_client import CurlResponse, _build_curl_args, _CURL_IMPERSONATE_FLAGS


# ---------------- CurlResponse ----------------

class TestCurlResponse:
    def test_text_decodes_bytes(self):
        r = CurlResponse(200, {"content-type": "text/html"}, b"<html>hello</html>", "http://x")
        assert r.text == "<html>hello</html>"
        assert r.content == b"<html>hello</html>"

    def test_text_handles_non_utf8(self):
        r = CurlResponse(200, {}, b"\xff\xfe", "http://x")
        # 非 UTF-8 字节不应抛异常，用 replace 兜底
        assert r.url == "http://x"
        assert isinstance(r.text, str)

    def test_headers_case_insensitive(self):
        r = CurlResponse(200, {"Content-Type": "image/jpeg"}, b"", "http://x")
        assert r.headers["content-type"] == "image/jpeg"

    def test_raise_for_status_ok(self):
        r = CurlResponse(200, {}, b"", "http://x")
        r.raise_for_status()  # 不应抛异常

    def test_raise_for_status_error(self):
        r = CurlResponse(403, {}, b"forbidden", "http://x")
        with pytest.raises(Exception, match="HTTP 403"):
            r.raise_for_status()

    def test_iter_content_chunks(self):
        body = b"x" * 100
        r = CurlResponse(200, {}, body, "http://x")
        chunks = list(r.iter_content(chunk_size=30))
        assert len(chunks) == 4
        assert b"".join(chunks) == body

    def test_iter_content_empty(self):
        r = CurlResponse(200, {}, b"", "http://x")
        assert list(r.iter_content()) == []

    def test_json(self):
        import json
        r = CurlResponse(200, {}, b'{"key": "val"}', "http://x")
        assert r.json() == {"key": "val"}


# ---------------- _build_curl_args ----------------

class TestBuildCurlArgs:
    def test_basic_get(self):
        args = _build_curl_args("http://x", "GET", {"UA": "test"}, 15, None, False)
        assert args[0] == "curl" or args[0].endswith("/curl")
        assert "-X" in args and "GET" in args
        assert "http://x" == args[-1]
        assert "--max-time" in args

    def test_impersonate_adds_tls_flags(self):
        args = _build_curl_args("http://x", "GET", {}, 15, "chrome110", False)
        # impersonate=True 时应该追加 TLS 伪装参数
        for flag in _CURL_IMPERSONATE_FLAGS:
            assert flag in args

    def test_no_impersonate_no_tls_flags(self):
        args = _build_curl_args("http://x", "GET", {}, 15, None, False)
        # 没传 impersonate 时不追加 TLS 伪装参数
        assert "--http2" not in args

    def test_headers_passed(self):
        args = _build_curl_args("http://x", "GET", {"Cookie": "abc", "Referer": "http://y"}, 15, None, False)
        h_strings = [args[i + 1] for i, v in enumerate(args) if v == "-H"]
        assert any("Cookie: abc" in h for h in h_strings)
        assert any("Referer: http://y" in h for h in h_strings)

    def test_timeout_in_args(self):
        args = _build_curl_args("http://x", "GET", {}, 42, None, False)
        idx = args.index("--max-time")
        assert args[idx + 1] == "42"
