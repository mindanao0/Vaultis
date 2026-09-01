# -*- coding: utf-8 -*-
"""คุมสูตรผลตอบแทนของโมเมนตัม (FIX_PLAN ข้อ 1.5).

บั๊กเดิม: ``score_from_prices`` ใช้ ``returns.tail(21).sum()`` ซึ่งเป็น
**ผลรวมเลขคณิตของ pct_change รายวัน** ไม่ใช่ผลตอบแทนของช่วง
ผลรวมนี้ ``> `` ผลตอบแทนทบต้นจริงเสมอเมื่อราคามีความผันผวน
(เพราะ ``log(1+r) < r`` ทุก ``r != 0`` → ``ผลรวม r_i > log(ราคาปลาย/ราคาต้น)``)

ผลที่วัดได้จริง: ราคาที่แกว่งแล้ว **กลับมาที่เดิมเป๊ะ** (ผลตอบแทนจริง 0.00%)
ถูกอ่านเป็นผลตอบแทนบวก → ได้ ``momentum_score`` 10 คะแนนฟรีต่อหน้าต่าง
และเป็นอคติ **ทางเดียว** (ทางลงไม่เคย flip กลับ) → ดัน tilt ของแผน DCA ขึ้น

ไฟล์นี้มีอยู่เพื่อไม่ให้ผลรวมรายวันกลับมาเป็นนิยามของ "ผลตอบแทน" อีก
"""

from __future__ import annotations

import ast
import inspect
import math
import sys
import textwrap
from pathlib import Path

import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from analysis import financial_model as fm  # noqa: E402
from analysis import returns as returns_mod  # noqa: E402
from analysis.returns import RETURN_WINDOWS, calculate_period_returns  # noqa: E402

_BARS_1M = RETURN_WINDOWS["1M"]
_BARS_3M = RETURN_WINDOWS["3M"]

#: เพดานของมิติที่คำนวณจากราคาได้ **เสมอ** จึงไม่มีสถานะ "ตัดออก" — ส่วนของ
#: ``max_score`` ที่ไม่เกี่ยวกับโมเมนตัมเลย เขียนรวมไว้ที่เดียวเพื่อให้เทสต์ในไฟล์นี้
#: พูดถึงเฉพาะ "โควตาโมเมนตัมอยู่หรือหายไป" ซึ่งเป็นประเด็นของ FIX_PLAN 1.5
#: (มิติ optional อย่าง Dividend/Valuation/RelStrength/Expense ไม่ถูกนับ เพราะซีรีส์
#: ทดสอบในไฟล์นี้ไม่มีข้อมูลของมัน — ทั้งหมดถูกตัดออกจากเพดานตาม C1 อยู่แล้ว)
_ALWAYS_ON_MAX = fm.TREND_MAX + fm.TIMING_MAX + fm.VOLATILITY_MAX


def _series(values: list[float]) -> pd.Series:
    idx = pd.date_range("2023-01-02", periods=len(values), freq="B")
    return pd.Series(values, index=idx, dtype=float)


def _round_trip_pattern() -> list[float]:
    """รูปแบบราคา 1 รอบยาว 21 แท่ง — ขึ้นแล้วลงกลับมาที่เดิมพอดีเมื่อครบรอบ.

    ทุกดัชนีที่ห่างกัน 21 แท่ง (และ 63 แท่ง) จึงมีราคา **เท่ากันเป๊ะ**
    → ผลตอบแทนจริงของทั้งหน้าต่าง 1 เดือนและ 3 เดือน = 0.00% พอดี
    """
    up = [1.0 + 0.01 * j for j in range(11)]          # j=0..10 : 1.00 → 1.10
    down = [1.10 - (0.10 / 11.0) * j for j in range(1, 11)]  # j=11..20 : ลงกลับหา 1.00
    return up + down


def _round_trip_series(n: int = 260) -> pd.Series:
    pattern = _round_trip_pattern()
    return _series([100.0 * pattern[i % len(pattern)] for i in range(n)])


def _steady_growth_series(n: int = 260, daily: float = 0.001) -> pd.Series:
    return _series([100.0 * (1.0 + daily) ** i for i in range(n)])


