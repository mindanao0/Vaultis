"""Pydantic models for Emergency Fund Calculator."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

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
    monthly_expense: float = Field(gt=0)
    current_savings: float = Field(ge=0)
    monthly_saving_capacity: float = Field(ge=0)
