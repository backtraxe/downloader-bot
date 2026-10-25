# -*- coding: utf-8 -*-
"""core.douyin_downloader.py 纯函数单元测试（无网络依赖）。

fixture 结构来自 2026 年真实 douyin.com/note 页面：
- 新版 React Flight 页：详情在 self.__pace_f.push 流式数据块，
  字段 camelCase（awemeId/images[].downloadUrlList/authorInfo）
- 旧版 RENDER_DATA 页：详情在 JSON，字段 snake_case（aweme_id/download_url_list/author）
"""

import json
import os

from urllib.parse import quote

import pytest

from core.douyin_downloader import (
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
            {
                "uri": "tos-cn-i-0813/abc",
                "downloadUrlList": ["https://p3-sign.douyinpic.com/img1.jpg"],
                "urlList": ["https://p3-sign.douyinpic.com/img1_small.jpg"],
            },
            {"urlList": ["https://p3-sign.douyinpic.com/img2.jpg"]},
            {
                "urlList": ["https://p3-sign.douyinpic.com/img3.jpg"],
                "video": {"playAddr": {"urlList": ["https://v.example.com/live3.mp4"]}},
            },
        ],
        "video": {"playAddr": {"urlList": ["https://v.example.com/note.mp4"]}},
    }


def _make_snake_detail():
    """旧版 snake_case 结构。"""
    return {
        "aweme_id": "7666381907030529443",
        "desc": "旧结构笔记",
        "author": {"nickname": "旧作者", "unique_id": "old"},
        "images": [
            {
                "download_url_list": ["http://p3.example.com/old1.jpg"],
                "url_list": ["http://p3.example.com/old1_small.jpg"],
            }
        ],
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
    return (
        f'<html><body><script>self.__pace_f.push([1,"{chunk}"])</script></body></html>'
    )


def _html_with_render_data(payload):
    return (
        '<html><body><script id="RENDER_DATA" type="application/json">'
        + quote(json.dumps(payload, ensure_ascii=False))
        + "</script></body></html>"
    )


# ---------------- is_douyin_note_url ----------------


class TestIsDouyinNoteUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.douyin.com/note/7666381907030529443",
            "https://www.douyin.com/note/7666381907030529443?previous_page=web_code_link",
            "http://douyin.com/note/12345",
        ],
    )
    def test_note_urls(self, url):
        assert is_douyin_note_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.douyin.com/video/7666381907030529443",
            "https://v.douyin.com/BZjp8nC95xM/",  # 短链需展开后才能判定
            "https://www.douyin.com/user/MS4wLjA",
            "",  # 空串
            None,  # None
        ],
    )
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
        data = {
            "loaderData": {"note_(id)/page": {"videoInfoRes": {"item_list": [detail]}}}
        }
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
        assert (
            extract_douyin_image_url({"urlList": ["data:image/png;base64,xx"]}) is None
        )


# ---------------- extract_live_photo_url ----------------


class TestExtractLivePhotoUrl:
    def test_camel_play_addr(self):
        img = _make_camel_detail()["images"][2]
        assert extract_live_photo_url(img) == "https://v.example.com/live3.mp4"

    def test_play_addr_as_string_list(self):
        # 新版 playAddr 也可能是纯字符串列表
        img = {
            "video": {
                "playAddr": [
                    "https://v.example.com/a.mp4",
                    "https://v.example.com/b.mp4",
                ]
            }
        }
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
        assert extract_douyin_author(None) == ""


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


class TestDetectJsvmChallengeCaptchaPage:
    """真实 Termux 样本：6329 字节验证码中间页（IP+Cookie 被限流触发）。"""

    _CAPTCHA_HTML = """<html>
<head>
<meta charset="utf-8">
<title>验证码中间页</title>
<script charset="utf-8" nonce="argus-csp-token" src="https://lf-cdn-tos.bytescm.com/obj/static/sec_sdk_build/3.5.2/captcha/index.js"></script>
</head><body></body></html>"""

    def test_captcha_page_by_title(self):
        assert detect_jsvm_challenge(self._CAPTCHA_HTML) is True

    def test_sdk_script_alone_is_not_captcha(self):
        # 真实笔记页同样引用 sec_sdk captcha 脚本，只有路径不足以判定验证码页
        # （2026-10 实测翻车：带 cookie 拿到完整页面仍被误判为验证码页）
        html = '<html><title>正常笔记页</title><script src="https://lf-cdn-tos.bytescm.com/obj/static/sec_sdk_build/3.5.2/captcha/index.js"></script></html>'
        assert detect_jsvm_challenge(html) is False

    def test_jsvm_challenge_still_detected(self):
        html = "<body></body><script>var glb;(glb=x)._$jsvmprt=function(){}</script>"
        assert detect_jsvm_challenge(html) is True