class TestPeriodReturnHelper:
    """หน่วยย่อย: ``period_return_pct`` ต้องเป็นผลตอบแทนทบต้นของช่วง."""

    def test_round_trip_is_exactly_zero(self):
        """(ก) ขึ้นแล้วลงกลับที่เดิมเป๊ะ → 0.00% ห้ามเป็นบวก (บั๊กตัวนี้ตรง ๆ)."""
        closes = _round_trip_series()
        assert closes.iloc[-1] == pytest.approx(closes.iloc[-(_BARS_1M + 1)])
        assert fm.period_return_pct(closes, _BARS_1M) == pytest.approx(0.0, abs=1e-9)
        assert fm.period_return_pct(closes, _BARS_3M) == pytest.approx(0.0, abs=1e-9)

    def test_matches_hand_computed_compounding(self):
        """(ข) ขึ้นสม่ำเสมอวันละ 0.1% → ต้องตรงสูตรทบต้นที่คำนวณมือ."""
        closes = _steady_growth_series()
        expected_1m = ((1.001**_BARS_1M) - 1.0) * 100.0   # 2.1211% (สูตรเดิมได้ 2.1%)
        expected_3m = ((1.001**_BARS_3M) - 1.0) * 100.0   # 6.4993% (สูตรเดิมได้ 6.3%)
        assert fm.period_return_pct(closes, _BARS_1M) == pytest.approx(expected_1m, rel=1e-9)
        assert fm.period_return_pct(closes, _BARS_3M) == pytest.approx(expected_3m, rel=1e-9)
        # ผลรวมรายวันของสูตรเดิมคือ 2.1% พอดี ซึ่งต่ำกว่าความจริง — ต้องไม่ใช่ค่านี้
        assert fm.period_return_pct(closes, _BARS_1M) != pytest.approx(2.1, abs=1e-4)

    def test_shorter_than_window_is_nan_not_zero(self):
        """(ค) ข้อมูลสั้นกว่าหน้าต่าง → NaN (ไม่ใช่ 0 ซึ่งเท่ากับเดาว่า 'ไม่บวก')."""
        short = _series([100.0] * 10)
        assert math.isnan(fm.period_return_pct(short, _BARS_1M))
        # ขอบพอดี: ต้องมีอย่างน้อย bars+1 แท่งถึงจะคำนวณได้
        assert math.isnan(fm.period_return_pct(_series([100.0] * _BARS_1M), _BARS_1M))
        assert not math.isnan(fm.period_return_pct(_series([100.0] * (_BARS_1M + 1)), _BARS_1M))


class TestMomentumScoreExcludesUnknown:
    """หน้าต่างที่คำนวณไม่ได้ต้องถูกตัดออกจากทั้งคะแนนและเพดาน (เหมือน dividend)."""

    def test_both_windows_unknown_returns_none(self):
        score, max_pts = fm._momentum_score(float("nan"), float("nan"))
        assert score is None
        assert max_pts == 0

    def test_partial_window_only_counts_what_is_known(self):
        score, max_pts = fm._momentum_score(1.5, float("nan"))
        assert (score, max_pts) == (10, 10)
        score, max_pts = fm._momentum_score(-1.5, float("nan"))
        assert (score, max_pts) == (0, 10)

    def test_full_windows(self):
        assert fm._momentum_score(1.0, 1.0) == (20, fm.MOMENTUM_MAX)
        assert fm._momentum_score(0.0, 0.0) == (0, fm.MOMENTUM_MAX)
        assert fm._momentum_score(-1.0, 2.0) == (10, fm.MOMENTUM_MAX)


