# -*- coding: utf-8 -*-
"""两个下载脚本共用的工具函数：UA 初始化、URL 归一化、文件名清洗、
原子去重、扩展名推断、日志配置。

集中在此以避免两个脚本各写一份且实现不一致。"""
import logging
import os
import re
from urllib.parse import urljoin, urlparse

# 文件名中需要剔除的非法字符（跨平台：路径分隔符 + Windows 保留字符）
_ILLEGAL_FILENAME_CHARS = re.compile(r'[\\/*?:"<>|\r\n]')

# 常见媒体扩展名白名单，用于从 URL 推断类型
_EXT_BY_URL = re.compile(r'\.(jpe?g|png|gif|webp|mp4|mov|webm|avi|mkv)(?:\?|#|$)', re.IGNORECASE)

# Content-Type -> 扩展名映射
_EXT_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/avif": ".avif",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-msvideo": ".avi",
    "video/x-matroska": ".mkv",
}

# 被防盗链/风控拦截时常返回的 HTML content-type
_HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """配置根日志格式，返回 logger。幂等——重复调用不会叠加 handler。"""
    root = logging.getLogger()
    # 清理已存在 handler，避免重复调用叠加输出
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S")
    )
    root.addHandler(handler)
    root.setLevel(level)
    return logging.getLogger("downloader")


def init_useragent(logger: logging.Logger):
    """初始化 fake_useragent。若随机 UA 不可用（数据加载失败 / fallback），
    返回 (ua_obj_or_None, fallback_ua_string)。调用方应优先用 ua.random，
    None 时回退到 fallback_ua。不静默失败——会记录 warning。"""
    try:
        from fake_useragent import UserAgent
    except ImportError:
        logger.warning("fake_useragent 未安装，使用固定 User-Agent")
        return None, _DEFAULT_UA

    try:
        ua = UserAgent(os="windows")
        # 主动触发一次 random，探测数据是否可用；
        # 2.x 在数据缺失时会记录 "Error occurred ... suppressed with fallback" 到 stderr
        # 并返回固定 fallback。这里无法直接判定，故用 try/except + 哨兵探测。
        sample = ua.random
        if not sample or sample == ua.fallback:
            logger.warning("fake_useragent 随机 UA 不可用（数据加载失败），降级为固定 UA")
            return None, _DEFAULT_UA
        return ua, _DEFAULT_UA
    except Exception as e:  # noqa: BLE001 初始化失败需兜底
        logger.warning("fake_useragent 初始化失败，降级为固定 UA。错误: %s", e)
        return None, _DEFAULT_UA


def normalize_url(url, page_url: str) -> str:
    """把相对/协议相对 URL 归一化为绝对 URL；data: URI 与空值原样返回。"""
    if not url:
        return ""
    url = url.strip()
    if not url or url.startswith("data:"):
        return url
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return urljoin(page_url, url)


def sanitize_filename(name: str, default: str = "untitled", max_len: int = 80) -> str:
    """清洗文件名：剔除路径分隔符与 Windows 保留字符，保留中文等非 ASCII，
    去首尾空白；空串/纯空白返回 default；超长截断。"""
    if not name:
        return default
    cleaned = _ILLEGAL_FILENAME_CHARS.sub("", name).strip()
    if not cleaned:
        return default
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len]
    return cleaned


def guess_extension(url: str, content_type: str = "", default: str = ".jpg") -> str:
    """由 URL 后缀或 Content-Type 推断扩展名。
    URL 明确扩展名优先于 content_type（避免被防盗链 HTML 响应误导）；
    否则用 content_type；都没有则返回 default。"""
    if url:
        m = _EXT_BY_URL.search(url)
        if m:
            return "." + m.group(1).lower()
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct and ct not in _HTML_CONTENT_TYPES:
        ext = _EXT_BY_CONTENT_TYPE.get(ct)
        if ext:
            return ext
    return default


def is_html_content(content_type: str) -> bool:
    """判断响应是否为 HTML（通常意味着被防盗链/风控拦截）。"""
    ct = (content_type or "").split(";")[0].strip().lower()
    return ct in _HTML_CONTENT_TYPES


def unique_path(path: str) -> str:
    """对给定路径做原子去重：若已存在，追加 `_1`、`_2`… 后缀。
    通过 os.open(O_CREAT|O_EXCL) 创建占位文件保证并发下不撞名，
    返回的路径已被独占创建（空文件），调用方写入即可。"""
    if not os.path.exists(path):
        # 尝试原子创建占位，避免与并发线程竞争
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return path
        except FileExistsError:
            pass  # 落到下面的去重逻辑

    root, ext = os.path.splitext(path)
    idx = 1
    while True:
        candidate = f"{root}_{idx}{ext}"
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return candidate
        except FileExistsError:
            idx += 1


def normalize_xhs_state_json(raw: str) -> str:
    """把小红书 __INITIAL_STATE__ 里 JSON 值语境的 undefined 替换为 null。
    只替换 `:` 或 `[` 之后、作为独立 token 的 undefined，避免误伤正文/URL 中的 "undefined" 字样。"""
    return re.sub(r'(?<=[\[:\s,])\s*undefined\b', " null", raw)
