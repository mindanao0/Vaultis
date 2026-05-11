"""Cash flow forecasting: projection, anomaly detection, emergency fund alert."""

from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime
from typing import NamedTuple

from ..models.cashflow_models import (
    CategoryAnomaly,
    ForecastMonth,
    ForecastResponse,
    ScenarioAdjustment,
    TransactionItem,
)

# Month-level key: YYYY-MM
_MonthKey = str

ANOMALY_THRESHOLD = 0.15   # flag categories that changed ≥15% vs average
EMERGENCY_MONTHS = 3       # default emergency fund covers 3 months of expenses


class _MonthlySummary(NamedTuple):
    income: float
    expense: float  # stored as positive number


def _month_key(date_str: str) -> _MonthKey:
    return date_str[:7]  # "YYYY-MM-DD" → "YYYY-MM"


def _next_months(n: int, from_date: date | None = None) -> list[_MonthKey]:
    """Return n consecutive YYYY-MM strings starting from the month after from_date."""
    if from_date is None:
        from_date = date.today()
    year, month = from_date.year, from_date.month
    result = []
    for _ in range(n):
        month += 1
        if month > 12:
            month = 1
            year += 1
        result.append(f"{year:04d}-{month:02d}")
    return result


def _summarise_by_month(
    transactions: list[TransactionItem],
) -> dict[_MonthKey, _MonthlySummary]:
    """Aggregate income and expense totals per calendar month."""
    buckets: dict[_MonthKey, dict] = defaultdict(lambda: {"income": 0.0, "expense": 0.0})
    for tx in transactions:
        key = _month_key(tx.date)
        if tx.type == "income":
            buckets[key]["income"] += abs(tx.amount)
        else:
            buckets[key]["expense"] += abs(tx.amount)
    return {k: _MonthlySummary(v["income"], v["expense"]) for k, v in buckets.items()}


def _category_monthly(
    transactions: list[TransactionItem],
) -> dict[str, dict[_MonthKey, float]]:
    """Return {category: {YYYY-MM: total_expense}} for expense transactions only."""
    data: dict[str, dict[_MonthKey, float]] = defaultdict(lambda: defaultdict(float))
    for tx in transactions:
        if tx.type == "expense":
            data[tx.category][_month_key(tx.date)] += abs(tx.amount)
    return data


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def forecast_cashflow(
    transactions: list[TransactionItem],
    months: int,
    current_balance: float,
    scenarios: list[ScenarioAdjustment] | None = None,
) -> list[ForecastMonth]:
    """Project cash flow month-by-month from historical transaction averages."""
    by_month = _summarise_by_month(transactions)
    if not by_month:
        # No data — flat zero projection
        result = []
        balance = current_balance
        for key in _next_months(months):
            result.append(
                ForecastMonth(
                    month=key,
                    projected_income=0.0,
                    projected_expense=0.0,
                    net_cashflow=0.0,
                    ending_balance=round(balance, 2),
                )
            )
        return result

    n_months = len(by_month)
    avg_income = sum(s.income for s in by_month.values()) / n_months
    avg_expense = sum(s.expense for s in by_month.values()) / n_months

    # Build scenario adjustment map: category → multiplier
    category_multiplier: dict[str, float] = {}
    if scenarios:
        cat_monthly = _category_monthly(transactions)
        total_exp_per_month = {
            m: sum(cat_monthly[c].get(m, 0.0) for c in cat_monthly)
            for m in by_month
        }
        for sc in scenarios:
            cat = sc.category
            if cat not in cat_monthly:
                continue
            cat_avg = sum(cat_monthly[cat].values()) / n_months
            # How much of avg_expense is this category?
            share = cat_avg / avg_expense if avg_expense else 0.0
            # Applying change_percent to this category reduces avg_expense proportionally
            delta = share * (sc.change_percent / 100)
            category_multiplier[cat] = delta

        # Net change to avg_expense from all scenario adjustments
        total_delta = sum(category_multiplier.values())
        adj_expense = avg_expense * (1 + total_delta)
    else:
        adj_expense = avg_expense

    balance = current_balance
    result = []
    for key in _next_months(months):
        net = avg_income - adj_expense
        balance += net
        result.append(
            ForecastMonth(
                month=key,
                projected_income=round(avg_income, 2),
                projected_expense=round(adj_expense, 2),
                net_cashflow=round(net, 2),
                ending_balance=round(balance, 2),
            )
        )
    return result


def detect_category_anomalies(
    transactions: list[TransactionItem],
) -> list[CategoryAnomaly]:
    """Compare last month's per-category spending vs historical average."""
    cat_monthly = _category_monthly(transactions)
    if not cat_monthly:
        return []

    all_months = sorted({_month_key(tx.date) for tx in transactions})
    if len(all_months) < 2:
        return []  # need at least 2 months to detect anomalies

    last_month = all_months[-1]
    prior_months = all_months[:-1]
    n_prior = len(prior_months)

    anomalies: list[CategoryAnomaly] = []
    for category, monthly_map in cat_monthly.items():
        last_val = monthly_map.get(last_month, 0.0)
        avg_val = sum(monthly_map.get(m, 0.0) for m in prior_months) / n_prior
        if avg_val == 0:
            continue
        change_pct = round((last_val - avg_val) / avg_val * 100, 1)
        if abs(change_pct) >= ANOMALY_THRESHOLD * 100:
            anomalies.append(
                CategoryAnomaly(
                    category=category,
                    avg_monthly=round(avg_val, 2),
                    last_month=round(last_val, 2),
                    change_percent=change_pct,
                )
            )

    anomalies.sort(key=lambda a: abs(a.change_percent), reverse=True)
    return anomalies


def check_emergency_fund_alert(
    forecast: list[ForecastMonth],
    emergency_threshold: float,
) -> tuple[bool, str]:
    """Alert if any projected ending balance drops below the emergency threshold."""
    if not forecast:
        return False, ""

    for fm in forecast:
        if fm.ending_balance < emergency_threshold:
            return (
                True,
                (
                    f"⚠️ ยอดเงินคาดการณ์เดือน {fm.month} "
                    f"({fm.ending_balance:,.0f} บาท) "
                    f"ต่ำกว่า emergency fund {emergency_threshold:,.0f} บาท"
                ),
            )
    return False, ""


def build_forecast_response(
    transactions: list[TransactionItem],
    months: int,
    current_balance: float,
    emergency_threshold: float | None = None,
    scenarios: list[ScenarioAdjustment] | None = None,
) -> ForecastResponse:
    forecast = forecast_cashflow(transactions, months, current_balance, scenarios)
    anomalies = detect_category_anomalies(transactions)

    if emergency_threshold is None:
        # Default: 3 months of average expenses
        by_month = _summarise_by_month(transactions)
        avg_exp = (
            sum(s.expense for s in by_month.values()) / len(by_month)
            if by_month else 0.0
        )
        emergency_threshold = avg_exp * EMERGENCY_MONTHS

    alerted, message = check_emergency_fund_alert(forecast, emergency_threshold)

    return ForecastResponse(
        current_balance=current_balance,
        months=months,
        forecast=forecast,
        anomalies=anomalies,
        emergency_alert=alerted,
        emergency_message=message,
    )
