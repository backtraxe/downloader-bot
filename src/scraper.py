import requests
import os
import re
import logging
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from config import USER_AGENT, DOWNLOAD_DIR

def scrape_generic_images(url, file_prefix):
    """
    针对 1024 手机版 (UTF-8) 和 电脑版 (GBK) 的通用适配
    """
    print(f"🕷️ 启动网页爬虫模式: {url}")
    downloaded_files = []
    
    # 模拟真实手机浏览器访问
    headers = {
        'User-Agent': USER_AGENT,
        'Referer': 'https://t66y.com/index.php',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    }

    try:
        # 1. 请求网页
        response = requests.get(url, headers=headers, timeout=20)
        
        # 2. 智能编码处理 (解决手机版 UTF-8 和电脑版 GBK 的冲突)
        # 先让 requests 自己猜
        response.encoding = response.apparent_encoding
        
        # 如果猜错了，手动修正 (t66y 手机版通常是 utf-8)
        if 'charset=utf-8' in response.text.lower():
            response.encoding = 'utf-8'
        elif 'charset=gbk' in response.text.lower():
            response.encoding = 'gbk'

        html_content = response.text
        
        print(f"📄 页面编码识别为: {response.encoding}")

        # 3. 暴力正则提取 (不依赖 HTML 结构，直接抠链接)
        # 匹配 http://...jpg 或 https://...png 等
        # 针对部分图床链接里没有扩展名的情况，放宽策略
        img_urls = set()
        
        # 模式 A: 标准图片链接 (.jpg, .png 等)
        regex_pattern = r'(http[s]?://[^\s"\'<>]+?\.(?:jpg|jpeg|png|webp|gif))'
        matches = re.findall(regex_pattern, html_content, re.IGNORECASE)
        for m in matches:
            img_urls.add(m)

        # 模式 B: 针对 ess-data 或 data-src (部分图床)
        soup = BeautifulSoup(html_content, 'html.parser')
        tags = soup.find_all(['img', 'input', 'a'])
        for tag in tags:
            # 尝试所有可能的属性
            link = tag.get('src') or tag.get('data-src') or tag.get('ess-data') or tag.get('href')
            if link and isinstance(link, str):
                # 过滤掉非图片链接
                if any(ext in link.lower() for ext in ['.jpg', '.jpeg', '.png', '.webp']):
                    img_urls.add(urljoin(url, link))

        # 4. 过滤与清洗
        valid_urls = []
        for link in img_urls:
            # 过滤掉网站自带的垃圾图标、广告图
            if any(x in link for x in ['/images/', '/face/', 'logo', 'icon', 'button', 'redircdn', 'xml', 'css']):
                continue
            # 过滤掉 text/css 这种假阳性
            if '.css' in link or '.js' in link:
                continue
            valid_urls.append(link)

        print(f"🔎 经筛选，共找到 {len(valid_urls)} 张潜在图片")

        # 5. 下载循环
        for i, img_url in enumerate(valid_urls[:60]): # 限制下载前60张
            try:
                # 确定文件后缀
                path = urlparse(img_url).path
                ext = os.path.splitext(path)[1].lower()
                if not ext: ext = '.jpg'
                
                save_path = f"{DOWNLOAD_DIR}/{file_prefix}_web_{i:03d}{ext}"
                
                # 下载请求 (带上 Referer 防止图床 403)
                img_headers = headers.copy()
                img_headers['Referer'] = url 
                
                # stream=True 防止大文件爆内存
                r = requests.get(img_url, headers=img_headers, stream=True, timeout=10)
                
                if r.status_code == 200:
                    with open(save_path, 'wb') as f:
                        for chunk in r.iter_content(1024): 
                            f.write(chunk)
                    
                    # 再次过滤：小于 30KB 的通常是广告小图
                    if os.path.getsize(save_path) < 30 * 1024:
                        os.remove(save_path)
                    else:
                        downloaded_files.append(save_path)
            except Exception:
                continue

    except Exception as e:
        print(f"❌ 爬虫错误: {e}")

    print(f"✅ 抓取完成，有效图片: {len(downloaded_files)} 张")
    return downloaded_files
