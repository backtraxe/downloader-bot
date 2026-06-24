# -*- coding: utf-8 -*-
"""站点识别与 Cookie 管理。

被 downloader.py(统一入口)与 xhs_downloader.py 共用，
保证 URL→站点名 的映射在两处一致。
"""
import logging
import os
from urllib.parse import urlparse

logger = logging.getLogger("downloader")

COOKIE_DIR = "cookies"

# 站点配置：site_name -> 是否强烈建议填写 Cookie
# youtube / x 通常无需 Cookie；instagram 几乎强制；bilibili / douyin 视频高清或风控规避需要
SITE_REQUIRE_COOKIE = {
    "xiaohongshu": "建议",  # 解析笔记需要
    "bilibili": "建议",
    "douyin": "建议",
    "youtube": "通常不需要",
    "instagram": "强烈建议",
    "twitter": "通常不需要",
}


def get_site_name(url):
    """根据链接解析并归一化网站名称。返回小写站点名，
    未知域名取最后两段；无点/IP 用完整 host。"""
    domain = urlparse(url).netloc.lower()

    if "xiaohongshu.com" in domain or "xhslink.com" in domain:
        return "xiaohongshu"
    elif "bilibili.com" in domain or "b23.tv" in domain:
        return "bilibili"
    elif "douyin.com" in domain or "v.douyin.com" in domain:
        return "douyin"
    elif "youtube.com" in domain or "youtu.be" in domain:
        return "youtube"
    elif "instagram.com" in domain or "instagr.am" in domain:
        return "instagram"
    elif "twitter.com" in domain or "x.com" in domain or "t.co" in domain:
        # X 历史上叫 Twitter，cookie 文件沿用 twitter.txt
        return "twitter"
    else:
        # 未知域名：取最后两段作为站点名；无点/IP 场景用完整 host
        parts = domain.split(":")[0].split(".")
        if len(parts) >= 2:
            return f"{parts[-2]}.{parts[-1]}"
        return domain.replace(":", "_") or "unknown"


def cookie_file_for(site_name):
    """返回某站点对应的 cookie 文件路径。"""
    return os.path.join(COOKIE_DIR, f"{site_name}.txt")


def load_cookie_for_url(url):
    """根据 URL 自动加载对应的独立 Cookie 文件。
    Cookie 为敏感凭据，仅从 gitignore 的 cookies/ 目录读取。
    文件不存在时自动创建空文件并提示；内容为空则返回 None。"""
    site_name = get_site_name(url)
    os.makedirs(COOKIE_DIR, exist_ok=True)
    cookie_file = cookie_file_for(site_name)

    if not os.path.exists(cookie_file):
        with open(cookie_file, "w", encoding="utf-8") as f:
            f.write("")
        logger.warning("未找到 %s 的 Cookie！", site_name)
        logger.warning("请在 %s 中填入 Cookie 保存后，再尝试下载。", cookie_file)
        return None

    with open(cookie_file, "r", encoding="utf-8") as f:
        cookie = f.read().strip()

    if not cookie:
        logger.warning("文件 %s 内容为空！请填入 Cookie 后重试。", cookie_file)
        return None

    return cookie
