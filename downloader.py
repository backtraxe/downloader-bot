# -*- coding: utf-8 -*-
"""统一媒体下载入口。

按 URL 自动识别站点并分发到对应下载器：
  - xiaohongshu → xhs_downloader（手写 __INITIAL_STATE__ 解析）
  - bilibili / douyin / youtube / instagram / twitter → yt-dlp
  - 其他站点 → 1024_downloader（通用静态网页抓取）

用法：
    python downloader.py
然后反复输入链接下载，输入 q 退出。
"""
import os
import random
import sys
import time

import yt_dlp
from yt_dlp.utils import DownloadError

from sites import (
    SITE_REQUIRE_COOKIE,
    cookie_file_for,
    get_site_name,
)
from utils import extract_urls, sanitize_filename, setup_logging

logger = setup_logging()

DOWNLOAD_DIR = "download"

# 需要登录 / 常见失败的错误关键字 → 可读提示
_LOGIN_HINTS = (
    "Login required",
    "login",
    "cookies",
    "Sign in",
    "HTTP Error 401",
    "Private video",
    "Age-restricted",
    "geo",
    "not available in your country",
    "blocked",
)


def resolve_uploader(info):
    """从 yt-dlp info dict 取作者名并清洗。返回可能为空的字符串。

    uploader 是 yt-dlp 统一字段，所有已支持站点都会填；缺失/为空时返回 ""，
    由调用方据此决定是否加作者目录层。uploader 来自网页、不可信，
    经 sanitize_filename 清洗防目录穿越。
    """
    raw = (info or {}).get("uploader") or ""
    # 空串直接返回 ""（sanitize_filename 对空串会兜底成 "untitled"，
    # 那会让作者缺失时误加一层 untitled/ 目录，违背"缺失则退化"语义）
    if not raw.strip():
        return ""
    return sanitize_filename(raw)


def build_ydl_opts(site_name, author=""):
    """构造 yt-dlp 下载选项。

    cookiefile 指向 cookies/<site>.txt；文件为空时 yt-dlp 会忽略，
    由调用方在下载前提示用户是否需要填写 Cookie。
    author 非空时输出路径多一层作者目录：download/<site>/<author>/<标题>.<ext>；
    为空则退化为 download/<site>/<标题>.<ext>（不产生 NA/ 或 unknown/ 占位）。
    """
    cookie_path = cookie_file_for(site_name)
    # 空文件 / 不存在都不传 cookiefile，避免 yt-dlp 警告
    cookiefile = None
    if os.path.exists(cookie_path) and os.path.getsize(cookie_path) > 0:
        cookiefile = cookie_path

    # 输出路径：有作者加一层；作者名已由 resolve_uploader 清洗
    outtmpl_parts = [DOWNLOAD_DIR, site_name]
    if author:
        outtmpl_parts.append(author)
    outtmpl_parts.append("%(title).200B.%(ext)s")

    return {
        # 输出路径：download/<site>[/<author>]/<标题>.<ext>
        "outtmpl": os.path.join(*outtmpl_parts),
        # 视频音频合并为 mp4
        "merge_output_format": "mp4",
        # 分片并发
        "concurrent_fragment_downloads": 3,
        # 不让 yt-dlp 自己打印，统一走我们的 logger
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        # cookie
        "cookiefile": cookiefile,
        # 文件名冲突不覆盖：yt-dlp 自带带序号后缀
        "nopart": False,
        # 瞬时网络错误（SSL EOF、连接重置、超时）多撑几轮再放弃，
        # 单次分片失败不再直接让整个下载挂掉
        "retries": 10,
        "fragment_retries": 10,
        "progress_hooks": [_on_progress],
    }


def _on_progress(d):
    """yt-dlp 进度回调，转成日志。"""
    status = d.get("status")
    if status == "downloading":
        # 已下载 / 总量，避免刷屏，只在显著进度时记一条 debug
        logger.debug("下载中: %s", d.get("filename", ""))
    elif status == "finished":
        logger.info("✅ 完成: %s", os.path.basename(d.get("filename", "")))
    elif status == "error":
        logger.error("❌ 出错: %s", d.get("filename", ""))


# 瞬时网络错误关键字：SSL 握手中断、连接重置、超时等——值得整链重试
_TRANSIENT_KEYWORDS = (
    "unexpected_eof_while_reading",   # SSL 握手中断
    "eof occurred in violation",       # SSL 协议异常
    "ssl: wrong_version_number",       # SSL 版本不匹配
    "connection reset",               # 连接被重置
    "connection aborted",             # 连接中断
    "connection broken",              # 连接断开
    "read timed out",                 # 读取超时
    "connect timeout",                # 连接超时
    "timeout",                        # 通用超时
    "max retries exceeded",           # 连接池耗尽
    "temporary failure",              # DNS 临时失败
    "no address associated",          # DNS 解析失败
    "name or service not known",      # DNS 解析失败 (Linux)
    "nodename nor servname",          # DNS 解析失败 (macOS)
    "503",                            # 服务暂时不可用
    "502",                            # 网关错误
)


def diagnose_error(err_msg):
    """把 yt-dlp 的错误信息翻译成可读提示。返回 (kind, message)。

    kind 取值：
      need_login  —— 登录/Cookie 相关，重试无益
      transient  —— 瞬时网络错误，值得整链重试
      unknown     —— 其他，原样回显
    """
    low = (err_msg or "").lower()
    if any(k.lower() in low for k in _LOGIN_HINTS):
        return "need_login", "该内容可能需要登录或 Cookie 失效，请检查 cookies/<站点>.txt"
    if any(kw in low for kw in _TRANSIENT_KEYWORDS):
        return "transient", err_msg
    return "unknown", err_msg



