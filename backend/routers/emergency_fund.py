"""Emergency Fund Calculator router."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..models.emergency_fund_models import EmergencyFundRequest, EmergencyFundResult
from ..services import emergency_fund_service

router = APIRouter(prefix="/api/emergency-fund", tags=["emergency-fund"])


@router.post("/calculate", response_model=EmergencyFundResult)
def calculate(payload: EmergencyFundRequest) -> JSONResponse:
    """คำนวณเงินสำรองฉุกเฉินที่เหมาะสมตามโปรไฟล์ความเสี่ยงส่วนบุคคล"""
    try:
        result = emergency_fund_service.calculate(
            profile=payload.profile,
            monthly_expense=payload.monthly_expense,
            current_savings=payload.current_savings,
            monthly_saving_capacity=payload.monthly_saving_capacity,
        )
        return JSONResponse(
            content=result.model_dump(),
            media_type="application/json; charset=utf-8",
        )
    except ValueError as exc:
        # ตัวเลขที่ผู้ใช้กรอกทำให้คำนวณไม่ได้ (ล้นช่วง) — เป็นความผิดของ**คำขอ**
        # ห้ามตอบ 500 ซึ่งอ่านว่า "เซิร์ฟเวอร์พัง" แล้วผู้ใช้ไปตามหาปัญหาผิดที่
        # (บั๊กชนิดเดียวกับที่ D3.1 แก้ให้ /api/analysis/dcf ไปแล้ว)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
