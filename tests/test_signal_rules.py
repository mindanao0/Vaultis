# -*- coding: utf-8 -*-
"""เทสต์นิยามสัญญาณกลาง — กันบั๊ก "ระบบเดียวกันให้สัญญาณตรงข้ามกัน" (AUDIT.md C2).

ไม่ยิง network ทั้งไฟล์
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest

from backend.models.etf_models import TechnicalIndicators
from backend.services.analysis_service import AnalysisService
from backend.services.technical_service import _signal as technical_signal
from technical import signal_rules


class TestRsiZone:
    def test_oversold(self):
        assert signal_rules.rsi_zone(28.0) == "oversold"

    def test_overbought(self):
        assert signal_rules.rsi_zone(75.0) == "overbought"

    def test_neutral(self):
        assert signal_rules.rsi_zone(50.0) == "neutral"

    @pytest.mark.parametrize("bad", [None, float("nan")])
    def test_missing_is_no_data(self, bad):
        assert signal_rules.rsi_zone(bad) == signal_rules.NO_DATA


class TestDcaSignal:
    def test_oversold_in_uptrend_is_accumulate_not_sell(self):
        """RSI 28 เหนือ MA200 = จังหวะสะสม — ห้ามเป็นสัญญาณขาย (บั๊กเดิม C2)."""
        assert signal_rules.dca_signal(price=110, ma50=105, ma200=100, rsi=28) == signal_rules.ACCUMULATE

    def test_oversold_in_downtrend_is_watch_not_sell(self):
        assert signal_rules.dca_signal(price=90, ma50=95, ma200=100, rsi=28) == signal_rules.DOWNTREND_WATCH

    def test_overbought_is_caution(self):
        assert signal_rules.dca_signal(price=120, ma50=110, ma200=100, rsi=78) == signal_rules.OVERBOUGHT_CAUTION

    def test_healthy_uptrend_is_bullish(self):
        assert signal_rules.dca_signal(price=110, ma50=105, ma200=100, rsi=55) == signal_rules.BULLISH

    def test_below_ma200_is_downtrend(self):
        assert signal_rules.dca_signal(price=90, ma50=95, ma200=100, rsi=50) == signal_rules.DOWNTREND

    def test_missing_data_never_becomes_a_signal(self):
        assert signal_rules.dca_signal(None, 105, 100, 50) == signal_rules.NO_DATA
        assert signal_rules.dca_signal(110, 105, None, 50) == signal_rules.NO_DATA
        assert signal_rules.dca_signal(110, 105, 100, float("nan")) == signal_rules.NO_DATA


class TestOverallSignal:
    def test_oversold_uptrend_never_maps_to_sell(self):
        """บั๊กเดิม: RSI < 30 → strong_sell ทั้งที่โมดูลอื่นให้คะแนนซื้อสูงสุด."""
        central = signal_rules.dca_signal(price=110, ma50=105, ma200=100, rsi=28)
        result = signal_rules.overall_signal(central, golden_cross=False, death_cross=False, rsi=28)
        assert result == "buy"
        assert "sell" not in result

    def test_golden_cross_upgrades_to_strong_buy(self):
        central = signal_rules.dca_signal(price=110, ma50=105, ma200=100, rsi=55)
        assert signal_rules.overall_signal(central, golden_cross=True, rsi=55) == "strong_buy"

    def test_downtrend_with_death_cross_is_sell(self):
        central = signal_rules.dca_signal(price=90, ma50=95, ma200=100, rsi=45)
        assert signal_rules.overall_signal(central, death_cross=True, rsi=45) == "sell"

    def test_no_data_stays_no_data(self):
        assert signal_rules.overall_signal(signal_rules.NO_DATA) == "no_data"


class TestSubsystemsAgree:
    """ข้อมูลชุดเดียวกันต้องไม่ให้สัญญาณขัดกันข้าม subsystem (หัวใจของ C2)."""

    OVERSOLD_UPTREND = dict(price=110.0, ma50=105.0, ma200=100.0, rsi=28.0)

    def test_technical_service_calls_oversold_uptrend_bullish(self):
        assert technical_signal(**self.OVERSOLD_UPTREND) == "bullish"

    def test_analysis_service_does_not_say_sell(self):
        tech = TechnicalIndicators(
            symbol="VOO",
            price=self.OVERSOLD_UPTREND["price"],
            rsi=self.OVERSOLD_UPTREND["rsi"],
            ma50=self.OVERSOLD_UPTREND["ma50"],
            ma200=self.OVERSOLD_UPTREND["ma200"],
            signal="bullish",
        )
        overall = AnalysisService().compute_overall_signal(tech)
        assert overall in {"buy", "strong_buy"}
        assert "sell" not in overall
