# -*- coding: utf-8 -*-
"""手机号 + 短信验证码半自动登录：一键刷新 cookies/<站点>.txt。

Playwright 驱动 Chromium：自动打开登录页、填手机号、发验证码；用户在
终端输入短信验证码后自动提交。登录成功（关键 cookie 出现）即按各站点
所需格式落盘——douyin / bilibili 写 Netscape（yt-dlp 侧可读），
xiaohongshu 写 `Cookie:` 请求头整段（手写解析器塞请求头用）。

短信登录的高频拦截是滑块/人机校验（无头环境尤甚）。默认无头全自动；
自动步骤任一失败请加 --headed 重跑：在弹出的浏览器里手动完成登录
（扫码亦可），脚本只在后台等关键 cookie 出现后收割落盘。

依赖（可选，不装不影响其他脚本）：
    pip install playwright && playwright install chromium

用法：
    python cookie_login.py douyin                 # 无头 + 终端输验证码
    python cookie_login.py xiaohongshu --headed   # 弹窗，手动登录兜底
    python cookie_login.py bilibili --browser-path /path/to/chromium
"""
import argparse
import logging
import sys
import time

from sites import cookie_file_for
from utils import (cookie_list_to_header, cookie_list_to_netscape,
                   setup_logging)

logger = logging.getLogger("downloader")

# 与下载器侧一致的 Chrome150 UA：登录拿到的 cookie 随后由 chrome150 指纹
# 的请求使用，UA 漂移太大会增加风控关联概率。
CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")

# 反自动化基础处理：抹掉 webdriver 标记，降低登录页被风控命中概率
_STEALTH_JS = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"


class LoginAutomationError(RuntimeError):
    """自动填表步骤失败（选择器没命中/被风控拦截等），由上层决定退回手动。"""


# ---------------- 站点流程 ----------------
# 各站点登录 DOM 差异大且频繁改版，流程各写一个函数；候选选择器按
# 命中概率排列，_first_visible 逐个尝试，全部失配抛 LoginAutomationError。
# 每个站：home（入口页）、success_cookies（登录成功的关键 cookie 名）、
# save_cookie()（收割后按本站格式写 cookies/<站点>.txt）。

def _first_visible(scope, selectors, timeout_ms=6000):
    """在候选选择器中找第一个可见元素，返回 locator；全失配返回 None。"""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for sel in selectors:
            try:
                loc = scope.locator(sel).first
                if loc.count() and loc.is_visible():
                    return loc
            except Exception:
                continue
        time.sleep(0.3)
    return None


def _must(scope, selectors, what, timeout_ms=6000):
    loc = _first_visible(scope, selectors, timeout_ms)
    if loc is None:
        raise LoginAutomationError(f"未找到「{what}」，页面结构可能已变更")
    return loc


def _douyin_flow(page, phone, code, send_code):
    """抖音：首页点「登录」→ 弹窗切「验证码登录」→ 手机号 + 验证码。"""
    page.goto("https://www.douyin.com/", wait_until="domcontentloaded")
    _must(page, ["text=登录", "button:has-text('登录')"],
          "登录入口", 10000).click()
    _must(page, ["text=验证码登录", "div:has-text('验证码登录')"],
          "验证码登录标签").click()
    _must(page, ["input[placeholder*='手机号']", "input[type='tel']"],
          "手机号输入框").fill(phone)
    # 协议勾选框（不勾无法登录）；未找到不视为失败（不同版本文案不一）
    agreed = _first_visible(page, ["input[type='checkbox']"], 2000)
    if agreed is not None:
        try:
            agreed.check(force=True)
        except Exception:
            pass
    send_code()  # 终端提示"即将发送，回车确认"
    _must(page, ["text=获取验证码", "button:has-text('获取验证码')",
                 "span:has-text('获取验证码')"],
          "获取验证码按钮").click()
    # 验证码在终端输入（上层 input 之后回调本函数续跑）
    return _douyin_submit


def _douyin_submit(page, code):
    _must(page, ["input[placeholder*='验证码']"], "验证码输入框").fill(code)
    _must(page, ["button:has-text('登录')", "div[role='button']:has-text('登录')"],
          "登录提交按钮").click()


def _xhs_flow(page, phone, code, send_code):
    """小红书：explore 页自动弹登录框 → 切「手机号登录」→ 手机号 + 验证码。"""
    page.goto("https://www.xiaohongshu.com/explore",
              wait_until="domcontentloaded")
    tab = _first_visible(page, ["text=手机号登录", "div:has-text('手机号登录')"],
                         10000)
    if tab is not None:
        tab.click()  # 部分版本默认就是手机号表单
    _must(page, ["input[placeholder*='手机号']", "input[type='tel']"],
          "手机号输入框").fill(phone)
    send_code()
    _must(page, ["text=发送验证码", "button:has-text('发送验证码')",
                 "span:has-text('发送验证码')"],
          "发送验证码按钮").click()
    return _xhs_submit


def _xhs_submit(page, code):
    _must(page, ["input[placeholder*='验证码']"], "验证码输入框").fill(code)
    _must(page, ["button:has-text('登录')", "button[type='submit']"],
          "登录提交按钮").click()


def _bilibili_flow(page, phone, code, send_code):
    """bilibili：passport 登录页切「短信登录」→ 手机号 + 验证码。"""
    page.goto("https://passport.bilibili.com/login",
              wait_until="domcontentloaded")
    _must(page, ["text=短信登录", "div:has-text('短信登录')"],
          "短信登录标签", 10000).click()
    _must(page, ["input[placeholder*='手机号']", "input[type='tel']"],
          "手机号输入框").fill(phone)
    send_code()
    _must(page, ["text=获取验证码", "button:has-text('获取验证码')",
                 "span:has-text('获取验证码')"],
          "获取验证码按钮").click()
    return _bilibili_submit


