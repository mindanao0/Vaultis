from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TransactionBase(BaseModel):
    date: str
    ticker: str
    shares: float = Field(gt=0)
    price_usd: float = Field(gt=0)
    amount_thb: float = Field(gt=0)
    fx_rate: float = Field(gt=0)
    fee: float = 0.0
    note: str = ""


class TransactionCreate(TransactionBase):
    pass


class TransactionRead(TransactionBase):
    id: int

    class Config:
        from_attributes = True


class PriceAlertBase(BaseModel):
    ticker: str
    alert_type: str
    target_price: float = Field(gt=0)


class PriceAlertCreate(PriceAlertBase):
    pass


class PriceAlertRead(PriceAlertBase):
    id: int
    is_triggered: bool
    created_at: datetime

    class Config:
        from_attributes = True


class BacktestRequest(BaseModel):
    initial_capital: float = Field(default=10000, gt=0)
    weights: dict[str, float]


class DcaSimRequest(BaseModel):
    monthly_investment: float = Field(default=1000, gt=0)
    weights: dict[str, float]


class AiAdviceRequest(BaseModel):
    budget_thb: float = Field(default=5000, gt=0)


class GenericResponse(BaseModel):
    status: str = "ok"
    data: Any


class SlipUploadResponse(BaseModel):
    success: bool
    amount: float | None = None
    date: str | None = None
    sender: str | None = None
    receiver: str | None = None
    category: str | None = None
    error: str | None = None


class SentimentResponse(BaseModel):
    symbol: str
    total_articles: int
    positive: int
    negative: int
    neutral: int
    avg_confidence: float
    overall_sentiment: str
    score: float
    created_at: datetime
    cached: bool


class HoldingInput(BaseModel):
    symbol: str
    shares: float = Field(gt=0)


class RebalanceRequest(BaseModel):
    holdings: list[HoldingInput]
    risk_profile: Literal["conservative", "moderate", "aggressive"] = "moderate"
    available_budget_thb: float = Field(default=0.0, ge=0)


class GoalCreate(BaseModel):
    model_config = ConfigDict(json_encoders={datetime: lambda v: v.isoformat()})

    name: str
    target_amount_thb: float = Field(gt=0)
    current_amount_thb: float = Field(default=0.0, ge=0)
    monthly_contribution_thb: float = Field(gt=0)
    target_date: datetime
    risk_profile: Literal["conservative", "moderate", "aggressive"] = "moderate"


class GoalRead(GoalCreate):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, json_encoders={datetime: lambda v: v.isoformat()})


class GoalContributeRequest(BaseModel):
    actual_contribution_thb: float = Field(gt=0)