# yt-dlp 接管的站点集合；其余已知站点走专用解析器或通用抓取
_YTDLP_SITES = frozenset(SITE_REQUIRE_COOKIE.keys()) - {"xiaohongshu"}


def dispatch_url(url):
    """统一入口：按站点自动分发到对应下载器。

    xiaohongshu → xhs_downloader（手写解析，需 Cookie）
    yt-dlp 站点  → download_url（bilibili / douyin / youtube / instagram / twitter）
    其他站点     → 1024_downloader（通用静态网页抓取）
    """
    site_name = get_site_name(url)

    if site_name == "xiaohongshu":
        # 延迟导入避免循环依赖与不必要的模块初始化
        from sites import load_cookie_for_url
        from xhs_downloader import download_xhs_media

        cookie = load_cookie_for_url(url)
        if cookie:
            download_xhs_media(url, cookie)
        return

    if site_name in _YTDLP_SITES:
        download_url(url)
        return

    # 未知站点：交给通用静态网页抓取器
    from importlib import import_module
    mod = import_module("1024_downloader")
    mod.extract_general_media(url)


def download_url(url):
    """下载单个 URL。自动识别站点并应用对应 cookie。"""
    site_name = get_site_name(url)
    if site_name not in SITE_REQUIRE_COOKIE:
        logger.warning("未识别的站点 %s，仍尝试用 yt-dlp 解析（cookie 按 %s 命名）。",
                       site_name, cookie_file_for(site_name))
    else:
        logger.info("识别站点: %s（Cookie: %s）", site_name, SITE_REQUIRE_COOKIE[site_name])

    # 提示 cookie 状态
    cookie_path = cookie_file_for(site_name)
    if not os.path.exists(cookie_path) or os.path.getsize(cookie_path) == 0:
        if SITE_REQUIRE_COOKIE.get(site_name) == "强烈建议":
            logger.warning("⚠️ %s 强烈建议填写 Cookie（cookies/%s.txt），否则大概率失败。",
                           site_name, site_name)

    logger.info("开始下载: %s", url)
    start_time = time.time()

    max_attempts = 3  # 瞬时网络错误（SSL EOF、连接重置等）整链最多重试 3 次
    for attempt in range(1, max_attempts + 1):
        try:
            # 两段式：先探一次拿作者（uploader），再按作者层构造 outtmpl 下载。
            # 代价是多一次请求；收益是作者缺失时自然退化为无作者层（无 NA/ 占位）。
            probe_opts = {k: v for k, v in build_ydl_opts(site_name).items()
                          if k in ("cookiefile", "quiet", "noprogress", "no_warnings")}
            author = ""
            with yt_dlp.YoutubeDL(probe_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                author = resolve_uploader(info)

            opts = build_ydl_opts(site_name, author=author)
            with yt_dlp.YoutubeDL(opts) as ydl:
                # 再 extract 并下载；失败给出明确原因
                info = ydl.extract_info(url, download=True)
                title = sanitize_filename(info.get("title", "untitled")) if info else "untitled"
                # 从最终文件路径取目录，向用户展示本地保存位置
                final_file = ydl.prepare_filename(info) if info else ""
                save_dir = os.path.dirname(final_file) or os.path.join(DOWNLOAD_DIR, site_name)
                elapsed = time.time() - start_time
                logger.info("🎉 下载完成: %s → %s（耗时 %.1f 秒）", title, os.path.abspath(save_dir), elapsed)
                return True
        except DownloadError as e:
            elapsed = time.time() - start_time
            kind, msg = diagnose_error(str(e))
            # 瞬时网络错误：退避后整链重试，不立刻判定失败
            if kind == "transient" and attempt < max_attempts:
                wait = attempt * 3 + random.uniform(0, 2)
                logger.warning(
                    "⚠️ 网络瞬时错误（第 %d/%d 次），%.1fs 后重试: %s",
                    attempt, max_attempts, wait, msg,
                )
                time.sleep(wait)
                continue
            if kind == "need_login":
                logger.error("❌ 下载失败（需登录）: %s（耗时 %.1f 秒）", msg, elapsed)
            else:
                logger.error("❌ 下载失败: %s（耗时 %.1f 秒）", msg, elapsed)
            return False
        except Exception as e:  # noqa: BLE001 顶层兜底，避免 prompt 循环中断
            elapsed = time.time() - start_time
            kind, msg = diagnose_error(str(e))
            if kind == "transient" and attempt < max_attempts:
                wait = attempt * 3 + random.uniform(0, 2)
                logger.warning(
                    "⚠️ 网络瞬时错误（第 %d/%d 次），%.1fs 后重试: %s",
                    attempt, max_attempts, wait, msg,
                )
                time.sleep(wait)
                continue
            logger.error("❌ 意外错误: %s（耗时 %.1f 秒）", e, elapsed)
            return False
    # 瞬时错误重试用尽
    elapsed = time.time() - start_time
    logger.error("❌ 下载失败: 多次重试仍遇瞬时网络错误（耗时 %.1f 秒）", elapsed)
    return False


def main():
    logger.info("🚀 统一下载器支持: 小红书 / bilibili / douyin / youtube / instagram / x / 任意网页")
    logger.info("输入 q 退出")
    while True:
        try:
            raw_input = input("\n🔗 请输入链接: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 退出程序")
            break

        if raw_input.lower() == "q":
            logger.info("退出程序")
            break
        if not raw_input:
            continue

        urls = extract_urls(raw_input)
        if not urls:
            logger.warning("未识别到有效链接，请输入包含 http(s):// 的分享文本或 URL。")
            continue

        for target_url in urls:
            dispatch_url(target_url)


if __name__ == "__main__":
    main()
