from dataclasses import dataclass, field
from typing import Optional, List
from pydantic import BaseModel


@dataclass
class ScreenerRule:
    field: str        # "rsi", "macd_cross", "price_vs_ma200", "golden_cross", "bb_squeeze", "volume_spike", "price_drop_pct"
    operator: str     # "lt", "gt", "cross_up", "cross_down", "squeeze", "spike", "drop_pct"
    value: Optional[float] = None
    description: str = ""


@dataclass
class ScreenerPreset:
    name: str
    rules: List[ScreenerRule]
    logic: str = "AND"   # "AND" | "OR"
    description: str = ""


@dataclass
class ScreenerResult:
    symbol: str
    matched_rules: List[str]
    price: float
    signal_strength: float  # 0-10
    preset_name: str
    timestamp: str


class ScreenerRunRequest(BaseModel):
    symbols: List[str]
    preset: str


class CustomScreenerRequest(BaseModel):
    symbols: List[str]
    rules: List[dict]
    logic: str = "AND"
