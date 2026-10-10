# -*- coding: utf-8 -*-
"""抖音图文笔记（douyin.com/note/<id>）手写解析下载。

yt-dlp 的 DouyinIE 只支持 /video/ 类型，图文笔记需走手写解析：
带有效登录 Cookie 请求笔记页，从内嵌数据中提取 aweme detail——
新版桌面页在 `self.__pace_f.push` 流式数据块（React Flight，主路径，
见 parse_pace_f_detail），旧版在 RENDER_DATA JSON（递归定位，见
extract_aweme_detail）。字段名为 camelCase（awemeId/downloadUrlList/
authorInfo），兼容老 snake_case（aweme_id/download_url_list/author）。
未登录 / Cookie 失效时抖音返回 jsvm 反爬挑战页（空 body + _$jsvmprt 混淆脚本），
由 detect_jsvm_challenge 识别并提示重新填写 cookies/douyin.txt。

Cookie 文件两种格式皆可：Netscape（与 yt-dlp 共用 cookies/douyin.txt）
或浏览器复制的 `Cookie:` 请求头整段——经 utils.cookie_text_to_header 统一转换。
"""
import glob
import json
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import unquote

import http_client
from utils import (
    build_download_dir,
    extract_urls,
    guess_extension,
    init_useragent,
    is_html_content,
    sanitize_filename,
    setup_logging,
    unique_path,
)
from xhs_downloader import _is_transient_error, ensure_https

logger = setup_logging()

ua, _FALLBACK_UA = init_useragent(logger)

# 抖音图文笔记 URL：/note/<数字ID>
_NOTE_URL_RE = re.compile(r"https?://(?:www\.)?douyin\.com/note/(?P<id>\d+)")

# jsvm 反爬挑战页特征：未登录/失效 Cookie 时返回空 body + VM 混淆脚本
_JSVM_CHALLENGE_MARKER = "_$jsvmprt"


def is_douyin_note_url(url):
    """判断 URL 是否为抖音图文笔记页（/note/<数字ID>）。"""
    return bool(url) and _NOTE_URL_RE.search(url) is not None


def detect_jsvm_challenge(html):
    """检测 jsvm 反爬挑战页。

    抖音对无有效登录态的请求返回一个正文为空、仅含 _$jsvmprt 混淆
    脚本的挑战页——需要浏览器执行 JS 并算 __ac_signature 才能通过，
    纯 HTTP 客户端无解。命中即判为 Cookie 缺失/失效。
    """
    return bool(html) and _JSVM_CHALLENGE_MARKER in html


def get_headers(cookie):
    """生成请求头。UA 走 fake_useragent 随机，Cookie 由调用方提供。"""
    return {
        "User-Agent": ua.random if ua else _FALLBACK_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cookie": cookie,
        "Referer": "https://www.douyin.com/",
    }


