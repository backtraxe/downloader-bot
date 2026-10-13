# -*- coding: utf-8 -*-
"""cookie_import.py 格式识别与转换的纯函数测试"""
import pytest

from cookie_import import convert_to_site_format, detect_format

_HEADER = "sessionid=abc123; ttwid=xyz; UIFID=u-1"
_NETSCAPE = ("# Netscape HTTP Cookie File\n"
             ".douyin.com\tTRUE\t/\tTRUE\t4070908800\tsessionid\tabc123\n"
             ".douyin.com\tTRUE\t/\tFALSE\t4070908800\tttwid\txyz\n")
_JSON = ('[{"name": "web_session", "value": "s1", "domain": ".xiaohongshu.com",'
         ' "path": "/", "secure": true, "expires": -1},'
         ' {"name": "tracker", "value": "t", "domain": ".third-party.com"}]')


class TestDetectFormat:
    def test_json(self):
        assert detect_format(_JSON) == "json"

    def test_json_wrapped(self):
        assert detect_format('{"cookies": []}') == "json"

    def test_netscape(self):
        assert detect_format(_NETSCAPE) == "netscape"

    def test_header(self):
        assert detect_format(_HEADER) == "header"

    def test_header_with_label(self):
        assert detect_format("Cookie: " + _HEADER) == "header"

    def test_empty(self):
        assert detect_format("") == "empty"
        assert detect_format("   \n") == "empty"


class TestConvertHeaderInput:
    def test_douyin_header_to_netscape(self):
        out = convert_to_site_format(_HEADER, "douyin")
        assert out.startswith("# Netscape HTTP Cookie File")
        lines = [l for l in out.splitlines() if l and not l.startswith("#")]
        assert len(lines) == 3
        assert all(l.split("\t")[0] == ".douyin.com" for l in lines)
        names = {l.split("\t")[5] for l in lines}
        assert names == {"sessionid", "ttwid", "UIFID"}

    def test_bilibili_header_to_netscape(self):
        out = convert_to_site_format("SESSDATA=s; bili_jct=j", "bilibili")
        assert ".bilibili.com" in out and "SESSDATA" in out

    def test_xhs_header_passthrough(self):
        out = convert_to_site_format("web_session=s1; a1=v", "xiaohongshu")
        assert out == "web_session=s1; a1=v\n"

    def test_header_label_stripped(self):
        out = convert_to_site_format("Cookie: web_session=s1", "xiaohongshu")
        assert out == "web_session=s1\n"


class TestConvertJsonInput:
    def test_json_to_netscape(self):
        out = convert_to_site_format(_JSON, "douyin")
        lines = [l for l in out.splitlines() if l and not l.startswith("#")]
        assert len(lines) == 2  # Netscape 保留全量域（含第三方，按域列存储）
        names = {l.split("\t")[5] for l in lines}
        assert names == {"web_session", "tracker"}

    def test_json_to_xhs_header_filters_foreign_domain(self):
        out = convert_to_site_format(_JSON, "xiaohongshu")
        assert out == "web_session=s1\n"  # 第三方域 cookie 不进请求头

    def test_json_wrapped(self):
        out = convert_to_site_format(
            '{"cookies": [{"name": "a", "value": "1", "domain": ".douyin.com"}]}',
            "douyin")
        assert "\ta\t1\n" in out


class TestConvertNetscapeInput:
    def test_netscape_passthrough_for_douyin(self):
        out = convert_to_site_format(_NETSCAPE, "douyin")
        assert out == _NETSCAPE

    def test_netscape_to_xhs_header(self):
        out = convert_to_site_format(_NETSCAPE, "xiaohongshu")
        assert out == "sessionid=abc123; ttwid=xyz\n"


class TestConvertErrors:
    def test_empty_input_raises(self):
        with pytest.raises(ValueError):
            convert_to_site_format("", "douyin")
