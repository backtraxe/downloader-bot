import requests
from requests.adapters import HTTPAdapter
import re
import json
import os
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed

from sites import get_site_name, load_cookie_for_url
from utils import (
    build_download_dir,
    extract_urls,
    guess_extension,
    init_useragent,
    normalize_xhs_state_json,
    sanitize_filename,
    setup_logging,
    unique_path,
)

logger = setup_logging()

# 初始化 fake_useragent，尽量生成 PC 端的 User-Agent
# 这样可以确保请求到的是小红书的网页端，从而顺利提取 __INITIAL_STATE__
ua, _FALLBACK_UA = init_useragent(logger)


def get_headers(cookie):
    """动态生成请求头，使用 fake_useragent 随机替换 UA；不可用时降级固定 UA。"""
    user_agent = ua.random if ua else _FALLBACK_UA

    return {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cookie": cookie,
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Referer": "https://www.xiaohongshu.com/",
    }


def ensure_https(url):
    """将协议相对(//)或 http:// 的媒体链接统一升级为 https://。
    小红书 CDN 图片直链常以 http:// 返回，部分 CDN 节点仅稳定支持 HTTPS，
    且 http:// 走 80 端口易因 DNS/网络问题失败。统一升级避免此问题。"""
    if not url:
        return url
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url[len("http://"):]
    return url


def _is_transient_error(err):
    """判断是否为值得重试的瞬时网络错误（DNS 解析失败、超时、连接重置等）。"""
    err_str = str(err).lower()
    keywords = (
        "no address associated with hostname",   # DNS 解析失败
        "name or service not known",             # DNS 解析失败 (Linux)
        "nodename nor servname provided",        # DNS 解析失败 (macOS)
        "max retries exceeded",                  # 连接池耗尽/重试上限
        "connection to",                         # 连接被拒/断开
        "connection reset",                      # 连接被重置
        "connection aborted",                    # 连接中断
        "read timed out",                        # 读取超时
        "connect timeout",                       # 连接超时
        "timeout",                                # 通用超时
    )
    return any(kw in err_str for kw in keywords)


def _build_session():
    """构造带连接重试的 Session。
    urllib3 层面配置 backoff_factor，使 DNS 解析失败、连接超时等
    瞬时错误在底层自动重试，避免单次失败就抛出 Max retries exceeded。
    """
    session = requests.Session()
    # urllib3.Retry 配置底层连接重试：
    # total=5 最多重试 5 次，backoff_factor=1 使退避递增（0, 1, 2, 4, 8 秒）
    # 仅对连接错误重试，不对已发送请求的读取错误重试（避免重复下载）
    from urllib3.util.retry import Retry
    retry = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[502, 503, 504],
        allowed_methods=["GET", "HEAD"],
    )
    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=10,
        pool_maxsize=10,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def download_file(session, url, filepath, headers, max_retries=5):
    """单文件下载逻辑，供多线程调用。文件名冲突时原子去重，绝不覆盖。
    对 DNS 解析失败、连接超时等瞬时网络错误自动重试，提高下载成功率。
    流式写入失败时清理残留文件，避免重试留下损坏文件。"""
    url = ensure_https(url)
    last_err = None
    for attempt in range(1, max_retries + 1):
        safe_path = None
        try:
            # 引入 0.1 ~ 0.5 秒的随机延迟，防止并发过高被瞬间阻断连接
            time.sleep(random.uniform(0.1, 0.5))
            r = session.get(url, headers=headers, stream=True, timeout=15)
            r.raise_for_status()

            # 从响应头推断真实扩展名，修正 filepath（避免 webp 存成 .jpg）
            content_type = r.headers.get("Content-Type", "")
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
            # 流式写入中途失败时清理残留文件（含空占位和 partial 写入），
            # 避免重试时 unique_path 跳过它而留下损坏文件
            if safe_path and os.path.exists(safe_path):
                try:
                    os.remove(safe_path)
                except OSError:
                    pass
            last_err = e
            # 仅对瞬时网络错误重试（DNS 失败、连接超时、连接重置）
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


def extract_xhs_author(note):
    """从小红书 note 对象取作者昵称。note.user 上的 nickname/nickName
    双 key 兼容（接口曾变更大小写）。取不到返回空串，由调用方据此不加作者层。"""
    user = (note or {}).get("user") or {}
    return user.get("nickname") or user.get("nickName") or ""


