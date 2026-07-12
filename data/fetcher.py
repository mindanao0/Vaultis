# -*- coding: utf-8 -*-
"""โมดูลสำหรับดึงข้อมูล ETF ย้อนหลัง 10 ปีจาก yfinance.

นโยบายความล้มเหลว (AUDIT.md C1): ดึงไม่สำเร็จ = raise `PriceDataUnavailableError`
เสียงดังทันที — ห้ามคืน DataFrame ว่างเงียบ ๆ เพราะ downstream จะแปลงเป็น
สัญญาณ/ราคา/กำไรปลอมโดยผู้ใช้ไม่รู้ตัว
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import List

import pandas as pd
import yfinance as yf

from utils.config import get_tickers

logger = logging.getLogger(__name__)

DEFAULT_TICKERS: List[str] = get_tickers()


class PriceDataUnavailableError(RuntimeError):
    """ดึงข้อมูลราคาไม่สำเร็จหลัง retry ครบ — ผู้เรียกต้องแสดงข้อผิดพลาด ห้ามเดาค่าแทน."""


def fetch_adjusted_close_data(
    tickers: List[str] | None = None,
    years: int = 10,
    interval: str = "1d",
) -> pd.DataFrame:
    """ดึงข้อมูลราคาปิดแบบปรับแล้ว (Adjusted Close) ของ ETF หลายตัว.

    คืน DataFrame ที่มีข้อมูลเสมอ; ล้มเหลว → raise PriceDataUnavailableError
    """
    selected_tickers: List[str] = tickers or get_tickers()
    end_date = datetime.today()
    start_date = end_date - timedelta(days=365 * years)

    last_error: Exception | None = None
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
        except Exception as exc:
            last_error = exc
            logger.warning(
                "fetch_adjusted_close_data attempt %d/3 failed for %s: %s",
                attempt + 1,
                selected_tickers,
                exc,
            )
            if attempt < 2:
                time.sleep(2)

    raise PriceDataUnavailableError(
        f"ดึงข้อมูลราคา {selected_tickers} ไม่สำเร็จหลังลอง 3 ครั้ง: {last_error}"
    ) from last_error


if __name__ == "__main__":
    try:
        frame = fetch_adjusted_close_data()
        print(frame.tail())
    except Exception as error:
        print(f"เกิดข้อผิดพลาด: {error}")
