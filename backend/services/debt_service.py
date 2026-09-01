"""Debt payoff optimizer: Avalanche and Snowball methods."""

from __future__ import annotations

from ..models.debt_models import (
    Debt,
    DebtComparison,
    DebtResult,
    DebtSchedule,
    NegativeAmortizationFlag,
    PaymentEntry,
    SensitivityResult,
)

_MAX_MONTHS = 600  # 50-year safety cap


def _simulate(debts_input: list[Debt], monthly_budget: float, method: str) -> DebtResult:
    n = len(debts_input)

    # AUDIT.md M10: ถ้างบต่อเดือนน้อยกว่าผลรวมยอดขั้นต่ำ ระบบเดิมจะ "จ่าย" เกินงบเงียบ ๆ
    # แล้วรายงานว่าหนี้หมดได้ ทั้งที่ในความจริงผู้ใช้จ่ายไม่ไหว
    total_min = sum(d.min_payment for d in debts_input)
    if monthly_budget < total_min:
        raise ValueError(
            f"งบชำระต่อเดือน ({monthly_budget:,.0f}) น้อยกว่ายอดขั้นต่ำรวมของหนี้ทั้งหมด "
            f"({total_min:,.0f}) — แผนนี้เป็นไปไม่ได้จริง กรุณาเพิ่มงบหรือเจรจาลดยอดขั้นต่ำ"
        )

    balances = [d.balance for d in debts_input]
    monthly_rates = [d.interest_rate / 100 / 12 for d in debts_input]
    schedules: list[list[PaymentEntry]] = [[] for _ in range(n)]
    interest_by_debt = [0.0] * n
    neg_amort_months: list[list[int]] = [[] for _ in range(n)]
    month = 0

    while any(b > 0.005 for b in balances) and month < _MAX_MONTHS:
        month += 1

        interests = [
            balances[i] * monthly_rates[i] if balances[i] > 0.005 else 0.0
            for i in range(n)
        ]

        # Minimum payment on every active debt first
        payments = [0.0] * n
        budget_left = monthly_budget
        for i in range(n):
            if balances[i] > 0.005:
                mp = min(debts_input[i].min_payment, balances[i] + interests[i])
                payments[i] = mp
                budget_left -= mp
        budget_left = max(0.0, budget_left)

        # Extra money → priority debt
        if method == "avalanche":
            priority = sorted(
                (i for i in range(n) if balances[i] > 0.005),
                key=lambda i: -debts_input[i].interest_rate,
            )
        else:
            priority = sorted(
                (i for i in range(n) if balances[i] > 0.005),
                key=lambda i: balances[i],
            )

        for i in priority:
            if budget_left <= 0:
                break
            headroom = max(0.0, balances[i] + interests[i] - payments[i])
            extra = min(budget_left, headroom)
            payments[i] += extra
            budget_left -= extra

        # Update balances and record payment
        for i in range(n):
            if balances[i] <= 0.005:
                continue
            interest = interests[i]
            payment = payments[i]
            new_balance = max(0.0, balances[i] + interest - payment)
            # AUDIT_2026-08-06 H4: ต้องนับ "ดอกเบี้ยที่เกิดขึ้น" ไม่ใช่ "ดอกเบี้ยที่จ่ายไหว"
            # เดิมใช้ min(interest, payment) → ส่วนที่จ่ายไม่ไหวถูกทบเข้าเงินต้น แล้วตอน
            # ชำระคืนภายหลังถูกจัดประเภทเป็น principal ⇒ total_interest ต่ำกว่าจริงเสมอ
            # และต่ำไม่เท่ากันระหว่าง avalanche/snowball จน interest_saved พลิกเครื่องหมายได้
            principal_paid = payment - interest      # ติดลบได้เมื่อหนี้โต — ห้ามบีบเป็น 0
            interest_by_debt[i] += interest
            if payment < interest - 0.005:
                neg_amort_months[i].append(month)
            balances[i] = new_balance
            schedules[i].append(
                PaymentEntry(
                    month=month,
                    payment=round(payment, 2),
                    principal=round(principal_paid, 2),
                    interest=round(interest, 2),
                    remaining_balance=round(new_balance, 2),
                )
            )

    if any(b > 0.005 for b in balances):
        # ชนเพดาน 50 ปีแล้วยังมีหนี้เหลือ — ต้องบอกตรง ๆ ไม่ใช่รายงานว่าจ่ายหมดใน 600 เดือน
        remaining = sum(b for b in balances if b > 0.005)
        # K8: สาเหตุมีสองแบบ ต้องแยก ไม่งั้นสินเชื่อ 0% ที่ผ่อนช้า (เพิ่งเปิดรับหลัง ge=0)
        # จะถูกโทษว่า "ดอกเบี้ยเดินเร็วกว่าเงินต้น" ทั้งที่ไม่มีดอกเบี้ยสักบาท
        still_growing = any(
            months and months[-1] > month - 12 for months in neg_amort_months
        )
        cause = (
            "ดอกเบี้ยเดินเร็วกว่าเงินต้นที่จ่ายได้ กรุณาเพิ่มงบหรือรีไฟแนนซ์"
            if still_growing
            else "งบพอจ่ายดอกเบี้ยไหว แต่เงินต้นลดช้าเกินกว่าจะหมดใน 50 ปี กรุณาเพิ่มงบต่อเดือน"
        )
        raise ValueError(
            f"ด้วยงบ {monthly_budget:,.0f} บาท/เดือน หนี้จะไม่มีวันหมด "
            f"(เหลือ {remaining:,.0f} บาท หลังผ่านไป {_MAX_MONTHS // 12} ปี) — {cause}"
        )

    debt_schedules = [
        DebtSchedule(
            name=debts_input[i].name,
            payments=schedules[i],
            # ค่าเดียวกับที่เข้า total_interest ของทั้งแผน จะได้ตรงกับ remaining_balance ที่โชว์
            total_interest=round(interest_by_debt[i], 2),
            months_to_payoff=len(schedules[i]),
            negative_amortization_months=neg_amort_months[i],
        )
        for i in range(n)
    ]

    return DebtResult(
        method=method,  # type: ignore[arg-type]
        monthly_budget=monthly_budget,
        total_interest=round(sum(interest_by_debt), 2),
        months_to_payoff=month,
        schedules=debt_schedules,
    )


