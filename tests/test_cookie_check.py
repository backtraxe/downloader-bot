# -*- coding: utf-8 -*-
"""cookie_check.py 离线测试：三格式归一、douyin/xiaohongshu 必需键规则、
未知站点的仅报告口径、CLI 退出码与输出（全部不联网、不产生真实 cookie）。"""

import json

import pytest

from core.tools.cookie_check import assess_cookie, main, parse_cookie_pairs

# 小红书 a1 生态口径约 52 位（>=30 视为足量）
_A1 = "1" * 52
_A1_SHORT = "1" * 10

_DOUYIN_HEADER = "UIFID=uifid-1; ttwid=ttwid-1; msToken=ms==token; sessionid=sess-1"
_DOUYIN_NETSCAPE = (
    "# Netscape HTTP Cookie File\n"
    ".douyin.com\tTRUE\t/\tTRUE\t4070908800\tUIFID\tuifid-1\n"
    ".douyin.com\tTRUE\t/\tFALSE\t4070908800\tttwid\tttwid-1\n"
    ".douyin.com\tTRUE\t/\tTRUE\t4070908800\tmsToken\tms==token\n"
    ".douyin.com\tTRUE\t/\tTRUE\t4070908800\tsessionid\tsess-1\n"
)
_DOUYIN_JSON = json.dumps(
    [
        {"name": "UIFID", "value": "uifid-1"},
        {"name": "ttwid", "value": "ttwid-1"},
        {"name": "sessionid", "value": "sess-1"},
        {"name": "msToken", "value": "ms==token"},
    ]
)

_XHS_JSON = json.dumps(
    [
        {"name": "a1", "value": _A1, "domain": ".xiaohongshu.com"},
        {"name": "web_session", "value": "ws-1", "domain": ".xiaohongshu.com"},
        {"name": "tracker", "value": "t", "domain": ".third-party.com"},
    ]
)
_XHS_JSON_WRAPPED = json.dumps(
    {"cookies": [{"name": "a1", "value": _A1}, {"name": "webId", "value": "wid-1"}]}
)


def _find(items, keyword):
    """按关键词（子串）定位检查项，返回 (检查项, 是否通过, 建议) 或 None。"""
    for label, ok, tip in items:
        if keyword in label:
            return label, ok, tip
    return None


def _has_gap(items):
    return any(not ok for _, ok, _ in items)


class TestParseCookiePairs:
    @pytest.mark.parametrize(
        "text",
        [
            _DOUYIN_HEADER,  # Cookie: 请求头整段
            "Cookie: " + _DOUYIN_HEADER,  # 带行首标签
            _DOUYIN_NETSCAPE,  # Netscape 7 列 tab
            _DOUYIN_HEADER.replace("; ", "\n"),  # 一对一行的松散粘贴
            _DOUYIN_JSON,  # Cookie-Editor 导出 JSON 数组
        ],
        ids=["header", "header-label", "netscape", "multiline", "json"],
    )
    def test_normalize_three_formats(self, text):
        pairs = parse_cookie_pairs(text)
        assert pairs["UIFID"] == "uifid-1"
        assert pairs["ttwid"] == "ttwid-1"
        assert pairs["sessionid"] == "sess-1"
        # 值里的 = 不截断（base64 的 == 保留完整）
        assert pairs["msToken"] == "ms==token"

    def test_first_occurrence_wins(self):
        assert parse_cookie_pairs("a=1; a=2") == {"a": "1"}

    def test_json_keeps_all_domains(self):
        # 健康检查只关心键名存在性，第三方域 cookie 也一并提取（与
        # cookie_import 写 xiaohongshu 文件时的按域过滤不同）
        assert parse_cookie_pairs(_XHS_JSON) == {
            "a1": _A1,
            "web_session": "ws-1",
            "tracker": "t",
        }

    def test_json_wrapped_cookies_key(self):
        # {"cookies": [...]} 包裹结构同样识别
        assert parse_cookie_pairs(_XHS_JSON_WRAPPED) == {"a1": _A1, "webId": "wid-1"}

    def test_json_tolerates_non_dict_entries(self):
        pairs = parse_cookie_pairs('[{"name": "a", "value": "1"}, 42, null]')
        assert pairs == {"a": "1"}

    def test_invalid_json_falls_back(self):
        # "[" 开头但不是合法 JSON：交回退路径而非崩溃
        assert parse_cookie_pairs("[oops") == {}

    def test_unrecognizable_text_returns_empty(self):
        assert parse_cookie_pairs("没有任何键值对的说明文字") == {}

    @pytest.mark.parametrize("text", ["", "   \n  ", None])
    def test_empty_input(self, text):
        assert parse_cookie_pairs(text) == {}

    def test_empty_value_normalized_to_empty(self):
        # 空值 cookie 记录为空串（门禁判定按"非空值"口径处理）
        assert parse_cookie_pairs("UIFID=; ttwid= ") == {"UIFID": "", "ttwid": ""}


