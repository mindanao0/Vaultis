from __future__ import annotations

from datetime import date, datetime
from typing import Any

import numpy as np
import numpy_financial as npf
from sqlalchemy.orm import Session

from ..models import InvestmentGoal
from ..schemas import GoalCreate

EXPECTED_RETURNS: dict[str, float] = {
    "conservative": 0.07,
    "moderate": 0.09,
    "aggressive": 0.12,
}

ALLOCATION_MAP: dict[str, dict[str, float]] = {
    "conservative": {"VOO": 0.30, "SCHD": 0.30, "QQQM": 0.10, "XLV": 0.20, "GLDM": 0.10},
    "moderate":     {"VOO": 0.35, "SCHD": 0.25, "QQQM": 0.20, "XLV": 0.10, "GLDM": 0.10},
    "aggressive":   {"VOO": 0.25, "SCHD": 0.10, "QQQM": 0.45, "XLV": 0.10, "GLDM": 0.10},
}


def calculate_pmt(target: float, current: float, rate: float, months: int) -> float:
    """คืนค่าเงินออมรายเดือนที่ต้องการ (บาท) โดยใช้สูตร PMT"""
    if months <= 0:
        return max(0.0, target - current)
    monthly_rate = rate / 12
    if monthly_rate == 0:
        return max(0.0, (target - current) / months)
    pmt = npf.pmt(monthly_rate, months, -current, target)
    return max(0.0, float(-pmt))


def suggest_allocation(risk_profile: str, required_return: float) -> dict[str, Any]:
    """เลือก ETF allocation จาก watchlist ตาม risk profile"""
    allocation = ALLOCATION_MAP.get(risk_profile, ALLOCATION_MAP["moderate"]).copy()
    expected = EXPECTED_RETURNS.get(risk_profile, 0.09)
    if required_return > expected * 1.2:
        allocation["note"] = (
            f"ผลตอบแทนที่ต้องการ ({required_return*100:.1f}%) "
            f"สูงกว่าค่าเฉลี่ยของโปรไฟล์ {risk_profile} ({expected*100:.0f}%) "
            "พิจารณาปรับโปรไฟล์หรือขยายระยะเวลาเป้าหมาย"
        )
    return allocation


def calculate_probability(
    current: float,
    monthly_contribution: float,
    months: int,
    annual_return: float,
    target: float,
    volatility: float = 0.15,
    n_simulations: int = 1000,
) -> float:
    """Monte Carlo simulation คืนค่าความน่าจะเป็นที่จะถึงเป้าหมาย (0–1)"""
    if months <= 0:
        return 1.0 if current >= target else 0.0

    monthly_return = annual_return / 12
    monthly_vol = volatility / np.sqrt(12)

    rng = np.random.default_rng(42)
    returns = rng.normal(monthly_return, monthly_vol, size=(n_simulations, months))

    portfolio = np.full(n_simulations, float(current))
    for t in range(months):
        portfolio = portfolio * (1.0 + returns[:, t]) + monthly_contribution

    return float(np.mean(portfolio >= target))


def _months_remaining(target_date_str: str) -> int:
    if not target_date_str:
        return 0
    try:
        target = date.fromisoformat(target_date_str[:10])
    except (ValueError, TypeError):
        return 0
    today = date.today()
    delta = (target.year - today.year) * 12 + (target.month - today.month)
    return max(1, delta)


def check_off_track(goal: InvestmentGoal, required_pmt: float) -> tuple[bool, str | None]:
    """คืน (off_track, correction_message) เมื่อเงินออมแผนขาดเกิน 15%"""
    if goal.monthly_contribution_thb >= required_pmt * 0.85:
        return False, None
    shortfall = required_pmt - goal.monthly_contribution_thb
    correction = (
        f"ควรเพิ่มเงินออมรายเดือนอีก {shortfall:,.0f} บาท "
        f"(เป็น {required_pmt:,.0f} บาท/เดือน) "
        f"เพื่อให้ถึงเป้าหมาย '{goal.name}' ตามกำหนด"
    )
    return True, correction


def _build_progress(goal: InvestmentGoal) -> dict[str, Any]:
    months = _months_remaining(goal.target_date)
    expected_return = EXPECTED_RETURNS.get(goal.risk_profile, 0.09)
    monthly_rate = expected_return / 12

    required_pmt = calculate_pmt(
        goal.target_amount_thb, goal.current_amount_thb, expected_return, months
    )

    if monthly_rate > 0:
        growth = (1.0 + monthly_rate) ** months
        projected_value = (
            goal.current_amount_thb * growth
            + goal.monthly_contribution_thb * (growth - 1.0) / monthly_rate
        )
    else:
        projected_value = goal.current_amount_thb + goal.monthly_contribution_thb * months

    probability = calculate_probability(
        current=goal.current_amount_thb,
        monthly_contribution=goal.monthly_contribution_thb,
        months=months,
        annual_return=expected_return,
        target=goal.target_amount_thb,
    )

    off_track, correction = check_off_track(goal, required_pmt)
    allocation = suggest_allocation(goal.risk_profile, expected_return)

    return {
        "goal_id": goal.id,
        "months_remaining": months,
        "required_monthly_pmt": round(required_pmt, 2),
        "projected_value": round(projected_value, 2),
        "probability_of_success": round(probability, 4),
        "on_track": not off_track,
        "course_correction": correction,
        "suggested_allocation": allocation,
    }


# ── CRUD + progress ─────────────────────────────────────────────────────────

def create_goal(db: Session, payload: GoalCreate) -> InvestmentGoal:
    data = payload.model_dump()
    data["target_date"] = data["target_date"].strftime("%Y-%m-%d")
    goal = InvestmentGoal(**data)
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return goal


def list_goals(db: Session) -> list[InvestmentGoal]:
    return db.query(InvestmentGoal).order_by(InvestmentGoal.created_at.desc()).all()


def get_goal(db: Session, goal_id: int) -> InvestmentGoal | None:
    return db.query(InvestmentGoal).filter(InvestmentGoal.id == goal_id).first()


def get_progress(db: Session, goal_id: int) -> dict[str, Any]:
    goal = get_goal(db, goal_id)
    if not goal:
        raise ValueError("ไม่พบเป้าหมายการออม")
    return _build_progress(goal)


def update_progress(db: Session, goal_id: int, actual_contribution: float) -> dict[str, Any]:
    goal = get_goal(db, goal_id)
    if not goal:
        raise ValueError("ไม่พบเป้าหมายการออม")
    goal.current_amount_thb += actual_contribution
    db.commit()
    db.refresh(goal)
    return _build_progress(goal)


def delete_goal(db: Session, goal_id: int) -> bool:
    goal = get_goal(db, goal_id)
    if not goal:
        return False
    db.delete(goal)
    db.commit()
    return True
