# -*- coding: utf-8 -*-
"""โมดูล Backtest พอร์ต ETF ย้อนหลัง."""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from analysis.risk import DEFAULT_RISK_FREE_RATE
from data.fetcher import fetch_adjusted_close_data


TRADING_DAYS_PER_YEAR = 252


def _normalize_weights(weights: Dict[str, float]) -> pd.Series:
    """Normalize และ validate weights."""
    if not weights:
        raise ValueError("weights ว่าง ไม่สามารถทำ backtest ได้")

    normalized_weights = pd.Series(weights, dtype=float)
    normalized_weights = normalized_weights[normalized_weights > 0]
    if normalized_weights.empty:
        raise ValueError("weights ต้องมีค่ามากกว่า 0 อย่างน้อย 1 ตัว")

    weight_sum = float(normalized_weights.sum())
    if weight_sum <= 0:
        raise ValueError("ผลรวม weights ต้องมากกว่า 0")
    return normalized_weights / weight_sum


def _build_value_curve(returns: pd.Series, initial_investment: float) -> pd.Series:
    """แปลง daily returns เป็นมูลค่าพอร์ต."""
    return (1.0 + returns).cumprod() * initial_investment


def _calculate_metrics(value_curve: pd.Series, daily_returns: pd.Series) -> Dict[str, float]:
    """คำนวณผลลัพธ์หลักของ backtest (Sharpe หัก risk-free เดียวกับหน้า Risk)."""
    total_return = (float(value_curve.iloc[-1]) / float(value_curve.iloc[0])) - 1.0
    num_days = max((value_curve.index[-1] - value_curve.index[0]).days, 1)
    annualized_return = (1.0 + total_return) ** (365.25 / num_days) - 1.0

    running_max = value_curve.cummax()
    drawdown = (value_curve / running_max) - 1.0
    max_drawdown = float(drawdown.min())

    volatility = float(daily_returns.std() * np.sqrt(TRADING_DAYS_PER_YEAR))
    sharpe_ratio = 0.0
    if volatility > 0:
        excess = daily_returns.mean() * TRADING_DAYS_PER_YEAR - DEFAULT_RISK_FREE_RATE
        sharpe_ratio = float(excess / volatility)

    return {
        "Total Return %": total_return * 100.0,
        "Annualized Return %": annualized_return * 100.0,
        "Max Drawdown %": max_drawdown * 100.0,
        "Sharpe Ratio": sharpe_ratio,
    }


def run_portfolio_backtest(
    price_df: pd.DataFrame,
    weights: Dict[str, float],
    initial_capital: float = 10000.0,
) -> pd.DataFrame:
    """ทดสอบผลตอบแทนพอร์ตย้อนหลัง (rebalance รายวันตามน้ำหนักที่กำหนด).

    เริ่มนับจากวันแรกที่ **ทุก ETF ในพอร์ตมีราคาแล้ว** — เดิมใช้ ``fillna(0.0)``
    ทำให้ ETF ที่ยังไม่เกิด (เช่น QQQM ก่อน ต.ค. 2020) ถูกนับเป็นผลตอบแทน 0%
    ทั้งที่ถือน้ำหนักอยู่ → ฉุดผลย้อนหลังของทั้งพอร์ตให้ต่ำกว่าความจริง (AUDIT.md M4)
    """
    try:
        if price_df.empty:
            raise ValueError("price_df ว่าง ไม่สามารถทำ backtest ได้")

        normalized_weights = _normalize_weights(weights)

        valid_assets = [ticker for ticker in normalized_weights.index if ticker in price_df.columns]
        if not valid_assets:
            raise ValueError("ไม่พบ ticker ใน weights ที่ตรงกับข้อมูลราคา")

        active_weights = normalized_weights[valid_assets]
        active_weights = active_weights / active_weights.sum()

        # ตัดช่วงก่อนที่ทุกตัวจะมีข้อมูลออก แทนการเติมผลตอบแทน 0%
        portfolio_prices = price_df[valid_assets].ffill().dropna(how="any")
        if len(portfolio_prices) < 2:
            raise ValueError(
                "ข้อมูลราคาที่ทุก ETF มีร่วมกันไม่พอทำ backtest "
                "(ETF ที่เพิ่งเกิดใหม่จะตัดช่วงเริ่มต้นของพอร์ตให้สั้นลง)"
            )

        portfolio_returns = (
            portfolio_prices.pct_change().fillna(0.0).mul(active_weights, axis=1).sum(axis=1)
        )
        portfolio_value = _build_value_curve(portfolio_returns, initial_capital)

        return pd.DataFrame({"Portfolio Value": portfolio_value, "Portfolio Return": portfolio_returns})
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการทำ Portfolio Backtest: {exc}") from exc


