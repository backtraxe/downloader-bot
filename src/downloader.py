import yt_dlp
import os
from config import USER_AGENT, COOKIES_FILE

def download_with_ytdlp(url, output_template, progress_hook=None):
    """
    使用 yt-dlp 下载视频或图集
    """
    ydl_opts = {
        'outtmpl': output_template,
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'noplaylist': False,  # 支持播放列表（对应小红书图集）
        'quiet': True,
        'user_agent': USER_AGENT,
        'ignoreerrors': True,
    }
    
    if progress_hook:
        ydl_opts['progress_hooks'] = [progress_hook]

    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
