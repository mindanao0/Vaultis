from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from analysis.financial_model import ALLOCATION_UNIT_THB
from data.fetcher import PriceDataUnavailableError

from ..schemas import DcaSimRequest, PortfolioBacktestRequest
from ..services import market_analysis_service as service

router = APIRouter(prefix="/api", tags=["Analysis"])


def _json(data) -> JSONResponse:
    return JSONResponse(content={"data": data}, media_type="application/json; charset=utf-8")


def _looks_like_unknown_symbol(exc: Exception) -> bool:
    """แยก "ไม่มีสัญลักษณ์นี้" (404 จาก Yahoo) ออกจาก "ดึงไม่สำเร็จชั่วคราว".

    yfinance ปัจจุบันใช้ curl_cffi ซึ่งโยน ``HTTPError`` ของตัวเอง (subclass ของ
    ``OSError`` ไม่ใช่ ``requests.HTTPError``) — จับด้วยชนิดข้ามไลบรารีไม่ได้
    จึงอ่าน status code จากอ็อบเจกต์ก่อน แล้วค่อยถอยไปดูข้อความ
    """
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None) or getattr(exc, "status_code", None)
    if status is not None:
        return int(status) == 404
    return "404" in str(exc)


# เดิม path นี้คือ "/backtest" ซึ่งชนกับ routers/backtest.py (กลยุทธ์ RSI+MACD ด้วย vectorbt)
# router ตัวนี้ถูก include ก่อน FastAPI จึงส่งงานมาที่นี่เสมอ → ทั้งไฟล์ backtest.py
# กลายเป็นโค้ดตาย ขณะที่ /docs โฆษณา schema ของตัวที่เข้าไม่ถึง (ยิงตามเอกสารได้ 422)
# Backend Router Map ยกชื่อ /api/backtest ให้ routers/backtest.py ตัวนี้จึงย้ายมาอยู่ใต้ /api/analysis
@router.post("/analysis/backtest")
def run_portfolio_weight_backtest(payload: PortfolioBacktestRequest):
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
    except Exception as exc:
        # เดิมข้อผิดพลาดจาก yfinance หลุดออกไปเป็น 500 + traceback ภาษาอังกฤษ
        # (ticker ที่ไม่มีจริง → HTTP 404 จาก Yahoo) — AUDIT_2026-08-06 D3.1
        if _looks_like_unknown_symbol(exc):
            raise HTTPException(
                status_code=404,
                detail=f"ไม่พบสัญลักษณ์ {ticker} ที่แหล่งข้อมูล (ตรวจตัวสะกดอีกครั้ง)",
            ) from exc
        raise HTTPException(
            status_code=503,
            detail=f"ดึงข้อมูลของ {ticker} ไม่สำเร็จ: {exc}",
        ) from exc


@router.get("/analysis/full")
def get_full_financial_analysis(budget_thb: float = Query(5000, ge=1)):
    # งบต่ำกว่าหนึ่งก้อน (100 บาท) แจกไม่ลงสักตัว เดิมได้แผนว่างแล้วปลายทางรายงานว่า
    # "ดึงข้อมูลไม่ได้" ทั้งที่ข้อมูลครบ — AUDIT_2026-08-06 D3.10
    if budget_thb < ALLOCATION_UNIT_THB:
        raise HTTPException(
            status_code=422,
            detail=(
                f"งบต้องอย่างน้อย {ALLOCATION_UNIT_THB} บาท "
                "(แผนจัดสรรปัดเป็นหลักร้อย งบน้อยกว่านี้แจกไม่ลงสักกอง)"
            ),
        )
    try:
        return _json(service.full_analysis(budget_thb))
    except PriceDataUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
