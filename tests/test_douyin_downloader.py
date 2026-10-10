# -*- coding: utf-8 -*-
"""douyin_downloader.py 纯函数单元测试（无网络依赖）"""
import json
from urllib.parse import quote

import pytest

from douyin_downloader import (
    detect_jsvm_challenge,
    extract_aweme_detail,
    extract_douyin_author,
    extract_douyin_image_url,
    extract_live_photo_url,
    extract_note_video_url,
    is_douyin_note_url,
    parse_render_data,
    resolve_douyin_basename,
)


def _make_detail():
    return {
        "aweme_id": "7666381907030529443",
        "desc": "没关系 一个人也能走很远的路。# 一个人拍照 # 清冷感",
        "author": {"nickname": "某摄影师", "unique_id": "photo_x"},
        "images": [
            {"download_url_list": ["http://p3-douyin.example.com/img1.jpg"],
             "url_list": ["http://p3-douyin.example.com/img1_small.jpg"]},
            {"url_list": ["http://p3-douyin.example.com/img2.jpg"]},
            {"url_list": ["http://p3-douyin.example.com/img3.jpg"],
             "video": {"play_addr": {"url_list": ["http://v-douyin.example.com/live3.mp4"]}}},
        ],
    }


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
        # 中文字段经 URL 编码后仍能还原
        html = _html_with_render_data({"aweme": {"detail": {"aweme_id": "123", "desc": "没标题"}}})
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


# ---------------- extract_aweme_detail ----------------

class TestExtractAwemeDetail:
    def test_normative_render_data_path(self):
        # RENDER_DATA 常见形态：aweme.detail
        detail = _make_detail()
        data = {"aweme": {"detail": detail}}
        assert extract_aweme_detail(data) is detail

    def test_router_data_item_list_path(self):
        # _ROUTER_DATA 形态：loaderData.<route>.item_list[0]
        detail = _make_detail()
        data = {"loaderData": {"note_(id)/page": {"videoInfoRes": {"item_list": [detail]}}}}
        assert extract_aweme_detail(data) is detail

    def test_detail_without_media_is_not_matched(self):
        # aweme_id 但无 images/video → 跳过（可能是列表条目模版而非详情）
        data = {"aweme": {"detail": {"aweme_id": "123"}}}
        assert extract_aweme_detail(data) is None

    def test_empty_data_returns_none(self):
        assert extract_aweme_detail({}) is None
        assert extract_aweme_detail(None) is None


# ---------------- extract_douyin_image_url ----------------

class TestExtractDouyinImageUrl:
    def test_prefers_download_url_list(self):
        img = _make_detail()["images"][0]
        assert extract_douyin_image_url(img) == "http://p3-douyin.example.com/img1.jpg"

    def test_falls_back_to_url_list(self):
        img = _make_detail()["images"][1]
        assert extract_douyin_image_url(img) == "http://p3-douyin.example.com/img2.jpg"

    def test_protocol_relative_kept_for_caller_to_upgrade(self):
        img = {"url_list": ["//p3-douyin.example.com/img.jpg"]}
        assert extract_douyin_image_url(img) == "//p3-douyin.example.com/img.jpg"

    def test_empty_both_lists_returns_none(self):
        assert extract_douyin_image_url({}) is None
        assert extract_douyin_image_url({"url_list": []}) is None
        # 非 http 开头的条目跳过（防 data: 等）
        assert extract_douyin_image_url({"url_list": ["data:image/png;base64,xx"]}) is None


# ---------------- extract_live_photo_url ----------------

class TestExtractLivePhotoUrl:
    def test_live_photo_present(self):
        img = _make_detail()["images"][2]
        assert extract_live_photo_url(img) == "http://v-douyin.example.com/live3.mp4"

    def test_no_live_photo_returns_none(self):
        img = _make_detail()["images"][1]
        assert extract_live_photo_url(img) is None
        assert extract_live_photo_url({}) is None


# ---------------- extract_note_video_url ----------------

class TestExtractNoteVideoUrl:
    def test_play_addr(self):
        video = {"play_addr": {"url_list": ["http://v.example.com/x.mp4", "http://v2.example.com/x.mp4"]}}
        assert extract_note_video_url(video) == ("http://v.example.com/x.mp4", None)

    def test_download_addr_fallback(self):
        video = {"download_addr": {"url_list": ["http://v.example.com/y.mp4"]}}
        assert extract_note_video_url(video) == ("http://v.example.com/y.mp4", None)

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
    def test_nickname(self):
        assert extract_douyin_author(_make_detail()) == "某摄影师"

    def test_unique_id_fallback(self):
        detail = {"author": {"unique_id": "photo_x"}}
        assert extract_douyin_author(detail) == "photo_x"

    def test_missing_author_returns_empty(self):
        assert extract_douyin_author({}) == ""
        assert extract_douyin_author(None) == ""


# ---------------- resolve_douyin_basename ----------------

class TestResolveDouyinBasename:
    def test_uses_desc(self):
        base = resolve_douyin_basename(_make_detail())
        assert base == "没关系 一个人也能走很远的路。# 一个人拍照 # 清冷感"

    def test_empty_desc_falls_back(self):
        assert resolve_douyin_basename({"desc": ""}) == "douyin"
        assert resolve_douyin_basename({}) == "douyin"

    def test_illegal_chars_sanitized(self):
        detail = {"desc": '标题: a/b\\c*d?"e<f>g|h'}
        base = resolve_douyin_basename(detail)
        for ch in '\\/*?:"<>|':
            assert ch not in base
