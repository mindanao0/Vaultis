# -*- coding: utf-8 -*-
"""โมดูลคำนวณผลตอบแทนหลายช่วงเวลา."""

from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from utils.cache import cache_data_1h


def monthly_seasonality(closes: pd.Series) -> pd.DataFrame:
    """สถิติผลตอบแทนรายเดือนแยกตามเดือนปฏิทิน (Roadmap B5 — เชิงบรรยายเท่านั้น).

    ห้ามนำไป override คะแนน/การจัดสรร — ข้อมูล ~10 ปีให้ตัวอย่างต่อเดือนแค่ ~10 ค่า
    (noise สูง) ใช้เล่าเรื่อง "เดือนไหนในอดีตมักอ่อน/แข็ง" ประกอบการอ่านกราฟ

    คืน DataFrame index = เดือน 1-12: ``median_pct``, ``mean_pct``,
    ``positive_rate_pct`` (% ของปีที่เดือนนั้นบวก), ``n_samples``
    เดือนที่ไม่มีตัวอย่างเลยคงเป็น NaN — ไม่เติม 0

    ``fill_method=None`` บังคับไว้ (B11): เดือนที่ไม่มีราคาเลยจะได้ ``NaN`` จาก
    ``resample`` ถ้าปล่อยให้ pandas ffill ตามค่าเริ่มต้น เดือนนั้นจะกลายเป็นผลตอบแทน
    0.00% (และเดือนถัดไปกลายเป็นผลตอบแทนข้ามหลายเดือน) — ทั้งคู่คือตัวอย่างที่ถูกกุขึ้น
    """
    closes = pd.to_numeric(closes, errors="coerce").dropna()
    if closes.empty:
        raise ValueError("ไม่มีข้อมูลราคา ไม่สามารถคำนวณ seasonality ได้")
    monthly_returns = closes.resample("ME").last().pct_change(fill_method=None).dropna()
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


def real_bars(closes: pd.Series) -> pd.Series:
    """แท่งราคา**จริง**ของอนุกรมเดียว — ช่องว่างถูก "ตัดทิ้ง" ไม่ใช่ "เติม".

    **นิยามเดียวของ "แท่งจริง" ทั้งระบบ** — ``calculate_period_returns``,
    ``main._real_bars`` และ ``etf_service._real_bars`` เรียกตัวนี้ตัวเดียวกัน
    (``financial_model.score_from_prices`` ทำสิ่งเดียวกันกับซีรีส์ที่รับเข้ามา)

    ทำไมต้องคิดรายคอลัมน์แทนการ ``ffill()`` ทั้งเฟรม (AUDIT_ROUND2_2026-08-07 G7):
    ``data/fetcher.fetch_adjusted_close_data`` ตัดทิ้งเฉพาะแถวที่ NaN **ทุกคอลัมน์**
    (``dropna(how="all")``) หางคอลัมน์ของ ticker ที่ผู้ให้ข้อมูลหยุดส่งจึงรอดมาเสมอ
    ถ้า ffill ก่อนคำนวณ ราคาสุดท้ายจะไม่มีวันเป็น NaN ⇒ guard ทั้งหมดของ
    ``period_return_pct`` กลายเป็นโค้ดตาย และเมื่อช่องว่างยาวกว่าหน้าต่าง
    ตัวตั้งกับตัวหารกลายเป็นราคาเดียวกันเป๊ะ ⇒ **0.0000%** ที่ผู้ใช้อ่านว่า
    "ราคาไม่ขยับเลยทั้งเดือน" ทั้งที่ความจริงคือ "ดึงราคาไม่ได้"

    ``inf``/``-inf`` ถูกตัดด้วย: ไม่ใช่ราคา และ ``JSONResponse`` (``allow_nan=False``)
    จะพาทั้ง endpoint ลงเป็น 500 ถ้าหลุดออกไป
    """
    series = pd.to_numeric(closes, errors="coerce")
    return series[np.isfinite(series)]