class TestScoreFromPricesUsesRealReturn:
    """เส้นทางจริงที่คะแนนเดินผ่าน — ตัวที่บั๊กเคยแจกคะแนนฟรี."""

    def test_round_trip_gets_no_free_momentum_points(self):
        """ราคากลับมาที่เดิม = ไม่มีโมเมนตัม → 0 คะแนน (เดิมได้ 20 จากผลรวมรายวัน)."""
        result = fm.score_from_prices("TEST", _round_trip_series(), div_yield=None)
        assert result["return_1m_pct"] == pytest.approx(0.0, abs=1e-6)
        assert result["return_3m_pct"] == pytest.approx(0.0, abs=1e-6)
        assert result["momentum_score"] == 0
        assert result["momentum_available"] is True
        assert result["momentum_max"] == fm.MOMENTUM_MAX
        assert result["max_score"] == _ALWAYS_ON_MAX + fm.MOMENTUM_MAX

    def test_steady_growth_reports_compound_return(self):
        result = fm.score_from_prices("TEST", _steady_growth_series(), div_yield=None)
        assert result["return_1m_pct"] == pytest.approx(round(((1.001**21) - 1) * 100, 2))
        assert result["return_3m_pct"] == pytest.approx(round(((1.001**63) - 1) * 100, 2))
        assert result["momentum_score"] == fm.MOMENTUM_MAX

    def test_arithmetic_sum_would_have_been_higher(self):
        """พิสูจน์ทิศทางของบั๊ก: ผลรวมรายวัน > ผลตอบแทนจริงเสมอเมื่อผันผวน."""
        closes = _round_trip_series()
        arithmetic = float(closes.pct_change().tail(_BARS_1M).sum()) * 100.0
        geometric = fm.period_return_pct(closes, _BARS_1M)
        assert arithmetic > 0.0, "fixture ต้องสร้างส่วนต่างจริง"
        assert geometric == pytest.approx(0.0, abs=1e-9)
        assert arithmetic > geometric


class TestSingleSourceOfTruth:
    """C7(ข) — สูตรผลตอบแทนของช่วงต้องมีที่เดียว.

    ก่อนแก้มีสองชุด: ``financial_model._period_return_pct`` (ที่เพิ่งเขียนในข้อ 1.5)
    กับ ``returns.calculate_period_returns`` ที่มีมาก่อน — หน้าต่างเท่ากันเป๊ะ
    (``RETURN_WINDOWS`` 1M=21, 3M=63) สูตรเดียวกัน guard "ข้อมูลสั้นกว่าหน้าต่าง"
    เดียวกัน ต่างกันแค่ ``financial_model`` มี guard เพิ่มอีก 3 ตัว
    → แก้ที่เดียวแล้วอีกที่ไม่ตาม = คะแนน DCA กับตาราง Returns เล่าคนละเรื่อง
    """

    def test_financial_model_reuses_the_returns_module(self):
        """ต้องเป็น**ฟังก์ชันตัวเดียวกัน** ไม่ใช่แค่ให้ผลเท่ากัน."""
        assert fm.period_return_pct is returns_mod.period_return_pct

    def test_no_duplicate_formula_left_behind(self):
        """ชื่อเดิมต้องหายไป ไม่ใช่ทิ้ง wrapper ที่ยังคำนวณเองซ้ำ."""
        assert not hasattr(fm, "_period_return_pct")
        src = inspect.getsource(fm)
        assert "iloc[-(bars + 1)]" not in src, "สูตรยังถูกคัดลอกไว้ใน financial_model"

    def test_score_windows_come_from_return_windows(self, monkeypatch):
        """หน้าต่าง 21/63 ต้องอ่านจาก ``RETURN_WINDOWS`` ไม่ใช่ตัวเลขฝังในโค้ด."""
        seen: list[int] = []
        real = returns_mod.period_return_pct

        def _spy(closes: pd.Series, bars: int) -> float:
            seen.append(bars)
            return real(closes, bars)

        monkeypatch.setattr(fm, "period_return_pct", _spy)
        fm.score_from_prices("TEST", _steady_growth_series(), div_yield=None)
        assert seen == [RETURN_WINDOWS["1M"], RETURN_WINDOWS["3M"]]

    def test_two_entry_points_agree_on_the_same_prices(self):
        """ตาราง Returns กับคะแนนโมเมนตัมต้องได้เลขเดียวกันจากราคาชุดเดียวกัน."""
        closes = _steady_growth_series()
        table = calculate_period_returns(pd.DataFrame({"VOO": closes}))
        assert float(table.loc["1M", "VOO"]) == pytest.approx(
            returns_mod.period_return_pct(closes, _BARS_1M), rel=1e-12
        )
        assert float(table.loc["3M", "VOO"]) == pytest.approx(
            returns_mod.period_return_pct(closes, _BARS_3M), rel=1e-12
        )


