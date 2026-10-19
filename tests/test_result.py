# -*- coding: utf-8 -*-
"""core/result.py 下载结果契约单元测试（无网络依赖）"""

from core.result import (
    DownloadResult,
    DownloadStatus,
    result_from_counts,
)


class TestResultFromCounts:
    def test_downloaded(self):
        r = result_from_counts(downloaded=2)
        assert r.status is DownloadStatus.DOWNLOADED
        assert "新增 2 项" in r.message
        assert r.ok is True

    def test_partial_when_downloaded_and_failed(self):
        r = result_from_counts(downloaded=2, skipped=1, failed=1)
        assert r.status is DownloadStatus.PARTIAL
        assert "新增 2 项" in r.message
        assert "跳过 1 项" in r.message
        assert "失败 1 项" in r.message
        assert r.ok is True

    def test_failed_precedes_skipped(self):
        # 有失败时不能因为“还有已存在文件”就误报为重复链接
        r = result_from_counts(skipped=2, failed=1)
        assert r.status is DownloadStatus.FAILED
        assert "失败 1 项" in r.message
        assert "已存在" in r.message
        assert r.ok is False

    def test_skipped(self):
        r = result_from_counts(skipped=3)
        assert r.status is DownloadStatus.SKIPPED
        assert "已存在 3 项" in r.message
        assert r.ok is True

    def test_filtered_only_is_no_media(self):
        r = result_from_counts(filtered=5)
        assert r.status is DownloadStatus.NO_MEDIA
        assert "装饰图" in r.message
        assert r.ok is False

    def test_empty_is_no_media(self):
        r = result_from_counts()
        assert r.status is DownloadStatus.NO_MEDIA
        assert "未发现可下载的媒体文件" in r.message

    def test_result_ok_property(self):
        assert DownloadResult(DownloadStatus.DOWNLOADED).ok is True
        assert DownloadResult(DownloadStatus.PARTIAL).ok is True
        assert DownloadResult(DownloadStatus.SKIPPED).ok is True
        assert DownloadResult(DownloadStatus.FAILED).ok is False
        assert DownloadResult(DownloadStatus.NO_MEDIA).ok is False
