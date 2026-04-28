from fastapi import APIRouter, HTTPException

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
        return {"data": result.reset_index().to_dict(orient="records")}
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
        return {"data": result.reset_index().to_dict(orient="records")}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/macro")
def get_macro():
    try:
        return {"data": get_macro_data()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
