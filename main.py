import os
import logging
import asyncio
import glob
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
    
    # 简单的 URL 校验
    if not url.startswith(("http://", "https://")):
        return # 忽略非链接消息

    status_msg = await update.message.reply_text("🔍 正在解析并下载，请稍候...")

    # 为每个请求创建一个唯一的文件前缀，防止冲突
    file_prefix = f"{user_id}_{message_id}"
    output_template = f"{DOWNLOAD_DIR}/{file_prefix}_%(title)s.%(ext)s"

    ydl_opts = {
        'outtmpl': output_template,
        'format': 'bestvideo+bestaudio/best', # 下载最佳画质
        'merge_output_format': 'mp4',         # 尽量合并为 mp4
        'noplaylist': True,                   # 不下载整个列表
        'quiet': True,                        # 减少日志输出
        'no_warnings': True,
        'progress_hooks': [progress_hook],
        # 限制文件大小 (Telegram 普通 Bot 上限 50MB，此处设大一点尝试发送，失败则提示)
        # 'max_filesize': 50 * 1024 * 1024, 
    }

    # 如果有 cookies.txt，则加载
    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE

    try:
        # 在单独的线程中运行下载，避免阻塞 asyncio 事件循环
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, download_media, ydl_opts, url)

        # 查找下载的文件 (因为 yt-dlp 可能会自动修改扩展名)
        downloaded_files = glob.glob(f"{DOWNLOAD_DIR}/{file_prefix}_*")

        if not downloaded_files:
            await status_msg.edit_text("❌ 下载失败：未找到文件。可能是该平台不支持或需要 Cookie。")
            return

        # 发送文件
        for file_path in downloaded_files:
            file_size = os.path.getsize(file_path)
            # Telegram Bot API 限制普通发送为 50MB
            if file_size > 49 * 1024 * 1024:
                await status_msg.reply_text(f"⚠️ 文件过大 ({file_size/1024/1024:.2f} MB)，无法通过 Bot 直接发送。建议在本地使用工具下载。")
            else:
                await status_msg.edit_text(f"⬆️ 正在上传...")
                if file_path.endswith(('.jpg', '.jpeg', '.png', '.webp')):
                    await update.message.reply_photo(photo=open(file_path, 'rb'))
                else:
                    await update.message.reply_video(video=open(file_path, 'rb'))
            
            # 清理文件
            os.remove(file_path)

        await status_msg.delete()

    except Exception as e:
        error_text = str(e)
        # 简化错误信息
        if "Sign in to confirm your age" in error_text:
            msg = "❌ 下载失败：需登录 (年龄限制/会员内容)。请配置 cookies.txt。"
        else:
            msg = f"❌ 发生错误: {error_text[:100]}..."
        await status_msg.edit_text(msg)
        # 清理可能残留的文件
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

