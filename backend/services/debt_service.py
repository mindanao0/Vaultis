"""Debt payoff optimizer: Avalanche and Snowball methods."""

from __future__ import annotations

from ..models.debt_models import (
    Debt,
    DebtComparison,
    DebtResult,
    DebtSchedule,
    PaymentEntry,
    SensitivityResult,
)

_MAX_MONTHS = 600  # 50-year safety cap


def _simulate(debts_input: list[Debt], monthly_budget: float, method: str) -> DebtResult:
    n = len(debts_input)
    balances = [d.balance for d in debts_input]
    monthly_rates = [d.interest_rate / 100 / 12 for d in debts_input]
    schedules: list[list[PaymentEntry]] = [[] for _ in range(n)]
    total_interest = 0.0
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
            interest_paid = min(interest, payment)
            principal_paid = payment - interest_paid
            total_interest += interest_paid
            balances[i] = new_balance
            schedules[i].append(
                PaymentEntry(
                    month=month,
                    payment=round(payment, 2),
                    principal=round(principal_paid, 2),
                    interest=round(interest_paid, 2),
                    remaining_balance=round(new_balance, 2),
                )
            )

    debt_schedules = [
        DebtSchedule(
            name=debts_input[i].name,
            payments=schedules[i],
            total_interest=round(sum(e.interest for e in schedules[i]), 2),
            months_to_payoff=len(schedules[i]),
        )
        for i in range(n)
    ]

    return DebtResult(
        method=method,  # type: ignore[arg-type]
        monthly_budget=monthly_budget,
        total_interest=round(total_interest, 2),
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
            )
        )

    return results
