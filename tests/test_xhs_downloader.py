# -*- coding: utf-8 -*-
"""xhs_downloader.py 纯函数单元测试（无网络依赖）"""
import pytest

from xhs_downloader import detect_note_not_found, detect_risk_control, extract_xhs_author


# ---------------- detect_note_not_found ----------------

class TestDetectNoteNotFound:
    """真实短链 http://xhslink.com/o/AneP1u22JsY 落地到
    https://www.xiaohongshu.com/404?...&errorCode=-510001&...，
    且页面 title 为「你访问的页面不见了」。本类覆盖该 404 落地场景的三信号。"""

    def test_404_path_in_final_url(self):
        # 信号 1：落地 URL 路径含 /404
        url = "https://www.xiaohongshu.com/404?source=note&noteId=abc"
        assert detect_note_not_found("<html>任意内容</html>", url) is True

    def test_error_code_510001_in_query(self):
        # 信号 2：URL query 带 errorCode=-510001（不含 /404 路径）
        url = (
            "https://www.xiaohongshu.com/explore/abc"
            "?errorCode=-510001&noteId=6a42799f00000000110172d3"
        )
        assert detect_note_not_found("<html>正常页</html>", url) is True

    def test_error_code_510001_case_insensitive(self):
        # errorCode 在 query 里被小写化处理，大小写不敏感
        url = "https://www.xiaohongshu.com/explore/abc?ErrorCode=-510001"
        assert detect_note_not_found("<html>x</html>", url) is True

    def test_404_title_in_html(self):
        # 信号 3：页面 title 文案命中（URL 不含 /404、不含 errorCode）
        html = "<html><head><title>小红书 - 你访问的页面不见了</title></head></html>"
        assert detect_note_not_found(html, "https://www.xiaohongshu.com/explore/abc") is True

    def test_normal_note_page_returns_false(self):
        # 正常详情页：URL 无 /404、无 errorCode、title 文案不命中
        html = "<html><head><title>我的笔记</title></head></html>"
        url = "https://www.xiaohongshu.com/explore/6a42799f00000000110172d3"
        assert detect_note_not_found(html, url) is False

    def test_empty_inputs_returns_false(self):
        assert detect_note_not_found("", "") is False
        assert detect_note_not_found(None, None) is False

    def test_other_error_code_not_treated_as_not_found(self):
        # 其它 errorCode 不应误判为笔记不存在
        url = "https://www.xiaohongshu.com/explore/abc?errorCode=-100"
        assert detect_note_not_found("<html>正常页</html>", url) is False


# ---------------- detect_risk_control 回归 ----------------

class TestDetectRiskControlRegression:
    """确保新增 404 检测不破坏既有风控判定。"""

    def test_captcha_text_still_detected(self):
        html = "<html>请输入验证码</html>"
        assert detect_risk_control(html, type("R", (), {"url": "https://www.xiaohongshu.com/explore/abc"})()) is True

    def test_frequency_text_still_detected(self):
        html = "<html>访问过于频繁</html>"
        assert detect_risk_control(html, type("R", (), {"url": "https://www.xiaohongshu.com/explore/abc"})()) is True

    def test_404_page_not_treated_as_risk_control(self):
        # 404 落地页既无验证码文案、URL 也无 verify/login/captcha，
        # 风控分支应返回 False（交由 detect_note_not_found 处理）
        html = "<html><title>小红书 - 你访问的页面不见了</title></html>"
        resp = type("R", (), {"url": "https://www.xiaohongshu.com/404?errorCode=-510001"})()
        assert detect_risk_control(html, resp) is False


# ---------------- extract_xhs_author ----------------

class TestExtractXhsAuthor:
    def test_nickname_key(self):
        note = {"user": {"nickname": "小红书作者"}}
        assert extract_xhs_author(note) == "小红书作者"

    def test_nickname_camel_case_fallback(self):
        # 接口曾变更大小写，nickName 兜底
        note = {"user": {"nickName": "另一作者"}}
        assert extract_xhs_author(note) == "另一作者"

    def test_nickname_preferred_over_nickName(self):
        # 两个 key 都在时优先 nickname
        note = {"user": {"nickname": "优先这个", "nickName": "不该取"}}
        assert extract_xhs_author(note) == "优先这个"

    def test_missing_user_returns_empty(self):
        assert extract_xhs_author({}) == ""
        assert extract_xhs_author(None) == ""

    def test_missing_both_keys_returns_empty(self):
        assert extract_xhs_author({"user": {}}) == ""
        assert extract_xhs_author({"user": None}) == ""


# ---------------- ensure_https ----------------

class TestEnsureHttps:
    """媒体链接协议升级测试：http:// 和 // 统一升级为 https://，
    避免 CDN 图片走 80 端口因 DNS/网络问题失败。"""

    def test_http_upgraded_to_https(self):
        from xhs_downloader import ensure_https
        url = "http://sns-webpic-qc.xhscdn.com/202607100245/img.webp"
        assert ensure_https(url) == "https://sns-webpic-qc.xhscdn.com/202607100245/img.webp"

    def test_protocol_relative_upgraded(self):
        from xhs_downloader import ensure_https
        assert ensure_https("//sns-webpic-qc.xhscdn.com/img.webp") == "https://sns-webpic-qc.xhscdn.com/img.webp"

    def test_https_unchanged(self):
        from xhs_downloader import ensure_https
        url = "https://sns-webpic-qc.xhscdn.com/img.webp"
        assert ensure_https(url) == url

    def test_empty_returns_empty(self):
        from xhs_downloader import ensure_https
        assert ensure_https("") == ""

    def test_none_returns_none(self):
        from xhs_downloader import ensure_https
        assert ensure_https(None) is None


