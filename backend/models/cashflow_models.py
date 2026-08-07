"""Pydantic models for Cash Flow Forecasting."""

from __future__ import annotations

import datetime as _dt
from typing import Literal

from pydantic import BaseModel, Field


class TransactionItem(BaseModel):
    # เป็น datetime.date ไม่ใช่สตริง: `_month_key()` เดิมตัด 7 ตัวแรกดื้อ ๆ ทำให้วันที่
    # ผิดรูป ("" หรือ "06/08/2026") กลายเป็น bucket เดือนขยะ แล้วถ่วงค่าเฉลี่ยลงเงียบ ๆ
    # ให้ Pydantic ปฏิเสธตั้งแต่ขอบ (422 พร้อมชี้แถวที่ผิด) — B1.4
    date: _dt.date
    amount: float  # ใช้ค่าสัมบูรณ์เสมอ ทิศทางดูจาก `type`
    category: str
    type: Literal["income", "expense"]
    description: str = ""


class CategoryAnomaly(BaseModel):
    category: str
    avg_monthly: float
    last_month: float
    # positive = จ่ายมากกว่าค่าเฉลี่ย, negative = น้อยกว่า
    # None = หมวดที่เพิ่งโผล่ครั้งแรก (ฐานเป็นศูนย์ คิดเป็นเปอร์เซ็นต์ไม่ได้)
    # ห้ามใส่ 0.0 แทน — 0 อ่านได้ว่า "ไม่เปลี่ยนแปลง" ซึ่งตรงข้ามกับความจริง
    change_percent: float | None
    # "change" = เทียบกับค่าเฉลี่ยเดือนก่อน ๆ · "new_category" = ไม่เคยมีมาก่อน
    kind: Literal["change", "new_category"] = "change"


class ForecastMonth(BaseModel):
    month: str  # YYYY-MM
    projected_income: float
    projected_expense: float
    net_cashflow: float
    ending_balance: float


class ForecastResponse(BaseModel):
    current_balance: float
    months: int
    forecast: list[ForecastMonth]
    anomalies: list[CategoryAnomaly]
    emergency_alert: bool
    emergency_message: str
    # จำนวนเดือนที่ครบทั้งเดือนและถูกใช้คำนวณจริง
    months_used: int
    # เดือนที่ถูกตัดออกเพราะยังไม่จบ — ตัดได้ แต่ต้องรายงานให้ผู้ใช้เห็น ห้ามตัดเงียบ
    excluded_partial_months: list[str] = []


class ScenarioAdjustment(BaseModel):
    category: str
    # -20 = หมวดนี้จ่ายน้อยลง 20%
    # -100 คือขีดล่าง (ตัดหมวดนี้ทิ้งทั้งก้อน) ต่ำกว่านั้นแปลว่ารายจ่ายติดลบ
    # ซึ่งไหลไปโผล่เป็น "เงินเข้า" ในยอดคงเหลือ — B1.5
    change_percent: float = Field(ge=-100, le=1000)


class ScenarioRequest(BaseModel):
    months: int = Field(default=3, ge=1, le=24)
    current_balance: float = Field(ge=0)
    transactions: list[TransactionItem]
    scenarios: list[ScenarioAdjustment] = []


class BulkTransactionRequest(BaseModel):
    transactions: list[TransactionItem]