def extract_video_url(video):
    """显式、逐层解析视频直链，避免静默吞错。
    返回 (url, reason)——url 为 None 时 reason 说明原因。"""
    media = (video or {}).get("media") or {}
    stream = (media.get("stream") or {})
    h264_list = stream.get("h264") or []
    if h264_list and isinstance(h264_list, list):
        master = (h264_list[0] or {}).get("masterUrl")
        if master:
            return master, None
        return None, "h264 流存在但缺少 masterUrl"
    # 兜底：直接挂在 video 上的 url
    direct = (video or {}).get("url")
    if direct:
        return direct, None
    return None, "无 stream/h264 也无顶层 url"


def detect_risk_control(html, response):
    """检测小红书风控/验证码。综合文案 + 重定向 + 关键状态判定。"""
    if not html:
        return False
    # 文案兜底（文案可能变化，仅作辅助）
    if "验证码" in html or "访问过于频繁" in html:
        return True
    # 重定向到验证/登录域
    final_url = (response.url or "").lower() if response is not None else ""
    if "verify" in final_url or "/login" in final_url or "captcha" in final_url:
        return True
    return False


def detect_note_not_found(html, final_url):
    """检测笔记已被删除/下架而落地到 404 页的情形。

    小红书对不存在的笔记会重定向到 https://www.xiaohongshu.com/404?...
    并在 query 里带 errorCode=-510001，页面 title 为「你访问的页面不见了」。
    此时 __INITIAL_STATE__ 仍在但 noteDetailMap 为空，会被误判为页面结构变更。
    三信号任一命中即判定为笔记不存在，避免误导排查方向。
    """
    final_url = (final_url or "").lower()
    # 信号 1：重定向落地 URL 路径为 /404
    if "/404" in final_url:
        return True
    # 信号 2：URL query 带 errorCode=-510001（小红书「笔记不存在」错误码）
    if "errorcode=-510001" in final_url:
        return True
    # 信号 3：页面 title 文案
    if html and "你访问的页面不见了" in html:
        return True
    return False


def _fetch_page(session, url, headers, max_retries=5):
    """请求页面并跟随重定向，对瞬时网络错误自动重试。
    返回 Response 对象；所有重试均失败时返回 None。
    """
    url = ensure_https(url)
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            response = session.get(url, headers=headers, allow_redirects=True, timeout=15)
            response.raise_for_status()
            return response
        except Exception as e:
            last_err = e
            if attempt < max_retries and _is_transient_error(e):
                wait = attempt * 3 + random.uniform(0, 1)
                logger.warning(
                    "请求重试 %d/%d 将在 %.1fs 后重试: %s",
                    attempt, max_retries, wait, e,
                )
                time.sleep(wait)
                continue
            break
    logger.error("请求失败: %s", last_err)
    return None


