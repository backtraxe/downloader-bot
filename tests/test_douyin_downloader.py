# -*- coding: utf-8 -*-
"""douyin_downloader.py 纯函数单元测试（无网络依赖）。

fixture 结构来自 2026 年真实 douyin.com/note 页面：
- 新版 React Flight 页：详情在 self.__pace_f.push 流式数据块，
  字段 camelCase（awemeId/images[].downloadUrlList/authorInfo）
- 旧版 RENDER_DATA 页：详情在 JSON，字段 snake_case（aweme_id/download_url_list/author）
"""
import json
from urllib.parse import quote

import pytest

from douyin_downloader import (
    detect_jsvm_challenge,
    extract_aweme_detail,
    extract_douyin_author,
    extract_douyin_image_url,
    extract_live_photo_url,
    extract_note_detail,
    extract_note_video_url,
    is_douyin_note_url,
    parse_pace_f_detail,
    parse_render_data,
    resolve_douyin_basename,
)


def _make_camel_detail():
    """新版 camelCase 结构（真实页面抓取形态）。"""
    return {
        "awemeId": "7666381907030529443",
        "desc": "没关系 一个人也能走很远的路。#一个人拍照 #清冷感",
        "authorInfo": {"nickname": "偏等落叶", "uniqueId": "photo_x"},
        "images": [
            {"uri": "tos-cn-i-0813/abc",
             "downloadUrlList": ["https://p3-sign.douyinpic.com/img1.jpg"],
             "urlList": ["https://p3-sign.douyinpic.com/img1_small.jpg"]},
            {"urlList": ["https://p3-sign.douyinpic.com/img2.jpg"]},
            {"urlList": ["https://p3-sign.douyinpic.com/img3.jpg"],
             "video": {"playAddr": {"urlList": ["https://v.example.com/live3.mp4"]}}},
        ],
        "video": {"playAddr": {"urlList": ["https://v.example.com/note.mp4"]}},
    }


def _make_snake_detail():
    """旧版 snake_case 结构。"""
    return {
        "aweme_id": "7666381907030529443",
        "desc": "旧结构笔记",
        "author": {"nickname": "旧作者", "unique_id": "old"},
        "images": [{"download_url_list": ["http://p3.example.com/old1.jpg"],
                    "url_list": ["http://p3.example.com/old1_small.jpg"]}],
        "video": {"play_addr": {"url_list": ["http://v.example.com/old.mp4"]}},
    }


def _make_pace_f_html(detail):
    """构造真实页面的 pace_f 数据块：JS 字符串内 JSON 转义两层。"""
    flight = '7:["$","$L9",null,{"awemeId":"%s","aweme":%s}]' % (
        detail["awemeId"],
        json.dumps({"statusCode": 0, "detail": detail}, ensure_ascii=False),
    )
    # JS 字符串字面量内容（外层引号由页面原样携带）：对 " 与 \ 转义
    chunk = flight.replace("\\", "\\\\").replace('"', '\\"')
    return f'<html><body><script>self.__pace_f.push([1,"{chunk}"])</script></body></html>'


def _html_with_render_data(payload):
    return ('<html><body><script id="RENDER_DATA" type="application/json">'
            + quote(json.dumps(payload, ensure_ascii=False))
            + "</script></body></html>")


# ---------------- is_douyin_note_url ----------------

class TestIsDouyinNoteUrl:
    @pytest.mark.parametrize("url", [
        "https://www.douyin.com/note/7666381907030529443",
        "https://www.douyin.com/note/7666381907030529443?previous_page=web_code_link",
        "http://douyin.com/note/12345",
    ])
    def test_note_urls(self, url):
        assert is_douyin_note_url(url) is True

    @pytest.mark.parametrize("url", [
        "https://www.douyin.com/video/7666381907030529443",
        "https://v.douyin.com/BZjp8nC95xM/",       # 短链需展开后才能判定
        "https://www.douyin.com/user/MS4wLjA",
        "",                                         # 空串
        None,                                       # None
    ])
    def test_non_note_urls(self, url):
        assert is_douyin_note_url(url) is False


# ---------------- detect_jsvm_challenge ----------------

