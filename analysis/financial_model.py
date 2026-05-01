# -*- coding: utf-8 -*-
"""Financial modeling helpers for ETFs: 3-statement-style fundamentals + simplified DCF + scoring."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import pandas as pd
import yfinance as yf

from analysis.ta_compat import ta


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _close_series(hist: pd.DataFrame, ticker: str) -> pd.Series:
    if hist.empty:
        raise ValueError(f"No price history for {ticker}")
    if "Close" in hist.columns:
        close = hist["Close"]
    elif "Adj Close" in hist.columns:
        close = hist["Adj Close"]
    else:
        raise ValueError(f"No Close column for {ticker}")
    if isinstance(close, pd.DataFrame):
        if ticker in close.columns:
            s = close[ticker]
        else:
            s = close.iloc[:, 0]
    else:
        s = close
    return pd.to_numeric(s, errors="coerce").dropna().sort_index()


def _download_close(ticker: str, period: str) -> pd.Series:
    hist = yf.download(ticker, period=period, progress=False, auto_adjust=False)
    return _close_series(hist, ticker)


def get_rsi(ticker: str, length: int = 14) -> float:
    close = _download_close(ticker, "1y")
    if len(close) < length + 1:
        raise ValueError(f"Not enough data for RSI ({ticker})")
    rsi_series = ta.rsi(close, length=length)
    return _safe_float(rsi_series.iloc[-1], 50.0)


def calculate_income_statement(ticker: str) -> dict[str, Any]:
    t = yf.Ticker(ticker)
    info = t.info or {}
    return {
        "dividend_yield": info.get("dividendYield", 0) or 0,
        "trailing_eps": info.get("trailingEps", 0) or 0,
        "revenue_growth": info.get("revenueGrowth", 0) or 0,
        "profit_margin": info.get("profitMargins", 0) or 0,
        "operating_margin": info.get("operatingMargins", 0) or 0,
        "return_on_equity": info.get("returnOnEquity", 0) or 0,
        "return_on_assets": info.get("returnOnAssets", 0) or 0,
    }


def calculate_balance_sheet(ticker: str) -> dict[str, Any]:
    t = yf.Ticker(ticker)
    info = t.info or {}
    total_assets = info.get("totalAssets", 0) or 0
    return {
        "price_to_book": info.get("priceToBook", 0) or 0,
        "debt_to_equity": info.get("debtToEquity", 0) or 0,
        "current_ratio": info.get("currentRatio", 0) or 0,
        "total_assets": total_assets,
        "nav": info.get("navPrice", 0) or 0,
        "aum": total_assets,
        "expense_ratio": info.get("annualReportExpenseRatio", 0) or 0,
    }


def calculate_cash_flow(ticker: str) -> dict[str, Any]:
    t = yf.Ticker(ticker)
    info = t.info or {}
    close = _download_close(ticker, "5y")
    annual_last = close.resample("YE").last()
    annual_returns = annual_last.pct_change().dropna()
    return {
        "dividend_per_share": info.get("dividendRate", 0) or 0,
        "5y_avg_return": _safe_float(annual_returns.mean(), 0.0),
        "5y_return_std": _safe_float(annual_returns.std(), 0.0),
        "free_cash_flow": info.get("freeCashflow", 0) or 0,
        "operating_cash_flow": info.get("operatingCashflow", 0) or 0,
    }


def dcf_valuation(ticker: str, years: int = 10) -> dict[str, Any]:
    t = yf.Ticker(ticker)
    info = t.info or {}
    hist = yf.download(ticker, period="10y", progress=False, auto_adjust=False)
    close = _close_series(hist, ticker)

    try:
        current_price = _safe_float(t.fast_info["last_price"], 0.0)
    except Exception:
        current_price = _safe_float(close.iloc[-1], 0.0)
    if current_price <= 0:
        current_price = _safe_float(close.iloc[-1], 0.0)

    annual_last = close.resample("YE").last()
    annual_returns = annual_last.pct_change().dropna()
    tail_mean = _safe_float(annual_returns.tail(3).mean(), 0.0) if len(annual_returns) else 0.0
    growth_rate_high = min(tail_mean, 0.12)
    growth_rate_high = max(growth_rate_high, 0.04)

    terminal_growth = 0.03
    risk_free_rate = 0.043
    equity_risk_premium = 0.065
    beta_raw = info.get("beta3Year") or info.get("beta") or 1.0
    beta = _safe_float(beta_raw, 1.0) or 1.0
    wacc = risk_free_rate + beta * equity_risk_premium

    if wacc - terminal_growth < 0.005:
        terminal_growth = max(0.01, wacc - 0.01)

    dividend = _safe_float(info.get("dividendRate"), 0.0)
    pe_ratio = _safe_float(info.get("trailingPE"), 20.0) or 20.0
    if pe_ratio <= 0:
        pe_ratio = 20.0
    earnings_per_price = 1 / pe_ratio
    base_cf = current_price * earnings_per_price + dividend

    cash_flows: list[dict[str, Any]] = []
    for year in range(1, years + 1):
        if year <= 5:
            cf = base_cf * (1 + growth_rate_high) ** year
        else:
            cf = base_cf * (1 + growth_rate_high) ** 5 * (1 + terminal_growth) ** (year - 5)
        pv = cf / (1 + wacc) ** year
        cash_flows.append({"year": year, "cash_flow": round(cf, 2), "present_value": round(pv, 2)})

    last_cf = cash_flows[-1]["cash_flow"]
    terminal_value = last_cf * (1 + terminal_growth) / (wacc - terminal_growth)
    pv_terminal = terminal_value / (1 + wacc) ** years

    intrinsic_value = sum(_safe_float(cf["present_value"], 0.0) for cf in cash_flows) + pv_terminal
    if intrinsic_value <= 0:
        intrinsic_value = current_price

    margin_of_safety = (intrinsic_value - current_price) / intrinsic_value * 100 if intrinsic_value else 0.0

    signal = (
        "Strong Buy"
        if margin_of_safety > 30
        else "Buy"
        if margin_of_safety > 15
        else "Fair Value"
        if margin_of_safety > 0
        else "Overvalued"
        if margin_of_safety > -15
        else "Avoid"
    )

    return {
        "ticker": ticker,
        "current_price": round(current_price, 2),
        "intrinsic_value": round(float(intrinsic_value), 2),
        "margin_of_safety": round(float(margin_of_safety), 2),
        "wacc": round(wacc * 100, 2),
        "growth_rate": round(growth_rate_high * 100, 2),
        "terminal_growth": round(terminal_growth * 100, 2),
        "beta": round(beta, 2),
        "cash_flows": cash_flows,
        "signal": signal,
    }


def calculate_signal_score(ticker: str) -> dict[str, Any]:
    t = yf.Ticker(ticker)

    rsi = get_rsi(ticker)
    if rsi < 30:
        tech_score = 30
    elif rsi < 40:
        tech_score = 25
    elif rsi < 50:
        tech_score = 20
    elif rsi < 60:
        tech_score = 10
    else:
        tech_score = 0

    try:
        price = _safe_float(t.fast_info["last_price"], 0.0)
    except Exception:
        price = _safe_float(_download_close(ticker, "5d").iloc[-1], 0.0)

    hist = yf.download(ticker, period="1y", progress=False, auto_adjust=False)
    close = _close_series(hist, ticker)
    ma50 = _safe_float(close.tail(50).mean(), 0.0)
    ma200 = _safe_float(close.tail(200).mean(), 0.0)

    if price > ma50 and price > ma200:
        ma_score = 20
    elif price > ma200:
        ma_score = 10
    elif price > ma50:
        ma_score = 5
    else:
        ma_score = 0

    dcf = dcf_valuation(ticker)
    mos = _safe_float(dcf["margin_of_safety"], 0.0)
    if mos > 50:
        dcf_score = 30
    elif mos > 30:
        dcf_score = 25
    elif mos > 15:
        dcf_score = 20
    elif mos > 0:
        dcf_score = 10
    elif mos > -15:
        dcf_score = 5
    else:
        dcf_score = 0

    returns = close.pct_change()
    return_1m = _safe_float(returns.tail(21).sum() * 100, 0.0)
    return_3m = _safe_float(returns.tail(63).sum() * 100, 0.0)

    mom_score = 0
    if return_1m > 0:
        mom_score += 10
    if return_3m > 0:
        mom_score += 10

    info = t.info or {}
    div_yield = _safe_float(info.get("dividendYield"), 0.0)
    if div_yield > 0.04:
        div_score = 10
    elif div_yield > 0.02:
        div_score = 5
    elif div_yield > 0:
        div_score = 2
    else:
        div_score = 0

    total = tech_score + ma_score + dcf_score + mom_score + div_score

    # Tiebreaker for ranking among equal total scores: lower RSI first, then higher MoS.
    tie_break = (-round(rsi, 4), -round(mos, 4))

    signal = (
        "Strong Buy"
        if total >= 80
        else "Buy"
        if total >= 60
        else "Neutral"
        if total >= 40
        else "Caution"
        if total >= 20
        else "Avoid"
    )

    return {
        "ticker": ticker,
        "total_score": total,
        "technical_score": tech_score,
        "ma_score": ma_score,
        "dcf_score": dcf_score,
        "momentum_score": mom_score,
        "dividend_score": div_score,
        "max_score": 110,
        "tiebreak": tie_break,
        "rsi": round(rsi, 2),
        "return_1m_pct": round(return_1m, 2),
        "return_3m_pct": round(return_3m, 2),
        "dcf": dcf,
        "signal": signal,
    }


def calculate_allocation(scores: dict[str, Any], budget_thb: float) -> dict[str, dict[str, Any]]:
    strong_buy = {k: v for k, v in scores.items() if _safe_float(v.get("total_score"), 0.0) >= 60}
    buy = {k: v for k, v in scores.items() if 40 <= _safe_float(v.get("total_score"), 0.0) < 60}
    neutral = {k: v for k, v in scores.items() if 20 <= _safe_float(v.get("total_score"), 0.0) < 40}

    allocation: dict[str, dict[str, Any]] = {}

    if strong_buy:
        sb_budget = budget_thb * 0.6
        total_sb = sum(_safe_float(v.get("total_score"), 0.0) for v in strong_buy.values())
        if total_sb > 0:
            for ticker, data in strong_buy.items():
                pct = _safe_float(data.get("total_score"), 0.0) / total_sb
                amount = int(round(sb_budget * pct / 100)) * 100
                allocation[ticker] = {
                    "amount_thb": amount,
                    "percent": round(pct * 60),
                    "group": "Strong Buy",
                }

    if buy:
        b_budget = budget_thb * 0.3
        total_b = sum(_safe_float(v.get("total_score"), 0.0) for v in buy.values())
        if total_b > 0:
            for ticker, data in buy.items():
                pct = _safe_float(data.get("total_score"), 0.0) / total_b
                amount = int(round(b_budget * pct / 100)) * 100
                allocation[ticker] = {
                    "amount_thb": amount,
                    "percent": round(pct * 30),
                    "group": "Buy",
                }

    if neutral and (not strong_buy and not buy):
        n_budget = budget_thb
        total_n = sum(_safe_float(v.get("total_score"), 0.0) for v in neutral.values())
        if total_n > 0:
            for ticker, data in neutral.items():
                pct = _safe_float(data.get("total_score"), 0.0) / total_n
                amount = int(round(n_budget * pct / 100)) * 100
                allocation[ticker] = {
                    "amount_thb": amount,
                    "percent": round(pct * 100),
                    "group": "Neutral",
                }

    if not strong_buy and buy:
        b_budget = budget_thb * 0.9
        total_b = sum(_safe_float(v.get("total_score"), 0.0) for v in buy.values())
        if total_b > 0:
            for ticker, data in buy.items():
                pct = _safe_float(data.get("total_score"), 0.0) / total_b
                amount = int(round(b_budget * pct / 100)) * 100
                allocation[ticker] = {
                    "amount_thb": amount,
                    "percent": round(pct * 90),
                    "group": "Buy",
                }

    return allocation


def run_full_analysis(budget_thb: float = 5000) -> dict[str, Any]:
    tickers = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]

    results: dict[str, Any] = {}
    for ticker in tickers:
        print(f"Analyzing {ticker}...")
        results[ticker] = calculate_signal_score(ticker)
        time.sleep(1)

    allocation = calculate_allocation(results, budget_thb)

    return {
        "analysis": results,
        "allocation": allocation,
        "timestamp": datetime.now().isoformat(),
    }
