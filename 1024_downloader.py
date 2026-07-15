from curl_cffi import requests
import os
import re
import time
import random
import string
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup

from sites import get_site_name
from utils import (
    build_download_dir,
    extract_urls,
    guess_extension,
    init_useragent,
    is_html_content,
    normalize_url,
    sanitize_filename,
    setup_logging,
    unique_path,
)

logger = setup_logging()

DOWNLOAD_DIR = "download"

# 初始化 fake_useragent
ua, _FALLBACK_UA = init_useragent(logger)


def get_headers():
    """生成随机请求头，UA 不可用时降级固定值"""
    user_agent = ua.random if ua else _FALLBACK_UA
    return {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }


def download_file(session, url, filepath, page_url):
    """单文件下载逻辑，增加防盗链绕过。文件名冲突时原子去重，绝不覆盖；
    下载后按 Content-Type 校正扩展名，避免名实不符。"""
    # 专门为图片下载准备的仿真请求头
    img_headers = {
        "User-Agent": _FALLBACK_UA,
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": page_url,  # 关键：告诉图床我是从原网页过来的（破解防盗链）
        "Sec-Fetch-Dest": "image",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "cross-site",
    }

    try:
        # 稍微把延迟调大一点，避免瞬间并发被封 IP
        time.sleep(random.uniform(0.5, 1.5))

        # 使用 impersonate 完美伪装成 Chrome 110 浏览器
        r = session.get(url, headers=img_headers, stream=True, timeout=30, impersonate="chrome110")
        r.raise_for_status()

        # 验证一下下载下来的到底是不是真实图片
        content_type = r.headers.get("Content-Type", "")
        if is_html_content(content_type):
            return f"  [失败] {os.path.basename(filepath)} 被防火墙拦截 (返回了HTML)"

        # 按 URL 后缀 + Content-Type 校正扩展名，避免 png/webp 被误存成 .jpg
        ext = guess_extension(url, content_type, default=".jpg")
        name, old_ext = os.path.splitext(filepath)
        if old_ext.lower() != ext.lower():
            filepath = name + ext

        # 原子去重：并发同名不会撞车覆盖
        safe_path = unique_path(filepath)

        with open(safe_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return f"  [成功] -> {os.path.basename(safe_path)}"
    except Exception as e:
        return f"  [失败] {os.path.basename(filepath)} 报错: {e}"


def extract_general_media(url):
    """深度解析并下载静态网站的隐藏媒体文件"""
    headers = get_headers()
    session = requests.Session()

    logger.info("正在请求网页: %s", url)

    try:
        response = session.get(url, headers=headers, timeout=15)
        response.raise_for_status()
    except Exception as e:
        logger.error("请求失败: %s", e)
        return

    soup = BeautifulSoup(response.text, "html.parser")

    # 提取标题并创建文件夹（保留中文等非 ASCII）
    title_tag = soup.find("title")
    page_title = title_tag.text.strip() if title_tag else "未命名网页"
    safe_title = sanitize_filename(page_title, default="未命名网页")[:20]

    # 任意网站归一化为最后两段域名（www.example.com → example.com），
    # 与 downloader.py 的站点目录命名口径一致
    site_name = get_site_name(url)
    base_path = build_download_dir(site_name, safe_title)
    os.makedirs(base_path, exist_ok=True)
    logger.info("目标文件夹: %s", base_path)

    media_urls = set()  # 使用集合自动去重

    # ==========================
    # 🕵️‍♂️ 深度挖掘逻辑开始
    # ==========================

    # 常见存放真实链接的“马甲”属性列表
    lazy_attrs = [
        "src", "data-src", "data-original", "data-lazy-src", "data-v-lazy",
        "data-url", "lazy-src", "file", "source", "data-src-retina", "data-hd-src",
        "ess-data", "data-link",  # 新增目标网站的专属属性
    ]

    # 1. 扫描所有图片和视频标签的隐藏属性
    for tag in soup.find_all(["img", "source", "video"]):
        for attr in lazy_attrs:
            val = tag.get(attr)
            if val and not val.startswith("data:image"):  # 排除 base64 编码的极小占位图
                full_url = normalize_url(val, url)
                if full_url.startswith("http"):
                    # 视频标签或 URL 含 mp4 视为视频，否则按图片
                    if tag.name == "video" or ".mp4" in full_url.lower():
                        media_urls.add((full_url, ".mp4"))
                    else:
                        # 扩展名交给下载阶段按 Content-Type 校正
                        media_urls.add((full_url, guess_extension(full_url, default=".jpg")))

    # 2. 扫描包裹媒体的 A 标签 (href 直链)
    for a_tag in soup.find_all("a"):
        href = a_tag.get("href", "")
        if href and any(href.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4"]):
            full_url = normalize_url(href, url)
            if full_url.startswith("http"):
                media_urls.add((full_url, guess_extension(full_url, default=".jpg")))

    # 3. 扫描 CSS 行内样式中的背景图 (background-image)
    for tag in soup.find_all(style=True):
        style_content = tag["style"]
        # 使用正则提取 url() 括号内的内容
        bg_match = re.search(r"url\([\'\"]?(.*?)[\'\"]?\)", style_content)
        if bg_match:
            bg_url = bg_match.group(1).strip()
            if bg_url and not bg_url.startswith("data:image"):
                full_url = normalize_url(bg_url, url)
                if full_url.startswith("http"):
                    media_urls.add((full_url, guess_extension(full_url, default=".jpg")))

    # ==========================
    # 挖掘结束
    # ==========================

    if not media_urls:
        logger.error("深度扫描后依然没有发现媒体文件。")
        return

    logger.info("深度扫描完成！共发现 %d 个媒体文件，开始下载...", len(media_urls))

    download_tasks = []
    for media_url, default_ext in media_urls:
        filename = sanitize_filename(os.path.basename(urlparse(media_url).path), default="media" + default_ext)
        if not filename:
            # URL 无文件名时随机生成
            random_str = "".join(random.choices(string.ascii_letters + string.digits, k=8))
            filename = f"media_{random_str}{default_ext}"
        filepath = os.path.join(base_path, filename)
        download_tasks.append((media_url, filepath))

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [
            executor.submit(download_file, session, task[0], task[1], url)
            for task in download_tasks
        ]

        for future in as_completed(futures):
            logger.info("%s", future.result())

    logger.info("📁 保存目录: %s", os.path.abspath(base_path))
    logger.info("该网页媒体下载任务完成！")


if __name__ == "__main__":
    logger.info("通用网页静态媒体下载器 (输入 q 退出)")
    while True:
        try:
            raw_input = input("🔗 请输入任意网页链接: ").strip()
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
            extract_general_media(target_url)