class TestFetchPageCaptchaRetry:
    """验证码中间页是概率性拦截，页面请求应退避重试而不是一次失败。（real: 4 次 1 次命中）"""

    class _FakeResp:
        def __init__(self, text, url="https://www.douyin.com/note/123"):
            self.text = text
            self.url = url
            self.status_code = 200

        def raise_for_status(self):
            pass

    class _FakeSession:
        def __init__(self, texts):
            self._texts = list(texts)
            self.calls = 0

        def get(self, url, **kwargs):
            self.calls += 1
            text = self._texts.pop(0) if self._texts else "ok"
            return TestFetchPageCaptchaRetry._FakeResp(text)

        def close(self):
            pass

    _CAPTCHA = '<html><title>验证码中间页</title><script src="https://lf-cdn-tos.bytescm.com/obj/static/sec_sdk_build/3.5.2/captcha/index.js"></script></html>'

    def test_captcha_retried_until_pass(self, monkeypatch):
        import core.douyin_downloader as dd

        monkeypatch.setattr("core.douyin_downloader.time.sleep", lambda s: None)
        session = self._FakeSession(
            [self._CAPTCHA, self._CAPTCHA, "正常页面 __pace_f 数据"]
        )
        monkeypatch.setattr(
            "core.douyin_downloader.http_client.Session", lambda: session
        )
        r = dd._fetch_page(session, "https://www.douyin.com/note/123", {})
        assert r is not None
        assert "__pace_f" in r.text
        assert session.calls == 3

    def test_captcha_exhausted_returns_last_response(self, monkeypatch):
        # 重试用尽仍全命中：返回最后响应（由调用方给出限流提示），不返回 None
        import core.douyin_downloader as dd

        monkeypatch.setattr("core.douyin_downloader.time.sleep", lambda s: None)
        session = self._FakeSession([self._CAPTCHA] * 5)
        monkeypatch.setattr(
            "core.douyin_downloader.http_client.Session", lambda: session
        )
        r = dd._fetch_page(session, "https://www.douyin.com/note/123", {})
        assert r is not None
        assert "验证码中间页" in r.text


