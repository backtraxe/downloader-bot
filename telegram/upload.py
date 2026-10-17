# -*- coding: utf-8 -*-
"""把 download/ 产物上传到 Telegram（官方 Bot API，无风控对尔）。

一次配置终身有效（没有 Cookie 过期问题）：
  1. Telegram 里找 @BotFather → /newbot → 拿到 bot token
  2. 给 bot 发任意一条消息，浏览器打开
     https://api.telegram.org/bot<token>/getUpdates 取 chat.id
     （发频道/群：把 bot 加进去，chat_id 用 @频道名 或群组 id）
  3. 填入 config.json 的 telegram 段（首跑自动生成模板与上述指引，已 gitignore）

用法：
    python -m telegram.upload <文件或目录>            # 目录：图片按 10 张/组发相册
    python -m telegram.upload <目标> --document      # 全部按"文件"发送（原图不压缩）
    python -m telegram.upload <目标> --caption 标题

限制：Bot API 上传单文件 ≤50MB。能预览的尽量预览：HEIC 与超标大图自动
转码/压缩为 ≤10MB JPEG 走相册（HEIC 两路：pillow-heif 优先，缺失时回退
libheif 自带的 heif-convert CLI——Termux `pkg install libheif` 即可获得，
都缺才按文件发送）；>50MB 视频在本机有 ffmpeg 时压到 ~49MB 再发，没有则
维持超限跳过。Telegram 上的是浏览版，原始字节始终在本地 download/。
超限走自建 telegram-bot-api（2GB）不在本脚本范围。
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time

from core import config as core_config
from core import http_client
from core.utils import setup_logging

logger = logging.getLogger("downloader")

_API_BASE = "https://api.telegram.org"

# Bot API 上传上限（自建 bot-api server 才是 2GB，官方是 50MB）
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
# Telegram photo 上限 10MB，超过自动按 document 发，否则服务端拒收
MAX_PHOTO_BYTES = 10 * 1024 * 1024
ALBUM_SIZE = 10  # sendMediaGroup 每组上限

_PHOTO_EXTS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}  # 可预览直发；heic 转码成功后以 JPEG 产物入列
_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}

# 预览图尺寸上限（宽+高像素和），超出先等比缩到线内再压体积
MAX_PHOTO_DIM_SUM = 10000
# 图片压缩链：质量阶梯 × 每轮 0.85 再缩放，任一结果 ≤10MB 即收
_JPEG_QUALITIES = (88, 80, 72)
_SCALE_FACTOR = 0.85
_MAX_COMPRESS_ROUNDS = 8
# 视频压缩目标 ~49MB（给封装开销留 1MB 余量）；音轨固定 96k，从码率预算扣除
_VIDEO_TARGET_BYTES = 49 * 1024 * 1024
_AUDIO_BITRATE = 96_000


class TgError(RuntimeError):
    """Bot API 返回 ok:false 或多次重试仍失败。"""


# ---------------- 配置与媒体规划（纯函数） ----------------


def load_config(path=None, legacy_path=None):
    """读 config.json 的 telegram 段；缺文件/字段未填时写模板并返回 None。

    旧版扁平结构 telegram.json 由 core.config 自动当作 telegram 段兜底读取。
    """
    kwargs = {}
    if path is not None:
        kwargs["path"] = path
    if legacy_path is not None:
        kwargs["legacy_telegram_path"] = legacy_path
    cfg = core_config.load_config(**kwargs)
    if cfg is None:
        return None
    tg = cfg.get("telegram") or {}
    if not tg.get("bot_token") or str(tg.get("bot_token")).startswith("在此"):
        return None
    return tg


def collect_media(target):
    """文件或目录 → 排序后的媒体文件列表（只扫一层，忽略隐藏文件）。"""
    target = os.fspath(target)
    if os.path.isfile(target):
        return [target]
    names = sorted(n for n in os.listdir(target) if not n.startswith("."))
    return [
        os.path.join(target, n)
        for n in names
        if os.path.isfile(os.path.join(target, n))
    ]


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


# ---------------- 预览预处理（插在 collect 与 plan 之间） ----------------


def _try_heic_to_jpeg(path, tmpdir, _run=subprocess.run):
    """HEIC/HEIF → JPEG 预览转码。两条路：

    1. Pillow + pillow-heif（桌面常规，惰性 import）；
    2. libheif 自带的 heif-convert CLI 兜底——Termux 只要
       `pkg install libheif` 即可获得，免去源码编译 pillow-heif；
       pillow-heif 缺失或解码失败都会尝试 CLI。
    两者都不可用/都失败返回 None（调用方维持 document 发送）。
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    out = os.path.join(tmpdir, stem + ".jpg")
    try:
        import pillow_heif
        from PIL import Image
    except ImportError:
        pillow_heif = None

    if pillow_heif is not None:
        try:
            pillow_heif.register_heif_opener()
            with Image.open(path) as img:
                img.convert("RGB").save(out, format="JPEG", quality=95)
            return out
        except Exception:
            logger.warning("HEIC 经 pillow-heif 转码失败，改试 heif-convert: %s", path)

    if shutil.which("heif-convert"):
        try:
            proc = _run(
                ["heif-convert", str(path), out], capture_output=True, timeout=60
            )
        except Exception:
            proc = None
        if (
            proc is not None
            and getattr(proc, "returncode", 1) == 0
            and os.path.exists(out)
            and os.path.getsize(out) > 0
        ):
            return out
        logger.warning("heif-convert 转码失败，按文件原样发送: %s", path)
        return None

    logger.warning(
        "HEIC 预览转码不可用：未装 pillow-heif，也未找到 heif-convert。"
        "Termux 运行 pkg install libheif 即可获得 heif-convert（推荐，免编译）；"
        "完整方案再补 pip install pillow-heif。未安装时 HEIC 按文件原样发送"
    )
    return None


