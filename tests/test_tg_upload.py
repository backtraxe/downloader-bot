# -*- coding: utf-8 -*-
"""telegram/upload.py 纯函数与流程测试（假 session，不联网）"""

import json
import os
import sys

import pytest

from core.config import _TEMPLATE
from telegram.upload import (
    MAX_PHOTO_BYTES,
    TgError,
    _prepare_one,
    _try_compress_video,
    call_api,
    collect_media,
    load_config,
    plan_uploads,
    prepare_media,
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
        self.calls = []  # (url, data_keys, file_fields, file_paths)
        self._payloads = list(payloads or [])

    def post_files(self, url, data=None, files=None, timeout=None):
        self.calls.append((url, data or {}, files or {}))
        payload = (
            self._payloads.pop(0)
            if self._payloads
            else {"ok": True, "result": {"message_id": 1}}
        )
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
        cfg_path = tmp_path / "config.json"
        assert load_config(str(cfg_path), str(tmp_path / "telegram.json")) is None
        template = json.loads(cfg_path.read_text())
        assert "telegram" in template
        assert "bot_token" in template["telegram"]

    def test_placeholder_token_returns_none(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps(_TEMPLATE, ensure_ascii=False))
        assert load_config(str(cfg_path), str(tmp_path / "telegram.json")) is None

    def test_valid_config(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps({"telegram": _CFG}, ensure_ascii=False))
        assert load_config(str(cfg_path), str(tmp_path / "telegram.json")) == _CFG

    def test_legacy_telegram_json_fallback(self, tmp_path):
        legacy = tmp_path / "telegram.json"
        legacy.write_text(json.dumps(_CFG, ensure_ascii=False))
        # config.json 不存在时，旧版扁平结构 telegram.json 兜底视作 telegram 段
        assert load_config(str(tmp_path / "config.json"), str(legacy)) == _CFG


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
            ("document", "001.heic"),
            ("document", "002.jpg"),
        ]

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
        files = [
            _touch(tmp_path / "a.jpg"),
            _touch(tmp_path / "b.jpg"),
            _touch(tmp_path / "v.mp4"),
            _touch(tmp_path / "c.jpg"),
        ]
        ops = plan_uploads(files)
        assert [k for k, _ in ops] == ["album", "video", "album"]
        assert len(ops[0][1]) == 2 and len(ops[2][1]) == 1


# ---------------- call_api 重试 ----------------


class TestCallApi:
    def test_429_retried_with_retry_after(self, monkeypatch):
        monkeypatch.setattr("telegram.upload.time.sleep", lambda s: None)
        s = _Session(
            [
                {
                    "ok": False,
                    "error_code": 429,
                    "description": "Too Many Requests",
                    "parameters": {"retry_after": 1},
                },
                {"ok": True, "result": {"message_id": 7}},
            ]
        )
        from telegram import upload as upload_mod

        result = upload_mod.call_api(s, _CFG, "sendPhoto", {}, _sleep=lambda x: None)
        assert result == {"message_id": 7}
        assert len(s.calls) == 2

    def test_error_raises(self):
        s = _Session([{"ok": False, "error_code": 400, "description": "Bad Request"}])
        with pytest.raises(TgError):
            call_api(s, _CFG, "sendPhoto", {}, _sleep=lambda x: None)

    def test_network_error_retried_then_raises(self):
        s = _Session(
            [
                lambda: ConnectionError("boom"),
                lambda: ConnectionError("boom"),
                lambda: ConnectionError("boom"),
            ]
        )
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


# ---------------- prepare_media 预览预处理 ----------------


class _Completed:
    """假 subprocess 返回值。"""

    def __init__(self, stdout=""):
        self.stdout = stdout


