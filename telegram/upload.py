# -*- coding: utf-8 -*-
"""把 download/ 产物上传到 Telegram（官方 Bot API，无风控对尔）。

一次配置终身有效（没有 Cookie 过期问题）：
  1. Telegram 里找 @BotFather → /newbot → 拿到 bot token
  2. 给 bot 发任意一条消息，浏览器打开
     https://api.telegram.org/bot<token>/getUpdates 取 chat.id
     （发频道/群：把 bot 加进去，chat_id 用 @频道名 或群组 id）
  3. 填入 telegram.json（首跑自动生成模板与上述指引，已 gitignore）

用法：
    python tg_upload.py <文件或目录>            # 目录：图片按 10 张/组发相册
    python tg_upload.py <目标> --document      # 全部按"文件"发送（原图不压缩）
    python tg_upload.py <目标> --caption 标题

限制：Bot API 上传单文件 ≤50MB；图片 >10MB 自动改按文件发送（Telegram
photo 上限）；超限走自建 telegram-bot-api（2GB）不在本脚本范围。
"""
import argparse
import json
import logging
import os
import sys
import time

from core import http_client
from core.utils import setup_logging

logger = logging.getLogger("downloader")

CONFIG_PATH = "telegram.json"
_API_BASE = "https://api.telegram.org"

# Bot API 上传上限（自建 bot-api server 才是 2GB，官方是 50MB）
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
# Telegram photo 上限 10MB，超过自动按 document 发，否则服务端拒收
MAX_PHOTO_BYTES = 10 * 1024 * 1024
ALBUM_SIZE = 10  # sendMediaGroup 每组上限

_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp"}  # heic 只按 document 发
_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}

_CONFIG_TEMPLATE = {
    "bot_token": "在此填入 @BotFather 给的 token",
    "chat_id": 0,
    "allowed_users": [],  # telegram/bot.py 白名单：允许使用的用户 id 列表
}


class TgError(RuntimeError):
    """Bot API 返回 ok:false 或多次重试仍失败。"""


# ---------------- 配置与媒体规划（纯函数） ----------------

def load_config(path=CONFIG_PATH):
    """读 telegram.json；缺失时写模板并返回 None（调用方打印指引退出）。"""
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_CONFIG_TEMPLATE, f, ensure_ascii=False, indent=2)
        return None
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    if not cfg.get("bot_token") or str(cfg.get("bot_token")).startswith("在此"):
        return None
    return cfg


def collect_media(target):
    """文件或目录 → 排序后的媒体文件列表（只扫一层，忽略隐藏文件）。"""
    target = os.fspath(target)
    if os.path.isfile(target):
        return [target]
    names = sorted(n for n in os.listdir(target) if not n.startswith("."))
    return [os.path.join(target, n) for n in names
            if os.path.isfile(os.path.join(target, n))]


def plan_uploads(files, document=False, size_of=os.path.getsize):
    """把文件列表规划成操作序列，保序且把连续图片合成相册。

    返回 [("album",[paths]) / ("video",path) / ("document",path) /
    ("too_big",path)]；document=True 时全部按文件发送（原图不压缩）。
    """
    ops, album = [], []

    def flush():
        if album:
            ops.append(("album", list(album)))
            album.clear()

    for path in files:
        try:
            size = size_of(path)
        except OSError:
            size = 0
        ext = os.path.splitext(path)[1].lower()
        if size > MAX_UPLOAD_BYTES:
            flush()
            ops.append(("too_big", path))
        elif not document and ext in _PHOTO_EXTS and size <= MAX_PHOTO_BYTES:
            album.append(path)
            if len(album) >= ALBUM_SIZE:
                flush()
        else:
            flush()
            if not document and ext in _VIDEO_EXTS:
                ops.append(("video", path))
            else:
                ops.append(("document", path))
    flush()
    return ops


# ---------------- Bot API 调用 ----------------

