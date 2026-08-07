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
    # ทิศที่พรีเซ็ตนี้มองหา: "buy" | "sell" | "neutral"
    # ใช้โดย ScreenerEngine._compute_signal_strength เท่านั้น — โบนัส RSI ต้องเข้าข้าง
    # ทิศของพรีเซ็ต ไม่ใช่บวกทั้งสองฝั่งเสมอ (AUDIT_2026-08-06 ข้อ B6.3)
    # ดีฟอลต์เป็น "buy" เพราะพรีเซ็ตที่ผู้ใช้ประกอบเองผ่าน /api/screener/custom
    # ไม่มีช่องบอกทิศ และทั้งระบบเป็นเครื่องมือฝั่งสะสม (DCA)
    direction: str = "buy"


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
