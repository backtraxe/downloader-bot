import logging
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from config import BOT_TOKEN
from src.handlers import start, process_url

# --- 全局日志配置 ---
# 这一步非常重要，它决定了整个项目的日志长什么样
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO, # 如果想看更详细的调试信息，可以改为 logging.DEBUG
    handlers=[
        logging.StreamHandler(), # 输出到控制台
        # logging.FileHandler("bot.log", encoding='utf-8') # 如果想保存到文件，把这行注释解开
    ]
)

# 获取 logger 实例
logger = logging.getLogger(__name__)

if __name__ == '__main__':
    if not BOT_TOKEN:
        logger.critical("未找到 BOT_TOKEN，程序无法启动！")
        exit(1)

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), process_url))

    logger.info("🚀 Bot 服务已启动 (全日志模式)...")
    application.run_polling()
