# -*- coding: utf-8 -*-
"""ตัวตรวจจับสัญญาณทางเทคนิคของ screener.

**กติกาข้อเดียวของไฟล์นี้: "คำนวณไม่ได้" ต้องคืน ``None`` ห้ามคืน ``False``**
(FIX_PLAN ข้อ 2.1 · AUDIT.md C1)

เดิมทุกตัวคืน ``bool`` ล้วน ⇒ ETF ที่ประวัติสั้นกว่า 200 วัน (MA200 เป็น NaN ทั้งเส้น)
ได้ ``False`` เท่ากับ ETF ที่คำนวณได้แล้วพบว่า "ไม่มีการตัด" ทุกไบต์ — พรีเซ็ต
``golden_cross_alert`` ที่งาน 07:00 ใช้จึงรายงาน "ไม่มีสัญญาณ" ให้กองเหล่านั้น
**ตลอดกาล** โดยไม่มีอะไรร้อง และผู้ใช้อ่านว่า "ตรวจแล้วไม่มีอะไรต้องทำ"

ความไม่สอดคล้องนี้อยู่ในเอนจินเดียวกัน: ``price_vs_ma200`` ใน ``engine.py`` โยน
``ValueError`` เมื่อ MA200 เป็น NaN อยู่แล้ว แต่ตัวตรวจจับในไฟล์นี้กลืนเงียบ

``engine._evaluate_rule`` เป็นตัวแปล ``None`` → ``ValueError`` แล้ว ``run()`` เก็บลง
``.errors`` รายสัญลักษณ์ ซึ่งเดินทางไปถึงงาน 07:00 และหน้าจอต่อ
"""

from __future__ import annotations

import pandas as pd
from typing import Optional

from analysis.ta_compat import ta

#: MACD คำนวณได้แล้วแต่ **ไม่มีการตัด** — คนละความหมายกับ ``None`` (คำนวณไม่ได้)
#: เดิมสองกรณีนี้คืน ``None`` เหมือนกัน จึงแยกไม่ออกที่ผู้เรียก
NO_CROSS = "none"

#: จำนวนแท่งขั้นต่ำของแต่ละสูตร — ใช้ตัดสิน "คำนวณไม่ได้" ก่อนแตะ ``iloc``
_MA_LONG = 200
_MA_SHORT = 50
_BB_LENGTH = 20
_BB_AVG_WINDOW = 50
_VOLUME_WINDOW = 20


