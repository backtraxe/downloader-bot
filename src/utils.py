import time
import math
import asyncio

def format_size(bytes_size):
    """将字节转换为 MB/GB"""
    if bytes_size == 0: return "0B"
    size_name = ("B", "KB", "MB", "GB")
    i = int(math.floor(math.log(bytes_size, 1024)))
    p = math.pow(1024, i)
    s = round(bytes_size / p, 2)
    return f"{s} {size_name[i]}"

def render_progressbar(percent, length=10):
    """生成字符进度条"""
    filled = int(length * percent / 100)
    return '■' * filled + '□' * (length - filled)

class SmartProgress:
    """智能进度条：控制更新频率，防止 Bot 被限流"""
    def __init__(self, message, loop):
        self.message = message
        self.loop = loop
        self.last_update = 0
        self.last_percent = 0
    
    def hook(self, d):
        if d['status'] == 'downloading':
            now = time.time()
            # 简单计算百分比
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            downloaded = d.get('downloaded_bytes', 0)
            percent = (downloaded / total * 100) if total > 0 else 0

            # 3秒更新一次
            if now - self.last_update > 3: 
                self.last_update = now
                bar = render_progressbar(percent)
                speed = d.get('speed', 0)
                speed_str = format_size(speed) + "/s" if speed else "N/A"
                
                text = (
                    f"📥 正在下载...\n"
                    f"{bar} {percent:.1f}%\n"
                    f"🚀 {speed_str}"
                )
                asyncio.run_coroutine_threadsafe(
                    self.safe_edit(text), 
                    self.loop
                )

    async def safe_edit(self, text):
        try:
            if self.message.text != text:
                await self.message.edit_text(text)
        except Exception:
            pass
