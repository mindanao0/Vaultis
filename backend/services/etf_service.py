from __future__ import annotations

import pandas as pd

from alerts.price_alert import get_current_prices
from analysis.correlation import calculate_correlation_matrix
from analysis.returns import calculate_period_returns
from analysis.risk import calculate_risk_metrics
from analysis.ta_compat import ta
from data.fetcher import fetch_adjusted_close_data
from technical import signal_rules
from utils.config import get_tickers


def _prices_df() -> pd.DataFrame:
    tickers = get_tickers()
    return fetch_adjusted_close_data(tickers=tickers, years=10).ffill()


def get_etf_prices() -> dict[str, float]:
    tickers = get_tickers()
    return get_current_prices(tickers)


def get_etf_daily_eod_snapshot() -> dict[str, dict[str, float | str]]:
    """Latest vs prior trading row from adjusted-close history (EOD)."""
    prices = _prices_df()
    if prices.empty or len(prices) < 2:
        return {}
    latest = prices.iloc[-1]
    prior = prices.iloc[-2]
    bar_date = pd.Timestamp(prices.index[-1]).strftime("%d/%m/%Y")
    out: dict[str, dict[str, float | str]] = {}
    for ticker in prices.columns:
        key = str(ticker).strip().upper()
        try:
            p_t = float(latest[ticker])
            p_y = float(prior[ticker])
            if p_y > 0:
                chg = (p_t - p_y) / p_y * 100.0
            else:
                chg = 0.0
            out[key] = {
                "price": round(p_t, 2),
                "previous_close": round(p_y, 2),
                "change_pct": round(chg, 4),
                "date": bar_date,
            }
        except (KeyError, TypeError, ValueError):
            continue
    return out


def get_etf_returns() -> dict:
    prices = _prices_df()
    result = calculate_period_returns(prices)
    return result.to_dict()


def get_etf_risk() -> dict:
    prices = _prices_df()
    result = calculate_risk_metrics(prices)
    return result.to_dict()


def get_etf_correlation() -> dict:
    prices = _prices_df()
    result = calculate_correlation_matrix(prices)
    return result.to_dict()


def get_etf_technical() -> dict[str, dict[str, float | str | bool]]:
    """สัญญาณเทคนิครายตัว — ใช้นิยามกลางจาก technical/signal_rules.py (AUDIT.md C2).

    ticker ที่ข้อมูลไม่พอจะมี ``data_ok: False`` และ ``signal: "no_data"``
    ไม่ถูกซ่อนหายไปเฉย ๆ เหมือนเดิม (AUDIT.md C1)
    """
    prices = _prices_df()
    signals: dict[str, dict[str, float | str | bool]] = {}
    for ticker in prices.columns:
        s = prices[ticker].dropna()
        if len(s) < 200:
            signals[ticker] = {
                "data_ok": False,
                "signal": signal_rules.NO_DATA,
                "reason": "ข้อมูลราคาน้อยกว่า 200 วันเทรด",
            }
            continue
        price = float(s.iloc[-1])
        ma50 = float(ta.sma(s, length=50).iloc[-1])
        ma200 = float(ta.sma(s, length=200).iloc[-1])
        rsi = float(ta.rsi(s, length=14).iloc[-1])
        central = signal_rules.dca_signal(price, ma50, ma200, rsi)
        if central == signal_rules.NO_DATA:
            signals[ticker] = {
                "data_ok": False,
                "signal": signal_rules.NO_DATA,
                "reason": "คำนวณตัวชี้วัดไม่ได้",
            }
            continue
        signals[ticker] = {
            "data_ok": True,
            "price": price,
            "ma50": ma50,
            "ma200": ma200,
            "rsi14": rsi,
            "ma50_state": "Above" if price >= ma50 else "Below",
            "ma200_state": "Above" if price >= ma200 else "Below",
            "signal": central,
            "signal_th": signal_rules.thai_description(central),
        }
    return signals
