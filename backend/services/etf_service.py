from __future__ import annotations

import pandas as pd

from alerts.price_alert import get_current_prices
from analysis.correlation import calculate_correlation_matrix
from analysis.returns import calculate_period_returns
from analysis.risk import calculate_risk_metrics
from analysis.ta_compat import ta
from data.fetcher import fetch_adjusted_close_data
from utils.config import get_tickers


def _prices_df() -> pd.DataFrame:
    tickers = get_tickers()
    return fetch_adjusted_close_data(tickers=tickers, years=10).ffill()


def get_etf_prices() -> dict[str, float]:
    tickers = get_tickers()
    return get_current_prices(tickers)


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


def get_etf_technical() -> dict[str, dict[str, float | str]]:
    prices = _prices_df()
    signals: dict[str, dict[str, float | str]] = {}
    for ticker in prices.columns:
        s = prices[ticker].dropna()
        if len(s) < 200:
            continue
        price = float(s.iloc[-1])
        ma50 = float(ta.sma(s, length=50).iloc[-1])
        ma200 = float(ta.sma(s, length=200).iloc[-1])
        rsi = float(ta.rsi(s, length=14).iloc[-1])
        signals[ticker] = {
            "price": price,
            "ma50": ma50,
            "ma200": ma200,
            "rsi14": rsi,
            "ma50_state": "Above" if price >= ma50 else "Below",
            "ma200_state": "Above" if price >= ma200 else "Below",
            "signal": "Buy Zone" if (price >= ma50 and price >= ma200 and rsi <= 70) else "Neutral",
        }
    return signals
