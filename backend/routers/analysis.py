from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from analysis.financial_model import dcf_valuation, run_full_analysis
from analysis.macro import get_macro_data
from data.fetcher import fetch_adjusted_close_data
from portfolio.backtest import run_portfolio_backtest
from portfolio.dca import simulate_monthly_dca
from utils.config import get_tickers

from ..schemas import BacktestRequest, DcaSimRequest

router = APIRouter(prefix="/api", tags=["Analysis"])


@router.post("/backtest")
def run_backtest(payload: BacktestRequest):
    try:
        prices = fetch_adjusted_close_data(get_tickers(), years=10)
        result = run_portfolio_backtest(
            prices=prices,
            weights=payload.weights,
            initial_capital=payload.initial_capital,
        )
        return JSONResponse(
            content={"data": result.reset_index().to_dict(orient="records")},
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/dca/simulate")
def run_dca_simulation(payload: DcaSimRequest):
    try:
        prices = fetch_adjusted_close_data(get_tickers(), years=10)
        result = simulate_monthly_dca(
            prices=prices,
            weights=payload.weights,
            monthly_investment=payload.monthly_investment,
        )
        return JSONResponse(
            content={"data": result.reset_index().to_dict(orient="records")},
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/macro")
def get_macro():
    try:
        return JSONResponse(
            content={"data": get_macro_data()},
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/analysis/dcf/{ticker}")
def get_dcf_for_ticker(ticker: str):
    try:
        symbol = str(ticker).strip().upper()
        if not symbol:
            raise ValueError("ticker is required")
        return JSONResponse(
            content={"data": dcf_valuation(symbol)},
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/analysis/full")
def get_full_financial_analysis(budget_thb: float = Query(5000, ge=1)):
    try:
        return JSONResponse(
            content={"data": run_full_analysis(budget_thb=budget_thb)},
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
