# -*- coding: utf-8 -*-
"""ทดสอบ weekly_dca_signal (Roadmap B3) — สัญญาณบนแท่งสัปดาห์ด้วย MA10w/MA40w."""

import numpy as np
import pandas as pd

from technical import signal_rules
from technical.indicators import weekly_dca_signal


def _daily_series(values: np.ndarray, start: str = "2020-01-01") -> pd.Series:
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=idx, dtype=float)


def test_short_history_returns_no_data():
    series = _daily_series(np.linspace(100, 120, 100))  # ~20 สัปดาห์ < MA40w
    result = weekly_dca_signal(series)
    assert result["signal"] == signal_rules.NO_DATA
    assert result["price"] is None
    assert result["ma40w"] is None


def test_signal_consistent_with_returned_components():
    i = np.arange(1000)
    values = 100.0 * np.exp(0.0006 * i) * (1 + 0.03 * np.sin(i / 15.0))
    result = weekly_dca_signal(_daily_series(values))
    assert result["signal"] != signal_rules.NO_DATA
    # สัญญาณต้องตรงกับการป้อนองค์ประกอบที่คืนมากลับเข้า dca_signal (นิยามกลางเดียว)
    assert result["signal"] == signal_rules.dca_signal(
        result["price"], result["ma10w"], result["ma40w"], result["rsi14w"]
    )


def test_downtrend_maps_to_down_family():
    values = np.concatenate([np.full(500, 200.0), np.linspace(200, 80, 500)])
    result = weekly_dca_signal(_daily_series(values))
    assert result["signal"] in (signal_rules.DOWNTREND, signal_rules.DOWNTREND_WATCH)
    assert result["price"] < result["ma40w"]