def _transcode_photo_if_needed(path, tmpdir):
    """超标图片压成 ≤10MB JPEG 预览；达标或无法解码 → 原样返回。

    返回 (发送路径, 需清理的临时文件或 None)。压缩链：先等比缩到
    宽+高 ≤ MAX_PHOTO_DIM_SUM，再按质量阶梯逐档试，每轮仍超标就再
    缩放 0.85 倍，上限 _MAX_COMPRESS_ROUNDS 轮；全失败回退原文件。
    """
    try:
        from PIL import Image
    except ImportError:
        return path, None
    try:
        with Image.open(path) as img:
            width, height = img.size
    except Exception:
        return path, None
    size = os.path.getsize(path)
    if size <= MAX_PHOTO_BYTES and width + height <= MAX_PHOTO_DIM_SUM:
        return path, None
    scale = min(1.0, MAX_PHOTO_DIM_SUM / (width + height))
    cur_w = max(1, int(width * scale))
    cur_h = max(1, int(height * scale))
    stem = os.path.splitext(os.path.basename(path))[0]
    out = os.path.join(tmpdir, stem + ".jpg")
    try:
        with Image.open(path) as img:
            rgb = img.convert("RGB")
            for _ in range(_MAX_COMPRESS_ROUNDS):
                if (cur_w, cur_h) != rgb.size:
                    rgb = rgb.resize((cur_w, cur_h), Image.LANCZOS)
                for quality in _JPEG_QUALITIES:
                    rgb.save(out, format="JPEG", quality=quality)
                    if os.path.getsize(out) <= MAX_PHOTO_BYTES:
                        logger.info(
                            "图片压缩预览：%s → %s（%.1fMB → %.1fMB）",
                            path,
                            out,
                            size / 1048576,
                            os.path.getsize(out) / 1048576,
                        )
                        return out, out
                cur_w = max(1, int(cur_w * _SCALE_FACTOR))
                cur_h = max(1, int(cur_h * _SCALE_FACTOR))
    except Exception:
        logger.warning("图片压缩失败，按文件原样发送: %s", path)
    return path, None


