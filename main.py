import os
import logging
import asyncio
import glob
import math
import subprocess
import time  # <--- 新增导入
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
import yt_dlp
from dotenv import load_dotenv

# --- 加载环境变量 ---
load_dotenv()

# 从环境变量获取 Token
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("错误：未在 .env 文件中找到 BOT_TOKEN，请检查配置。")

# --- 配置区域 ---
DOWNLOAD_DIR = "./downloads"
COOKIES_FILE = "cookies.txt"

# --- 日志设置 ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# --- 辅助函数：生成进度条字符 ---
def render_progressbar(percent, length=10):
    """生成进度条字符串，例如 [■■■■■□□□□□]"""
    filled = int(length * percent / 100)
    return '■' * filled + '□' * (length - filled)

def format_size(bytes_size):
    """将字节转换为易读格式 (MB, GB)"""
    if bytes_size == 0: return "0B"
    size_name = ("B", "KB", "MB", "GB")
    i = int(math.floor(math.log(bytes_size, 1024)))
    p = math.pow(1024, i)
    s = round(bytes_size / p, 2)
    return f"{s} {size_name[i]}"

# --- 核心类：智能进度监听 ---
class SmartProgress:
    def __init__(self, message, loop):
        self.message = message
        self.loop = loop
        self.last_update_time = 0
        self.last_percent = 0
    
    def progress_hook(self, d):
        if d['status'] == 'downloading':
            # 获取当前时间
            now = time.time()
            
            # 计算百分比 (部分直播流可能没有 total_bytes，这里做个容错)
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            downloaded = d.get('downloaded_bytes', 0)
            
            if total > 0:
                percent = (downloaded / total) * 100
            else:
                percent = 0

            # 限制更新频率：每 3 秒更新一次，或者进度达到 100% 时立即更新
            # 同时也避免进度倒退显示的尴尬
            if (now - self.last_update_time > 3) or (percent >= 100 and self.last_percent < 100):
                self.last_update_time = now
                self.last_percent = percent
                
                # 构建显示的文本
                bar = render_progressbar(percent)
                speed = d.get('speed', 0)
                speed_str = format_size(speed) + "/s" if speed else "N/A"
                total_str = format_size(total)
                
                text = (
                    f"📥 正在下载...\n"
                    f"{bar} {percent:.1f}%\n"
                    f"🚀 速度: {speed_str} | 📦 大小: {total_str}"
                )
                
                # 必须使用 threadsafe 方法，因为 yt-dlp 在独立线程运行
                asyncio.run_coroutine_threadsafe(
                    self.safe_edit(text), 
                    self.loop
                )

        elif d['status'] == 'finished':
            asyncio.run_coroutine_threadsafe(
                self.safe_edit("✅ 下载完成，正在处理/转码..."), 
                self.loop
            )

    async def safe_edit(self, text):
        """防止因为网络波动或消息被删导致的报错"""
        try:
            # 如果文本没变，Telegram API 会报错，所以这里最好也判断一下（虽然上面限制了频率）
            if self.message.text != text:
                await self.message.edit_text(text)
        except Exception:
            pass

# --- 原始视频处理逻辑 ---
def get_video_info(file_path):
    try:
        cmd = [
            'ffprobe', 
            '-v', 'error', 
            '-show_entries', 'format=duration', 
            '-of', 'default=noprint_wrappers=1:nokey=1', 
            file_path
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"获取视频信息失败: {e}")
        return None

def split_video(file_path, file_prefix):
    file_size = os.path.getsize(file_path)
    limit_bytes = 49 * 1024 * 1024
    
    if file_size <= limit_bytes:
        return [file_path]

    print(f"文件大小 {file_size/1024/1024:.2f}MB，正在进行切割...")
    
    duration = get_video_info(file_path)
    if not duration:
        return [file_path]

    num_parts = math.ceil(file_size / limit_bytes)
    segment_time = int(duration / num_parts)
    if segment_time < 10: segment_time = 10

    output_pattern = f"{os.path.dirname(file_path)}/{file_prefix}_part%03d.mp4"
    
    cmd = [
        'ffmpeg', '-y',
        '-i', file_path,
        '-c', 'copy',
        '-map', '0',
        '-f', 'segment',
        '-segment_time', str(segment_time),
        '-reset_timestamps', '1',
        output_pattern
    ]
    
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        parts = sorted(glob.glob(f"{os.path.dirname(file_path)}/{file_prefix}_part*.mp4"))
        if parts:
            os.remove(file_path)
            return parts
        else:
            return [file_path]
    except subprocess.CalledProcessError:
        return [file_path]

