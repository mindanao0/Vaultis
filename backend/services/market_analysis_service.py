"""Service layer สำหรับ backtest / DCA sim / macro / DCF (AUDIT.md L5).

เดิม ``routers/analysis.py`` import จาก ``analysis/`` และ ``portfolio/`` ตรง ๆ
ซึ่งขัดกฎของโปรเจกต์เอง (Routers → Services → Analysis) — router ไม่ควรรู้จัก
โครงสร้างของชั้น analysis และไม่ควรแปลง DataFrame เป็น JSON เอง
"""

from __future__ import annotations

from typing import Any

from analysis.financial_model import dcf_valuation, run_full_analysis
from analysis.macro import get_macro_data
from data.fetcher import fetch_adjusted_close_data
from portfolio.backtest import run_portfolio_backtest
from portfolio.dca import simulate_monthly_dca
from utils.config import get_tickers

from .cache_service import PRICE_HISTORY_TTL, shared_cache


def _prices():
    tickers = get_tickers()
    key = "prices_10y:" + ",".join(sorted(tickers))
    return shared_cache.get_or_compute(
        key, PRICE_HISTORY_TTL, lambda: fetch_adjusted_close_data(tickers=tickers, years=10).ffill()
    )


def run_backtest(weights: dict[str, float], initial_capital: float) -> list[dict[str, Any]]:
    result = run_portfolio_backtest(
        price_df=_prices(), weights=weights, initial_capital=initial_capital
    )
    return result.reset_index().to_dict(orient="records")


def simulate_dca(weights: dict[str, float], monthly_investment: float) -> list[dict[str, Any]]:
    result = simulate_monthly_dca(
        price_df=_prices(), weights=weights, monthly_investment=monthly_investment
    )
    return result.reset_index().to_dict(orient="records")


def macro_snapshot() -> dict[str, Any]:
    return get_macro_data()


def dcf_for_ticker(ticker: str) -> dict[str, Any]:
    symbol = str(ticker).strip().upper()
    if not symbol:
        raise ValueError("ต้องระบุ ticker")
    return dcf_valuation(symbol)


def full_analysis(budget_thb: float) -> dict[str, Any]:
    return run_full_analysis(budget_thb=budget_thb)
