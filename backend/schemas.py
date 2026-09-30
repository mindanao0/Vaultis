import math
from datetime import date as _date
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from analysis.financial_model import ALLOCATION_UNIT_THB
from portfolio.weight_rules import validate_weights

from .models.finite import FiniteFloat, ensure_finite_input, register_field_labels

register_field_labels(
    {
        "shares": "จำนวนหน่วย",
        "price_usd": "ราคาต่อหน่วย (USD)",
        "amount_thb": "จำนวนเงิน (บาท)",
        "fx_rate": "อัตราแลกเปลี่ยน",
        "fee": "ค่าธรรมเนียม",
        "target_price": "ราคาเป้าหมาย",
        "target_amount_thb": "เป้าหมาย (บาท)",
        "monthly_contribution_thb": "เงินสมทบต่อเดือน (บาท)",
        "actual_contribution_thb": "เงินที่สมทบจริง (บาท)",
    }
)


class TransactionBase(BaseModel):
    date: str
    ticker: str
    # ทุกช่องเป็น FiniteFloat: ``Field(gt=0)`` ปล่อย ``inf`` ผ่าน (K8/G8) และธุรกรรม
    # เป็นข้อมูลที่ถูก **บันทึกถาวร** ค่าพิษหนึ่งแถวจึงเสียหายยาวกว่าคำขอเดียว
    shares: FiniteFloat = Field(gt=0)
    price_usd: FiniteFloat = Field(gt=0)
    amount_thb: FiniteFloat = Field(gt=0)
    fx_rate: FiniteFloat = Field(gt=0)
    fee: FiniteFloat = 0.0
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
    # ``alerts/price_alert.py`` มีด่าน ``math.isfinite`` ของตัวเองอยู่แล้ว ตรงนี้เป็น
    # การกันชั้นนอกให้ตอบ 422 พร้อมเหตุผลไทย แทนที่จะให้ไปตกด่านในเป็น 400/500
    target_price: FiniteFloat = Field(gt=0)


class PriceAlertCreate(PriceAlertBase):
    pass


class PriceAlertRead(PriceAlertBase):
    id: int
    is_triggered: bool
    created_at: datetime

    class Config:
        from_attributes = True


def _finite_amount(value: float, label: str) -> float:
    """``Field(gt=0)`` ไม่กัน ``inf`` — ``inf > 0`` เป็น True (AUDIT_ROUND2_2026-08-07 G8).

    จำนวนเงินที่เป็น ``inf`` ทำให้ทุกช่องของผลลัพธ์กลายเป็น ``null`` (json_safe แปลง
    ``inf`` เป็น ``None``) แล้ว endpoint ยังตอบ 200 ราวกับคำนวณสำเร็จ

    รูปแบบฟังก์ชันนี้มีไว้เรียกจากใน ``field_validator`` (``cashflow_models`` ใช้อยู่)
    ส่วนฟิลด์ที่ประกาศชนิดได้ตรง ๆ ใช้ :data:`backend.models.finite.FiniteFloat`
    — **การตัดสินว่า "จำกัดค่า" คืออะไร อยู่ที่ ``finite.py`` ที่เดียว** ตัวนี้เป็นเปลือก
    ที่เรียกต่อ ห้ามเขียนเงื่อนไข ``isfinite`` ซ้ำที่นี่ (ต้องเป็น ``ensure_finite_input``
    ไม่ใช่ ``ensure_finite_result`` — ค่าที่ผู้ใช้ส่งมากับค่าที่เราคำนวณล้น เป็นคนละอาการ)
    """
    return ensure_finite_input(value, label)


def parse_iso_date(value: str, label: str) -> _date:
    """แปลงสตริงเป็นวันที่ ``YYYY-MM-DD`` — ผิดรูป = ``ValueError`` ภาษาไทย.

    กฎเดียวกับ ``SnapshotRequest._check_snapshot_date`` (``backend/models/networth_models.py``)
    คือ ``date.fromisoformat()`` แล้วรายงานเป็นภาษาไทยพร้อมค่าที่รับมาจริง — การตรวจ
    วันที่ของทั้งระบบต้องมีนิยามเดียว ไม่ใช่ต่างคนต่างเขียน

    ทำไมต้องมี (AUDIT_ROUND2_2026-08-07): ``POST /api/backtest`` ประกาศ ``start``/``end``
    เป็น ``str`` เปล่า ค่าอย่าง ``"banana"`` จึงเดินผ่าน Pydantic ไปตายที่ yfinance
    (``ValueError: time data 'banana' does not match format '%Y-%m-%d'``) ซึ่งชั้นล่างนับ
    เป็น "ผลว่าง" แล้ว retry 3 รอบ จบด้วย ``PriceDataUnavailableError`` → router แปลเป็น
    **503 "ดึงราคาไม่สำเร็จ"** = เอาความผิดของคำขอไปโยนให้แหล่งข้อมูล ผู้ใช้จึงลองใหม่
    ซ้ำ ๆ โดยไม่มีวันสำเร็จ และเสียการยิงเน็ตจริงรอบละ 3 ครั้ง (เชื้อของ rate-limit)

    คืนเป็น ``date`` ให้ผู้เรียก normalize เป็น ``isoformat()`` ก่อนส่งต่อ: ``fromisoformat``
    ของ Python ≥3.11 รับรูปแบบ ISO อื่นด้วย (เช่น ``"20260105"``) ซึ่ง yfinance อ่านไม่ออก
    """
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} ห้ามว่าง — ต้องเป็นวันที่รูปแบบ YYYY-MM-DD")
    try:
        return _date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{label} ต้องเป็นวันที่รูปแบบ YYYY-MM-DD (ได้ {text!r})") from exc


