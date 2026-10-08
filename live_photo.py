# -*- coding: utf-8 -*-
"""实况照片后处理模块：把小红书下载的 (静态图, mp4) 对儿打包成各平台可用的实况格式。

独立于下载器本体（utils/1024/xhs/downloader 不依赖本模块），使用方法：

    python live_photo.py <live_photo目录> [--android] [--ios]

产物：
  --android（默认开）  在源目录下生成 <stem>_motion.jpg——Google Motion Photo
                       单文件（JPEG 尾部内嵌 MP4，含 MicroVideo + Camera XMP），
                       小米/OPPO/Google Photos/三星相册可识别为动态照片；
                       vivo/iQOO 需相册支持 vivo 私有 XMP（当前按标准格式尽力，
                       不保证）。
  --ios                调用系统 exiftool 给 (静态图, 视频) 写入相同
                       ContentIdentifier 配对 ID，输出 <stem>_ios.heic +
                       <stem>_ios.mov 到源目录——用户需经支持 Live Photo 的
                       App/快捷指令导入图库后才会显示为实况（无法仅靠文件落盘）。
"""
import argparse
import glob
import io
import os
import shutil
import struct
import subprocess
import uuid

from PIL import Image  # noqa: E402 核心功能依赖 Pillow，Termux 需 pkg install python-pillow

from utils import setup_logging

logger = setup_logging()

# JPEG 段标记
_SOI = b"\xff\xd8"
_EOI = b"\xff\xd9"
_APP1 = b"\xff\xe1"

# XMP 命名空间（Motion Photo Format 1.0 + 旧版 MicroVideo 双写，最大化兼容）
_XMP_NS = (
    'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
    'xmlns:Camera="http://ns.google.com/photos/1.0/camera/" '
    'xmlns:GCamera="http://ns.google.com/photos/1.0/camera/" '
    'xmlns:Container="http://ns.google.com/photos/1.0/container/" '
    'xmlns:Item="http://ns.google.com/photos/1.0/container/item/"'
)


def find_pairs(directory):
    """在目录中查找 (静态图, mp4) 同 stem 配对，按 stem 排序返回列表。"""
    pairs = []
    for stem_path in sorted(glob.glob(os.path.join(directory, "*"))):
        stem, ext = os.path.splitext(stem_path)
        if ext.lower() not in (".heic", ".jpg", ".jpeg", ".png", ".mpo"):
            continue
        video = stem + ".mp4"
        if os.path.exists(video):
            pairs.append((stem_path, video))
    return pairs


def build_xmp_packet(video_length):
    """构造 XMP 段内容（不含 JPEG 段标记），双写新规范 Camera 与旧版 MicroVideo。"""
    return (
        f"<rdf:RDF {_XMP_NS}>"
        f'<rdf:Description rdf:about="" '
        f'Camera:MotionPhoto="1" Camera:MotionPhotoVersion="1" '
        f'Camera:MotionPhotoPresentationTimestampUs="-1" '
        f'GCamera:MicroVideo="1" GCamera:MicroVideoVersion="1" '
        f'GCamera:MicroVideoOffset="{video_length}" '
        f'GCamera:MicroVideoPresentationTimestampUs="0">'
        f"<Container:Directory><rdf:Seq>"
        f'<rdf:li rdf:parseType="Resource">'
        f'<Container:Item Item:Mime="image/jpeg" Item:Semantic="Primary" '
        f'Item:Length="0" Item:Padding="0"/>'
        f"</rdf:li>"
        f'<rdf:li rdf:parseType="Resource">'
        f'<Container:Item Item:Mime="video/mp4" Item:Semantic="MotionPhoto" '
        f'Item:Length="{video_length}" Item:Padding="0"/>'
        f"</rdf:li>"
        f"</rdf:Seq></Container:Directory>"
        f"</rdf:Description></rdf:RDF>"
    ).encode("utf-8")


def mux_motion_photo(jpeg_bytes, video_bytes):
    """把 XMP 段插入 JPEG，视频追加到 EOI 之后，返回 Google Motion Photo 单文件字节。"""
    xmp = build_xmp_packet(len(video_bytes))
    xmp_header = b"http://ns.adobe.com/xap/1.0/\x00"
    app1_payload = xmp_header + xmp
    # APP1 段长度含自身的 2 字节长度字段 + payload
    app1_segment = _APP1 + struct.pack(">H", len(app1_payload) + 2) + app1_payload
    # XMP 段必须紧跟 SOI 之后（APP0 前），按 Motion Photo 规范
    out = _SOI + app1_segment + jpeg_bytes[2:] + video_bytes
    if not out.endswith(video_bytes):
        # JPEG 本身以 EOI 结尾，视频应紧接其后
        raise ValueError("mux 结构异常：视频未追加到文件尾")
    return out


