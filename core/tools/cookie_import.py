# -*- coding: utf-8 -*-
"""把任意来源的 Cookie 文本标准化写入 cookies/<站点>.txt。

配合"手机上快速恢复 Cookie"流程：浏览器扩展（Cookie-Editor 等）能导出
哪种格式就用哪种，stdin 喂给本脚本，自动识别并输出各站点需要的形态：

  输入：Cookie-Editor 的 JSON 数组 / `Cookie:` 请求头整段 / Netscape 文件
  输出：douyin、bilibili → Netscape（yt-dlp 可读）；
        xiaohongshu → `Cookie:` 请求头整段（手写解析器塞请求头用）

Termux 典型用法（配合 Termux:API，扩展导出复制到剪贴板后一条命令）：
    termux-clipboard-get | python -m core.tools.cookie_import douyin
桌面端也可把扩展导出的文件直接喂进来：
    python -m core.tools.cookie_import bilibili < ~/Downloads/cookies.txt
"""

import argparse
import json
import sys

from ..sites import cookie_file_for
from ..utils import (
    cookie_header_to_netscape,
    cookie_list_to_header,
    cookie_list_to_netscape,
    cookie_text_to_header,
)

# 各站点输出格式与兜底域名（header → netscape 转换需要指定 cookie 域；
# header 转 xhs 请求头无需过滤，文件本就只装本站 cookie）
_SITES = {
    "douyin": {"format": "netscape", "domain": ".douyin.com"},
    "bilibili": {"format": "netscape", "domain": ".bilibili.com"},
    "xiaohongshu": {"format": "header", "domain": "xiaohongshu.com"},
}


def detect_format(text):
    """识别 cookie 文本格式，返回 json / netscape / header / empty。"""
    t = (text or "").strip()
    if not t:
        return "empty"
    if t[:1] in "[{":
        try:
            obj = json.loads(t)
            if isinstance(obj, dict):
                obj = obj.get("cookies", [])
            if isinstance(obj, list):
                return "json"
        except ValueError:
            pass
    if any(len(line.split("\t")) >= 7 for line in t.splitlines()):
        return "netscape"  # 任一行出现 >=7 个 Tab 字段即 Netscape 行
    return "header"


def _parse_json_cookies(text):
    """解析 Cookie-Editor 类扩展导出的 JSON，返回 cookie 字典列表。
    兼容顶层数组与 {"cookies": [...]} 包裹两种结构。"""
    obj = json.loads(text)
    if isinstance(obj, dict):
        obj = obj.get("cookies", [])
    return obj if isinstance(obj, list) else []


def _strip_cookie_label(header):
    """浏览器复制时可能带上 "Cookie:" 标签行首，剥掉只留 k=v 整段。"""
    t = (header or "").strip()
    if t.lower().startswith("cookie:"):
        t = t[len("cookie:") :].strip()
    return t


def find_empty_cookie_values(text):
    """检测任意格式 cookie 文本中值为空的条目，返回 name 列表。

    导入时向用户预警用——空值 cookie 多为复制不完整或失效导出，登录态
    往往缺关键项（对齐 gallery-dl v1.32.16 "check for empty cookie
    values"）。三种输入格式（json / netscape / header）各按其结构判定。
    """
    t = (text or "").strip()
    if not t:
        return []
    empty = []
    fmt = detect_format(t)
    if fmt == "json":
        try:
            cookies = _parse_json_cookies(t)
        except ValueError:
            cookies = []
        for c in cookies:
            name = str(c.get("name") or "").strip() if isinstance(c, dict) else ""
            value = c.get("value") if isinstance(c, dict) else ""
            if name and (value is None or str(value).strip() == ""):
                empty.append(name)
    elif fmt == "netscape":
        for line in t.splitlines():
            parts = line.split("\t")
            # name 在第 6 列、value 在第 7 列；value 为空即空值条目。
            # 末行尾部 Tab 可能被上游的 .strip() 吃掉只剩 6 列——
            # "有 name 无 value 列"同样按空值计（Netscape 标准是 7 列）
            if len(parts) >= 6 and parts[5].strip() and (
                len(parts) < 7 or not parts[6].strip()
            ):
                empty.append(parts[5].strip())
    else:
        header = _strip_cookie_label(t)
        for seg in header.split(";"):
            if "=" not in seg:
                continue
            name, _, value = seg.partition("=")
            if name.strip() and not value.strip():
                empty.append(name.strip())
    return list(dict.fromkeys(empty))  # 去重保序


def convert_to_site_format(text, site):
    """把任意格式的 cookie 文本转换成本站点 cookies 文件应有的内容。

    返回字符串（含结尾换行）。site 必须在 _SITES 中；空输入抛 ValueError。
    """
    cfg = _SITES[site]
    fmt = detect_format(text)
    if fmt == "empty":
        raise ValueError("输入为空：请确认扩展导出已复制到剪贴板/重定向了文件")

    if fmt == "json":
        cookies = _parse_json_cookies(text)
        if cfg["format"] == "netscape":
            return cookie_list_to_netscape(cookies)
        # xhs 请求头只装本站域 cookie，第三方域（统计/广告）丢弃
        return cookie_list_to_header(cookies, cfg["domain"]) + "\n"

    if fmt == "netscape":
        t = (text or "").strip()
        if cfg["format"] == "netscape":
            return t + "\n"  # Netscape 进 Netscape 出，原样保留
        return cookie_text_to_header(t) + "\n"

    header = _strip_cookie_label(text)
    if cfg["format"] == "netscape":
        return cookie_header_to_netscape(header, cfg["domain"])
    return header + "\n"


def main():
    parser = argparse.ArgumentParser(
        prog="python -m core.tools.cookie_import",
        description="把 stdin 的 Cookie 文本（JSON / Cookie头 / Netscape "
        "自动识别）按站点所需格式写入 cookies/<站点>.txt",
    )
    parser.add_argument("site", choices=sorted(_SITES), help="目标站点")
    args = parser.parse_args()

    text = sys.stdin.read()
    try:
        content = convert_to_site_format(text, args.site)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1

    path = cookie_file_for(args.site)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(
        f"已写入 {path}（识别格式：{detect_format(text)}，"
        f"输出格式：{_SITES[args.site]['format']}）"
    )
    empty_names = find_empty_cookie_values(text)
    if empty_names:
        print(
            f"⚠️ 检测到 {len(empty_names)} 个空值 cookie：{'、'.join(empty_names)}——"
            "登录态可能不完整，建议在浏览器里重新完整复制后再导入",
            file=sys.stderr,
        )
    print("可直接开始下载；cookies/ 已在 .gitignore 中，请勿提交。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
