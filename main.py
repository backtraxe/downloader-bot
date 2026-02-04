import os
import logging
import asyncio
import glob
import math
import subprocess
import json
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
import yt_dlp
from dotenv import load_dotenv  # <--- 新增导入

# --- 加载环境变量 ---
load_dotenv()  # 加载 .env 文件

# 从环境变量获取 Token
BOT_TOKEN = os.getenv("BOT_TOKEN")

# 检查 Token 是否存在，防止报错
if not BOT_TOKEN:
    raise ValueError("错误：未在 .env 文件中找到 BOT_TOKEN，请检查配置。")

# --- 配置区域 ---
DOWNLOAD_DIR = "./downloads"           # 下载文件存放路径
COOKIES_FILE = "cookies.txt"           # Cookie文件路径 (可选，用于Twitter/Insta/B站高清)

# --- 日志设置 ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# 确保下载目录存在
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

def get_video_info(file_path):
    """获取视频时长(秒)"""
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
    """
    如果视频大于 50MB，将其切割为多个小片段。
    返回文件路径列表。
    """
    file_size = os.path.getsize(file_path)
    limit_bytes = 49 * 1024 * 1024  # 限制为 49MB (留点余量)
    
    # 如果文件小于 49MB，直接返回原文件
    if file_size <= limit_bytes:
        return [file_path]

    print(f"文件大小 {file_size/1024/1024:.2f}MB，正在进行切割...")
    
    duration = get_video_info(file_path)
    if not duration:
        return [file_path] # 获取时长失败，尝试原样发送

    # 计算预期的片段数量
    # 假设比特率是均匀的（虽然不一定，但为了速度我们使用估算）
    num_parts = math.ceil(file_size / limit_bytes)
    
    # 计算每段的大致时长 (总时长 / 片段数)
    segment_time = int(duration / num_parts)
    
    # 防止切得太细，最少 10 秒
    if segment_time < 10: 
        segment_time = 10

    output_pattern = f"{os.path.dirname(file_path)}/{file_prefix}_part%03d.mp4"
    
    # 使用 FFmpeg 进行切割
    # -c copy: 直接流复制，不重新编码 (速度极快，画质无损)
    # -segment_time: 每段时长
    # -reset_timestamps 1: 重置时间戳，保证每一段都能独立播放
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
        
        # 查找生成的分段文件
        parts = sorted(glob.glob(f"{os.path.dirname(file_path)}/{file_prefix}_part*.mp4"))
        
        # 切割成功后，删除原大文件
        if parts:
            os.remove(file_path)
            return parts
        else:
            return [file_path] # 切割失败返回原文件
            
    except subprocess.CalledProcessError:
        return [file_path] # 出错返回原文件

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("你好！请直接发送包含视频/图片的 URL 给我就行。支持 Youtube, B站, 抖音, 小红书, X, Ins 等。")

def progress_hook(d):
    """(可选) 这里可以用来打印下载进度"""
    if d['status'] == 'finished':
        print('下载完成，正在处理...')

async def process_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    user_id = update.effective_user.id
    message_id = update.message.message_id
    
    if not url.startswith(("http://", "https://")):
        return

    status_msg = await update.message.reply_text("🔍 正在解析并下载，请稍候...")

    file_prefix = f"{user_id}_{message_id}"
    # 注意：这里我们强制后缀为 mp4，方便 ffmpeg 处理
    output_template = f"{DOWNLOAD_DIR}/{file_prefix}_raw.%(ext)s"

    ydl_opts = {
        'outtmpl': output_template,
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'merge_output_format': 'mp4',
        'noplaylist': True,
        'quiet': True,
        # 'max_filesize': 不要限制下载大小，我们要下载下来自己切
    }

    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, download_media, ydl_opts, url)

        # 查找下载的原始文件
        # 注意：yt-dlp 可能会把 ext 变成 mkv 等，所以我们要模糊匹配 _raw.*
        downloaded_raw_files = glob.glob(f"{DOWNLOAD_DIR}/{file_prefix}_raw.*")

        if not downloaded_raw_files:
            await status_msg.edit_text("❌ 下载失败：未找到文件。")
            return
        
        raw_file_path = downloaded_raw_files[0]
        
        # --- 核心修改：调用切割逻辑 ---
        # 如果是图片，split_video 会直接返回列表；如果是视频且过大，会切割
        if raw_file_path.endswith(('.jpg', '.jpeg', '.png', '.webp')):
            final_files = [raw_file_path]
        else:
            # 在线程池中运行切割，防止卡住 Bot
            await status_msg.edit_text("✂️ 文件较大，正在切割中...")
            final_files = await loop.run_in_executor(None, split_video, raw_file_path, file_prefix)

        # --- 循环发送所有文件 ---
        total_parts = len(final_files)
        for index, file_path in enumerate(final_files):
            file_size = os.path.getsize(file_path)
            
            # 进度提示
            if total_parts > 1:
                await status_msg.edit_text(f"⬆️ 正在上传第 {index+1}/{total_parts} 部分...")
            else:
                await status_msg.edit_text(f"⬆️ 正在上传...")

            # 最后的防线：如果切完还大于 50MB (极少见)，只能提示
            if file_size > 49.5 * 1024 * 1024:
                await update.message.reply_text(f"⚠️ 第 {index+1} 部分仍然过大 ({file_size/1024/1024:.1f}MB)，无法发送。")
            else:
                with open(file_path, 'rb') as f:
                    if file_path.endswith(('.jpg', '.jpeg', '.png', '.webp')):
                        await update.message.reply_photo(photo=f)
                    else:
                        await update.message.reply_video(video=f, caption=f"Part {index+1}/{total_parts}" if total_parts > 1 else "")
            
            # 发送完立即删除分片
            os.remove(file_path)

        await status_msg.delete()

    except Exception as e:
        await status_msg.edit_text(f"❌ 发生错误: {str(e)[:100]}")
        # 清理残留
        for f in glob.glob(f"{DOWNLOAD_DIR}/{file_prefix}_*"):
            try: os.remove(f)
            except: pass

def download_media(opts, url):
    """同步的 yt-dlp 下载函数，将被放入 executor 运行"""
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

if __name__ == '__main__':
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    start_handler = CommandHandler('start', start)
    # 过滤掉命令，只处理纯文本
    msg_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), process_url)

    application.add_handler(start_handler)
    application.add_handler(msg_handler)

    print("Bot 正在运行...")
    application.run_polling()

