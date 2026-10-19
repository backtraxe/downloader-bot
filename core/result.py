# -*- coding: utf-8 -*-
"""下载结果契约。

各下载后端统一返回 DownloadResult，上层（CLI / Telegram bot）据此区分
成功、部分成功、重复链接、失败与无媒体，不再只靠文件系统快照猜结果。
"""

from dataclasses import dataclass
from enum import Enum


class DownloadStatus(str, Enum):
    DOWNLOADED = "downloaded"  # 至少写入一个新文件
    PARTIAL = "partial"  # 有新文件，但也存在失败项
    SKIPPED = "skipped"  # 没有新文件，目标已存在（重复链接）
    FAILED = "failed"  # 没有新文件，且存在失败原因
    NO_MEDIA = "no_media"  # 解析成功但没有可下载媒体（全被过滤/页面无媒体）


@dataclass(frozen=True)
class DownloadResult:
    status: DownloadStatus
    message: str = ""

    @property
    def ok(self) -> bool:
        """是否属于“无错误”结果（含重复链接）。"""
        return self.status in (
            DownloadStatus.DOWNLOADED,
            DownloadStatus.PARTIAL,
            DownloadStatus.SKIPPED,
        )


def result_from_counts(
    downloaded: int = 0,
    skipped: int = 0,
    failed: int = 0,
    filtered: int = 0,
) -> DownloadResult:
    """按“新增/已存在/失败/被过滤”计数生成统一结果。

    判定优先级：新增 > 失败 > 已存在 > 被过滤。
    - downloaded > 0：DOWNLOADED；同时有失败则降为 PARTIAL
    - downloaded == 0 且有失败：FAILED（不能把失败误报成“已下载过”）
    - 只有 skipped：SKIPPED
    - 只有 filtered：NO_MEDIA
    - 全部为 0：NO_MEDIA
    """
    if downloaded:
        parts = [f"新增 {downloaded} 项"]
        if skipped:
            parts.append(f"跳过 {skipped} 项")
        if failed:
            parts.append(f"失败 {failed} 项")
        status = DownloadStatus.PARTIAL if failed else DownloadStatus.DOWNLOADED
        return DownloadResult(status, "，".join(parts))
    if failed:
        msg = f"失败 {failed} 项"
        if skipped:
            msg += f"（另有 {skipped} 项已存在）"
        return DownloadResult(DownloadStatus.FAILED, msg)
    if skipped:
        return DownloadResult(DownloadStatus.SKIPPED, f"已存在 {skipped} 项")
    if filtered:
        return DownloadResult(
            DownloadStatus.NO_MEDIA, f"全部 {filtered} 项被判定为装饰图"
        )
    return DownloadResult(DownloadStatus.NO_MEDIA, "未发现可下载的媒体文件")
