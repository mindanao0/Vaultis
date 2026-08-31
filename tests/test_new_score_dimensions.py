# -*- coding: utf-8 -*-
"""เทสต์ 4 มิติคะแนนใหม่ (2026-08): Volatility, Valuation, Relative Strength, Expense.

ทุกมิติต้องตัด max_score ออกเมื่อข้อมูลไม่พร้อม (ไม่ใช่ให้คะแนน 0) — pattern เดียวกับ
Dividend เดิม (ดู test_dividend_score.py) ทุกเทสต์ใช้ข้อมูลสังเคราะห์แบบ deterministic
ไม่มี randomness และไม่ยิง network
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import numpy as np
import pandas as pd
import pytest

from analysis import financial_model as fm


def _series_from(values, start: str = "2023-01-02") -> pd.Series:
    idx = pd.date_range(start, periods=len(values), freq="B")
    return pd.Series(values, index=idx, dtype=float)


class TestVolatilityScore:
    """คำนวณจาก closes ได้เสมอ — ไม่มีสถานะ "ไม่พร้อม" (ต่างจากมิติอื่นที่เพิ่มมาใหม่)."""

    def test_steady_low_volatility_scores_high(self):
        steady = 100 * np.cumprod(np.full(300, 1.0003))
        score = fm._volatility_score(_series_from(steady))
        assert score >= 8

    def test_wild_swings_and_deep_drawdown_score_low(self):
        t = np.arange(300)
        wild = 100 * np.cumprod(1 + 0.06 * np.sin(t))
        score = fm._volatility_score(_series_from(wild))
        assert score <= 2

    def test_bounded_0_to_10(self):
        for values in (
            100 * np.cumprod(np.full(260, 1.0005)),
            100 * np.cumprod(1 + 0.05 * np.sin(np.arange(260))),
        ):
            assert 0 <= fm._volatility_score(_series_from(values)) <= 10


class TestValuationScore:
    """ต้องมี >= trend_channel.MIN_TREND_POINTS ไม่งั้นตัดออก (คืน None ไม่ใช่ 0 — C1)."""

    @staticmethod
    def _on_trend_series(n: int = 520) -> np.ndarray:
        x = np.arange(n)
        log_trend = 0.0006 * x + np.log(100.0)
        wiggle = 0.02 * np.sin(x / 15.0)  # noise เล็กน้อยแบบ deterministic กัน residual variance=0
        return np.exp(log_trend + wiggle)

    def test_insufficient_history_returns_none_not_zero(self):
        short = self._on_trend_series()[:300]
        assert fm._valuation_score(_series_from(short, start="2022-01-03")) is None

    def test_price_spiked_far_above_trend_scores_low(self):
        above = self._on_trend_series()
        above[-40:] *= 1.7
        score = fm._valuation_score(_series_from(above, start="2022-01-03"))
        assert score == 0

    def test_price_dropped_far_below_trend_scores_high(self):
        below = self._on_trend_series()
        below[-40:] *= 0.5
        score = fm._valuation_score(_series_from(below, start="2022-01-03"))
        assert score == 10

    def test_bounded_0_to_10_when_available(self):
        score = fm._valuation_score(_series_from(self._on_trend_series(), start="2022-01-03"))
        assert score is not None
        assert 0 <= score <= 10


class TestRelativeStrengthScore:
    """เทียบผลตอบแทน 3 เดือนกับ benchmark (VOO) — ไม่มี benchmark = ตัดออก (None)."""

    @staticmethod
    def _base() -> pd.Series:
        return _series_from(100 * np.cumprod(np.full(300, 1.0004)))

    def _tilt_tail(self, base: pd.Series, target_factor: float) -> pd.Series:
        values = base.to_numpy(copy=True)
        fac = np.linspace(1.0, target_factor, 64)
        values[-64:] = values[-64] * fac
        return _series_from(values)

    def test_no_benchmark_returns_none(self):
        base = self._base()
        assert fm._relative_strength_score(base, None) is None

    def test_benchmark_too_short_returns_none(self):
        base = self._base()
        assert fm._relative_strength_score(base, base.tail(30)) is None

    def test_strong_outperformance_scores_max(self):
        base = self._base()
        outperform = self._tilt_tail(base, 1.10)
        assert fm._relative_strength_score(outperform, base) == 5

    def test_mild_outperformance_scores_middle_tier(self):
        base = self._base()
        mild = self._tilt_tail(base, 1.06)
        assert fm._relative_strength_score(mild, base) == 3

    def test_strong_underperformance_scores_zero(self):
        base = self._base()
        underperform = self._tilt_tail(base, 0.80)
        assert fm._relative_strength_score(underperform, base) == 0


class TestExpenseScore:
    @pytest.mark.parametrize(
        "expense_pct, expected",
        [
            (0.03, 5),   # VOO ของจริง
            (0.10, 5),
            (0.1001, 4),
            (0.15, 4),   # QQQM ของจริง
            (0.20, 4),
            (0.2001, 2),
            (0.40, 2),
            (0.4001, 0),
            (1.0, 0),
        ],
    )
    def test_score_tiers(self, expense_pct, expected):
        assert fm._expense_score(expense_pct) == expected


class TestExpenseRatioFromTicker:
    def test_reads_annual_report_expense_ratio(self, monkeypatch):
        monkeypatch.setattr(
            fm.yf,
            "Ticker",
            lambda _s: type("T", (), {"info": {"annualReportExpenseRatio": 0.0003}})(),
        )
        assert fm._expense_ratio_pct("VOO") == pytest.approx(0.03)

    def test_falls_back_to_net_expense_ratio(self, monkeypatch):
        monkeypatch.setattr(
            fm.yf, "Ticker", lambda _s: type("T", (), {"info": {"netExpenseRatio": 0.0015}})()
        )
        assert fm._expense_ratio_pct("QQQM") == pytest.approx(0.15)

    def test_network_failure_returns_none(self, monkeypatch):
        def _boom(_s):
            raise RuntimeError("network down")

        monkeypatch.setattr(fm.yf, "Ticker", _boom)
        assert fm._expense_ratio_pct("VOO") is None

    def test_missing_field_returns_none(self, monkeypatch):
        monkeypatch.setattr(fm.yf, "Ticker", lambda _s: type("T", (), {"info": {}})())
        assert fm._expense_ratio_pct("VOO") is None

    def test_implausible_value_returns_none_instead_of_guessing(self, monkeypatch):
        """expense ratio > 5% ไม่ใช่ ETF จริง — ตัดออกแทนการเดา (C1, เหมือน dividend M15)."""
        monkeypatch.setattr(
            fm.yf, "Ticker", lambda _s: type("T", (), {"info": {"annualReportExpenseRatio": 0.10}})()
        )
        assert fm._expense_ratio_pct("VOO") is None


class TestScoreFromPricesWithAllDimensions:
    """เมื่อมีข้อมูลครบทุกมิติ (optional args ครบ) max_score ต้องรวมทุกก้อน — ยังคง 0<=total_pct<=100."""

    @staticmethod
    def _long_uptrend(n: int = 520) -> pd.Series:
        x = np.arange(n)
        log_trend = 0.0006 * x + np.log(100.0)
        wiggle = 0.02 * np.sin(x / 15.0)
        return _series_from(np.exp(log_trend + wiggle), start="2022-01-03")

    def test_max_score_includes_all_available_dimensions(self):
        closes = self._long_uptrend()
        benchmark = _series_from(100 * np.cumprod(np.full(520, 1.0003)), start="2022-01-03")
        result = fm.score_from_prices(
            "TEST",
            closes,
            div_yield=0.02,
            benchmark_closes=benchmark,
            expense_ratio_pct=0.05,
        )
        expected_max = (
            fm.TREND_MAX
            + fm.TIMING_MAX
            + fm.MOMENTUM_MAX
            + fm.VOLATILITY_MAX
            + fm.DIVIDEND_MAX
            + fm.VALUATION_MAX
            + fm.RELATIVE_STRENGTH_MAX
            + fm.EXPENSE_MAX
        )
        assert result["max_score"] == expected_max
        assert 0 <= result["total_pct"] <= 100
        assert result["valuation_available"] is True
        assert result["relative_strength_available"] is True
        assert result["expense_available"] is True