# --- 核心业务逻辑 ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("你好！请直接发送 URL，我会显示下载进度条。")

async def process_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    user_id = update.effective_user.id
    message_id = update.message.message_id
    
    if not url.startswith(("http://", "https://")):
        return

    # 发送初始消息，并获取消息对象以便后续编辑
    status_msg = await update.message.reply_text("🔍 正在解析链接...")

    file_prefix = f"{user_id}_{message_id}"
    output_template = f"{DOWNLOAD_DIR}/{file_prefix}_raw.%(ext)s"
    
    # 初始化进度追踪器
    loop = asyncio.get_running_loop()
    progress_tracker = SmartProgress(status_msg, loop)

    ydl_opts = {
        'outtmpl': output_template,
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'merge_output_format': 'mp4',
        'noplaylist': True,
        'quiet': True,
        # 挂载进度回调函数
        'progress_hooks': [progress_tracker.progress_hook],
    }

    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE
        
    try:
        # 在线程池中下载 (传入包含 hook 的 opts)
        await loop.run_in_executor(None, download_media, ydl_opts, url)

        downloaded_raw_files = glob.glob(f"{DOWNLOAD_DIR}/{file_prefix}_raw.*")

        if not downloaded_raw_files:
            await status_msg.edit_text("❌ 下载失败：未找到文件。")
            return
        
        raw_file_path = downloaded_raw_files[0]
        
        # --- 切割逻辑 ---
        if raw_file_path.endswith(('.jpg', '.jpeg', '.png', '.webp')):
            final_files = [raw_file_path]
        else:
            file_size_mb = os.path.getsize(raw_file_path) / (1024 * 1024)
            if file_size_mb > 49:
                await status_msg.edit_text(f"✂️ 文件较大 ({file_size_mb:.1f}MB)，正在智能切割...")
                final_files = await loop.run_in_executor(None, split_video, raw_file_path, file_prefix)
            else:
                final_files = [raw_file_path]

        # --- 上传逻辑 ---
        total_parts = len(final_files)
        for index, file_path in enumerate(final_files):
            file_size = os.path.getsize(file_path)
            
            # 显示上传进度 (第几部分)
            part_info = f" ({index+1}/{total_parts})" if total_parts > 1 else ""
            await status_msg.edit_text(f"☁️ 正在上传{part_info}...\n(Telegram API 限制，上传无实时进度条，请耐心等待)")

            if file_size > 49.5 * 1024 * 1024:
                await update.message.reply_text(f"⚠️ 第 {index+1} 部分仍然过大 ({file_size/1024/1024:.1f}MB)，无法发送。")
            else:
                with open(file_path, 'rb') as f:
                    if file_path.endswith(('.jpg', '.jpeg', '.png', '.webp')):
                        await update.message.reply_photo(photo=f)
                    else:
                        caption = ""
                        if total_parts > 1:
                            caption = f"Part {index+1}/{total_parts}"
                        # 增加 read_timeout 防止上传大文件超时
                        await update.message.reply_video(
                            video=f, 
                            caption=caption,
                            read_timeout=120, 
                            write_timeout=120, 
                            pool_timeout=120
                        )
            
            os.remove(file_path)

        await status_msg.delete()
        
    except Exception as e:
        await status_msg.edit_text(f"❌ 发生错误: {str(e)[:100]}")
        # 出错清理
        for f in glob.glob(f"{DOWNLOAD_DIR}/{file_prefix}_*"):
            try: os.remove(f)
            except: pass

def download_media(opts, url):
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

if __name__ == '__main__':
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    start_handler = CommandHandler('start', start)
    msg_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), process_url)

    application.add_handler(start_handler)
    application.add_handler(msg_handler)

    print("Bot 正在运行 (带进度显示版)...")
    application.run_polling()
    