from __future__ import annotations

import asyncio
from typing import Any

import pandas as pd
import pandas_ta as pta  # noqa: F401 — registers `df.ta` accessor
import yfinance as yf

from ..models.etf_models import TechnicalIndicators


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.droplevel(-1)
    return out


def _scalar_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        x = float(val)
    except (TypeError, ValueError):
        return None
    if pd.isna(x):
        return None
    return x


def _scalar_float_required(val: Any) -> float:
    x = _scalar_float(val)
    return 0.0 if x is None else x


def _cross_flags_last_n(ma50: pd.Series, ma200: pd.Series, n: int = 5) -> tuple[bool, bool]:
    golden = False
    death = False
    if len(ma50) < 2 or len(ma200) < 2:
        return golden, death
    start = max(1, len(ma50) - n)
    for i in range(start, len(ma50)):
        p50_prev, p50_curr = ma50.iloc[i - 1], ma50.iloc[i]
        p200_prev, p200_curr = ma200.iloc[i - 1], ma200.iloc[i]
        if any(pd.isna(v) for v in (p50_prev, p50_curr, p200_prev, p200_curr)):
            continue
        if p50_curr > p200_curr and p50_prev <= p200_prev:
            golden = True
        if p50_curr < p200_curr and p50_prev >= p200_prev:
            death = True
    return golden, death


def _signal(price: float, rsi: float | None, ma50: float | None, ma200: float | None) -> str:
    if pd.notna(ma200) and ma200 is not None and price < ma200:
        return "bearish"
    if rsi is not None and pd.notna(rsi) and rsi < 35:
        return "bearish"
    if (
        rsi is not None
        and pd.notna(rsi)
        and 40 <= rsi <= 70
        and ma50 is not None
        and pd.notna(ma50)
        and ma200 is not None
        and pd.notna(ma200)
        and price > ma50
        and price > ma200
    ):
        return "bullish"
    return "neutral"


def _pick_col(df: pd.DataFrame, prefix: str) -> str | None:
    matches = [c for c in df.columns if str(c).startswith(prefix)]
    return str(matches[-1]) if matches else None


def _pick_macd_line_col(df: pd.DataFrame) -> str | None:
    matches = [
        c
        for c in df.columns
        if str(c).startswith("MACD_") and not str(c).startswith(("MACDs_", "MACDh_"))
    ]
    return str(matches[-1]) if matches else None


class TechnicalService:
    async def get_technical(self, symbol: str) -> TechnicalIndicators:
        sym = symbol.strip().upper()

        def _compute() -> TechnicalIndicators:
            df = yf.download(sym, period="1y", interval="1d", progress=False, auto_adjust=False)
            df = _normalize_ohlcv(df)
            if df.empty or "Close" not in df.columns:
                raise ValueError("no OHLCV")

            work = df.copy()
            close = work["Close"].astype(float)
            vol = work["Volume"].astype(float) if "Volume" in work.columns else pd.Series(index=work.index, dtype=float)

            ma50_s = close.rolling(50).mean()
            ma200_s = close.rolling(200).mean()
            vol_ma20_s = vol.rolling(20).mean()

            work.ta.rsi(length=14, append=True)
            work.ta.macd(fast=12, slow=26, signal=9, append=True)
            work.ta.bbands(length=20, std=2, append=True)

            last = work.iloc[-1]
            price = _scalar_float_required(last["Close"])

            rsi_col = _pick_col(work, "RSI_")
            macd_col = _pick_macd_line_col(work)
            macds_col = _pick_col(work, "MACDs_")
            macdh_col = _pick_col(work, "MACDh_")
            bbl_col = _pick_col(work, "BBL_")
            bbm_col = _pick_col(work, "BBM_")
            bbu_col = _pick_col(work, "BBU_")

            rsi = _scalar_float(last[rsi_col]) if rsi_col else None
            macd = _scalar_float(last[macd_col]) if macd_col else None
            macd_signal = _scalar_float(last[macds_col]) if macds_col else None
            macd_hist = _scalar_float(last[macdh_col]) if macdh_col else None

            bb_upper = _scalar_float(last[bbu_col]) if bbu_col else None
            bb_middle = _scalar_float(last[bbm_col]) if bbm_col else None
            bb_lower = _scalar_float(last[bbl_col]) if bbl_col else None

            ma50_v = _scalar_float(ma50_s.iloc[-1])
            ma200_v = _scalar_float(ma200_s.iloc[-1])
            volume_ma20 = _scalar_float(vol_ma20_s.iloc[-1])

            golden_cross, death_cross = _cross_flags_last_n(ma50_s, ma200_s, n=5)
            sig = _signal(price, rsi, ma50_v, ma200_v)

            return TechnicalIndicators(
                symbol=sym,
                price=price,
                rsi=rsi,
                macd=macd,
                macd_signal=macd_signal,
                macd_hist=macd_hist,
                bb_upper=bb_upper,
                bb_middle=bb_middle,
                bb_lower=bb_lower,
                ma50=ma50_v,
                ma200=ma200_v,
                volume_ma20=volume_ma20,
                golden_cross=golden_cross,
                death_cross=death_cross,
                signal=sig,
            )

        try:
            return await asyncio.to_thread(_compute)
        except Exception:
            return TechnicalIndicators(symbol=sym, price=0.0, signal="neutral")
