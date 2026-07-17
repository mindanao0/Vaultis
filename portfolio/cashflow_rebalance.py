# -*- coding: utf-8 -*-
"""Rebalance ด้วย "เงินใหม่" — เทงบ DCA เดือนนี้เข้าตัวที่ต่ำกว่าเป้า (Roadmap Phase 4 ข้อ 12).

money-moving → กติกาเข้ม:
- **ไม่มีการขายเด็ดขาด** (จึงไม่มีภาษี/ค่าคอมขาขาย) — ทำได้แค่แจกงบเดือนนี้
- เป็นโหมด **opt-in ต่อครั้ง** ใน UI เท่านั้น ระบบไม่สลับให้อัตโนมัติ
- ทุกตัวเลขคำนวณในโค้ดนี้และแสดงที่มาครบ (ปัจจุบัน/เป้า/หลังเติม)

วิธี: มูลค่าเป้าหมายหลังเติม = target_weight × (มูลค่าพอร์ต + งบ)
ความขาด (gap) = max(0, เป้าหมาย − ปัจจุบัน) → แจกงบตามสัดส่วน gap
ถ้า gap รวมน้อยกว่างบ ส่วนที่เหลือแจกตาม target weights (พอร์ตชิดเป้าแล้ว = กลับสู่ DCA ปกติ)
ปัดหลักร้อยแบบ largest-remainder เดียวกับ calculate_allocation
"""

from __future__ import annotations

from typing import Any

UNIT_THB = 100


def rebalance_with_new_money(
    current_values_thb: dict[str, float],
    target_weights: dict[str, float],
    budget_thb: float,
) -> dict[str, dict[str, Any]]:
    """แผนแจกงบเดือนนี้ให้พอร์ตขยับเข้าเป้ามากที่สุดโดยไม่ขาย.

    คืน ``{ticker: {amount_thb, current_pct, target_pct, projected_pct}}``
    พอร์ตว่าง/งบ ≤ 0/ไม่มีเป้าหมาย → ValueError (โหมดนี้มีความหมายเมื่อมีพอร์ตจริงเท่านั้น)
    """
    holdings = {t: float(v) for t, v in current_values_thb.items() if float(v) > 0}
    if not holdings:
        raise ValueError("ยังไม่มีพอร์ตจริง — โหมดดึงเข้าเป้าใช้ไม่ได้ (ใช้แผน DCA ปกติ)")
    if budget_thb <= 0:
        raise ValueError("งบต้องมากกว่า 0")
    weights = {t: float(w) for t, w in target_weights.items() if float(w) > 0}
    if not weights:
        raise ValueError("ไม่มีน้ำหนักเป้าหมาย")
    weight_total = sum(weights.values())
    weights = {t: w / weight_total for t, w in weights.items()}

    total_now = sum(holdings.values())
    total_after = total_now + float(budget_thb)

    gaps = {
        t: max(0.0, weights[t] * total_after - holdings.get(t, 0.0)) for t in weights
    }
    gap_total = sum(gaps.values())

    exact: dict[str, float] = {}
    if gap_total >= budget_thb:
        # งบไม่พอปิด gap ทั้งหมด → แจกตามสัดส่วนความขาด
        for t in weights:
            exact[t] = budget_thb * (gaps[t] / gap_total) if gap_total > 0 else 0.0
    else:
        # ปิด gap ได้หมด ส่วนเกินแจกตาม target weights (กลับสู่ DCA ปกติ)
        leftover = budget_thb - gap_total
        for t in weights:
            exact[t] = gaps[t] + leftover * weights[t]

    # ปัดหลักร้อย + แจกเศษให้ตัวที่เศษมากสุด (ใช้งบครบ ไม่หายเงียบ)
    total_units = int(budget_thb // UNIT_THB)
    exact_units = {t: (v / budget_thb) * total_units for t, v in exact.items()}
    units = {t: int(v) for t, v in exact_units.items()}
    leftover_units = total_units - sum(units.values())
    if leftover_units > 0:
        by_remainder = sorted(exact_units, key=lambda t: exact_units[t] - units[t], reverse=True)
        for t in by_remainder[:leftover_units]:
            units[t] += 1

    plan: dict[str, dict[str, Any]] = {}
    for t in sorted(weights, key=lambda x: units.get(x, 0), reverse=True):
        amount = units.get(t, 0) * UNIT_THB
        if amount <= 0:
            continue
        current_value = holdings.get(t, 0.0)
        plan[t] = {
            "amount_thb": amount,
            "current_pct": round(current_value / total_now * 100.0, 1),
            "target_pct": round(weights[t] * 100.0, 1),
            "projected_pct": round((current_value + amount) / total_after * 100.0, 1),
        }
    return plan
