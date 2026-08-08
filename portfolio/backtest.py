# -*- coding: utf-8 -*-
"""โมดูล Backtest พอร์ต ETF ย้อนหลัง."""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from analysis.risk import DEFAULT_RISK_FREE_RATE
from data.fetcher import fetch_adjusted_close_data
# "ใครไม่ได้อยู่ในผลลัพธ์และเพราะอะไร" มีนิยามเดียวทั้งโปรเจกต์ อยู่ที่ ``portfolio/dca.py``
# (AUDIT_ROUND2_2026-08-07 T6) — Backtest กับ DCA Simulator ตัดกองออกด้วยเหตุผลเดียวกัน
# เป๊ะ ๆ ("ตั้งน้ำหนักไว้ 0" กับ "ไม่มีคอลัมน์ราคา") ถ้าต่างคนต่างเขียนคีย์/ข้อความของ
# ตัวเอง หน้าจอจะต้องรู้จักสองสำนวน แล้วสำนวนที่ถูกลืมจะกลายเป็นการตัดทิ้งเงียบอีกครั้ง
# (``backend/services/market_analysis_service.py`` ก็ import ชุดนี้จาก ``dca`` อยู่แล้ว)
from portfolio.dca import (
    COVERAGE_ATTR,
    _split_by_weight,
    _with_exclusions,
    describe_coverage,
)
from portfolio.weight_rules import validate_weights


TRADING_DAYS_PER_YEAR = 252


def _normalize_weights(weights: Dict[str, float]) -> pd.Series:
    """ตรวจและ normalize weights ให้รวมเป็น 1 — **ไม่ตัดกองไหนทิ้งที่นี่**.

    ด่านตรวจอยู่ที่ ``portfolio/weight_rules.validate_weights()`` ที่เดียว — เดิมด่านที่นี่
    เขียนเป็น ``normalized_weights > 0`` ซึ่ง ``inf`` ผ่านได้ แล้วกลายเป็น ``NaN``
    ตอนหาร ⇒ เส้นมูลค่าแบนราบที่ทุนตั้งต้น (AUDIT_ROUND2_2026-08-07 G8)

    บรรทัด ``normalized_weights[normalized_weights > 0]`` เคยอยู่ตรงนี้ และมันคือจุดที่
    กองน้ำหนัก 0 **หายจากพอร์ตโดยไม่มีใครรู้**: ที่เหลือถูก normalize ใหม่ให้รวมเป็น
    1.0 ⇒ ตัวเลขที่ตอบกลับไปเป็นผลของ *พอร์ตอื่น* แต่ถูกนำเสนอเป็นคำตอบของพอร์ตที่
    ผู้ใช้กรอก (T6) การตัดยังต้องทำอยู่ (กองที่ไม่ได้ถือไม่ควรบีบช่วงเวลาให้สั้นลง)
    แต่ต้องทำที่ผู้เรียกด้วย :func:`portfolio.dca._split_by_weight` ซึ่ง **คืนรายชื่อ
    ที่ถูกตัดออกมาด้วย** เพื่อให้ชื่อกองเดินทางไปถึงหน้าจอ — ห้ามย้ายกลับมาที่นี่
    """
    validated = validate_weights(weights)  # inf/NaN/ติดลบ/รวมกันไม่ได้ ตายที่นี่ พร้อมชื่อกอง

    normalized_weights = pd.Series(validated, dtype=float)

    # ตาข่ายชั้นสอง: validate_weights การันตีไว้แล้วว่าผลรวม > 0
    # แต่ถ้ามีใครแก้กติกาที่นั่น ที่นี่ต้องล้มดัง ไม่ใช่หารด้วย 0 เงียบ ๆ
    weight_sum = float(normalized_weights.sum())
    if weight_sum <= 0:
        raise ValueError("ผลรวม weights ต้องมากกว่า 0")
    return normalized_weights / weight_sum