class TestAssessDouyin:
    def test_healthy_netscape_all_pass_with_mstoken_info(self):
        items = assess_cookie("douyin", _DOUYIN_NETSCAPE)
        assert not _has_gap(items)
        assert _find(items, "文件")[1] is True
        assert _find(items, "UIFID")[1] is True
        assert _find(items, "ttwid")[1] is True
        # msToken 是信息项：存在时以通过姿态提示"将被优先使用"
        ms = _find(items, "msToken")
        assert ms is not None and ms[1] is True
        assert "已存在，将被优先使用" in ms[2]

    def test_healthy_without_mstoken_omits_info_item(self):
        items = assess_cookie("douyin", "UIFID=u; ttwid=t")
        assert not _has_gap(items)
        assert _find(items, "msToken") is None

    def test_missing_uifid_fails(self):
        items = assess_cookie("douyin", "ttwid=t; sessionid=s")
        assert _find(items, "UIFID")[1] is False
        assert _find(items, "ttwid")[1] is True
        assert _has_gap(items)

    def test_uifid_temp_counts(self):
        # AGENTS.md 口径：签名素材 UIFID / UIFID_TEMP 皆可（任意非空值）
        items = assess_cookie("douyin", "UIFID_TEMP=u; sessionid=s")
        assert _find(items, "UIFID")[1] is True
        assert not _has_gap(items)

    def test_sessionid_alone_passes_gate(self):
        items = assess_cookie("douyin", "UIFID=u; sessionid=s")
        assert _find(items, "ttwid")[1] is True
        assert not _has_gap(items)

    @pytest.mark.parametrize(
        "text",
        ["UIFID=u", "UIFID=u; foo=bar", "UIFID=u; ttwid=", "UIFID=u; sessionid="],
        ids=["none", "irrelevant", "empty-ttwid", "empty-sessionid"],
    )
    def test_missing_login_gate_fails(self, text):
        items = assess_cookie("douyin", text)
        assert _find(items, "ttwid")[1] is False
        assert _has_gap(items)

    @pytest.mark.parametrize("text", [None, ""])
    def test_missing_or_empty_file_all_fail(self, text):
        items = assess_cookie("douyin", text)
        assert _has_gap(items)
        assert all(ok is False for _, ok, _ in items)
        assert _find(items, "文件")[1] is False

    def test_suggestions_explain_gate_reasons(self):
        items = assess_cookie("douyin", "foo=bar")
        assert "Uifid" in _find(items, "UIFID")[2]  # Argus 403 的原文标记
        assert "200" in _find(items, "ttwid")[2]  # 过期即 200 空响应
        # 文件缺失场景的修复建议指向 cookie_import 入口
        missing = assess_cookie("douyin", None)
        assert "cookie_import" in _find(missing, "文件")[2]

    def test_never_leaks_cookie_values(self):
        items = assess_cookie("douyin", "UIFID=topsecret; ttwid=ttwid-1")
        assert all("topsecret" not in (label + tip) for label, _, tip in items)


class TestAssessXiaohongshu:
    def test_healthy_header_webid(self):
        items = assess_cookie("xiaohongshu", f"a1={_A1}; webId=wid")
        assert not _has_gap(items)

    def test_healthy_header_web_session(self):
        items = assess_cookie("xiaohongshu", f"a1={_A1}; web_session=ws")
        assert not _has_gap(items)

    def test_healthy_json_input(self):
        items = assess_cookie("xiaohongshu", _XHS_JSON)
        assert not _has_gap(items)

    def test_a1_at_30_boundary_passes(self):
        items = assess_cookie("xiaohongshu", "a1=" + "1" * 30 + "; webId=wid")
        assert _find(items, "a1")[1] is True

    def test_short_a1_fails(self):
        items = assess_cookie("xiaohongshu", f"a1={_A1_SHORT}; webId=wid")
        assert _find(items, "a1")[1] is False
        assert _find(items, "webId")[1] is True

    def test_missing_a1_fails(self):
        items = assess_cookie("xiaohongshu", "webId=wid")
        assert _find(items, "a1")[1] is False
        assert _has_gap(items)

    def test_missing_web_identity_fails(self):
        items = assess_cookie("xiaohongshu", f"a1={_A1}")
        assert _find(items, "webId")[1] is False
        assert _has_gap(items)

    @pytest.mark.parametrize("text", [None, ""])
    def test_missing_or_empty_file_all_fail(self, text):
        items = assess_cookie("xiaohongshu", text)
        assert _has_gap(items)
        assert all(ok is False for _, ok, _ in items)

    def test_never_leaks_cookie_values(self):
        items = assess_cookie("xiaohongshu", "a1=topsecret; webId=wid")
        assert all("topsecret" not in (label + tip) for label, _, tip in items)


