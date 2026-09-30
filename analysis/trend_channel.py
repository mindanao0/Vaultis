# -*- coding: utf-8 -*-
"""Trend channel เชิงสถิติ (Roadmap A2): linear regression บน log(price) ± σ.

ตอบ "ราคาตอนนี้อยู่ส่วนไหนของเทรนด์หลายปีของตัวเอง" เชิงพรรณนา — ไม่ใช่การพยากรณ์
ใช้วาดประกอบกราฟ และตั้งแต่ 2026-08-31 เป็นมิติ Valuation (0–10) ของคะแนนกลาง
(``financial_model._valuation_score`` — ผู้ใช้เลือกเพิ่มมิตินี้เอง) จึง**เข้าเลขคะแนนและ
การจัดสรร DCA** ผ่านตัวคูณ 0.6–1.4 แล้ว ไม่ใช่สถิติพรรณนาล้วนอย่างที่ไฟล์นี้เคยเขียนไว้
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# ~2 ปีเทรด — น้อยกว่านี้เส้นเทรนด์ "หลายปี" ไม่มีความหมาย อย่าวาดแถบมั่ว
#
# ต้อง **ต่ำกว่า** จำนวนแท่งที่ ``period="2y"`` ส่งมาเสมอ (ทุกผู้เรียกของคะแนนกลางดึง 2 ปี)
# เดิมตั้ง 504 (= 252 × 2) แต่หน้าต่าง 2 ปีจริงมีแท่ง 500–507 ขึ้นกับวันหยุดที่ตกในช่วงนั้น
# (วัดจาก VOO 6 ปีย้อนหลัง 2026-09-30: 500×85 · 501×223 · 502×285 · 503×349 · 504×309 ·
# 505×223 · 506×31 · 507×2 วัน) ⇒ มิติ Valuation ติดแค่ ~37% ของวันแล้วดับที่เหลือ
# ตัวหารคะแนนสลับ 115↔125 และแผน DCA กระโดดตามวันที่ ไม่ใช่ตามตลาด
# 490 = ต่ำกว่าค่าต่ำสุดที่วัดได้ 10 แท่ง (เผื่อแท่งที่ผู้ให้ข้อมูลเว้นว่าง) ยังคง ~1.95 ปี
MIN_TREND_POINTS = 490


def fit_trend_channel(closes: pd.Series) -> dict[str, Any]:
    """fit เส้นเทรนด์ log-linear และวัดระยะราคาปัจจุบันจากเทรนด์เป็นหน่วย σ.

    คืน dict:
    - ``trend``: pd.Series เส้นเทรนด์ในหน่วยราคา (index เดียวกับข้อมูลที่ใช้ fit)
    - ``sigma_log``: ส่วนเบี่ยงเบนมาตรฐานของ residual ใน log space
      (แถบราคา = trend × exp(k·sigma_log))
    - ``current_sigma``: ราคาปิดล่าสุดห่างเทรนด์กี่ σ (+ = เหนือเทรนด์)
    - ``annual_growth_pct``: อัตราโตต่อปีตามเส้นเทรนด์ (%)

    ข้อมูลน้อยกว่า ``MIN_TREND_POINTS`` / มีราคา ≤ 0 / residual ไม่มีความแปรปรวน
    → raise ``ValueError`` (fail loud — ผู้เรียกต้องแสดงเป็นข้อความ ไม่เดาแถบแทน)
    """
    closes = pd.to_numeric(closes, errors="coerce").dropna()
    if len(closes) < MIN_TREND_POINTS:
        raise ValueError(
            f"ข้อมูล {len(closes)} วันเทรด ไม่พอ fit เทรนด์หลายปี (ต้องมี ≥ {MIN_TREND_POINTS})"
        )
    if (closes <= 0).any():
        raise ValueError("พบราคา ≤ 0 ในข้อมูล — fit เทรนด์แบบ log ไม่ได้")

    x = np.arange(len(closes), dtype=float)
    log_price = np.log(closes.to_numpy(dtype=float))
    slope, intercept = np.polyfit(x, log_price, 1)
    fitted_log = slope * x + intercept
    residuals = log_price - fitted_log
    sigma = float(residuals.std(ddof=1))
    if not np.isfinite(sigma) or sigma <= 0:
        raise ValueError("residual ไม่มีความแปรปรวน — คำนวณแถบ σ ไม่ได้")

    return {
        "trend": pd.Series(np.exp(fitted_log), index=closes.index),
        "sigma_log": sigma,
        "current_sigma": float(residuals[-1] / sigma),
        "annual_growth_pct": float((np.exp(slope * 252) - 1.0) * 100.0),
    }
