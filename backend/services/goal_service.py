from __future__ import annotations

import time
from datetime import date, datetime
from typing import Any

import numpy as np
import numpy_financial as npf
from sqlalchemy.orm import Session

from ..models import InvestmentGoal
from ..schemas import GoalCreate

from analysis.risk import portfolio_mu_sigma
from portfolio.targets import RISK_PROFILES
from utils.cache import is_cacheable

EXPECTED_RETURNS: dict[str, float] = {
    "conservative": 0.07,
    "moderate": 0.09,
    "aggressive": 0.12,
}
DEFAULT_VOLATILITY = 0.15

# สามสถานะที่ต้องแยกจากกันทุกชั้น (กฎเดียวกับ ``fetch_*_status`` ของ news):
# "ดึงไม่สำเร็จ" ≠ "ไม่มีข้อมูล" — ยุบรวมกันเมื่อไหร่ ผู้ใช้ก็ไปแก้ผิดที่เมื่อนั้น
ASSUMPTIONS_OK = "ok"        # มี μ/σ จากพอร์ตจริง
ASSUMPTIONS_EMPTY = "empty"  # ยังไม่มีพอร์ต/ยังไม่มีกองที่ราคาพร้อม — ไม่ใช่ความล้มเหลว
ASSUMPTIONS_ERROR = "error"  # ดึงราคา/อัตราแลกเปลี่ยนไม่สำเร็จ หรือคำนวณ μ/σ ไม่ได้

_real_assumptions_cache: tuple[float, dict[str, Any]] | None = None
_REAL_ASSUMPTIONS_TTL_SEC = 600.0

_REAL_SOURCE = "พอร์ตจริงจาก ledger (ย้อนหลัง 10 ปี)"


