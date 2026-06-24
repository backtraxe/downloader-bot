# -*- coding: utf-8 -*-
"""sites.py 站点识别单元测试"""
import pytest

from sites import SITE_REQUIRE_COOKIE, cookie_file_for, get_site_name


class TestGetSiteName:
    @pytest.mark.parametrize("url,expected", [
        # xiaohongshu
        ("https://www.xiaohongshu.com/explore/abc", "xiaohongshu"),
        ("https://www.xiaohongshu.com/discovery/item/abc", "xiaohongshu"),
        ("https://xhslink.com/abc", "xiaohongshu"),
        # bilibili
        ("https://www.bilibili.com/video/BV1xx", "bilibili"),
        ("https://b23.tv/abc", "bilibili"),
        # douyin
        ("https://www.douyin.com/video/123", "douyin"),
        ("https://v.douyin.com/abc/", "douyin"),
        # youtube
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
        ("https://youtu.be/dQw4w9WgXcQ", "youtube"),
        ("https://m.youtube.com/watch?v=abc", "youtube"),
        # instagram
        ("https://www.instagram.com/p/Cabc/", "instagram"),
        ("https://instagram.com/reel/abc", "instagram"),
        # twitter / x
        ("https://x.com/user/status/123", "twitter"),
        ("https://twitter.com/user/status/123", "twitter"),
        ("https://t.co/abc", "twitter"),
    ])
    def test_known_sites(self, url, expected):
        assert get_site_name(url) == expected

    def test_unknown_domain_two_parts(self):
        assert get_site_name("https://example.com/page") == "example.com"

    def test_unknown_domain_with_port(self):
        # 单段 host（localhost）带端口：端口信息用下划线保留，避免不同端口混用 cookie
        assert get_site_name("http://localhost:8000/x") == "localhost_8000"

    def test_case_insensitive(self):
        assert get_site_name("HTTPS://WWW.YouTube.COM/watch?v=x") == "youtube"

    def test_all_target_sites_have_cookie_config(self):
        """5 个新增站点 + xhs 都应有 cookie 策略说明"""
        for site in ["youtube", "bilibili", "douyin", "instagram", "twitter", "xiaohongshu"]:
            assert site in SITE_REQUIRE_COOKIE, f"{site} 缺少 cookie 策略配置"


class TestCookieFile:
    def test_cookie_file_path(self):
        assert cookie_file_for("youtube") == "cookies/youtube.txt"
        assert cookie_file_for("twitter") == "cookies/twitter.txt"
        assert cookie_file_for("xiaohongshu") == "cookies/xiaohongshu.txt"
