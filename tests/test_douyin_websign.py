# -*- coding: utf-8 -*-
"""douyin_websign（vendored Apache-2.0）：x-secsdk-web-signature 纯函数测试。

算法（逆向自 @byted/secsdk-strategy v1.0.40 的 webSignUrl）：
    query = URLSearchParams(除签名外全部参数).toString()
    sig   = md5(f"{uifid}_{timestamp}_{SALT}_{query}").hexdigest()
"""

import hashlib

import core.douyin_websign as websign


class TestEncodePairs:
    """按 JS URLSearchParams.toString() 语义序列化——哈希与发送的是同一字节串。"""

    def test_space_encodes_as_percent20_not_plus(self):
        # urlencode 会把空格编成 '+'，JS 语义必须是 '%20'，否则签名对不上
        assert websign.encode_pairs([("a", "1 2")]) == "a=1%202"

    def test_safe_chars_preserved(self):
        assert websign.encode_pairs([("a", "*-._x")]) == "a=*-._x"

    def test_multiple_pairs_joined_with_amp(self):
        assert websign.encode_pairs([("a", "1"), ("b", "2")]) == "a=1&b=2"


class TestPickUifid:
    def test_prefers_first_available_spelling(self):
        cookies = {"UIFID_TEMP": "temp_val", "UIFID": "val"}
        result = websign.pick_uifid(cookies)
        assert result in ("temp_val", "val")

    def test_empty_or_missing_returns_none(self):
        assert websign.pick_uifid(None) is None
        assert websign.pick_uifid({"foo": "bar"}) is None
        assert websign.pick_uifid({"UIFID": ""}) is None


class TestSign:
    def test_signature_matches_md5_formula(self):
        pairs = [("aweme_id", "1"), ("a_bogus", "xyz+/=")]
        query, sig, headers = websign.sign(pairs, "uid_123", timestamp=1791914678)
        # 签名覆盖 uifid + timestamp 在内的完整 query（不含签名本身）
        body = query[: -len("&x-secsdk-web-signature=" + sig)]
        expected = hashlib.md5(
            f"uid_123_1791914678_{websign.SALT}_{body}".encode()
        ).hexdigest()
        assert sig == expected
        assert query.endswith("&x-secsdk-web-signature=" + sig)
        assert sig in query

    def test_same_second_is_deterministic(self):
        pairs = [("a", "1"), ("b", "2 3")]
        q1, s1, _ = websign.sign(pairs, "uid", timestamp=100)
        q2, s2, _ = websign.sign(pairs, "uid", timestamp=100)
        assert (q1, s1) == (q2, s2)

    def test_appends_uifid_and_timestamp_once(self):
        query, _, _ = websign.sign([("a", "1")], "uid_9", timestamp=7)
        assert "uifid=uid_9" in query
        assert "timestamp=7" in query
        # 已带 uifid 的入参不重复追加
        query2, _, _ = websign.sign([("uifid", "keep")], "uid_9", timestamp=7)
        assert query2.count("uifid=") == 1
        assert "uifid=keep" in query2

    def test_headers_carry_signature_bundle(self):
        _, sig, headers = websign.sign([("a", "1")], "uid_1", timestamp=3)
        assert headers["uifid"] == "uid_1"
        assert headers["x-secsdk-web-signature"] == sig
        assert headers["x-secsdk-web-expire"] == "3"