def compare_methods(debts: list[Debt], monthly_budget: float) -> DebtComparison:
    avalanche = _simulate(debts, monthly_budget, "avalanche")
    snowball = _simulate(debts, monthly_budget, "snowball")
    return DebtComparison(
        avalanche=avalanche,
        snowball=snowball,
        interest_saved=round(snowball.total_interest - avalanche.total_interest, 2),
        months_saved=snowball.months_to_payoff - avalanche.months_to_payoff,
    )


def _neg_amort_flags(result: DebtResult) -> list[NegativeAmortizationFlag]:
    """ดึงธง negative amortization ออกจากแผน — ใช้ค่าที่ ``_simulate`` ตรวจไว้แล้วเท่านั้น
    (นิยามมีที่เดียว ห้ามคำนวณเงื่อนไข payment < interest ซ้ำที่นี่)."""
    return [
        NegativeAmortizationFlag(
            debt_index=i,
            name=schedule.name,
            months=list(schedule.negative_amortization_months),
        )
        for i, schedule in enumerate(result.schedules)
        if schedule.negative_amortization_months
    ]


def sensitivity_analysis(
    debts: list[Debt],
    monthly_budget: float,
    method: str,
    extra_payments: list[float] | None = None,
) -> list[SensitivityResult]:
    if extra_payments is None:
        extra_payments = [500, 1000, 2000, 5000]

    base = _simulate(debts, monthly_budget, method)
    results: list[SensitivityResult] = [
        SensitivityResult(
            extra_payment=0.0,
            total_interest=base.total_interest,
            months_to_payoff=base.months_to_payoff,
            interest_saved=0.0,
            # K8: ตัวเลือกที่ "ดอกเบี้ยรวมน้อยกว่า" อาจเป็นตัวเลือกที่หนี้ยังโตขึ้นอยู่
            # ผู้ใช้ต้องเห็นธงนี้พร้อมตัวเลข ไม่ใช่ต้องไปเปิด /optimize เองถึงจะรู้
            negative_amortization=_neg_amort_flags(base),
        )
    ]

    for extra in extra_payments:
        result = _simulate(debts, monthly_budget + extra, method)
        results.append(
            SensitivityResult(
                extra_payment=extra,
                total_interest=result.total_interest,
                months_to_payoff=result.months_to_payoff,
                interest_saved=round(base.total_interest - result.total_interest, 2),
                negative_amortization=_neg_amort_flags(result),
            )
        )

    return results
