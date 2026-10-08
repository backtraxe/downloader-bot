# -*- coding: utf-8 -*-
"""live_photo.py 纯函数单元测试（不依赖 ffmpeg/exiftool/网络）"""
import os

import pytest

import live_photo as lp


def _make_jpeg(w=64, h=64):
    import io as _io
    from PIL import Image
    buf = _io.BytesIO()
    Image.new("RGB", (w, h), (200, 100, 50)).save(buf, format="JPEG")
    return buf.getvalue()


# ---------------- find_pairs ----------------

class TestFindPairs:
    def test_pairs_by_stem(self, tmp_path):
        (tmp_path / "001.heic").write_bytes(b"img")
        (tmp_path / "001.mp4").write_bytes(b"vid")
        (tmp_path / "002.jpg").write_bytes(b"img2")
        (tmp_path / "002.mp4").write_bytes(b"vid2")
        (tmp_path / "003.jpg").write_bytes(b"img3")  # 无配对视频
        pairs = lp.find_pairs(str(tmp_path))
        assert len(pairs) == 2
        assert (pairs[0][0].endswith("001.heic") and pairs[0][1].endswith("001.mp4"))
        assert (pairs[1][0].endswith("002.jpg") and pairs[1][1].endswith("002.mp4"))

    def test_ignores_unpaired_video(self, tmp_path):
        (tmp_path / "001.jpg").write_bytes(b"img")
        (tmp_path / "video.mp4").write_bytes(b"vid")
        assert lp.find_pairs(str(tmp_path)) == []


# ---------------- build_xmp_packet ----------------

class TestBuildXmpPacket:
    def test_contains_required_fields(self):
        xmp = lp.build_xmp_packet(video_length=1234)
        assert b"APP1" not in xmp  # packet 不应带 JPEG 段标记
        for field in [b'Camera:MotionPhoto="1"', b'MicroVideo="1"',
                      b'MicroVideoOffset="1234"', b'MotionPhotoVersion="1"',
                      b'Item:Semantic="MotionPhoto"', b'Item:Length="1234"']:
            assert field in xmp, field


# ---------------- mux_motion_photo ----------------

class TestMuxMotionPhoto:
    def test_structure(self):
        jpeg = _make_jpeg()
        video = b"fakemp4data"
        out = lp.mux_motion_photo(jpeg, video)
        # Python 字节层面可验证：以 JPEG SOI 开头
        assert out[:2] == b"\xff\xd8"
        # 尾部是完整 MP4 数据
        assert out.endswith(video)
        # XMP 段已被插入
        assert b"MicroVideoOffset=\"11\"" in out
        assert b"Camera:MotionPhoto" in out

    def test_parsable_by_pillow(self):
        # mux 后前半段仍是合法 JPEG
        jpeg = _make_jpeg()
        out = lp.mux_motion_photo(jpeg, b"v" * 100)
        import io as _io
        from PIL import Image
        img = Image.open(_io.BytesIO(out))
        assert img.size == (64, 64)


# ---------------- transcode_to_jpeg ----------------

class TestTranscodeToJpeg:
    def test_jpeg_passthrough(self):
        src = _make_jpeg(32, 24)
        out = lp.transcode_to_jpeg(src, ".jpg")
        from PIL import Image
        import io as _io
        assert Image.open(_io.BytesIO(out)).size == (32, 24)

    def test_png_to_jpeg(self):
        import io as _io
        from PIL import Image
        buf = _io.BytesIO()
        Image.new("RGBA", (16, 16), (0, 255, 0, 128)).save(buf, format="PNG")
        out = lp.transcode_to_jpeg(buf.getvalue(), ".png")
        assert Image.open(_io.BytesIO(out)).format == "JPEG"


# ---------------- digest_motion_photo（反向拆解验证 mux 正确性） ----------------

class TestDigestMotionPhoto:
    def test_roundtrip(self):
        jpeg = _make_jpeg()
        video = os.urandom(4096)
        muxed = lp.mux_motion_photo(jpeg, video)
        img, vid = lp.digest_motion_photo(muxed)
        # 视频部分必须字节级完全一致
        assert vid == video
        # 图片部分：mux 在 JPEG 里插入了 APP1 XMP，与原字节不同但必须是合法 JPEG
        import io as _io
        from PIL import Image
        decoded = Image.open(_io.BytesIO(img))
        assert decoded.size == (64, 64)