def _held_and_dropped(weights: Dict[str, float]) -> tuple[Dict[str, float], list[str]]:
    """แยก "กองที่ถือจริง" ออกจาก "กองที่ตั้งน้ำหนัก 0" พร้อมคืนชื่อกลุ่มหลังออกมาด้วย.

    เป็นเพียงทางเข้าเดียวของไฟล์นี้ไปยัง :func:`portfolio.dca._split_by_weight`
    (นิยามเดียว — ดูเหตุผลที่ import ด้านบน) มีไว้เพื่อให้ทั้งสองฟังก์ชันสาธารณะ
    ของไฟล์นี้เรียกแบบเดียวกัน และเพื่อให้ ``validate_weights`` ถูกเรียก **ก่อน**
    การคัดกอง เสมอ ไม่งั้นค่าเสีย (inf/NaN/ติดลบ) ของกองที่ถูกคัดออกจะรอดด่าน
    """
    return _split_by_weight(validate_weights(weights))


def _weighted_daily_returns(portfolio_prices: pd.DataFrame, active_weights: pd.Series) -> pd.Series:
    """ผลตอบแทนรายวันของพอร์ตตามน้ำหนัก — แถวที่ไม่มีค่าจริงเลยต้องเป็น ``NaN``.

    ``DataFrame.sum(axis=1)`` ใช้ ``skipna=True`` เป็นค่าเริ่มต้น แถวที่เป็น ``NaN``
    ล้วนจึงถูกยุบเป็น **0.0** ซึ่งอ่านได้ว่า "วันนั้นพอร์ตไม่ขยับ" ทั้งที่ความจริงคือ
    "ไม่รู้" — ``min_count=1`` บังคับให้ต้องมีค่าจริงอย่างน้อยหนึ่งช่องถึงจะสรุปเป็น
    ตัวเลขได้ (ตาข่ายชั้นสองของ G8 คู่กับ ``validate_weights``)
    """
    return (
        portfolio_prices.pct_change()
        .fillna(0.0)
        .mul(active_weights, axis=1)
        .sum(axis=1, min_count=1)
    )


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

    ผลลัพธ์พก **รายชื่อกองที่ไม่ได้อยู่ในการทดสอบ** ไว้ที่ ``df.attrs[COVERAGE_ATTR]``
    สองกลุ่ม แบบเดียวกับ ``portfolio/dca.py`` (AUDIT_ROUND2_2026-08-07 T6):
    ``excluded_zero_weight`` = ตั้งน้ำหนักไว้ 0 (เจตนาของผู้ใช้ ไม่ใช่ความล้มเหลว)
    และ ``excluded_no_price`` = ถือน้ำหนักอยู่แต่ไม่มีคอลัมน์ราคาในชุดที่ส่งมา ⇒
    เส้นมูลค่าที่ได้เป็นของพอร์ตที่ normalize ใหม่บนกองที่เหลือ **ไม่ใช่พอร์ตที่กรอกมา**
    ใช้ ``describe_coverage()`` แปลงเป็นข้อความไทยหนึ่งบรรทัด — ห้ามแสดงตัวเลข
    โดยไม่แสดงคำเตือนคู่กัน
    """
    try:
        if price_df.empty:
            raise ValueError("price_df ว่าง ไม่สามารถทำ backtest ได้")

        # ตรวจน้ำหนัก **ทั้งชุด** ก่อนคัดกอง แล้วเก็บชื่อกองที่ตั้งไว้ 0 ไว้รายงาน
        held, zero_weight = _held_and_dropped(weights)
        normalized_weights = _normalize_weights(held)

        valid_assets = [ticker for ticker in normalized_weights.index if ticker in price_df.columns]
        no_price = [ticker for ticker in normalized_weights.index if ticker not in price_df.columns]
        if not valid_assets:
            raise ValueError(
                "ไม่พบ ticker ใน weights ที่ตรงกับข้อมูลราคา: "
                + ", ".join(no_price or list(held))
            )

        active_weights = normalized_weights[valid_assets]
        active_weights = active_weights / active_weights.sum()

        # ตัดช่วงก่อนที่ทุกตัวจะมีข้อมูลออก แทนการเติมผลตอบแทน 0%
        portfolio_prices = price_df[valid_assets].ffill().dropna(how="any")
        if len(portfolio_prices) < 2:
            raise ValueError(
                "ข้อมูลราคาที่ทุก ETF มีร่วมกันไม่พอทำ backtest "
                "(ETF ที่เพิ่งเกิดใหม่จะตัดช่วงเริ่มต้นของพอร์ตให้สั้นลง)"
            )

        portfolio_returns = _weighted_daily_returns(portfolio_prices, active_weights)
        portfolio_value = _build_value_curve(portfolio_returns, initial_capital)

        result = pd.DataFrame(
            {"Portfolio Value": portfolio_value, "Portfolio Return": portfolio_returns}
        )
        # ตั้งคีย์เสมอแม้เป็นลิสต์ว่าง — "ไม่มีใครถูกตัด" ต้องแยกออกจาก "ผลลัพธ์เก่าที่ยังไม่มีฟิลด์นี้"
        result.attrs[COVERAGE_ATTR] = _with_exclusions({}, zero_weight, no_price)
        return result
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการทำ Portfolio Backtest: {exc}") from exc


def run_backtest(weights: Dict[str, float], initial_investment: float, start_date: str) -> Dict[str, Any]:
    """
    รัน backtest แบบ end-to-end:
    - ดึงข้อมูลราคา ETF ตาม ticker ใน weights + benchmark (VOO)
    - คำนวณผลตอบแทนพอร์ตและ benchmark
    - สรุป metrics + กราฟมูลค่าพอร์ตเทียบ benchmark

    ผลลัพธ์มี ``coverage`` + ``coverage_warning`` ติดมาด้วยเสมอ (รูปแบบเดียวกับ
    ``portfolio.dca.simulate_dca``) = รายชื่อกองที่ **ไม่ได้อยู่ในผลการทดสอบ**
    แยกเป็น "ตั้งน้ำหนักไว้ 0" กับ "ไม่มีข้อมูลราคา" — ตัดกองออกจากพอร์ตเงียบ ๆ
    ผิดพอกับกุตัวเลข (AUDIT_ROUND2_2026-08-07 T6)
    """
    try:
        if initial_investment <= 0:
            raise ValueError("initial_investment ต้องมากกว่า 0")

        # กองที่ตั้งน้ำหนัก 0 ไม่ต้องดึงราคาและไม่ต้องบีบช่วงเวลา — แต่ต้องมีชื่ออยู่ในรายงาน
        held, zero_weight = _held_and_dropped(weights)
        normalized_weights = _normalize_weights(held)
        tickers = sorted(set(normalized_weights.index.tolist() + ["VOO"]))

        price_df = fetch_adjusted_close_data(tickers=tickers, years=30).ffill().sort_index()
        if price_df.empty:
            raise ValueError("ไม่พบข้อมูลราคา ETF สำหรับการทำ backtest")

        start_ts = pd.to_datetime(start_date)
        filtered_prices = price_df.loc[price_df.index >= start_ts].dropna(how="all")
        if filtered_prices.empty:
            raise ValueError("ไม่พบข้อมูลราคาหลัง start_date ที่ระบุ")

        available_assets = [ticker for ticker in normalized_weights.index if ticker in filtered_prices.columns]
        no_price = [ticker for ticker in normalized_weights.index if ticker not in filtered_prices.columns]
        if not available_assets:
            raise ValueError(
                "ไม่มี ticker ใน weights ที่มีข้อมูลราคาใช้งานได้: "
                + ", ".join(no_price or list(held))
            )

        active_weights = normalized_weights[available_assets]
        active_weights = active_weights / active_weights.sum()

        portfolio_prices = filtered_prices[available_assets].dropna(how="any")
        if portfolio_prices.empty:
            raise ValueError("ข้อมูลราคาพอร์ตไม่เพียงพอหลังจัดการค่าว่าง")

        portfolio_returns = _weighted_daily_returns(portfolio_prices, active_weights)
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

        coverage = _with_exclusions({}, zero_weight, no_price)
        result_df.attrs[COVERAGE_ATTR] = coverage

        return {
            "portfolio_metrics": portfolio_metrics,
            "benchmark_metrics": benchmark_metrics,
            "backtest_df": result_df,
            "figure": figure,
            "coverage": coverage,
            # ข้อความไทยพร้อมแสดง (``None`` = ไม่มีกองไหนหายไปจากพอร์ตที่กรอกมา)
            "coverage_warning": describe_coverage(coverage),
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
