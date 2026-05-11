"""Cash Flow Forecasting router."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from ..models.cashflow_models import (
    BulkTransactionRequest,
    ForecastResponse,
    ScenarioRequest,
    TransactionItem,
)
from ..services import cashflow_service

router = APIRouter(prefix="/api/cashflow", tags=["cashflow"])

# In-memory store for transactions imported via /transactions/bulk
# Keyed by nothing — just one global list for now (will connect to DB/OCR later)
_stored_transactions: list[TransactionItem] = []


@router.get("/forecast", response_model=ForecastResponse)
def get_forecast(
    months: int = Query(default=3, ge=1, le=24),
    current_balance: float = Query(default=0.0, ge=0),
    emergency_threshold: float | None = Query(default=None, ge=0),
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
        return JSONResponse(
            content=result.model_dump(),
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
        return JSONResponse(
            content=result.model_dump(),
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
