"""Pydantic models for the backtest endpoint."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


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
    """ผลลัพธ์ของ ``POST /api/backtest``.

    ``None`` = **ไม่นิยาม** ไม่ใช่ 0: เมื่อ ``num_trades == 0`` กลยุทธ์ไม่เคยเข้าเทรด
    จึงไม่มีผลตอบแทน/Sharpe/Max Drawdown/Win Rate และ **เทียบกับ Buy & Hold ไม่ได้**
    (``outperformed`` เป็น ``None``) เหตุผลอยู่ในช่อง ``detail`` เป็นภาษาไทย
    (AUDIT_2026-08-06 B3.1)

    ``extra="forbid"`` ตั้งใจ: ค่าเริ่มต้นของ pydantic v2 คือทิ้งคีย์ที่ไม่ได้ประกาศ
    **เงียบ ๆ** ซึ่งเป็นสาเหตุที่ ``strategy_used`` (กลยุทธ์ที่ใช้จริง — RSI+MACD หรือ
    RSI ล้วนแบบ fallback) หายก่อนถึงผู้ใช้มาตลอด (B3.4) ต่อไปนี้คีย์ใหม่ที่ engine
    เพิ่มโดยไม่ประกาศตรงนี้จะพังเสียงดังแทนที่จะหายไปเฉย ๆ
    """

    model_config = ConfigDict(extra="forbid")

    symbol: str
    start: str
    end: str
    # ห้ามสลับกลยุทธ์เงียบ: "rsi_macd_3day_window" หรือ "rsi_only_fallback"
    strategy_used: str
    total_return: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    max_drawdown: Optional[float] = None
    win_rate: Optional[float] = None
    num_trades: int
    benchmark_return: Optional[float] = None
    outperformed: Optional[bool] = None
    detail: Optional[str] = None
    best_params: Optional[dict] = None
    # ผลเต็มของ optimize(): train/test period, train_sharpe, test_sharpe, all_results, note
    # (คำเตือน overfit อยู่ใน note — เดิมถูกทิ้งทั้งบล็อกเหลือแต่ best_params)
    optimization: Optional[dict] = None
    ai_summary: str