class TestFetchAwemeDetail:
    """detail API：2xx 非 JSON（空响应/拦截页）是概率性限流信号——
    应退避重试并在日志里带出服务端返回头部，而不是一次判死。
    （2026-10 实测：同链接 Mac 3/3 过，另一网络环境 0.2s 空响应失败）"""

    _OK = json.dumps({"status_code": 0, "aweme_detail": {"aweme_id": "1"}})

    class _Resp:
        def __init__(self, text, status=200):
            self.text = text
            self.status_code = status
            self.url = "https://www.douyin.com/aweme/v1/web/aweme/detail/"

        def raise_for_status(self):
            if self.status_code >= 400:
                raise Exception(f"HTTP {self.status_code}")

    class _Session:
        def __init__(self, items):
            self._items = list(items)
            self.calls = 0

        def get(self, url, **kwargs):
            self.calls += 1
            item = self._items.pop(0) if self._items else TestFetchAwemeDetail._OK
            return item(self) if callable(item) else item

        def close(self):
            pass

    def _resp(self, text, status=200):
        return self._Resp(text, status)

    def _run(self, monkeypatch, session):
        import core.douyin_downloader as dd

        monkeypatch.setattr("core.douyin_downloader.time.sleep", lambda s: None)
        return dd.fetch_aweme_detail("7691296880559540623", "ttwid=x", session)

    def test_success(self, monkeypatch):
        session = self._Session([self._resp(self._OK)])
        assert self._run(monkeypatch, session) == {"aweme_id": "1"}
        assert session.calls == 1

    def test_empty_body_retried_then_success(self, monkeypatch):
        # 空响应是限流 blip：重试后成功
        session = self._Session([self._resp(""), self._resp(self._OK)])
        assert self._run(monkeypatch, session) == {"aweme_id": "1"}
        assert session.calls == 2

    def test_html_block_page_retried(self, monkeypatch):
        html = "<html><head></head><body>验证</body></html>"
        session = self._Session([self._resp(html), self._resp(self._OK)])
        assert self._run(monkeypatch, session) == {"aweme_id": "1"}
        assert session.calls == 2

    def test_non_json_exhausted_raises_cookie_expired(self, monkeypatch):
        # 重试全空 = ttwid/sessionid 失效（"旧 Cookie 突然全 200 空"）：
        # 上抛 _CookieExpiredError 让调用方把刷新指引写进用户可见的失败消息
        import core.douyin_downloader as dd

        session = self._Session([self._resp("")] * 5)
        with pytest.raises(dd._CookieExpiredError):
            self._run(monkeypatch, session)
        assert session.calls == 3  # 默认重试 3 次

    def test_argus_403_not_retried(self, monkeypatch):
        resp = self._resp("Blocked by ArgusSecurityPlugin Uifid Not Found", 403)
        session = self._Session([resp])
        assert self._run(monkeypatch, session) is None
        assert session.calls == 1  # 确定性拒绝，不重试

    def test_status_code_nonzero_returns_none(self, monkeypatch):
        body = json.dumps({"status_code": 8, "status_msg": "flower"})
        session = self._Session([self._resp(body)])
        assert self._run(monkeypatch, session) is None
        assert session.calls == 1


class TestCookieRefreshHint:
    """Cookie 失效必须把可执行的刷新指引写进 DownloadResult.message——
    bot 用户只能看到结果消息，日志里的提示到不了聊天窗口。"""

    _JSVM_HTML = (
        "<html><head></head><body></body>"
        "<script>var glb;(glb=x)._$jsvmprt=function(){}</script></html>"
    )

    class _Resp:
        def __init__(self, text):
            self.text = text
            self.url = "https://www.douyin.com/note/7691296880559540623"

    def _note_result(self, monkeypatch, api_detail):
        import core.douyin_downloader as dd

        monkeypatch.setattr(
            dd, "_fetch_page", lambda *a, **k: self._Resp(self._JSVM_HTML)
        )
        if isinstance(api_detail, Exception):

            def fake_fetch(*a, **k):
                raise api_detail

        else:
            fake_fetch = lambda *a, **k: api_detail  # noqa: E731
        monkeypatch.setattr(dd, "fetch_aweme_detail", fake_fetch)
        return dd.download_douyin_note(
            "https://www.douyin.com/note/7691296880559540623", "ttwid=x"
        )

    def test_note_jsvm_page_hint(self, monkeypatch):
        # 笔记页 jsvm 挑战 + API 兜底拿不到详情 → 提示刷新命令
        from core.result import DownloadStatus

        result = self._note_result(monkeypatch, api_detail=None)
        assert result.status == DownloadStatus.FAILED
        assert "cookie_login douyin" in result.message
        assert "cookie_import douyin" in result.message

    def test_note_api_fallback_cookie_expired_hint(self, monkeypatch):
        # API 兜底持续 200 空（Cookie 失效的典型形态）→ 明确说 Cookie 已失效
        from core.result import DownloadStatus

        import core.douyin_downloader as dd

        result = self._note_result(
            monkeypatch, api_detail=dd._CookieExpiredError("200 非 JSON（0 字节）")
        )
        assert result.status == DownloadStatus.FAILED
        assert "Cookie 已失效" in result.message
        assert "cookie_login douyin" in result.message

    def test_video_api_cookie_expired_hint(self, monkeypatch):
        import core.douyin_downloader as dd
        from core.result import DownloadStatus

        def boom(aweme_id, cookie, session):
            raise dd._CookieExpiredError("200 非 JSON 响应（0 字节）")

        monkeypatch.setattr(dd, "fetch_aweme_detail", boom)
        result = dd.download_douyin_video(
            "https://www.douyin.com/video/7691296880559540623", "ttwid=x"
        )
        assert result.status == DownloadStatus.FAILED
        assert "Cookie 已失效" in result.message
        assert "cookie_login douyin" in result.message


