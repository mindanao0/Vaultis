"""โมดูลคำนวณผลตอบแทนหลายช่วงเวลา."""

from __future__ import annotations

from typing import Dict

import pandas as pd

from utils.cache import cache_data_1h


RETURN_WINDOWS: Dict[str, int] = {
    "1M": 21,
    "3M": 63,
    "6M": 126,
    "1Y": 252,
    "3Y": 756,
    "5Y": 1260,
    "10Y": 2520,
}


@cache_data_1h
def calculate_period_returns(price_df: pd.DataFrame) -> pd.DataFrame:
    """คำนวณผลตอบแทนย้อนหลังตามช่วงเวลาที่กำหนดให้ ETF แต่ละตัว."""
    try:
        if price_df.empty:
            raise ValueError("price_df ว่าง ไม่สามารถคำนวณผลตอบแทนได้")

        results: dict[str, dict[str, float]] = {}
        latest = price_df.ffill().iloc[-1]

        for period, window in RETURN_WINDOWS.items():
            if len(price_df) <= window:
                period_return = pd.Series(index=price_df.columns, dtype=float)
            else:
                base = price_df.ffill().iloc[-window - 1]
                period_return = (latest / base - 1.0) * 100.0
            results[period] = period_return.to_dict()

        returns_df = pd.DataFrame(results).T
        returns_df.index.name = "Period"
        return returns_df
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการคำนวณผลตอบแทน: {exc}") from exc
