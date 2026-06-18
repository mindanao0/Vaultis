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


# --- Lightweight multi-ticker scores for advisor / jobs pipeline ---

DEFAULT_ETF_TICKERS = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]

_DCF_SCORE_PLACEHOLDER = 15


def _yf_close_series(ticker: str) -> pd.Series:
    """ดึงราคาปิดรายวันย้อนหลัง ~1 ปี; ล้มเหลวคืน series ว่าง."""
    try:
        df = yf.download(
            tickers=ticker,
            period="1y",
            interval="1d",
            progress=False,
            auto_adjust=False,
        )
        if df.empty or "Close" not in df.columns:
            return pd.Series(dtype=float)
        close_data = df["Close"]
        if isinstance(close_data, pd.DataFrame):
            if close_data.empty:
                return pd.Series(dtype=float)
            close_series = close_data.iloc[:, 0]
        else:
            close_series = close_data
        return close_series.dropna().sort_index()
    except Exception:
        return pd.Series(dtype=float)


def _pipeline_ma_score(price: float, ma50: float, ma200: float) -> int:
    if price > ma200:
        return 20
    if price > ma50:
        return 10
    return 0


def _pipeline_technical_score(rsi: float) -> int:
    if 40 <= rsi <= 60:
        return 30
    if (30 <= rsi < 40) or (60 < rsi <= 70):
        return 15
    return 0


def _pipeline_momentum_score_one_month(close: pd.Series) -> int:
    if len(close) < 22:
        return 0
    last = float(close.iloc[-1])
    prev = float(close.iloc[-22])
    if prev == 0:
        return 0
    ret_pct = (last / prev - 1.0) * 100.0
    if ret_pct > 2.0:
        return 20
    if ret_pct > 0.0:
        return 10
    return 0


def _pipeline_signal_label(total_score: int) -> str:
    if total_score >= 60:
        return "Strong Buy"
    if total_score >= 40:
        return "Buy"
    if total_score >= 20:
        return "Neutral"
    return "Avoid"


def _pipeline_score_ticker(ticker: str) -> dict[str, Any]:
    closes = _yf_close_series(ticker).ffill()
    if len(closes) < 200:
        return {
            "ticker": ticker,
            "price": None,
            "ma50": None,
            "ma200": None,
            "rsi": None,
            "total_score": 0,
            "signal": "Avoid",
        }

    price = float(closes.iloc[-1])
    ma50_series = ta.sma(closes, length=50)
    ma200_series = ta.sma(closes, length=200)
    rsi_series = ta.rsi(closes, length=14)

    ma50 = float(ma50_series.iloc[-1])
    ma200 = float(ma200_series.iloc[-1])
    rsi = float(rsi_series.iloc[-1])
    if pd.isna(ma50) or pd.isna(ma200) or pd.isna(rsi):
        return {
            "ticker": ticker,
            "price": round(price, 2) if not pd.isna(price) else None,
            "ma50": None,
            "ma200": None,
            "rsi": None,
            "total_score": 0,
            "signal": "Avoid",
        }

    ma_s = _pipeline_ma_score(price, ma50, ma200)
    tech_s = _pipeline_technical_score(rsi)
    mom_s = _pipeline_momentum_score_one_month(closes)
    dcf_s = _DCF_SCORE_PLACEHOLDER
    total = int(tech_s + ma_s + dcf_s + mom_s)

    return {
        "ticker": ticker,
        "price": round(price, 2),
        "ma50": round(ma50, 2),
        "ma200": round(ma200, 2),
        "rsi": round(rsi, 2),
        "total_score": total,
        "signal": _pipeline_signal_label(total),
    }


def build_etf_scores(tickers: list[str] | None = None) -> list[dict[str, Any]]:
    """คำนวณคะแนนและสัญญาณต่อ ETF จาก yfinance + ตัวชี้วัด ``ta``.

    ถ้า ``tickers`` เป็น ``None`` ใช้ค่าเริ่มต้น
    ``VOO``, ``SCHD``, ``QQQM``, ``XLV``, ``GLDM``.
    """
    symbols = DEFAULT_ETF_TICKERS if tickers is None else list(tickers)
    return [_pipeline_score_ticker(sym.strip().upper()) for sym in symbols if sym.strip()]
