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
from core.result import DownloadResult, DownloadStatus


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

    def test_snapshot_skips_part_residue(self, tmp_path):
        # 下载中断的 .part 残留不算产物：不触发"新文件"，也不该被回传
        root = str(tmp_path / "download")
        _touch(os.path.join(root, "youtube", "a.mp4.part"))
        _touch(os.path.join(root, "youtube", "b.mp4"))
        groups = group_new_files({}, root)
        assert groups == {"youtube": [os.path.join("youtube", "b.mp4")]}

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
        # 兼容直接传文件路径（group_key 为空）：从 rel_files 父目录推导
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

    def test_parse_post_meta_standalone_file_group(self):
        # group_new_files 对散文件返回的 group_key 是父目录（<site> 或
        # <site>/<作者>），不能当成帖子目录解析，否则作者层会被误当标题
        assert parse_post_meta(
            os.path.join("youtube", "SomeChannel"),
            [os.path.join("youtube", "SomeChannel", "My Video.mp4")],
        ) == ("youtube", "SomeChannel", "My Video")
        assert parse_post_meta(
            "youtube", [os.path.join("youtube", "My Video.mp4")]
        ) == ("youtube", "", "My Video")
        assert parse_post_meta(
            os.path.join("douyin", "背影"),
            [os.path.join("douyin", "背影", "标题_1.mp4")],
        ) == ("douyin", "背影", "标题_1")

    def test_parse_post_meta_post_dir_with_generated_files(self):
        # 目录产物的文件按 001.jpg / video.mp4 生成，深度 2 时仍是帖子目录
        assert parse_post_meta(
            os.path.join("xiaohongshu", "标题_123"),
            [os.path.join("xiaohongshu", "标题_123", "video.mp4")],
        ) == ("xiaohongshu", "", "标题_123")
        assert parse_post_meta(
            os.path.join("douyin", "标题_123"),
            [
                os.path.join("douyin", "标题_123", "001.jpg"),
                os.path.join("douyin", "标题_123", "video.mp4"),
            ],
        ) == ("douyin", "", "标题_123")


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
        # sendMessage(开始) + sendMediaGroup(2 图 + 1 视频混排) + sendMessage(完成)
        assert methods.count("sendMediaGroup") == 1
        assert methods.count("sendVideo") == 0
        assert methods[0] == "sendMessage" and methods[-1] == "sendMessage"
        # 混排相册：图片与视频一条消息，caption 带作者与站点标签
        media = json.loads(s.sent[1][1]["media"])
        assert [m["type"] for m in media] == ["photo", "photo", "video"]
        assert media[0]["caption"].startswith("#背影 #douyin")
        assert any(r.startswith("完成：3 项已回传") for r in replies)
        # 回传到消息来源聊天，而非配置里的默认 chat_id
        assert all(d.get("chat_id") == 42 for _, d in s.sent)

    def test_youtube_single_file_caption_has_author_tag(self, tmp_path):
        root = str(tmp_path / "download")

        def dispatch(url):
            _touch(os.path.join(root, "youtube", "SomeChannel", "My Video.mp4"))
            return DownloadResult(DownloadStatus.DOWNLOADED, "已下载: My Video")

        s = _Session()
        replies = handle_message(
            s,
            _CFG,
            _msg("https://www.youtube.com/watch?v=abc"),
            dispatch=dispatch,
            download_root=root,
        )
        video = next(d for m, d in s.sent if m == "sendVideo")
        assert video["caption"].startswith("#SomeChannel #youtube\nMy Video")
        assert any(r.startswith("完成：1 项已回传") for r in replies)

    def test_youtube_single_file_group_only_uploads_new_file(self, tmp_path):
        # 散文件组的 group_key 是作者目录，不能把目录里的旧视频一起重发
        root = str(tmp_path / "download")
        _touch(os.path.join(root, "youtube", "SomeChannel", "Old Video.mp4"))

        def dispatch(url):
            _touch(os.path.join(root, "youtube", "SomeChannel", "New Video.mp4"))
            return DownloadResult(DownloadStatus.DOWNLOADED, "已下载: New Video")

        s = _Session()
        handle_message(
            s,
            _CFG,
            _msg("https://www.youtube.com/watch?v=new"),
            dispatch=dispatch,
            download_root=root,
        )
        videos = [d for m, d in s.sent if m == "sendVideo"]
        assert len(videos) == 1
        assert videos[0]["caption"].startswith("#SomeChannel #youtube\nNew Video")

    def test_request_id_shared_across_turn(self, tmp_path, monkeypatch):
        # bot 单条消息的下载与上传共享同一请求 ID，grep 该 ID 可取整轮轨迹
        import re

        from core.utils import current_request_id

        root = str(tmp_path / "download")
        seen = {}

        def dispatch(url):
            _touch(os.path.join(root, "youtube", "Chan", "v.mp4"))
            seen["dispatch"] = current_request_id()
            return DownloadResult(DownloadStatus.DOWNLOADED, "已下载: v")

        def fake_upload(session, cfg, target, **kw):
            seen["upload"] = current_request_id()
            return 1, []

        monkeypatch.setattr("telegram.bot.upload", fake_upload)
        handle_message(
            _Session(),
            _CFG,
            _msg("https://www.youtube.com/watch?v=v"),
            dispatch=dispatch,
            download_root=root,
        )
        assert re.fullmatch(r"r[a-z0-9]{4}", seen["dispatch"])
        assert seen["upload"] == seen["dispatch"]

    def test_upload_exception_reports_and_continues(self, tmp_path, monkeypatch):
        # upload 抛非 TgError（读盘失败等）也要回显而不是吞掉整条消息的回复
        root = str(tmp_path / "download")

        def dispatch(url):
            _touch(os.path.join(root, "youtube", "Chan", "v.mp4"))
            return DownloadResult(DownloadStatus.DOWNLOADED, "已下载: v")

        def boom(*args, **kwargs):
            raise RuntimeError("磁盘读取出错")

        monkeypatch.setattr("telegram.bot.upload", boom)
        s = _Session()
        replies = handle_message(
            s,
            _CFG,
            _msg("https://www.youtube.com/watch?v=v"),
            dispatch=dispatch,
            download_root=root,
        )
        assert any("上传失败" in r for r in replies)

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

    def test_dispatch_failed_reports_reason(self, tmp_path):
        s = _Session()
        replies = handle_message(
            s,
            _CFG,
            _msg("https://x.com/a"),
            dispatch=lambda u: DownloadResult(
                DownloadStatus.FAILED, "缺少小红书 Cookie"
            ),
            download_root=str(tmp_path / "download"),
        )
        assert any("下载失败" in r and "Cookie" in r for r in replies)
        assert not any("此前已下载过" in r for r in replies)

    def test_dispatch_skipped_notice(self, tmp_path):
        s = _Session()
        replies = handle_message(
            s,
            _CFG,
            _msg("https://x.com/a"),
            dispatch=lambda u: DownloadResult(DownloadStatus.SKIPPED, "已存在 2 项"),
            download_root=str(tmp_path / "download"),
        )
        assert any("此前已下载过" in r for r in replies)

    def test_dispatch_no_media_notice(self, tmp_path):
        s = _Session()
        replies = handle_message(
            s,
            _CFG,
            _msg("https://x.com/a"),
            dispatch=lambda u: DownloadResult(
                DownloadStatus.NO_MEDIA, "全部 5 项被判定为装饰图"
            ),
            download_root=str(tmp_path / "download"),
        )
        assert any("没有找到可下载的媒体" in r and "装饰图" in r for r in replies)

    def test_dispatch_success_without_files_not_duplicate(self, tmp_path):
        # 后端说成功但快照没有新文件：不能再误报成确定的“此前已下载过”
        s = _Session()
        replies = handle_message(
            s,
            _CFG,
            _msg("https://x.com/a"),
            dispatch=lambda u: DownloadResult(
                DownloadStatus.DOWNLOADED, "已下载: 测试"
            ),
            download_root=str(tmp_path / "download"),
        )
        assert any("没有新产物" in r and "可能此前已下载过" in r for r in replies)
        assert not any("下载失败" in r for r in replies)

    def test_partial_download_message(self, tmp_path):
        root = str(tmp_path / "download")

        def dispatch(url):
            d = os.path.join(root, "douyin", "背影", "我去！好美_123")
            _touch(os.path.join(d, "001.jpg"))
            return DownloadResult(DownloadStatus.PARTIAL, "新增 1 项，失败 1 项")

        s = _Session()
        replies = handle_message(
            s,
            _CFG,
            _msg("https://x.com/a"),
            dispatch=dispatch,
            download_root=root,
        )
        assert any("完成：1 项已回传（新增 1 项，失败 1 项）" in r for r in replies)

    def test_failed_with_files_still_uploads_and_reports_reason(self, tmp_path):
        # 后端已落盘部分文件但整体标记 FAILED：仍应回传，并带上失败原因
        root = str(tmp_path / "download")

        def dispatch(url):
            d = os.path.join(root, "douyin", "背影", "我去！好美_123")
            _touch(os.path.join(d, "001.jpg"))
            return DownloadResult(DownloadStatus.FAILED, "部分图片失败")

        s = _Session()
        replies = handle_message(
            s,
            _CFG,
            _msg("https://x.com/a"),
            dispatch=dispatch,
            download_root=root,
        )
        assert any(m == "sendPhoto" for m, _ in s.sent)
        assert any("部分图片失败" in r for r in replies)

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


