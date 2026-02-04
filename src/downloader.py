# src/downloader.py
import yt_dlp
import os
import logging
from config import USER_AGENT, COOKIES_FILE

logger = logging.getLogger(__name__)

def download_with_ytdlp(url, output_template, progress_hook=None):
    """
    使用 yt-dlp 下载
    """
    # logger.info(f"启动 yt-dlp 下载: {url}") # 可选，嫌日志太多可以注释掉
    
    ydl_opts = {
        'outtmpl': output_template,
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'noplaylist': False,
        'quiet': True,
        'user_agent': USER_AGENT,
        'ignoreerrors': True,
    }
    
    if progress_hook:
        ydl_opts['progress_hooks'] = [progress_hook]

    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        logger.error(f"yt-dlp 内部错误: {e}")
        raise e