class TestPeriodReturnGuards:
    """C7(ค) — guard 3 ตัวที่ย้ายมาอยู่แหล่งเดียว ต้องมีเทสต์ครบทุกกิ่ง."""

    def test_non_positive_bars_is_nan(self):
        """หน้าต่าง 0 หรือติดลบ = คำถามไร้ความหมาย → NaN ไม่ใช่ 0.0%.

        (ถ้าไม่ดัก ``bars=0`` จะได้ ``end/end - 1 = 0.0%`` ซึ่งอ่านเป็น
        "ไม่ขึ้นไม่ลง" ทั้งที่ไม่ได้วัดอะไรเลย)
        """
        closes = _steady_growth_series()
        assert math.isnan(returns_mod.period_return_pct(closes, 0))
        assert math.isnan(returns_mod.period_return_pct(closes, -5))

    def test_zero_base_price_is_nan_not_infinity(self):
        """ราคาอ้างอิง 0 → NaN (ไม่ใช่ ``inf`` ที่หลุดเข้าตาราง/คะแนนได้)."""
        values = [100.0] * 40
        values[-(_BARS_1M + 1)] = 0.0
        assert math.isnan(returns_mod.period_return_pct(_series(values), _BARS_1M))

    def test_negative_base_price_is_nan(self):
        """ราคาติดลบ = ข้อมูลผิด ถ้าไม่ดักจะได้ตัวเลขที่ 'ดูสมจริง' แต่มั่ว."""
        values = [100.0] * 40
        values[-(_BARS_1M + 1)] = -5.0
        naive = (values[-1] / values[-(_BARS_1M + 1)] - 1.0) * 100.0
        assert naive == pytest.approx(-2100.0)  # ตัวเลขที่จะหลุดออกไปถ้าไม่มี guard
        assert math.isnan(returns_mod.period_return_pct(_series(values), _BARS_1M))

    def test_nan_base_price_is_nan(self):
        values = [100.0] * 40
        values[-(_BARS_1M + 1)] = float("nan")
        assert math.isnan(returns_mod.period_return_pct(_series(values), _BARS_1M))

    def test_nan_end_price_is_nan(self):
        """ราคาล่าสุดหาย → NaN ห้ามย้อนไปหยิบราคาเก่ามาสวมแทน."""
        values = [100.0] * 40
        values[-1] = float("nan")
        assert math.isnan(returns_mod.period_return_pct(_series(values), _BARS_1M))


