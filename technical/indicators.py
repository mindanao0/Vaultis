# -*- coding: utf-8 -*-
"""โมดูลคำนวณตัวชี้วัดทางเทคนิคสำหรับ ETF."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from utils.cache import cache_data_1h

from data.fetcher import fetch_adjusted_close_data


def _extract_adjusted_close(df: pd.DataFrame) -> pd.Series:
    """ดึง series ราคาจากคอลัมน์ Adjusted Close."""
    if df.empty:
        raise ValueError("DataFrame ว่าง ไม่สามารถคำนวณตัวชี้วัดได้")

    for col in ("Adj Close", "Adjusted Close"):
        if col in df.columns:
            return pd.to_numeric(df[col], errors="coerce")

    if df.shape[1] == 1:
        return pd.to_numeric(df.iloc[:, 0], errors="coerce")

    raise ValueError("ไม่พบคอลัมน์ Adjusted Close ใน DataFrame")


def calculate_ma(df: pd.DataFrame, periods: list[int] = [50, 200]) -> pd.DataFrame:
    """คำนวณ Moving Average จากราคา Adjusted Close ตามช่วงวันที่กำหนด."""
    try:
        price = _extract_adjusted_close(df)
        output = pd.DataFrame(index=df.index)
        output["Adj Close"] = price

        for period in periods:
            if period <= 0:
                raise ValueError("period ของ MA ต้องมากกว่า 0")
            output[f"MA{period}"] = price.rolling(window=period, min_periods=period).mean()

        return output
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการคำนวณ MA: {exc}") from exc


def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """คำนวณ RSI และจัด signal เป็น Oversold/Neutral/Overbought."""
    try:
        if period <= 0:
            raise ValueError("period ของ RSI ต้องมากกว่า 0")

        price = _extract_adjusted_close(df)
        delta = price.diff()
        gain = delta.clip(lower=0.0)
        loss = -delta.clip(upper=0.0)

        avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

        rs = avg_gain / avg_loss.replace(0, pd.NA)
        rsi = 100 - (100 / (1 + rs))
        rsi = rsi.fillna(100).where(avg_loss.ne(0), 100)

        output = pd.DataFrame(index=df.index)
        output["Adj Close"] = price
        output["RSI"] = rsi
        output["Signal"] = "Neutral"
        output.loc[output["RSI"] < 30, "Signal"] = "Oversold"
        output.loc[output["RSI"] > 70, "Signal"] = "Overbought"
        return output
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการคำนวณ RSI: {exc}") from exc


@cache_data_1h
def get_signals(ticker: str) -> dict[str, Any]:
    """สรุปสถานะ MA และ RSI ของ ticker ปัจจุบัน."""
    try:
        prices = fetch_adjusted_close_data([ticker], years=10)
        if prices.empty or ticker not in prices.columns:
            raise ValueError(f"ไม่พบข้อมูลราคาของ {ticker}")

        ticker_df = prices[[ticker]].rename(columns={ticker: "Adj Close"})
        ma_df = calculate_ma(ticker_df, periods=[50, 200])
        rsi_df = calculate_rsi(ticker_df, period=14)

        latest_ma = ma_df.dropna(subset=["MA50", "MA200"]).iloc[-1]
        latest_rsi = rsi_df.dropna(subset=["RSI"]).iloc[-1]

        ma_zone = "Bullish" if latest_ma["MA50"] > latest_ma["MA200"] else "Bearish"
        price_zone = "Above MA200" if latest_ma["Adj Close"] >= latest_ma["MA200"] else "Below MA200"
        rsi_zone = str(latest_rsi["Signal"])
        zone = f"{ma_zone} | {price_zone} | RSI: {rsi_zone}"

        return {
            "ticker": ticker,
            "price": float(latest_ma["Adj Close"]),
            "ma50": float(latest_ma["MA50"]),
            "ma200": float(latest_ma["MA200"]),
            "rsi14": float(latest_rsi["RSI"]),
            "rsi_signal": rsi_zone,
            "zone": zone,
        }
    except Exception:
        st.warning("ไม่สามารถดึงข้อมูลได้ กรุณารอสักครู่")
        return {}
