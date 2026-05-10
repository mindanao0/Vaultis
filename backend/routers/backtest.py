"""Backtest router: POST /api/backtest"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from analysis.backtest_engine import BacktestEngine
from analysis.backtest_summary import generate_summary
from backend.models.backtest_models import BacktestRequest, BacktestResponse

router = APIRouter(prefix="/api", tags=["backtest"])

_engine = BacktestEngine()


@router.post("/backtest", response_model=BacktestResponse)
def run_backtest(payload: BacktestRequest):
    strategy_params = {
        "rsi_period": payload.rsi_period,
        "rsi_oversold": payload.rsi_oversold,
        "rsi_overbought": payload.rsi_overbought,
        "macd_fast": payload.macd_fast,
        "macd_slow": payload.macd_slow,
        "macd_signal": payload.macd_signal,
    }

    best_params = None

    if payload.run_optimization:
        try:
            opt = _engine.optimize(payload.symbol, payload.start, payload.end)
            best_params = opt["best_params"]
            strategy_params.update(best_params)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Optimization failed: {exc}")

    try:
        result = _engine.run(
            payload.symbol,
            payload.start,
            payload.end,
            strategy_params=strategy_params,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Backtest failed: {exc}")

    try:
        ai_summary = generate_summary(result, payload.symbol)
    except Exception:
        ai_summary = "ไม่สามารถสร้างสรุป AI ได้ในขณะนี้"

    return BacktestResponse(
        **result,
        best_params=best_params,
        ai_summary=ai_summary,
    )
