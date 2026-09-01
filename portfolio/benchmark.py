# -*- coding: utf-8 -*-
"""เทียบพอร์ตจริงกับ benchmark อย่างต่อเนื่อง (Roadmap Phase 4 ข้อ 14).

สองเครื่องมือ (สถิติเชิงพรรณนาจาก ledger + ราคาจริง — ไม่เข้าเลขคะแนน/จัดสรร):

- ``shadow_benchmark``: "ถ้า**กระแสเงินเข้า-ออกจากภายนอกชุดเดียวกัน** ไปลง benchmark
  (VOO) ล้วน วันนี้ได้เท่าไร" — money-weighted ตรง ๆ ไม่ต้องมีสมมติฐานอัตราผลตอบแทน
- ``xirr``: ผลตอบแทน %ต่อปีแบบ money-weighted จากกระแสเงินสดจริง (bisection บน NPV)

**กติกาข้อเดียวที่ทำให้การเทียบนี้ยุติธรรม: สองขาต้องเจอตารางเงินเข้า-ออกชุดเดียวกัน**
(FIX_PLAN ข้อ 3.1) ไม้ซื้อ = เงินเข้า (เงาซื้อ benchmark) · ปันผลที่รับ = เงินที่พอร์ตจริง
คายออกมา (เงา**ขาย** benchmark มูลค่าเท่ากัน วันเดียวกัน) แล้วเทียบเฉพาะมูลค่าปลายทาง

เหตุผลที่ขาปันผลจำเป็น ไม่ใช่ของแถม — ราคาที่ป้อนเข้ามาคือ **Adjusted Close** ซึ่งเป็น
total return (ปันผลของ benchmark ถูกลงทุนต่อให้เองในตัวเลข) ขณะที่มูลค่าพอร์ตจริงบนหน้าจอ
คือ ``หุ้น × ราคาปิดดิบ`` = ราคาล้วน ปันผลที่ผู้ใช้รับเป็นเงินสดออกไปแล้วไม่ถูกนับกลับ
วัดจริง 2026-08-08: VOO 3 ปี total **79.29%** vs price **72.38%** ⇒ **เอียงเข้าข้าง VOO
1.58 จุด/ปี ตลอดเวลา** สมุดที่ซื้อ VOO ล้วนวันเดียวกันเป๊ะจึงยังโชว์ว่า "แพ้ VOO"
การบังคับให้เงาคายเงินก้อนเดียวกันออกในวันเดียวกัน หักส่วนที่ปันผลถูกลงทุนต่อออกพอดี
(แท่งล่าสุดของ Adj Close เท่ากับ Close เป๊ะ มูลค่าปลายทางสองขาจึงอยู่บนฐานราคาเดียวกัน)

ผลพลอยได้ที่สำคัญ: **ไม้ DRIP หักล้างตัวเองในขาเงา** (ปันผลเข้า → ซื้อออก วันเดียวกัน
จำนวนเดียวกัน) เดิมไม้ที่ซื้อด้วยเงินปันผลถูกนับเป็น "เงินใหม่จากภายนอก" ⇒ ขาเงาพองเกินจริง
และ ``invested_usd`` ที่พองไปเป็นตัวหารของ %พอร์ตจริงอีกชั้น — ผู้ใช้ที่ลงทุนปันผลต่อ
อย่างมีวินัยที่สุดจึงถูกลงโทษหนักที่สุด

**วัดกับข้อมูล VOO จริง 2026-08-08** (ซื้อ 10 หุ้น @412.29 เมื่อ 2023-08-08 ไม่ซื้ออีกเลย
บันทึกปันผลจริงครบ 12 งวด รวม 209.01 USD · พอร์ตจริงวันนี้ 7,107.10 USD):

    โมเดลเดิม (ไม่คายปันผล)  เงา = 7,391.79 USD  → "แพ้ VOO 284.69" = **+1.32 จุด/ปี**
    โมเดลนี้                 เงา = 7,106.55 USD  → ส่วนต่าง −0.55   = **−0.003 จุด/ปี**

เศษ −0.0077% ที่เหลือ **ไม่ใช่ศูนย์เป๊ะ และไม่ควรอ้างว่าเป็นศูนย์**: ตัวคูณปรับราคาของ
yfinance คิดจากราคาปิด**ก่อน**วัน ex-date ขณะที่เงาขายที่ราคาปิด**ของ**วัน ex-date ซึ่ง
วันนั้นราคายังขยับด้วยเหตุอื่นนอกจากปันผล — เป็นความต่างระดับ 0.003 จุด/ปี เทียบกับอคติ
1.32 จุด/ปีที่ถูกกำจัดไป (``tests/test_shadow_symmetry.py`` ใช้ตัวคูณเชิงทฤษฎี
``f = P_ex/(P_ex + d)`` จึงตรวจความสมมาตรได้ถึงทศนิยมสุดท้าย)
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

XIRR_LOW, XIRR_HIGH = -0.9999, 10.0  # เผื่อเคสขาดทุนเกือบหมด (root ต่ำกว่า -99%)
_DAYS_PER_YEAR = 365.25
_NPV_REL_TOLERANCE = 1e-6  # เทียบกับผลรวม |กระแสเงิน| — ดู _npv_tolerance()

# ลำดับของเหตุการณ์ในวันเดียวกัน: **ซื้อก่อน ปันผลทีหลัง** — ต้องถือก่อนถึงจะได้ปันผล
# (จำนวนหุ้นปลายทางไม่ขึ้นกับลำดับ เพราะแต่ละเหตุการณ์ใช้ราคาของวันตัวเอง แต่ด่าน
#  "ขายเกินที่ถือ" ด้านล่างขึ้นกับลำดับ — เรียงผิดจะกล่าวหาสมุดที่ถูกต้องว่าผิด)
_EVENT_BUY = 0
_EVENT_PAYOUT = 1

# ความคลาดเคลื่อนของ float ที่ยอมให้จำนวนหุ้นติดลบได้ (สัมพันธ์กับจำนวนหุ้นที่เคยถือสูงสุด)
# ปันผลที่ล้างพอร์ตพอดีอาจได้ −1e-16 ซึ่งไม่ใช่ "ขายเกิน" — แต่เลขติดลบจริง ๆ ต้องดัง
_SHARE_REL_EPS = 1e-9


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


def _buy_amounts(buys: pd.DataFrame) -> tuple[list[tuple[pd.Timestamp, float]], int]:
    """(วันที่, เงิน USD) ของไม้ซื้อที่ใช้ได้ + จำนวนแถวที่อ่านไม่ออก."""
    rows: list[tuple[pd.Timestamp, float]] = []
    bad = 0
    for _, row in buys.iterrows():
        date = pd.to_datetime(row.get("date"), errors="coerce")
        shares = _finite(row.get("shares"))
        price_usd = _finite(row.get("price_usd"))
        if pd.isna(date) or shares is None or price_usd is None:
            bad += 1
            continue
        if shares <= 0.0 or price_usd <= 0.0:
            # ต้องตรวจ**ทีละตัว**: หุ้นติดลบ × ราคาติดลบ ได้เงินบวกที่ดูสมเหตุสมผล
            # ด่านที่ดูแต่ผลคูณจึงรับสองค่าที่ใช้ไม่ได้เข้ามาเป็นไม้ซื้อจริง ๆ
            bad += 1
            continue
        amount_usd = shares * price_usd  # ตัวเลขใหญ่สองตัวคูณกันล้นเป็น inf ได้
        if not math.isfinite(amount_usd) or amount_usd <= 0.0:
            bad += 1
            continue
        rows.append((date, amount_usd))
    return rows, bad


def _payout_amounts(
    payouts: pd.DataFrame | None,
) -> tuple[list[tuple[pd.Timestamp, float]], int]:
    """(วันที่, เงิน USD) ของปันผลที่พอร์ตจริงรับมา + จำนวนแถวที่อ่านไม่ออก.

    ``None``/ตารางว่าง = **ไม่มีแถวปันผลในสมุด** ซึ่งไม่ใช่ความล้มเหลว (ผู้ใช้อาจยังไม่เคย
    ได้ปันผล หรือยังไม่ได้บันทึก) — แต่ผู้เรียกต้องรู้ว่าตัวเองอยู่ในกรณีไหน เพราะ
    "ไม่เคยได้ปันผล" กับ "ได้แล้วไม่ได้บันทึก" ให้ตัวเลขต่างกันมาก (ดู docstring ของโมดูล)

    ยอด ``<= 0`` ถือเป็นแถวเสีย ไม่ใช่แถวที่ข้ามได้เงียบ ๆ — ปันผล 0 บาทคือข้อมูลผิดรูป
    ที่ต้องมีคนไปแก้ ไม่ใช่เหตุการณ์ที่เกิดขึ้นจริง (กติกาเดียวกับ ``shares <= 0`` ของไม้ซื้อ)
    """
    if payouts is None or payouts.empty:
        return [], 0
    rows: list[tuple[pd.Timestamp, float]] = []
    bad = 0
    for _, row in payouts.iterrows():
        date = pd.to_datetime(row.get("date"), errors="coerce")
        amount_usd = _finite(row.get("amount_usd"))
        if pd.isna(date) or amount_usd is None or amount_usd <= 0.0:
            bad += 1
            continue
        rows.append((date, amount_usd))
    return rows, bad


def shadow_benchmark(
    buys: pd.DataFrame,
    benchmark_closes: pd.Series,
    payouts: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """จำลอง "เอากระแสเงินเข้า-ออกจากภายนอกชุดเดียวกัน ไปลง benchmark ล้วน".

    ``buys``: แถวซื้อจริง ต้องมี ``date``, ``shares``, ``price_usd`` — เงิน**เข้า** พอร์ต
    ``payouts``: แถวปันผลที่ **รับมาแล้ว** ต้องมี ``date``, ``amount_usd`` — เงิน**ออก**
    จากพอร์ต (เงาต้องขาย benchmark มูลค่าเท่ากันในวันเดียวกัน) ปล่อยว่าง = ไม่มีแถวปันผล
    ``benchmark_closes``: ราคาปิด adjusted รายวันของ benchmark (เช่น VOO)

    **ทำไมขาปันผลถึงจำเป็น** อ่าน docstring ของโมดูล — สรุปคือราคาที่ป้อนเข้ามาเป็น
    total return ส่วนมูลค่าพอร์ตจริงเป็นราคาล้วน ไม่หักออกก็เอียงเข้าข้าง benchmark
    ราว 1.6 จุด/ปี ตลอดเวลา และไม้ DRIP จะถูกนับเป็นเงินใหม่จากภายนอก

    คืน ``{invested_usd, payout_usd, net_external_usd, benchmark_shares,
    benchmark_value_usd, rounds, payout_rounds, skipped, skipped_bad_row,
    skipped_no_price, payouts_skipped, payouts_skipped_bad_row, payouts_skipped_no_price,
    benchmark_prices_dropped, benchmark_asof}``
    ตัวเลขเงินทุกตัว**การันตีว่า finite เสมอ** — NaN/inf บนเส้นทางเงาแปลว่า "เทียบไม่ได้"
    จึงห้ามหลุดออกไปให้ผู้เรียกเอาไปหาร/แสดงผล และห้ามแทนด้วย ``0.0``
    (0 หุ้น/0 บาท ดูสมเหตุสมผลแต่เป็นเรื่องโกหก) — ต้องข้ามแถวนั้นแล้ว**รายงานจำนวนออกไป**

    แถวที่ข้าม แยกเหตุผลให้ผู้เรียกอ่านได้ (``skipped``/``payouts_skipped`` = ผลรวมของคู่ตัวเอง):
    - ``*_bad_row``: แถวในสมุดเสียเอง — วันที่/จำนวนหุ้น/ราคา/ยอดเงินอ่านไม่ออก, NaN, inf
      หรือเงินที่คิดได้ไม่เป็นบวก
    - ``*_no_price``: หาราคา benchmark ณ วันนั้นไม่ได้ (เกิดก่อนช่วงที่มีข้อมูล)

    ⚠ **ปันผลที่ข้ามไม่ใช่เรื่องเล็กเท่าไม้ซื้อที่ข้าม** — ไม้ซื้อที่ข้ามหายไปจากทั้งตัวตั้ง
    และตัวหาร แต่ปันผลที่ข้ามแปลว่าเงาเก็บเงินที่ควรคายออกไว้กับตัว = เอียงเข้าข้าง benchmark
    ทางเดียว ผู้เรียกควรปฏิเสธที่จะแสดงคำตัดสิน "ชนะ/แพ้" เมื่อ ``payouts_skipped > 0``

    ฝั่งราคา benchmark ก็รายงานเหมือนกัน — ``benchmark_prices_dropped`` = จำนวนจุดราคาที่
    ถูกคัดทิ้งเพราะใช้ไม่ได้ และ ``benchmark_asof`` = วันของราคาที่ใช้ตี ``benchmark_value_usd``
    (ถ้าราคาล่าสุดใช้ไม่ได้ ค่านี้จะเก่ากว่าวันนี้ ผู้เรียกต้องเตือนก่อนเอาไปเทียบ)

    ``ValueError`` เมื่อ: ไม่มีข้อมูลราคา benchmark ที่ใช้ได้เลย · ตัวเลขล้นช่วงที่คำนวณได้ ·
    ปันผลที่บันทึกไว้มากกว่ามูลค่าที่ขาเงาถืออยู่ ณ วันนั้น (สมุดมีปันผลที่ไม่มีไม้ซื้อรองรับ)
    """
    prices = pd.to_numeric(benchmark_closes, errors="coerce")
    # ราคา NaN/inf/≤0 ไม่ใช่ราคา — ตัดทิ้งเหมือนวันที่ไม่มีข้อมูล (เทียบกับ NaN ได้ False ทั้งคู่)
    usable = (prices > 0.0) & (prices < math.inf)
    closes = prices[usable].sort_index()
    if closes.empty:
        raise ValueError("ไม่มีข้อมูลราคา benchmark ที่ใช้ได้ — เทียบไม่ได้")
    prices_dropped = int((~usable).sum())

    buy_rows, skipped_bad_row = _buy_amounts(buys)
    payout_rows, payouts_skipped_bad_row = _payout_amounts(payouts)

    # ปรับ tz **ก่อน** เรียง: สมุดจริงเป็น tz-naive แต่ข้อมูลนำเข้าบางแหล่งติด tz มาด้วย
    # เอาไปเรียงรวมกันดิบ ๆ จะได้ ``TypeError: Cannot compare tz-naive and tz-aware``
    # กลางทาง แทนที่จะเป็นแถวที่ถูก "ข้าม + นับ" ตามกติกาของไฟล์นี้
    events: list[tuple[pd.Timestamp, int, float]] = []
    for date, amount_usd in buy_rows:
        aligned = _align_tz(date, closes.index)
        if aligned is None:
            skipped_bad_row += 1
            continue
        events.append((aligned, _EVENT_BUY, amount_usd))
    for date, amount_usd in payout_rows:
        aligned = _align_tz(date, closes.index)
        if aligned is None:
            payouts_skipped_bad_row += 1
            continue
        events.append((aligned, _EVENT_PAYOUT, amount_usd))
    events.sort(key=lambda item: (item[0], item[1]))

    invested = 0.0
    paid_out = 0.0
    shares_acc = 0.0
    peak_shares = 0.0
    rounds = 0
    payout_rounds = 0
    skipped_no_price = 0
    payouts_skipped_no_price = 0
    for aligned, kind, amount_usd in events:
        is_buy = kind == _EVENT_BUY
        try:
            price_at = _finite(closes.asof(aligned))
        except TypeError:  # วันที่เทียบกับดัชนีราคาไม่ได้ — ไม่ปล่อยให้ระเบิดขึ้นไปถึงหน้าจอ
            if is_buy:
                skipped_bad_row += 1
            else:
                payouts_skipped_bad_row += 1
            continue
        # ราคาเล็กจน underflow → หารแล้วล้นเป็น inf จึงต้องตรวจผลหารด้วย ไม่ใช่แค่ตัวหาร
        units = amount_usd / price_at if (price_at is not None and price_at > 0.0) else None
        if units is None or not math.isfinite(units):
            if is_buy:
                skipped_no_price += 1
            else:
                payouts_skipped_no_price += 1
            continue
        if is_buy:
            invested += amount_usd
            shares_acc += units
            peak_shares = max(peak_shares, shares_acc)
            rounds += 1
            continue
        shares_acc -= units
        paid_out += amount_usd
        payout_rounds += 1
        tolerance = _SHARE_REL_EPS * max(1.0, peak_shares)
        if shares_acc < -tolerance:
            # ขายเกินที่ถือ = สมุดมีปันผลที่ไม่มีไม้ซื้อรองรับ (หรือวันที่ผิด) ผลลัพธ์จะเป็น
            # หุ้นติดลบ → มูลค่าเงาติดลบ ซึ่งเป็นตัวเลขที่ไม่มีความหมายทางการเงินเลย
            raise ValueError(
                f"ปันผลที่บันทึกไว้ ณ {pd.Timestamp(aligned):%Y-%m-%d} มากกว่ามูลค่าที่ขา "
                "benchmark ถืออยู่ ณ วันนั้น — สมุดบัญชีมีแถวปันผลที่ไม่มีไม้ซื้อรองรับ "
                "(หรือวันที่ผิด) จึงเทียบไม่ได้"
            )
        if shares_acc < 0.0:
            shares_acc = 0.0  # ปันผลที่ล้างพอร์ตพอดี — เศษ float ติดลบ ไม่ใช่การขายเกิน

    result = {
        "invested_usd": invested,
        "payout_usd": paid_out,
        # เงินสุทธิจาก **ภายนอก** — ตัวหารที่ถูกต้องของ %ผลตอบแทนทั้งสองขา
        # (ไม้ DRIP บวกเข้า invested แล้วถูกปันผลก้อนเดียวกันหักออก เหลือศูนย์พอดี)
        "net_external_usd": invested - paid_out,
        "benchmark_shares": shares_acc,
        "benchmark_value_usd": shares_acc * float(closes.iloc[-1]),
        "rounds": rounds,
        "payout_rounds": payout_rounds,
        "skipped": skipped_bad_row + skipped_no_price,
        "skipped_bad_row": skipped_bad_row,
        "skipped_no_price": skipped_no_price,
        "payouts_skipped": payouts_skipped_bad_row + payouts_skipped_no_price,
        "payouts_skipped_bad_row": payouts_skipped_bad_row,
        "payouts_skipped_no_price": payouts_skipped_no_price,
        # ราคาที่ถูกคัดทิ้งต้องรายงาน: ถ้าราคา**ล่าสุด**ใช้ไม่ได้ มูลค่าเงาจะถูกตีด้วยราคาเก่า
        # แล้วเอาไปเทียบกับพอร์ต ณ วันนี้แบบคนละวันโดยไม่มีอะไรบอก
        "benchmark_prices_dropped": prices_dropped,
        "benchmark_asof": closes.index[-1],
    }
    # ผลรวม/ผลคูณของตัวเลขที่ finite ทีละตัวยังล้นเป็น inf ได้ — ปล่อยออกไปคือส่ง inf
    # ให้ผู้เรียกเอาไปหารต่อ ยอมล้มดัง ๆ ดีกว่า (ผู้เรียกจับ ValueError อยู่แล้ว)
    if not all(
        math.isfinite(result[key])
        for key in (
            "invested_usd",
            "payout_usd",
            "net_external_usd",
            "benchmark_shares",
            "benchmark_value_usd",
        )
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
