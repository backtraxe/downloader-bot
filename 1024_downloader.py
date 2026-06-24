from curl_cffi import requests
import os
import re
import time
import random
import string
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
from fake_useragent import UserAgent

DOWNLOAD_DIR = "download"

# 初始化 fake_useragent
try:
    ua = UserAgent(os='windows')
except Exception as e:
    print(f"⚠️ fake_useragent 初始化失败。错误: {e}")
    ua = None

def get_headers():
    """生成随机请求头"""
    user_agent = ua.random if ua else "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    return {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

def get_safe_filename(url, default_ext=".jpg"):
    """从 URL 中提取安全的文件名"""
    parsed = urlparse(url)
    basename = os.path.basename(parsed.path)
    if not basename:
        # 如果 URL 没有具体文件名，则随机生成一个
        random_str = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        basename = f"media_{random_str}{default_ext}"
    
    # 移除非法字符
    valid_chars = "-_.() %s%s" % (string.ascii_letters, string.digits)
    filename = ''.join(c for c in basename if c in valid_chars)
    return filename if filename else f"unknown{default_ext}"

def download_file(session, url, filepath, page_url):
    """单文件下载逻辑，增加防盗链绕过"""
    # 专门为图片下载准备的仿真请求头
    img_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": page_url,  # 关键：告诉图床我是从原网页过来的（破解防盗链）
        "Sec-Fetch-Dest": "image",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "cross-site",
    }
    
    try:
        # 避免文件重名被覆盖
        if os.path.exists(filepath):
            name, ext = os.path.splitext(filepath)
            filepath = f"{name}_{int(time.time())}{ext}"

        time.sleep(random.uniform(0.5, 1.5)) # 稍微把延迟调大一点，避免瞬间并发被封 IP
        
        # 使用 impersonate 完美伪装成 Chrome 110 浏览器
        r = session.get(url, headers=img_headers, stream=True, timeout=30, impersonate="chrome110")
        r.raise_for_status()
        
        # 验证一下下载下来的到底是不是真实图片
        content_type = r.headers.get('Content-Type', '')
        if 'text/html' in content_type:
             return f"  [失败] {os.path.basename(filepath)} 被防火墙拦截 (返回了HTML)"

        with open(filepath, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return f"  [成功] -> {os.path.basename(filepath)}"
    except Exception as e:
        return f"  [失败] {os.path.basename(filepath)} 报错: {e}"

def extract_general_media(url):
    """深度解析并下载静态网站的隐藏媒体文件"""
    headers = get_headers()
    session = requests.Session()

    print(f"🔗 正在请求网页: {url}")
    
    try:
        response = session.get(url, headers=headers, timeout=15)
        response.raise_for_status()
    except Exception as e:
        print(f"❌ 请求失败: {e}\n")
        return

    soup = BeautifulSoup(response.text, 'html.parser')
    
    # 提取标题并创建文件夹
    title_tag = soup.find('title')
    page_title = title_tag.text.strip() if title_tag else "未命名网页"
    valid_chars = "-_.() %s%s" % (string.ascii_letters, string.digits)
    safe_title = ''.join(c for c in page_title if c in valid_chars)
    
    domain = urlparse(url).netloc
    base_path = os.path.join(DOWNLOAD_DIR, f"{domain}_{safe_title[:20]}")
    os.makedirs(base_path, exist_ok=True)
    print(f"📁 目标文件夹: {base_path}")

    media_urls = set() # 使用集合自动去重

    # ==========================
    # 🕵️‍♂️ 深度挖掘逻辑开始
    # ==========================
    
    # 常见存放真实链接的“马甲”属性列表
    lazy_attrs = [
        'src', 'data-src', 'data-original', 'data-lazy-src', 'data-v-lazy', 
        'data-url', 'lazy-src', 'file', 'source', 'data-src-retina', 'data-hd-src',
        'ess-data', 'data-link'  # 新增目标网站的专属属性
    ]

    # 1. 扫描所有图片和视频标签的隐藏属性
    for tag in soup.find_all(['img', 'source', 'video']):
        for attr in lazy_attrs:
            val = tag.get(attr)
            if val and not val.startswith('data:image'): # 排除 base64 编码的极小占位图
                if val.startswith('//'):
                    val = 'https:' + val
                full_url = urljoin(url, val)
                if full_url.startswith('http'):
                    ext = ".mp4" if tag.name == 'video' or 'mp4' in full_url else ".jpg"
                    media_urls.add((full_url, ext))

    # 2. 扫描包裹媒体的 A 标签 (href 直链)
    for a_tag in soup.find_all('a'):
        href = a_tag.get('href', '')
        if href and any(href.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.mp4']):
            if href.startswith('//'):
                href = 'https:' + href
            ext = ".mp4" if 'mp4' in href else ".jpg"
            media_urls.add((urljoin(url, href), ext))

    # 3. 扫描 CSS 行内样式中的背景图 (background-image)
    for tag in soup.find_all(style=True):
        style_content = tag['style']
        # 使用正则提取 url() 括号内的内容
        bg_match = re.search(r'url\([\'"]?(.*?)[\'"]?\)', style_content)
        if bg_match:
            bg_url = bg_match.group(1).strip()
            if bg_url and not bg_url.startswith('data:image'): 
                if bg_url.startswith('//'):
                    bg_url = 'https:' + bg_url
                media_urls.add((urljoin(url, bg_url), ".jpg"))

    # ==========================
    # 挖掘结束
    # ==========================

    if not media_urls:
        print("❌ 深度扫描后依然没有发现媒体文件。\n")
        return

    print(f"🔍 深度扫描完成！共发现 {len(media_urls)} 个媒体文件，开始下载...")
    
    download_tasks = []
    for media_url, default_ext in media_urls:
        filename = get_safe_filename(media_url, default_ext)
        filepath = os.path.join(base_path, filename)
        download_tasks.append((media_url, filepath))

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [
            executor.submit(download_file, session, task[0], task[1], url)
            for task in download_tasks
        ]
        
        for future in as_completed(futures):
            print(future.result())

    print("✅ 该网页媒体下载任务完成！\n" + "-"*40 + "\n")

if __name__ == "__main__":
    print("🚀 通用网页静态媒体下载器 (输入 q 退出)")
    while True:
        target_url = input("🔗 请输入任意网页链接: ").strip()
        if target_url.lower() == 'q':
            print("👋 退出程序")
            break
            
        if not target_url.startswith('http'):
            print("⚠️ 链接需以 http:// 或 https:// 开头\n")
            continue
            
        extract_general_media(target_url)