class TestExtractDouyinImageUrlWatermarkFilter:
    """downloadUrlList 在真实页面是 ~tplv-dy-water-* 水印端，urlList 才是无水印端。"""

    def test_watermark_variant_skipped(self):
        img = {
            "downloadUrlList": [
                "https://p3-sign.example.com/x~tplv-dy-water-v2:abc.webp"
            ],
            "urlList": ["https://p3-sign.example.com/x~tplv-dy-aweme-images:q75.webp"],
        }
        assert (
            extract_douyin_image_url(img)
            == "https://p3-sign.example.com/x~tplv-dy-aweme-images:q75.webp"
        )

    def test_jpeg_preferred_over_webp_when_same_quality_tier(self):
        # 真实页面的无水印端是 q75.webp + q75.jpeg 同档双格式——jpeg 保真上限更高
        img = {
            "downloadUrlList": ["https://x.example.com/a~tplv-dy-water-v2:a.webp"],
            "urlList": [
                "https://x.example.com/a~tplv-dy-aweme-images:q75.webp",
                "https://x.example.com/a~tplv-dy-aweme-images:q75.jpeg",
            ],
        }
        assert (
            extract_douyin_image_url(img)
            == "https://x.example.com/a~tplv-dy-aweme-images:q75.jpeg"
        )

    def test_all_watermark_falls_back_to_first(self):
        # 全是水印版时宁可用水印版也不能没图
        img = {"urlList": ["https://x.example.com/a~tplv-dy-water-v2:a"]}
        assert (
            extract_douyin_image_url(img)
            == "https://x.example.com/a~tplv-dy-water-v2:a"
        )

    def test_only_download_list_watermark_filtered(self):
        img = {
            "downloadUrlList": ["https://x.example.com/a~tplv-dy-water-v2:a"],
            "urlList": [],
        }
        # 有水印 fallback 仍然存在
        assert (
            extract_douyin_image_url(img)
            == "https://x.example.com/a~tplv-dy-water-v2:a"
        )


# ---------------- 视频（/video/）签名详情 API 链路 ----------------


class TestIsDouyinVideoUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.douyin.com/video/7556303777536740671",
            "https://www.douyin.com/video/7556303777536740671?previous_page=web_code_link",
            "http://douyin.com/video/12345",
        ],
    )
    def test_video_urls(self, url):
        from core.douyin_downloader import is_douyin_video_url

        assert is_douyin_video_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.douyin.com/note/7666381907030529443",
            "https://v.douyin.com/BZjp8nC95xM/",
            "https://www.douyin.com/user/MS4wLjA",
            "",
            None,
        ],
    )
    def test_non_video_urls(self, url):
        from core.douyin_downloader import is_douyin_video_url

        assert is_douyin_video_url(url) is False


