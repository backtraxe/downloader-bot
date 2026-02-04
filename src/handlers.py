import os
import glob
import asyncio
from telegram import Update
from telegram.ext import ContextTypes

# 导入我们的自定义模块
from config import DOWNLOAD_DIR
from src.utils import SmartProgress
from src.downloader import download_with_ytdlp
from src.scraper import scrape_generic_images
from src.processor import split_video

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 机器人就绪！\n发送链接即可（支持 1024/小红书/视频/网页）。")

async def process_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not url.startswith(("http://", "https://")): return
    
    user_id = update.effective_user.id
    msg_id = update.message.message_id
    file_prefix = f"{user_id}_{msg_id}"
    
    # 初始状态消息
    status_msg = await update.message.reply_text("🔍 正在分析链接...")
    
    loop = asyncio.get_running_loop()
    progress = SmartProgress(status_msg, loop)
    final_files = []

    # 1. 尝试 yt-dlp (优先视频/社交媒体)
    ytdlp_template = f"{DOWNLOAD_DIR}/{file_prefix}_%(autonumber)03d.%(ext)s"
    
    try:
        # 在线程池运行
        # print("正在尝试 yt-dlp...")
        await loop.run_in_executor(None, download_with_ytdlp, url, ytdlp_template, progress.hook)
        
        # 检查下载结果
        found = sorted(glob.glob(f"{DOWNLOAD_DIR}/{file_prefix}_*"))
        if found:
            final_files = found
    except Exception:
        # 这里的报错是预期的，因为 yt-dlp 不支持普通论坛
        # 我们直接忽略错误，进入下一步
        pass

    # 2. 如果 yt-dlp 没拿到文件，或者它报错了，则进入【网页爬虫模式】
    if not final_files:
        await status_msg.edit_text("📖 识别为普通网页/论坛，正在抓取图片...")
        
        # 调用我们更新后的 scraper
        web_files = await loop.run_in_executor(None, scrape_generic_images, url, file_prefix)
        final_files = sorted(web_files)

    # 3. 处理结果
    if not final_files:
        await status_msg.edit_text("❌ 无法提取有效内容。\n(可能是需要登录、VPN问题或页面无大图)")
        return

    # 4. 发送逻辑
    count = len(final_files)
    await status_msg.edit_text(f"✅ 提取成功，准备发送 {count} 个文件...")

    # 分组：区分图片和视频
    files_to_send = []
    for path in final_files:
        if not path.endswith(('.jpg', '.png', '.webp', '.gif', '.jpeg', '.bmp')):
             # 视频切割处理
             parts = await loop.run_in_executor(None, split_video, path, file_prefix)
             files_to_send.extend(parts)
        else:
             files_to_send.append(path)

    # 逐个发送
    for idx, path in enumerate(files_to_send):
        try:
            with open(path, 'rb') as f:
                if path.endswith(('.jpg', '.png', '.webp', '.gif', '.jpeg', '.bmp')):
                    # 图片
                    await update.message.reply_photo(f)
                else:
                    # 视频
                    await update.message.reply_video(f, read_timeout=120)
        except Exception as e:
            print(f"发送文件 {path} 失败: {e}")
        finally:
            if os.path.exists(path): 
                os.remove(path) # 清理缓存

    await status_msg.delete()
