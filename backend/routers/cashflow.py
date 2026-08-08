"""Cash Flow Forecasting router.

นโยบายรหัสสถานะของไฟล์นี้ (AUDIT_ROUND2_2026-08-07) — สามชั้นที่ห้ามปนกัน:

- **422** = คำขอใช้ไม่ได้ (ค่าไม่ใช่ตัวเลขจำกัด, หมวดที่ scenario อ้างไม่มีอยู่จริง,
  ยังไม่มีเดือนที่จบให้คำนวณ) ⇒ ผู้เรียกแก้ที่ตัวเองได้
- **500** = ผลลัพธ์ของเราเองใช้ไม่ได้ (เช่น serialize ไม่ผ่านเพราะมี ``inf``/``NaN``
  หลุดออกมาจากการคำนวณ) ⇒ บั๊กของระบบ ห้ามเล่าเป็นความผิดของผู้เรียก
- **400** = ยังไม่ได้ import transactions เข้ามาเลย (สถานะของเซิร์ฟเวอร์ ไม่ใช่ค่าที่ส่งมา)

เดิม ``try`` คร่อมทั้ง ``build_forecast_response()`` **และ** ``JSONResponse(...)`` แล้วดัก
``ValueError`` เป็น 422 ⇒ ``ValueError("Out of range float values are not JSON compliant:
inf")`` ของ json encoder ถูกแปลงเป็น 422 ที่ ``detail`` เป็นสตริงอังกฤษ ทั้งที่ openapi
ประกาศว่า 422 ``detail`` เป็น **array ของ object ที่มี loc** ⇒ ไคลเอนต์ที่อ่าน
``detail[0].loc`` ตามเอกสารพัง และบั๊กจริงที่ทำให้ผลลัพธ์เป็น inf ในอนาคตจะถูกกลบ
เป็น "อินพุตผิด" ตอนนี้การ serialize จึงอยู่นอก ``try`` (ดู ``_forecast_json``)
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import AfterValidator

from ..models.cashflow_models import (
    BulkTransactionRequest,
    ForecastResponse,
    ScenarioRequest,
    TransactionItem,
)
from ..schemas import _finite_amount
from ..services import cashflow_service

router = APIRouter(prefix="/api/cashflow", tags=["cashflow"])

# In-memory store for transactions imported via /transactions/bulk
# Keyed by nothing — just one global list for now (will connect to DB/OCR later)
_stored_transactions: list[TransactionItem] = []


def _finite_balance(value: float) -> float:
    """``Query(ge=0)`` ไม่กัน ``inf`` (``inf >= 0`` เป็น True) — ต้องมีด่านนี้เพิ่ม."""
    return _finite_amount(value, "ยอดเงินคงเหลือปัจจุบัน (current_balance)")


def _finite_threshold(value: float | None) -> float | None:
    """``None`` = ไม่ได้ตั้งเกณฑ์ (คนละเรื่องกับตั้งเป็น 0) จึงปล่อยผ่านโดยไม่ตรวจ."""
    if value is None:
        return None
    return _finite_amount(value, "เกณฑ์เงินสำรองฉุกเฉิน (emergency_threshold)")


def _forecast_json(result: ForecastResponse) -> JSONResponse:
    """แปลงผลเป็น JSON — ล้มเหลวตรงนี้คือ **บั๊กของเรา** ต้องเป็น 500 ไม่ใช่ 422.

    ``JSONResponse`` เรียก ``json.dumps(..., allow_nan=False)`` ซึ่งโยน ``ValueError``
    เมื่อผลลัพธ์มี ``inf``/``NaN`` อินพุตที่ทำให้เกิดกรณีนี้ถูกปฏิเสธไปตั้งแต่ชั้น
    ``cashflow_models`` แล้ว (422 พร้อม ``loc`` ชี้แถว) ดังนั้นถ้ายังมาถึงตรงนี้ได้แปลว่า
    การคำนวณของเราเองผลิตค่าที่ไม่ใช่ตัวเลขจำกัด — ต้องดังเป็น 500 พร้อมบอกตรง ๆ
    ห้ามถูกกลบเป็น "อินพุตผิด" (AUDIT_ROUND2_2026-08-07)
    """
    try:
        return JSONResponse(
            content=result.model_dump(),
            media_type="application/json; charset=utf-8",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "ผลพยากรณ์มีค่าที่ไม่ใช่ตัวเลขจำกัด (inf/NaN) จึงส่งกลับเป็น JSON ไม่ได้ "
                f"— เป็นข้อผิดพลาดของระบบ ไม่ใช่ของคำขอ: {exc}"
            ),
        ) from exc


@router.get("/forecast", response_model=ForecastResponse)
def get_forecast(
    months: int = Query(default=3, ge=1, le=24),
    current_balance: Annotated[
        float, Query(ge=0), AfterValidator(_finite_balance)
    ] = 0.0,
    emergency_threshold: Annotated[
        float | None, Query(ge=0), AfterValidator(_finite_threshold)
    ] = None,
):
    """Forecast cash flow using the last-imported transaction set."""
    if not _stored_transactions:
        raise HTTPException(
            status_code=400,
            detail="ยังไม่มี transactions — กรุณา POST /api/cashflow/transactions/bulk ก่อน",
        )
    try:
        result = cashflow_service.build_forecast_response(
            transactions=_stored_transactions,
            months=months,
            current_balance=current_balance,
            emergency_threshold=emergency_threshold,
        )
    except ValueError as exc:
        # ข้อมูลไม่พอจะพยากรณ์ (ไม่มีเดือนที่จบแล้ว) — บอกผู้ใช้ตรง ๆ ห้ามคืนตัวเลขปลอม
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # อยู่นอก try โดยตั้งใจ: ความล้มเหลวตอน serialize เป็นบั๊กของเรา ไม่ใช่ของคำขอ
    return _forecast_json(result)


@router.post("/scenario", response_model=ForecastResponse)
def run_scenario(payload: ScenarioRequest):
    """Forecast with scenario adjustments applied to specific categories."""
    if not payload.transactions:
        raise HTTPException(status_code=422, detail="transactions ห้ามว่าง")
    try:
        result = cashflow_service.build_forecast_response(
            transactions=payload.transactions,
            months=payload.months,
            current_balance=payload.current_balance,
            scenarios=payload.scenarios,
        )
    except ValueError as exc:
        # scenario ระบุหมวดที่ไม่มี / ซ้ำหมวด / ไม่มีเดือนที่จบแล้ว — ต้องไม่เงียบ
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # อยู่นอก try โดยตั้งใจ: ความล้มเหลวตอน serialize เป็นบั๊กของเรา ไม่ใช่ของคำขอ
    return _forecast_json(result)


@router.post("/transactions/bulk", status_code=201)
def bulk_import(payload: BulkTransactionRequest):
    """Replace the in-memory transaction list (for testing / pre-DB wiring)."""
    global _stored_transactions
    if not payload.transactions:
        raise HTTPException(status_code=422, detail="transactions ห้ามว่าง")
    _stored_transactions = list(payload.transactions)
    return JSONResponse(
        content={"imported": len(_stored_transactions)},
        status_code=201,
        media_type="application/json; charset=utf-8",
    )