class TestBuildSignedDetailUrl:
    """detail API 三件套签名（a_bogus + 伪 msToken + x-secsdk-web-signature）。

    x-secsdk-web-signature 是解密 Argus 403 "Uifid Not Found" 的关键
    （2026-10-14 实测：yt-dlp 缺它在 detail API 一律 403）。
    """

    _COOKIES = {"UIFID_TEMP": "uid_test_123", "s_v_web_id": "verify_test_x"}

    def _url_and_headers(self):
        from core.douyin_downloader import build_signed_detail_url

        return build_signed_detail_url("7556303777536740671", self._COOKIES)

    def test_carries_required_params(self):
        from urllib.parse import parse_qs, urlsplit

        url, _ = self._url_and_headers()
        q = parse_qs(urlsplit(url).query)
        assert urlsplit(url).path.endswith("/aweme/v1/web/aweme/detail/")
        for key in (
            "aweme_id",
            "aid",
            "msToken",
            "a_bogus",
            "verifyFp",
            "fp",
            "uifid",
            "timestamp",
            "x-secsdk-web-signature",
        ):
            assert key in q, f"缺少签名参数 {key}"
        assert q["aweme_id"] == ["7556303777536740671"]
        assert q["aid"] == ["6383"]
        # 伪 msToken：126 位随机串 + "=="
        assert len(q["msToken"][0]) == 128
        # verifyFp/fp 取 Cookie 里的 s_v_web_id 原值
        assert q["verifyFp"] == ["verify_test_x"]
        assert q["fp"] == ["verify_test_x"]
        # uifid 取 Cookie 里的 UIFID/UIFID_TEMP
        assert q["uifid"] == ["uid_test_123"]

    def test_websign_md5_matches_preimage(self):
        import hashlib

        url, extra_headers = self._url_and_headers()
        query = url.split("?", 1)[1]
        marker = "&x-secsdk-web-signature="
        assert marker in query
        body, sig = query.rsplit(marker, 1)
        ts = dict(p.split("=", 1) for p in body.split("&")).get("timestamp")
        from core.douyin_sign.douyin_websign import SALT

        expected = hashlib.md5(f"uid_test_123_{ts}_{SALT}_{body}".encode()).hexdigest()
        assert sig == expected
        # 同名参数也复制进请求头（页面自己的行为，更像真浏览器）
        assert extra_headers["x-secsdk-web-signature"] == sig
        assert extra_headers["uifid"] == "uid_test_123"

    def test_missing_uifid_falls_back_to_unsigned(self):
        # 没有 UIFID 时仍给出 a_bogus 签名 URL（未保护端点可用），但不伪造 websign
        from core.douyin_downloader import build_signed_detail_url

        url, extra_headers = build_signed_detail_url("1", {"s_v_web_id": "x"})
        assert "a_bogus=" in url
        assert "x-secsdk-web-signature=" not in url
        assert extra_headers == {}


class TestMsTokenCookiePreferred:
    """msToken 生成策略对齐 Evil0ctal v5：Cookie 里现成的 msToken 优先用，
    缺失/为空才退回伪 126 随机串——平台"对多数端点收下伪 token、对部分
    端点会拒"，现值最稳；detail 端点对伪串不校验，故兜底仍可用。"""

    _AWEME_ID = "7556303777536740671"

    @staticmethod
    def _ms_token_of(url):
        from urllib.parse import parse_qs, urlsplit

        return parse_qs(urlsplit(url).query)["msToken"][0]

    @staticmethod
    def _assert_false_token_shape(token):
        import core.douyin_downloader as dd

        # 兜底伪串：126 位 _MS_TOKEN_ALPHABET 字符 + "==" 后缀
        assert len(token) == 128
        assert token.endswith("==")
        assert set(token[:-2]) <= set(dd._MS_TOKEN_ALPHABET)

    def test_existing_ms_token_helper_contract(self):
        from core.douyin_downloader import _existing_ms_token

        assert _existing_ms_token({"msToken": "ABC"}) == "ABC"
        # 空值视为无现值：返回 None 交由伪串兜底
        assert _existing_ms_token({"msToken": ""}) is None
        assert _existing_ms_token({}) is None
        assert _existing_ms_token(None) is None

    def test_cookie_ms_token_wins(self):
        # Cookie 现值优先：签名 URL 的 msToken 参数取 Cookie 里的原值
        from core.douyin_downloader import build_signed_detail_url

        cookies = {"UIFID_TEMP": "uid_test_123", "msToken": "ABC123"}
        url, _ = build_signed_detail_url(self._AWEME_ID, cookies)
        assert self._ms_token_of(url) == "ABC123"

    def test_empty_cookie_ms_token_falls_back_to_false_token(self):
        from core.douyin_downloader import build_signed_detail_url

        cookies = {"UIFID_TEMP": "uid_test_123", "msToken": ""}
        url, _ = build_signed_detail_url(self._AWEME_ID, cookies)
        self._assert_false_token_shape(self._ms_token_of(url))

    def test_no_cookie_ms_token_keeps_false_token(self):
        # 无 msToken 时保持现行为：伪串形态不受 cookie 其他键影响
        from core.douyin_downloader import build_signed_detail_url

        cookies = {"UIFID_TEMP": "uid_test_123", "ttwid": "t1"}
        url, _ = build_signed_detail_url(self._AWEME_ID, cookies)
        self._assert_false_token_shape(self._ms_token_of(url))

    def test_header_form_through_fetch_aweme_detail(self):
        # 真实调用形态端到端：fetch_aweme_detail 收 `Cookie:` 请求头整串，
        # 经 _cookie_dict 转 jar 后进 build_signed_detail_url——请求头里的
        # msToken 必须原样进最终签名 URL
        import core.douyin_downloader as dd

        class _Capture:
            def __init__(self):
                self.urls = []

            def get(self, url, **kwargs):
                self.urls.append(url)

                ok = json.dumps({"status_code": 0, "aweme_detail": {"aweme_id": "1"}})

                class R:
                    text = ok
                    status_code = 200

                    def raise_for_status(self):
                        pass

                return R()

        session = _Capture()
        cookie = "ttwid=t1; UIFID_TEMP=uid_test_123; msToken=ABC123"
        assert dd.fetch_aweme_detail(self._AWEME_ID, cookie, session) == {
            "aweme_id": "1"
        }
        assert self._ms_token_of(session.urls[0]) == "ABC123"


