"""Pydantic models for Debt Optimization."""

from __future__ import annotations

from typing import Literal

from pydantic import (
    BaseModel,
    Field,
    computed_field,
)

from .finite import FiniteFloat, register_field_labels

# ชื่อไทยของแต่ละฟิลด์ ใช้ประกอบข้อความ error ให้ผู้ใช้อ่านออก
# นิยามของ ``FiniteFloat`` ย้ายไปอยู่ที่ ``backend/models/finite.py`` แล้ว (นิยามมีที่เดียว)
# — ไฟล์นี้เหลือหน้าที่บอกว่าฟิลด์ของตัวเองชื่อไทยว่าอะไรเท่านั้น
register_field_labels(
    {
        "balance": "ยอดหนี้คงเหลือ",
        "interest_rate": "อัตราดอกเบี้ย",
        "min_payment": "ยอดชำระขั้นต่ำ",
        "monthly_budget": "งบชำระต่อเดือน",
        "extra_payments": "เงินจ่ายเพิ่มต่อเดือน",
    }
)


class Debt(BaseModel):
    name: str
    balance: FiniteFloat = Field(gt=0)
    # ge=0 ไม่ใช่ gt=0: ผ่อน 0% พบบ่อยมากในไทย (มือถือ/เครื่องใช้ไฟฟ้า/บัตรผ่อนสินค้า)
    # เดิมถูกปฏิเสธด้วย 422 ทั้งที่เป็นหนี้จริงที่ต้องเข้าแผน — และดอกเบี้ย 0 ไม่ทำให้หารด้วย
    # ศูนย์ที่ไหนใน debt_service (``interest = balance × rate/100/12`` เป็นการคูณล้วน)
    interest_rate: FiniteFloat = Field(ge=0, description="Annual interest rate, e.g. 18.0 for 18%")
    min_payment: FiniteFloat = Field(gt=0)


class PaymentEntry(BaseModel):
    month: int
    payment: float
    # ติดลบได้ — งวดที่จ่ายน้อยกว่าดอกเบี้ย ยอดหนี้โตขึ้นจริง ห้ามบีบเป็น 0
    principal: float
    interest: float
    remaining_balance: float


class DebtSchedule(BaseModel):
    name: str
    payments: list[PaymentEntry]
    total_interest: float
    months_to_payoff: int
    # H4: งวดที่ payment < interest = negative amortization (จ่ายขั้นต่ำแล้วหนี้โตขึ้น)
    # ต้องรายงานออกไปให้ผู้ใช้เห็น ไม่ใช่ซ่อนไว้ในตาราง
    negative_amortization_months: list[int] = Field(default_factory=list)


class DebtResult(BaseModel):
    method: Literal["avalanche", "snowball"]
    monthly_budget: float
    total_interest: float
    months_to_payoff: int
    schedules: list[DebtSchedule]


class DebtComparison(BaseModel):
    avalanche: DebtResult
    snowball: DebtResult
    interest_saved: float   # positive = avalanche saves more interest than snowball
    months_saved: int       # positive = avalanche finishes faster


class NegativeAmortizationFlag(BaseModel):
    """หนี้ก้อนหนึ่งกับงวดที่จ่ายไม่พอดอกเบี้ย (ยอดหนี้โตขึ้น).

    ระบุ ``debt_index`` ไว้ด้วย เพราะหนี้คนละก้อนตั้งชื่อซ้ำกันได้ — ถ้าใช้ชื่อเป็นคีย์ (dict)
    ก้อนหลังจะทับก้อนแรกเงียบ ๆ ซึ่งคือการตัดข้อมูลทิ้ง
    """

    debt_index: int
    name: str
    months: list[int]


class SensitivityResult(BaseModel):
    extra_payment: float
    total_interest: float
    months_to_payoff: int
    interest_saved: float   # compared to extra_payment=0
    # K8: /optimize ส่งธง negative amortization ออกไปแล้วผ่าน DebtSchedule แต่ /sensitivity
    # มีแค่ 4 คีย์ ผู้ใช้ที่ถามว่า "จ่ายเพิ่มเดือนละเท่าไหร่ดี" จึงไม่รู้ว่าตัวเลือกที่จ่ายน้อย
    # ยังทำให้หนี้โตขึ้น — เห็นแค่ "ดอกเบี้ยรวมน้อยกว่า" ซึ่งอ่านผิดได้ว่าตัวเลือกนั้นดีกว่า
    negative_amortization: list[NegativeAmortizationFlag] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_negative_amortization(self) -> bool:
        """ธงสรุปสำหรับหน้าจอ — คำนวณจากลิสต์เสมอ จึงขัดแย้งกับรายละเอียดไม่ได้."""
        return bool(self.negative_amortization)


# ---------- Request bodies ----------

class OptimizeRequest(BaseModel):
    debts: list[Debt]
    monthly_budget: FiniteFloat = Field(gt=0)
    method: Literal["both", "avalanche", "snowball"] = "both"


class SensitivityRequest(BaseModel):
    debts: list[Debt]
    monthly_budget: FiniteFloat = Field(gt=0)
    method: Literal["avalanche", "snowball"] = "avalanche"
    extra_payments: list[FiniteFloat] = Field(default=[500, 1000, 2000, 5000])