class TestCalculatePeriodReturnsInheritsGuards:
    """guard ต้องคุ้มถึงตาราง Returns ด้วย — เดิมมีแต่ใน financial_model."""

    def _frame(self, values: list[float]) -> pd.DataFrame:
        return pd.DataFrame({"VOO": _series(values)})

    def test_zero_base_price_becomes_nan_not_inf(self):
        values = [100.0] * 40
        values[-(_BARS_1M + 1)] = 0.0
        out = calculate_period_returns(self._frame(values))
        assert math.isnan(float(out.loc["1M", "VOO"])), "0/ฐาน → ต้องเป็น NaN ไม่ใช่ inf"

    def test_negative_base_price_becomes_nan(self):
        values = [100.0] * 40
        values[-(_BARS_1M + 1)] = -5.0
        out = calculate_period_returns(self._frame(values))
        assert math.isnan(float(out.loc["1M", "VOO"]))

    def test_short_history_still_nan_per_window(self):
        """ตัวเลขเดิมต้องไม่เปลี่ยน: หน้าต่างที่ข้อมูลไม่พอยังเป็น NaN ทุกช่อง."""
        out = calculate_period_returns(self._frame([100.0 + i for i in range(40)]))
        assert not math.isnan(float(out.loc["1M", "VOO"]))
        for period in ("3M", "6M", "1Y", "3Y", "5Y", "10Y"):
            assert math.isnan(float(out.loc[period, "VOO"]))

    def test_mid_column_gap_counts_the_tickers_own_bars(self):
        """ช่องว่าง**กลางทาง** ไม่ทำให้ทั้งคอลัมน์พัง — แต่หน้าต่างนับจากแท่งจริงของ ticker เอง.

        แทนที่ ``test_forward_fill_behaviour_unchanged`` เดิม (G7 —
        AUDIT_ROUND2_2026-08-07): ``ffill`` แก้ช่องว่างกลางทางได้ก็จริง แต่มันเติม
        **หางคอลัมน์**ของ ticker ที่ผู้ให้ข้อมูลหยุดส่งไปด้วย จนตัวตั้งกับตัวหาร
        กลายเป็นราคาเดียวกัน ⇒ 0.0000% ตัวเลขที่ถูกกุขึ้น
        ตัวเลขที่ตรึงจึงเปลี่ยนตาม: หน้าต่าง 21 แท่งคือ 21 แท่ง**ของ ticker นั้น**
        ไม่ใช่ 21 แถวของทั้งเฟรมที่ยืมวันของเพื่อนมา — นิยามเดียวกับที่
        ``financial_model.score_from_prices`` ใช้ (มัน ``dropna()`` ก่อนเสมอ)
        """
        values = [100.0 + i for i in range(40)]
        values[-3] = float("nan")
        real = [v for v in values if not math.isnan(v)]
        out = calculate_period_returns(self._frame(values))
        expected = (real[-1] / real[-(_BARS_1M + 1)] - 1.0) * 100.0
        assert float(out.loc["1M", "VOO"]) == pytest.approx(expected, rel=1e-12)
        assert not math.isnan(float(out.loc["1M", "VOO"])), "ช่องว่างกลางทางต้องไม่ทำให้ทั้งช่องหาย"


