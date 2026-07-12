from __future__ import annotations

from datetime import date, datetime
from typing import Any

import numpy as np
import numpy_financial as npf
from sqlalchemy.orm import Session

from ..models import InvestmentGoal
from ..schemas import GoalCreate

from portfolio.targets import RISK_PROFILES

EXPECTED_RETURNS: dict[str, float] = {
    "conservative": 0.07,
    "moderate": 0.09,
    "aggressive": 0.12,
}

# ใช้ชุดเดียวกับ DCA/rebalance (portfolio/targets.py)
ALLOCATION_MAP = RISK_PROFILES


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
    """เลือก ETF allocation ตาม risk profile.

    AUDIT.md M9: เดิมยัด key ``note`` (string) ปนใน dict น้ำหนัก (ตัวเลข) —
    ผู้บริโภคที่วนหาน้ำหนักจะพัง — ตอนนี้แยก ``weights`` กับ ``warning`` ออกจากกัน
    """
    weights = ALLOCATION_MAP.get(risk_profile, ALLOCATION_MAP["moderate"]).copy()
    expected = EXPECTED_RETURNS.get(risk_profile, 0.09)
    warning: str | None = None
    if required_return > expected * 1.2:
        warning = (
            f"ผลตอบแทนที่ต้องการ ({required_return*100:.1f}% ต่อปี) "
            f"สูงกว่าค่าคาดหวังของโปรไฟล์ {risk_profile} ({expected*100:.0f}%) อย่างมีนัยสำคัญ "
            "พิจารณาเพิ่มเงินออม ขยายระยะเวลา หรือลดเป้าหมายลง"
        )
    return {
        "weights": weights,
        "expected_return_pct": round(expected * 100, 1),
        "warning": warning,
    }


def required_annual_return(target: float, current: float, monthly: float, months: int) -> float | None:
    """หาผลตอบแทนต่อปีที่ต้องได้ เพื่อให้เงินออมปัจจุบันถึงเป้าหมายในเวลาที่เหลือ.

    ใช้ ``numpy_financial.rate`` แก้สมการ FV; คืน None ถ้าหาคำตอบไม่ได้
    (เดิมไม่มีฟังก์ชันนี้ ทำให้คำเตือน "ผลตอบแทนที่ต้องการสูงเกินไป" ไม่มีวันทำงาน — M9)
    """
    if months <= 0 or (monthly <= 0 and current <= 0):
        return None
    try:
        monthly_rate = float(npf.rate(months, -monthly, -current, target))
    except Exception:
        return None
    if monthly_rate != monthly_rate or monthly_rate <= -1:  # NaN / ไม่มีคำตอบ
        return None
    return (1.0 + monthly_rate) ** 12 - 1.0


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

    # ผลตอบแทนที่ "ต้องได้จริง" จากเงินออมที่ผู้ใช้ตั้งไว้ (ไม่ใช่ค่าคาดหวังของโปรไฟล์)
    # — เดิมส่ง expected_return เข้าไปเทียบกับตัวมันเอง คำเตือนจึงไม่มีวันทำงาน (M9)
    needed = required_annual_return(
        goal.target_amount_thb, goal.current_amount_thb, goal.monthly_contribution_thb, months
    )
    allocation = suggest_allocation(goal.risk_profile, needed if needed is not None else expected_return)

    return {
        "goal_id": goal.id,
        "months_remaining": months,
        "required_monthly_pmt": round(required_pmt, 2),
        "required_annual_return_pct": round(needed * 100, 2) if needed is not None else None,
        "assumed_annual_return_pct": round(expected_return * 100, 1),
        "projected_value": round(projected_value, 2),
        "probability_of_success": round(probability, 4),
        "on_track": not off_track,
        "course_correction": correction,
        "suggested_allocation": allocation,
        "assumptions_note": (
            f"ประมาณการใช้ผลตอบแทน {expected_return*100:.0f}% ต่อปี และความผันผวน 15% "
            "ซึ่งเป็นสมมติฐาน ไม่ใช่การรับประกัน"
        ),
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
