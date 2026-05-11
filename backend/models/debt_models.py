"""Pydantic models for Debt Optimization."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Debt(BaseModel):
    name: str
    balance: float = Field(gt=0)
    interest_rate: float = Field(gt=0, description="Annual interest rate, e.g. 18.0 for 18%")
    min_payment: float = Field(gt=0)


class PaymentEntry(BaseModel):
    month: int
    payment: float
    principal: float
    interest: float
    remaining_balance: float


class DebtSchedule(BaseModel):
    name: str
    payments: list[PaymentEntry]
    total_interest: float
    months_to_payoff: int


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


class SensitivityResult(BaseModel):
    extra_payment: float
    total_interest: float
    months_to_payoff: int
    interest_saved: float   # compared to extra_payment=0


# ---------- Request bodies ----------

class OptimizeRequest(BaseModel):
    debts: list[Debt]
    monthly_budget: float = Field(gt=0)
    method: Literal["both", "avalanche", "snowball"] = "both"


class SensitivityRequest(BaseModel):
    debts: list[Debt]
    monthly_budget: float = Field(gt=0)
    method: Literal["avalanche", "snowball"] = "avalanche"
    extra_payments: list[float] = Field(default=[500, 1000, 2000, 5000])
