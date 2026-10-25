# -*- coding: utf-8 -*-
"""Telegram 下载机器人（长轮询常驻，Termux 后台跑）。

完整闭环：聊天里发链接 → 调 core.downloader.dispatch_url 下载 → 把产物
（图片相册 / 视频 / 原文件）回传到同一聊天，caption 带 `#作者` `#站点`
标签——Telegram 原生可点击、可搜索，方便日后按作者翻记录。

设计要点：
- 下载核心零改动：靠下载前后 `download/` 快照比对拿产物，从
  `download/<站点>[/<作者>]/<标题>/` 路径结构反推元数据打标签
- 长轮询走 http_client（Termux 系统 curl 降级同样可用），零新增依赖
- 白名单（config.json 的 telegram.allowed_users）：未授权用户只收到含其
  用户 id 的提示，不消耗下载
- 串行处理：一次一条链接，多余消息在 Telegram 服务端排队

配置（config.json 的 telegram 段，已 gitignore，首跑 telegram/upload.py / 本脚本生成模板）：
  bot_token     @BotFather 给的 token
  chat_id       tg_upload 直发模式默认聊天；bot 模式按消息来源回复
  allowed_users 允许使用的用户 id 列表

运行（建议 tmux 常驻）：
    python -m telegram.bot
"""

import logging
import os
import re
import sys
import threading
import time

from core import http_client
from core.downloader import dispatch_url
from core.result import DownloadStatus
from .upload import TgError, call_api, load_config, upload
from core.utils import extract_urls, is_partial_download, setup_logging

logger = logging.getLogger("downloader")

_API_BASE = "https://api.telegram.org"
DOWNLOAD_ROOT = "download"
# 过程提示消息自动删除的默认 TTL（config.json telegram 段缺失该键时按此值）
PROCESS_MESSAGE_TTL_DEFAULT = 300


# ---------------- 标签与元数据（纯函数） ----------------


def make_tag(text):
    """把任意文本压成单个合法 hashtag 词（#作者）：空白/标点归并为下划线。

    Telegram hashtag 以空格和大部分标点断词；合法可点击即满足搜索。
    空结果返回 ""。
    """
    t = re.sub(r"[^0-9A-Za-z_一-鿿]+", "_", (text or "")).strip("_")
    return f"#{t}" if t else ""


def build_caption(site, author, title):
    """相册/视频 caption：`#作者 #站点` 打头 + 标题行。"""
    tags = " ".join(t for t in (make_tag(author), make_tag(site)) if t)
    head = f"{tags}\n" if tags else ""
    return f"{head}{title}" if title else head.rstrip()


def snapshot_tree(root=DOWNLOAD_ROOT):
    """{相对路径: mtime}——下载前后比对拿本批产物。

    跳过隐藏文件（.skip 标记等）与下载中断的 .part/.ytdl 残留——
    残留不算新产物，否则半截文件会被当媒体回传。
    """
    snap = {}
    if not os.path.isdir(root):
        return snap
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.startswith(".") or is_partial_download(name):
                continue
            p = os.path.join(dirpath, name)
            try:
                snap[os.path.relpath(p, root)] = os.path.getmtime(p)
            except OSError:
                continue
    return snap


def group_new_files(before, root=DOWNLOAD_ROOT):
    """返回按目录分组的新增/变更文件：{"相对目录": [相对路径…]}（保序）。"""
    after = snapshot_tree(root)
    groups = {}
    for rel, mtime in sorted(after.items()):
        if rel in before and before[rel] >= mtime:
            continue
        groups.setdefault(os.path.dirname(rel), []).append(rel)
    return groups


def _looks_generated(name):
    """产物目录内的文件按 001.jpg / video.mp4 序号命名；散文件按标题命名。"""
    stem = name.split(".", 1)[0]
    return stem == "video" or (len(stem) == 3 and stem.isdigit())


def _is_post_dir_group(group_key, rel_files):
    """判断 group_key 是帖子目录还是散文件的父目录。

    目录形态：download/<site>[/<作者>]/<标题>/<001.jpg…>
    散文件：  download/<site>[/<作者>]/<标题>.<ext>
    深度 >=3 必为帖子目录；<=1 必为散文件；深度 2 靠文件名区分。
    """
    parts = group_key.split(os.sep) if group_key else []
    if len(parts) >= 3:
        return True
    if len(parts) == 2:
        if not rel_files:
            # 没有文件信息时沿用旧口径：目录产物更常见
            return True
        return all(_looks_generated(os.path.basename(p)) for p in rel_files)
    return False