def _bilibili_submit(page, code):
    _must(page, ["input[placeholder*='验证码']"], "验证码输入框").fill(code)
    _must(page, ["text=登录", "button:has-text('登录')"],
          "登录提交按钮").click()


_SITES = {
    "douyin": {
        "flow": _douyin_flow,
        "success_cookies": ("sessionid", "sessionid_ss"),
        "format": "netscape",
        "domain_filter": None,  # Netscape 文件保留全量域（含签名素材 s_v_web_id 等）
    },
    "xiaohongshu": {
        "flow": _xhs_flow,
        "success_cookies": ("web_session",),
        "format": "header",
        "domain_filter": "xiaohongshu.com",
    },
    "bilibili": {
        "flow": _bilibili_flow,
        "success_cookies": ("SESSDATA",),
        "format": "netscape",
        "domain_filter": None,
    },
}


# ---------------- Cookie 收割与落盘 ----------------

def _harvest(context, site, timeout_s):
    """轮询浏览器上下文，关键 cookie 出现即视为登录成功。返回 cookie 列表。"""
    markers = _SITES[site]["success_cookies"]
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            cookies = context.cookies()
        except Exception:
            cookies = []
        names = {c.get("name") for c in cookies}
        if any(m in names for m in markers):
            logger.info("检测到登录成功（cookie %s 已出现）", " / ".join(markers))
            return cookies
        time.sleep(1.5)
    return None


def _save_cookie(cookies, site):
    cfg = _SITES[site]
    path = cookie_file_for(site)
    if cfg["format"] == "netscape":
        cookie_list_to_netscape(cookies, path)
    else:
        header = cookie_list_to_header(cookies, cfg["domain_filter"])
        with open(path, "w", encoding="utf-8") as f:
            f.write(header + "\n")
    logger.info("Cookie 已写入 %s（%d 条，格式：%s）",
                path, len(cookies), cfg["format"])
    if site == "douyin":
        names = {c.get("name") for c in cookies}
        if not ({"UIFID", "UIFID_TEMP", "s_v_web_id"} & names):
            logger.warning("未捕获 UIFID / s_v_web_id：抖音视频签名可能失败，"
                           "建议登录成功后在浏览器里多刷新几个页面再收割")


# ---------------- 主流程 ----------------

def main():
    parser = argparse.ArgumentParser(
        description="半自动登录刷新 cookies/<站点>.txt（手机号 + 短信验证码）")
    parser.add_argument("site", choices=sorted(_SITES),
                        help="要登录的站点")
    parser.add_argument("--headed", action="store_true",
                        help="弹出浏览器窗口；自动步骤失败时可手动登录兜底")
    parser.add_argument("--browser-path", default=None,
                        help="自定义浏览器可执行文件路径（Termux 装 tur 源 "
                             "chromium 后可用；默认用 playwright 自带 chromium）")
    parser.add_argument("--timeout", type=int, default=300,
                        help="等待登录成功的秒数（默认 300）")
    args = parser.parse_args()

    setup_logging()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("缺少依赖：pip install playwright && playwright install chromium",
              file=sys.stderr)
        return 1

    print(f"站点：{args.site}（{'弹窗模式' if args.headed else '无头模式'}）")
    phone = input("手机号：").strip()
    if not phone:
        print("手机号不能为空", file=sys.stderr)
        return 1

    flow = _SITES[args.site]["flow"]
    with sync_playwright() as p:
        launch_kwargs = {"headless": not args.headed,
                         "args": ["--disable-blink-features=AutomationControlled"]}
        if args.browser_path:
            launch_kwargs["executable_path"] = args.browser_path
        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            user_agent=CHROME_UA, locale="zh-CN",
            viewport={"width": 1280, "height": 800})
        context.add_init_script(_STEALTH_JS)
        page = context.new_page()

        auto_failed = None
        try:
            # flow 内部在发验证码前回调 send_code() 让终端提示确认
            submit = flow(
                page, phone, None,
                send_code=lambda: input("即将发送短信验证码，按回车继续……"))
            code = input("短信验证码：").strip()
            submit(page, code)
        except LoginAutomationError as e:
            auto_failed = e
        except KeyboardInterrupt:
            print("\n已取消")
            browser.close()
            return 1
        except Exception as e:  # playwright TimeoutError 等
            auto_failed = e

        if auto_failed is not None:
            if not args.headed:
                print(f"自动登录步骤失败：{auto_failed}", file=sys.stderr)
                print("可能遇到滑块/人机校验或页面改版。请加 --headed 重跑，"
                      "在弹出的浏览器里手动完成登录（扫码亦可），脚本会"
                      "自动收割 Cookie。", file=sys.stderr)
                browser.close()
                return 1
            print(f"自动步骤失败（{auto_failed}）。")
            print("请在浏览器里手动完成登录（扫码或短信均可），"
                  "成功后脚本会自动收割 Cookie。")

        cookies = _harvest(context, args.site, args.timeout)
        if cookies is None:
            print("等待登录成功超时：未检测到关键 Cookie。", file=sys.stderr)
            browser.close()
            return 1
        _save_cookie(cookies, args.site)
        browser.close()
    print("完成。可直接开始下载；cookies/ 已在 .gitignore 中，请勿提交。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
