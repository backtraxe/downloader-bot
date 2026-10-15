# -*- coding: utf-8 -*-
"""tg_upload.py 纯函数与流程测试（假 session，不联网）"""
import json
import os

import pytest

from tg_upload import (
    TgError,
    call_api,
    collect_media,
    load_config,
    plan_uploads,
    upload,
)


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Session:
    """记录调用并按队列返回响应。"""

    def __init__(self, payloads=None):
        self.calls = []          # (url, data_keys, file_fields, file_paths)
        self._payloads = list(payloads or [])

    def post_files(self, url, data=None, files=None, timeout=None):
        self.calls.append((url, data or {}, files or {}))
        payload = self._payloads.pop(0) if self._payloads else \
            {"ok": True, "result": {"message_id": 1}}
        if callable(payload):
            raise payload()  # 网络异常模拟
        return _Resp(payload)

    def close(self):
        pass


def _touch(p, b=b"x"):
    with open(p, "wb") as f:
        f.write(b)
    return str(p)


_CFG = {"bot_token": "123:abc", "chat_id": 42}


# ---------------- load_config ----------------

class TestLoadConfig:
    def test_missing_writes_template_and_returns_none(self, tmp_path):
        cfg_path = tmp_path / "telegram.json"
        assert load_config(str(cfg_path)) is None
        template = json.loads(cfg_path.read_text())
        assert "bot_token" in template and "chat_id" in template

    def test_placeholder_token_returns_none(self, tmp_path):
        cfg_path = tmp_path / "telegram.json"
        cfg_path.write_text(json.dumps({"bot_token": "在此填入 @BotFather 给的 token",
                                        "chat_id": 1}))
        assert load_config(str(cfg_path)) is None

    def test_valid_config(self, tmp_path):
        cfg_path = tmp_path / "telegram.json"
        cfg_path.write_text(json.dumps(_CFG, ensure_ascii=False))
        assert load_config(str(cfg_path)) == _CFG


# ---------------- collect_media / plan_uploads ----------------

class TestCollectAndPlan:
    def test_collect_dir_sorted_ignores_hidden(self, tmp_path):
        _touch(tmp_path / "002.jpg")
        _touch(tmp_path / "001.jpg")
        _touch(tmp_path / ".DS_Store")
        sub = tmp_path / "sub"
        sub.mkdir()
        _touch(sub / "x.jpg")
        files = collect_media(str(tmp_path))
        assert [os.path.basename(f) for f in files] == ["001.jpg", "002.jpg"]

    def test_collect_single_file(self, tmp_path):
        f = _touch(tmp_path / "a.jpg")
        assert collect_media(f) == [f]

    def test_album_chunking_and_order(self, tmp_path):
        files = [_touch(tmp_path / f"{i:03d}.jpg") for i in range(12)]
        ops = plan_uploads(files)
        kinds = [(k, len(v) if isinstance(v, list) else 1) for k, v in ops]
        assert kinds == [("album", 10), ("album", 2)]

    def test_heic_and_big_photo_go_document(self, tmp_path):
        heic = _touch(tmp_path / "001.heic")
        big = _touch(tmp_path / "002.jpg")
        sizes = {heic: 1024, big: 11 * 1024 * 1024}
        ops = plan_uploads([heic, big], size_of=sizes.get)
        assert [(k, os.path.basename(v)) for k, v in ops] == [
            ("document", "001.heic"), ("document", "002.jpg")]

    def test_video_and_too_big(self, tmp_path):
        v = _touch(tmp_path / "video.mp4")
        h = _touch(tmp_path / "huge.mp4")
        sizes = {v: 1024, h: 51 * 1024 * 1024}
        ops = plan_uploads([v, h], size_of=sizes.get)
        assert [k for k, _ in ops] == ["video", "too_big"]

    def test_document_flag_forces_document(self, tmp_path):
        f = [_touch(tmp_path / "a.jpg")]
        ops = plan_uploads(f, document=True)
        assert [k for k, _ in ops] == ["document"]

    def test_interleaved_photos_split_albums(self, tmp_path):
        files = [_touch(tmp_path / "a.jpg"), _touch(tmp_path / "b.jpg"),
                 _touch(tmp_path / "v.mp4"), _touch(tmp_path / "c.jpg")]
        ops = plan_uploads(files)
        assert [k for k, _ in ops] == ["album", "video", "album"]
        assert len(ops[0][1]) == 2 and len(ops[2][1]) == 1


# ---------------- call_api 重试 ----------------

class TestCallApi:
    def test_429_retried_with_retry_after(self, monkeypatch):
        monkeypatch.setattr("tg_upload.time.sleep", lambda s: None)
        s = _Session([
            {"ok": False, "error_code": 429,
             "description": "Too Many Requests",
             "parameters": {"retry_after": 1}},
            {"ok": True, "result": {"message_id": 7}},
        ])
        import tg_upload
        result = tg_upload.call_api(s, _CFG, "sendPhoto", {},
                                    _sleep=lambda x: None)
        assert result == {"message_id": 7}
        assert len(s.calls) == 2

    def test_error_raises(self):
        s = _Session([{"ok": False, "error_code": 400, "description": "Bad Request"}])
        with pytest.raises(TgError):
            call_api(s, _CFG, "sendPhoto", {}, _sleep=lambda x: None)

    def test_network_error_retried_then_raises(self):
        s = _Session([lambda: ConnectionError("boom"),
                      lambda: ConnectionError("boom"),
                      lambda: ConnectionError("boom")])
        with pytest.raises(TgError):
            call_api(s, _CFG, "sendPhoto", {}, _sleep=lambda x: None)
        assert len(s.calls) == 3


# ---------------- upload 编排 ----------------

class TestUpload:
    def test_directory_flow(self, tmp_path):
        _touch(tmp_path / "001.jpg")
        _touch(tmp_path / "002.jpg")
        _touch(tmp_path / "003.heic")
        _touch(tmp_path / "video.mp4")
        s = _Session()
        sent, skipped = upload(s, _CFG, str(tmp_path))
        assert sent == 4 and skipped == []
        methods = [c[0].rsplit("/", 1)[1] for c in s.calls]
        assert methods == ["sendMediaGroup", "sendDocument", "sendVideo"]
        # 相册：media JSON 两个 attach，字段名与图一一对应
        media = json.loads(s.calls[0][1]["media"])
        assert len(media) == 2
        assert set(s.calls[0][2].keys()) == {"file0", "file1"}

    def test_single_photo_uses_sendphoto(self, tmp_path):
        _touch(tmp_path / "001.jpg")
        s = _Session()
        sent, _ = upload(s, _CFG, str(tmp_path))
        assert sent == 1
        assert s.calls[0][0].endswith("/sendPhoto")

    def test_empty_dir_raises(self, tmp_path):
        with pytest.raises(TgError):
            upload(_Session(), _CFG, str(tmp_path))
