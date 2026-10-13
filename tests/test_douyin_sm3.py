# -*- coding: utf-8 -*-
"""douyin_sm3（vendored Apache-2.0）：SM3 哈希回归测试。"""

from douyin_sm3 import sm3_hash, sm3_hexdigest, sm3_to_array

# GB/T 32905-2016 公布的 "abc" 测试向量
_SM3_ABC = "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0"


class TestSm3:
    def test_abc_vector_hex(self):
        assert sm3_hexdigest(b"abc") == _SM3_ABC

    def test_abc_vector_raw(self):
        assert sm3_hash(b"abc").hex() == _SM3_ABC

    def test_to_array_is_byte_list(self):
        arr = sm3_to_array(b"abc")
        assert isinstance(arr, list)
        assert len(arr) == 32
        assert all(isinstance(x, int) and 0 <= x <= 255 for x in arr)
        assert arr[0] == 0x66 and arr[-1] == 0xE0
