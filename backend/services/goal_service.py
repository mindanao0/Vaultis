from __future__ import annotations

import time
from datetime import date, datetime
from typing import Any

import numpy as np
import numpy_financial as npf
from sqlalchemy.orm import Session

from ..models import InvestmentGoal
from ..schemas import GoalCreate

from analysis.risk import portfolio_return_stats
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

# จำนวนปีที่ "ขอ" จาก fetcher — ไม่ใช่จำนวนปีที่ได้ใช้จริง (ดู ``_real_source_label``)
_HISTORY_YEARS_REQUESTED = 10


def _real_source_label(window: dict[str, Any]) -> str:
    """ป้ายที่บอก **ช่วงข้อมูลที่ใช้จริง** ไม่ใช่ช่วงที่ขอมา.

    เดิมป้ายเป็นค่าคงที่ ``"พอร์ตจริงจาก ledger (ย้อนหลัง 10 ปี)"`` ทั้งที่ ``dropna``
    ตัดอนุกรมเหลือ "ประวัติร่วม" ที่สั้นที่สุดของพอร์ต (QQQM เพิ่งลิสต์ปี 2020 ⇒ ใช้จริง
    ราว 5.4–5.8 ปี และหน้าต่างที่เหลือไม่มีวิกฤตใหญ่สักรอบ) — ตัวเลขที่ติดป้ายว่าเป็น
    อย่างอื่นคือการกุข้อมูลชนิดเดียวกับ "ดึงไม่สำเร็จ ≠ ไม่มีข้อมูล"
    (AUDIT_ROUND2_2026-08-07 · FIX_PLAN เฟส 4①)
    """
    return (
        f"พอร์ตจริงจาก ledger — ข้อมูลที่ใช้จริง {window['start']} ถึง {window['end']} "
        f"({window['days']:,} วันทำการ ≈ {window['years']:.1f} ปี "
        f"จาก {window['days_available']:,} วันที่ดึงมาได้ในคำขอ {_HISTORY_YEARS_REQUESTED} ปี)"
    )


