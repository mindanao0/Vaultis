# -*- coding: utf-8 -*-
"""ทดสอบ ma_cross_dates — helper สกัดวันที่ golden/death cross ทั้งหมด (Roadmap A1)."""

import pandas as pd

from technical.indicators import ma_cross_dates


def _series(values: list[float], start: str = "2024-01-01") -> pd.Series:
    idx = pd.date_range(start, periods=len(values), freq="D")
    return pd.Series(values, index=idx, dtype=float)


def test_detects_all_golden_and_death_cross_dates():
    fast = _series([1, 2, 3, 4, 3, 2, 1, 2, 3, 4])
    slow = _series([2.5] * 10)
    result = ma_cross_dates(fast, slow)
    golden = [d.strftime("%Y-%m-%d") for d in result["golden"]]
    death = [d.strftime("%Y-%m-%d") for d in result["death"]]
    # ตัดขึ้นวันที่ 3 (3>2.5), ตัดลงวันที่ 6 (2<2.5), ตัดขึ้นอีกรอบวันที่ 9 (3>2.5)
    assert golden == ["2024-01-03", "2024-01-09"]
    assert death == ["2024-01-06"]


def test_warmup_nan_is_not_a_cross():
    nan = float("nan")
    fast = _series([nan, nan, 3, 4, 5])
    slow = _series([nan, nan, 2, 2, 2])
    # เริ่มมาก็อยู่เหนืออยู่แล้วหลังพ้น warm-up — ห้ามนับเป็น golden cross (AUDIT.md C1)
    assert ma_cross_dates(fast, slow) == {"golden": [], "death": []}


def test_short_or_empty_series_returns_no_crosses():
    empty = pd.Series(dtype=float)
    assert ma_cross_dates(empty, empty) == {"golden": [], "death": []}
    one = _series([1.0])
    assert ma_cross_dates(one, one * 2) == {"golden": [], "death": []}
