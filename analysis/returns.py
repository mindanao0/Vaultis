# -*- coding: utf-8 -*-
"""โมดูลคำนวณผลตอบแทนหลายช่วงเวลา."""

from __future__ import annotations

from typing import Dict

import pandas as pd

from utils.cache import cache_data_1h


def monthly_seasonality(closes: pd.Series) -> pd.DataFrame:
    """สถิติผลตอบแทนรายเดือนแยกตามเดือนปฏิทิน (Roadmap B5 — เชิงบรรยายเท่านั้น).

    ห้ามนำไป override คะแนน/การจัดสรร — ข้อมูล ~10 ปีให้ตัวอย่างต่อเดือนแค่ ~10 ค่า
    (noise สูง) ใช้เล่าเรื่อง "เดือนไหนในอดีตมักอ่อน/แข็ง" ประกอบการอ่านกราฟ

    คืน DataFrame index = เดือน 1-12: ``median_pct``, ``mean_pct``,
    ``positive_rate_pct`` (% ของปีที่เดือนนั้นบวก), ``n_samples``
    เดือนที่ไม่มีตัวอย่างเลยคงเป็น NaN — ไม่เติม 0
    """
    closes = pd.to_numeric(closes, errors="coerce").dropna()
    if closes.empty:
        raise ValueError("ไม่มีข้อมูลราคา ไม่สามารถคำนวณ seasonality ได้")
    monthly_returns = closes.resample("ME").last().pct_change().dropna()
    if monthly_returns.empty:
        raise ValueError("ข้อมูลสั้นเกินกว่าจะได้ผลตอบแทนรายเดือนแม้แต่ค่าเดียว")

    grouped = monthly_returns.groupby(monthly_returns.index.month)
    stats = pd.DataFrame(
        {
            "median_pct": grouped.median() * 100.0,
            "mean_pct": grouped.mean() * 100.0,
            "positive_rate_pct": grouped.apply(lambda s: float((s > 0).mean()) * 100.0),
            "n_samples": grouped.size(),
        }
    )
    return stats.reindex(range(1, 13))


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
