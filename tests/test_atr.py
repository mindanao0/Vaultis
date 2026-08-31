# -*- coding: utf-8 -*-
"""ATR (Average True Range) — ตัวชี้วัดความผันผวนตัวใหม่ในชั้นตัวชี้วัดกลาง.

สองอย่างที่ไฟล์นี้ตรึงไว้เพราะพังแล้วยังได้ตัวเลขที่ดูปกติ:

* **True Range ต้องนับ gap** ``max(H−L, |H−C_prev|, |L−C_prev|)`` — ถ้าเหลือแค่ ``H−L``
  วันที่ราคาเปิดกระโดดจะถูกวัดว่าผันผวน "ต่ำ" ทั้งที่เป็นวันที่ราคาขยับมากที่สุด
* **warmup ต้องเป็น NaN** ไม่ใช่ 0 — ATR = 0 อ่านว่า "ราคานิ่งสนิท" ซึ่งเป็นคำกล่าวอ้าง
  ไม่ใช่ความว่างเปล่าของข้อมูล (กฎเดียวกับ MA/RSI ใน AUDIT.md M1)

และ ``atr_stats`` เป็นสถิติพรรณนา — ต้องไม่มีทางไหลเข้าเลขคะแนน/จัดสรร DCA
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from analysis import risk as risk_mod  # noqa: E402
from analysis.ta_compat import _true_range, ta  # noqa: E402


def _flat_ohlc(n: int = 60, spread: float = 1.0) -> pd.DataFrame:
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    close = pd.Series(np.linspace(100.0, 110.0, n), index=idx)
    return pd.DataFrame({"High": close + spread, "Low": close - spread, "Close": close}, index=idx)


class TestTrueRange:
    def test_แท่งแรกไม่มีราคาปิดก่อนหน้า_ต้องเป็น_NaN(self):
        tr = _true_range(pd.Series([10.0, 11.0]), pd.Series([9.0, 10.0]), pd.Series([9.5, 10.5]))
        assert pd.isna(tr.iloc[0]), "แท่งแรกต้องเป็น NaN ไม่ใช่ H−L ซึ่งเดาว่าไม่มี gap"

    def test_gap_ลงต้องถูกนับเต็ม_ไม่ใช่แค่ช่วงในวัน(self):
        """ปิด 100 → วันรุ่งขึ้นแกว่งแคบแถว 90: H−L = 1 แต่ TR จริง = 10."""
        high = pd.Series([100.0, 91.0])
        low = pd.Series([99.0, 90.0])
        close = pd.Series([100.0, 90.5])
        tr = _true_range(high, low, close)
        assert tr.iloc[1] == pytest.approx(10.0)
        assert tr.iloc[1] > (high.iloc[1] - low.iloc[1])

    def test_gap_ขึ้นก็ต้องถูกนับเต็มเช่นกัน(self):
        tr = _true_range(
            pd.Series([100.0, 111.0]), pd.Series([99.0, 110.0]), pd.Series([100.0, 110.5])
        )
        assert tr.iloc[1] == pytest.approx(11.0)

    def test_ไม่มี_gap_ให้ผลเท่ากับช่วงในวัน(self):
        tr = _true_range(
            pd.Series([100.0, 101.0]), pd.Series([99.0, 100.0]), pd.Series([100.0, 100.5])
        )
        assert tr.iloc[1] == pytest.approx(1.0)


class TestAtrSeries:
    def test_warmup_เป็น_NaN_ไม่ใช่ศูนย์(self):
        df = _flat_ohlc(40)
        series = ta.atr(df["High"], df["Low"], df["Close"], length=14)
        # แท่งแรกไม่มี prev_close (1) + ต้องสะสม TR ให้ครบ 14 แท่ง
        assert int(series.isna().sum()) == 14
        assert not (series.fillna(-1) == 0).any(), "ห้ามมี 0 ที่เกิดจากการ fill warmup"

    def test_ข้อมูลสั้นกว่าหน้าต่าง_ได้_NaN_ทั้งเส้น(self):
        df = _flat_ohlc(10)
        assert ta.atr(df["High"], df["Low"], df["Close"], length=14).isna().all()

    def test_ช่วงกว้างคงที่ให้ค่า_ATR_เท่ากับความกว้างนั้น(self):
        df = _flat_ohlc(80, spread=1.5)
        series = ta.atr(df["High"], df["Low"], df["Close"], length=14).dropna()
        # ราคาไต่ขึ้นช้ามาก ⇒ TR ≈ ความกว้างในวัน = 3.0
        assert float(series.iloc[-1]) == pytest.approx(3.0, abs=0.05)

    def test_ผันผวนมากขึ้นแล้ว_ATR_ต้องสูงขึ้น(self):
        narrow = _flat_ohlc(80, spread=0.5)
        wide = _flat_ohlc(80, spread=3.0)
        a = ta.atr(narrow["High"], narrow["Low"], narrow["Close"]).iloc[-1]
        b = ta.atr(wide["High"], wide["Low"], wide["Close"]).iloc[-1]
        assert float(b) > float(a)


class TestAtrStats:
    def test_คืนทั้งดอลลาร์และเปอร์เซ็นต์(self):
        stats = risk_mod.atr_stats(_flat_ohlc(80, spread=1.0))
        assert stats["atr"] > 0
        # ~2.0 ดอลลาร์ บนราคาปลายทาง ~110 ⇒ ราว 1.8%
        assert stats["atr_pct"] == pytest.approx(stats["atr"] / 110.0 * 100.0, rel=0.05)

    def test_ข้อมูลไม่พอต้อง_raise_ไม่ใช่คืนศูนย์(self):
        with pytest.raises(ValueError):
            risk_mod.atr_stats(_flat_ohlc(10))

    def test_ขาดคอลัมน์ต้องบอกว่าขาดอะไร(self):
        df = _flat_ohlc(80).drop(columns=["High"])
        with pytest.raises(ValueError, match="High"):
            risk_mod.atr_stats(df)

    def test_เปอร์เซ็นไทล์อยู่ในช่วง_0_ถึง_100(self):
        stats = risk_mod.atr_stats(_flat_ohlc(300))
        assert stats["percentile"] is None or 0.0 <= stats["percentile"] <= 100.0

    def test_ราคาปิดไม่เป็นบวกต้อง_raise_ไม่ใช่หารด้วยศูนย์(self):
        df = _flat_ohlc(80)
        df.loc[df.index[-1], "Close"] = 0.0
        with pytest.raises(ValueError):
            risk_mod.atr_stats(df)


class TestAtrStaysDescriptive:
    """ATR ต้องไม่ไหลเข้าเลขคะแนนหรือการจัดสรร (invariant เดียวกับ trend_channel)."""

    def test_ไม่มีใครใน_financial_model_เรียก_atr(self):
        source = (_ROOT / "analysis" / "financial_model.py").read_text(encoding="utf-8")
        assert "atr" not in source.lower().replace("separator", "")

    def test_ไม่มีใครใน_targets_หรือ_dca_เรียก_atr(self):
        for rel in ("portfolio/targets.py", "portfolio/dca.py"):
            source = (_ROOT / rel).read_text(encoding="utf-8")
            assert "atr_stats" not in source and "ta.atr" not in source
