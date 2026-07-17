# -*- coding: utf-8 -*-
"""โมดูลคำนวณตัวชี้วัดความเสี่ยงของ ETF."""

from __future__ import annotations

import numpy as np
import pandas as pd

from utils.cache import cache_data_1h

# อัตราปลอดความเสี่ยงมาตรฐานของทั้งระบบ — ใช้ค่าเดียวกันทุกที่ที่คำนวณ Sharpe
# (AUDIT.md M4: เดิม backtest ใช้ 0% ส่วนหน้า Risk ใช้ 2% → เทียบกันไม่ได้)
DEFAULT_RISK_FREE_RATE = 0.02


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
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
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


def underwater_series(prices: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    """ซีรีส์ % ต่ำกว่าจุดสูงสุดเดิม (underwater) — ค่ากลางตัวเดียวกับที่ใช้คิด Max Drawdown.

    0 = อยู่ที่ ATH, -0.25 = ต่ำกว่า ATH 25% (Roadmap A3 — กราฟ underwater)
    รับได้ทั้ง DataFrame (ต่อคอลัมน์) และ Series ตัวเดียว
    """
    if prices.empty:
        raise ValueError("prices ว่าง ไม่สามารถคำนวณ underwater ได้")
    cumulative_max = prices.ffill().cummax()
    return (prices / cumulative_max) - 1.0


def calculate_max_drawdown(price_df: pd.DataFrame) -> pd.Series:
    """คำนวณ Max Drawdown ของ ETF แต่ละตัว (จุดต่ำสุดของซีรีส์ underwater)."""
    try:
        return underwater_series(price_df).min()
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการคำนวณ Max Drawdown: {exc}") from exc


def drawdown_episodes(prices: pd.Series, min_depth: float = 0.10) -> list[dict]:
    """แยกรอบ drawdown ในอดีตของ ETF ตัวเดียว: พีค → จุดต่ำสุด → วันกลับมา ATH.

    ใช้เล่าประวัติ "เคยลงลึกแค่ไหน ฟื้นกี่เดือน" ประกอบกราฟ underwater (Roadmap A3)
    — สถิติเชิงบรรยายจากราคาจริง ไม่ใช่สัญญาณซื้อขาย และไม่เข้าเลขคะแนน/จัดสรรใด ๆ

    คืนเฉพาะรอบที่ลึกเกิน ``min_depth`` (สัดส่วน เช่น 0.10 = ลง 10%) เรียงจากลึกสุด
    รอบที่ยังไม่กลับมา ATH (รอบปัจจุบัน) จะมี ``recovery_date=None``
    """
    close = pd.to_numeric(prices, errors="coerce").dropna()
    if close.empty:
        raise ValueError("ไม่มีข้อมูลราคา ไม่สามารถแยกรอบ drawdown ได้")

    uw = underwater_series(close)
    in_drawdown = uw < 0
    runs = (in_drawdown != in_drawdown.shift(1)).cumsum()

    episodes: list[dict] = []
    for _, segment in uw[in_drawdown].groupby(runs[in_drawdown]):
        depth = float(segment.min())
        if depth > -abs(min_depth):
            continue
        start = segment.index[0]
        start_pos = int(uw.index.get_loc(start))
        peak_date = uw.index[start_pos - 1] if start_pos > 0 else start
        trough_date = segment.idxmin()
        end_pos = int(uw.index.get_loc(segment.index[-1]))
        recovered = end_pos + 1 < len(uw)  # มีวันถัดไปที่กลับมา ≥ ATH; ไม่มี = รอบปัจจุบัน
        recovery_date = uw.index[end_pos + 1] if recovered else None
        episodes.append(
            {
                "peak_date": peak_date,
                "trough_date": trough_date,
                "recovery_date": recovery_date,
                "depth_pct": round(depth * 100, 1),
                "months_to_trough": round((trough_date - peak_date).days / 30.44, 1),
                "months_to_recover": (
                    round((recovery_date - peak_date).days / 30.44, 1) if recovered else None
                ),
            }
        )

    episodes.sort(key=lambda e: e["depth_pct"])
    return episodes


def portfolio_mu_sigma(price_df: pd.DataFrame, weights: dict[str, float]) -> tuple[float, float]:
    """μ/σ ต่อปีของพอร์ตตามน้ำหนักที่ให้ — ตัวป้อน Monte Carlo (Roadmap ข้อ 15).

    ใช้ผลตอบแทนรายวันย้อนหลังของ ticker ที่มีทั้งน้ำหนัก > 0 และราคา
    (น้ำหนัก normalize ภายใน จึงส่งเป็นมูลค่าถือครองดิบ ๆ ได้เลย)

    ข้อมูล/น้ำหนักใช้ไม่ได้ → raise ValueError — ผู้เรียกค่อย fallback ไปค่า preset
    อย่างโปร่งใส ห้ามเงียบ ๆ กลายเป็นเลขคงที่ (AUDIT.md C1)
    """
    tickers = [t for t, w in weights.items() if w > 0 and t in price_df.columns]
    if not tickers:
        raise ValueError("ไม่มี ticker ที่มีทั้งน้ำหนักและข้อมูลราคา")
    daily_returns = calculate_daily_returns(price_df[tickers]).dropna()
    if daily_returns.empty:
        raise ValueError("ผลตอบแทนรายวันว่าง — คำนวณ μ/σ ไม่ได้")
    normalized = pd.Series({t: float(weights[t]) for t in tickers})
    normalized = normalized / normalized.sum()
    portfolio_daily = (daily_returns * normalized).sum(axis=1)
    mu = float(portfolio_daily.mean() * 252)
    sigma = float(portfolio_daily.std() * np.sqrt(252))
    if not np.isfinite(mu) or not np.isfinite(sigma) or sigma <= 0:
        raise ValueError("μ/σ ที่ได้ไม่สมเหตุสมผล (ข้อมูลอาจสั้น/นิ่งเกินไป)")
    return mu, sigma


@cache_data_1h
def calculate_risk_metrics(
    price_df: pd.DataFrame, risk_free_rate: float = DEFAULT_RISK_FREE_RATE
) -> pd.DataFrame:
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