def parse_post_meta(group_key, rel_files):
    """从产物位置反推 (site, author, title)。

    目录形态：download/<site>[/<作者>]/<标题>/
    散文件：  download/<site>[/<作者>]/<标题>.<ext>
    group_new_files 对散文件返回的 group_key 是父目录（<site> 或
    <site>/<作者>），必须与帖子目录区分，否则作者层会被误当成标题。
    """
    parts = group_key.split(os.sep) if group_key else []
    if _is_post_dir_group(group_key, rel_files):
        site = parts[0]
        if len(parts) >= 3:
            return site, parts[1], parts[2]
        return site, "", parts[-1]
    # 散文件：group_key 为空时从文件路径的父目录推导
    if not parts and rel_files:
        parts = os.path.dirname(rel_files[0]).split(os.sep)
    site = parts[0] if parts else ""
    author = parts[1] if len(parts) >= 2 else ""
    stem = os.path.splitext(os.path.basename(rel_files[0]))[0] if rel_files else ""
    return site, author, stem


# ---------------- 消息处理 ----------------


def _reply(session, token, chat_id, text, ttl=0, timer_cls=threading.Timer):
    """发文字提示消息；ttl>0 时对其 message_id 挂自动删除。

    只覆盖 say() 链路的文字提示（开始下载/无链接/已下载过/失败提示等）；
    媒体消息（相册/视频/document）不走这里，永不自动删除。
    响应拿不到 message_id 时跳过调度，不报错。
    """
    result = call_api(
        session, {"bot_token": token}, "sendMessage", {"chat_id": chat_id, "text": text}
    )
    message_id = (result or {}).get("message_id") if isinstance(result, dict) else None
    if ttl > 0 and message_id is not None:
        _schedule_delete(session, token, chat_id, message_id, ttl, timer_cls)


def _schedule_delete(
    session, token, chat_id, message_id, ttl, timer_cls=threading.Timer
):
    """ttl 秒后删除该消息。

    Timer 设为 daemon，不阻碍进程退出；bot 重启时未到期的删除计划随之
    丢失（预期语义——重启本来就是清状态，消息最多多留一会）。
    """
    t = timer_cls(ttl, _delete_message, args=(session, token, chat_id, message_id))
    t.daemon = True
    t.start()


def _delete_message(session, token, chat_id, message_id):
    """调 deleteMessage；失败只记日志（频道里 bot 可能无删除消息管理员权限）。"""
    try:
        call_api(
            session,
            {"bot_token": token},
            "deleteMessage",
            {"chat_id": chat_id, "message_id": message_id},
        )
    except Exception as e:
        logger.warning("删除过程消息失败: %s", e)


def is_allowed(cfg, user_id):
    """白名单判定；allowed_users 为空时拒绝所有并提示（防陌生人白嫖）。"""
    allowed = cfg.get("allowed_users") or []
    return user_id in allowed