class TestTrailingGapIsNeverZeroPercent:
    """G7 (AUDIT_ROUND2_2026-08-07) — ticker ที่ผู้ให้ข้อมูลหยุดส่งแท่ง ห้ามอ่านเป็น "ราคาไม่ขยับ".

    ``data/fetcher.fetch_adjusted_close_data`` ตัดทิ้งเฉพาะแถวที่ NaN **ทุกคอลัมน์**
    (``dropna(how="all")``) หางคอลัมน์เดียวที่หายจึงรอดมาถึงตาราง Returns เสมอ
    เดิม ``calculate_period_returns`` ``ffill()`` ทั้งเฟรมก่อนคำนวณ ⇒

    - ช่องว่างยาวกว่าหน้าต่าง: ตัวตั้ง = ตัวหาร = ราคาจริงแท่งสุดท้าย → **0.0000% พอดี**
    - ช่องว่างสั้นกว่าหน้าต่าง: ผลตอบแทนถูกหดลงตามจำนวนแท่งที่ถูกเติม
      (10 แท่ง = 1.11% แทน 2.12%) — อันตรายกว่าเพราะยังดูสมเหตุสมผล
    - guard ``pd.isna(end)`` ใน ``period_return_pct`` กลายเป็นโค้ดตายบนเส้นทางนี้
    """

    def _frame_with_dead_tail(self, gap: int, rows: int = 400) -> tuple[pd.DataFrame, pd.Series]:
        """VOO ปกติ + X ที่หยุดส่งแท่ง ``gap`` แท่งท้าย (ยังเป็นคอลัมน์อยู่ ไม่ได้หายไป)."""
        healthy = _steady_growth_series(rows)
        dead = healthy.copy()
        if gap:
            dead.iloc[-gap:] = float("nan")
        return pd.DataFrame({"VOO": healthy, "X": dead}), dead

    def test_gap_longer_than_window_is_not_zero_percent(self):
        """หลักฐานตรง ๆ ของบั๊ก: ขาด 40 แท่ง → ตารางพิมพ์ 0.0000% (ราคาจริงขึ้นทั้งเดือน)."""
        frame, dead = self._frame_with_dead_tail(40)
        out = calculate_period_returns(frame)
        got = float(out.loc["1M", "X"])
        assert got != pytest.approx(0.0, abs=1e-9), (
            "ffill เติมหางคอลัมน์จนตัวตั้ง = ตัวหาร → 'ดึงราคาไม่ได้' ถูกอ่านเป็น 'ราคาไม่ขยับเลยทั้งเดือน'"
        )
        assert got == pytest.approx(
            returns_mod.period_return_pct(dead.dropna(), _BARS_1M), rel=1e-12
        ), "ต้องเป็นผลตอบแทนของแท่งจริงของ X เอง"
        assert float(out.loc["1M", "VOO"]) == pytest.approx(
            returns_mod.period_return_pct(frame["VOO"], _BARS_1M), rel=1e-12
        ), "ticker ที่ข้อมูลปกติต้องไม่เปลี่ยนค่า"

    @pytest.mark.parametrize("gap", [1, 5, 10, 20, 21, 40, 90])
    def test_any_trailing_gap_uses_the_tickers_own_bars(self, gap):
        """ทุกความยาวช่องว่าง: ค่าต้องเท่ากับผลตอบแทนของแท่งจริง ไม่ถูกหดลงตามแท่งที่เติม."""
        frame, dead = self._frame_with_dead_tail(gap)
        out = calculate_period_returns(frame)
        expected = returns_mod.period_return_pct(dead.dropna(), _BARS_1M)
        assert float(out.loc["1M", "X"]) == pytest.approx(expected, rel=1e-12)

    def test_column_that_stops_before_the_window_is_nan(self):
        """เหลือแท่งจริงไม่ถึงหน้าต่าง → NaN ("ไม่รู้") ไม่ใช่ 0.0%.

        เดิม ffill ยืดคอลัมน์ให้ยาวเท่าเฟรมเสมอ ``len(closes) > bars`` จึงเป็นจริง
        ทั้งที่ ticker นั้นมีแท่งจริงแค่ 15 แท่ง → ได้ 0.0000% ที่ดูเหมือนคำตอบ
        """
        healthy = _steady_growth_series(400)
        dead = healthy.copy()
        dead.iloc[15:] = float("nan")
        out = calculate_period_returns(pd.DataFrame({"VOO": healthy, "X": dead}))
        for period in RETURN_WINDOWS:
            assert math.isnan(float(out.loc[period, "X"])), f"{period} ต้องเป็น NaN ไม่ใช่ตัวเลข"

    def test_all_nan_column_is_nan_everywhere(self):
        """คอลัมน์ที่ไม่มีแท่งจริงเลย = ดึงไม่สำเร็จ → NaN ทุกช่อง ห้ามเป็น 0."""
        healthy = _steady_growth_series(400)
        dead = pd.Series(float("nan"), index=healthy.index)
        out = calculate_period_returns(pd.DataFrame({"VOO": healthy, "X": dead}))
        for period in RETURN_WINDOWS:
            assert math.isnan(float(out.loc[period, "X"]))
        assert not math.isnan(float(out.loc["1M", "VOO"]))

    def test_infinite_price_is_not_a_real_bar(self):
        """``inf`` ไม่ใช่ราคา — ถ้าปล่อยผ่านจะได้ ``inf``/-100% ที่ล้ม JSONResponse ทั้ง endpoint."""
        healthy = _steady_growth_series(400)
        broken = healthy.copy()
        broken.iloc[-1] = float("inf")
        out = calculate_period_returns(pd.DataFrame({"X": broken}))
        got = float(out.loc["1M", "X"])
        assert math.isfinite(got), "inf หลุดเข้าตาราง Returns"
        assert got == pytest.approx(
            returns_mod.period_return_pct(broken.iloc[:-1], _BARS_1M), rel=1e-12
        )

    def test_no_ffill_left_on_this_path(self):
        """กันการกลับไปใช้ ffill บนเส้นทางที่ผลลัพธ์ไปโชว์เป็นผลตอบแทน.

        ตรวจเฉพาะ**โค้ด** — ``ast.unparse`` ตัดคอมเมนต์และ docstring ทิ้งก่อน
        เพราะ docstring ที่อธิบายว่า "ทำไมถึงห้าม ffill ที่นี่" ต้องเขียนคำนั้นออกมาได้
        (สำนวนเดียวกับ ``test_source_has_no_or_zero_idiom``)
        """
        tree = ast.parse(textwrap.dedent(inspect.getsource(calculate_period_returns)))
        func = tree.body[0]
        body = func.body
        if isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body = body[1:]  # docstring
        code = "\n".join(ast.unparse(node) for node in body)
        assert "ffill" not in code, "ffill กลับมาอยู่ในเส้นทางตาราง Returns แล้ว"

    def test_table_and_score_agree_on_a_ticker_with_gaps(self):
        """ตาราง Returns กับคะแนนโมเมนตัมต้องเล่าเรื่องเดียวกันแม้ข้อมูลมีรู (C7)."""
        healthy = _steady_growth_series(400)
        dead = healthy.copy()
        dead.iloc[-40:] = float("nan")
        dead.iloc[100] = float("nan")
        table = calculate_period_returns(pd.DataFrame({"X": dead}))
        score = fm.score_from_prices("X", dead, div_yield=None)
        assert score["return_1m_pct"] == pytest.approx(round(float(table.loc["1M", "X"]), 2))
        assert score["return_3m_pct"] == pytest.approx(round(float(table.loc["3M", "X"]), 2))


