# -*- coding: utf-8 -*-
"""ทดสอบ underwater_series / drawdown_episodes (Roadmap A3)."""

import pandas as pd
import pytest

from analysis.risk import calculate_max_drawdown, drawdown_episodes, underwater_series


def _prices(values: list[float], start: str = "2024-01-01") -> pd.Series:
    idx = pd.date_range(start, periods=len(values), freq="D")
    return pd.Series(values, index=idx, dtype=float)


def test_underwater_series_matches_ath_distance():
    uw = underwater_series(_prices([100, 110, 99, 110, 121]))
    assert uw.iloc[0] == 0.0
    assert uw.iloc[1] == 0.0
    assert uw.iloc[2] == pytest.approx(-0.1)
    assert uw.iloc[3] == 0.0
    assert uw.iloc[4] == 0.0


def test_max_drawdown_is_underwater_minimum():
    df = pd.DataFrame(
        {"A": [100.0, 80.0, 120.0], "B": [50.0, 55.0, 60.0]},
        index=pd.date_range("2024-01-01", periods=3),
    )
    mdd = calculate_max_drawdown(df)
    assert mdd["A"] == pytest.approx(-0.2)
    assert mdd["B"] == pytest.approx(0.0)


def test_drawdown_episodes_split_recovery_and_open_round():
    # 110→88 (-20%) ฟื้นที่ 110, ทำ ATH ใหม่ 120 → ร่วงเหลือ 96 (-20%) ยังไม่ฟื้น
    s = _prices([100, 110, 99, 88, 110, 120, 96])
    episodes = drawdown_episodes(s, min_depth=0.15)
    assert len(episodes) == 2

    recovered = [e for e in episodes if e["recovery_date"] is not None]
    still_open = [e for e in episodes if e["recovery_date"] is None]
    assert len(recovered) == 1 and len(still_open) == 1

    rec = recovered[0]
    assert rec["depth_pct"] == pytest.approx(-20.0)
    assert pd.Timestamp(rec["peak_date"]) == pd.Timestamp("2024-01-02")
    assert pd.Timestamp(rec["trough_date"]) == pd.Timestamp("2024-01-04")
    assert pd.Timestamp(rec["recovery_date"]) == pd.Timestamp("2024-01-05")
    assert rec["months_to_recover"] is not None and rec["months_to_recover"] > 0

    cur = still_open[0]
    assert pd.Timestamp(cur["peak_date"]) == pd.Timestamp("2024-01-06")
    assert cur["months_to_recover"] is None


def test_min_depth_filters_shallow_episodes():
    s = _prices([100, 95, 100, 50, 100])  # -5% ตื้นเกิน, -50% ลึกพอ
    episodes = drawdown_episodes(s, min_depth=0.10)
    assert len(episodes) == 1
    assert episodes[0]["depth_pct"] == pytest.approx(-50.0)


def test_empty_prices_fail_loud():
    with pytest.raises(ValueError):
        underwater_series(pd.Series(dtype=float))
    with pytest.raises(ValueError):
        drawdown_episodes(pd.Series(dtype=float))
