# -*- coding: utf-8 -*-
"""โมดูลคำนวณตัวชี้วัดความเสี่ยงของ ETF."""

from __future__ import annotations

import numpy as np
import pandas as pd

from utils.cache import cache_data_1h


def calculate_daily_returns(price_df: pd.DataFrame) -> pd.DataFrame:
    """คำนวณผลตอบแทนรายวันจากราคา Adjusted Close."""
    try:
        if price_df.empty:
            raise ValueError("price_df ว่าง ไม่สามารถคำนวณผลตอบแทนรายวันได้")
        return price_df.sort_index().pct_change().dropna(how="all")
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการคำนวณผลตอบแทนรายวัน: {exc}") from exc


def calculate_volatility(price_df: pd.DataFrame, annualization: int = 252) -> pd.Series:
    """คำนวณความผันผวนรายปี (Annualized Volatility)."""
    try:
        daily_returns = calculate_daily_returns(price_df)
        volatility = daily_returns.std() * np.sqrt(annualization)
        return volatility
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการคำนวณ Volatility: {exc}") from exc


def calculate_sharpe_ratio(
    price_df: pd.DataFrame,
    risk_free_rate: float = 0.02,
    annualization: int = 252,
) -> pd.Series:
    """คำนวณ Sharpe Ratio แบบ annualized."""
    try:
        daily_returns = calculate_daily_returns(price_df)
        mean_return = daily_returns.mean() * annualization
        volatility = daily_returns.std() * np.sqrt(annualization)
        sharpe = (mean_return - risk_free_rate) / volatility.replace(0, np.nan)
        return sharpe
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการคำนวณ Sharpe Ratio: {exc}") from exc


def calculate_max_drawdown(price_df: pd.DataFrame) -> pd.Series:
    """คำนวณ Max Drawdown ของ ETF แต่ละตัว."""
    try:
        if price_df.empty:
            raise ValueError("price_df ว่าง ไม่สามารถคำนวณ Max Drawdown ได้")
        cumulative_max = price_df.ffill().cummax()
        drawdown = (price_df / cumulative_max) - 1.0
        max_drawdown = drawdown.min()
        return max_drawdown
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการคำนวณ Max Drawdown: {exc}") from exc


@cache_data_1h
def calculate_risk_metrics(price_df: pd.DataFrame, risk_free_rate: float = 0.02) -> pd.DataFrame:
    """รวมผลลัพธ์ตัวชี้วัดความเสี่ยงเป็นตารางเดียว."""
    try:
        metrics = pd.DataFrame(
            {
                "Volatility": calculate_volatility(price_df),
                "Sharpe Ratio": calculate_sharpe_ratio(price_df, risk_free_rate=risk_free_rate),
                "Max Drawdown": calculate_max_drawdown(price_df),
            }
        )
        return metrics
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการรวม Risk Metrics: {exc}") from exc
