import os
import glob
import asyncio
from telegram import Update, InputMediaPhoto
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
        await loop.run_in_executor(None, download_with_ytdlp, url, ytdlp_template, progress.hook)
        
        # 检查下载结果
        found = sorted(glob.glob(f"{DOWNLOAD_DIR}/{file_prefix}_*"))
        if found:
            final_files = found
    except Exception:
        pass

    # 2. 如果 yt-dlp 没拿到文件，进入【网页爬虫模式】
    if not final_files:
        await status_msg.edit_text("📖 识别为普通网页/论坛，正在抓取图片...")
        web_files = await loop.run_in_executor(None, scrape_generic_images, url, file_prefix)
        final_files = sorted(web_files)

    # 3. 处理结果
    if not final_files:
        await status_msg.edit_text("❌ 无法提取有效内容。\n(可能是需要登录、VPN问题或页面无大图)")
        return

    # 4. 准备发送：区分图片和视频
    count = len(final_files)
    await status_msg.edit_text(f"✅ 提取成功，准备发送 {count} 个文件...")

    images_to_send = []
    videos_to_send = []

    # 预处理：分类并切割大视频
    for path in final_files:
        if path.endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp')):
            images_to_send.append(path)
        else:
            # 视频切割操作
            parts = await loop.run_in_executor(None, split_video, path, file_prefix)
            videos_to_send.extend(parts)

    # --- 📸 策略 A: 图片聚合发送 (Album) ---
    # Telegram 限制每组最多 10 张，所以需要切片
    CHUNK_SIZE = 10
    
    # 按照 10 个一组进行循环
    for i in range(0, len(images_to_send), CHUNK_SIZE):
        chunk = images_to_send[i : i + CHUNK_SIZE]
        
        media_group = []
        opened_files = [] # 用于追踪打开的文件句柄，以便稍后关闭

        try:
            # 构建 Media Group
            for img_path in chunk:
                f = open(img_path, 'rb')
                opened_files.append(f)
                # InputMediaPhoto 是 Telegram 用于构建相册的对象
                media_group.append(InputMediaPhoto(f))
            
            # 发送这一组
            if media_group:
                await update.message.reply_media_group(media=media_group)
                
        except Exception as e:
            print(f"发送图片组失败: {e}")
            # 如果聚合发送失败，尝试回退到单张发送（可选，这里为了简单先只打印错误）
        finally:
            # 必须关闭文件，否则删除时会报错
            for f in opened_files:
                f.close()
            # 发送完立即删除这一组的物理文件
            for img_path in chunk:
                if os.path.exists(img_path):
                    os.remove(img_path)

    # --- 🎥 策略 B: 视频单独发送 ---
    # 视频通常较大，聚合发送容易超时，且手机端看视频通常是单发的体验更好
    for idx, vid_path in enumerate(videos_to_send):
        try:
            with open(vid_path, 'rb') as f:
                await update.message.reply_video(
                    video=f, 
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=120
                )
        except Exception as e:
            print(f"发送视频 {vid_path} 失败: {e}")
        finally:
            if os.path.exists(vid_path):
                os.remove(vid_path)

    await status_msg.delete()
