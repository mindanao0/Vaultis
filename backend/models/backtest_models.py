"""Pydantic models for the backtest endpoint."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class BacktestRequest(BaseModel):
    symbol: str
    start: str
    end: str
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    run_optimization: bool = False


class BacktestResponse(BaseModel):
    symbol: str
    start: str
    end: str
    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    num_trades: int
    benchmark_return: float
    outperformed: bool
    best_params: Optional[dict] = None
    ai_summary: str