def run_backtest(weights: Dict[str, float], initial_investment: float, start_date: str) -> Dict[str, Any]:
    """
    รัน backtest แบบ end-to-end:
    - ดึงข้อมูลราคา ETF ตาม ticker ใน weights + benchmark (VOO)
    - คำนวณผลตอบแทนพอร์ตและ benchmark
    - สรุป metrics + กราฟมูลค่าพอร์ตเทียบ benchmark
    """
    try:
        if initial_investment <= 0:
            raise ValueError("initial_investment ต้องมากกว่า 0")

        normalized_weights = _normalize_weights(weights)
        tickers = sorted(set(normalized_weights.index.tolist() + ["VOO"]))

        price_df = fetch_adjusted_close_data(tickers=tickers, years=30).ffill().sort_index()
        if price_df.empty:
            raise ValueError("ไม่พบข้อมูลราคา ETF สำหรับการทำ backtest")

        start_ts = pd.to_datetime(start_date)
        filtered_prices = price_df.loc[price_df.index >= start_ts].dropna(how="all")
        if filtered_prices.empty:
            raise ValueError("ไม่พบข้อมูลราคาหลัง start_date ที่ระบุ")

        available_assets = [ticker for ticker in normalized_weights.index if ticker in filtered_prices.columns]
        if not available_assets:
            raise ValueError("ไม่มี ticker ใน weights ที่มีข้อมูลราคาใช้งานได้")

        active_weights = normalized_weights[available_assets]
        active_weights = active_weights / active_weights.sum()

        portfolio_prices = filtered_prices[available_assets].dropna(how="any")
        if portfolio_prices.empty:
            raise ValueError("ข้อมูลราคาพอร์ตไม่เพียงพอหลังจัดการค่าว่าง")

        portfolio_returns = portfolio_prices.pct_change().fillna(0.0).mul(active_weights, axis=1).sum(axis=1)
        portfolio_value = _build_value_curve(portfolio_returns, initial_investment)

        benchmark_prices = filtered_prices[["VOO"]].dropna(how="any")
        benchmark_returns = benchmark_prices["VOO"].pct_change().fillna(0.0)
        benchmark_value = _build_value_curve(benchmark_returns, initial_investment)

        # ใช้ช่วงเวลาซ้อนกันจริงของพอร์ตและ benchmark เพื่อเทียบได้ตรงกัน
        common_index = portfolio_value.index.intersection(benchmark_value.index)
        if common_index.empty:
            raise ValueError("ไม่พบช่วงเวลาร่วมกันของพอร์ตและ benchmark")

        portfolio_value = portfolio_value.loc[common_index]
        portfolio_returns = portfolio_returns.loc[common_index]
        benchmark_value = benchmark_value.loc[common_index]
        benchmark_returns = benchmark_returns.loc[common_index]

        portfolio_metrics = _calculate_metrics(portfolio_value, portfolio_returns)
        benchmark_metrics = _calculate_metrics(benchmark_value, benchmark_returns)

        result_df = pd.DataFrame(
            {
                "Portfolio Value": portfolio_value,
                "Benchmark (VOO) Value": benchmark_value,
                "Portfolio Return": portfolio_returns,
                "Benchmark Return": benchmark_returns,
            }
        )

        figure = go.Figure()
        figure.add_trace(
            go.Scatter(
                x=result_df.index,
                y=result_df["Portfolio Value"],
                mode="lines",
                name="Portfolio",
            )
        )
        figure.add_trace(
            go.Scatter(
                x=result_df.index,
                y=result_df["Benchmark (VOO) Value"],
                mode="lines",
                name="Benchmark (VOO)",
            )
        )
        figure.update_layout(
            title="Portfolio Value vs Benchmark (VOO)",
            xaxis_title="Date",
            yaxis_title="Portfolio Value (USD)",
            legend_title="Series",
            template="plotly_white",
        )

        return {
            "portfolio_metrics": portfolio_metrics,
            "benchmark_metrics": benchmark_metrics,
            "backtest_df": result_df,
            "figure": figure,
        }
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการทำ Backtest: {exc}") from exc


if __name__ == "__main__":
    TEST_WEIGHTS = {"VOO": 0.4, "SCHD": 0.25, "QQQM": 0.2, "XLV": 0.1, "GLDM": 0.05}
    output = run_backtest(weights=TEST_WEIGHTS, initial_investment=10000.0, start_date="2015-01-01")

    print("Portfolio Metrics")
    for key, value in output["portfolio_metrics"].items():
        if "Ratio" in key:
            print(f"  - {key}: {value:.4f}")
        else:
            print(f"  - {key}: {value:.2f}%")

    print("\nBenchmark (VOO) Metrics")
    for key, value in output["benchmark_metrics"].items():
        if "Ratio" in key:
            print(f"  - {key}: {value:.4f}")
        else:
            print(f"  - {key}: {value:.2f}%")
