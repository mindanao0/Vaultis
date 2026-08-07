"""Cash flow forecasting: projection, anomaly detection, emergency fund alert."""

from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from datetime import date
from typing import Iterable, NamedTuple

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


def _month_key(d: date) -> _MonthKey:
    return f"{d.year:04d}-{d.month:02d}"


def _month_is_complete(key: _MonthKey, today: date) -> bool:
    """เดือนนับว่า "ครบ" ก็ต่อเมื่อวันสุดท้ายของเดือนนั้นผ่านไปแล้ว.

    เดือนที่ยังเดินอยู่มีธุรกรรมแค่บางส่วน (เช่นเงินเดือนออกวันที่ 25 แต่วันนี้วันที่ 7)
    เอาไปหารเป็น "หนึ่งเดือน" เท่ากับเดือนเต็มไม่ได้ — ค่าเฉลี่ยจะต่ำกว่าจริงทันที
    """
    year, month = int(key[:4]), int(key[5:7])
    last_day = monthrange(year, month)[1]
    return date(year, month, last_day) < today


def _split_complete(
    keys: Iterable[_MonthKey], today: date
) -> tuple[list[_MonthKey], list[_MonthKey]]:
    """แยกคีย์เดือนเป็น (เดือนที่จบแล้ว, เดือนที่ยังไม่จบ) เรียงจากเก่าไปใหม่"""
    complete: list[_MonthKey] = []
    partial: list[_MonthKey] = []
    for key in sorted(keys):
        (complete if _month_is_complete(key, today) else partial).append(key)
    return complete, partial


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
    keep_months: set[_MonthKey] | None = None,
) -> dict[str, dict[_MonthKey, float]]:
    """Return {category: {YYYY-MM: total_expense}} for expense transactions only.

    ``keep_months`` จำกัดให้เหลือเฉพาะเดือนที่จบแล้ว เพื่อให้ตัวหารตรงกับ
    ``_usable_months()`` — ไม่งั้น share ของหมวดจะคิดจากคนละชุดเดือนกับ avg_expense
    """
    data: dict[str, dict[_MonthKey, float]] = defaultdict(lambda: defaultdict(float))
    for tx in transactions:
        if tx.type != "expense":
            continue
        key = _month_key(tx.date)
        if keep_months is not None and key not in keep_months:
            continue
        data[tx.category][key] += abs(tx.amount)
    return data


def _usable_months(
    transactions: list[TransactionItem], today: date
) -> tuple[dict[_MonthKey, _MonthlySummary], list[_MonthKey]]:
    """คืน (สรุปเฉพาะเดือนที่จบแล้ว, รายชื่อเดือนที่ตัดออกเพราะยังไม่จบ).

    ไม่มีเดือนที่จบเลย = พยากรณ์ไม่ได้ ต้องบอกตรง ๆ ห้ามคืนตัวเลขที่ปั้นจากเดือนค้าง
    """
    by_month_all = _summarise_by_month(transactions)
    if not by_month_all:
        raise ValueError(
            "ไม่มีธุรกรรมสำหรับคำนวณ — พยากรณ์กระแสเงินสดไม่ได้ "
            "(ไม่มีข้อมูล ≠ รายรับ/รายจ่ายเท่ากับ 0)"
        )
    complete, partial = _split_complete(by_month_all.keys(), today)
    if not complete:
        raise ValueError(
            "ยังไม่มีเดือนที่ข้อมูลครบทั้งเดือน (มีแต่ "
            + ", ".join(partial)
            + ") — พยากรณ์ไม่ได้ ต้องมีอย่างน้อย 1 เดือนที่จบแล้ว"
        )
    return {k: by_month_all[k] for k in complete}, partial


def _scenario_delta(
    scenarios: list[ScenarioAdjustment],
    transactions: list[TransactionItem],
    usable_months: set[_MonthKey],
    avg_expense: float,
) -> float:
    """สัดส่วนที่รายจ่ายเฉลี่ยเปลี่ยนไปจาก scenario ทั้งชุด (−0.25 = ลด 25%).

    หมวดที่ไม่มีในรายจ่าย และหมวดที่ระบุซ้ำ ถูกปฏิเสธด้วย ``ValueError`` —
    ทั้งสองเคยถูกกลืนเงียบจนคำตอบเหมือนไม่ได้ส่ง scenario มาเลย (B1.2 / B1.3)
    """
    seen: dict[str, int] = defaultdict(int)
    for sc in scenarios:
        seen[sc.category] += 1
    dups = sorted(cat for cat, n in seen.items() if n > 1)
    if dups:
        raise ValueError(
            "scenario ระบุหมวดซ้ำ: "
            + ", ".join(dups)
            + " — ระบุได้หมวดละ 1 รายการ (ถ้าต้องการหลายเงื่อนไข ให้รวมเปอร์เซ็นต์เองก่อนส่ง)"
        )

    cat_monthly = _category_monthly(transactions, keep_months=usable_months)
    unknown = [sc.category for sc in scenarios if sc.category not in cat_monthly]
    if unknown:
        raise ValueError(
            "scenario ระบุหมวดที่ไม่มีในรายจ่ายของเดือนที่ใช้คำนวณ: "
            + ", ".join(unknown)
            + " — หมวดที่มีคือ "
            + (", ".join(sorted(cat_monthly)) or "(ไม่มีรายจ่ายเลย)")
        )

    n_months = len(usable_months)
    total_delta = 0.0
    for sc in scenarios:
        cat_avg = sum(cat_monthly[sc.category].values()) / n_months
        # หมวดนี้กินรายจ่ายเฉลี่ยกี่ส่วน — ปรับหมวดหนึ่งจึงขยับรายจ่ายรวมตามสัดส่วน
        share = cat_avg / avg_expense if avg_expense else 0.0
        total_delta += share * (sc.change_percent / 100)
    return total_delta


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def forecast_cashflow(
    transactions: list[TransactionItem],
    months: int,
    current_balance: float,
    scenarios: list[ScenarioAdjustment] | None = None,
    today: date | None = None,
) -> list[ForecastMonth]:
    """Project cash flow month-by-month from historical transaction averages.

    ``today`` มีไว้ให้เทสต์ตรึงวันอ้างอิง — โค้ดจริงปล่อยว่างเพื่อใช้วันนี้
    """
    if today is None:
        today = date.today()

    by_month, _ = _usable_months(transactions, today)
    n_months = len(by_month)
    avg_income = sum(s.income for s in by_month.values()) / n_months
    avg_expense = sum(s.expense for s in by_month.values()) / n_months

    if scenarios:
        total_delta = _scenario_delta(
            scenarios, transactions, set(by_month), avg_expense
        )
        # ขอบล่างเป็นศูนย์: ScenarioAdjustment จำกัด change_percent ที่ -100 อยู่แล้ว
        # ตัวนี้กันเศษทศนิยมไม่ให้หลุดเป็นรายจ่ายติดลบ (= เสกเงินเข้ากระเป๋า)
        adj_expense = max(avg_expense * (1 + total_delta), 0.0)
    else:
        adj_expense = avg_expense

    balance = current_balance
    result = []
    for key in _next_months(months, today):
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


