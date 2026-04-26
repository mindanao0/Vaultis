"""โมดูลสำหรับดึงข้อมูล ETF ย้อนหลัง 10 ปีจาก yfinance."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List

import pandas as pd
import yfinance as yf


DEFAULT_TICKERS: List[str] = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]


def fetch_adjusted_close_data(
    tickers: List[str] | None = None,
    years: int = 10,
    interval: str = "1d",
) -> pd.DataFrame:
    """ดึงข้อมูลราคาปิดแบบปรับแล้ว (Adjusted Close) ของ ETF หลายตัว."""
    selected_tickers: List[str] = tickers or DEFAULT_TICKERS
    end_date = datetime.today()
    start_date = end_date - timedelta(days=365 * years)

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
        return cleaned
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดระหว่างดึงข้อมูล ETF: {exc}") from exc


if __name__ == "__main__":
    try:
        frame = fetch_adjusted_close_data()
        print(frame.tail())
    except Exception as error:
        print(f"เกิดข้อผิดพลาด: {error}")
