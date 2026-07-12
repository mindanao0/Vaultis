"""Debt Optimization router."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..models.debt_models import (
    DebtComparison,
    DebtResult,
    OptimizeRequest,
    SensitivityRequest,
    SensitivityResult,
)
from ..services import debt_service

router = APIRouter(prefix="/api/debt", tags=["debt"])


@router.post("/optimize")
def optimize(payload: OptimizeRequest) -> JSONResponse:
    """
    คำนวณแผนชำระหนี้ด้วยวิธี Avalanche และ/หรือ Snowball

    - method="both" → DebtComparison (เปรียบเทียบทั้งสองวิธี)
    - method="avalanche"|"snowball" → DebtResult (วิธีเดียว)
    """
    if not payload.debts:
        raise HTTPException(status_code=422, detail="debts ห้ามว่าง")
    try:
        if payload.method == "both":
            result: DebtComparison | DebtResult = debt_service.compare_methods(
                payload.debts, payload.monthly_budget
            )
        else:
            result = debt_service._simulate(
                payload.debts, payload.monthly_budget, payload.method
            )
        return JSONResponse(
            content=result.model_dump(),
            media_type="application/json; charset=utf-8",
        )
    except ValueError as exc:
        # แผนเป็นไปไม่ได้จริง (งบต่ำกว่ายอดขั้นต่ำ / หนี้ไม่มีวันหมด) — บอกผู้ใช้ตรง ๆ
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/sensitivity")
def sensitivity(payload: SensitivityRequest) -> JSONResponse:
    """
    วิเคราะห์ sensitivity — ผลของการเพิ่ม extra payment ต่อดอกเบี้ยรวมและระยะเวลาชำระหนี้
    """
    if not payload.debts:
        raise HTTPException(status_code=422, detail="debts ห้ามว่าง")
    try:
        results: list[SensitivityResult] = debt_service.sensitivity_analysis(
            debts=payload.debts,
            monthly_budget=payload.monthly_budget,
            method=payload.method,
            extra_payments=payload.extra_payments,
        )
        return JSONResponse(
            content=[r.model_dump() for r in results],
            media_type="application/json; charset=utf-8",
        )
    except ValueError as exc:
        # แผนเป็นไปไม่ได้จริง (งบต่ำกว่ายอดขั้นต่ำ / หนี้ไม่มีวันหมด) — บอกผู้ใช้ตรง ๆ
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