def _assumptions(
    status: str,
    *,
    source: str,
    mu: float | None = None,
    sigma: float | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """รูปคืนค่ามาตรฐานของสมมติฐานพอร์ตจริง.

    ``data_ok`` คือธงเดียวกับที่ทั้งระบบใช้ — ``utils.cache.is_cacheable`` อ่านมันเพื่อ
    ปฏิเสธการแคช ผลจึงเป็น "ไม่แคชความล้มเหลว" โดยไม่ต้องนิยามกติกาซ้ำที่นี่
    """
    return {
        "status": status,
        "mu": mu,
        "sigma": sigma,
        "source": source,
        "error": error,
        "data_ok": status == ASSUMPTIONS_OK,
    }


def _compute_real_portfolio_assumptions() -> dict[str, Any]:
    """คำนวณ μ/σ ของพอร์ตจริง — จับเฉพาะความล้มเหลวของ "ข้อมูล" เท่านั้น.

    บั๊กจริงในโค้ด (TypeError/KeyError/...) ต้องดังต่อ ห้ามให้กลายเป็น "ยังไม่มีพอร์ต"
    """
    from data.fetcher import PriceDataUnavailableError, fetch_adjusted_close_data
    from portfolio.tracker import get_portfolio_summary
    from utils.fx import FxRateUnavailable

    try:
        holdings = get_portfolio_summary()
    except (PriceDataUnavailableError, FxRateUnavailable) as exc:
        return _assumptions(
            ASSUMPTIONS_ERROR,
            source="อ่านพอร์ตจริงไม่สำเร็จ",
            error=f"อ่านพอร์ตจาก ledger ไม่สำเร็จ: {exc}",
        )

    priced = holdings[holdings["Price OK"]] if not holdings.empty else holdings
    # เดิมเขียน ``float(x or 0) > 0`` — ``or`` ดัก NaN ไม่ได้ (NaN เป็น truthy) ใช้ isfinite ตรง ๆ
    weights = {
        str(row["Ticker"]): float(row["Current Value (THB)"])
        for _, row in priced.iterrows()
        if np.isfinite(float(row["Current Value (THB)"]))
        and float(row["Current Value (THB)"]) > 0
    }
    if not weights:
        return _assumptions(
            ASSUMPTIONS_EMPTY,
            source="ยังไม่มีพอร์ตจริงใน ledger (ยังไม่มีกองที่ราคาพร้อม)",
        )

    try:
        prices = fetch_adjusted_close_data(list(weights), years=10)
    except PriceDataUnavailableError as exc:
        return _assumptions(
            ASSUMPTIONS_ERROR,
            source="ดึงราคาย้อนหลังของพอร์ตจริงไม่สำเร็จ",
            error=f"ดึงราคาย้อนหลังไม่สำเร็จ: {exc}",
        )

    try:
        mu, sigma = portfolio_mu_sigma(prices, weights)
    except ValueError as exc:  # ข้อมูลสั้น/ไม่ตรงกอง — เป็นปัญหาข้อมูล ไม่ใช่บั๊ก
        return _assumptions(
            ASSUMPTIONS_ERROR,
            source="คำนวณ μ/σ ของพอร์ตจริงไม่ได้",
            error=f"คำนวณ μ/σ จากราคาที่ได้ไม่สำเร็จ: {exc}",
        )

    return _assumptions(ASSUMPTIONS_OK, source=_REAL_SOURCE, mu=mu, sigma=sigma)


def real_portfolio_assumptions_with_status() -> dict[str, Any]:
    """μ/σ ต่อปีจากพอร์ตจริง พร้อม **สาเหตุ** เมื่อใช้ไม่ได้.

    Roadmap ข้อ 15: Monte Carlo ของเป้าหมายต้องผูกพอร์ตจริง ไม่ใช่ค่าคงที่ต่อโปรไฟล์
    คืน dict เสมอ: ``status`` ∈ {``ok``, ``empty``, ``error``} + ``source``/``error``
    ผู้เรียกที่ fallback ไป preset **ต้องบอกผู้ใช้ว่ากำลังใช้สมมติฐานสำเร็จรูป**

    แคช 10 นาที **เฉพาะผลสำเร็จ** (หน้า goals เรียกซ้ำต่อหลายเป้าหมาย) — ความล้มเหลว
    และ "ยังไม่มีพอร์ต" ต้องลองใหม่ทุกครั้ง ตามกฎเดียวกับ ``utils/cache.py`` (C1)
    """
    global _real_assumptions_cache
    now = time.monotonic()
    if _real_assumptions_cache is not None and now - _real_assumptions_cache[0] < _REAL_ASSUMPTIONS_TTL_SEC:
        return dict(_real_assumptions_cache[1])

    result = _compute_real_portfolio_assumptions()
    _real_assumptions_cache = (now, result) if is_cacheable(result) else None
    return dict(result)


def real_portfolio_assumptions() -> dict[str, Any] | None:
    """รูปย่อของ :func:`real_portfolio_assumptions_with_status` — ``None`` เมื่อใช้ไม่ได้.

    ผู้เรียกที่ต้องแยก "ยังไม่มีพอร์ต" ออกจาก "ดึงราคาไม่สำเร็จ" ต้องใช้ตัว ``_with_status``
    (คู่เดียวกับ ``get_news`` / ``get_news_with_status``)
    """
    status = real_portfolio_assumptions_with_status()
    if status["status"] != ASSUMPTIONS_OK:
        return None
    return {"mu": status["mu"], "sigma": status["sigma"], "source": status["source"]}

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

    # ผูกสมมติฐานกับพอร์ตจริงก่อน (Roadmap ข้อ 15) — ใช้ไม่ได้ค่อยตกไป preset
    # แต่ต้องบอกผู้ใช้ให้ตรงว่า "ยังไม่มีพอร์ต" หรือ "ดึงข้อมูลไม่สำเร็จ" (คนละเรื่องกัน)
    assumptions = real_portfolio_assumptions_with_status()
    assumptions_status = str(assumptions["status"])
    assumptions_error = assumptions["error"]
    if assumptions_status == ASSUMPTIONS_OK:
        expected_return = float(assumptions["mu"])
        volatility = float(assumptions["sigma"])
        assumptions_source = str(assumptions["source"])
    else:
        expected_return = EXPECTED_RETURNS.get(goal.risk_profile, 0.09)
        volatility = DEFAULT_VOLATILITY
        if assumptions_status == ASSUMPTIONS_ERROR:
            assumptions_source = (
                f"preset โปรไฟล์ {goal.risk_profile} — สมมติฐานสำเร็จรูป ไม่ใช่พอร์ตจริงของคุณ "
                f"({assumptions_error})"
            )
        else:
            assumptions_source = (
                f"preset โปรไฟล์ {goal.risk_profile} ({assumptions['source']})"
            )
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
        volatility=volatility,
    )

    off_track, correction = check_off_track(goal, required_pmt)

    # ผลตอบแทนที่ "ต้องได้จริง" จากเงินออมที่ผู้ใช้ตั้งไว้ (ไม่ใช่ค่าคาดหวังของโปรไฟล์)
    # — เดิมส่ง expected_return เข้าไปเทียบกับตัวมันเอง คำเตือนจึงไม่มีวันทำงาน (M9)
    needed = required_annual_return(
        goal.target_amount_thb, goal.current_amount_thb, goal.monthly_contribution_thb, months
    )
    allocation = suggest_allocation(goal.risk_profile, needed if needed is not None else expected_return)

    assumptions_note = (
        f"ประมาณการใช้ผลตอบแทน {expected_return*100:.1f}% ต่อปี "
        f"และความผันผวน {volatility*100:.1f}% (ที่มา: {assumptions_source}) — "
        "อิงสถิติอดีต เป็นสมมติฐาน ไม่ใช่การรับประกัน"
    )
    if assumptions_status == ASSUMPTIONS_ERROR:
        # ดึงข้อมูลพอร์ตจริงไม่สำเร็จ ≠ ยังไม่มีพอร์ต — ตัวเลขยังตอบได้ แต่ห้ามให้ผู้ใช้
        # เข้าใจว่านี่คือพอร์ตของเขา (คำเตือนต้องมาก่อน ไม่ใช่ซ่อนท้ายประโยค)
        assumptions_note = (
            "ดึงข้อมูลพอร์ตจริงไม่สำเร็จ — ตัวเลขด้านล่างคิดจากสมมติฐานสำเร็จรูป (preset) "
            f"ไม่ใช่พอร์ตจริงของคุณ ({assumptions_error}) · " + assumptions_note
        )

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
        "assumptions_source": assumptions_source,
        # ok = พอร์ตจริง · empty = ยังไม่มีพอร์ต · error = ดึงข้อมูลไม่สำเร็จ (ใช้ preset แทน)
        "assumptions_status": assumptions_status,
        "assumptions_error": assumptions_error,
        "assumptions_note": assumptions_note,
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