def handle_message(
    session,
    cfg,
    message,
    dispatch=dispatch_url,
    download_root=DOWNLOAD_ROOT,
    timer_cls=threading.Timer,
):
    """处理一条消息；返回回复文本列表（真实发送经 _reply，测试中读返回）。"""
    token = cfg["bot_token"]
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    user_id = (message.get("from") or {}).get("id")
    # 过程提示消息 TTL：0 = 关闭自动删除；配置缺键时按默认 300 秒
    ttl = cfg.get("process_message_ttl_seconds")
    if ttl is None:
        ttl = PROCESS_MESSAGE_TTL_DEFAULT
    replies = []

    def say(text):
        replies.append(text)
        _reply(session, token, chat_id, text, ttl=ttl, timer_cls=timer_cls)

    text = (message.get("text") or "").strip()
    if not text:
        return replies
    if text.startswith("/start") or text.startswith("/help"):
        say(
            "发链接即可：小红书 / 抖音图文或视频 / bilibili / youtube / "
            "instagram / x / 任意网页。产物会以相册或视频回传，"
            "caption 带 #作者 标签方便搜索。"
        )
        return replies
    # 频道消息不做用户白名单：频道里只有管理员能发帖（匿名管理员无
    # from，拿不到 user_id，白名单无从匹配——实测被拒且回显 None）
    if chat.get("type") != "channel" and not is_allowed(cfg, user_id):
        say(f"未授权用户。把你的用户 id 发给维护者加入白名单：{user_id}")
        logger.warning("拒绝未授权用户: %s", user_id)
        return replies

    urls = extract_urls(text)
    if not urls:
        say("没识别到链接，直接发链接（可带分享文案）即可。")
        return replies

    for url in urls:
        say(f"开始下载：{url}")
        before = snapshot_tree(download_root)
        try:
            result = dispatch(url)
        except Exception as e:  # 下载器内部多已容错，这里兜底
            logger.exception("下载失败: %s", url)
            say(f"下载失败：{e}")
            continue
        status = getattr(result, "status", None)
        detail = getattr(result, "message", "") or ""
        groups = group_new_files(before, download_root)
        if status == DownloadStatus.FAILED and not groups:
            say(f"下载失败：{detail or '未知原因'}")
            continue
        if status == DownloadStatus.NO_MEDIA and not groups:
            say(
                f"没有找到可下载的媒体：{detail}"
                if detail
                else "没有找到可下载的媒体。"
            )
            continue
        if not groups:
            if status == DownloadStatus.SKIPPED:
                say("没有新产物——该内容此前已下载过。")
            elif status in (DownloadStatus.DOWNLOADED, DownloadStatus.PARTIAL):
                say("没有新产物——可能此前已下载过，或产物被过滤。")
            else:
                # 兼容旧的 dispatch 桩/第三方后端：保持原提示
                say("没有新产物——该内容此前已下载过。")
            continue
        sent_total, skipped_total = 0, 0
        for group_key, rel_files in groups.items():
            if group_key and _is_post_dir_group(group_key, rel_files):
                targets = [
                    (
                        os.path.join(download_root, group_key),
                        parse_post_meta(group_key, rel_files),
                    )
                ]
            else:
                # 散文件：逐个上传本批新文件，避免把同目录里的旧文件重发
                targets = [
                    (os.path.join(download_root, rel), parse_post_meta("", [rel]))
                    for rel in rel_files
                ]
            for target, (site, author, title) in targets:
                caption = build_caption(site, author, title)
                try:
                    sent, skipped = upload(
                        session, cfg, target, caption=caption, chat_id=chat_id
                    )
                except TgError as e:
                    say(f"上传 Telegram 失败：{e}")
                    continue
                except Exception as e:  # noqa: BLE001 读盘/预处理等意外
                    # 不让单个目标吞掉整条消息的后续回复（完成提示等）
                    logger.exception("上传失败: %s", target)
                    say(f"上传失败：{e}")
                    continue
                sent_total += sent
                skipped_total += len(skipped)
                for path in skipped:
                    say(f"超 50MB 已跳过（文件在 {path}）")
        completion = (
            f"完成：{sent_total} 项已回传。"
            if not skipped_total
            else f"完成：{sent_total} 项已回传，{skipped_total} 项超限跳过。"
        )
        if status in (DownloadStatus.PARTIAL, DownloadStatus.FAILED) and detail:
            completion = completion.rstrip("。") + f"（{detail}）。"
        say(completion)
    return replies


# ---------------- 长轮询 ----------------


def get_updates(session, token, offset, timeout=25, long_timeout=40):
    """long poll getUpdates；网络异常向上抛，由主循环退避。"""
    url = f"{_API_BASE}/bot{token}/getUpdates?offset={offset}&timeout={timeout}"
    r = session.get(url, timeout=long_timeout)
    payload = r.json()
    if not payload.get("ok"):
        raise TgError(f"getUpdates 失败: {payload.get('description')}")
    return payload.get("result") or []


def initial_offset(session, token):
    """启动时跳过离线期积压的消息（防重启后把历史链接重下一遍）。"""
    updates = get_updates(session, token, offset=0, timeout=0, long_timeout=15)
    if not updates:
        return 0
    return max(u["update_id"] for u in updates) + 1


def main():
    setup_logging()
    cfg = load_config()
    if cfg is None:
        print(
            "请先在 config.json 的 telegram 段填入 bot_token / chat_id /"
            " allowed_users（@BotFather 拿 token；用户 id 会在首次私聊被拒时回显）",
            file=sys.stderr,
        )
        return 1

    session = http_client.Session()
    try:
        offset = initial_offset(session, cfg["bot_token"])
        logger.info(
            "tg_bot 已启动（白名单 %s），等待消息……",
            cfg.get("allowed_users") or "空：全部拒绝",
        )
        while True:
            try:
                updates = get_updates(session, cfg["bot_token"], offset)
            except Exception as e:
                logger.warning("getUpdates 异常，5s 后重试: %s", e)
                time.sleep(5)
                continue
            for upd in updates:
                offset = upd["update_id"] + 1
                message = upd.get("message") or upd.get("channel_post")
                if message:
                    try:
                        handle_message(session, cfg, message)
                    except Exception:
                        logger.exception("处理消息失败: %s", str(message)[:200])
    except KeyboardInterrupt:
        logger.info("已停止")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
