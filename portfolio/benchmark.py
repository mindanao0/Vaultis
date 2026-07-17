# -*- coding: utf-8 -*-
"""เทียบพอร์ตจริงกับ benchmark อย่างต่อเนื่อง (Roadmap Phase 4 ข้อ 14).

สองเครื่องมือ (สถิติเชิงพรรณนาจาก ledger + ราคาจริง — ไม่เข้าเลขคะแนน/จัดสรร):

- ``shadow_benchmark``: "ถ้าเงินก้อนเดียวกัน วันเดียวกัน ซื้อ benchmark (VOO) ล้วน
  วันนี้ได้เท่าไร" — money-weighted ตรง ๆ ไม่ต้องมีสมมติฐานอัตราผลตอบแทน
- ``xirr``: ผลตอบแทน %ต่อปีแบบ money-weighted จากกระแสเงินสดจริง (bisection บน NPV)
"""

from __future__ import annotations

from typing import Any

import pandas as pd

XIRR_LOW, XIRR_HIGH = -0.9999, 10.0  # เผื่อเคสขาดทุนเกือบหมด (root ต่ำกว่า -99%)
_DAYS_PER_YEAR = 365.25


def shadow_benchmark(buys: pd.DataFrame, benchmark_closes: pd.Series) -> dict[str, Any]:
    """จำลอง "ซื้อ benchmark ล้วนด้วยเงิน (USD) และวันเดียวกับไม้จริงทุกไม้".

    ``buys``: แถวซื้อจริง ต้องมี ``date``, ``shares``, ``price_usd``
    ``benchmark_closes``: ราคาปิด adjusted รายวันของ benchmark (เช่น VOO)

    คืน ``{invested_usd, benchmark_shares, benchmark_value_usd, rounds, skipped}``
    ไม้ที่หาราคา benchmark ณ วันซื้อไม่ได้ = ข้ามทั้งสองขา + นับ ``skipped`` (ไม่เดาราคา)
    ไม่มีข้อมูลราคาเลย → ValueError
    """
    closes = pd.to_numeric(benchmark_closes, errors="coerce").dropna().sort_index()
    if closes.empty:
        raise ValueError("ไม่มีข้อมูลราคา benchmark — เทียบไม่ได้")

    invested = 0.0
    shares_acc = 0.0
    rounds = 0
    skipped = 0
    for _, row in buys.iterrows():
        date = pd.to_datetime(row.get("date"), errors="coerce")
        amount_usd = float(pd.to_numeric(row.get("shares"), errors="coerce") or 0.0) * float(
            pd.to_numeric(row.get("price_usd"), errors="coerce") or 0.0
        )
        if pd.isna(date) or amount_usd <= 0:
            skipped += 1
            continue
        price_at_buy = closes.asof(date)
        if pd.isna(price_at_buy) or float(price_at_buy) <= 0:
            skipped += 1
            continue
        invested += amount_usd
        shares_acc += amount_usd / float(price_at_buy)
        rounds += 1

    return {
        "invested_usd": invested,
        "benchmark_shares": shares_acc,
        "benchmark_value_usd": shares_acc * float(closes.iloc[-1]),
        "rounds": rounds,
        "skipped": skipped,
    }


def _npv(rate: float, flows: list[tuple[pd.Timestamp, float]], t0: pd.Timestamp) -> float:
    total = 0.0
    for date, amount in flows:
        years = (date - t0).days / _DAYS_PER_YEAR
        total += amount / (1.0 + rate) ** years
    return total


def xirr(cashflows: list[tuple[pd.Timestamp, float]]) -> float | None:
    """อัตราผลตอบแทนต่อปีแบบ money-weighted (แก้ NPV=0 ด้วย bisection).

    ``cashflows``: (วันที่, จำนวนเงิน) — เงินที่จ่ายออก (ซื้อ) เป็นลบ,
    เงินที่รับ (ปันผล/มูลค่าปัจจุบัน) เป็นบวก

    คืน ``None`` เมื่อข้อมูลไม่พอหรือไม่มีคำตอบในช่วง [-99%, +1000%] ต่อปี
    — ผู้เรียกแสดง "คำนวณไม่ได้" ห้ามเดาเลขแทน
    """
    flows = [
        (pd.to_datetime(d), float(a))
        for d, a in cashflows
        if pd.notna(pd.to_datetime(d, errors="coerce")) and float(a) != 0.0
    ]
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
    if npv_low * npv_high > 0:
        return None
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
    return (low + high) / 2.0
