# -*- coding: utf-8 -*-
"""ด่านตรวจน้ำหนักพอร์ต — **นิยามเดียว** ที่ทั้ง API และตัวจำลองใช้ร่วมกัน.

ทำไมต้องมีไฟล์นี้ (AUDIT_ROUND2_2026-08-07 · G8): ``inf`` เดินผ่านทุกด่านของ
``_normalize_weights`` ได้จนกลายเป็นตัวเลขที่ระบบไม่เคยคำนวณ::

    inf > 0                -> True   (ต่างจาก NaN > 0 ที่เป็น False จึงถูกดักถูก)
    weight_sum = inf       -> ไม่ ≤ 0 จึงรอดด่านที่สอง
    inf / inf              -> NaN
    NaN ทั้งแถว .sum()     -> 0.0    (pandas ใช้ skipna=True เป็นค่าเริ่มต้น)

ปลายทางคือ ``POST /api/analysis/backtest`` ตอบ 200 พร้อมเส้นมูลค่าแบนราบที่ทุนตั้งต้น
= "พอร์ตนี้ให้ผลตอบแทน 0% ตลอด 10 ปี" และ ``POST /api/dca/simulate`` ตอบ 200 พร้อม
"ขาดทุน 100%" — ทั้งคู่เป็นการกุตัวเลขบนเส้นทางเงินจากอินพุตขยะ

กฎที่นี่มีชุดเดียว ผู้เรียกทุกชั้นต้องได้คำตอบเหมือนกัน:

- ``backend/schemas.py`` → ปฏิเสธตั้งแต่ชั้น schema (HTTP 422 พร้อมข้อความไทย)
- ``portfolio/backtest.py`` / ``portfolio/dca.py`` → ปฏิเสธที่ตัวโมดูล เพราะแดชบอร์ด
  เรียกฟังก์ชันเหล่านี้ตรง ๆ ไม่ผ่าน API ด่านชั้น schema จึงไม่ได้ครอบให้

ข้อความต้องบอก **ชื่อกองที่ผิด** เสมอ — "weights ไม่ถูกต้อง" เฉย ๆ ผู้ใช้แก้ตามไม่ได้
"""

from __future__ import annotations

import math
from typing import Any, Mapping

__all__ = ["validate_weights"]


def _as_float(ticker: str, value: Any) -> float:
    """แปลงเป็น float โดยบอกชื่อกองเมื่อค่าไม่ใช่ตัวเลข (เดิมได้ ValueError ภาษาอังกฤษของ pandas)."""
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"น้ำหนักของ {ticker} ไม่ใช่ตัวเลข: {value!r}") from exc


def validate_weights(weights: Mapping[str, Any] | None) -> dict[str, float]:
    """ตรวจน้ำหนักพอร์ตก่อนนำไปคำนวณ — คืน dict ของ float ที่ใช้ได้จริง.

    เงื่อนไขที่ต้องผ่านครบ (ไม่ผ่าน = ``ValueError`` พร้อมชื่อกองที่ผิด):

    1. มีอย่างน้อย 1 กอง
    2. ทุกค่าเป็นจำนวนจริงจำกัด — ``inf``/``-inf``/``NaN`` ไม่ผ่าน
    3. ไม่มีค่าติดลบ (แพลตฟอร์มนี้ซื้ออย่างเดียว ไม่มี short)
    4. ผลรวมเป็นจำนวนจริงและมากกว่า 0 (ผลรวมล้นจนเป็น ``inf`` ก็ไม่ผ่าน —
       ไม่งั้น ``w / inf`` = 0.0 ทุกกอง แล้วได้เส้นแบนราบแบบเดียวกับข้อ 2)

    หมายเหตุ: **ไม่** normalize ให้ที่นี่ — ผู้เรียกแต่ละคนมีนิยามของตัวเองว่าจะ
    ทำอย่างไรกับน้ำหนัก 0 (backtest ตัดออกก่อน normalize ส่วน DCA ถือไว้ที่ 0)
    """
    if not weights:
        raise ValueError("ต้องระบุน้ำหนักพอร์ตอย่างน้อย 1 กอง (weights ว่าง)")

    cleaned: dict[str, float] = {}
    non_finite: list[str] = []
    negative: list[str] = []

    for raw_ticker, raw_value in dict(weights).items():
        ticker = str(raw_ticker)
        value = _as_float(ticker, raw_value)
        if not math.isfinite(value):
            non_finite.append(f"{ticker}={value}")
        elif value < 0:
            negative.append(f"{ticker}={value:g}")
        cleaned[ticker] = value

    if non_finite:
        raise ValueError(
            "น้ำหนักต้องเป็นตัวเลขจำกัด ไม่ใช่ inf หรือ NaN: "
            + ", ".join(non_finite)
            + " (ค่าแบบนี้ทำให้ผลลัพธ์กลายเป็นเส้นมูลค่าแบนราบที่อ่านผิดว่าได้ผลตอบแทน 0%)"
        )

    if negative:
        raise ValueError(
            "น้ำหนักติดลบไม่ได้ พอร์ตนี้ซื้ออย่างเดียว (ไม่มี short): " + ", ".join(negative)
        )

    try:
        total = math.fsum(cleaned.values())
    except OverflowError:  # ผลรวมใหญ่เกิน float — fsum โยนแทนที่จะคืน inf
        total = math.inf
    if not math.isfinite(total):
        raise ValueError(
            f"ผลรวมน้ำหนักใหญ่เกินกว่าจะคำนวณได้ (รวมแล้วล้นเป็น {total}) — ใช้สัดส่วนปกติ เช่น 0.4 หรือ 40"
        )
    if total <= 0:
        raise ValueError(f"ผลรวมน้ำหนักต้องมากกว่า 0 (ตอนนี้รวมได้ {total:g})")

    return cleaned
