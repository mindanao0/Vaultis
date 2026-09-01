# -*- coding: utf-8 -*-
"""โมดูลคำนวณ Correlation Matrix ระหว่าง ETF."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import yfinance as yf

from utils.cache import cache_data_1h
from utils.config import get_tickers

logger = logging.getLogger(__name__)

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
    """ดึงข้อมูล ETF และคำนวณ Correlation Matrix จากผลตอบแทนรายวัน.

    ล้มเหลว → raise (ห้ามคืน DataFrame ว่างเงียบ ๆ — AUDIT.md C1)
    """
    tickers = get_tickers()
    raw_data = yf.download(
        tickers=tickers,
        period=period,
        interval="1d",
        auto_adjust=False,
        progress=False,
        group_by="ticker",
    )
    prices = _extract_adj_close(raw_data, tickers)
    # fill_method=None: ห้าม ffill ราคาก่อนคำนวณ (B11 — เหตุผลเดียวกับ risk.py)
    daily_returns = prices.pct_change(fill_method=None).dropna(how="all")
    if daily_returns.empty:
        raise ValueError("ผลตอบแทนรายวันว่าง ไม่สามารถคำนวณ Correlation ได้")
    return daily_returns.corr()


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
    """คำนวณเมทริกซ์ความสัมพันธ์จากผลตอบแทนรายวัน.

    ``fill_method=None`` บังคับไว้ (B11) — ถ้า ffill วันที่ไม่มีแท่งจะกลายเป็นผลตอบแทน
    0.00% ซึ่งบิดค่าความสัมพันธ์เข้าหา 0 เทียม ๆ วันที่ไม่มีข้อมูลต้องเป็น ``NaN``
    แล้วให้ ``corr()`` ตัดคู่นั้นทิ้งเอง (pairwise) ตามจริง
    """
    try:
        if price_df.empty:
            raise ValueError("price_df ว่าง ไม่สามารถคำนวณ Correlation ได้")
        daily_returns = price_df.sort_index().pct_change(fill_method=None).dropna(how="all")
        corr = daily_returns.corr()
        return corr
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการคำนวณ Correlation Matrix: {exc}") from exc


#: หน้าต่างของ rolling correlation — 252 วันทำการ ≈ 1 ปี
ROLLING_WINDOW_DAYS = 252


def rolling_correlation_summary(
    price_df: pd.DataFrame, base: str, window: int = ROLLING_WINDOW_DAYS
) -> pd.DataFrame:
    """สรุป correlation แบบ **เลื่อนหน้าต่าง** ของทุกกองเทียบ ``base``.

    ทำไมค่าเดียวไม่พอ (FIX_PLAN เฟส 4③): ตัวเลข correlation ค่าเดียวของทั้งช่วงซ่อน
    ความจริงที่สำคัญที่สุดไว้ — **ตัวที่ควรกระจายความเสี่ยงมักหยุดกระจายพอดีตอนที่
    ต้องการมันที่สุด** วัดตอนตรวจ: SCHD เทียบ VOO แสดงค่าเดียว 0.76 ทั้งที่ rolling
    1 ปีเคยขึ้นถึง **0.94** (ตอนตลาดพัง ทุกอย่างวิ่งไปทางเดียวกัน) และตอนนี้อยู่ที่ 0.29
    ⇒ เลขที่โชว์ไม่ตรงกับทั้งสถานะปัจจุบันและกรณีเลวร้าย · เกณฑ์เตือน ``>= 0.85`` ที่ดู
    ค่าเดียวจึงจับได้แค่ QQQM

    คืน DataFrame index = ticker (ไม่รวม ``base``) คอลัมน์ ``min``/``mean``/``max``/``current``
    และ ``n_windows`` — กองที่หน้าต่างไม่พอจะไม่อยู่ในผลลัพธ์ (ไม่เดาค่าแทน)

    ``ValueError`` เมื่อไม่มีคอลัมน์ ``base`` หรือข้อมูลสั้นกว่าหน้าต่าง
    """
    if base not in price_df.columns:
        raise ValueError(f"ไม่มีข้อมูลราคาของ {base} — เทียบ correlation ไม่ได้")
    returns = price_df.pct_change(fill_method=None)
    if len(returns) <= window:
        raise ValueError(
            f"ข้อมูล {len(returns)} แถว สั้นกว่าหน้าต่าง {window} วัน — คิด rolling correlation ไม่ได้"
        )
    rows: dict[str, dict[str, float]] = {}
    for column in price_df.columns:
        if column == base:
            continue
        series = returns[column].rolling(window).corr(returns[base]).dropna()
        if series.empty:
            continue
        rows[str(column)] = {
            "min": float(series.min()),
            "mean": float(series.mean()),
            "max": float(series.max()),
            "current": float(series.iloc[-1]),
            "n_windows": int(len(series)),
        }
    if not rows:
        raise ValueError("ไม่มีกองไหนมีข้อมูลยาวพอสำหรับหน้าต่างที่ขอ")
    return pd.DataFrame.from_dict(rows, orient="index")
