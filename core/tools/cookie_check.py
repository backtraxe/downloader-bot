# -*- coding: utf-8 -*-
"""Cookie 健康自检（纯离线：不联网、不打印任何 Cookie 值）。

用法：
    python -m core.tools.cookie_check                     # 检查 cookies/ 下所有已存在的 *.txt
    python -m core.tools.cookie_check douyin xiaohongshu  # 只检查指定站点

检查规则口径来自本仓库 AGENTS.md 与 2026-10 生态实测：
- douyin：签名 detail API（aweme/v1/web/aweme/detail/）门禁——
  文件存在且非空；UIFID（或 UIFID_TEMP）非空（x-secsdk-web-signature
  的签名素材，任意非空值即可，服务端不校验内容）；ttwid 或 sessionid
  至少其一非空（2026-10-14 对照矩阵实测的真正门禁：两者的现值都会被
  服务端校验，过期即返回 200 空响应；ttwid 访客态短期就过期，是
  "旧 Cookie 突然全部 200 空"的常见原因）。
  msToken 存在时仅提示"已存在，将被优先使用"——信息项，不参与及格
  判定（缺失时签名栈以伪 126 串兜底，detail 端点不校验）。
- xiaohongshu：文件存在且非空；a1 非空且长度 >=30（生态口径正常约
  52 位；缺失或过短会被 302 到 /login、误判为 Cookie 失效）；
  webId 或 web_session 至少其一非空（访客标记 / 登录态）。
- 其他站点：无已知必需键规则，只报告文件存在性，不做及格判定
  （不产生 ✗，不影响退出码）。

输入文件格式（cookies/<站点>.txt）三种皆可，由 parse_cookie_pairs 统一
归一：Netscape（7 列 tab）/ 浏览器复制的 `Cookie:` 请求头整段 /
Cookie-Editor 导出的 JSON 数组。

退出码：任一明确缺口（✗）→ 1；否则 0。
"""

import argparse
import json
import os
import sys

from ..sites import COOKIE_DIR, cookie_file_for
from ..utils import cookie_text_to_header

# 小红书 a1 的足量长度下限：生态口径正常约 52 位，>=30 即视为足够
_XHS_A1_MIN_LEN = 30

_FILE_LABEL = "Cookie 文件存在且非空"

_DOUYIN_IMPORT_TIP = (
    "请在浏览器登录抖音后导出完整 Cookie 写入 "
    "cookies/douyin.txt（推荐 termux-clipboard-get | python -m core.tools.cookie_import douyin；"
    "桌面端可把扩展导出文件重定向进来）。"
)
_XHS_IMPORT_TIP = (
    "请在浏览器登录小红书后，从开发者工具复制 `Cookie:` 请求头整段写入 "
    "cookies/xiaohongshu.txt（推荐 python -m core.tools.cookie_import xiaohongshu）。"
)
_UIFID_FIX_TIP = (
    "它是 x-secsdk-web-signature 的签名素材（任意非空值即可，服务端不校验内容）；"
    "缺失会被 Argus 风控拦为 403 “Blocked by ArgusSecurityPlugin Uifid Not Found”。"
    "请重新导出完整 Cookie 补齐。"
)
_LOGIN_GATE_FIX_TIP = (
    "这是 2026-10-14 对照矩阵实测的真正门禁：UIFID 签名之外还需有效 ttwid 或 "
    "sessionid 至少其一（两者的现值都会被服务端校验，过期即返回 200 空响应；"
    "ttwid 访客态短期就过期，是“旧 Cookie 突然全部 200 空”的常见原因）。"
    "刷新 Cookie 是正路（纯 HTTP 无法自助续换）。"
)
_NO_RULE_TIP = "本站无已知必需键规则，仅报告文件存在性，不做及格判定。"


