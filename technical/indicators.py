# -*- coding: utf-8 -*-
"""โมดูลคำนวณตัวชี้วัดทางเทคนิคสำหรับ ETF."""

from __future__ import annotations

from typing import Any

import pandas as pd

from utils.cache import cache_data_1h

from data.fetcher import fetch_adjusted_close_data
from technical import signal_rules


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
        # ไม่มีแรงขายเลย (avg_loss == 0) → RSI = 100 ตามนิยาม
        # ช่วง warmup ต้องคงเป็น NaN — ห้าม fill 100 (บั๊กเดิม: กราฟช่วงแรกกลายเป็น Overbought ปลอม)
        rsi = rsi.where(avg_loss.ne(0) | avg_loss.isna(), 100.0)

        output = pd.DataFrame(index=df.index)
        output["Adj Close"] = price
        output["RSI"] = rsi
        output["Signal"] = "Neutral"
        output.loc[output["RSI"] < 30, "Signal"] = "Oversold"
        output.loc[output["RSI"] > 70, "Signal"] = "Overbought"
        return output
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการคำนวณ RSI: {exc}") from exc


def ma_cross_dates(ma_fast: pd.Series, ma_slow: pd.Series) -> dict[str, list[pd.Timestamp]]:
    """หาวันที่เกิด golden/death cross ทั้งหมดจากอนุกรม MA สองเส้น (Roadmap A1).

    CrossoverDetector ของ screener ตอบได้แค่ "เพิ่ง cross ในไม่กี่วันล่าสุด"
    แต่กราฟวาดเหตุผลต้องการตำแหน่ง cross ทุกจุดย้อนหลัง จึงสกัดจาก MA series ตรง ๆ

    ช่วง warm-up ที่ MA ยังเป็น NaN ถูกตัดทิ้ง — ไม่ตีความเป็น cross (AUDIT.md C1)
    คืน ``{"golden": [วันที่ fast ตัดขึ้น], "death": [วันที่ fast ตัดลง]}``
    """
    aligned = pd.concat([ma_fast.rename("fast"), ma_slow.rename("slow")], axis=1).dropna()
    if len(aligned) < 2:
        return {"golden": [], "death": []}
    above = aligned["fast"] > aligned["slow"]
    flipped = above.ne(above.shift(1))
    flipped.iloc[0] = False  # จุดแรกไม่มีอดีตให้เทียบ — ไม่นับเป็น cross
    return {
        "golden": list(aligned.index[flipped & above]),
        "death": list(aligned.index[flipped & ~above]),
    }


WEEKLY_MA_FAST = 10   # MA10w ≈ MA50d บนแท่งสัปดาห์
WEEKLY_MA_SLOW = 40   # MA40w ≈ MA200d บนแท่งสัปดาห์


def weekly_dca_signal(daily_closes: pd.Series) -> dict[str, Any]:
    """สัญญาณ DCA บนแท่งรายสัปดาห์ (Roadmap B3): RSI14 weekly + MA10w/MA40w.

    MA10w/MA40w คือคู่เทียบเท่า MA50d/MA200d — ห้ามใช้ MA50/MA200 บนแท่ง week
    ตรง ๆ (MA200w = ค่าเฉลี่ย ~4 ปี และ QQQM ประวัติไม่พอ)
    ใช้เป็น "ชั้นความมั่นใจ" เทียบกับสัญญาณรายวัน — ไม่ใช่สัญญาณใหม่ ไม่เข้าเลขคะแนน

    ข้อมูลไม่พอ (สัปดาห์ < MA ยาวสุด + 1) → ``signal = NO_DATA`` ไม่เดา (AUDIT.md C1)
    """
    no_data = {
        "signal": signal_rules.NO_DATA,
        "price": None,
        "ma10w": None,
        "ma40w": None,
        "rsi14w": None,
    }
    closes = pd.to_numeric(daily_closes, errors="coerce").dropna()
    if closes.empty:
        return no_data
    weekly = closes.resample("W-FRI").last().dropna()
    if len(weekly) < WEEKLY_MA_SLOW + 1:
        return no_data

    ma_fast = weekly.rolling(WEEKLY_MA_FAST, min_periods=WEEKLY_MA_FAST).mean().iloc[-1]
    ma_slow = weekly.rolling(WEEKLY_MA_SLOW, min_periods=WEEKLY_MA_SLOW).mean().iloc[-1]
    rsi_series = calculate_rsi(weekly.to_frame("Adj Close"), period=14)["RSI"].dropna()
    if rsi_series.empty or pd.isna(ma_fast) or pd.isna(ma_slow):
        return no_data

    price = float(weekly.iloc[-1])
    rsi_weekly = float(rsi_series.iloc[-1])
    return {
        "signal": signal_rules.dca_signal(price, float(ma_fast), float(ma_slow), rsi_weekly),
        "price": price,
        "ma10w": float(ma_fast),
        "ma40w": float(ma_slow),
        "rsi14w": rsi_weekly,
    }


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
    except Exception as exc:
        # ห้ามคืน {} เงียบ ๆ — ข้อมูลพังต้องเสียงดังถึงผู้เรียก (AUDIT.md C1)
        raise RuntimeError(f"ดึงสัญญาณเทคนิคของ {ticker} ไม่สำเร็จ: {exc}") from exc
