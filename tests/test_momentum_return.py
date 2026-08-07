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

import inspect
import math
import sys
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
        assert result["max_score"] == fm.TREND_MAX + fm.TIMING_MAX + fm.MOMENTUM_MAX

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

    def test_forward_fill_behaviour_unchanged(self):
        """ยัง ffill ก่อนคำนวณเหมือนเดิม — ช่องว่างกลางทางไม่ทำให้ทั้งคอลัมน์พัง."""
        values = [100.0 + i for i in range(40)]
        values[-3] = float("nan")
        out = calculate_period_returns(self._frame(values))
        expected = (values[-1] / values[-(_BARS_1M + 1)] - 1.0) * 100.0
        assert float(out.loc["1M", "VOO"]) == pytest.approx(expected, rel=1e-12)


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
        assert result["max_score"] == fm.TREND_MAX + fm.TIMING_MAX
        assert result["total_score"] == result["trend_score"] + result["timing_score"]
        assert result["total_pct"] == round(
            result["total_score"] * 100.0 / result["max_score"], 1
        )

    def test_zero_momentum_still_counts_against_the_ceiling(self):
        """ตรงข้ามกัน: มีข้อมูลแล้วได้ 0 คะแนน → เพดานยังนับโควตาโมเมนตัมเต็ม."""
        result = fm.score_from_prices("TEST", _round_trip_series(), div_yield=None)
        assert result["momentum_score"] == 0
        assert result["momentum_available"] is True
        assert result["max_score"] == fm.TREND_MAX + fm.TIMING_MAX + fm.MOMENTUM_MAX
