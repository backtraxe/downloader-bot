import time
import math
import asyncio

def format_size(bytes_size):
    if bytes_size == 0: return "0B"
    size_name = ("B", "KB", "MB", "GB")
    i = int(math.floor(math.log(bytes_size, 1024)))
    p = math.pow(1024, i)
    s = round(bytes_size / p, 2)
    return f"{s} {size_name[i]}"

def render_progressbar(percent, length=10):
    filled = int(length * percent / 100)
    return '■' * filled + '□' * (length - filled)

class SmartProgress:
    def __init__(self, message, loop):
        self.message = message
        self.loop = loop
        self.last_update = 0
    
    # yt-dlp 专用的 hook
    def hook(self, d):
        if d['status'] == 'downloading':
            now = time.time()
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            downloaded = d.get('downloaded_bytes', 0)
            percent = (downloaded / total * 100) if total > 0 else 0

            # 3秒限流
            if now - self.last_update > 3: 
                self.last_update = now
                bar = render_progressbar(percent)
                speed = d.get('speed', 0)
                speed_str = format_size(speed) + "/s" if speed else "N/A"
                text = f"📥 正在下载视频/图集...\n{bar} {percent:.1f}%\n🚀 {speed_str}"
                asyncio.run_coroutine_threadsafe(self.safe_edit(text), self.loop)

    # --- 新增：通用批量进度更新 (用于普通网页抓图) ---
    def update_batch(self, current, total, desc="抓取图片"):
        now = time.time()
        # 2秒更新一次，或者是最后一张时强制更新
        if now - self.last_update > 2 or current == total:
            self.last_update = now
            percent = (current / total) * 100 if total > 0 else 0
            bar = render_progressbar(percent)
            text = f"📥 {desc}...\n{bar} {current}/{total} ({percent:.0f}%)"
            asyncio.run_coroutine_threadsafe(self.safe_edit(text), self.loop)

    async def safe_edit(self, text):
        try:
            if self.message.text != text:
                await self.message.edit_text(text)
        except Exception:
            pass
