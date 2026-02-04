import logging
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from config import BOT_TOKEN
from src.handlers import start, process_url

# 日志配置
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

if __name__ == '__main__':
    # 构建 Bot
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # 注册 Handler (从 handlers.py 导入)
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), process_url))

    print("🚀 Bot 模块化版本正在运行...")
    application.run_polling()
