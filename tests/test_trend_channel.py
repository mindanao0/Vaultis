# -*- coding: utf-8 -*-
"""ทดสอบ fit_trend_channel (Roadmap A2)."""

import numpy as np
import pandas as pd
import pytest

from analysis.trend_channel import MIN_TREND_POINTS, fit_trend_channel

DAILY_LOG_GROWTH = 0.0008


def _noisy_growth(n: int = 600, amp: float = 0.04) -> pd.Series:
    """ราคาโตแบบ exponential + ลูกคลื่น sine ให้ residual มีความแปรปรวนจริง."""
    i = np.arange(n)
    log_price = np.log(100.0) + DAILY_LOG_GROWTH * i + amp * np.sin(i / 25.0)
    idx = pd.bdate_range("2022-01-03", periods=n)
    return pd.Series(np.exp(log_price), index=idx)


def test_recovers_annual_growth_rate():
    channel = fit_trend_channel(_noisy_growth())
    expected_pct = (np.exp(DAILY_LOG_GROWTH * 252) - 1.0) * 100.0
    assert abs(channel["annual_growth_pct"] - expected_pct) < 3.0
    assert len(channel["trend"]) == 600
    assert channel["sigma_log"] > 0
    assert abs(channel["current_sigma"]) < 2.5


def test_price_spike_reads_as_above_trend():
    series = _noisy_growth()
    series.iloc[-1] *= 1.35
    assert fit_trend_channel(series)["current_sigma"] > 1.5


def test_price_dip_reads_as_below_trend():
    series = _noisy_growth()
    series.iloc[-1] *= 0.70
    assert fit_trend_channel(series)["current_sigma"] < -1.5


def test_short_series_fails_loud():
    with pytest.raises(ValueError):
        fit_trend_channel(_noisy_growth(n=MIN_TREND_POINTS - 1))


def test_nonpositive_price_fails_loud():
    series = _noisy_growth()
    series.iloc[10] = 0.0
    with pytest.raises(ValueError):
        fit_trend_channel(series)
