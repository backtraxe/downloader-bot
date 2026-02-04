import os
from dotenv import load_dotenv

# 加载 .env
load_dotenv()

# --- 新增：配置代理 ---
# ⚠️ 请根据你的实际情况修改端口号 (Clash通常是7890, v2rayNG是10808)
PROXY_URL = "http://127.0.0.1:10808" 

# 设置环境变量，让所有 Python 库 (requests, yt-dlp) 自动走代理
os.environ["http_proxy"] = PROXY_URL
os.environ["https_proxy"] = PROXY_URL
os.environ["all_proxy"] = PROXY_URL

print(f"🌍 已配置网络代理: {PROXY_URL}")
# ---------------------

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ 错误：未找到 BOT_TOKEN，请检查 .env 文件")

DOWNLOAD_DIR = "./downloads"
COOKIES_FILE = "cookies.txt"

# 模拟浏览器 UA
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'

if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)
