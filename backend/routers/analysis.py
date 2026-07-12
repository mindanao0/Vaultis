from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from data.fetcher import PriceDataUnavailableError

from ..schemas import BacktestRequest, DcaSimRequest
from ..services import market_analysis_service as service

router = APIRouter(prefix="/api", tags=["Analysis"])


def _json(data) -> JSONResponse:
    return JSONResponse(content={"data": data}, media_type="application/json; charset=utf-8")


@router.post("/backtest")
def run_backtest(payload: BacktestRequest):
    try:
        return _json(service.run_backtest(payload.weights, payload.initial_capital))
    except PriceDataUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/dca/simulate")
def run_dca_simulation(payload: DcaSimRequest):
    try:
        return _json(service.simulate_dca(payload.weights, payload.monthly_investment))
    except PriceDataUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/macro")
def get_macro():
    data = service.macro_snapshot()
    if not data:
        raise HTTPException(status_code=503, detail="ดึงข้อมูล macro ไม่สำเร็จ (ตรวจสอบ FRED_API_KEY)")
    return _json(data)


@router.get("/analysis/dcf/{ticker}")
def get_dcf_for_ticker(ticker: str):
    try:
        return _json(service.dcf_for_ticker(ticker))
    except ValueError as exc:
        # เช่น GLDM: สินทรัพย์ที่ไม่มีกำไร ทำ DCF ไม่ได้ — บอกตรง ๆ ไม่เดาตัวเลข
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PriceDataUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/analysis/full")
def get_full_financial_analysis(budget_thb: float = Query(5000, ge=1)):
    try:
        return _json(service.full_analysis(budget_thb))
    except PriceDataUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