class TestDetectJsvmChallenge:
    def test_challenge_page_detected(self):
        # 真实挑战页：空 body + _$jsvmprt 混淆 VM 脚本
        html = '<html><head></head><body></body> <script> var glb;(glb="undefined"==typeof window?global:window)._$jsvmprt=function(b,e,f){}</script></html>'
        assert detect_jsvm_challenge(html) is True

    def test_normal_page_returns_false(self):
        html = "<html><body><title>抖音图文</title></body></html>"
        assert detect_jsvm_challenge(html) is False

    def test_empty_html(self):
        assert detect_jsvm_challenge("") is False
        assert detect_jsvm_challenge(None) is False


# ---------------- parse_render_data ----------------

class TestParseRenderData:
    def test_render_data_script(self):
        html = _html_with_render_data({"aweme": {"detail": {"aweme_id": "123"}}})
        data = parse_render_data(html)
        assert data == {"aweme": {"detail": {"aweme_id": "123"}}}

    def test_render_data_with_cjk(self):
        html = _html_with_render_data({"aweme": {"detail": {"desc": "没标题"}}})
        data = parse_render_data(html)
        assert data["aweme"]["detail"]["desc"] == "没标题"

    def test_router_data_fallback(self):
        payload = {"loaderData": {"note_(id)/page": {"item": {"aweme_id": "456"}}}}
        html = f"<html><script>window._ROUTER_DATA = {json.dumps(payload, ensure_ascii=False)}</script></html>"
        data = parse_render_data(html)
        assert data == payload

    def test_no_embedded_data_returns_none(self):
        assert parse_render_data("<html><body>nothing</body></html>") is None

    def test_empty_or_none_html(self):
        assert parse_render_data("") is None
        assert parse_render_data(None) is None


# ---------------- parse_pace_f_detail（新版页面主路径） ----------------

class TestParsePaceFDetail:
    def test_pace_f_chunk_parsed(self):
        detail = _make_camel_detail()
        html = _make_pace_f_html(detail)
        got = parse_pace_f_detail(html)
        assert got is not None
        assert got["awemeId"] == "7666381907030529443"
        assert len(got["images"]) == 3

    def test_broken_chunk_skipped(self):
        # 坏 chunk 跳过，能解析后续正常 chunk
        detail = _make_camel_detail()
        good_chunk = _make_pace_f_html(detail)
        bad = '<script>self.__pace_f.push([1,"bad{unclosed"])</script>'
        assert parse_pace_f_detail(bad + good_chunk) is not None

    def test_no_aweme_in_chunk_returns_none(self):
        html = '<script>self.__pace_f.push([1,"0:[\\"other\\"]"])</script>'
        assert parse_pace_f_detail(html) is None

    def test_empty_html_returns_none(self):
        assert parse_pace_f_detail("") is None
        assert parse_pace_f_detail(None) is None


# ---------------- extract_note_detail（总入口） ----------------

class TestExtractNoteDetail:
    def test_pace_f_path_priority_fallback(self):
        # RENDER_DATA 无详情（新版页面里它只是 app 配置）→ 落到 pace_f
        detail = _make_camel_detail()
        pace_html = _make_pace_f_html(detail)
        app_config = _html_with_render_data({"app": {"isLogin": True}})
        got = extract_note_detail(app_config + pace_html)
        assert got is not None
        assert got["awemeId"] == "7666381907030529443"

    def test_render_data_path_still_works(self):
        data = {"aweme": {"detail": _make_snake_detail()}}
        got = extract_note_detail(_html_with_render_data(data))
        assert got is not None
        assert got["aweme_id"] == "7666381907030529443"

    def test_nothing_found_returns_none(self):
        assert extract_note_detail("<html><body>jsvm 挑战页</body></html>") is None


# ---------------- extract_aweme_detail ----------------

class TestExtractAwemeDetail:
    def test_snake_case_detail(self):
        detail = _make_snake_detail()
        data = {"aweme": {"detail": detail}}
        assert extract_aweme_detail(data) is detail

    def test_camel_case_detail(self):
        detail = _make_camel_detail()
        data = {"loaderData": {"note_(id)/page": {"videoInfoRes": {"item_list": [detail]}}}}
        assert extract_aweme_detail(data) is detail

    def test_detail_without_media_is_not_matched(self):
        data = {"aweme": {"detail": {"aweme_id": "123"}}}
        assert extract_aweme_detail(data) is None

    def test_empty_data_returns_none(self):
        assert extract_aweme_detail({}) is None
        assert extract_aweme_detail(None) is None


# ---------------- extract_douyin_image_url ----------------