def parse_render_data(html):
    """从笔记页 HTML 提取内嵌的页面数据 JSON。

    两种已知内嵌形态：
      1. <script id="RENDER_DATA" type="application/json">URL编码的JSON</script>
      2. window._ROUTER_DATA = {...} 的 JS 赋值
    解析失败返回 None，由调用方报"页面结构可能已更新"。
    """
    if not html:
        return None

    m = re.search(
        r'<script[^>]+id=["\']RENDER_DATA["\'][^>]*>(.*?)</script>', html, re.DOTALL)
    if m:
        try:
            return json.loads(unquote(m.group(1)))
        except (json.JSONDecodeError, TypeError):
            pass

    m = re.search(r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>", html, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def parse_pace_f_detail(html):
    """从 SSR 内嵌的 self.__pace_f.push 流式数据块中提取笔记详情（新版页面主路径）。

    抖音桌面页 2025 年后迁移到 React Flight 架构：数据不再在 RENDER_DATA，
    而是经 `self.__pace_f.push([1,"<chunk>"])` 分块推送——chunk 是 JSON 转义两层的
    JS 字符串，解开后是 Flight 行数据，其中含 `"aweme":{"statusCode":0,"detail":{...}}`
    包装段。chunk 各自独立解析，任一倍失败跳过尝试下一个。
    """
    if not html:
        return None
    pattern = re.compile(r'self\.__pace_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', re.DOTALL)
    for m in pattern.finditer(html):
        try:
            # 捕获组是 JS 字符串字面量的内容，包一层引号交给 JSON 解码恢复转义
            payload = json.loads('"' + m.group(1) + '"')
        except (json.JSONDecodeError, TypeError):
            continue
        idx = payload.find('"aweme":{')
        if idx < 0:
            continue
        try:
            obj, _ = json.JSONDecoder().raw_decode(payload, idx + len('"aweme":'))
        except json.JSONDecodeError:
            continue
        # obj 可能是包装层（带 detail 键）或直接就是详情
        if isinstance(obj, dict):
            detail = obj.get("detail")
            if isinstance(detail, dict):
                return detail
            if obj.get("aweme_id") or obj.get("awemeId"):
                return obj
    return None


def extract_note_detail(html):
    """总入口：优先 RENDER_DATA 递归定位（旧版/通用），退回 pace_f 块（新版）。"""
    data = parse_render_data(html)
    if data is not None:
        detail = extract_aweme_detail(data)
        if detail is not None:
            return detail
    return parse_pace_f_detail(html)


def extract_aweme_detail(data):
    """在页面数据里递归定位 aweme detail（含 awemeId/aweme_id 且有 images 或 video 的字典）。

    RENDER_DATA 的路径随页面版本变动（aweme.detail、loaderData.<route>.item_list 等），
    递归按"长得像 aweme detail"的特征找，比固定路径更抗压。
    新字段名 camelCase（awemeId），老字段名 snake_case（aweme_id），双 key 兼容。
    """
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if (node.get("aweme_id") or node.get("awemeId")) \
                    and ("images" in node or "video" in node):
                return node
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return None


def extract_douyin_image_url(img):
    """从单张图片对象取直链。download 版（原图直链）优先于 url 列表。
    新字段名 camelCase（downloadUrlList/urlList），老字段名 snake_case，双 key 兼容。"""
    img = img or {}
    for key in ("downloadUrlList", "download_url_list", "urlList", "url_list"):
        urls = img.get(key) or []
        for u in urls:
            if u and isinstance(u, str) and u.startswith(("http://", "https://", "//")):
                return u
    return None


def _first_play_url(video_subtree):
    """在视频字典（camelCase/snake_case 混合）中找首个可播放直链。

    兼容多种形态：snake `play_addr.url_list`、camel `playAddr`（可能是
    字符串列表或带 urlList 的字典）、`bitRate`/`bitRateList` 分档流。
    cover 封面键跳过——它是图片不是播放地址。
    """
    video_subtree = video_subtree or {}
    _PRIORITY_KEYS = (
        "play_addr", "download_addr", "playAddr", "playAddrH265",
        "play_addr_h265", "bitRate", "bitRateList", "playApi",
    )

    def _urls_in(node):
        if isinstance(node, str):
            if node.startswith(("http://", "https://", "//")):
                yield node
        elif isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str) and k.startswith("cover"):
                    continue
                yield from _urls_in(v)
        elif isinstance(node, list):
            for v in node:
                yield from _urls_in(v)

    for key in _PRIORITY_KEYS:
        sub = video_subtree.get(key)
        if sub:
            for u in _urls_in(sub):
                return u
    for u in _urls_in(video_subtree):
        return u
    return None


def extract_live_photo_url(img):
    """实况图：图片对象上挂 video 播放地址，取首个有效直链（无则 None）。"""
    video = (img or {}).get("video") or {}
    return _first_play_url(video)


def extract_note_video_url(video):
    """笔记级视频直链。返回 (url, reason)——url 为 None 时 reason 说明原因。"""
    url = _first_play_url(video or {})
    if url:
        return url, None
    return None, "video 子树中未找到可播放直链（play_addr/playAddr/bitRate 均空）"


