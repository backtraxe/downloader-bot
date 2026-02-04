import requests
import os
import re
import logging  # 导入 logging
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from config import USER_AGENT, DOWNLOAD_DIR

# 获取当前模块的 logger
logger = logging.getLogger(__name__)

def scrape_generic_images(url, file_prefix, progress_callback=None):
    """
    通用网页图片爬虫 (Logging 版)
    """
    logger.info(f"🕷️ 启动网页爬虫模式: {url}")
    downloaded_files = []
    
    headers = {
        'User-Agent': USER_AGENT,
        'Referer': 'https://t66y.com/index.php',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    }

    try:
        response = requests.get(url, headers=headers, timeout=20)
        
        # 编码处理
        response.encoding = response.apparent_encoding
        if 'charset=utf-8' in response.text.lower():
            response.encoding = 'utf-8'
        elif 'charset=gbk' in response.text.lower():
            response.encoding = 'gbk'

        logger.info(f"📄 页面编码识别为: {response.encoding}")
        html_content = response.text
        
        # 提取链接
        img_urls = set()
        
        # 1. 正则提取
        regex_pattern = r'(http[s]?://[^\s"\'<>]+?\.(?:jpg|jpeg|png|webp|gif))'
        matches = re.findall(regex_pattern, html_content, re.IGNORECASE)
        for m in matches:
            img_urls.add(m)

        # 2. DOM 解析补充
        soup = BeautifulSoup(html_content, 'html.parser')
        tags = soup.find_all(['img', 'input', 'a'])
        for tag in tags:
            link = tag.get('src') or tag.get('data-src') or tag.get('ess-data') or tag.get('href')
            if link and isinstance(link, str):
                if any(ext in link.lower() for ext in ['.jpg', '.jpeg', '.png', '.webp']):
                    img_urls.add(urljoin(url, link))

        # 3. 过滤
        valid_urls = []
        for link in img_urls:
            if any(x in link for x in ['/images/', '/face/', 'logo', 'icon', 'button', 'redircdn', '.css', '.js']):
                continue
            valid_urls.append(link)

        # 限制数量
        valid_urls = valid_urls[:60]
        total_count = len(valid_urls)
        logger.info(f"🔎 经筛选，共找到 {total_count} 张潜在图片")

        # 4. 下载循环
        for i, img_url in enumerate(valid_urls):
            if progress_callback:
                progress_callback(i + 1, total_count)

            try:
                path = urlparse(img_url).path
                ext = os.path.splitext(path)[1].lower()
                if not ext: ext = '.jpg'
                
                save_path = f"{DOWNLOAD_DIR}/{file_prefix}_web_{i:03d}{ext}"
                img_headers = headers.copy()
                img_headers['Referer'] = url 
                
                r = requests.get(img_url, headers=img_headers, stream=True, timeout=10)
                
                if r.status_code == 200:
                    with open(save_path, 'wb') as f:
                        for chunk in r.iter_content(1024): 
                            f.write(chunk)
                    
                    if os.path.getsize(save_path) < 30 * 1024:
                        os.remove(save_path)
                        # logger.debug(f"已丢弃过小图片: {img_url}") 
                    else:
                        downloaded_files.append(save_path)
            except Exception as e:
                logger.warning(f"图片下载失败 {img_url}: {e}")
                continue

    except Exception as e:
        logger.error(f"❌ 爬虫发生严重错误: {e}", exc_info=True) # exc_info=True 会打印堆栈轨迹

    logger.info(f"✅ 抓取完成，有效保存: {len(downloaded_files)} 张")
    return downloaded_files