class TestExtractDouyinImageUrl:
    def test_camel_prefers_download_url_list(self):
        img = _make_camel_detail()["images"][0]
        assert extract_douyin_image_url(img) == "https://p3-sign.douyinpic.com/img1.jpg"

    def test_camel_falls_back_to_url_list(self):
        img = _make_camel_detail()["images"][1]
        assert extract_douyin_image_url(img) == "https://p3-sign.douyinpic.com/img2.jpg"

    def test_snake_case_still_supported(self):
        img = _make_snake_detail()["images"][0]
        assert extract_douyin_image_url(img) == "http://p3.example.com/old1.jpg"

    def test_protocol_relative_kept_for_caller_to_upgrade(self):
        img = {"urlList": ["//p3-douyin.example.com/img.jpg"]}
        assert extract_douyin_image_url(img) == "//p3-douyin.example.com/img.jpg"

    def test_empty_both_lists_returns_none(self):
        assert extract_douyin_image_url({}) is None
        assert extract_douyin_image_url({"urlList": []}) is None
        # 非 http 开头的条目跳过（防 data: 等）
        assert extract_douyin_image_url({"urlList": ["data:image/png;base64,xx"]}) is None


# ---------------- extract_live_photo_url ----------------

class TestExtractLivePhotoUrl:
    def test_camel_play_addr(self):
        img = _make_camel_detail()["images"][2]
        assert extract_live_photo_url(img) == "https://v.example.com/live3.mp4"

    def test_play_addr_as_string_list(self):
        # 新版 playAddr 也可能是纯字符串列表
        img = {"video": {"playAddr": ["https://v.example.com/a.mp4", "https://v.example.com/b.mp4"]}}
        assert extract_live_photo_url(img) == "https://v.example.com/a.mp4"

    def test_snake_case(self):
        img = {"video": {"play_addr": {"url_list": ["http://v.example.com/old.mp4"]}}}
        assert extract_live_photo_url(img) == "http://v.example.com/old.mp4"

    def test_no_live_photo_returns_none(self):
        img = _make_camel_detail()["images"][1]
        assert extract_live_photo_url(img) is None
        assert extract_live_photo_url({}) is None


# ---------------- extract_note_video_url ----------------

class TestExtractNoteVideoUrl:
    def test_camel_play_addr(self):
        video = _make_camel_detail()["video"]
        assert extract_note_video_url(video) == ("https://v.example.com/note.mp4", None)

    def test_snake_download_addr_fallback(self):
        video = {"download_addr": {"url_list": ["http://v.example.com/y.mp4"]}}
        assert extract_note_video_url(video) == ("http://v.example.com/y.mp4", None)

    def test_cover_urls_are_skipped(self):
        # 只有封面 URL 的视频：封面键不参与播放直链匹配
        video = {"coverUrlList": ["https://cover.example.com/c.jpg"]}
        url, _ = extract_note_video_url(video)
        assert url != "https://cover.example.com/c.jpg"

    def test_no_url_list_returns_reason(self):
        url, reason = extract_note_video_url({})
        assert url is None
        assert reason

    def test_none_input(self):
        url, reason = extract_note_video_url(None)
        assert url is None
        assert reason


# ---------------- extract_douyin_author ----------------

class TestExtractDouyinAuthor:
    def test_camel_author_info(self):
        assert extract_douyin_author(_make_camel_detail()) == "偏等落叶"

    def test_snake_author(self):
        assert extract_douyin_author(_make_snake_detail()) == "旧作者"

    def test_unique_id_fallback(self):
        detail = {"authorInfo": {"uniqueId": "photo_x"}}
        assert extract_douyin_author(detail) == "photo_x"

    def test_missing_author_returns_empty(self):
        assert extract_douyin_author({}) == ""
        assert extract_douyin_author(None) is "" or extract_douyin_author(None) == ""


# ---------------- resolve_douyin_basename ----------------

class TestResolveDouyinBasename:
    def test_uses_desc(self):
        base = resolve_douyin_basename(_make_camel_detail())
        assert base == "没关系 一个人也能走很远的路。#一个人拍照 #清冷感"

    def test_empty_desc_falls_back(self):
        assert resolve_douyin_basename({"desc": ""}) == "douyin"
        assert resolve_douyin_basename({}) == "douyin"

    def test_illegal_chars_sanitized(self):
        detail = {"desc": '标题: a/b\\c*d?"e<f>g|h'}
        base = resolve_douyin_basename(detail)
        for ch in '\\/*?:"<>|':
            assert ch not in base
