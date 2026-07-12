# -*- coding: utf-8 -*-
"""เทสต์คณิตที่เกี่ยวกับเงินโดยตรง — allocation, alert levels, ตัวชี้วัด, พอร์ต.

ทุกเทสต์ใช้ข้อมูลสังเคราะห์ ไม่ยิง network (AUDIT.md L1: เดิมคณิตเงินไม่มีเทสต์เลย)
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

from analysis.ai_advisor import _suggest_alert_levels
from analysis.financial_model import calculate_allocation, score_from_prices
from analysis.ta_compat import ta
from technical import signal_rules


def _score(ticker: str, pct: float, data_ok: bool = True) -> dict:
    return {"ticker": ticker, "total_pct": pct, "data_ok": data_ok}


def _series_from(values: list[float]) -> pd.Series:
    idx = pd.date_range("2023-01-02", periods=len(values), freq="B")
    return pd.Series(values, index=idx, dtype=float)


class TestUnifiedScore:
    """คะแนนต้องมาจากสูตรเดียว และสอดคล้องกับ signal_rules (AUDIT.md C2)."""

    @staticmethod
    def _uptrend_then_dip(n: int = 300, dip_pct: float = 0.10) -> pd.Series:
        """ขาขึ้นยาว แล้วย่อแรงช่วงท้าย (RSI ต่ำ แต่ยังเหนือ MA200)."""
        rising = list(np.linspace(100.0, 180.0, n - 12))
        peak = rising[-1]
        dip = list(np.linspace(peak, peak * (1 - dip_pct), 12))
        return _series_from(rising + dip)

    def test_dip_in_uptrend_scores_well_and_agrees_with_signal_rules(self):
        result = score_from_prices("TEST", self._uptrend_then_dip())
        assert result["data_ok"] is True
        assert result["price"] >= result["ma200"], "fixture ต้องยังอยู่เหนือ MA200"
        # จังหวะย่อในขาขึ้น = คะแนน timing สูง และสัญญาณกลางเป็น ACCUMULATE/BULLISH
        assert result["timing_score"] >= 20
        assert result["technical_signal"] in {signal_rules.ACCUMULATE, signal_rules.BULLISH}
        assert "sell" not in result["signal"].lower()

    def test_downtrend_scores_low(self):
        falling = _series_from(list(np.linspace(200.0, 100.0, 300)))
        result = score_from_prices("TEST", falling)
        assert result["price"] < result["ma200"]
        assert result["trend_score"] == 0
        assert result["total_pct"] < 40

    def test_score_is_bounded_0_to_100(self):
        for series in (self._uptrend_then_dip(), _series_from(list(np.linspace(200.0, 100.0, 300)))):
            result = score_from_prices("TEST", series)
            assert 0 <= result["total_pct"] <= 100

    def test_dividend_absent_reduces_max_score_not_the_result(self):
        series = self._uptrend_then_dip()
        with_div = score_from_prices("TEST", series, div_yield=0.05)
        without_div = score_from_prices("TEST", series, div_yield=None)
        assert with_div["max_score"] == 100
        assert without_div["max_score"] == 90
        assert without_div["dividend_available"] is False

    def test_insufficient_history_raises_not_zero_score(self):
        """ข้อมูลไม่พอต้อง raise ให้ผู้เรียกแปลงเป็น NO DATA ห้ามคืนคะแนน 0 (C1)."""
        with pytest.raises(ValueError):
            score_from_prices("TEST", _series_from([100.0] * 50))


class TestCalculateAllocation:
    def test_allocation_never_exceeds_budget(self):
        scores = {
            "VOO": _score("VOO", 75.0),
            "SCHD": _score("SCHD", 65.0),
            "QQQM": _score("QQQM", 45.0),
        }
        allocation = calculate_allocation(scores, 5000.0)
        total = sum(item["amount_thb"] for item in allocation.values())
        assert total <= 5000.0

    def test_money_follows_score_monotonically(self):
        """คะแนนสูงกว่า = ได้เงินมากกว่า เสมอ.

        บั๊กเดิม: แบ่งงบเป็นก้อนต่อกลุ่ม (60%/30%) ทำให้ตัวคะแนนต่ำสุดที่อยู่คนเดียว
        ในกลุ่ม Buy ได้เงินมากกว่าตัวคะแนนสูงสุดที่ต้องหารกันในกลุ่ม Strong Buy
        (ของจริง: GLDM 42.9 ได้ 1,500 บาท ขณะที่ VOO 100 ได้ 800 บาท)
        """
        scores = {
            "VOO": _score("VOO", 100.0),
            "SCHD": _score("SCHD", 85.7),
            "QQQM": _score("QQQM", 100.0),
            "XLV": _score("XLV", 78.6),
            "GLDM": _score("GLDM", 42.9),
        }
        allocation = calculate_allocation(scores, 5000.0)
        ranked = sorted(scores, key=lambda t: scores[t]["total_pct"], reverse=True)
        amounts = [allocation[t]["amount_thb"] for t in ranked if t in allocation]
        assert amounts == sorted(amounts, reverse=True), "เงินต้องไหลตามคะแนน"
        assert allocation["VOO"]["amount_thb"] > allocation["GLDM"]["amount_thb"]

    def test_higher_score_gets_more_money(self):
        scores = {"VOO": _score("VOO", 80.0), "SCHD": _score("SCHD", 62.0)}
        allocation = calculate_allocation(scores, 10000.0)
        assert allocation["VOO"]["amount_thb"] > allocation["SCHD"]["amount_thb"]

    def test_no_data_ticker_never_gets_money(self):
        """ข้อมูลพังต้องไม่ได้รับเงิน — เดิมกลายเป็นคะแนน 0 แล้วยังหลุดเข้าโครงสร้าง (C1)."""
        scores = {
            "VOO": _score("VOO", 70.0),
            "GLDM": _score("GLDM", 0.0, data_ok=False),
        }
        allocation = calculate_allocation(scores, 5000.0)
        assert "GLDM" not in allocation
        assert "VOO" in allocation

    def test_ticker_without_score_is_skipped(self):
        scores = {"VOO": _score("VOO", 70.0), "XLV": {"ticker": "XLV", "total_pct": None}}
        allocation = calculate_allocation(scores, 5000.0)
        assert "XLV" not in allocation

    def test_all_low_scores_allocate_nothing(self):
        """คะแนนต่ำกว่า 25 (Caution/Avoid) = ถือเงินสด ไม่จัดสรร."""
        scores = {"VOO": _score("VOO", 10.0), "SCHD": _score("SCHD", 5.0)}
        assert calculate_allocation(scores, 5000.0) == {}

    def test_strong_buy_tier_beats_buy_tier_at_similar_score(self):
        """ตัวคูณตามกลุ่มยังทำงาน: Strong Buy ได้เปรียบ Buy ที่คะแนนใกล้กัน."""
        scores = {"A": _score("A", 70.0), "B": _score("B", 69.0)}
        allocation = calculate_allocation(scores, 10000.0)
        assert allocation["A"]["group"] == "Strong Buy"
        assert allocation["B"]["group"] == "Buy"
        assert allocation["A"]["amount_thb"] > allocation["B"]["amount_thb"] * 1.3


class TestSuggestAlertLevels:
    """ระดับ alert ต้องมีเหตุผลเชิงโครงสร้างเสมอ — เดิมให้ AI เดาราคาเอง (C3/M8)."""

    BASE = {
        "ticker": "VOO",
        "price": 500.0,
        "rsi14": 55.0,
        "ma50": 490.0,
        "ma200": 470.0,
        "support": 480.0,
        "resistance": 520.0,
    }

    def test_buy_below_current_below_warning(self):
        result = _suggest_alert_levels(self.BASE)
        assert result["buy_alert"] < result["current_price"] < result["warning_alert"]

    def test_buy_uses_nearest_support_below_price(self):
        result = _suggest_alert_levels(self.BASE)
        # ค่าที่ต่ำกว่าราคาและใกล้ที่สุดคือ MA50 (490) ไม่ใช่ support 480 หรือ MA200 470
        assert result["buy_alert"] == pytest.approx(490.0)

    def test_invariant_holds_when_everything_is_above_price(self):
        """ราคาต่ำกว่าทุกแนวรับ (ขาลงหนัก) ก็ยังต้องได้ buy < price < warning."""
        snapshot = {**self.BASE, "price": 450.0, "support": 460.0, "ma50": 490.0, "ma200": 470.0}
        result = _suggest_alert_levels(snapshot)
        assert result["buy_alert"] < 450.0 < result["warning_alert"]

    def test_invariant_holds_at_all_time_high(self):
        """ราคาทะลุแนวต้าน (ทำ new high) ก็ยังต้องได้ warning เหนือราคา."""
        snapshot = {**self.BASE, "price": 530.0, "resistance": 520.0}
        result = _suggest_alert_levels(snapshot)
        assert result["warning_alert"] > 530.0
        assert result["buy_alert"] < 530.0

    def test_overbought_rsi_mentioned_in_warning(self):
        snapshot = {**self.BASE, "rsi14": 78.0}
        assert "overbought" in _suggest_alert_levels(snapshot)["warning_reason"].lower()


class TestIndicators:
    """ช่วง warmup ต้องเป็น NaN — เดิม fill 100/0 ทำให้เกิด Overbought/Oversold ปลอม (M1)."""

    @staticmethod
    def _rising_series(n: int = 300) -> pd.Series:
        idx = pd.date_range("2024-01-01", periods=n, freq="B")
        return pd.Series(np.linspace(100.0, 200.0, n), index=idx)

    def test_rsi_warmup_is_nan_not_a_signal(self):
        rsi = ta.rsi(self._rising_series(), length=14)
        assert rsi.iloc[:13].isna().all(), "ช่วง warmup ของ RSI ต้องเป็น NaN"

    def test_rsi_stays_in_range(self):
        rsi = ta.rsi(self._rising_series(), length=14).dropna()
        assert ((rsi >= 0) & (rsi <= 100)).all()

    def test_sma_warmup_is_nan(self):
        sma = ta.sma(self._rising_series(), length=50)
        assert sma.iloc[:49].isna().all()

    def test_macd_columns_match_expected_names(self):
        macd = ta.macd(self._rising_series(), fast=12, slow=26, signal=9)
        assert {"MACD_12_26_9", "MACDs_12_26_9", "MACDh_12_26_9"} <= set(macd.columns)

    def test_macd_histogram_is_line_minus_signal(self):
        macd = ta.macd(self._rising_series()).dropna()
        expected = macd["MACD_12_26_9"] - macd["MACDs_12_26_9"]
        pd.testing.assert_series_equal(macd["MACDh_12_26_9"], expected, check_names=False)

    def test_bbands_upper_above_lower(self):
        bb = ta.bbands(self._rising_series(), length=20, std=2).dropna()
        assert (bb["BBU_20_2.0"] >= bb["BBL_20_2.0"]).all()


class TestPortfolioMissingPrice:
    """ราคาที่ดึงไม่ได้ต้องเป็น NaN + ธง Price OK — ห้ามกลายเป็น 0 แล้วโชว์ขาดทุน -100% (C1)."""

    def test_missing_price_does_not_become_minus_100_percent(self, monkeypatch):
        import portfolio.tracker as tracker

        tx = pd.DataFrame(
            {
                "date": pd.to_datetime(["2025-01-02", "2025-01-03"]),
                "ticker": ["VOO", "GLDM"],
                "shares": [1.0, 2.0],
                "price_usd": [500.0, 60.0],
                "fx_rate_thb": [34.0, 34.0],
                "amount_thb": [17000.0, 4080.0],
                "fee_thb": [0.0, 0.0],
                "note": ["", ""],
            }
        )
        monkeypatch.setattr(tracker, "_load_transactions", lambda: tx)
        # VOO มีราคา, GLDM ดึงไม่ได้
        monkeypatch.setattr(tracker, "_get_latest_prices", lambda tickers: {"VOO": 520.0})
        monkeypatch.setattr(tracker, "_get_usdthb_rate", lambda: 34.0)

        holdings = tracker.get_portfolio_summary()
        gldm = holdings[holdings["Ticker"] == "GLDM"].iloc[0]
        voo = holdings[holdings["Ticker"] == "VOO"].iloc[0]

        assert bool(voo["Price OK"]) is True
        assert bool(gldm["Price OK"]) is False
        assert pd.isna(gldm["Current Price (USD)"])
        assert pd.isna(gldm["Return (%)"])  # ไม่ใช่ -100.0

        summary = tracker.get_total_summary()
        assert summary["missing_prices"] == ["GLDM"]
        # กำไรคิดจากตัวที่มีราคาเท่านั้น: (520-500) * 1 share * 34 = 680 บาท
        assert summary["total_pnl_thb"] == pytest.approx(680.0)
        assert summary["total_return_pct"] > 0  # ไม่ใช่ค่าติดลบมหาศาลจากราคา 0
