from http_client import Session
import glob
import os
import re
import time
import random
from urllib.parse import unquote, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup

from sites import get_site_name
from utils import (
    build_download_dir,
    extract_url_numeric_id,
    extract_urls,
    get_image_dimensions,
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

# 装饰性资源过滤：logo/头像/二维码/表情包/备案图标等站点装饰图
# 按文件名关键词（下载前）与体积阈值（下载后）双重过滤
_DECORATIVE_KEYWORDS = (
    "logo", "avatar", "qrcode", "qr_code", "emotion", "emoji", "beian", "icon",
)
# 尺寸可解析时按尺寸过滤：短边小于该值判为装饰图（头像/logo 通常 ≤200px，
# 正文照片通常 ≥1000px）；解析失败回退体积阈值（正文照片通常远大于 20KB）
MIN_IMAGE_DIMENSION = 300
MIN_IMAGE_BYTES = 20 * 1024


def _is_decorative_name(filename):
    """按文件名判断是否为站点装饰资源（logo/头像/二维码/表情等）。

    用单词边界匹配而非子串命中，避免 "iconic" 被误里 "icon"、
    "avatare" 被误里 "avatar"——这类单词只是以装饰关键词开头/结尾，
    语义上不是装饰词。"""
    low = filename.lower()
    return any(re.search(rf"(?:^|[\W_]){re.escape(kw)}(?:[\W_]|$)", low)
               for kw in _DECORATIVE_KEYWORDS)


def get_headers():
    """生成随机请求头，UA 不可用时降级固定值"""
    user_agent = ua.random if ua else _FALLBACK_UA
    return {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }


def _is_block_status(status_code):
    """判定 HTTP 状态码是否为值得重试的拦截/临时故障。

    429 限流、5xx 服务端故障均为瞬时状态；酷安等站点的腾讯云 EdgeOne
    WAF 会返回非标的 567 拦截页（"请求已被站点的安全策略拦截"），
    同样值得退避后重试。4xx（401/403/404 等）是确定性错误，重试无益。
    """
    return status_code == 429 or status_code >= 500


def fetch_page(session, url, headers, max_attempts=3):
    """请求页面并在 WAF 拦截/5xx/瞬时网络错误时退避重试。

    成功返回 Response；重试用尽或遇 4xx 确定性错误返回 None。
    与 downloader.py 对 yt-dlp 错误整链退避重试同一思路：
    WAF（如腾讯云 EdgeOne）按请求概率拦截，换一条请求往往放行。
    """
    for attempt in range(1, max_attempts + 1):
        try:
            resp = session.get(url, headers=headers, timeout=15, impersonate="chrome110")
        except Exception as e:
            if attempt < max_attempts:
                wait = attempt * 2 + random.uniform(0, 1)
                logger.warning("请求异常（第 %d/%d 次），%.1fs 后重试: %s",
                               attempt, max_attempts, wait, e)
                time.sleep(wait)
                continue
            logger.error("请求失败: %s", e)
            return None
        if _is_block_status(resp.status_code):
            if attempt < max_attempts:
                wait = attempt * 2 + random.uniform(0, 1)
                logger.warning("疑似被站点防护拦截 (HTTP %d，第 %d/%d 次)，%.1fs 后重试...",
                               resp.status_code, attempt, max_attempts, wait)
                time.sleep(wait)
                continue
            logger.error("请求被站点安全防护拦截 (HTTP %d)，重试后仍失败", resp.status_code)
            return None
        try:
            resp.raise_for_status()
        except Exception as e:
            logger.error("请求失败: %s", e)
            return None
        return resp
    return None


def download_file(session, url, filepath, page_url):
    """单文件下载逻辑，增加防盗链绕过。文件名冲突时原子去重，绝不覆盖；
    下载后按 Content-Type 校正扩展名，避免名实不符。
    注意：落盘文件名已是 <标题>_NN 序号名，按名称的装饰过滤在
    collect_media_urls 扫描阶段基于原始 URL 完成，此处不做名称检查。"""
    # 重下幂等：目标已存在（含扩展名变体，如 001.jpg / 001.png 同一序号）
    # 直接跳过，避免重复下载产生 001_1 副本
    stem, _ = os.path.splitext(filepath)
    if glob.glob(stem + ".*"):
        return f"  [跳过] {os.path.basename(filepath)} 已存在，无需重复下载"

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

        # 始终传 impersonate；http_client 在 curl_cffi 不可用时用系统 curl 模拟
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

        # 装饰图过滤：优先按宽高（短边过小判为头像/logo），解析不出再按体积。
        # 视频不过滤——短视频也可能很小。
        if ext != ".mp4":
            dims = None
            with open(safe_path, "rb") as f:
                dims = get_image_dimensions(f.read(32 * 1024))
            if dims is not None:
                if min(dims) < MIN_IMAGE_DIMENSION:
                    os.remove(safe_path)
                    return (f"  [跳过] {os.path.basename(safe_path)} "
                            f"尺寸 {dims[0]}x{dims[1]} 过小，判定为装饰图")
            elif os.path.getsize(safe_path) < MIN_IMAGE_BYTES:
                os.remove(safe_path)
                return f"  [跳过] {os.path.basename(safe_path)} 体积过小，判定为装饰图"

        return f"  [成功] -> {os.path.basename(safe_path)}"
    except Exception as e:
        return f"  [失败] {os.path.basename(filepath)} 报错: {e}"


# 标题中"正文 来自 作者 - 站点"的站点后缀模式（酷安等），用于兜底提取作者
_TITLE_AUTHOR_TAIL = re.compile(r"^(?P<title>.*?)\s+来自\s+(?P<author>[^-<>]{1,30})\s+-\s+(?P<site>\S+)\s*$")


def _match_title_tail(page_title, url):
    """匹配标题的"来自 作者 - 站点"尾巴，且站点标签须与 URL 域名一致才采信。

    防止正文本身含"来自 X - Y"时被误剥（如"我 来自 上海 - 记录生活"）。
    站点标签比对比口径与 get_site_name 的最后两段归一一致。"""
    m = _TITLE_AUTHOR_TAIL.match(page_title or "")
    if not m:
        return None
    tail_site = m.group("site").strip().lower()
    page_site = get_site_name(url)
    # 站点标签可能是中文站点名（酷安）或域名本身；域名主段前缀匹配即可
    domain_core = page_site.split(".")[0].lower()
    if domain_core in tail_site or tail_site in domain_core:
        return m
    # 中文站点名映射（站点品牌名与域名无字面重合的情况）
    _SITE_BRAND = {"coolapk.com": "酷安"}
    if _SITE_BRAND.get(page_site) == m.group("site").strip():
        return m
    return None


def extract_page_author(soup, page_title, url):
    """尝试从页面提取作者，返回 (author, cleaned_title)；提取不到返回 ("", 原标题)。

    两级策略：
    1. DOM 结构选择器（酷安卡片页的 .username-item）
    2. <title> 的"正文 来自 作者 - 站点"后缀模式兜底（页面改版时仍可用），
       尾巴站点标签须与当前 URL 域名一致才采信，防正文本身含"来自"误剥
    命中时标题剥离作者尾巴，避免目录名里作者信息重复一层；
    未命中原样返回，调用方自然退化为无作者层。
    """
    tail = _match_title_tail(page_title, url)
    cleaned_title = tail.group("title") if tail else page_title

    node = soup.select_one(".username-item p")
    if node and node.get_text(strip=True):
        return node.get_text(strip=True), cleaned_title

    if tail:
        return tail.group("author").strip(), cleaned_title

    return "", page_title


def make_page_dir_name(page_title, url):
    """通用抓取的目标目录名：<标题>[_<数字ID>]。

    标题走 sanitize_filename 默认 80 字符上限（不再 [:20] 硬截断）；
    URL 末段含数字 ID（如酷安 /feed/74208176）时追加 _<id>，
    撞名时可区分、且目录名可直接溯源到原帖。
    """
    safe_title = sanitize_filename(page_title, default="未命名网页")
    nid = extract_url_numeric_id(url)
    return f"{safe_title}_{nid}" if nid else safe_title


# 常见存放真实链接的“马甲”属性列表
_LAZY_ATTRS = [
    "src", "data-src", "data-original", "data-lazy-src", "data-v-lazy",
    "data-url", "lazy-src", "file", "source", "data-src-retina", "data-hd-src",
    "ess-data", "data-link",  # 新增目标网站的专属属性
]


def collect_media_urls(soup, page_url):
    """按 DOM 顺序深度扫描媒体候选 URL，返回 [(url, 扩展名), ...]（保序去重）。

    顺序很重要：序号命名（<标题>_01.jpg）依赖 DOM 出现顺序还原帖内图片次序，
    因此用列表 + 手动去重而不是 set。
    """
    items = []
    seen = set()

    def _add(full_url, default_ext):
        if full_url in seen:
            return
        # 名称过滤必须在此时做（这里还拿得到远端原始文件名）：
        # 落盘会被重命名为 <标题>_NN，下载阶段再查名称已来不及
        raw_name = unquote(urlparse(full_url).path.rsplit("/", 1)[-1])
        if _is_decorative_name(raw_name):
            return
        seen.add(full_url)
        items.append((full_url, default_ext))

    # 1. 扫描所有图片和视频标签的隐藏属性
    for tag in soup.find_all(["img", "source", "video"]):
        for attr in _LAZY_ATTRS:
            val = tag.get(attr)
            if val and not val.startswith("data:image"):  # 排除 base64 编码的极小占位图
                full_url = normalize_url(val, page_url)
                if full_url.startswith("http"):
                    # 视频标签或 URL 含 mp4 视为视频，否则按图片
                    if tag.name == "video" or ".mp4" in full_url.lower():
                        _add(full_url, ".mp4")
                    else:
                        # 扩展名交给下载阶段按 Content-Type 校正
                        _add(full_url, guess_extension(full_url, default=".jpg"))

    # 2. 扫描包裹媒体的 A 标签 (href 直链)
    for a_tag in soup.find_all("a"):
        href = a_tag.get("href", "")
        if href and any(href.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4"]):
            full_url = normalize_url(href, page_url)
            if full_url.startswith("http"):
                _add(full_url, guess_extension(full_url, default=".jpg"))

    # 3. 扫描 CSS 行内样式中的背景图 (background-image)
    for tag in soup.find_all(style=True):
        style_content = tag["style"]
        # 使用正则提取 url() 括号内的内容
        bg_match = re.search(r"url\([\'\"]?(.*?)[\'\"]?\)", style_content)
        if bg_match:
            bg_url = bg_match.group(1).strip()
            if bg_url and not bg_url.startswith("data:image"):
                full_url = normalize_url(bg_url, page_url)
                if full_url.startswith("http"):
                    _add(full_url, guess_extension(full_url, default=".jpg"))

    return items


def build_media_tasks(media_items, base_path):
    """把扫描到的媒体列表映射为 (url, 本地路径)，按 DOM 顺序序号命名。

    目录名已带标题与 ID，文件本体只要 001.jpg / 002.mp4 即可，
    替代远端乱序哈希名，目录内按序号即还原帖内次序。"""
    tasks = []
    for idx, (media_url, default_ext) in enumerate(media_items, 1):
        filename = f"{idx:03d}{default_ext}"
        tasks.append((media_url, os.path.join(base_path, filename)))
    return tasks


def extract_general_media(url):
    """深度解析并下载静态网站的隐藏媒体文件"""
    start_time = time.time()
    headers = get_headers()
    session = Session()

    logger.info("正在请求网页: %s", url)

    response = fetch_page(session, url, headers)
    if response is None:
        logger.error("获取页面失败（耗时 %.1f 秒）", time.time() - start_time)
        return

    soup = BeautifulSoup(response.text, "html.parser")

    # 提取标题并创建文件夹（保留中文等非 ASCII）；
    # 能提取作者时剥离标题里的作者尾巴，目录加作者层（与 yt-dlp/xhs 口径一致）
    title_tag = soup.find("title")
    raw_title = title_tag.text.strip() if title_tag else "未命名网页"
    author, page_title = extract_page_author(soup, raw_title, url)
    dir_name = make_page_dir_name(page_title, url)

    # 任意网站归一化为最后两段域名（www.example.com → example.com），
    # 与 downloader.py 的站点目录命名口径一致
    site_name = get_site_name(url)
    base_path = build_download_dir(site_name, dir_name, author=author or None)
    os.makedirs(base_path, exist_ok=True)
    logger.info("目标文件夹: %s", base_path)

    media_items = collect_media_urls(soup, url)

    if not media_items:
        logger.error("深度扫描后依然没有发现媒体文件。（耗时 %.1f 秒）", time.time() - start_time)
        return

    logger.info("深度扫描完成！共发现 %d 个媒体文件，开始下载...", len(media_items))

    download_tasks = build_media_tasks(media_items, base_path)

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [
            executor.submit(download_file, session, task[0], task[1], url)
            for task in download_tasks
        ]

        for future in as_completed(futures):
            logger.info("%s", future.result())

    elapsed = time.time() - start_time
    logger.info("📁 保存目录: %s", os.path.abspath(base_path))
    logger.info("该网页媒体下载任务完成！（耗时 %.1f 秒）", elapsed)


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
