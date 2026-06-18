from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ETFInfo(BaseModel):
    symbol: str
    name: Optional[str] = None
    price: Optional[float] = None
    nav: Optional[float] = None
    total_assets: Optional[float] = None
    expense_ratio: Optional[float] = None
    dividend_yield: Optional[float] = None
    trailing_dividend: Optional[float] = None
    ytd_return: Optional[float] = None
    three_year_return: Optional[float] = None
    five_year_return: Optional[float] = None
    beta: Optional[float] = None
    category: Optional[str] = None
    profile: Optional[str] = None  # ETF description from hardcoded map


class TechnicalIndicators(BaseModel):
    symbol: str
    price: float
    rsi: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_hist: Optional[float] = None
    bb_upper: Optional[float] = None
    bb_middle: Optional[float] = None
    bb_lower: Optional[float] = None
    ma50: Optional[float] = None
    ma200: Optional[float] = None
    volume_ma20: Optional[float] = None
    golden_cross: bool = False
    death_cross: bool = False
    signal: str  # "bullish" / "bearish" / "neutral"


class ETFAnalysis(BaseModel):
    symbol: str
    info: ETFInfo
    technical: TechnicalIndicators
    overall_signal: str  # strong_buy/buy/hold/sell/strong_sell
    ai_summary: Optional[str] = None
    updated_at: datetime


class ETFCompareResponse(BaseModel):
    analyses: list[ETFAnalysis]
    ai_summary: str
