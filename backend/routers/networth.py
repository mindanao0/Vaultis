"""Net Worth router."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.networth_models import NetWorthResponse, SnapshotRequest
from ..services import networth_service

router = APIRouter(prefix="/api/networth", tags=["networth"])


@router.get("/current", response_model=NetWorthResponse)
def get_current(db: Session = Depends(get_db)):
    try:
        return networth_service.get_current(db)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/history", response_model=list[NetWorthResponse])
def get_history(months: int = 12, db: Session = Depends(get_db)):
    if months < 1 or months > 120:
        raise HTTPException(status_code=422, detail="months ต้องอยู่ระหว่าง 1-120")
    try:
        return networth_service.get_history(db, months)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/snapshot", response_model=NetWorthResponse, status_code=201)
def save_snapshot(payload: SnapshotRequest, db: Session = Depends(get_db)):
    if not payload.assets:
        raise HTTPException(status_code=422, detail="ต้องมี assets อย่างน้อย 1 รายการ")
    try:
        return JSONResponse(
            content=networth_service.save_snapshot(db, payload).model_dump(),
            status_code=201,
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