class TestExtractVideoPlayUrls:
    """视频直链候选：play_addr 无水印优先，过滤 playwm，bit_rate 兜底。"""

    def test_play_addr_url_list_in_order(self):
        from core.douyin_downloader import extract_video_play_urls

        video = {
            "play_addr": {
                "url_list": [
                    "https://v26-web.douyinvod.com/a",
                    "https://www.douyin.com/aweme/v1/play/?x",
                ]
            }
        }
        assert extract_video_play_urls(video) == [
            "https://v26-web.douyinvod.com/a",
            "https://www.douyin.com/aweme/v1/play/?x",
        ]

    def test_watermark_candidates_filtered(self):
        from core.douyin_downloader import extract_video_play_urls

        video = {
            "play_addr": {
                "url_list": [
                    "https://a.example.com/playwm/x",
                    "https://v26-web.douyinvod.com/clean",
                ]
            }
        }
        assert extract_video_play_urls(video) == ["https://v26-web.douyinvod.com/clean"]

    def test_falls_back_to_bit_rate(self):
        from core.douyin_downloader import extract_video_play_urls

        video = {
            "play_addr": {"url_list": []},
            "bit_rate": [
                {"play_addr": {"url_list": ["https://v9.example.com/hd"]}},
                {"play_addr": {"url_list": ["https://v9.example.com/sd"]}},
            ],
        }
        assert extract_video_play_urls(video) == ["https://v9.example.com/hd"]

    def test_empty_video_returns_empty(self):
        from core.douyin_downloader import extract_video_play_urls

        assert extract_video_play_urls({}) == []
        assert extract_video_play_urls(None) == []


# ---------------- download_file --force ----------------


class TestDownloadFileForce:
    """--force 强制重下：跳过幂等判断、覆盖旧文件、失败不破坏旧产物。"""

    @staticmethod
    def _fake_session(body, content_type="video/mp4"):
        class R:
            status_code = 200
            headers = {"Content-Type": content_type}

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size=8192):
                yield body

        class S:
            def get(self, url, **kw):
                return R()

        return S()

    def test_force_overwrites_existing_no_dup(self, tmp_path, monkeypatch):
        import core.douyin_downloader as mod

        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        target = str(tmp_path / "video.mp4")
        with open(target, "wb") as f:
            f.write(b"old")
        result = mod.download_file(
            self._fake_session(b"new-bytes"),
            "http://www.douyin.com/aweme/v1/media/video.mp4",
            target,
            {},
            force=True,
        )
        assert "[成功]" in result
        with open(target, "rb") as f:
            assert f.read() == b"new-bytes"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["video.mp4"]

    def test_force_failure_preserves_existing(self, tmp_path, monkeypatch):
        import core.douyin_downloader as mod

        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        target = str(tmp_path / "video.mp4")
        with open(target, "wb") as f:
            f.write(b"old")

        class S:
            def get(self, url, **kw):
                raise RuntimeError("boom")

        result = mod.download_file(
            S(),
            "http://www.douyin.com/aweme/v1/media/video.mp4",
            target,
            {},
            force=True,
        )
        assert "[失败]" in result
        with open(target, "rb") as f:
            assert f.read() == b"old"
        assert not os.path.exists(target + ".part")
