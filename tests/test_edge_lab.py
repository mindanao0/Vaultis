# -*- coding: utf-8 -*-
"""ทดสอบ Edge Lab (มติ 2026-07-18: ออกแบบ edge ใหม่แล้ววัดผ่าน harness)."""

import numpy as np
import pandas as pd
import pytest

from portfolio.edge_lab import (
    EDGE_TILT_MAX,
    EDGE_TILT_MIN,
    combo_uw_ivol_tilts,
    edge_weights_fn,
    inverse_vol_tilts,
    rel_strength_tilts,
    stretch_tilts,
    underwater_tilts,
)


def _frame(columns: dict[str, np.ndarray]) -> pd.DataFrame:
    length = len(next(iter(columns.values())))
    idx = pd.bdate_range("2020-01-01", periods=length)
    return pd.DataFrame(columns, index=idx)


def _wiggle(n: int, base: float, drift: float, amp: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return base * np.exp(np.cumsum(rng.normal(drift, amp, n)))


class TestUnderwater:
    def test_deeper_drawdown_gets_higher_tilt(self):
        n = 400
        at_ath = np.linspace(100, 150, n)                       # ทำ ATH ตลอด
        below = np.concatenate([np.linspace(100, 150, n - 60), np.full(60, 150 * 0.80)])
        history = _frame({"ATH": at_ath, "DOWN": below})
        tilts = underwater_tilts(history, ["ATH", "DOWN"])
        assert tilts["ATH"] == pytest.approx(1.0)
        assert tilts["DOWN"] == pytest.approx(1.2, abs=0.01)   # ลึก 20% = เพดาน

    def test_short_history_is_neutral(self):
        history = _frame({"A": np.linspace(100, 90, 100)})
        assert underwater_tilts(history, ["A"]) == {"A": 1.0}


class TestInverseVol:
    def test_low_vol_gets_more_weight(self):
        n = 300
        history = _frame(
            {"CALM": _wiggle(n, 100, 0.0002, 0.004, 1), "WILD": _wiggle(n, 100, 0.0002, 0.03, 2)}
        )
        tilts = inverse_vol_tilts(history, ["CALM", "WILD"])
        assert tilts["CALM"] > 1.0 > tilts["WILD"]
        assert EDGE_TILT_MIN <= min(tilts.values()) and max(tilts.values()) <= EDGE_TILT_MAX

    def test_single_ticker_is_neutral(self):
        history = _frame({"A": _wiggle(300, 100, 0.0002, 0.01, 3)})
        assert inverse_vol_tilts(history, ["A"]) == {"A": 1.0}


class TestRelStrength:
    def test_winner_tilts_above_loser(self):
        n = 300
        history = _frame(
            {
                "UP": _wiggle(n, 100, 0.0015, 0.008, 4),
                "FLAT": _wiggle(n, 100, 0.0000, 0.008, 5),
                "DOWN": _wiggle(n, 100, -0.0015, 0.008, 6),
            }
        )
        tilts = rel_strength_tilts(history, ["UP", "FLAT", "DOWN"])
        assert tilts["UP"] > tilts["FLAT"] > tilts["DOWN"]
        assert EDGE_TILT_MIN <= min(tilts.values()) and max(tilts.values()) <= EDGE_TILT_MAX

    def test_fewer_than_three_tickers_neutral(self):
        history = _frame({"A": _wiggle(300, 100, 0.001, 0.01, 7), "B": _wiggle(300, 100, 0.0, 0.01, 8)})
        assert rel_strength_tilts(history, ["A", "B"]) == {"A": 1.0, "B": 1.0}


class TestStretch:
    def test_stretched_price_tilts_down_and_cheap_tilts_up(self):
        n = 700
        flat = np.full(n, 100.0)
        stretched = np.concatenate([np.full(n - 40, 100.0), np.linspace(100, 135, 40)])
        dipped = np.concatenate([np.full(n - 40, 100.0), np.linspace(100, 80, 40)])
        tilts_hot = stretch_tilts(_frame({"HOT": stretched}), ["HOT"])
        tilts_cheap = stretch_tilts(_frame({"CHEAP": dipped}), ["CHEAP"])
        assert tilts_hot["HOT"] == pytest.approx(EDGE_TILT_MIN, abs=0.02)
        assert tilts_cheap["CHEAP"] == pytest.approx(EDGE_TILT_MAX, abs=0.02)


class TestComboAndWeights:
    def test_combo_stays_bounded(self):
        n = 400
        below = np.concatenate([np.linspace(100, 150, n - 60), np.full(60, 150 * 0.75)])
        history = _frame({"A": below, "B": _wiggle(n, 100, 0.0002, 0.004, 9)})
        tilts = combo_uw_ivol_tilts(history, ["A", "B"])
        assert all(EDGE_TILT_MIN <= v <= EDGE_TILT_MAX for v in tilts.values())

    def test_weights_fn_never_drops_a_ticker(self):
        history = _frame({"A": np.full(50, 100.0)})  # สั้นเกินทุกเกณฑ์ → neutral หมด
        fn = edge_weights_fn({"A": 0.6, "B": 0.4}, underwater_tilts)
        weights = fn(pd.Timestamp("2024-01-01"), history)
        assert set(weights) == {"A", "B"}
        assert weights["A"] == pytest.approx(0.6)
        assert weights["B"] == pytest.approx(0.4)