def _finite(value: object) -> bool:
    """เป็นตัวเลขที่ใช้เทียบได้จริงไหม (NaN/None/แปลงไม่ได้ = ไม่ใช่)."""
    try:
        return bool(pd.notna(value)) and pd.notna(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


class CrossoverDetector:
    def detect_macd_cross(self, df: pd.DataFrame) -> Optional[str]:
        """``"bullish"`` / ``"bearish"`` / :data:`NO_CROSS` — ``None`` = คำนวณไม่ได้."""
        macd = ta.macd(df["Close"], fast=12, slow=26, signal=9)
        if macd is None or len(macd) < 3:
            return None
        macd_col = [c for c in macd.columns if c.startswith("MACD_")][0]
        signal_col = [c for c in macd.columns if c.startswith("MACDs_")][0]
        values = [
            macd[macd_col].iloc[-2],
            macd[signal_col].iloc[-2],
            macd[macd_col].iloc[-1],
            macd[signal_col].iloc[-1],
        ]
        if not all(_finite(v) for v in values):
            # ช่วงอุ่นเครื่อง MACD ยังเป็น NaN — "ยังไม่รู้" ไม่ใช่ "ไม่มีการตัด"
            return None
        prev_diff = float(values[0]) - float(values[1])
        curr_diff = float(values[2]) - float(values[3])
        if prev_diff < 0 and curr_diff > 0:
            return "bullish"
        if prev_diff > 0 and curr_diff < 0:
            return "bearish"
        return NO_CROSS

    def _ma_pair(self, df: pd.DataFrame, lookback_days: int):
        """(ma50, ma200) ที่ **การันตีว่าอ่านค่าในหน้าต่างที่ต้องใช้ได้ครบ** — ไม่ครบคืน ``None``."""
        needed = lookback_days + 1
        if len(df) < _MA_LONG + needed:
            return None
        ma_short = df["Close"].rolling(_MA_SHORT).mean()
        ma_long = df["Close"].rolling(_MA_LONG).mean()
        window = [
            ma_short.iloc[-i - 1] for i in range(1, lookback_days + 1)
        ] + [ma_long.iloc[-i - 1] for i in range(1, lookback_days + 1)] + [
            ma_short.iloc[-i] for i in range(1, lookback_days + 1)
        ] + [ma_long.iloc[-i] for i in range(1, lookback_days + 1)]
        if not all(_finite(v) for v in window):
            return None
        return ma_short, ma_long

    def detect_golden_cross(self, df: pd.DataFrame, lookback_days: int = 3) -> Optional[bool]:
        """MA50 ตัดขึ้นเหนือ MA200 ในช่วง lookback — ``None`` = ประวัติไม่พอให้ตัดสิน."""
        pair = self._ma_pair(df, lookback_days)
        if pair is None:
            return None
        ma50, ma200 = pair
        for i in range(1, lookback_days + 1):
            if ma50.iloc[-i - 1] < ma200.iloc[-i - 1] and ma50.iloc[-i] > ma200.iloc[-i]:
                return True
        return False

    def detect_death_cross(self, df: pd.DataFrame, lookback_days: int = 3) -> Optional[bool]:
        """MA50 ตัดลงใต้ MA200 ในช่วง lookback — ``None`` = ประวัติไม่พอให้ตัดสิน."""
        pair = self._ma_pair(df, lookback_days)
        if pair is None:
            return None
        ma50, ma200 = pair
        for i in range(1, lookback_days + 1):
            if ma50.iloc[-i - 1] > ma200.iloc[-i - 1] and ma50.iloc[-i] < ma200.iloc[-i]:
                return True
        return False

    def detect_bb_squeeze(self, df: pd.DataFrame) -> Optional[bool]:
        """Bollinger bandwidth แคบกว่าครึ่งของค่าเฉลี่ย 50 แท่ง — ``None`` = คำนวณไม่ได้."""
        if len(df) < _BB_LENGTH + _BB_AVG_WINDOW:
            return None
        bb = ta.bbands(df["Close"], length=_BB_LENGTH, std=2)
        if bb is None:
            return None
        upper_col = [c for c in bb.columns if c.startswith("BBU")][0]
        lower_col = [c for c in bb.columns if c.startswith("BBL")][0]
        mid_col = [c for c in bb.columns if c.startswith("BBM")][0]
        mid = bb[mid_col]
        # เส้นกลางเป็น 0 ทำให้ bandwidth เป็น inf/NaN — ไม่ใช่ "ไม่บีบตัว"
        bandwidth = (bb[upper_col] - bb[lower_col]) / mid.where(mid != 0)
        current_bw = bandwidth.iloc[-1]
        avg_bw = bandwidth.rolling(_BB_AVG_WINDOW).mean().iloc[-1]
        if not _finite(current_bw) or not _finite(avg_bw):
            return None
        return bool(float(current_bw) < float(avg_bw) * 0.5)

    def detect_volume_spike(self, df: pd.DataFrame, multiplier: float = 2.0) -> Optional[bool]:
        """ปริมาณซื้อขายพุ่งเกิน ``multiplier`` เท่าของค่าเฉลี่ย 20 แท่ง — ``None`` = ไม่รู้."""
        if "Volume" not in df.columns or len(df) < _VOLUME_WINDOW:
            return None
        vol_ma20 = df["Volume"].rolling(_VOLUME_WINDOW).mean().iloc[-1]
        current_vol = df["Volume"].iloc[-1]
        if not _finite(vol_ma20) or not _finite(current_vol) or float(vol_ma20) <= 0.0:
            # ค่าเฉลี่ย 0 (ผู้ให้ข้อมูลไม่ส่ง volume) ทำให้ทุกอย่าง "พุ่ง" — ห้ามตอบว่าจริง
            return None
        return bool(float(current_vol) > float(vol_ma20) * float(multiplier))

    def detect_price_drop_pct(
        self, df: pd.DataFrame, pct: float = 5.0, days: int = 10
    ) -> Optional[bool]:
        """ราคาลดลงจากแท่งที่ ``iloc[-days]`` เกิน ``pct``% — ``None`` = แท่งไม่พอ/ราคาใช้ไม่ได้.

        จุดอ้างอิงยังเป็น ``iloc[-days]`` ตามเดิมเป๊ะ (ไม่ใช่ ``-days-1``) — คอมมิตนี้เพิ่ม
        เฉพาะด่าน "คำนวณไม่ได้" การขยับจุดอ้างอิงจะเปลี่ยนสัญญาณเงียบ ๆ ซึ่งเป็นคนละเรื่อง
        """
        if days <= 0 or len(df) < days:
            return None
        price_now = df["Close"].iloc[-1]
        price_before = df["Close"].iloc[-days]
        if not _finite(price_now) or not _finite(price_before) or float(price_before) <= 0.0:
            return None
        drop = (float(price_before) - float(price_now)) / float(price_before) * 100.0
        return bool(drop >= pct)
