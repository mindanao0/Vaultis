from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..services import etf_service

router = APIRouter(prefix="/api/etf", tags=["ETF"])


@router.get("/prices")
def get_prices():
    try:
        return JSONResponse(
            content={"data": etf_service.get_etf_prices()},
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/daily-snapshot")
def get_daily_snapshot():
    try:
        return JSONResponse(
            content={"data": etf_service.get_etf_daily_eod_snapshot()},
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/returns")
def get_returns():
    try:
        return JSONResponse(
            content={"data": etf_service.get_etf_returns()},
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/risk")
def get_risk():
    try:
        return JSONResponse(
            content={"data": etf_service.get_etf_risk()},
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/correlation")
def get_correlation():
    try:
        return JSONResponse(
            content={"data": etf_service.get_etf_correlation()},
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/technical")
def get_technical():
    try:
        return JSONResponse(
            content={"data": etf_service.get_etf_technical()},
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