def parse_cookie_pairs(text: str | None) -> dict[str, str]:
    """把任意格式的 cookie 文本归一成 name → value 字典（离线、单输入字符串）。

    兼容三种格式：
    - `Cookie:` 请求头整段（行首标签可带）；同名首个出现生效（请求头语义）；
    - Netscape 文件（7 列 tab，# 注释行忽略——与 douyin_downloader 经
      cookie_text_to_header 的实际加载口径一致）；
    - Cookie-Editor 导出的 JSON 数组（含 {"cookies": [...]} 包裹；
      只按对象数组的 name/value 取，非 dict 项跳过）。
    完全无法识别的文本回退到“按分号/换行粗暴切 name=value”——换行切分由
    cookie_text_to_header 的逐行归一完成，这里是分号切分；解析不出返回
    空字典。只关心键名与值是否非空，不打印/不返回 Cookie 值以外的信息。
    """
    t = (text or "").strip()
    if not t:
        return {}
    if t[:1] == "[" or t[:1] == "{":
        pairs = _pairs_from_json(t)
        if pairs is not None:  # 合法 JSON：以 JSON 提取结果为准（即使为空）
            return pairs
    header = cookie_text_to_header(_strip_cookie_label(t))
    return _pairs_from_header(header)


def _pairs_from_json(text):
    """解析 Cookie-Editor 导出的 JSON，返回 name → value；非法 JSON 返回 None。

    顶层数组 / {"cookies": [...]} 包裹两种结构都认（做法参考
    cookie_import 的同场景处理，此处自写以容忍非 dict 项与空列表）。
    健康检查只关心键名存在性，第三方域 cookie 也一并提取（与
    cookie_import 写 xiaohongshu 文件时的按域过滤口径不同，无需过滤）。
    """
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if isinstance(obj, dict):
        obj = obj.get("cookies", [])
    if not isinstance(obj, list):
        obj = []
    pairs = {}
    for item in obj:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        if name and name not in pairs:
            pairs[name] = value
    return pairs


def _strip_cookie_label(text):
    """剥掉浏览器复制时可能带上的行首 `Cookie:` 标签，只留 k=v 整段。"""
    t = (text or "").strip()
    if t.lower().startswith("cookie:"):
        t = t[len("cookie:") :].strip()
    return t


def _pairs_from_header(header):
    """把已归一的请求头整段按分号切成 name → value（first occurrence 生效）。"""
    pairs = {}
    for piece in (header or "").split(";"):
        piece = piece.strip()
        if not piece or "=" not in piece:
            continue
        name, value = piece.split("=", 1)
        name = name.strip()
        if not name:
            continue
        value = value.strip()
        if name not in pairs:
            pairs[name] = value
    return pairs


def assess_cookie(site: str, text: str | None) -> list[tuple[str, bool, str]]:
    """按站点已知规则体检 cookies/<site>.txt 的文本内容。

    返回 [(检查项, 是否通过, 建议)]，text 为 None 表示文件缺失。
    已知规则站点（douyin / xiaohongshu）产出完整清单，False 即明确缺口；
    msToken 等信息项恒为 True，不参与及格判定。无已知规则的站点只报告
    文件存在性，恒为 True（不产生缺口）。
    建议只描述修复动作与原理，绝不包含 Cookie 值本身。
    """
    assess = _SITE_RULES.get(site, _assess_unknown_site)
    return assess(text)


def _file_item(text, import_tip):
    """已知规则站点共用的“文件存在且非空”检查项（缺失/为空均为明确缺口）。"""
    if text is None:
        return (_FILE_LABEL, False, f"cookies 下没有该文件。{import_tip}")
    if not text.strip():
        return (_FILE_LABEL, False, f"文件已创建但内容为空。{import_tip}")
    return (_FILE_LABEL, True, "")


def _assess_douyin(text):
    # 检查次序：文件 → UIFID → 登录态门禁 → msToken（信息项，有才出现）
    items = [_file_item(text, _DOUYIN_IMPORT_TIP)]
    pairs = parse_cookie_pairs(text or "")
    uifid_ok = bool(pairs.get("UIFID") or pairs.get("UIFID_TEMP"))
    items.append(
        ("UIFID（或 UIFID_TEMP）非空", uifid_ok, "" if uifid_ok else _UIFID_FIX_TIP)
    )
    hits = [n for n in ("ttwid", "sessionid") if pairs.get(n)]
    gate_ok = bool(hits)
    gate_tip = f"命中 {'、'.join(hits)}。" if gate_ok else _LOGIN_GATE_FIX_TIP
    items.append(("ttwid 或 sessionid 至少其一非空", gate_ok, gate_tip))
    if pairs.get("msToken"):
        items.append(
            ("msToken 已存在（信息项，不参与及格判定）", True, "已存在，将被优先使用。")
        )
    return items


