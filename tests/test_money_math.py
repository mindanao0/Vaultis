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
    """นโยบาย: สัดส่วนเป้าหมายเป็นฐาน + คะแนนเป็นตัวปรับน้ำหนัก (0.6–1.4 เท่า).

    หลักที่ต้องคุ้มครอง: (1) ไม่เกินงบ (2) ไม่ตัดสินทรัพย์ใดออกจากพอร์ต
    (3) คะแนนสูงกว่า → ได้มากกว่าเป้าของตัวเอง (4) ข้อมูลพัง → ไม่ได้เงิน
    """

    TARGETS = {"VOO": 0.35, "SCHD": 0.25, "QQQM": 0.20, "XLV": 0.10, "GLDM": 0.10}

    def test_allocation_never_exceeds_budget(self):
        scores = {t: _score(t, 60.0) for t in self.TARGETS}
        allocation = calculate_allocation(scores, 5000.0, target_weights=self.TARGETS)
        assert sum(i["amount_thb"] for i in allocation.values()) <= 5000.0

    def test_uses_full_budget_when_divisible(self):
        """เศษจากการปัดหลักร้อยต้องถูกแจกคืน ไม่หายเงียบ."""
        scores = {t: _score(t, 60.0) for t in self.TARGETS}
        allocation = calculate_allocation(scores, 5000.0, target_weights=self.TARGETS)
        assert sum(i["amount_thb"] for i in allocation.values()) == 5000.0

    def test_weak_asset_still_gets_money(self):
        """หัวใจของนโยบาย (ข): สินทรัพย์ที่สัญญาณอ่อนยังต้องได้ซื้อ — ไม่ตัดออกจากพอร์ต.

        (แบบคะแนนล้วนเดิม GLDM คะแนน 20 จะไม่ได้เงินเลย = market timing)
        """
        scores = {
            "VOO": _score("VOO", 72.0),
            "SCHD": _score("SCHD", 75.0),
            "QQQM": _score("QQQM", 80.0),
            "XLV": _score("XLV", 72.0),
            "GLDM": _score("GLDM", 20.0),
        }
        allocation = calculate_allocation(scores, 5000.0, target_weights=self.TARGETS)
        assert "GLDM" in allocation
        assert allocation["GLDM"]["amount_thb"] > 0
        # แต่ต้องได้ "น้อยกว่าเป้าหมาย" เพราะคะแนนต่ำ
        assert allocation["GLDM"]["tilt"] < 1.0

    def test_strong_asset_gets_more_than_its_target(self):
        scores = {
            "VOO": _score("VOO", 40.0),
            "SCHD": _score("SCHD", 95.0),
            "QQQM": _score("QQQM", 50.0),
            "XLV": _score("XLV", 50.0),
            "GLDM": _score("GLDM", 50.0),
        }
        allocation = calculate_allocation(scores, 10000.0, target_weights=self.TARGETS)
        assert allocation["SCHD"]["tilt"] > 1.0, "คะแนนสูงต้องได้มากกว่าเป้า"
        assert allocation["VOO"]["tilt"] < 1.0, "คะแนนต่ำต้องได้น้อยกว่าเป้า"

    def test_big_target_still_beats_small_target_at_similar_score(self):
        """VOO เป้า 35% ต้องยังได้เงินมากกว่า GLDM เป้า 10% เมื่อคะแนนใกล้กัน."""
        scores = {t: _score(t, 60.0) for t in self.TARGETS}
        allocation = calculate_allocation(scores, 5000.0, target_weights=self.TARGETS)
        assert allocation["VOO"]["amount_thb"] > allocation["GLDM"]["amount_thb"]

    def test_higher_score_gets_more_when_targets_equal(self):
        targets = {"A": 0.5, "B": 0.5}
        scores = {"A": _score("A", 90.0), "B": _score("B", 30.0)}
        allocation = calculate_allocation(scores, 10000.0, target_weights=targets)
        assert allocation["A"]["amount_thb"] > allocation["B"]["amount_thb"]

    def test_tilt_is_bounded(self):
        """ตัวคูณต้องอยู่ในกรอบ 0.6–1.4 เสมอ — ไม่มีตัวไหนโดนตัดหรือกินรวบ."""
        targets = {"A": 0.5, "B": 0.5}
        for score_a, score_b in [(100.0, 0.0), (0.0, 100.0), (50.0, 50.0)]:
            allocation = calculate_allocation(
                {"A": _score("A", score_a), "B": _score("B", score_b)},
                10000.0,
                target_weights=targets,
            )
            for item in allocation.values():
                assert 0.55 <= item["tilt"] <= 1.45

    def test_no_data_ticker_never_gets_money(self):
        """ข้อมูลพังต้องไม่ได้รับเงิน และน้ำหนักของมันกระจายให้ตัวอื่น (C1)."""
        scores = {t: _score(t, 60.0) for t in self.TARGETS}
        scores["GLDM"] = _score("GLDM", 0.0, data_ok=False)
        allocation = calculate_allocation(scores, 5000.0, target_weights=self.TARGETS)
        assert "GLDM" not in allocation
        assert sum(i["amount_thb"] for i in allocation.values()) == 5000.0

    def test_ticker_without_score_is_skipped(self):
        scores = {"VOO": _score("VOO", 70.0), "XLV": {"ticker": "XLV", "total_pct": None}}
        allocation = calculate_allocation(scores, 5000.0, target_weights=self.TARGETS)
        assert "XLV" not in allocation

    def test_low_scores_still_allocate_but_reduced(self):
        """คะแนนต่ำทั้งพอร์ต = ยังซื้อ (DCA ไม่หยุด) และสัดส่วนยังตรงตามเป้าหมาย.

        เมื่อทุกตัวโดนลดน้ำหนักเท่ากัน การ normalize ทำให้สัดส่วนกลับไปเท่าเป้าหมายเดิม
        — นั่นคือพฤติกรรมที่ถูก: ตลาดแย่ทั้งกระดาน ≠ เปลี่ยนโครงสร้างพอร์ต
        """
        scores = {t: _score(t, 10.0) for t in self.TARGETS}
        allocation = calculate_allocation(scores, 5000.0, target_weights=self.TARGETS)
        assert len(allocation) == 5
        assert sum(i["amount_thb"] for i in allocation.values()) == 5000.0
        for ticker, item in allocation.items():
            # สัดส่วนจริงต้องใกล้เป้าหมาย (คลาดได้จากการปัดหลักร้อย = 2% ของงบต่อก้อน)
            assert item["percent"] == pytest.approx(item["target_percent"], abs=2.0)

    def test_tilt_reports_score_effect_only_not_rounding_noise(self):
        """`tilt` ต้องสะท้อนผลจากคะแนนล้วน ๆ ไม่ปนเศษการปัดเงิน (ไม่งั้นผู้ใช้เข้าใจผิด)."""
        scores = {t: _score(t, 50.0) for t in self.TARGETS}
        allocation = calculate_allocation(scores, 5000.0, target_weights=self.TARGETS)
        for item in allocation.values():
            assert item["tilt"] == 1.0, "คะแนน 50 = กลาง ๆ → ตัวคูณต้องเป็น 1.00 พอดี"


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
