# -*- coding: utf-8 -*-
"""เทียบพอร์ตจริงกับ benchmark อย่างต่อเนื่อง (Roadmap Phase 4 ข้อ 14).

สองเครื่องมือ (สถิติเชิงพรรณนาจาก ledger + ราคาจริง — ไม่เข้าเลขคะแนน/จัดสรร):

- ``shadow_benchmark``: "ถ้าเงินก้อนเดียวกัน วันเดียวกัน ซื้อ benchmark (VOO) ล้วน
  วันนี้ได้เท่าไร" — money-weighted ตรง ๆ ไม่ต้องมีสมมติฐานอัตราผลตอบแทน
- ``xirr``: ผลตอบแทน %ต่อปีแบบ money-weighted จากกระแสเงินสดจริง (bisection บน NPV)
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

XIRR_LOW, XIRR_HIGH = -0.9999, 10.0  # เผื่อเคสขาดทุนเกือบหมด (root ต่ำกว่า -99%)
_DAYS_PER_YEAR = 365.25
_NPV_REL_TOLERANCE = 1e-6  # เทียบกับผลรวม |กระแสเงิน| — ดู _npv_tolerance()


def _finite(value: Any) -> float | None:
    """แปลงเป็น float ที่เอาไปคำนวณเงินได้จริง — คืน ``None`` เมื่ออ่านไม่ออก/NaN/inf.

    **ห้ามใช้สำนวน ``float(pd.to_numeric(x) or 0.0)`` แทน**: ``bool(float('nan')) is True``
    guard ``or 0.0`` จึงดัก NaN ไม่ได้เลย NaN ไหลไปคูณต่อจนผลลัพธ์ทั้งก้อนเป็น NaN เงียบ ๆ
    (และเทียบ ``NaN <= 0`` ได้ False เสมอ ด่านคัดของถัดไปจึงปล่อยผ่านด้วย)
    """
    number = pd.to_numeric(value, errors="coerce")
    try:
        result = float(number)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _align_tz(date: pd.Timestamp, index: pd.Index) -> pd.Timestamp | None:
    """ปรับ timezone ของวันที่ซื้อให้เทียบกับดัชนีราคาได้ — คืน ``None`` เมื่อปรับไม่ได้.

    ``Series.asof()`` โยน ``TypeError: Cannot compare tz-naive and tz-aware timestamps``
    เมื่อสองฝั่งไม่ตรงกัน สมุดจริงเป็น tz-naive แต่ข้อมูลนำเข้าบางแหล่งติด tz มาด้วย

    **ยึดเวลาหน้าปัด (wall clock) ห้ามใช้ ``tz_convert()``**: ไม้ที่ซื้อ 06:00 น. เวลาไทย
    วันที่ 2 คือไม้ของวันที่ 2 แต่ ``tz_convert(None)`` ย้ายไปเวลา UTC ก่อนถอด tz แล้ว
    **เลื่อนวันที่ได้ทั้งขึ้นและลง** ไม้จึงถูกตีด้วยราคาของวันอื่นเงียบ ๆ (หรือตกนอกช่วง
    ราคาจนถูกนับเป็น "ไม่มีราคา") — เงียบกว่า ``TypeError`` เดิมแต่โกหก
    """
    index_tz = getattr(index, "tz", None)
    try:
        if date.tzinfo is not None:
            date = date.tz_localize(None)  # ถอด tz โดยคงเวลาหน้าปัด = คงวันที่ซื้อ
        if index_tz is not None:
            # DST: ชั่วโมงที่ไม่มีอยู่จริง/ซ้ำสองรอบ เลือกอย่างไรก็ยังเป็น "วันเดียวกัน"
            # จึงเลือกให้เดินต่อได้ ดีกว่าทิ้งไม้ที่ข้อมูลครบ
            date = date.tz_localize(index_tz, nonexistent="shift_forward", ambiguous=False)
    except Exception:
        # pytz ``NonExistentTimeError``/``AmbiguousTimeError`` **ไม่ใช่ลูกของ ValueError**
        # (แม่คือ ``pytz.exceptions.Error``) จับแคบ ๆ แล้วมันจะหลุดทั้งชั้นนี้และ
        # ``except ValueError`` ของหน้าจอ ไม้ที่ปรับ tz ไม่ได้ต้องถูก "ข้าม + นับ" ไม่ใช่ระเบิด
        return None
    return date


def shadow_benchmark(buys: pd.DataFrame, benchmark_closes: pd.Series) -> dict[str, Any]:
    """จำลอง "ซื้อ benchmark ล้วนด้วยเงิน (USD) และวันเดียวกับไม้จริงทุกไม้".

    ``buys``: แถวซื้อจริง ต้องมี ``date``, ``shares``, ``price_usd``
    ``benchmark_closes``: ราคาปิด adjusted รายวันของ benchmark (เช่น VOO)

    คืน ``{invested_usd, benchmark_shares, benchmark_value_usd, rounds,
    skipped, skipped_bad_row, skipped_no_price, benchmark_prices_dropped, benchmark_asof}``
    ตัวเลขทั้งสามตัวแรก**การันตีว่า finite เสมอ** — NaN/inf บนเส้นทางเงาแปลว่า "เทียบไม่ได้"
    จึงห้ามหลุดออกไปให้ผู้เรียกเอาไปหาร/แสดงผล และห้ามแทนด้วย ``0.0``
    (0 หุ้น/0 บาท ดูสมเหตุสมผลแต่เป็นเรื่องโกหก) — ต้องข้ามไม้นั้นแล้ว**รายงานจำนวนออกไป**

    ไม้ที่ข้าม แยกเหตุผลให้ผู้เรียกอ่านได้ (``skipped`` = ผลรวมของสองตัวนี้):
    - ``skipped_bad_row``: แถวในสมุดเสียเอง — วันที่/จำนวนหุ้น/ราคาอ่านไม่ออก, NaN, inf
      หรือเงินที่คิดได้ไม่เป็นบวก
    - ``skipped_no_price``: หาราคา benchmark ณ วันซื้อไม่ได้ (ซื้อก่อนช่วงที่มีข้อมูล)

    ฝั่งราคา benchmark ก็รายงานเหมือนกัน — ``benchmark_prices_dropped`` = จำนวนจุดราคาที่
    ถูกคัดทิ้งเพราะใช้ไม่ได้ และ ``benchmark_asof`` = วันของราคาที่ใช้ตี ``benchmark_value_usd``
    (ถ้าราคาล่าสุดใช้ไม่ได้ ค่านี้จะเก่ากว่าวันนี้ ผู้เรียกต้องเตือนก่อนเอาไปเทียบ)

    ไม่มีข้อมูลราคา benchmark ที่ใช้ได้เลย → ``ValueError``
    """
    prices = pd.to_numeric(benchmark_closes, errors="coerce")
    # ราคา NaN/inf/≤0 ไม่ใช่ราคา — ตัดทิ้งเหมือนวันที่ไม่มีข้อมูล (เทียบกับ NaN ได้ False ทั้งคู่)
    usable = (prices > 0.0) & (prices < math.inf)
    closes = prices[usable].sort_index()
    if closes.empty:
        raise ValueError("ไม่มีข้อมูลราคา benchmark ที่ใช้ได้ — เทียบไม่ได้")
    prices_dropped = int((~usable).sum())

    invested = 0.0
    shares_acc = 0.0
    rounds = 0
    skipped_bad_row = 0
    skipped_no_price = 0
    for _, row in buys.iterrows():
        date = pd.to_datetime(row.get("date"), errors="coerce")
        shares = _finite(row.get("shares"))
        price_usd = _finite(row.get("price_usd"))
        if pd.isna(date) or shares is None or price_usd is None:
            skipped_bad_row += 1
            continue
        if shares <= 0.0 or price_usd <= 0.0:
            # ต้องตรวจ**ทีละตัว**: หุ้นติดลบ × ราคาติดลบ ได้เงินบวกที่ดูสมเหตุสมผล
            # ด่านที่ดูแต่ผลคูณจึงรับสองค่าที่ใช้ไม่ได้เข้ามาเป็นไม้ซื้อจริง ๆ
            skipped_bad_row += 1
            continue
        amount_usd = shares * price_usd  # ตัวเลขใหญ่สองตัวคูณกันล้นเป็น inf ได้
        if not math.isfinite(amount_usd) or amount_usd <= 0.0:
            skipped_bad_row += 1
            continue
        aligned = _align_tz(date, closes.index)
        if aligned is None:
            skipped_bad_row += 1
            continue
        try:
            price_at_buy = _finite(closes.asof(aligned))
        except TypeError:  # วันที่เทียบกับดัชนีราคาไม่ได้ — ไม่ปล่อยให้ระเบิดขึ้นไปถึงหน้าจอ
            skipped_bad_row += 1
            continue
        if price_at_buy is None or price_at_buy <= 0.0:
            skipped_no_price += 1
            continue
        bench_shares = amount_usd / price_at_buy  # ราคาเล็กจน underflow → หารแล้วล้นเป็น inf
        if not math.isfinite(bench_shares):
            skipped_no_price += 1
            continue
        invested += amount_usd
        shares_acc += bench_shares
        rounds += 1

    result = {
        "invested_usd": invested,
        "benchmark_shares": shares_acc,
        "benchmark_value_usd": shares_acc * float(closes.iloc[-1]),
        "rounds": rounds,
        "skipped": skipped_bad_row + skipped_no_price,
        "skipped_bad_row": skipped_bad_row,
        "skipped_no_price": skipped_no_price,
        # ราคาที่ถูกคัดทิ้งต้องรายงาน: ถ้าราคา**ล่าสุด**ใช้ไม่ได้ มูลค่าเงาจะถูกตีด้วยราคาเก่า
        # แล้วเอาไปเทียบกับพอร์ต ณ วันนี้แบบคนละวันโดยไม่มีอะไรบอก
        "benchmark_prices_dropped": prices_dropped,
        "benchmark_asof": closes.index[-1],
    }
    # ผลรวม/ผลคูณของตัวเลขที่ finite ทีละตัวยังล้นเป็น inf ได้ — ปล่อยออกไปคือส่ง inf
    # ให้ผู้เรียกเอาไปหารต่อ ยอมล้มดัง ๆ ดีกว่า (ผู้เรียกจับ ValueError อยู่แล้ว)
    if not all(
        math.isfinite(result[key])
        for key in ("invested_usd", "benchmark_shares", "benchmark_value_usd")
    ):
        raise ValueError("ตัวเลขเทียบ benchmark ล้นช่วงที่คำนวณได้ — ข้อมูลนำเข้าผิดรูป เทียบไม่ได้")
    return result


def _npv_tolerance(flows: list[tuple[pd.Timestamp, float]]) -> float:
    """เกณฑ์ยอมรับว่า ``NPV ≈ 0`` — สัมพันธ์กับขนาดกระแสเงิน ไม่ใช่ค่าคงที่.

    พอร์ตหลักล้านสะสมความคลาดเคลื่อนของ float มากกว่าพอร์ตหลักพันเป็นพันเท่า
    เกณฑ์คงที่จึงตึงเกินไปกับพอร์ตใหญ่ (ปฏิเสธรากที่ถูก) และหลวมเกินไปกับพอร์ตเล็ก
    (รับรากที่ผิด)
    """
    return _NPV_REL_TOLERANCE * sum(abs(amount) for _, amount in flows)


def _npv(rate: float, flows: list[tuple[pd.Timestamp, float]], t0: pd.Timestamp) -> float:
    """NPV ณ อัตรา ``rate``.

    ตัวหารที่ล้น/ตกขอบของ float ไม่ปล่อยให้ระเบิดเป็น ``ZeroDivisionError``:
    ตัวหารโตจนล้น → เทอมนั้นลู่เข้า 0, ตัวหาร underflow เป็น 0 → เทอมนั้นเป็น ±inf
    ตามเครื่องหมายของเงิน (เครื่องหมายยังถูก bisection ใช้ตีกรอบได้ต่อ)
    """
    total = 0.0
    for date, amount in flows:
        years = (date - t0).days / _DAYS_PER_YEAR
        try:
            discount = (1.0 + rate) ** years
        except OverflowError:
            discount = math.inf
        if not math.isfinite(discount):
            continue
        total += amount / discount if discount > 0.0 else math.copysign(math.inf, amount)
    return total


def xirr(cashflows: list[tuple[pd.Timestamp, float]]) -> float | None:
    """อัตราผลตอบแทนต่อปีแบบ money-weighted (แก้ NPV=0 ด้วย bisection).

    ``cashflows``: (วันที่, จำนวนเงิน) — เงินที่จ่ายออก (ซื้อ) เป็นลบ,
    เงินที่รับ (ปันผล/มูลค่าปัจจุบัน) เป็นบวก

    คืน ``None`` เมื่อข้อมูลไม่พอ, มีแถวเสีย, หรือไม่มีคำตอบในช่วง [-99%, +1000%] ต่อปี
    — ผู้เรียกแสดง "คำนวณไม่ได้" ห้ามเดาเลขแทน

    **แถวเสียแม้แถวเดียว = คืน None ทั้งก้อน** (วันที่อ่านไม่ออก, จำนวนเงินเป็น NaN/inf
    หรือไม่ใช่ตัวเลข) เพราะถ้ากรองทิ้งแล้วคำนวณต่อ ผลที่ได้คือ XIRR ของพอร์ตที่ไม่มีอยู่จริง
    — ตัดข้อมูลทิ้งเงียบ ๆ แล้วรายงานเป็น %ต่อปี คือการกุตัวเลข
    """
    flows: list[tuple[pd.Timestamp, float]] = []
    for raw_date, raw_amount in cashflows:
        date = pd.to_datetime(raw_date, errors="coerce")
        raw = pd.to_numeric(raw_amount, errors="coerce")
        if pd.isna(date) or pd.isna(raw):
            return None
        amount = float(raw)
        if not math.isfinite(amount):
            return None
        if amount != 0.0:
            flows.append((date, amount))
    if len(flows) < 2:
        return None
    has_negative = any(a < 0 for _, a in flows)
    has_positive = any(a > 0 for _, a in flows)
    if not (has_negative and has_positive):
        return None

    flows.sort(key=lambda item: item[0])
    t0 = flows[0][0]
    low, high = XIRR_LOW, XIRR_HIGH
    npv_low = _npv(low, flows, t0)
    npv_high = _npv(high, flows, t0)
    if math.isnan(npv_low) or math.isnan(npv_high) or npv_low * npv_high > 0:
        return None  # ไม่มีการเปลี่ยนเครื่องหมายในช่วง = ไม่มีรากให้หา
    for _ in range(200):
        mid = (low + high) / 2.0
        npv_mid = _npv(mid, flows, t0)
        if abs(npv_mid) < 1e-9:
            return mid
        if npv_low * npv_mid < 0:
            high = mid
            npv_high = npv_mid
        else:
            low = mid
            npv_low = npv_mid

    # ครบ 200 รอบแล้วยังไม่เจอราก: ต้องพิสูจน์ว่าจุดที่ได้เป็นรากจริงก่อนคืนออกไป
    # ไม่งั้นค่าที่คืนคือขอบของช่วงค้นหา (10.0 = "+1000%/ปี") ซึ่งเป็นเลขที่กุขึ้นมา
    result = (low + high) / 2.0
    npv_result = _npv(result, flows, t0)
    if not math.isfinite(npv_result) or abs(npv_result) > _npv_tolerance(flows):
        return None
    return result
