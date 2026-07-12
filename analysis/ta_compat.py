"""Compatibility layer for technical indicators.

ชั้นกลางตัวชี้วัดทางเทคนิคของทั้งโปรเจกต์ (แทน pandas-ta ที่ถูกถอดออก):
- ใช้ library `ta` เมื่อมี, มี fallback เป็น pandas ล้วนเสมอ
- ชื่อคอลัมน์ MACD/BBands ตามรูปแบบ pandas-ta เดิม (MACD_12_26_9, BBL_20_2.0, ...)
  เพื่อให้โค้ดเดิมที่เลือกคอลัมน์ด้วย prefix ใช้ต่อได้
- ค่า warmup ของทุกตัวชี้วัดเป็น NaN — ห้าม fill เป็น 0/100 (ดู AUDIT.md M1)
"""

from __future__ import annotations

import pandas as pd


def _macd_frame(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    """MACD มาตรฐาน (EMA fast - EMA slow) พร้อม signal/histogram แบบ pandas ล้วน."""
    close = pd.to_numeric(series, errors="coerce")
    ema_fast = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_slow = close.ewm(span=slow, adjust=False, min_periods=slow).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    hist = macd_line - signal_line
    return pd.DataFrame(
        {
            f"MACD_{fast}_{slow}_{signal}": macd_line,
            f"MACDs_{fast}_{slow}_{signal}": signal_line,
            f"MACDh_{fast}_{slow}_{signal}": hist,
        },
        index=series.index,
    )


def _bbands_frame(series: pd.Series, length: int = 20, std: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands (SMA ± std × rolling std)."""
    close = pd.to_numeric(series, errors="coerce")
    mid = close.rolling(window=length, min_periods=length).mean()
    dev = close.rolling(window=length, min_periods=length).std(ddof=0)
    upper = mid + std * dev
    lower = mid - std * dev
    suffix = f"{length}_{float(std)}"
    return pd.DataFrame(
        {
            f"BBL_{suffix}": lower,
            f"BBM_{suffix}": mid,
            f"BBU_{suffix}": upper,
        },
        index=series.index,
    )


def _rsi_fallback(series: pd.Series, length: int = 14) -> pd.Series:
    """RSI (Wilder smoothing) แบบ pandas ล้วน; ช่วง warmup เป็น NaN."""
    close = pd.to_numeric(series, errors="coerce")
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    # ช่วงที่ไม่มีแรงขายเลย (avg_loss == 0) RSI = 100 ตามนิยาม; warmup (NaN) คงเป็น NaN
    rsi = rsi.where(avg_loss.ne(0.0) | avg_loss.isna(), 100.0)
    return rsi.astype(float)


class _FallbackIndicatorAPI:
    """Fallback implementation (pandas ล้วน) เมื่อไม่มี library `ta`."""

    @staticmethod
    def sma(series: pd.Series, length: int = 14) -> pd.Series:
        return pd.to_numeric(series, errors="coerce").rolling(
            window=length, min_periods=length
        ).mean()

    @staticmethod
    def rsi(series: pd.Series, length: int = 14) -> pd.Series:
        return _rsi_fallback(series, length=length)

    @staticmethod
    def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
        return _macd_frame(series, fast=fast, slow=slow, signal=signal)

    @staticmethod
    def bbands(series: pd.Series, length: int = 20, std: float = 2.0) -> pd.DataFrame:
        return _bbands_frame(series, length=length, std=std)


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

        @staticmethod
        def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
            # ใช้สูตร pandas ล้วนตัวเดียวกับ fallback เพื่อให้ชื่อคอลัมน์/ค่าตรงกันทุกสภาพแวดล้อม
            return _macd_frame(series, fast=fast, slow=slow, signal=signal)

        @staticmethod
        def bbands(series: pd.Series, length: int = 20, std: float = 2.0) -> pd.DataFrame:
            return _bbands_frame(series, length=length, std=std)

    ta = _TaWrapper()
except Exception:
    ta = _FallbackIndicatorAPI()
