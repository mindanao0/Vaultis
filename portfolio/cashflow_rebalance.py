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


class RebalancePlan(dict):
    """แผนแจกงบ (``{ticker: {...}}``) **พร้อมเศษที่แจกไม่ลง**.

    เป็น ``dict`` ทุกประการเพื่อให้ผู้เรียกเดิมใช้ต่อได้ แต่เพิ่ม ``unallocated_thb``
    ที่ผู้เรียก **ต้องแสดงให้ผู้ใช้เห็น** — การปัดหลักร้อยทำให้เงินตกหล่นได้ถึง 99 บาท
    และ "ตัดทิ้งเงียบ" ผิดพอกับ "กุตัวเลข" (AUDIT_2026-08-06 D3.9)

    ``budget_thb`` ติดมาด้วยเพื่อให้ตรวจ invariant ได้ที่ปลายทาง:
    ``sum(amount_thb) + unallocated_thb == budget_thb``
    """

    def __init__(self, *args: Any, budget_thb: float = 0.0, unallocated_thb: float = 0.0, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.budget_thb = float(budget_thb)
        self.unallocated_thb = float(unallocated_thb)


def rebalance_with_new_money(
    current_values_thb: dict[str, float],
    target_weights: dict[str, float],
    budget_thb: float,
) -> RebalancePlan:
    """แผนแจกงบเดือนนี้ให้พอร์ตขยับเข้าเป้ามากที่สุดโดยไม่ขาย.

    คืน ``RebalancePlan`` = ``{ticker: {amount_thb, current_pct, target_pct, projected_pct}}``
    บวกแอตทริบิวต์ ``unallocated_thb`` (เศษจากการปัดหลักร้อย — ต้องรายงานต่อผู้ใช้)

    พอร์ตว่าง/งบไม่ถึงหนึ่งก้อน (100 บาท)/ไม่มีเป้าหมาย → ValueError
    (โหมดนี้มีความหมายเมื่อมีพอร์ตจริงเท่านั้น และงบที่แจกไม่ลงสักกองไม่ใช่ "แผนว่าง")
    """
    holdings = {t: float(v) for t, v in current_values_thb.items() if float(v) > 0}
    if not holdings:
        raise ValueError("ยังไม่มีพอร์ตจริง — โหมดดึงเข้าเป้าใช้ไม่ได้ (ใช้แผน DCA ปกติ)")
    if budget_thb < UNIT_THB:
        # เดิมงบ 1–99 บาทผ่านด่านนี้แล้วคืนแผนว่างเงียบ ๆ (ปัดหลักร้อยแล้วเหลือ 0 ก้อน)
        raise ValueError(
            f"งบต้องอย่างน้อย {UNIT_THB} บาท (แผนปัดเป็นหลักร้อย งบน้อยกว่านี้แจกไม่ลงสักกอง)"
        )
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

    plan = RebalancePlan(budget_thb=float(budget_thb))
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
    # เศษที่หน่วยละ 100 บาทแจกไม่ลง — ผู้เรียกต้องแสดงให้ผู้ใช้เห็น ห้ามกลืนหาย
    plan.unallocated_thb = round(
        float(budget_thb) - sum(item["amount_thb"] for item in plan.values()), 2
    )
    return plan