def _try_compress_video(path, tmpdir, _run=subprocess.run):
    """>50MB 视频用 ffmpeg 压到 ~49MB；ffmpeg/ffprobe 缺失、时长解析失败
    或产物仍超限 → 返回 None（调用方维持 too_big 语义）。"""
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        return None
    try:
        probe = _run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        duration = float((probe.stdout or "").strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if duration <= 0:
        return None
    bitrate = int((_VIDEO_TARGET_BYTES * 8) / duration) - _AUDIO_BITRATE
    if bitrate <= 0:
        return None
    stem = os.path.splitext(os.path.basename(path))[0]
    out = os.path.join(tmpdir, stem + ".mp4")
    try:
        _run(
            [
                "ffmpeg",
                "-y",
                "-i",
                path,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-b:v",
                str(bitrate),
                "-maxrate",
                str(bitrate),
                "-bufsize",
                str(bitrate * 2),
                "-c:a",
                "aac",
                "-b:a",
                "96k",
                "-movflags",
                "+faststart",
                out,
            ],
            capture_output=True,
            timeout=1800,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if not os.path.isfile(out):
        return None
    if os.path.getsize(out) > MAX_UPLOAD_BYTES:
        return None  # 产物仍超限 → 放弃，维持 too_big
    logger.info(
        "视频压缩预览：%s → %s（%.1fMB → %.1fMB）",
        path,
        out,
        os.path.getsize(path) / 1048576,
        os.path.getsize(out) / 1048576,
    )
    return out


def _prepare_one(path, tmpdir, _run=subprocess.run):
    """单个文件的预览预处理，返回 (发送路径, 需清理的临时文件或 None)。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in _VIDEO_EXTS and os.path.getsize(path) > MAX_UPLOAD_BYTES:
        compressed = _try_compress_video(path, tmpdir, _run=_run)
        if compressed:
            return compressed, compressed
        return path, None
    if ext in (".heic", ".heif"):
        converted = _try_heic_to_jpeg(path, tmpdir, _run=_run)
        if converted is None:
            return path, None
        send, _temp = _transcode_photo_if_needed(converted, tmpdir)
        # 转码与后续压缩的产物都落在 tmpdir 同一 jpg 路径上，统一按 send 清理
        return send, send
    if ext in _PHOTO_EXTS:
        return _transcode_photo_if_needed(path, tmpdir)
    return path, None


def prepare_media(files, tmpdir=None):
    """collect 之后、plan 之前的预览预处理入口。

    返回 (发送路径列表, 需清理的临时文件列表)；tmpdir 可注入便于测试，
    默认 tempfile.mkdtemp(prefix="tg_preview_")。临时文件与目录的清理由
    调用方负责。
    """
    if tmpdir is None:
        tmpdir = tempfile.mkdtemp(prefix="tg_preview_")
    send_paths, temp_paths = [], []
    for path in files:
        send, temp = _prepare_one(path, tmpdir)
        send_paths.append(send)
        if temp:
            temp_paths.append(temp)
    return send_paths, temp_paths


# ---------------- Bot API 调用 ----------------


def call_api(session, cfg, method, data, files=None, retries=3, _sleep=time.sleep):
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
            wait = (payload.get("parameters") or {}).get("retry_after") or 3
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
    temp_paths, tmpdir = [], None
    if document:  # 用户明确要原样 → 跳过预览预处理
        send_files = files
    else:
        tmpdir = tempfile.mkdtemp(prefix="tg_preview_")
        send_files, temp_paths = prepare_media(files, tmpdir=tmpdir)
    ops = plan_uploads(send_files, document=document)
    sent, skipped = 0, []
    if chat_id is None:
        chat_id = cfg["chat_id"]
    try:
        for kind, payload in ops:
            if kind == "too_big":
                skipped.append(payload)
                logger.warning("超过 Bot API 50MB 上限，跳过：%s", payload)
                continue
            if kind == "album":
                if len(payload) == 1:
                    call_api(
                        session,
                        cfg,
                        "sendPhoto",
                        {
                            "chat_id": chat_id,
                            "caption": _caption_for(payload[0], caption),
                        },
                        {"photo": payload[0]},
                    )
                else:
                    media, files_map = [], {}
                    for i, path in enumerate(payload):
                        attach = f"file{i}"
                        item = {"type": "photo", "media": f"attach://{attach}"}
                        if i == 0 and caption:
                            item["caption"] = caption
                        media.append(item)
                        files_map[attach] = path
                    call_api(
                        session,
                        cfg,
                        "sendMediaGroup",
                        {
                            "chat_id": chat_id,
                            "media": json.dumps(media, ensure_ascii=False),
                        },
                        files_map,
                    )
                logger.info(
                    "相册 %d 张已发送：%s…", len(payload), os.path.basename(payload[0])
                )
                sent += len(payload)
            else:
                method = "sendVideo" if kind == "video" else "sendDocument"
                call_api(
                    session,
                    cfg,
                    method,
                    {"chat_id": chat_id, "caption": _caption_for(payload, caption)},
                    {"video" if kind == "video" else "document": payload},
                )
                logger.info("已发送（%s）：%s", kind, os.path.basename(payload))
                sent += 1
    finally:
        for temp in temp_paths:
            try:
                os.unlink(temp)
            except OSError:
                pass
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
    return sent, skipped


def main():
    parser = argparse.ArgumentParser(
        prog="python -m telegram.upload",
        description="把 download/ 产物上传到 Telegram（Bot API）",
    )
    parser.add_argument("target", help="文件或目录")
    parser.add_argument(
        "--document", action="store_true", help="全部按文件发送（原图不被压缩）"
    )
    parser.add_argument("--caption", default=None, help="说明文字")
    parser.add_argument(
        "--config", default=None, help="配置文件路径（默认 config.json）"
    )
    args = parser.parse_args()

    setup_logging()
    cfg = load_config(args.config)
    if cfg is None:
        print(
            f"请先在 {args.config or core_config.CONFIG_PATH} 的 telegram 段填入 bot_token 与 chat_id：\n"
            "  1. 找 @BotFather /newbot 拿 token\n"
            "  2. 给 bot 发一条消息，打开 "
            "https://api.telegram.org/bot<token>/getUpdates 取 chat.id\n"
            "  （模板已自动生成）",
            file=sys.stderr,
        )
        return 1
    session = http_client.Session()
    try:
        sent, skipped = upload(
            session, cfg, args.target, document=args.document, caption=args.caption
        )
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
