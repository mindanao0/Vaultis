"""Pydantic models for Cash Flow Forecasting."""

from __future__ import annotations

import datetime as _dt
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# ตัวตรวจ "ต้องเป็นตัวเลขจำกัด" มีนิยามเดียวทั้ง backend อยู่ที่ backend/schemas.py
# — ห้ามเขียนใหม่ในไฟล์นี้ (ข้อความ error จะได้เป็นชุดเดียวกันทุก endpoint)
from ..schemas import _finite_amount


class TransactionItem(BaseModel):
    # เป็น datetime.date ไม่ใช่สตริง: `_month_key()` เดิมตัด 7 ตัวแรกดื้อ ๆ ทำให้วันที่
    # ผิดรูป ("" หรือ "06/08/2026") กลายเป็น bucket เดือนขยะ แล้วถ่วงค่าเฉลี่ยลงเงียบ ๆ
    # ให้ Pydantic ปฏิเสธตั้งแต่ขอบ (422 พร้อมชี้แถวที่ผิด) — B1.4
    date: _dt.date
    amount: float  # ใช้ค่าสัมบูรณ์เสมอ ทิศทางดูจาก `type`
    category: str
    type: Literal["income", "expense"]
    description: str = ""

    @field_validator("amount")
    @classmethod
    def _amount_must_be_finite(cls, value: float) -> float:
        """``inf``/``NaN`` ต้องถูกปฏิเสธที่ประตู — AUDIT_ROUND2_2026-08-07.

        Starlette แกะ body ด้วย ``json.loads`` ของ Python ซึ่งรับทั้ง ``1e400`` (→ ``inf``)
        และ literal ``NaN`` ค่าจึงไหลผ่าน ``float`` เปล่า ๆ เข้าไปคำนวณจนสุดทาง แล้วไป
        ตายตอน ``JSONResponse`` render (``allow_nan=False``) — ซึ่ง ``except ValueError``
        ของ router ดักไว้แล้วแปลงเป็น 422 ที่ ``detail`` เป็น**สตริงอังกฤษ**ของ JSON
        encoder ("Out of range float values are not JSON compliant: inf") ทั้งที่ openapi
        ประกาศว่า 422 ``detail`` เป็น array ของ object ที่มี ``loc`` ⇒ ไคลเอนต์ที่ทำตาม
        เอกสารพัง และผู้ใช้ไม่รู้ว่าแถวไหนผิด

        ปฏิเสธตรงนี้แทน: ได้ 422 มาตรฐานที่ ``loc`` ชี้ถึงแถว
        (``["body","transactions",3,"amount"]``) พร้อมข้อความไทย
        """
        return _finite_amount(value, "จำนวนเงินของรายการ (amount)")


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
    #
    # ช่องนี้ไม่ต้องมีด่าน isfinite เพิ่ม เพราะกรอบ ge/le ปิด inf/NaN ให้แล้ว:
    # `inf <= 1000` เป็น False และ NaN เทียบอะไรก็ False ทั้งคู่จึงตกด่าน Pydantic เอง
    # (ต่างจาก `ge=0` เปล่า ๆ ที่ `inf >= 0` เป็น True — ดู current_balance ข้างล่าง)
    change_percent: float = Field(ge=-100, le=1000)


class ScenarioRequest(BaseModel):
    months: int = Field(default=3, ge=1, le=24)
    current_balance: float = Field(ge=0)
    transactions: list[TransactionItem]
    scenarios: list[ScenarioAdjustment] = []

    @field_validator("current_balance")
    @classmethod
    def _balance_must_be_finite(cls, value: float) -> float:
        """``ge=0`` ไม่กัน ``inf`` — ``inf >= 0`` เป็น True (AUDIT_ROUND2_2026-08-07).

        ยอดตั้งต้นที่เป็น ``inf`` ทำให้ ``ending_balance`` ทุกเดือนเป็น ``inf`` แล้วไป
        ระเบิดตอน serialize เหมือนกับ ``amount`` ทุกประการ — ต้องปฏิเสธที่ประตูเดียวกัน
        """
        return _finite_amount(value, "ยอดเงินคงเหลือปัจจุบัน (current_balance)")


class BulkTransactionRequest(BaseModel):
    transactions: list[TransactionItem]