class TestPrepareOne:
    def test_small_jpeg_unchanged_no_temp(self, tmp_path):
        p = _touch(tmp_path / "a.jpg", b"tiny")
        tmpdir = tmp_path / "t"
        tmpdir.mkdir()
        send, temps = prepare_media([p], tmpdir=str(tmpdir))
        assert send == [p] and temps == []
        assert os.listdir(tmpdir) == []  # 无任何临时产物落盘

    def test_prepare_one_small_jpeg(self, tmp_path):
        p = _touch(tmp_path / "a.jpg", b"tiny")
        tmpdir = tmp_path / "t"
        tmpdir.mkdir()
        send, temp = _prepare_one(p, str(tmpdir))
        assert send == p and temp is None

    def test_big_noisy_jpeg_compressed(self, tmp_path):
        Image = pytest.importorskip("PIL.Image")
        # 随机噪声图保证源文件 >10MB 且近似不可压（真实触发压缩链）
        img = Image.frombytes("RGB", (6000, 5000), os.urandom(6000 * 5000 * 3))
        src = tmp_path / "big.jpg"
        img.save(src, format="JPEG", quality=95)
        assert src.stat().st_size > MAX_PHOTO_BYTES
        tmpdir = tmp_path / "t"
        tmpdir.mkdir()
        send, temp = _prepare_one(str(src), str(tmpdir))
        assert temp is not None and send == temp
        assert send.endswith(".jpg") and str(tmpdir) in send
        assert os.path.getsize(send) <= MAX_PHOTO_BYTES
        with Image.open(send) as again:  # 产物可再次被 PIL 打开
            again.verify()

    def test_heic_missing_deps_falls_back(self, tmp_path, monkeypatch, caplog):
        # pillow_heif 导入失败 → 原样返回（维持 document 现状），日志给出安装提示
        monkeypatch.setitem(sys.modules, "pillow_heif", None)
        p = _touch(tmp_path / "a.heic", b"x")
        tmpdir = tmp_path / "t"
        tmpdir.mkdir()
        send, temp = _prepare_one(p, str(tmpdir))
        assert send == p and temp is None
        assert any("pillow-heif" in r.message for r in caplog.records)

    def test_heic_converted_to_jpeg(self, tmp_path):
        Image = pytest.importorskip("PIL.Image")
        pillow_heif = pytest.importorskip("pillow_heif")
        pillow_heif.register_heif_opener()
        src = tmp_path / "a.heic"
        Image.new("RGB", (64, 48), (200, 30, 30)).save(src, format="HEIF")
        tmpdir = tmp_path / "t"
        tmpdir.mkdir()
        send, temp = _prepare_one(str(src), str(tmpdir))
        assert temp is not None and send == temp
        assert send.endswith(".jpg") and str(tmpdir) in send

    def test_video_bitrate_from_duration(self, tmp_path):
        calls = []

        def fake_run(argv, **_kwargs):
            calls.append(argv)
            if argv[0] == "ffprobe":
                return _Completed(stdout="120.5\n")
            with open(argv[-1], "wb") as f:  # 假 ffmpeg：产出小文件即视为成功
                f.write(b"x" * 1024)
            return _Completed()

        src = _touch(tmp_path / "big.mp4", b"x" * (51 * 1024 * 1024))
        tmpdir = tmp_path / "t"
        tmpdir.mkdir()
        out = _try_compress_video(src, str(tmpdir), _run=fake_run)
        expected = int((49 * 1024 * 1024 * 8) / 120.5) - 96_000
        assert out == str(tmpdir / "big.mp4")
        ffmpeg_argv = [a for a in calls if a[0] == "ffmpeg"][0]
        assert ffmpeg_argv[ffmpeg_argv.index("-b:v") + 1] == str(expected)
        assert ffmpeg_argv[ffmpeg_argv.index("-maxrate") + 1] == str(expected)
        assert ffmpeg_argv[ffmpeg_argv.index("-bufsize") + 1] == str(expected * 2)

    def test_video_no_ffmpeg_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("telegram.upload.shutil.which", lambda name: None)
        src = _touch(tmp_path / "big.mp4", b"x")
        assert _try_compress_video(src, str(tmp_path)) is None


class TestPrepareMediaIntegration:
    def test_upload_prepares_and_cleans_tempdir(self, tmp_path, monkeypatch):
        Image = pytest.importorskip("PIL.Image")
        img = Image.frombytes("RGB", (6000, 5000), os.urandom(6000 * 5000 * 3))
        media_dir = tmp_path / "media"
        media_dir.mkdir()
        img.save(media_dir / "big.jpg", format="JPEG", quality=95)
        _touch(media_dir / "small.mp4", b"x")
        pre = tmp_path / "pre"
        monkeypatch.setattr(
            "telegram.upload.tempfile.mkdtemp",
            lambda prefix="": (pre.mkdir(), str(pre))[1],
        )
        s = _Session()
        sent, skipped = upload(s, _CFG, str(media_dir))
        assert sent == 2 and skipped == []
        methods = [c[0].rsplit("/", 1)[1] for c in s.calls]
        assert methods == ["sendPhoto", "sendVideo"]  # 大图被压到 ≤10MB 走 photo
        photo_path = s.calls[0][2]["photo"]
        assert str(pre) in photo_path  # 发送的是 tmpdir 里的预览产物
        assert not pre.exists()  # finally 后临时目录被清空