def extract_douyin_author(detail):
    """从 aweme detail 取作者昵称。authorInfo（新版）或 author（旧版），
    nickname / uniqueId / unique_id 依次兜底。"""
    detail = detail or {}
    author = detail.get("authorInfo") or detail.get("author") or {}
    return author.get("nickname") or author.get("uniqueId") or author.get("unique_id") or ""


def resolve_douyin_basename(detail):
    """目录基名：desc 截前 30 字符，清洗后仍空则兜底 "douyin"。"""
    desc = ((detail or {}).get("desc") or "").strip()
    if desc:
        cleaned = sanitize_filename(desc[:30], default="")
        if cleaned:
            return cleaned
    return "douyin"


def _fetch_page(session, url, headers, max_retries=5):
    """请求笔记页并跟随重定向（curl_cffi 默认跟随），瞬时错误自动重试。
    全部失败返回 None。"""
    url = ensure_https(url)
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            response = session.get(
                url, headers=headers, timeout=15, impersonate="chrome110")
            response.raise_for_status()
            return response
        except Exception as e:
            last_err = e
            if attempt < max_retries and _is_transient_error(e):
                wait = attempt * 3 + random.uniform(0, 1)
                logger.warning("请求重试 %d/%d 将在 %.1fs 后重试: %s",
                               attempt, max_retries, wait, e)
                time.sleep(wait)
                continue
            break
    logger.error("请求失败: %s", last_err)
    return None


