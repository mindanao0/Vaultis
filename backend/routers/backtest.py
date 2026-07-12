"""Backtest router: POST /api/backtest"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from analysis.backtest_engine import BacktestEngine
from analysis.backtest_summary import generate_summary
from analysis.llm import AI_DISABLED_MESSAGE
from backend.models.backtest_models import BacktestRequest, BacktestResponse

router = APIRouter(prefix="/api", tags=["backtest"])

_engine = BacktestEngine()


@router.post("/backtest", response_model=BacktestResponse)
def run_backtest(
    payload: BacktestRequest,
    include_ai: bool = Query(False, description="เรียก AI อธิบายผล (มีค่าใช้จ่าย)"),
):
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

    # คำอธิบายจาก AI = ค่าใช้จ่าย → ต้องขอมาโดยตรงเท่านั้น (?include_ai=true)
    ai_summary = ""
    if include_ai:
        try:
            ai_summary = generate_summary(result, payload.symbol, user_initiated=True)
        except Exception as exc:
            ai_summary = f"ไม่สามารถสร้างสรุป AI ได้: {exc}"
    else:
        ai_summary = AI_DISABLED_MESSAGE

    return BacktestResponse(
        **result,
        best_params=best_params,
        ai_summary=ai_summary,
    )
