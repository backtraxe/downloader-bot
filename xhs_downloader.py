import requests
import re
import json
import os
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed

from sites import get_site_name, load_cookie_for_url
from utils import (
    init_useragent,
    normalize_xhs_state_json,
    sanitize_filename,
    setup_logging,
    unique_path,
)

logger = setup_logging()

DOWNLOAD_DIR = "download"

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
    }


def download_file(session, url, filepath, headers):
    """单文件下载逻辑，供多线程调用。文件名冲突时原子去重，绝不覆盖。"""
    try:
        # 引入 0.1 ~ 0.5 秒的随机延迟，防止并发过高被瞬间阻断连接
        time.sleep(random.uniform(0.1, 0.5))
        r = session.get(url, headers=headers, stream=True, timeout=15)
        r.raise_for_status()

        safe_path = unique_path(filepath)
        with open(safe_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return f"  [成功] -> {os.path.basename(safe_path)}"
    except Exception as e:
        return f"  [失败] {os.path.basename(filepath)} 下载报错: {e}"


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
    direct = video.get("url")
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


def download_xhs_media(url, cookie):
    # 每次解析新链接都生成一个全新的随机 Header
    headers = get_headers(cookie)

    # 使用 Session 维持连接池，提高多图下载效率
    session = requests.Session()

    logger.info("正在请求: %s", url)
    logger.debug("当前伪装 UA: %s", headers["User-Agent"])

    try:
        response = session.get(url, headers=headers, allow_redirects=True, timeout=10)
        response.raise_for_status()
    except Exception as e:
        logger.error("请求失败: %s", e)
        return

    html = response.text

    # 风控检测（综合判定，文案 + 重定向）
    if detect_risk_control(html, response):
        logger.error(
            "提取失败：触发风控拦截或 Cookie 已失效（可能被重定向到验证页）。"
            "请在浏览器中打开链接完成验证后重试。"
        )
        return

    # 贪婪匹配到最后一个 } 后接 </script>，避免被内部 }</script> 截断
    state_match = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*\})\s*</script>", html, re.DOTALL)
    if not state_match:
        logger.error("未能找到页面数据。可能是页面结构变更或 Cookie 失效。")
        return

    try:
        # 仅替换 JSON 值语境的 undefined -> null，避免误伤正文/URL
        state_json = normalize_xhs_state_json(state_match.group(1))
        data = json.loads(state_json)
    except json.JSONDecodeError as e:
        logger.error("JSON 解析失败: %s", e)
        return

    try:
        note_data = data.get("note", {}).get("noteDetailMap", {})
        if not note_data:
            logger.error("提取详情失败，页面结构可能已更新。")
            return

        note_id = list(note_data.keys())[0]
        note = note_data[note_id].get("note", {})

        title = note.get("title", f"xhs_{note_id}")
        safe_title = sanitize_filename(title) or f"xhs_{note_id}"

        base_path = os.path.join(DOWNLOAD_DIR, safe_title)
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
                    if img_url.startswith("//"):
                        img_url = "https:" + img_url
                    filepath = os.path.join(base_path, f"{safe_title}_{i + 1}.jpg")
                    download_tasks.append((img_url, filepath))

        # 2. 提取视频链接并加入任务池（显式判定，失败有原因）
        video = note.get("video")
        if video:
            video_url, reason = extract_video_url(video)
            if video_url:
                logger.info("发现视频，加入下载队列...")
                if video_url.startswith("//"):
                    video_url = "https:" + video_url
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
                for future in as_completed(futures):
                    logger.info("%s", future.result())

        logger.info("该链接下载任务完成！")

    except Exception as e:
        logger.error("解析数据时发生意外错误: %s", e)


if __name__ == "__main__":
    logger.info("欢迎使用媒体下载器 (输入 q 退出)")
    while True:
        try:
            target_url = input("🔗 请输入文章链接: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 退出程序")
            break

        if target_url.lower() == "q":
            logger.info("退出程序")
            break

        if not target_url:
            continue

        site_cookie = load_cookie_for_url(target_url)

        if site_cookie:
            site_name = get_site_name(target_url)
            if site_name == "xiaohongshu":
                download_xhs_media(target_url, site_cookie)
            else:
                logger.warning("目前还没有编写 %s 的解析代码，仅支持小红书。", site_name)
