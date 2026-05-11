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
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