def digest_motion_photo(muxed):
    """反向拆解：把 mux 后的单文件拆回 (jpeg_bytes, video_bytes)。

    依据：MicroVideoOffset = 视频长度，从文件尾回溯。用于测试与自检。"""
    xmp_start = muxed.find(b"MicroVideoOffset=\"")
    if xmp_start == -1:
        raise ValueError("不是 Motion Photo 文件：缺少 MicroVideoOffset")
    vlen_start = xmp_start + len(b"MicroVideoOffset=\"")
    vlen_end = muxed.find(b"\"", vlen_start)
    vlen = int(muxed[vlen_start:vlen_end])
    video = muxed[-vlen:]
    jpeg = muxed[:-vlen]
    return jpeg, video


def transcode_to_jpeg(image_bytes, ext):
    """任意静态图转 JPEG 字节（JPEG 直通；HEIC 需 pillow-heif 可选依赖）。"""
    if ext.lower() in (".heic", ".heif"):
        try:
            import pillow_heif
        except ImportError:
            raise RuntimeError(
                "处理 HEIC 需安装 pillow-heif：pip install pillow-heif\n"
                "  （Termux 需先 pkg install libheif，再 pip install pillow-heif 源码编译）"
            )
        pillow_heif.register_heif_opener()
    with Image.open(io.BytesIO(image_bytes)) as img:
        rgb = img.convert("RGB")
        buf = io.BytesIO()
        rgb.save(buf, format="JPEG", quality=95)
        return buf.getvalue()


def process_android(image_path, video_path, out_path=None):
    """生成 Motion Photo 单文件，返回输出路径。"""
    with open(image_path, "rb") as f:
        jpeg_bytes = transcode_to_jpeg(f.read(), os.path.splitext(image_path)[1])
    with open(video_path, "rb") as f:
        video_bytes = f.read()
    muxed = mux_motion_photo(jpeg_bytes, video_bytes)
    if out_path is None:
        stem, _ = os.path.splitext(image_path)
        out_path = f"{stem}_motion.jpg"
    # Motion Photo 规范：Item:Length=0 表示主图长度由 JPEG 本身决定，
    # 但规范要求视频定位——这里用 MicroVideoOffset 兼容旧版读取器
    with open(out_path, "wb") as f:
        f.write(muxed)
    return out_path


def process_ios(image_path, video_path, out_dir=None):
    """用 exiftool 给 (静态图, 视频) 写配对 ContentIdentifier，返回 (img, mov) 输出路径。

    依赖系统 exiftool；不可用时抛 RuntimeError 由调用方提示。"""
    if not shutil.which("exiftool"):
        raise RuntimeError(
            "iOS 配对需要 exiftool：brew install exiftool（macOS）或对应包管理器安装"
        )
    out_dir = out_dir or os.path.dirname(image_path)
    stem = os.path.splitext(os.path.basename(image_path))[0]
    pair_id = str(uuid.uuid4()).upper()
    out_img = os.path.join(out_dir, f"{stem}_ios{os.path.splitext(image_path)[1]}")
    out_mov = os.path.join(out_dir, f"{stem}_ios.mov")
    shutil.copy2(image_path, out_img)
    shutil.copy2(video_path, out_mov)
    # 静态图：写 Apple MakerNote ContentIdentifier
    subprocess.run([
        "exiftool", "-overwrite_original", "-q",
        f"-MakerApple:ContentIdentifier={pair_id}", out_img,
    ], check=True, capture_output=True)
    # 视频：写 QuickTime metadata content.identifier（同值配对）
    subprocess.run([
        "exiftool", "-overwrite_original", "-q",
        f"-com.apple.quicktime.content.identifier={pair_id}", out_mov,
    ], check=True, capture_output=True)
    return out_img, out_mov


def main():
    parser = argparse.ArgumentParser(
        description="把实况照片对打包成平台可用格式（Android Motion Photo / iOS 配对元数据）"
    )
    parser.add_argument("directory", help="实况照片目录（含 001.heic+001.mp4 等对儿）")
    parser.add_argument("--android", action="store_true", default=True,
                        help="生成 Motion Photo 单文件（默认开启）")
    parser.add_argument("--no-android", dest="android", action="store_false")
    parser.add_argument("--ios", action="store_true",
                        help="用 exiftool 写 iOS 配对元数据")
    args = parser.parse_args()

    pairs = find_pairs(args.directory)
    if not pairs:
        logger.error("目录中未找到 (静态图, mp4) 配对: %s", args.directory)
        return 1
    logger.info("找到 %d 对实况照片", len(pairs))

    succeeded = failed = 0
    for image_path, video_path in pairs:
        stem = os.path.splitext(os.path.basename(image_path))[0]
        try:
            if args.android:
                out = process_android(image_path, video_path)
                logger.info("  [Android] %s -> %s", stem, os.path.basename(out))
            if args.ios:
                img_out, mov_out = process_ios(image_path, video_path)
                logger.info("  [iOS] %s -> %s + %s", stem,
                            os.path.basename(img_out), os.path.basename(mov_out))
            succeeded += 1
        except Exception as e:  # noqa: BLE001 单边失败不阻断其余
            logger.error("  [失败] %s: %s", stem, e)
            failed += 1

    logger.info("处理完成: %d 成功 / %d 失败 / 共 %d 对", succeeded, failed, len(pairs))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
