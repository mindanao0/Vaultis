"""Pydantic models for Cash Flow Forecasting."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TransactionItem(BaseModel):
    date: str  # YYYY-MM-DD
    amount: float  # positive = income, negative = expense
    category: str
    type: Literal["income", "expense"]
    description: str = ""


class CategoryAnomaly(BaseModel):
    category: str
    avg_monthly: float
    last_month: float
    change_percent: float  # positive = more spending vs avg, negative = less


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


class ScenarioAdjustment(BaseModel):
    category: str
    change_percent: float  # -20 = spend 20% less in this category


class ScenarioRequest(BaseModel):
    months: int = Field(default=3, ge=1, le=24)
    current_balance: float = Field(ge=0)
    transactions: list[TransactionItem]
    scenarios: list[ScenarioAdjustment] = []


class BulkTransactionRequest(BaseModel):
    transactions: list[TransactionItem]
