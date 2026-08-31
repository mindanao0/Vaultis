# -*- coding: utf-8 -*-
"""คุมการอ่าน dividend yield และคะแนนปันผล (AUDIT.md M15).

บั๊กเดิม: `_dividend_yield` เดาหน่วยเองด้วย ``value / 100 if value > 1 else value``
→ ETF ที่ yield ต่ำกว่า 1% ถูกอ่านเป็นหลักสิบเปอร์เซ็นต์ แล้วได้คะแนนปันผลเต็ม
ไฟล์นี้มีอยู่เพื่อไม่ให้การเดาหน่วยกลับมาอีก
"""

import pytest

from analysis import financial_model as fm


class TestNormalizeDividendYield:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            (1.07, 0.0107),   # VOO
            (3.3, 0.033),     # SCHD
            (1.6, 0.016),     # XLV
            (0.0, 0.0),       # GLDM — ไม่จ่ายปันผล
        ],
    )
    def test_percent_input_becomes_fraction(self, raw, expected):
        assert fm._normalize_dividend_yield(raw) == pytest.approx(expected)

    def test_yield_below_one_percent_is_not_mistaken_for_fraction(self):
        """M15: QQQM yield 0.43% ต้องได้ 0.0043 ไม่ใช่ 0.43 (=43%)."""
        assert fm._normalize_dividend_yield(0.43) == pytest.approx(0.0043)

    @pytest.mark.parametrize("raw", [None, "", "abc", object()])
    def test_missing_or_unparsable_returns_none(self, raw):
        assert fm._normalize_dividend_yield(raw) is None

    @pytest.mark.parametrize("raw", [-0.01, -5])
    def test_negative_returns_none(self, raw):
        assert fm._normalize_dividend_yield(raw) is None

    def test_implausible_yield_returns_none_instead_of_guessing(self):
        """yield > 100% = ข้อมูลผิด → ตัดคะแนนปันผลออก ไม่เดาหน่วยให้ (C1)."""
        assert fm._normalize_dividend_yield(101.0) is None
        assert fm._normalize_dividend_yield(100.0) == pytest.approx(1.0)


class TestDividendScore:
    @pytest.mark.parametrize(
        "div_yield, expected",
        [
            (0.05, 10),    # > 4%
            (0.0401, 10),
            (0.04, 5),     # ขอบ: ไม่เกิน 4% → ชั้นถัดไป
            (0.03, 5),
            (0.0201, 5),
            (0.02, 2),     # ขอบ: ไม่เกิน 2% → ชั้นถัดไป
            (0.0043, 2),   # QQQM ของจริง
            (0.0, 0),
        ],
    )
    def test_score_tiers(self, div_yield, expected):
        assert fm._dividend_score(div_yield) == expected

    def test_qqqm_case_end_to_end(self):
        """0.43 ดิบ → 0.43% → คะแนน 2 (เดิมได้ 10 = บั๊ก M15)."""
        assert fm._dividend_score(fm._normalize_dividend_yield(0.43)) == 2


class TestDividendYieldFromTicker:
    def test_reads_and_normalizes_info(self, monkeypatch):
        monkeypatch.setattr(
            fm.yf, "Ticker", lambda _s: type("T", (), {"info": {"dividendYield": 0.43}})()
        )
        assert fm._dividend_yield("QQQM") == pytest.approx(0.0043)

    def test_network_failure_returns_none(self, monkeypatch):
        def _boom(_s):
            raise RuntimeError("network down")

        monkeypatch.setattr(fm.yf, "Ticker", _boom)
        assert fm._dividend_yield("VOO") is None

    def test_missing_field_returns_none(self, monkeypatch):
        monkeypatch.setattr(fm.yf, "Ticker", lambda _s: type("T", (), {"info": {}})())
        assert fm._dividend_yield("VOO") is None


class TestScoreExcludesDividendWhenUnavailable:
    def test_max_score_drops_to_90_without_dividend(self):
        """ไม่มีข้อมูลปันผล = ตัดออกจากตัวหาร ไม่ใช่ให้ 0 (ห้ามลงโทษข้อมูลที่หายไป)."""
        import numpy as np
        import pandas as pd

        closes = pd.Series(
            np.linspace(100.0, 130.0, 260),
            index=pd.date_range("2025-01-01", periods=260, freq="B"),
        )
        with_div = fm.score_from_prices("TEST", closes, div_yield=0.033)
        without = fm.score_from_prices("TEST", closes, div_yield=None)

        # 90 (Trend+Timing+Momentum) + 10 (Volatility เสมอ) + 10 (Dividend ถ้ามี) — ดู
        # test_new_score_dimensions.py สำหรับมิติ Valuation/RelStrength/Expense ที่เพิ่มมาใหม่
        assert with_div["max_score"] == 110
        assert without["max_score"] == 100
        assert without["dividend_score"] == 0
        assert without["dividend_available"] is False