# ---------------- _is_transient_error ----------------

class TestIsTransientError:
    """瞬时网络错误判定测试，确保 DNS 失败、超时等错误能触发重试。"""

    def test_dns_failure_is_transient(self):
        from xhs_downloader import _is_transient_error
        err = Exception("HTTPConnectionPool: Failed to resolve 'sns-webpic-qc.xhscdn.com' "
                        "([Errno 7] No address associated with hostname)")
        assert _is_transient_error(err) is True

    def test_timeout_is_transient(self):
        from xhs_downloader import _is_transient_error
        assert _is_transient_error(Exception("ConnectTimeoutError: connect timeout")) is True

    def test_connection_reset_is_transient(self):
        from xhs_downloader import _is_transient_error
        assert _is_transient_error(Exception("ConnectionResetError: connection reset")) is True

    def test_http_404_not_transient(self):
        from xhs_downloader import _is_transient_error
        err = Exception("404 Client Error: Not Found")
        assert _is_transient_error(err) is False

    def test_http_403_not_transient(self):
        from xhs_downloader import _is_transient_error
        err = Exception("403 Client Error: Forbidden")
        assert _is_transient_error(err) is False


# ---------------- extract_video_url ----------------

class TestExtractVideoUrl:
    """视频直链解析测试，覆盖三个分支：h264 masterUrl / 顶层 url / 都没有。"""

    def test_h264_master_url(self):
        from xhs_downloader import extract_video_url
        video = {"media": {"stream": {"h264": [{"masterUrl": "https://cdn.example.com/v.mp4"}]}}}
        url, reason = extract_video_url(video)
        assert url == "https://cdn.example.com/v.mp4"
        assert reason is None

    def test_h264_list_missing_master_url(self):
        from xhs_downloader import extract_video_url
        video = {"media": {"stream": {"h264": [{"backupUrls": ["x"]}]}}}
        url, reason = extract_video_url(video)
        assert url is None
        assert reason == "h264 流存在但缺少 masterUrl"

    def test_h264_first_element_none(self):
        # h264 列表首元素为 None 时不崩溃，走兜底
        from xhs_downloader import extract_video_url
        video = {"media": {"stream": {"h264": [None]}}, "url": "https://cdn.example.com/fallback.mp4"}
        url, reason = extract_video_url(video)
        assert url is None
        assert reason == "h264 流存在但缺少 masterUrl"

    def test_fallback_to_top_level_url(self):
        from xhs_downloader import extract_video_url
        video = {"url": "https://cdn.example.com/direct.mp4"}
        url, reason = extract_video_url(video)
        assert url == "https://cdn.example.com/direct.mp4"
        assert reason is None

    def test_no_stream_no_url_returns_reason(self):
        from xhs_downloader import extract_video_url
        url, reason = extract_video_url({})
        assert url is None
        assert reason == "无 stream/h264 也无顶层 url"

    def test_none_video_returns_reason(self):
        from xhs_downloader import extract_video_url
        url, reason = extract_video_url(None)
        assert url is None
        assert reason == "无 stream/h264 也无顶层 url"

    def test_empty_h264_list_falls_back_to_url(self):
        # h264 为空列表时不进 h264 分支，走顶层 url 兜底
        from xhs_downloader import extract_video_url
        video = {"media": {"stream": {"h264": []}}, "url": "https://cdn.example.com/fallback.mp4"}
        url, reason = extract_video_url(video)
        assert url == "https://cdn.example.com/fallback.mp4"
        assert reason is None

    def test_h264_not_list_falls_back_to_url(self):
    # h264 不是 list（如误传 dict）时不进 h264 分支
        from xhs_downloader import extract_video_url
        video = {"media": {"stream": {"h264": "not-a-list"}}, "url": "https://cdn.example.com/fallback.mp4"}
        url, reason = extract_video_url(video)
        assert url == "https://cdn.example.com/fallback.mp4"
        assert reason is None


# ---------------- 标题兜底逻辑 ----------------

class TestTitleFallback:
    """标题为空/None/纯特殊字符时应回退 xhs_<noteId>，而非 untitled。
    真实案例：http://xhslink.com/o/6DR1V8jhJ56 的 title 返回 "/"，清洗后为空。"""

    def _safe_title(self, title, note_id="abc123"):
        from utils import sanitize_filename
        return sanitize_filename(title, default="") or f"xhs_{note_id}"

    def test_slash_title_falls_back(self):
        # 真实案例：title 返回 "/"
        assert self._safe_title("/") == "xhs_abc123"

    def test_none_title_falls_back(self):
        assert self._safe_title(None) == "xhs_abc123"

    def test_empty_string_falls_back(self):
        assert self._safe_title("") == "xhs_abc123"

    def test_whitespace_falls_back(self):
        assert self._safe_title("   ") == "xhs_abc123"

    def test_pure_special_chars_fall_back(self):
        assert self._safe_title("*?:<>|") == "xhs_abc123"

    def test_normal_title_preserved(self):
        assert self._safe_title("我的笔记") == "我的笔记"

    def test_title_with_special_chars_cleaned(self):
        # 含特殊字符但清洗后非空，保留
        assert self._safe_title("你好/世界") == "你好世界"