def _anomaly_sort_key(a: CategoryAnomaly) -> tuple[int, float]:
    """หมวดใหม่ขึ้นก่อน (เรียงตามยอด) แล้วจึงหมวดที่เปลี่ยนแปลง (เรียงตามขนาด %)"""
    if a.kind == "new_category":
        return (0, -a.last_month)
    return (1, -abs(a.change_percent or 0.0))


def detect_category_anomalies(
    transactions: list[TransactionItem],
    today: date | None = None,
) -> list[CategoryAnomaly]:
    """Compare the last complete month's per-category spending vs historical average."""
    if today is None:
        today = date.today()

    # เดือนที่ยังไม่จบใช้เป็น "เดือนล่าสุด" ไม่ได้ — มันจะดูเหมือนรายจ่ายลดฮวบทุกหมวด
    complete, _partial = _split_complete(
        {_month_key(tx.date) for tx in transactions}, today
    )
    if len(complete) < 2:
        return []  # need at least 2 complete months to detect anomalies

    usable = set(complete)
    cat_monthly = _category_monthly(transactions, keep_months=usable)
    if not cat_monthly:
        return []

    last_month = complete[-1]
    prior_months = complete[:-1]
    n_prior = len(prior_months)

    # รายจ่ายรวมเฉลี่ยของเดือนก่อน ๆ — ใช้ตัดสินว่าหมวดใหม่ "ใหญ่พอจะเป็นเรื่อง" ไหม
    prior_total_avg = sum(
        sum(m.get(k, 0.0) for k in prior_months) for m in cat_monthly.values()
    ) / n_prior
    new_category_floor = ANOMALY_THRESHOLD * prior_total_avg

    anomalies: list[CategoryAnomaly] = []
    for category, monthly_map in cat_monthly.items():
        last_val = monthly_map.get(last_month, 0.0)
        avg_val = sum(monthly_map.get(m, 0.0) for m in prior_months) / n_prior

        if avg_val == 0:
            # หมวดที่ไม่เคยมีในเดือนก่อน ๆ — เทียบเป็นเปอร์เซ็นต์ไม่ได้เพราะฐานเป็นศูนย์
            # เดิม `continue` ทิ้งทั้งดุ้น ก้อนใหญ่ที่สุดของเดือนจึงหลุดการตรวจ (B1.6)
            if last_val <= 0 or last_val < new_category_floor:
                continue
            anomalies.append(
                CategoryAnomaly(
                    category=category,
                    avg_monthly=0.0,
                    last_month=round(last_val, 2),
                    change_percent=None,
                    kind="new_category",
                )
            )
            continue

        change_pct = round((last_val - avg_val) / avg_val * 100, 1)
        if abs(change_pct) >= ANOMALY_THRESHOLD * 100:
            anomalies.append(
                CategoryAnomaly(
                    category=category,
                    avg_monthly=round(avg_val, 2),
                    last_month=round(last_val, 2),
                    change_percent=change_pct,
                    kind="change",
                )
            )

    anomalies.sort(key=_anomaly_sort_key)
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
    today: date | None = None,
) -> ForecastResponse:
    if today is None:
        today = date.today()

    # โยน ValueError ทันทีถ้าไม่มีเดือนที่ข้อมูลครบ — ผู้เรียกต้องเห็น ไม่ใช่ได้ตัวเลขปลอม
    by_month, excluded = _usable_months(transactions, today)

    forecast = forecast_cashflow(transactions, months, current_balance, scenarios, today)
    anomalies = detect_category_anomalies(transactions, today)

    if emergency_threshold is None:
        # Default: 3 months of average expenses (คิดจากเดือนที่จบแล้วเท่านั้น)
        avg_exp = sum(s.expense for s in by_month.values()) / len(by_month)
        emergency_threshold = avg_exp * EMERGENCY_MONTHS

    alerted, message = check_emergency_fund_alert(forecast, emergency_threshold)

    return ForecastResponse(
        current_balance=current_balance,
        months=months,
        forecast=forecast,
        anomalies=anomalies,
        emergency_alert=alerted,
        emergency_message=message,
        months_used=len(by_month),
        excluded_partial_months=excluded,
    )