def _assess_xiaohongshu(text):
    items = [_file_item(text, _XHS_IMPORT_TIP)]
    pairs = parse_cookie_pairs(text or "")
    a1_ok = len(pairs.get("a1", "")) >= _XHS_A1_MIN_LEN
    a1_tip = (
        ""
        if a1_ok
        else (
            "a1 是小红书的核心身份键，生态口径正常约 52 位（>=30 视为足量）；"
            "缺失或过短会进不了笔记页（被 302 到 /login，易误判为 Cookie 失效）。"
            "请重新复制完整请求头，或用 cookie_import 重写该文件。"
        )
    )
    items.append(("a1 非空且长度>=30", a1_ok, a1_tip))
    web_ok = bool(pairs.get("webId") or pairs.get("web_session"))
    web_tip = (
        ""
        if web_ok
        else (
            "webId（访客标记）与 web_session（登录态）至少需要其一，请补充或重新导出 Cookie。"
        )
    )
    items.append(("webId 或 web_session 至少其一非空", web_ok, web_tip))
    return items


def _assess_unknown_site(text):
    # 无已知规则：单向度报告文件存在性，恒 True（不给及格判定，不影响退出码）
    if text is None:
        state = "未创建"
    elif not text.strip():
        state = "已创建但内容为空"
    else:
        state = f"存在且非空（解析到 {len(parse_cookie_pairs(text))} 个键）"
    return [(f"Cookie 文件{state}", True, _NO_RULE_TIP)]


_SITE_RULES = {
    "douyin": _assess_douyin,
    "xiaohongshu": _assess_xiaohongshu,
}


def _discover_sites():
    """无参数模式下收集 cookies/ 下已存在的 *.txt 对应站点名（排序保证输出稳定）。"""
    if not os.path.isdir(COOKIE_DIR):
        return []
    return sorted(
        fn[: -len(".txt")] for fn in os.listdir(COOKIE_DIR) if fn.endswith(".txt")
    )


def _load_cookie_text(site):
    """读取 cookies/<site>.txt（CWD 相对，与其他模块口径一致）；文件缺失返回 None。"""
    path = cookie_file_for(site)
    if not os.path.exists(path):
        return path, None
    with open(path, encoding="utf-8") as f:
        return path, f.read()


def _print_site_report(site, path, items):
    print(f"{site} —— {path}")
    has_rule = site in _SITE_RULES
    for label, ok, tip in items:
        if has_rule:
            marker = "✓" if ok else "✗"
        else:
            marker = "·"  # 无规则站点：中性标记，不给及格判定
        line = f"  {marker} {label}"
        if tip:
            line += f" —— {tip}"
        print(line)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m core.tools.cookie_check",
        description=(
            "Cookie 健康自检（纯离线，不联网、不打印 Cookie 值）："
            "检查必需键/门禁项，输出 ✓/✗ 清单与修复建议；有明确缺口时退出码 1，否则 0"
        ),
    )
    parser.add_argument(
        "sites",
        nargs="*",
        metavar="站点",
        help="要检查的站点名（可传多个；缺省则检查 cookies/ 下所有已存在的 .txt）",
    )
    args = parser.parse_args(argv)

    sites = []
    for name in args.sites:
        # 容忍 douyin.txt 后缀与大小写不一致；重复参数只检查一次
        name = name.strip().removesuffix(".txt").strip().lower()
        if name and name not in sites:
            sites.append(name)
    if not sites:
        sites = _discover_sites()
        if not sites:
            print("cookies/ 下没有可检查的 .txt 文件（目录不存在或为空）。")
            return 0

    has_gap = False
    for site in sites:
        path, text = _load_cookie_text(site)
        items = assess_cookie(site, text)
        _print_site_report(site, path, items)
        print()
        if any(not ok for _, ok, _ in items):
            has_gap = True

    if has_gap:
        print("存在明确缺口（✗）：请按以上建议修复 Cookie 后重试。")
        return 1
    print("未发现明确缺口：已知必需键规则全部通过（信息项不影响及格判定）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
