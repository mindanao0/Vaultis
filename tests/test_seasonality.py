# -*- coding: utf-8 -*-
"""ทดสอบ monthly_seasonality (Roadmap B5) — สถิติรายเดือนเชิงบรรยาย."""

import pandas as pd
import pytest

from analysis.returns import monthly_seasonality


def _march_pattern_prices(years: int = 6) -> pd.Series:
    """ราคา ณ สิ้นเดือน: มีนาคม +10% ทุกปี เดือนอื่นนิ่ง."""
    dates = pd.date_range("2018-01-31", periods=years * 12, freq="ME")
    price = 100.0
    prices = []
    for date in dates:
        if date.month == 3:
            price *= 1.10
        prices.append(price)
    return pd.Series(prices, index=dates)


def test_march_pattern_detected():
    stats = monthly_seasonality(_march_pattern_prices())
    assert stats.loc[3, "median_pct"] == pytest.approx(10.0, abs=0.01)
    assert stats.loc[3, "positive_rate_pct"] == pytest.approx(100.0)
    assert stats.loc[6, "median_pct"] == pytest.approx(0.0, abs=1e-9)
    assert stats.loc[6, "positive_rate_pct"] == pytest.approx(0.0)


def test_always_returns_all_12_months():
    stats = monthly_seasonality(_march_pattern_prices(years=2))
    assert list(stats.index) == list(range(1, 13))
    assert stats["n_samples"].dropna().min() >= 1


def test_empty_prices_fail_loud():
    with pytest.raises(ValueError):
        monthly_seasonality(pd.Series(dtype=float))