def download_xhs_media(url, cookie):
    start_time = time.time()
    # 每次解析新链接都生成一个全新的随机 Header
    headers = get_headers(cookie)

    # 使用 Session 维持连接池，提高多图下载效率
    session = _build_session()

    logger.info("正在请求: %s", url)
    logger.debug("当前伪装 UA: %s", headers["User-Agent"])

    response = _fetch_page(session, url, headers)
    if response is None:
        logger.error("请求失败，耗时 %.1f 秒。", time.time() - start_time)
        return

    html = response.text

    # 风控检测（综合判定，文案 + 重定向）
    if detect_risk_control(html, response):
        logger.error(
            "提取失败：触发风控拦截或 Cookie 已失效（可能被重定向到验证页）。"
            "请在浏览器中打开链接完成验证后重试。（耗时 %.1f 秒）",
            time.time() - start_time,
        )
        return

    # 笔记不存在检测：落地到 404 页时 noteDetailMap 为空，
    # 会被下方「页面结构可能已更新」误报，需在此提前拦截并给出明确原因。
    if detect_note_not_found(html, response.url if response is not None else ""):
        logger.error(
            "提取失败：该笔记已被删除或不存在（小红书返回 404）。"
            "请确认链接是否有效或已被作者删除。（耗时 %.1f 秒）",
            time.time() - start_time,
        )
        return

    # 贪婪匹配到最后一个 } 后接 </script>，避免被内部 }</script> 截断
    state_match = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*\})\s*</script>", html, re.DOTALL)
    if not state_match:
        logger.error("未能找到页面数据。可能是页面结构变更或 Cookie 失效。（耗时 %.1f 秒）", time.time() - start_time)
        return

    try:
        # 仅替换 JSON 值语境的 undefined -> null，避免误伤正文/URL
        state_json = normalize_xhs_state_json(state_match.group(1))
        data = json.loads(state_json)
    except json.JSONDecodeError as e:
        logger.error("JSON 解析失败: %s（耗时 %.1f 秒）", e, time.time() - start_time)
        return

    try:
        note_data = data.get("note", {}).get("noteDetailMap", {})
        if not note_data:
            logger.error("提取详情失败，页面结构可能已更新。（耗时 %.1f 秒）", time.time() - start_time)
            return

        note_id = list(note_data.keys())[0]
        note = note_data[note_id].get("note", {})

        title = note.get("title", "")
        # title 可能为 None（undefined->null）、空串、纯特殊字符（如 "/"）
        # sanitize_filename 会把纯特殊字符清洗为空并返回 "untitled"，
        # 这些情况统一回退到 xhs_<noteId>，避免多篇无标题笔记都落到 untitled 目录
        safe_title = sanitize_filename(title, default="") or f"xhs_{note_id}"

        # 作者：note.user 上的 nickname/nickName（接口曾变更大小写，双 key 兼容）
        author = extract_xhs_author(note)

        # 有作者则多一层目录：download/xiaohongshu/<作者>/<标题>/；无则退化
        base_path = build_download_dir("xiaohongshu", safe_title, author=author)
        os.makedirs(base_path, exist_ok=True)
        logger.info("目标文件夹: %s", base_path)

        download_tasks = []

        # 1. 提取图片链接并加入任务池
        image_list = note.get("imageList", []) or []
        if image_list:
            logger.info("发现 %d 张图片，开启多线程下载...", len(image_list))
            for i, img in enumerate(image_list):
                img_url = (
                    img.get("urlDefault")
                    or img.get("url")
                    or ((img.get("infoList") or [{}])[0] or {}).get("url")
                )
                if img_url:
                    img_url = ensure_https(img_url)
                    filepath = os.path.join(base_path, f"{safe_title}_{i + 1}.jpg")
                    download_tasks.append((img_url, filepath))

        # 2. 提取视频链接并加入任务池（显式判定，失败有原因）
        video = note.get("video")
        if video:
            video_url, reason = extract_video_url(video)
            if video_url:
                logger.info("发现视频，加入下载队列...")
                video_url = ensure_https(video_url)
                filepath = os.path.join(base_path, f"{safe_title}_video.mp4")
                download_tasks.append((video_url, filepath))
            else:
                logger.warning("发现 video 字段但未能提取到直链：%s", reason)

        # 3. 核心：执行多线程下载任务
        if download_tasks:
            # 设定 max_workers 控制并发数，建议维持在 5 左右，过高容易触发 IP 熔断
            with ThreadPoolExecutor(max_workers=5) as executor:
                # 提交所有下载任务
                futures = [
                    executor.submit(download_file, session, task[0], task[1], headers)
                    for task in download_tasks
                ]

                # as_completed 允许我们在任意一个线程完成时立即打印结果，而不用等待所有任务结束
                succeeded = 0
                failed = 0
                for future in as_completed(futures):
                    result = future.result()
                    if result.startswith("  [失败]"):
                        failed += 1
                        logger.error("%s", result)
                    else:
                        succeeded += 1
                        logger.info("%s", result)

                elapsed = time.time() - start_time
                logger.info(
                    "下载完成：%d 成功 / %d 失败 / 共 %d 项，耗时 %.1f 秒",
                    succeeded, failed, len(download_tasks), elapsed,
                )
        else:
            logger.info("未发现可下载的媒体文件，耗时 %.1f 秒。", time.time() - start_time)

        logger.info("该链接处理完成，耗时 %.1f 秒。", time.time() - start_time)

    except Exception as e:
        logger.error("解析数据时发生意外错误: %s（耗时 %.1f 秒）", e, time.time() - start_time, exc_info=True)


if __name__ == "__main__":
    logger.info("欢迎使用媒体下载器 (输入 q 退出)")
    while True:
        try:
            raw_input = input("🔗 请输入文章链接: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 退出程序")
            break

        if raw_input.lower() == "q":
            print("👋 退出程序")
            break

        if not raw_input:
            continue

        urls = extract_urls(raw_input)
        if not urls:
            logger.warning("未识别到有效链接，请输入包含 http(s):// 的分享文本或 URL。")
            continue

        for target_url in urls:
            site_cookie = load_cookie_for_url(target_url)

            if site_cookie:
                site_name = get_site_name(target_url)
                if site_name == "xiaohongshu":
                    download_xhs_media(target_url, site_cookie)
                else:
                    logger.warning("目前还没有编写 %s 的解析代码，仅支持小红书。", site_name)
