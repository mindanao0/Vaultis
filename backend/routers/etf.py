from fastapi import APIRouter, HTTPException

from ..services import etf_service

router = APIRouter(prefix="/api/etf", tags=["ETF"])


@router.get("/prices")
def get_prices():
    try:
        return {"data": etf_service.get_etf_prices()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/returns")
def get_returns():
    try:
        return {"data": etf_service.get_etf_returns()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/risk")
def get_risk():
    try:
        return {"data": etf_service.get_etf_risk()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/correlation")
def get_correlation():
    try:
        return {"data": etf_service.get_etf_correlation()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/technical")
def get_technical():
    try:
        return {"data": etf_service.get_etf_technical()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