# ---------------- 过程消息 TTL 自动删除 ----------------


class _ImmediateTimer:
    """假 threading.Timer：start() 即同步执行回调，便于断言删除调用。"""

    def __init__(self, interval, function, args=(), kwargs=None):
        self.interval = interval
        self.function = function
        self.args = args
        self.kwargs = kwargs or {}
        self.daemon = False

    def start(self):
        self.function(*self.args, **self.kwargs)


class TestProcessMessageTtl:
    def test_text_message_scheduled_and_deleted(self):
        cfg = {**_CFG, "process_message_ttl_seconds": 60}
        s = _Session()
        handle_message(
            s,
            cfg,
            _msg("今天天气不错"),
            dispatch=lambda u: None,
            timer_cls=_ImmediateTimer,
        )
        methods = [m for m, _ in s.sent]
        assert methods == ["sendMessage", "deleteMessage"]
        assert s.sent[0][1]["chat_id"] == 42
        assert s.sent[1][1] == {"chat_id": 42, "message_id": 1}

    def test_ttl_zero_never_deletes(self):
        cfg = {**_CFG, "process_message_ttl_seconds": 0}
        s = _Session()
        handle_message(
            s,
            cfg,
            _msg("今天天气不错"),
            dispatch=lambda u: None,
            timer_cls=_ImmediateTimer,
        )
        assert all(m != "deleteMessage" for m, _ in s.sent)

    def test_missing_message_id_skips_schedule(self):
        class _RespNoId:
            def json(self):
                return {"ok": True, "result": {}}

        class _NoIdSession:
            def __init__(self):
                self.sent = []

            def post_files(self, url, data=None, files=None, timeout=None):
                self.sent.append((url.rsplit("/", 1)[1], data or {}))
                return _RespNoId()

            def get(self, url, **kwargs):
                raise AssertionError("本组测试不触发 getUpdates")

            def close(self):
                pass

        s = _NoIdSession()
        handle_message(
            s,
            {**_CFG, "process_message_ttl_seconds": 60},
            _msg("今天天气不错"),
            dispatch=lambda u: None,
            timer_cls=_ImmediateTimer,
        )
        # 响应缺 message_id → 不调度不报错，只发出 sendMessage
        assert [m for m, _ in s.sent] == ["sendMessage"]

    def test_media_messages_never_deleted(self, tmp_path):
        root = str(tmp_path / "download")

        def dispatch(url):
            _touch(os.path.join(root, "site", "标题", "001.jpg"))

        cfg = {**_CFG, "process_message_ttl_seconds": 60}
        s = _Session()
        handle_message(
            s,
            cfg,
            _msg("https://x.com/a"),
            dispatch=dispatch,
            download_root=root,
            timer_cls=_ImmediateTimer,
        )
        methods = [m for m, _ in s.sent]
        assert "sendPhoto" in methods or "sendMediaGroup" in methods
        # 自动删除只作用于 _reply 文字提示：删除条数恰等于文字消息条数，
        # 媒体消息（相册/视频/document）永不进入删除序列
        assert methods.count("deleteMessage") == methods.count("sendMessage") == 2
