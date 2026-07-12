# -*- coding: utf-8 -*-
"""นิยามสัญญาณกลางหนึ่งเดียวของทั้งระบบ (แก้ AUDIT.md C2).

ก่อนหน้านี้แต่ละ subsystem ตีความ RSI/MA คนละทาง (RSI 28 บางหน้าเป็นคะแนนซื้อสูงสุด
บางหน้าเป็น strong_sell) — โมดูลนี้เป็น source of truth เดียว ทุกที่ต้อง import จากที่นี่

นโยบายสำหรับนักลงทุน DCA ระยะยาว:
- Oversold (RSI < 30) **ไม่ใช่สัญญาณขาย**
  - อยู่ในแนวโน้มขาขึ้น (ราคา ≥ MA200) = โอกาสสะสม (ACCUMULATE)
  - อยู่ในแนวโน้มขาลง = เฝ้าระวัง รอยืนยัน (DOWNTREND_WATCH) ไม่เชียร์ทั้งซื้อและขาย
- Overbought (RSI > 70) = ระวังไล่ราคา (OVERBOUGHT_CAUTION) ไม่ใช่คำสั่งขายอัตโนมัติ
- ข้อมูลไม่ครบ = NO_DATA เสมอ ห้ามเดาเป็นสัญญาณอื่น (AUDIT.md C1)
"""

from __future__ import annotations

RSI_OVERSOLD = 30.0
RSI_OVERBOUGHT = 70.0

# ค่าที่เป็นไปได้ของสัญญาณกลาง
NO_DATA = "no_data"
ACCUMULATE = "accumulate"              # oversold ในขาขึ้น — จังหวะสะสมของ DCA
BULLISH = "bullish"                    # ขาขึ้นปกติ
OVERBOUGHT_CAUTION = "overbought_caution"
DOWNTREND_WATCH = "downtrend_watch"    # oversold ในขาลง — เฝ้าดู ไม่ใช่ขาย
DOWNTREND = "downtrend"
NEUTRAL = "neutral"


def _valid(*values: float | None) -> bool:
    for v in values:
        if v is None:
            return False
        try:
            f = float(v)
        except (TypeError, ValueError):
            return False
        if f != f:  # NaN
            return False
    return True


def rsi_zone(rsi: float | None) -> str:
    """แบ่งโซน RSI มาตรฐานเดียว: oversold / neutral / overbought / no_data."""
    if not _valid(rsi):
        return NO_DATA
    r = float(rsi)  # type: ignore[arg-type]
    if r < RSI_OVERSOLD:
        return "oversold"
    if r > RSI_OVERBOUGHT:
        return "overbought"
    return "neutral"


def dca_signal(
    price: float | None,
    ma50: float | None,
    ma200: float | None,
    rsi: float | None,
) -> str:
    """สัญญาณกลางสำหรับมุมมอง DCA ระยะยาว — คืนค่าหนึ่งในค่าคงที่ด้านบน."""
    if not _valid(price, ma200, rsi):
        return NO_DATA
    p, m200, r = float(price), float(ma200), float(rsi)  # type: ignore[arg-type]
    uptrend = p >= m200

    if r > RSI_OVERBOUGHT:
        return OVERBOUGHT_CAUTION
    if r < RSI_OVERSOLD:
        return ACCUMULATE if uptrend else DOWNTREND_WATCH
    if uptrend:
        above_ma50 = _valid(ma50) and p >= float(ma50)  # type: ignore[arg-type]
        return BULLISH if above_ma50 else NEUTRAL
    return DOWNTREND


def to_technical_label(central: str) -> str:
    """แปลงสัญญาณกลางเป็น label เดิมของ TechnicalIndicators.signal (bullish/bearish/neutral)."""
    if central in (BULLISH, ACCUMULATE):
        return "bullish"
    if central in (DOWNTREND, DOWNTREND_WATCH):
        return "bearish"
    return "neutral"


def overall_signal(
    central: str,
    golden_cross: bool = False,
    death_cross: bool = False,
    rsi: float | None = None,
) -> str:
    """ป้ายสรุปสำหรับหน้า ETF analysis: strong_buy / buy / hold / sell.

    หมายเหตุ: oversold ไม่ map เป็น strong_sell อีกต่อไป (บั๊กเดิมใน AUDIT.md C2)
    """
    if central == NO_DATA:
        return "no_data"
    if central in (BULLISH, ACCUMULATE):
        rsi_ok = _valid(rsi) and float(rsi) < 65  # type: ignore[arg-type]
        if golden_cross and rsi_ok:
            return "strong_buy"
        return "buy"
    if central == DOWNTREND and death_cross:
        return "sell"
    # overbought_caution, downtrend_watch, downtrend (ไม่มี death cross), neutral
    return "hold"


def thai_description(central: str) -> str:
    """คำอธิบายภาษาไทยสั้น ๆ ของสัญญาณกลาง (ใช้ในข้อความแจ้งเตือน/AI prompt)."""
    return {
        NO_DATA: "ข้อมูลไม่พร้อม — ห้ามตีความเป็นสัญญาณ",
        ACCUMULATE: "ย่อตัวในแนวโน้มขาขึ้น (จังหวะสะสมตามแผน DCA)",
        BULLISH: "แนวโน้มขาขึ้น",
        OVERBOUGHT_CAUTION: "ราคาร้อนแรง (overbought) ระวังไล่ราคา",
        DOWNTREND_WATCH: "oversold ในแนวโน้มขาลง — เฝ้าดู รอยืนยัน",
        DOWNTREND: "แนวโน้มขาลง (ต่ำกว่า MA200)",
        NEUTRAL: "กลาง ๆ ยังไม่มีสัญญาณชัด",
    }.get(central, central)
