"""Compatibility layer for technical indicators using `ta` library."""

from __future__ import annotations

import pandas as pd


def _rsi_fallback(series: pd.Series, length: int = 14) -> pd.Series:
    """Calculate RSI using pandas-only operations."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(0.0)


class _FallbackIndicatorAPI:
    """Fallback implementation for required indicator APIs."""

    @staticmethod
    def sma(series: pd.Series, length: int = 14) -> pd.Series:
        return series.rolling(window=length, min_periods=length).mean()

    @staticmethod
    def rsi(series: pd.Series, length: int = 14) -> pd.Series:
        return _rsi_fallback(series, length=length)


try:
    import ta as ta_lib  # type: ignore

    class _TaWrapper:
        """Expose a pandas_ta-like surface used by this project."""

        @staticmethod
        def sma(series: pd.Series, length: int = 14) -> pd.Series:
            return ta_lib.trend.SMAIndicator(close=series, window=length, fillna=False).sma_indicator()

        @staticmethod
        def rsi(series: pd.Series, length: int = 14) -> pd.Series:
            return ta_lib.momentum.RSIIndicator(close=series, window=length, fillna=False).rsi()

    ta = _TaWrapper()
except Exception:
    ta = _FallbackIndicatorAPI()
