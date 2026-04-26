"""โมดูลสำหรับดึงข้อมูล ETF ย้อนหลัง 10 ปีจาก yfinance."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import List

import pandas as pd
import streamlit as st
import yfinance as yf

from utils.cache import cache_data_1h
from utils.config import get_tickers


DEFAULT_TICKERS: List[str] = get_tickers()


@cache_data_1h
def fetch_adjusted_close_data(
    tickers: List[str] | None = None,
    years: int = 10,
    interval: str = "1d",
) -> pd.DataFrame:
    """ดึงข้อมูลราคาปิดแบบปรับแล้ว (Adjusted Close) ของ ETF หลายตัว."""
    selected_tickers: List[str] = tickers or get_tickers()
    end_date = datetime.today()
    start_date = end_date - timedelta(days=365 * years)

    for attempt in range(3):
        try:
            raw_data = yf.download(
                tickers=selected_tickers,
                start=start_date.strftime("%Y-%m-%d"),
                end=end_date.strftime("%Y-%m-%d"),
                interval=interval,
                auto_adjust=False,
                progress=False,
                group_by="ticker",
            )

            if raw_data.empty:
                raise ValueError("ไม่พบข้อมูลราคา ETF จาก yfinance")

            # รองรับทั้งกรณี ticker เดียวและหลาย ticker
            if isinstance(raw_data.columns, pd.MultiIndex):
                adj_close = raw_data.xs("Adj Close", axis=1, level=1)
            else:
                if "Adj Close" not in raw_data.columns:
                    raise ValueError("ไม่พบคอลัมน์ Adj Close ในข้อมูลที่ดึงมา")
                adj_close = raw_data[["Adj Close"]].rename(columns={"Adj Close": selected_tickers[0]})

            cleaned = adj_close.dropna(how="all").sort_index()
            if cleaned.empty:
                raise ValueError("ข้อมูลราคาหลังทำความสะอาดว่างเปล่า")
            return cleaned
        except Exception:
            if attempt < 2:
                st.warning("ไม่สามารถดึงข้อมูลได้ กรุณารอสักครู่")
                time.sleep(2)
                continue
            st.warning("ไม่สามารถดึงข้อมูลได้ กรุณารอสักครู่")
            return pd.DataFrame()

    return pd.DataFrame()


if __name__ == "__main__":
    try:
        frame = fetch_adjusted_close_data()
        print(frame.tail())
    except Exception as error:
        print(f"เกิดข้อผิดพลาด: {error}")
