"""Pydantic models for Debt Optimization."""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    ValidationInfo,
    computed_field,
)

# ชื่อไทยของแต่ละฟิลด์ ใช้ประกอบข้อความ error ให้ผู้ใช้อ่านออก
_FIELD_LABELS = {
    "balance": "ยอดหนี้คงเหลือ",
    "interest_rate": "อัตราดอกเบี้ย",
    "min_payment": "ยอดชำระขั้นต่ำ",
    "monthly_budget": "งบชำระต่อเดือน",
    "extra_payments": "เงินจ่ายเพิ่มต่อเดือน",
}

# สตริงที่ ``float()`` แปลงเป็น inf/NaN ได้ (รวมรูปที่ผู้ใช้พิมพ์มาเองด้วย)
_NON_FINITE_TOKENS = {"inf", "-inf", "+inf", "infinity", "-infinity", "+infinity", "nan", "-nan"}


def _to_json_safe(value: Any) -> Any:
    """ด่านที่ 1 — แปลง inf/NaN เป็นสตริงก่อนที่ pydantic จะจดค่านี้ลง error.

    FastAPI ใส่ ``input`` (ค่าที่ผู้ใช้ส่งมา) ลงใน response 422 ด้วย และ ``json.dumps``
    ของ starlette ตั้ง ``allow_nan=False`` → ถ้าปล่อย ``inf`` ติดไปกับ error ทั้งคำขอจะ
    กลายเป็น 500 ที่ไม่มีเนื้อความแทนที่จะเป็น 422 ที่บอกสาเหตุ
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value  # ไม่ใช่ตัวเลข — ปล่อยให้ pydantic รายงาน type error ตามปกติ
    if math.isfinite(number):
        return value
    return repr(number)  # 'inf' | '-inf' | 'nan'


def _reject_non_finite(value: Any, info: ValidationInfo) -> Any:
    """ด่านที่ 2 — ปฏิเสธค่าที่ด่านแรกทำเครื่องหมายไว้ พร้อมเหตุผลภาษาไทย.

    K8: ``Field(gt=0)`` ปล่อย ``inf`` ผ่าน เพราะ ``inf > 0`` เป็น True เดิม ``inf`` จึงไหล
    เข้า ``_simulate`` ไปชนเพดาน ``_MAX_MONTHS`` แล้ว raise ว่า "ดอกเบี้ยเดินเร็วกว่าเงินต้น
    ที่จ่ายได้" — **ไม่ใช่สาเหตุจริง** ผู้ใช้จะไปเพิ่มงบทั้งที่ปัญหาอยู่ที่ตัวเลขที่กรอก
    (``NaN`` เดิมตกที่ ``gt=0`` อยู่แล้ว แต่ข้อความเป็นอังกฤษและบอกเหตุผลผิดเช่นกัน)
    """
    if isinstance(value, str) and value.strip().lower() in _NON_FINITE_TOKENS:
        label = _FIELD_LABELS.get(info.field_name or "", info.field_name or "ค่านี้")
        raise ValueError(
            f"{label} ต้องเป็นตัวเลขจริงที่มีค่าจำกัด (ได้รับ {value}) — "
            "inf/NaN ไม่ใช่จำนวนเงินหรืออัตราที่คำนวณแผนชำระหนี้ได้"
        )
    return value


# BeforeValidator ใน Annotated ทำงานจากขวาไปซ้าย: _to_json_safe ก่อน แล้วค่อย _reject_non_finite
FiniteFloat = Annotated[
    float,
    BeforeValidator(_reject_non_finite),
    BeforeValidator(_to_json_safe),
]


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