def period_return_pct(closes: pd.Series, bars: int) -> float:
    """ผลตอบแทน**ทบต้นจริง**ของช่วง ``bars`` แท่งล่าสุด (หน่วยเปอร์เซ็นต์).

    **นิยามเดียวของ "ผลตอบแทนของช่วง" ทั้งระบบ** — ทั้งตาราง Returns
    (``calculate_period_returns``) และคะแนนโมเมนตัมใน
    ``analysis/financial_model.py`` เรียกฟังก์ชันนี้ตัวเดียวกัน
    เดิมมีสองชุดที่หน้าต่าง/สูตรตรงกันเป๊ะแต่ guard ไม่เท่ากัน (C7)

    **ห้ามกลับไปใช้ ``pct_change().tail(bars).sum()``** (FIX_PLAN ข้อ 1.5):
    ผลรวมเลขคณิตของผลตอบแทนรายวันมากกว่าผลตอบแทนจริงเสมอเมื่อราคาผันผวน
    (``log(1+r) < r`` ทุก ``r != 0`` → ``Σ r_i > log(ราคาปลาย/ราคาต้น)``)
    ราคาที่แกว่งแล้วกลับมาที่เดิมเป๊ะ (ผลตอบแทนจริง 0%) จึงถูกอ่านเป็นบวก
    แล้วได้ ``momentum_score`` ฟรี — วัดจริงบน 10 ปี × 5 ETF พบ 156/10,718 วัน
    (1.46%) ที่ผลรวม > 0 ทั้งที่ผลตอบแทนจริง ≤ 0 และ **ไม่มี flip ทางตรงข้ามเลย**
    = อคติทางเดียวที่ดัน tilt ของแผน DCA ขึ้น

    คืน ``NaN`` เมื่อคำนวณไม่ได้ ผู้เรียกต้องตัดช่วงนั้นออก ห้ามแปลงเป็น 0 (C1):

    - ``bars <= 0`` — คำถามไร้ความหมาย (ถ้าปล่อยผ่านจะได้ 0.0% ที่อ่านเป็น "ไม่ขึ้นไม่ลง")
    - ข้อมูลสั้นกว่าหน้าต่าง (ต้องมีอย่างน้อย ``bars + 1`` แท่ง)
    - ราคาอ้างอิง ``<= 0`` หรือเป็น ``NaN`` — หารแล้วได้ ``inf``/เลขติดลบมโหฬารที่ดูสมจริง
    - ราคาล่าสุดเป็น ``NaN``
    """
    if bars <= 0 or len(closes) <= bars:
        return float("nan")
    start = float(closes.iloc[-(bars + 1)])
    end = float(closes.iloc[-1])
    if not (start > 0) or pd.isna(end):
        return float("nan")
    return (end / start - 1.0) * 100.0


@cache_data_1h
def calculate_period_returns(price_df: pd.DataFrame) -> pd.DataFrame:
    """คำนวณผลตอบแทนย้อนหลังตามช่วงเวลาที่กำหนดให้ ETF แต่ละตัว.

    ใช้ ``period_return_pct`` ซึ่งเป็นนิยามเดียวกับคะแนนโมเมนตัม — ตาราง Returns
    กับคะแนนต้องเล่าเรื่องเดียวกันเสมอ (C7)

    **ห้าม ``ffill()`` ที่นี่** (AUDIT_ROUND2_2026-08-07 G7) — หน้าต่าง "21 แท่ง"
    นับจากแท่งจริงของ ticker นั้นเอง (``real_bars``) ไม่ใช่ 21 แถวของทั้งเฟรมที่
    ยืมวันของ ticker อื่นมา วิธีนี้แก้ทั้งช่องว่าง**กลางทาง** (เหตุผลเดิมที่ใส่ ffill —
    วันหยุดที่ต่างกันระหว่าง ETF ไม่ทำให้ทั้งคอลัมน์หาย) และช่องว่าง**ท้ายคอลัมน์**
    (ผู้ให้ข้อมูลหยุดส่งแท่งของ ticker นั้น) พร้อมกัน  ffill ทำให้กรณีหลังกลายเป็น
    ``0.0000%`` = "ดึงราคาไม่ได้" ถูกอ่านเป็น "ราคาไม่ขยับเลยทั้งช่วง"

    ช่องที่คำนวณไม่ได้คง ``NaN`` ไว้ — ETF ที่เกิดทีหลัง และ ticker ที่มีแท่งจริง
    ไม่ถึงหน้าต่าง ต้องได้ ``NaN`` ไม่ใช่ 0 (C1)
    """
    try:
        if price_df.empty:
            raise ValueError("price_df ว่าง ไม่สามารถคำนวณผลตอบแทนได้")

        # คิดแท่งจริงคอลัมน์ละครั้ง แล้วใช้ซ้ำทุกหน้าต่าง
        bars_by_column = {
            col: real_bars(price_df.iloc[:, i]) for i, col in enumerate(price_df.columns)
        }
        results: dict[str, dict[str, float]] = {
            period: {
                col: period_return_pct(bars, window) for col, bars in bars_by_column.items()
            }
            for period, window in RETURN_WINDOWS.items()
        }

        returns_df = pd.DataFrame(results).T
        returns_df.index.name = "Period"
        return returns_df
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการคำนวณผลตอบแทน: {exc}") from exc