class TestMomentumMissingIsNotZero:
    """C7(ก) — "ไม่มีข้อมูลโมเมนตัม" ต้องแยกออกจาก "ได้ 0 คะแนน"."""

    def test_source_has_no_or_zero_idiom(self):
        """``or 0`` เป็นสำนวนต้องห้ามของโปรเจกต์บนเส้นทางตัวเลข (กฎเดียวกับข้อ 1.6).

        ตรวจเฉพาะ**โค้ด** — ตัดคอมเมนต์ทิ้งก่อน เพราะคอมเมนต์ที่อธิบายว่า
        "ทำไมถึงห้ามใช้ ``or 0``" ต้องเขียนสำนวนนั้นออกมาได้
        """
        code = "\n".join(
            line.split("#", 1)[0] for line in inspect.getsource(fm.score_from_prices).splitlines()
        )
        assert " or 0" not in code
        assert "if mom_s is not None else 0" in code, "ต้องเขียน 'ไม่มีข้อมูล' ให้ชัดเจน"

    def test_unavailable_momentum_shrinks_the_denominator(self, monkeypatch):
        """เหตุผลที่บวก 0 ได้: ``max_score`` หดตาม — ไม่ใช่เพราะ 0 เป็นคำตอบที่ถูก."""
        monkeypatch.setattr(fm, "period_return_pct", lambda closes, bars: float("nan"))
        result = fm.score_from_prices("TEST", _steady_growth_series(), div_yield=None)

        assert result["momentum_score"] is None
        assert result["momentum_available"] is False
        assert result["momentum_max"] == 0
        assert result["return_1m_pct"] is None and result["return_3m_pct"] is None
        # เพดานไม่มีโควตาโมเมนตัม และคะแนนรวมไม่ถูกบวก 0 เข้ามาแบบเงียบ ๆ
        assert result["max_score"] == _ALWAYS_ON_MAX
        assert (
            result["total_score"]
            == result["trend_score"] + result["timing_score"] + result["volatility_score"]
        )
        assert result["total_pct"] == round(
            result["total_score"] * 100.0 / result["max_score"], 1
        )

    def test_zero_momentum_still_counts_against_the_ceiling(self):
        """ตรงข้ามกัน: มีข้อมูลแล้วได้ 0 คะแนน → เพดานยังนับโควตาโมเมนตัมเต็ม."""
        result = fm.score_from_prices("TEST", _round_trip_series(), div_yield=None)
        assert result["momentum_score"] == 0
        assert result["momentum_available"] is True
        assert result["max_score"] == _ALWAYS_ON_MAX + fm.MOMENTUM_MAX