def download_file(session, url, filepath, headers, max_retries=5):
    """单文件下载，供多线程调用。文件名冲突原子去重，绝不覆盖。
    拦截到 HTML 响应（被 CDN 防盗链/风控）直接判失败。"""
    stem, _ = os.path.splitext(filepath)
    if glob.glob(stem + ".*"):
        return f"  [跳过] {os.path.basename(filepath)} 已存在，无需重复下载"
    url = ensure_https(url)
    last_err = None
    for attempt in range(1, max_retries + 1):
        safe_path = None
        try:
            time.sleep(random.uniform(0.1, 0.5))
            r = session.get(
                url, headers=headers, timeout=15, impersonate="chrome110", stream=True)
            r.raise_for_status()

            content_type = r.headers.get("Content-Type", "") or r.headers.get("content-type", "")
            if is_html_content(content_type):
                raise Exception(f"被拦截：响应为 HTML（{content_type}）")
            ext = guess_extension(url, content_type, default="")
            if ext:
                root, old_ext = os.path.splitext(filepath)
                if old_ext.lower() != ext.lower():
                    filepath = root + ext

            safe_path = unique_path(filepath)
            with open(safe_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            return f"  [成功] -> {os.path.basename(safe_path)}"
        except Exception as e:
            if safe_path and os.path.exists(safe_path):
                try:
                    os.remove(safe_path)
                except OSError:
                    pass
            last_err = e
            if attempt < max_retries and _is_transient_error(e):
                wait = attempt * 3 + random.uniform(0, 1)
                logger.warning(
                    "  [重试 %d/%d] %s 将在 %.1fs 后重试: %s",
                    attempt, max_retries, os.path.basename(filepath), wait, e,
                )
                time.sleep(wait)
                continue
            break
    return f"  [失败] {os.path.basename(filepath)} 下载报错: {last_err}"


def download_douyin_note(url, cookie):
    """下载抖音图文笔记的全部图片（及笔记级视频/实况图视频）。"""
    start_time = time.time()
    headers = get_headers(cookie)
    session = http_client.Session()

    logger.info("正在请求: %s", url)
    response = _fetch_page(session, url, headers)
    if response is None:
        logger.error("请求失败，耗时 %.1f 秒。", time.time() - start_time)
        return

    html = response.text

    # jsvm 挑战页：无有效登录态，无法解析
    if detect_jsvm_challenge(html):
        logger.error(
            "提取失败：抖音返回了反爬挑战页，说明 Cookie 缺失或已失效。"
            "请在 cookies/douyin.txt 中填入有效的登录态 Cookie 后重试。（耗时 %.1f 秒）",
            time.time() - start_time,
        )
        return

    detail = extract_note_detail(html)
    if detail is None:
        logger.error(
            "未在页面数据中定位到笔记详情（RENDER_DATA/pace_f 均无 aweme detail）。"
            "可能是页面结构已更新或 Cookie 失效。（耗时 %.1f 秒）",
            time.time() - start_time,
        )
        return

    # 笔记 ID 优先取自 URL（可溯源），缺失时用 detail 的 awemeId/aweme_id
    m = _NOTE_URL_RE.search(url)
    aweme_id = (m.group("id") if m else None) \
        or str(detail.get("aweme_id") or detail.get("awemeId") or "")
    safe_title = f"{resolve_douyin_basename(detail)}_{aweme_id}" if aweme_id \
        else resolve_douyin_basename(detail)

    author = extract_douyin_author(detail)
    base_path = build_download_dir("douyin", safe_title, author=author)
    os.makedirs(base_path, exist_ok=True)
    logger.info("目标文件夹: %s", base_path)

    download_tasks = []

    # 1. 图文列表：001.jpg 序号命名；实况图视频同序号配对 001.mp4
    images = detail.get("images") or []
    if images:
        logger.info("发现 %d 张图片，开启多线程下载...", len(images))
        for i, img in enumerate(images):
            img_url = extract_douyin_image_url(img)
            if img_url:
                filepath = os.path.join(base_path, f"{i + 1:03d}.jpg")
                download_tasks.append((ensure_https(img_url), filepath))
            live_url = extract_live_photo_url(img)
            if live_url:
                filepath = os.path.join(base_path, f"{i + 1:03d}.mp4")
                download_tasks.append((ensure_https(live_url), filepath))

    # 2. 笔记级视频（图文带视频 / 视频型笔记）
    video = detail.get("video")
    if video:
        video_url, reason = extract_note_video_url(video)
        if video_url:
            logger.info("发现视频，加入下载队列...")
            download_tasks.append((ensure_https(video_url), os.path.join(base_path, "video.mp4")))
        else:
            logger.warning("发现 video 字段但未能提取到直链：%s", reason)

    # 3. 并发下载（维持 max_workers=5，避免触发 IP 熔断）
    if download_tasks:
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [
                executor.submit(download_file, session, task[0], task[1], headers)
                for task in download_tasks
            ]
            succeeded = failed = 0
            for future in as_completed(futures):
                result = future.result()
                if result.startswith("  [失败]"):
                    failed += 1
                    logger.error("%s", result)
                else:
                    succeeded += 1
                    logger.info("%s", result)
            logger.info(
                "下载完成：%d 成功 / %d 失败 / 共 %d 项，耗时 %.1f 秒",
                succeeded, failed, len(download_tasks), time.time() - start_time,
            )
    else:
        logger.info("未发现可下载的媒体文件，耗时 %.1f 秒。", time.time() - start_time)

    session.close()
    logger.info("📁 保存目录: %s", os.path.abspath(base_path))
    logger.info("该链接处理完成，耗时 %.1f 秒。", time.time() - start_time)


if __name__ == "__main__":
    from sites import load_cookie_for_url
    from utils import cookie_text_to_header

    logger.info("抖音图文笔记下载 (输入 q 退出)")
    while True:
        try:
            raw_input = input("🔗 请输入笔记链接: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 退出程序")
            break
        if raw_input.lower() == "q":
            print("👋 退出程序")
            break
        if not raw_input:
            continue
        for target_url in extract_urls(raw_input):
            if not is_douyin_note_url(target_url):
                logger.warning("仅支持 douyin.com/note/<id> 图文笔记链接：%s", target_url)
                continue
            cookie = load_cookie_for_url(target_url)
            if cookie:
                download_douyin_note(target_url, cookie_text_to_header(cookie))