def validate_dca_budget(value: float) -> float:
    """ด่านเดียวของ "งบ DCA น้อยกว่าหนึ่งก้อน" — ต้องเป็น 422 ไม่ใช่ 500.

    ``calculate_allocation()`` โยน ``ValueError`` เมื่องบ < ``ALLOCATION_UNIT_THB``
    (100 บาท) เพราะแผนจัดสรรปัดเป็นหลักร้อย งบน้อยกว่านั้นแจกไม่ลงสักกอง
    ``/api/analysis/full`` มีด่าน 422 รับไว้แล้ว แต่ ``POST /api/ai/advice`` ยังปล่อยให้
    ``get_monthly_advice()`` ห่อเป็น ``RuntimeError`` → router ตอบ **500** ⇒ อินพุตที่
    ผู้เรียกกรอกผิดถูกเล่าเป็น "เซิร์ฟเวอร์พัง" และของจริงจะยิง yfinance ครบทุกกอง
    ก่อนถึงบรรทัดที่ตาย = เสียเวลา/โควตาฟรี ๆ (AUDIT_ROUND2_2026-08-07)

    ตัวเลข 100 มีนิยามเดียวที่ ``financial_model.ALLOCATION_UNIT_THB`` — ห้ามเขียนซ้ำ

    **ผู้เรียกทั้งสองฝั่งต้องเรียกฟังก์ชันนี้ ไม่ใช่ลอกเงื่อนไขไปเขียนเอง** — ไม่ใช่แค่ค่า
    คงที่ที่ต้องมีนิยามเดียว ประโยคไทยที่ผู้ใช้เห็นก็ด้วย: ``GET /api/analysis/full`` เคยมี
    ด่านของตัวเองที่เขียนประโยคเดียวกันซ้ำอีกชุดพร้อม ``Query(..., ge=1)`` ผลคือ
    ``budget_thb=inf`` เดินผ่านทั้งสองเงื่อนไข (``inf >= 1`` จริง และ ``inf < 100`` เท็จ)
    ไปตายตอน serialize เป็น **500 ข้อความอังกฤษ** ขณะที่ ``POST /api/ai/advice`` ตอบ 422
    ภาษาไทย — อินพุตเดียวกัน สองคำตอบ (AUDIT_ROUND2_2026-08-07)

    ด่านนี้จึงกันสองอย่างพร้อมกัน และ **แยกข้อความให้คนละสาเหตุ**: ``inf``/``NaN`` =
    "ค่าที่ใช้ไม่ได้" (``_finite_amount``) ส่วน 1–99 บาท = "งบน้อยเกินจัดสรร"
    """
    amount = _finite_amount(value, "งบ DCA (budget_thb)")
    if amount < ALLOCATION_UNIT_THB:
        raise ValueError(
            f"งบต้องอย่างน้อย {ALLOCATION_UNIT_THB} บาท "
            "(แผนจัดสรรปัดเป็นหลักร้อย งบน้อยกว่านี้แจกไม่ลงสักกอง)"
        )
    return amount


class _WeightedPortfolioRequest(BaseModel):
    """ฐานร่วมของคำขอที่รับ "น้ำหนักพอร์ต" — ด่านตรวจน้ำหนักมีนิยามเดียว.

    ตรวจที่ชั้น schema เพื่อให้ตอบ **422 พร้อมข้อความไทย** ตั้งแต่ก่อนแตะราคา แทนที่จะ
    ปล่อยให้ ``inf`` ไหลลงไปเป็น ``NaN`` แล้วออกมาเป็นเส้นมูลค่าแบนราบ (200) — กฎเดียวกัน
    กับที่ ``portfolio/backtest.py`` และ ``portfolio/dca.py`` ใช้ เพราะแดชบอร์ดเรียก
    สองไฟล์นั้นตรง ๆ โดยไม่ผ่าน API
    """

    weights: dict[str, float]

    @field_validator("weights")
    @classmethod
    def _weights_must_be_usable(cls, value: dict[str, float]) -> dict[str, float]:
        return validate_weights(value)


