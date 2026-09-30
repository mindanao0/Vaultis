"""Pydantic models for Emergency Fund Calculator."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .finite import FiniteFloat, register_field_labels

register_field_labels(
    {
        "monthly_expense": "รายจ่ายต่อเดือน",
        "current_savings": "เงินสำรองที่มีอยู่",
        "monthly_saving_capacity": "เงินออมต่อเดือน",
    }
)

JobStability = Literal["very_stable", "stable", "unstable", "freelance"]
IncomeType = Literal["salary", "mixed", "freelance", "business"]
Industry = Literal["government", "startup", "self_employed", "other"]


class RiskProfile(BaseModel):
    job_stability: JobStability
    dependents: int = Field(ge=0, description="จำนวนผู้พึ่งพิง (ใช้ 3 แทน 3+)")
    income_type: IncomeType
    has_health_insurance: bool
    industry: Industry = "other"


class EmergencyFundResult(BaseModel):
    risk_score: int                  # 0-100 after clamp
    multiplier: float                # เช่น 5.0 = 5 เดือน
    target_amount: float             # monthly_expense × multiplier
    current_savings: float
    gap: float                       # target_amount - current_savings; ติดลบ = มีเกินเป้าหมาย
    months_to_goal: float | None     # None ถ้าครบเป้าแล้ว หรือ saving_capacity = 0
    recommendation: str              # ภาษาไทย


class EmergencyFundRequest(BaseModel):
    profile: RiskProfile
    # FiniteFloat ไม่ใช่ของประดับ: ``Field(gt=0)`` ปล่อย ``inf`` ผ่าน (inf > 0 เป็นจริง)
    # แล้วไปตายที่ ``math.ceil(inf)`` เป็น HTTP 500 — ความผิดของคำขอถูกรายงานว่า
    # เซิร์ฟเวอร์พัง (K8/G8 · วัดจริง 2026-09-01)
    monthly_expense: FiniteFloat = Field(gt=0)
    current_savings: FiniteFloat = Field(ge=0)
    monthly_saving_capacity: FiniteFloat = Field(ge=0)
