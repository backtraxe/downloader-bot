# src/handlers.py
import os
import glob
import asyncio
import logging # 导入 logging
from telegram import Update, InputMediaPhoto
from telegram.ext import ContextTypes

from config import DOWNLOAD_DIR
from src.utils import SmartProgress
from src.downloader import download_with_ytdlp
from src.scraper import scrape_generic_images
from src.processor import split_video

# 获取 logger
logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 机器人就绪！(日志已接管)")

async def process_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not url.startswith(("http://", "https://")): return
    
    user_id = update.effective_user.id
    msg_id = update.message.message_id
    file_prefix = f"{user_id}_{msg_id}"
    
    logger.info(f"收到用户 {user_id} 的链接: {url}")
    
    status_msg = await update.message.reply_text("🔍 正在分析链接...")
    loop = asyncio.get_running_loop()
    progress = SmartProgress(status_msg, loop)
    final_files = []

    # 1. 尝试 yt-dlp
    ytdlp_template = f"{DOWNLOAD_DIR}/{file_prefix}_%(autonumber)03d.%(ext)s"
    try:
        await loop.run_in_executor(None, download_with_ytdlp, url, ytdlp_template, progress.hook)
        found = sorted(glob.glob(f"{DOWNLOAD_DIR}/{file_prefix}_*"))
        if found:
            final_files = found
            logger.info(f"yt-dlp 下载成功，文件数: {len(found)}")
    except Exception as e:
        logger.debug(f"yt-dlp 尝试未果 (这是正常的，如果是普通网页): {e}")

    # 2. 尝试爬虫
    if not final_files:
        await status_msg.edit_text("📖 识别为普通网页，正在抓取...")
        logger.info("切换至网页爬虫模式")
        
        def scrape_progress(curr, total):
            progress.update_batch(curr, total, desc="抓取图片")
            
        web_files = await loop.run_in_executor(
            None, 
            scrape_generic_images, 
            url, 
            file_prefix, 
            scrape_progress
        )
        final_files = sorted(web_files)

    # 3. 结果处理
    if not final_files:
        logger.warning(f"链接 {url} 未提取到任何内容")
        await status_msg.edit_text("❌ 无法提取有效内容。")
        return

    # 4. 发送流程
    count = len(final_files)
    logger.info(f"准备发送 {count} 个文件")
    await status_msg.edit_text(f"✅ 下载完成，准备发送 {count} 个文件...")

    images_to_send = []
    videos_to_send = []

    for path in final_files:
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            logger.warning(f"发现空文件或文件缺失: {path}")
            continue

        if path.endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp')):
            images_to_send.append(path)
        else:
            parts = await loop.run_in_executor(None, split_video, path, file_prefix)
            videos_to_send.extend(parts)

    # --- 图片聚合发送 ---
    CHUNK_SIZE = 10
    for i in range(0, len(images_to_send), CHUNK_SIZE):
        chunk = images_to_send[i : i + CHUNK_SIZE]
        try:
            media_group = []
            opened_files = []
            for img_path in chunk:
                f = open(img_path, 'rb')
                opened_files.append(f)
                media_group.append(InputMediaPhoto(f))
            
            if media_group:
                await update.message.reply_media_group(media=media_group, read_timeout=60)
                logger.info(f"成功发送图片组 (索引 {i}-{i+len(chunk)})")
            
            for f in opened_files: f.close()
            
        except Exception as e:
            logger.warning(f"相册发送失败: {e}，尝试降级为单张发送")
            for f in opened_files: f.close()
            
            # 降级重试
            for img_path in chunk:
                try:
                    with open(img_path, 'rb') as f:
                        await update.message.reply_photo(f)
                        await asyncio.sleep(0.5)
                except Exception as single_e:
                    logger.error(f"单张图片发送失败 {os.path.basename(img_path)}: {single_e}")

        finally:
            for img_path in chunk:
                if os.path.exists(img_path):
                    try: os.remove(img_path)
                    except: pass

    # --- 视频发送 ---
    for vid_path in videos_to_send:
        try:
            with open(vid_path, 'rb') as f:
                await update.message.reply_video(video=f, read_timeout=120)
                logger.info(f"视频发送成功: {os.path.basename(vid_path)}")
        except Exception as e:
            logger.error(f"视频发送失败 {vid_path}: {e}")
        finally:
            if os.path.exists(vid_path): os.remove(vid_path)

    await status_msg.delete()
    logger.info("任务处理完成")
