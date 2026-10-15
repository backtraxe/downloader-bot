# -*- coding: utf-8 -*-
"""telegram/bot.py 纯函数与消息流测试（假 dispatch + 假 session，不联网）"""

import json
import os

from telegram.bot import (
    build_caption,
    group_new_files,
    handle_message,
    is_allowed,
    make_tag,
    parse_post_meta,
    snapshot_tree,
)


class _Resp:
    def json(self):
        return {"ok": True, "result": {"message_id": 1}}


class _Session:
    """记录 Bot API 调用：(方法名, data)。"""

    def __init__(self):
        self.sent = []

    def post_files(self, url, data=None, files=None, timeout=None):
        method = url.rsplit("/", 1)[1]
        self.sent.append((method, data or {}))
        return _Resp()

    def get(self, url, **kwargs):
        raise AssertionError("本组测试不触发 getUpdates")

    def close(self):
        pass


_CFG = {"bot_token": "123:abc", "chat_id": 0, "allowed_users": [7]}
_MSG = {"chat": {"id": 42}, "from": {"id": 7}, "text": ""}


def _msg(text, user_id=7):
    m = dict(_MSG)
    m["text"] = text
    m["from"] = {"id": user_id}
    return m


def _touch(path, b=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b)


# ---------------- make_tag / build_caption ----------------


class TestTags:
    def test_make_tag_sanitizes(self):
        assert make_tag("张三 三") == "#张三_三"
        assert make_tag("a-b/c.d") == "#a_b_c_d"
        assert make_tag("  _ab_  ") == "#ab"
        assert make_tag("") == ""
        assert make_tag("###") == ""

    def test_build_caption_with_author(self):
        cap = build_caption("douyin", "讨厌晚上", "我去！好美_7691296880559540623")
        assert cap.startswith("#讨厌晚上 #douyin\n")
        assert "我去！好美" in cap

    def test_build_caption_without_author(self):
        cap = build_caption("bilibili", "", "标题_x")
        assert cap == "#bilibili\n标题_x"


# ---------------- 快照与元数据 ----------------


class TestSnapshotAndMeta:
    def test_group_new_files(self, tmp_path):
        root = str(tmp_path / "download")
        _touch(os.path.join(root, "douyin", "a", "t", "old.jpg"))
        before = snapshot_tree(root)
        _touch(os.path.join(root, "douyin", "a", "t", "001.jpg"))
        _touch(os.path.join(root, "douyin", "a", "t", "video.mp4"))
        _touch(os.path.join(root, "site", "单文件.mp4"))
        groups = group_new_files(before, root)
        key = os.path.join("douyin", "a", "t")
        assert groups[key] == [
            os.path.join(key, "001.jpg"),
            os.path.join(key, "video.mp4"),
        ]
        assert groups["site"] == [os.path.join("site", "单文件.mp4")]

    def test_group_unchanged_excluded(self, tmp_path):
        root = str(tmp_path / "download")
        _touch(os.path.join(root, "a", "1.jpg"))
        before = snapshot_tree(root)
        assert group_new_files(before, root) == {}

    def test_parse_post_meta(self):
        assert parse_post_meta(os.path.join("bilibili", "张三", "标题_1"), []) == (
            "bilibili",
            "张三",
            "标题_1",
        )
        assert parse_post_meta(os.path.join("bilibili", "标题_1"), []) == (
            "bilibili",
            "",
            "标题_1",
        )
        # 散文件：download/<site>/<作者>?/<标题>.mp4 → group_key 为空
        assert parse_post_meta("", [os.path.join("douyin", "背影", "标题_1.mp4")]) == (
            "douyin",
            "背影",
            "标题_1",
        )
        assert parse_post_meta("", [os.path.join("youtube", "标题.mp4")]) == (
            "youtube",
            "",
            "标题",
        )


# ---------------- 消息流 ----------------


class TestHandleMessage:
    def test_unauthorized_no_dispatch(self):
        calls = []

        def dispatch(url):
            calls.append(url)

        s = _Session()
        replies = handle_message(
            s, _CFG, _msg("https://x.com/a", user_id=999), dispatch=dispatch
        )
        assert calls == []  # 未授权不消耗下载
        assert any("未授权" in r and "999" in r for r in replies)
        assert s.sent[0][0] == "sendMessage"

    def test_channel_post_bypasses_user_whitelist(self, tmp_path):
        # 频道消息不做用户白名单：能发帖的只有频道管理员（匿名管理员无 from）
        root = str(tmp_path / "download")

        def dispatch(url):
            d = os.path.join(root, "douyin", "背影", "我去！好美_123")
            _touch(os.path.join(d, "001.jpg"))

        s = _Session()
        msg = {
            "chat": {"id": -100123, "type": "channel"},
            "sender_chat": {"id": -100123, "type": "channel"},
            "text": "https://x.com/a",
        }
        replies = handle_message(s, _CFG, msg, dispatch=dispatch, download_root=root)
        assert not any("未授权" in r for r in replies)
        assert replies[0].startswith("开始下载")

    def test_no_link_hint(self):
        s = _Session()
        replies = handle_message(s, _CFG, _msg("今天天气不错"), dispatch=lambda u: None)
        assert replies == ["没识别到链接，直接发链接（可带分享文案）即可。"]

    def test_full_flow_with_tags(self, tmp_path):
        root = str(tmp_path / "download")

        def dispatch(url):
            d = os.path.join(root, "douyin", "背影", "我去！好美_123")
            _touch(os.path.join(d, "001.jpg"))
            _touch(os.path.join(d, "002.jpg"))
            _touch(os.path.join(d, "video.mp4"))

        s = _Session()
        replies = handle_message(
            s,
            _CFG,
            _msg("看看 https://v.douyin.com/abc 好美"),
            dispatch=dispatch,
            download_root=root,
        )
        methods = [m for m, _ in s.sent]
        # sendMessage(开始) + sendMediaGroup + sendVideo + sendMessage(完成)
        assert methods.count("sendMediaGroup") == 1
        assert methods.count("sendVideo") == 1
        assert methods[0] == "sendMessage" and methods[-1] == "sendMessage"
        # 相册 caption 带作者与站点标签
        media = json.loads(s.sent[1][1]["media"])
        assert media[0]["caption"].startswith("#背影 #douyin")
        assert any(r.startswith("完成：3 项已回传") for r in replies)
        # 回传到消息来源聊天，而非配置里的默认 chat_id
        assert all(d.get("chat_id") == 42 for _, d in s.sent)

    def test_no_new_files_notice(self, tmp_path):
        s = _Session()
        replies = handle_message(
            s,
            _CFG,
            _msg("https://x.com/a"),
            dispatch=lambda u: None,
            download_root=str(tmp_path / "download"),
        )
        assert any("没有新产物" in r for r in replies)

    def test_dispatch_error_reported(self, tmp_path):
        def boom(url):
            raise RuntimeError("风控拦截")

        s = _Session()
        replies = handle_message(
            s,
            _CFG,
            _msg("https://x.com/a"),
            dispatch=boom,
            download_root=str(tmp_path / "download"),
        )
        assert any("下载失败" in r and "风控拦截" in r for r in replies)

    def test_start_help(self):
        s = _Session()
        replies = handle_message(s, _CFG, _msg("/start"), dispatch=lambda u: None)
        assert replies and "发链接" in replies[0]


class TestAllowlist:
    def test_is_allowed(self):
        assert is_allowed(_CFG, 7) is True
        assert is_allowed(_CFG, 8) is False
        assert is_allowed({"allowed_users": []}, 7) is False
        assert is_allowed({}, 7) is False
