# -*- coding: utf-8 -*-
"""douyin_abogus（vendored Apache-2.0）：a_bogus 签名回归测试。

黄金值用于防止移植/后续编辑改动算法行为；同一 rng 种子 + 固定时钟下
输出必须逐字节稳定（_algorithm 只对 query/body/UA 与时钟敏感，噪声不进校验位）。
"""

import random

import pytest

from core.douyin_abogus import ABogus, PAGE_ID, AID

_FIXED_MS = 1791914678000
# ABogus("test-ua", rng=Random(42)).get_value("aweme_id=1&aid=6383",
# now_ms=_FIXED_MS) 的移植时实测输出
_GOLDEN = (
    "QJMcvtyjEoRcPVFG8Kn3et3lhy9ANPSymtT/WuFPHxYfL10GSRPGTPGvGxol4qI0"
    "HWBsie3HZdMAbdncpUU0pqHkomkvSPhWvU5CV80LhqwRGMJQLrbTezXFuwMn054qe"
    "55UiARI8Urq6VxAkrQD/B59SKLFQbSBMpxbk2YcT9sg1MyAE1cHPBtdNhGbUn54"
)


class TestABogus:
    def test_seed_and_clock_make_output_deterministic(self):
        a = ABogus("ua-x", rng=random.Random(7))
        b = ABogus("ua-x", rng=random.Random(7))
        assert a.get_value("k=1", now_ms=_FIXED_MS) == \
            b.get_value("k=1", now_ms=_FIXED_MS)

    def test_golden_value_regression(self):
        bogus = ABogus("test-ua", rng=random.Random(42)).get_value(
            "aweme_id=1&aid=6383", now_ms=_FIXED_MS)
        assert bogus == _GOLDEN

    def test_clock_before_epoch_rejected(self):
        bogus = ABogus("ua", rng=random.Random(0))
        with pytest.raises(ValueError):
            bogus.get_value("k=1", now_ms=0)

    def test_defaults_match_douyin_web(self):
        # aid 6383/page_id 6241 写进签名体，改动会被平台拒——锁死默认值
        assert AID == 6383
        assert PAGE_ID == 6241
