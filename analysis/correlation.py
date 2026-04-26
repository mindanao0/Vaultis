"""โมดูลคำนวณ Correlation Matrix ระหว่าง ETF."""

from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from utils.cache import cache_data_1h
import yfinance as yf

TICKERS: list[str] = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]


def _extract_adj_close(raw_data: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """แปลงข้อมูลดิบจาก yfinance ให้เหลือราคาปิดแบบปรับแล้วของแต่ละ ETF."""
    if raw_data.empty:
        raise ValueError("ไม่พบข้อมูลราคาจาก yfinance")

    if isinstance(raw_data.columns, pd.MultiIndex):
        price_df = raw_data.xs("Adj Close", axis=1, level=1)
    else:
        if "Adj Close" not in raw_data.columns:
            raise ValueError("ไม่พบคอลัมน์ Adj Close ในข้อมูลที่ดึงมา")
        price_df = raw_data[["Adj Close"]].rename(columns={"Adj Close": tickers[0]})

    cleaned = price_df.reindex(columns=tickers).dropna(how="all").sort_index()
    if cleaned.empty:
        raise ValueError("ข้อมูลราคาหลังทำความสะอาดว่างเปล่า")
    return cleaned


def calculate_correlation(period: str = "10y") -> pd.DataFrame:
    """ดึงข้อมูล ETF 5 ตัวและคำนวณ Correlation Matrix จากผลตอบแทนรายวัน."""
    try:
        raw_data = yf.download(
            tickers=TICKERS,
            period=period,
            interval="1d",
            auto_adjust=False,
            progress=False,
            group_by="ticker",
        )
        prices = _extract_adj_close(raw_data, TICKERS)
        daily_returns = prices.pct_change().dropna(how="all")
        if daily_returns.empty:
            raise ValueError("ผลตอบแทนรายวันว่าง ไม่สามารถคำนวณ Correlation ได้")
        return daily_returns.corr()
    except Exception:
        st.warning("ไม่สามารถดึงข้อมูลได้ กรุณารอสักครู่")
        return pd.DataFrame()


def get_correlation_insight(corr_matrix: pd.DataFrame) -> str:
    """สรุปคู่ ETF ที่มีความสัมพันธ์สูงสุดและต่ำสุดเป็นข้อความภาษาไทย."""
    try:
        if corr_matrix.empty:
            raise ValueError("corr_matrix ว่าง")
        if corr_matrix.shape[0] < 2:
            raise ValueError("corr_matrix ต้องมีอย่างน้อย 2 ETF")

        lower_triangle_mask = np.tril(np.ones(corr_matrix.shape, dtype=bool), k=0)
        corr_pairs = corr_matrix.where(~lower_triangle_mask).stack()
        if corr_pairs.empty:
            raise ValueError("ไม่พบคู่ข้อมูลเพียงพอสำหรับวิเคราะห์ Correlation")

        highest_pair = corr_pairs.idxmax()
        lowest_pair = corr_pairs.idxmin()
        highest_value = float(corr_pairs.max())
        lowest_value = float(corr_pairs.min())

        return (
            f"คู่ที่มีความสัมพันธ์สูงสุดคือ {highest_pair[0]} กับ {highest_pair[1]} "
            f"(Correlation = {highest_value:.2f}) "
            f"ส่วนคู่ที่มีความสัมพันธ์ต่ำสุดคือ {lowest_pair[0]} กับ {lowest_pair[1]} "
            f"(Correlation = {lowest_value:.2f})"
        )
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการสรุป Correlation Insight: {exc}") from exc


@cache_data_1h
def calculate_correlation_matrix(price_df: pd.DataFrame) -> pd.DataFrame:
    """คำนวณเมทริกซ์ความสัมพันธ์จากผลตอบแทนรายวัน."""
    try:
        if price_df.empty:
            raise ValueError("price_df ว่าง ไม่สามารถคำนวณ Correlation ได้")
        daily_returns = price_df.sort_index().pct_change().dropna(how="all")
        corr = daily_returns.corr()
        return corr
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการคำนวณ Correlation Matrix: {exc}") from exc