def _assumptions(
    status: str,
    *,
    source: str,
    mu_geometric: float | None = None,
    mu_arithmetic: float | None = None,
    sigma: float | None = None,
    window: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """รูปคืนค่ามาตรฐานของสมมติฐานพอร์ตจริง.

    ``data_ok`` คือธงเดียวกับที่ทั้งระบบใช้ — ``utils.cache.is_cacheable`` อ่านมันเพื่อ
    ปฏิเสธการแคช ผลจึงเป็น "ไม่แคชความล้มเหลว" โดยไม่ต้องนิยามกติกาซ้ำที่นี่

    อัตราผลตอบแทนมีสองตัวและ**ห้ามสลับกัน** (AUDIT_ROUND2_2026-08-07 · FIX_PLAN เฟส 4①):
    ``mu_geometric`` = อัตราทบต้น (CAGR) ใช้กับสูตรที่ทบต้นเอง (PMT / มูลค่าคาดการณ์) ·
    ``mu_arithmetic`` = ค่าเฉลี่ยเลขคณิต ใช้เป็น drift ต่องวดของ Monte Carlo เท่านั้น
    ``mu`` เป็นชื่อพ้องของ ``mu_geometric`` (ค่าเดียวกัน) เก็บไว้เพื่อความเข้ากันได้ย้อนหลัง
    """
    return {
        "status": status,
        "mu": mu_geometric,
        "mu_geometric": mu_geometric,
        "mu_arithmetic": mu_arithmetic,
        "sigma": sigma,
        "window": window,
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
        prices = fetch_adjusted_close_data(list(weights), years=_HISTORY_YEARS_REQUESTED)
    except PriceDataUnavailableError as exc:
        return _assumptions(
            ASSUMPTIONS_ERROR,
            source="ดึงราคาย้อนหลังของพอร์ตจริงไม่สำเร็จ",
            error=f"ดึงราคาย้อนหลังไม่สำเร็จ: {exc}",
        )

    try:
        stats = portfolio_return_stats(prices, weights)
    except ValueError as exc:  # ข้อมูลสั้น/ไม่ตรงกอง — เป็นปัญหาข้อมูล ไม่ใช่บั๊ก
        return _assumptions(
            ASSUMPTIONS_ERROR,
            source="คำนวณ μ/σ ของพอร์ตจริงไม่ได้",
            error=f"คำนวณ μ/σ จากราคาที่ได้ไม่สำเร็จ: {exc}",
        )

    window = {
        "start": stats["window_start"],
        "end": stats["window_end"],
        "days": int(stats["window_days"]),
        "days_available": int(stats["window_days_available"]),
        "years": float(stats["window_years"]),
        "tickers": list(stats["tickers"]),
    }
    return _assumptions(
        ASSUMPTIONS_OK,
        source=_real_source_label(window),
        mu_geometric=float(stats["mu_geometric"]),
        mu_arithmetic=float(stats["mu_arithmetic"]),
        sigma=float(stats["sigma"]),
        window=window,
    )


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
    return {
        "mu": status["mu"],  # = mu_geometric (อัตราทบต้น) — ผู้เรียกเดิมทบต้นได้ปลอดภัย
        "mu_geometric": status["mu_geometric"],
        "mu_arithmetic": status["mu_arithmetic"],
        "sigma": status["sigma"],
        "source": status["source"],
        "window": status["window"],
    }

# ใช้ชุดเดียวกับ DCA/rebalance (portfolio/targets.py)
ALLOCATION_MAP = RISK_PROFILES


def monthly_compound_rate(annual_rate: float) -> float:
    """แปลงอัตรา**ทบต้น**ต่อปี (CAGR) เป็นอัตราทบต้นต่อเดือน: ``(1+r)^(1/12) − 1``.

    **ห้ามใช้ ``rate / 12``** ซึ่งเป็นอัตรา *นาม* (nominal): ทบ 12 งวดแล้วได้
    ``(1+r/12)^12 − 1`` ซึ่ง **สูงกว่า** ``r`` ที่รับมา (9.00% ⇒ ทบจริง 9.38%)
    ผลคือสูตรที่ทบต้นเองจะโตเร็วเกินจริง แล้วบอกผู้ใช้ให้ออมเงิน**น้อยกว่าที่ต้องออมจริง**
    — ทิศทางเดียวกับบั๊ก σ²/2 ที่ FIX_PLAN เฟส 4① ตั้งใจปิด จึงต้องปิดคู่กัน
    (AUDIT_ROUND2_2026-08-07)

    ตัวเลขที่โชว์ผู้ใช้ (``assumed_annual_return_pct``) กับตัวเลขที่ใช้คำนวณจริงต้องเป็น
    ตัวเดียวกัน และต้องเทียบกับ ``required_annual_return()`` ได้ตรงหน่วย — ฟังก์ชันนั้น
    แปลงกลับด้วย ``(1+m)^12 − 1`` (effective) อยู่แล้ว
    """
    if annual_rate <= -1.0:
        # ขาดทุนเกิน 100% ต่อปี = ทบต้นไม่ได้ ห้ามคืน NaN ให้ไหลต่อไปเป็นตัวเลขบนจอ
        raise ValueError("อัตราผลตอบแทนต่อปี ≤ −100% — แปลงเป็นอัตราทบต้นรายเดือนไม่ได้")
    return (1.0 + annual_rate) ** (1.0 / 12.0) - 1.0


def calculate_pmt(target: float, current: float, rate: float, months: int) -> float:
    """คืนค่าเงินออมรายเดือนที่ต้องการ (บาท) โดยใช้สูตร PMT.

    ``rate`` ต้องเป็นอัตรา**ทบต้น**ต่อปี (CAGR) เพราะสูตรนี้ทบต้นเอง —
    ป้อนค่าเฉลี่ยเลขคณิตเข้ามาเมื่อไหร่ เงินออมที่ตอบจะ**ต่ำกว่าที่ต้องออมจริง**
    (AUDIT_ROUND2_2026-08-07 · FIX_PLAN เฟส 4①) และด้วยเหตุผลเดียวกัน การแปลงเป็น
    รายเดือนต้องผ่าน :func:`monthly_compound_rate` ไม่ใช่ ``rate / 12``
    """
    if months <= 0:
        return max(0.0, target - current)
    monthly_rate = monthly_compound_rate(rate)
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
    """Monte Carlo simulation คืนค่าความน่าจะเป็นที่จะถึงเป้าหมาย (0–1).

    ``annual_return`` ที่นี่คือ **drift แบบเลขคณิต** ไม่ใช่อัตราทบต้น: มันถูกใช้เป็น
    ค่าเฉลี่ยของผลตอบแทนรายเดือนที่สุ่มจาก normal แล้วคูณทบกันในลูป ตัวจำลองจึงหัก
    ส่วนต่าง σ²/2 ออกให้เองอยู่แล้ว — ป้อน CAGR เข้ามาตรงนี้จะเป็นการหักซ้ำสองรอบ
    (คู่ตรงข้ามของบั๊ก PMT ใน AUDIT_ROUND2_2026-08-07 · FIX_PLAN เฟส 4① — สองสูตรนี้
    ต้องการอัตราคนละตัว ห้ามส่งตัวเดียวกันเข้าทั้งคู่เพราะ "ก็ μ เหมือนกัน")
    """
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


def _rate_from(assumptions: dict[str, Any], key: str) -> float:
    """อ่านอัตราผลตอบแทนตัวที่ระบุ โดยถอยไปที่ ``mu`` เมื่อผู้เรียกเก่ายังไม่มีคีย์ใหม่.

    เขียนเป็น ``assumptions.get(key) or assumptions["mu"]`` ไม่ได้ — ``or`` ตัดสินด้วย
    ความ falsy ⇒ อัตรา 0.0% (พอร์ตที่ผลตอบแทนย้อนหลังเสมอตัวพอดี) จะถูกอ่านว่า "ไม่มีค่า"
    แล้วเงียบ ๆ กลายเป็นอัตราอีกตัวหนึ่ง ซึ่งคือการกุตัวเลขแบบเดียวกับที่ ``_compute_
    real_portfolio_assumptions`` เลิกใช้ ``float(x or 0)`` ไปแล้ว — เช็ก ``None`` ตรง ๆ
    """
    value = assumptions.get(key)
    if value is None:
        value = assumptions["mu"]
    return float(value)


def _build_progress(goal: InvestmentGoal) -> dict[str, Any]:
    months = _months_remaining(goal.target_date)

    # ผูกสมมติฐานกับพอร์ตจริงก่อน (Roadmap ข้อ 15) — ใช้ไม่ได้ค่อยตกไป preset
    # แต่ต้องบอกผู้ใช้ให้ตรงว่า "ยังไม่มีพอร์ต" หรือ "ดึงข้อมูลไม่สำเร็จ" (คนละเรื่องกัน)
    assumptions = real_portfolio_assumptions_with_status()
    assumptions_status = str(assumptions["status"])
    assumptions_error = assumptions["error"]
    assumptions_window: dict[str, Any] | None = None
    if assumptions_status == ASSUMPTIONS_OK:
        # สองอัตรานี้คนละตัวกัน — ``compound_return`` ไว้ทบต้น (PMT/มูลค่าคาดการณ์)
        # ส่วน ``drift_return`` ไว้เป็นค่าเฉลี่ยต่องวดของ Monte Carlo เท่านั้น
        # (``mu`` = ``mu_geometric`` จึงเป็น default ที่ปลอดภัยของทั้งคู่หากผู้เรียกเก่า
        #  ส่ง dict ที่ยังไม่มีคีย์ใหม่เข้ามา)
        compound_return = _rate_from(assumptions, "mu_geometric")
        drift_return = _rate_from(assumptions, "mu_arithmetic")
        volatility = float(assumptions["sigma"])
        assumptions_source = str(assumptions["source"])
        assumptions_window = assumptions.get("window")
    else:
        # preset เป็นสมมติฐานตัวเดียวที่ตั้งไว้เอง ไม่ได้วัดจากอดีต จึงไม่มีคู่เลขคณิต/เรขาคณิต
        # ให้แยก — ใช้ค่าเดียวกันทั้งสองทาง (ผลคือ MC ออกมาระมัดระวังกว่าเล็กน้อย)
        compound_return = EXPECTED_RETURNS.get(goal.risk_profile, 0.09)
        drift_return = compound_return
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
    # ↓ ทุกสูตรใต้บรรทัดนี้ทบต้นเอง จึงต้องกิน ``compound_return`` (CAGR) เท่านั้น
    #   มีข้อยกเว้นเดียวคือ Monte Carlo ที่รับ ``drift_return`` (ดูคอมเมนต์ตรงนั้น)
    #   และการแปลงเป็นรายเดือนต้องเป็นอัตราทบต้น ไม่ใช่ ``/ 12`` (ดู monthly_compound_rate)
    monthly_rate = monthly_compound_rate(compound_return)

    required_pmt = calculate_pmt(
        goal.target_amount_thb, goal.current_amount_thb, compound_return, months
    )

    if monthly_rate > 0:
        growth = (1.0 + monthly_rate) ** months
        projected_value = (
            goal.current_amount_thb * growth
            + goal.monthly_contribution_thb * (growth - 1.0) / monthly_rate
        )
    else:
        projected_value = goal.current_amount_thb + goal.monthly_contribution_thb * months

    # ที่เดียวในฟังก์ชันนี้ที่ต้องใช้ค่าเฉลี่ยเลข**คณิต**: ``calculate_probability`` สุ่ม
    # ผลตอบแทนรายเดือนรอบค่านี้แล้วคูณทบเอง ตัวจำลองจึงหักส่วนต่าง σ²/2 ให้อยู่แล้ว
    # ส่ง CAGR เข้ามาตรงนี้ = หักซ้ำสองรอบ ⇒ ความน่าจะเป็นต่ำกว่าจริง
    probability = calculate_probability(
        current=goal.current_amount_thb,
        monthly_contribution=goal.monthly_contribution_thb,
        months=months,
        annual_return=drift_return,
        target=goal.target_amount_thb,
        volatility=volatility,
    )

    off_track, correction = check_off_track(goal, required_pmt)

    # ผลตอบแทนที่ "ต้องได้จริง" จากเงินออมที่ผู้ใช้ตั้งไว้ (ไม่ใช่ค่าคาดหวังของโปรไฟล์)
    # — เดิมส่ง expected_return เข้าไปเทียบกับตัวมันเอง คำเตือนจึงไม่มีวันทำงาน (M9)
    needed = required_annual_return(
        goal.target_amount_thb, goal.current_amount_thb, goal.monthly_contribution_thb, months
    )
    # ``needed`` มาจาก ``required_annual_return`` = ``(1+r_เดือน)**12 − 1`` คืออัตรา**ทบต้น**
    # ค่าที่ใช้แทนเมื่อหาคำตอบไม่ได้จึงต้องเป็นอัตราทบต้นด้วย ไม่งั้นเป็นการเทียบคนละหน่วย
    allocation = suggest_allocation(goal.risk_profile, needed if needed is not None else compound_return)

    assumptions_note = (
        f"ประมาณการใช้ผลตอบแทนทบต้น (CAGR) {compound_return*100:.1f}% ต่อปี "
        f"และความผันผวน {volatility*100:.1f}% (ที่มา: {assumptions_source}) — "
        "อิงสถิติอดีต เป็นสมมติฐาน ไม่ใช่การรับประกัน"
    )
    # ค่าเฉลี่ยเลขคณิตสูงกว่า CAGR ราว σ²/2 ต่อปีเสมอ — ถ้าโชว์ตัวเดียวโดยไม่บอกว่าเป็นตัวไหน
    # ผู้ใช้จะเอาไปเทียบกับ "ผลตอบแทนเฉลี่ย" ที่อ่านจากที่อื่นแล้วสรุปว่าระบบคำนวณผิด
    if round(drift_return * 100, 1) != round(compound_return * 100, 1):
        assumptions_note += (
            f" · ความน่าจะเป็นสำเร็จจำลองด้วยค่าเฉลี่ยเลขคณิต {drift_return*100:.1f}% ต่อปี "
            "(เป็น drift ต่องวดของการสุ่ม ไม่ใช่อัตราทบต้น)"
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
        # อัตรา**ทบต้น** (CAGR) — ตัวเดียวกับที่ PMT/มูลค่าคาดการณ์ใช้ ผู้ใช้เอาไปคูณต่อได้
        "assumed_annual_return_pct": round(compound_return * 100, 1),
        # drift ของ Monte Carlo (ค่าเฉลี่ยเลขคณิต) — ตั้งชื่อให้ต่างกันชัด ๆ เพราะสองค่านี้
        # ไม่เท่ากันโดยธรรมชาติ (ต่างกันราว σ²/2) และเคยถูกสลับกันมาแล้ว
        "montecarlo_drift_annual_pct": round(drift_return * 100, 1),
        "projected_value": round(projected_value, 2),
        "probability_of_success": round(probability, 4),
        "on_track": not off_track,
        "course_correction": correction,
        "suggested_allocation": allocation,
        "assumptions_source": assumptions_source,
        # ช่วงข้อมูลที่ใช้จริง (start/end/days/days_available/years/tickers) — ตัวเลขล้วนเป็น
        # str/int/float/list จาก ``portfolio_return_stats`` จึงผ่าน JSONResponse ได้ตรง ๆ
        # (routers/goals.py ไม่มี response_model มากรอง คีย์นี้จึงถึงผู้ใช้จริง)
        # ``None`` เมื่อใช้ preset — preset ไม่ได้วัดจากอดีต จึงไม่มีหน้าต่างข้อมูลให้อ้าง
        "assumptions_window": assumptions_window,
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
