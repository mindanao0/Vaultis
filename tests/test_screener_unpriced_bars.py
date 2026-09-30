# -*- coding: utf-8 -*-
"""แถวที่ไม่มีราคาปิดจาก Yahoo ต้องไม่ทำให้ screener ตาบอดทั้งเช้า.

เหตุการณ์จริง 2026-09-29 และ 2026-09-30: งาน 07:00 น. (= 00:00 UTC) รายงาน
``Screener run complete: 0/5 symbols passed, 5 ตรวจไม่ได้`` สามพรีเซ็ตติดกัน
(MA200 · golden cross · Bollinger squeeze) ส่วน ``overbought_warning`` (RSI + MACD)
ผ่านครบ — ทั้งที่ดึงซ้ำตอนสายได้ 501 แท่งสะอาด และพรีเซ็ตทั้งสี่รันได้หมด

กลไกที่พิสูจน์แล้ว: ใส่ ``Close = NaN`` แถวเดียวลงในเฟรมจริง ได้แพตเทิร์น error เดียวกันเป๊ะ
เพราะค่าเฉลี่ยเคลื่อนที่ (rolling) เป็น NaN ทั้งหน้าต่าง ส่วน ewm (RSI/MACD) ข้ามไปได้
``_fetch_df`` เป็นทางเข้าเดียวของราคาในระบบที่ไม่ตัดแถวแบบนี้ทิ้ง

ไม่ยิง network — สตับ ``yfinance.download`` ด้วยเฟรมที่รู้คำตอบ
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

import backend.screener.engine as engine_module
from backend.screener.engine import ScreenerEngine, _drop_unpriced_bars
from backend.screener.presets import get_preset

# วันที่ของเหตุการณ์จริง: เช้า 30/09 เวลาไทย = เย็น 29/09 ที่นิวยอร์ก
NY_TODAY = pd.Timestamp("2026-09-29")
PRESETS = ["oversold_momentum", "golden_cross_alert", "bb_breakout_watch", "overbought_warning"]


def _market_frame(last_day: str = "2026-09-29", bars: int = 501) -> pd.DataFrame:
    """เฟรมรูปเดียวกับที่ ``yfinance.download`` คืน (หลัง flatten คอลัมน์) — ราคาสะอาด."""
    idx = pd.bdate_range(end=last_day, periods=bars)
    i = np.arange(bars, dtype=float)
    close = 400 + 0.3 * i + 8 * np.sin(i / 9)
    return pd.DataFrame(
        {
            "Close": close,
            "High": close + 2,
            "Low": close - 2,
            "Open": close - 0.5,
            "Volume": np.full(bars, 5_000_000.0),
        },
        index=idx,
    )


def _with_placeholder(df: pd.DataFrame, day: str) -> pd.DataFrame:
    """ต่อแถวจองของวันที่ยังไม่เปิดตลาด — ราคาเป็น NaN แต่ volume ไม่ใช่ 0.

    (แถวที่ NaN ทุกช่องและ volume 0 ถูก yfinance ตัดเองแล้วด้วย ``keepna=False``
    แถวที่รอดมาถึงเราคือแถวที่มีบางช่องไม่ว่าง)
    """
    row = pd.DataFrame(
        {"Close": [np.nan], "High": [np.nan], "Low": [np.nan], "Open": [np.nan], "Volume": [1200.0]},
        index=[pd.Timestamp(day)],
    )
    return pd.concat([df, row])


def _errors_per_preset(frame: pd.DataFrame) -> dict[str, int]:
    engine = ScreenerEngine()
    return {
        name: len(engine.run(["VOO"], get_preset(name), {"VOO": frame}).errors)
        for name in PRESETS
    }


class TestIncidentReplay:
    def test_nan_row_reproduces_the_morning_pattern(self):
        """ยืนยันกลไก: เฟรมที่ยังไม่ผ่านการตัด ให้แพตเทิร์น error เดียวกับ log จริง."""
        raw = _with_placeholder(_market_frame(), "2026-09-30")
        assert _errors_per_preset(raw) == {
            "oversold_momentum": 1,
            "golden_cross_alert": 1,
            "bb_breakout_watch": 1,
            "overbought_warning": 0,
        }

    def test_placeholder_row_for_tomorrow_is_dropped_and_every_preset_runs(self):
        raw = _with_placeholder(_market_frame(), "2026-09-30")
        cleaned = _drop_unpriced_bars("VOO", raw, today=NY_TODAY)

        assert cleaned.index[-1] == pd.Timestamp("2026-09-29")
        assert cleaned["Close"].notna().all()
        assert _errors_per_preset(cleaned) == {name: 0 for name in PRESETS}


class TestThreeCases:
    def test_clean_frame_is_returned_untouched(self):
        df = _market_frame()
        assert _drop_unpriced_bars("VOO", df, today=NY_TODAY) is df

    def test_gap_in_the_middle_is_dropped(self):
        df = _market_frame()
        gap_day = df.index[-30]
        df.loc[gap_day, "Close"] = np.nan

        cleaned = _drop_unpriced_bars("VOO", df, today=NY_TODAY)

        assert gap_day not in cleaned.index
        assert len(cleaned) == len(df) - 1
        assert cleaned.index[-1] == df.index[-1], "แท่งล่าสุดจริงต้องยังอยู่"
        assert _errors_per_preset(cleaned) == {name: 0 for name in PRESETS}

    def test_latest_session_without_close_is_not_silently_replaced_by_yesterday(self):
        """ตัดแท่งล่าสุดทิ้งแล้วคำนวณต่อ = เอาสัญญาณของวันก่อนมารายงานเป็นของวันนี้."""
        df = _market_frame()
        df.loc[df.index[-1], "Close"] = np.nan

        with pytest.raises(ValueError, match="แท่งล่าสุด"):
            _drop_unpriced_bars("VOO", df, today=NY_TODAY)

    def test_latest_session_rule_applies_even_with_a_placeholder_after_it(self):
        df = _market_frame()
        df.loc[df.index[-1], "Close"] = np.nan
        raw = _with_placeholder(df, "2026-09-30")

        with pytest.raises(ValueError, match="29/09/2026"):
            _drop_unpriced_bars("VOO", raw, today=NY_TODAY)

    def test_frame_with_no_close_at_all_raises(self):
        df = _market_frame(bars=5)
        df["Close"] = np.nan
        with pytest.raises(ValueError):
            _drop_unpriced_bars("VOO", df, today=NY_TODAY)

    def test_every_drop_is_logged_with_its_date(self, caplog):
        raw = _with_placeholder(_market_frame(), "2026-09-30")
        with caplog.at_level("WARNING", logger="backend.screener.engine"):
            _drop_unpriced_bars("VOO", raw, today=NY_TODAY)
        assert "2026-09-30" in caplog.text


class TestFetchPath:
    def test_fetch_df_applies_the_rule(self, monkeypatch):
        """ทางเข้าจริง (``_fetch_df`` → งาน 07:00 และ ``/api/screener``) ต้องผ่านตัวตัด."""
        tomorrow_ny = (pd.Timestamp.now(tz="America/New_York") + pd.Timedelta(days=2)).tz_localize(None)
        raw = _with_placeholder(_market_frame(last_day=str(tomorrow_ny.date() - pd.Timedelta(days=3))),
                                str(tomorrow_ny.date()))
        monkeypatch.setattr(engine_module.yfinance, "download", lambda *a, **k: raw.copy())

        df = ScreenerEngine()._fetch_df("VOO")

        assert df["Close"].notna().all()
        assert len(df) == len(raw) - 1