class TestAssessUnknownSite:
    @pytest.mark.parametrize(
        "text",
        [None, "", "SESSDATA=s; bili_jct=j", "随便什么文本"],
        ids=["missing", "empty", "pairs", "garbage"],
    )
    def test_report_only_no_verdict(self, text):
        # 无已知规则：只报告文件存在性，恒不产生 ✗（不影响退出码）
        items = assess_cookie("example.com", text)
        assert len(items) == 1
        assert not _has_gap(items)
        joined = "".join(label + tip for label, _, tip in items)
        assert "无已知" in joined

    def test_never_leaks_cookie_values(self):
        items = assess_cookie("example.com", "SESSDATA=topsecret")
        assert all("topsecret" not in (label + tip) for label, _, tip in items)


class TestCli:
    @pytest.fixture()
    def cookies_dir(self, tmp_path, monkeypatch):
        d = tmp_path / "cookies"
        d.mkdir()
        monkeypatch.chdir(tmp_path)
        return d

    @staticmethod
    def _write(cookies_dir, site, content):
        (cookies_dir / f"{site}.txt").write_text(content, encoding="utf-8")

    def test_discovery_all_healthy_exit_zero(self, cookies_dir, capsys):
        self._write(cookies_dir, "douyin", _DOUYIN_NETSCAPE)
        self._write(cookies_dir, "xiaohongshu", f"a1={_A1}; webId=wid")
        self._write(cookies_dir, "example.com", "foo=bar")
        rc = main([])
        out = capsys.readouterr().out
        assert rc == 0
        assert "✗" not in out
        assert "✓" in out
        for site in ("douyin", "xiaohongshu", "example.com"):
            assert site in out

    def test_gap_exits_one_with_fixin(self, cookies_dir, capsys):
        self._write(cookies_dir, "douyin", "foo=bar")
        rc = main(["douyin"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "✗" in out
        assert "UIFID" in out  # 有具体修复指引

    def test_empty_file_is_gap_in_discovery_mode(self, cookies_dir, capsys):
        self._write(cookies_dir, "douyin", "")
        self._write(cookies_dir, "example.com", "foo=bar")
        rc = main([])
        out = capsys.readouterr().out
        assert rc == 1
        assert "✗" in out
        # 无规则站点不受牵连：其报告段落里没有 ✗
        assert "✗" not in out.split("example.com")[1]

    def test_explicit_site_missing_file_exits_one(self, cookies_dir, capsys):
        rc = main(["douyin"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "✗" in out
        assert "douyin.txt" in out

    def test_explicit_unknown_site_missing_file_exit_zero(self, cookies_dir, capsys):
        rc = main(["example.com"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "✗" not in out

    def test_unknown_site_uses_neutral_marker(self, cookies_dir, capsys):
        self._write(cookies_dir, "example.com", "")
        rc = main(["example.com"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "·" in out
        assert "无已知" in out

    def test_no_cookie_files_exit_zero(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)  # 连 cookies/ 目录都没有
        rc = main([])
        out = capsys.readouterr().out
        assert rc == 0
        assert "没有" in out

    def test_site_args_dedupe_and_suffix(self, cookies_dir, capsys):
        self._write(cookies_dir, "douyin", _DOUYIN_NETSCAPE)
        rc = main(["douyin.txt", "douyin"])
        out = capsys.readouterr().out
        assert rc == 0
        assert out.count("douyin ——") == 1

    def test_site_arg_case_insensitive(self, cookies_dir, capsys):
        self._write(cookies_dir, "douyin", _DOUYIN_NETSCAPE)
        rc = main(["DOUYIN"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "✓" in out

    def test_output_never_leaks_cookie_values(self, cookies_dir, capsys):
        self._write(cookies_dir, "douyin", "UIFID=topsecret; ttwid=ttwid-1")
        main(["douyin"])
        out = capsys.readouterr().out
        assert "topsecret" not in out