def call_api(session, cfg, method, data, files=None, retries=3,
             _sleep=time.sleep):
    """调 Bot API，返回 result；429 按 retry_after 等待，5xx/网络异常退避。"""
    url = f"{_API_BASE}/bot{cfg['bot_token']}/{method}"
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            r = session.post_files(url, data=data, files=files, timeout=120)
            payload = r.json()
        except Exception as e:  # 网络层错误/非 JSON
            last_err = e
            if attempt < retries:
                _sleep(attempt * 3)
                continue
            raise TgError(f"{method} 请求失败: {e}") from e
        if payload.get("ok"):
            return payload.get("result")
        code = payload.get("error_code")
        if code == 429 and attempt < retries:
            wait = ((payload.get("parameters") or {}).get("retry_after") or 3)
            logger.warning("Telegram 限流，%ss 后重试", wait)
            _sleep(wait)
            continue
        raise TgError(f"{method} 失败: {code} {payload.get('description')}")
    raise TgError(f"{method} 多次重试仍失败: {last_err}")


def _caption_for(path, caption):
    return caption if caption is not None else os.path.basename(path)


def upload(session, cfg, target, document=False, caption=None, chat_id=None):
    """执行上传，返回 (成功项数, [跳过的超限文件])。

    chat_id 缺省用配置里的；telegram/bot.py 按消息来源聊天传入。"""
    files = collect_media(target)
    if not files:
        raise TgError(f"目标里没有文件：{target}")
    ops = plan_uploads(files, document=document)
    sent, skipped = 0, []
    if chat_id is None:
        chat_id = cfg["chat_id"]
    for kind, payload in ops:
        if kind == "too_big":
            skipped.append(payload)
            logger.warning("超过 Bot API 50MB 上限，跳过：%s", payload)
            continue
        if kind == "album":
            if len(payload) == 1:
                call_api(session, cfg, "sendPhoto",
                         {"chat_id": chat_id,
                          "caption": _caption_for(payload[0], caption)},
                         {"photo": payload[0]})
            else:
                media, files_map = [], {}
                for i, path in enumerate(payload):
                    attach = f"file{i}"
                    item = {"type": "photo", "media": f"attach://{attach}"}
                    if i == 0 and caption:
                        item["caption"] = caption
                    media.append(item)
                    files_map[attach] = path
                call_api(session, cfg, "sendMediaGroup",
                         {"chat_id": chat_id, "media": json.dumps(
                             media, ensure_ascii=False)}, files_map)
            logger.info("相册 %d 张已发送：%s…", len(payload),
                        os.path.basename(payload[0]))
            sent += len(payload)
        else:
            method = "sendVideo" if kind == "video" else "sendDocument"
            call_api(session, cfg, method,
                     {"chat_id": chat_id,
                      "caption": _caption_for(payload, caption)},
                     {"video" if kind == "video" else "document": payload})
            logger.info("已发送（%s）：%s", kind, os.path.basename(payload))
            sent += 1
    return sent, skipped


def main():
    parser = argparse.ArgumentParser(
        description="把 download/ 产物上传到 Telegram（Bot API）")
    parser.add_argument("target", help="文件或目录")
    parser.add_argument("--document", action="store_true",
                        help="全部按文件发送（原图不被压缩）")
    parser.add_argument("--caption", default=None, help="说明文字")
    parser.add_argument("--config", default=CONFIG_PATH, help="配置文件路径")
    args = parser.parse_args()

    setup_logging()
    cfg = load_config(args.config)
    if cfg is None:
        print(f"请先在 {args.config} 填入 bot_token 与 chat_id：\n"
              "  1. 找 @BotFather /newbot 拿 token\n"
              "  2. 给 bot 发一条消息，打开 "
              "https://api.telegram.org/bot<token>/getUpdates 取 chat.id\n"
              "  （模板已自动生成）", file=sys.stderr)
        return 1
    session = http_client.Session()
    try:
        sent, skipped = upload(session, cfg, args.target,
                               document=args.document, caption=args.caption)
    except TgError as e:
        print(str(e), file=sys.stderr)
        return 1
    finally:
        session.close()
    msg = f"完成：{sent} 项已上传"
    if skipped:
        msg += f"，{len(skipped)} 项超 50MB 被跳过"
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