class PortfolioBacktestRequest(_WeightedPortfolioRequest):
    """คำขอ backtest แบบน้ำหนักพอร์ต (POST /api/analysis/backtest).

    เดิมชื่อ ``BacktestRequest`` ซึ่งชนกับ ``backend/models/backtest_models.BacktestRequest``
    (กลยุทธ์ RSI+MACD ของ /api/backtest) ทำให้ openapi ต้องตั้งชื่อ component เป็น
    ``backend__schemas__BacktestRequest`` และคนอ่าน /docs แยกสองอันนี้ไม่ออก
    """

    initial_capital: float = Field(default=10000, gt=0)

    @field_validator("initial_capital")
    @classmethod
    def _capital_must_be_finite(cls, value: float) -> float:
        return _finite_amount(value, "เงินลงทุนเริ่มต้น (initial_capital)")


class DcaSimRequest(_WeightedPortfolioRequest):
    monthly_investment: float = Field(default=1000, gt=0)

    @field_validator("monthly_investment")
    @classmethod
    def _investment_must_be_finite(cls, value: float) -> float:
        return _finite_amount(value, "งบลงทุนต่อเดือน (monthly_investment)")


class AiAdviceRequest(BaseModel):
    """คำขอ ``POST /api/ai/advice``.

    ``budget_thb`` ถูกตรวจที่ชั้น schema เพื่อให้ "งบน้อยเกินจัดสรร" ได้ **422 เท่ากับ
    ``GET /api/analysis/full``** (อินพุตเดียวกันต้องได้คำตอบเดียวกัน) และเพื่อให้
    ปฏิเสธก่อนที่ ``get_monthly_advice()`` จะยิงราคาทุกกอง
    """

    budget_thb: float = Field(
        default=5000,
        gt=0,
        description=f"งบ DCA เดือนนี้ (บาท) — ต้องอย่างน้อย {ALLOCATION_UNIT_THB} บาท",
    )

    @field_validator("budget_thb")
    @classmethod
    def _budget_must_be_allocatable(cls, value: float) -> float:
        return validate_dca_budget(value)


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
    """สรุป sentiment ล่าสุดของ symbol หนึ่ง — ``null`` = "ไม่รู้" ไม่ใช่ 0 และไม่ใช่ "neutral".

    ทุกคอลัมน์ของ ``sentiment_summary`` เป็น ``nullable`` และ ``sentiment_aggregator``
    ก็เขียน ``None`` ลง ``score``/``avg_confidence`` จริงเมื่อไม่มีบทความที่จัดป้ายได้
    (คู่กับ ``overall_sentiment = "unknown"``) เดิม router แปลงทุกช่องด้วยสำนวน ``or``
    (``float(row.score or 0.0)``, ``str(row.overall_sentiment or "neutral")``) แถวที่ยัง
    ไม่มีผลวิเคราะห์จึงออกไปเป็น sentiment ที่ดูสมบูรณ์: 0 บทความ ความเชื่อมั่น 0.0
    คะแนน 0.0 ทิศทาง "neutral" — ตรงกับข้อห้ามใน CLAUDE.md เป๊ะ และสำนวน ``or`` ยัง
    กลืน ``0``/``0.0`` ที่เป็นคำตอบจริงเข้ากับ NULL จนแยกไม่ออก (AUDIT_ROUND2_2026-08-07)

    ``missing_fields`` = รายชื่อช่องที่ฐานไม่มีค่าให้ (ธงให้ผู้บริโภครู้ว่าแถวนี้ไม่ครบ
    โดยไม่ต้องเดาจาก ``null``) ว่าง = ครบทุกช่อง
    """

    symbol: str
    total_articles: int | None = None
    positive: int | None = None
    negative: int | None = None
    neutral: int | None = None
    avg_confidence: float | None = None
    # "unknown" = ไม่มีบทความที่จัดป้ายได้ · null = ฐานไม่มีค่าในช่องนี้เลย
    overall_sentiment: str | None = None
    score: float | None = None
    created_at: datetime | None = None
    cached: bool
    missing_fields: list[str] = []


class HoldingInput(BaseModel):
    symbol: str
    shares: FiniteFloat = Field(gt=0)


class RebalanceRequest(BaseModel):
    holdings: list[HoldingInput]
    risk_profile: Literal["conservative", "moderate", "aggressive"] = "moderate"
    available_budget_thb: float = Field(default=0.0, ge=0)


class GoalCreate(BaseModel):
    model_config = ConfigDict(json_encoders={datetime: lambda v: v.isoformat()})

    name: str
    target_amount_thb: FiniteFloat = Field(gt=0)
    current_amount_thb: float = Field(default=0.0, ge=0)
    monthly_contribution_thb: FiniteFloat = Field(gt=0)
    target_date: datetime
    risk_profile: Literal["conservative", "moderate", "aggressive"] = "moderate"


class GoalRead(GoalCreate):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, json_encoders={datetime: lambda v: v.isoformat()})


class GoalContributeRequest(BaseModel):
    actual_contribution_thb: FiniteFloat = Field(gt=0)
