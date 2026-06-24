import requests
import re
import json
import os
import time
import random
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from fake_useragent import UserAgent

COOKIE_DIR = "cookies"
DOWNLOAD_DIR = "download"

# 初始化 fake_useragent，尽量生成 PC 端的 User-Agent
# 这样可以确保请求到的是小红书的网页端，从而顺利提取 __INITIAL_STATE__
try:
    ua = UserAgent(os='windows')
except Exception as e:
    print(f"⚠️ fake_useragent 初始化失败，将使用默认请求头。错误: {e}")
    ua = None

def get_site_name(url):
    """根据链接解析并归一化网站名称"""
    domain = urlparse(url).netloc.lower()
    
    if "xiaohongshu.com" in domain or "xhslink.com" in domain:
        return "xiaohongshu"
    elif "bilibili.com" in domain or "b23.tv" in domain:
        return "bilibili"
    elif "douyin.com" in domain or "v.douyin.com" in domain:
        return "douyin"
    else:
        parts = domain.split('.')
        if len(parts) >= 2:
            return f"{parts[-2]}.{parts[-1]}"
        return domain.replace(":", "_")

def load_cookie_for_url(url):
    """根据 URL 自动加载对应的独立 Cookie 文件"""
    site_name = get_site_name(url)
    os.makedirs(COOKIE_DIR, exist_ok=True)
    cookie_file = os.path.join(COOKIE_DIR, f"{site_name}.txt")

    if not os.path.exists(cookie_file):
        with open(cookie_file, "w", encoding="utf-8") as f:
            f.write("")
        print(f"⚠️ 未找到 {site_name} 的 Cookie！")
        print(f"👉 请在 {cookie_file} 中填入 Cookie 保存后，再尝试下载。\n")
        return None

    with open(cookie_file, "r", encoding="utf-8") as f:
        cookie = f.read().strip()

    if not cookie:
        print(f"⚠️ 文件 {cookie_file} 内容为空！请填入 Cookie 后重试。\n")
        return None

    return cookie

def get_headers(cookie):
    """动态生成请求头，使用 fake_useragent 随机替换 UA"""
    # 如果 fake-useragent 加载成功则使用随机 UA，否则使用保底 UA
    user_agent = ua.random if ua else "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    
    return {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cookie": cookie,
        "Sec-Ch-Ua-Platform": '"Windows"', 
    }

def download_file(session, url, filepath, headers):
    """单文件下载逻辑，供多线程调用"""
    try:
        # 引入 0.1 ~ 0.5 秒的随机延迟，防止并发过高被瞬间阻断连接
        time.sleep(random.uniform(0.1, 0.5)) 
        r = session.get(url, headers=headers, stream=True, timeout=15)
        r.raise_for_status()
        
        with open(filepath, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return f"  [成功] -> {os.path.basename(filepath)}"
    except Exception as e:
        return f"  [失败] {os.path.basename(filepath)} 下载报错: {e}"

def download_xhs_media(url, cookie):
    # 每次解析新链接都生成一个全新的随机 Header
    headers = get_headers(cookie)
    
    # 使用 Session 维持连接池，提高多图下载效率
    session = requests.Session()

    print(f"🔗 正在请求: {url}")
    print(f"🕵️ 当前伪装 UA: {headers['User-Agent']}")
    
    try:
        response = session.get(url, headers=headers, allow_redirects=True, timeout=10)
        response.raise_for_status()
    except Exception as e:
        print(f"❌ 请求失败: {e}\n")
        return

    html = response.text

    # 风控检测拦截
    if "验证码" in html or "访问过于频繁" in html:
        print("❌ 提取失败：当前 IP 似乎触发了小红书的风控拦截，或者 Cookie 已失效。请尝试在浏览器中打开链接完成验证。\n")
        return

    state_match = re.search(r'window\.__INITIAL_STATE__=({.*?})</script>', html, re.DOTALL)
    if not state_match:
        print("❌ 未能找到页面数据。可能是页面结构变更或 Cookie 失效。\n")
        return

    try:
        state_json = state_match.group(1).replace('undefined', 'null')
        data = json.loads(state_json)
    except json.JSONDecodeError as e:
        print(f"❌ JSON 解析失败: {e}\n")
        return

    try:
        note_data = data.get('note', {}).get('noteDetailMap', {})
        if not note_data:
            print("❌ 提取详情失败，页面结构可能已更新。\n")
            return

        note_id = list(note_data.keys())[0]
        note = note_data[note_id].get('note', {})
        
        title = note.get('title', f'xhs_{note_id}')
        safe_title = re.sub(r'[\\/*?:"<>|\r\n]', "", title).strip() or f'xhs_{note_id}'

        base_path = os.path.join(DOWNLOAD_DIR, safe_title)
        os.makedirs(base_path, exist_ok=True)
        print(f"📁 目标文件夹: {base_path}")
 
        download_tasks = []

        # 1. 提取图片链接并加入任务池
        image_list = note.get('imageList', [])
        if image_list:
            print(f"📸 发现 {len(image_list)} 张图片，开启多线程下载...")
            for i, img in enumerate(image_list):
                img_url = img.get('urlDefault') or img.get('url') or img.get('infoList', [{}])[0].get('url')
                if img_url:
                    if img_url.startswith('//'):
                        img_url = 'https:' + img_url
                    filepath = os.path.join(base_path, f"{safe_title}_{i+1}.jpg")
                    download_tasks.append((img_url, filepath))

        # 2. 提取视频链接并加入任务池
        video = note.get('video')
        if video:
            print("🎥 发现视频，加入下载队列...")
            video_url = None
            try:
                video_url = video.get('media', {}).get('stream', {}).get('h264', [{}])[0].get('masterUrl')
            except (IndexError, AttributeError):
                pass
            
            if not video_url:
                video_url = video.get('url')

            if video_url:
                if video_url.startswith('//'):
                    video_url = 'https:' + video_url
                filepath = os.path.join(base_path, f"{safe_title}_video.mp4")
                download_tasks.append((video_url, filepath))

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
                    print(future.result())

        print("✅ 该链接下载任务完成！\n" + "-"*40 + "\n")

    except Exception as e:
         print(f"❌ 解析数据时发生意外错误: {e}\n")

if __name__ == "__main__":
    print("🚀 欢迎使用媒体下载器 (输入 q 退出)")
    while True:
        target_url = input("🔗 请输入文章链接: ").strip()
        if target_url.lower() == 'q':
            print("👋 退出程序")
            break
            
        if not target_url:
            continue
            
        site_cookie = load_cookie_for_url(target_url)
        
        if site_cookie:
            site_name = get_site_name(target_url)
            if site_name == "xiaohongshu":
                download_xhs_media(target_url, site_cookie)
            else:
                print(f"⚠️ 目前还没有编写 {site_name} 的解析代码，仅支持小红书。\n")
