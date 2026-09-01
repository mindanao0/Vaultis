"""Sentiment API: อ่านสรุป sentiment จาก PostgreSQL เท่านั้น."""

from __future__ import annotations

from collections.abc import Generator

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from db.sentiment_models import SentimentSummary, SessionLocal

from ..schemas import SentimentResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Sentiment"])

_DB_DOWN_DETAIL = (
    "ต่อฐานข้อมูล sentiment ไม่ได้ — ข่าว/sentiment เป็นบริบทประกอบเท่านั้น "
    "ไม่กระทบคะแนนหรือแผน DCA"
)


def get_sentiment_db() -> Generator[Session, None, None]:
    if SessionLocal is None:
        raise HTTPException(
            status_code=503,
            detail="Sentiment database is not configured. Set DATABASE_URL in .env.",
        )
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _as_int(value: object) -> int | None:
    """NULL ในฐาน = ``None`` — ห้ามใช้ ``or`` เพราะมันกลืน ``0`` ที่เป็นคำตอบจริงด้วย."""
    return None if value is None else int(value)


def _as_float(value: object) -> float | None:
    """เช่นเดียวกับ ``_as_int`` — ``0.0`` ที่เป็นคำตอบจริงต้องไม่ถูกนับเป็น "ไม่รู้"."""
    return None if value is None else float(value)


def _summary_to_response(row: SentimentSummary) -> SentimentResponse:
    """แปลงแถวในฐานเป็นคำตอบ — NULL ออกไปเป็น ``null`` พร้อมธง ไม่ใช่ 0 / "neutral".

    เดิมทุกช่องใช้สำนวน ``or`` (``int(row.total_articles or 0)``,
    ``str(row.overall_sentiment or "neutral")``) ⇒ แถวที่คอลัมน์เป็น NULL — ซึ่งเกิดจริง
    เพราะ ``sentiment_aggregator`` เขียน ``None`` ลง ``score``/``avg_confidence`` เมื่อ
    ไม่มีบทความที่จัดป้ายได้ — ออกไปเป็น sentiment ที่ดูสมบูรณ์แบบ (0 บทความ,
    ความเชื่อมั่น 0.0, คะแนน 0.0, ทิศทาง "neutral") โดยไม่มีช่องไหนบอกว่าค่าเหล่านี้
    ถูกเดาขึ้นมา กล่องบริบทในหน้า AI Advisor จึงแสดง "neutral, ความเชื่อมั่น 0%" เป็น
    ข้อเท็จจริง ผู้ใช้แยก "ตลาดเฉย ๆ" ออกจาก "ยังไม่มีผลวิเคราะห์" ไม่ได้
    (AUDIT_ROUND2_2026-08-07 · กฎ "ห้าม return 0 / return 'neutral'" ใน CLAUDE.md)

    ``created_at`` ก็เช่นกัน: เดิมถอยไปใช้ "เวลาตอนนี้" ซึ่งทำให้แถวเก่าที่ไม่มีเวลา
    ดูเหมือนเพิ่งวิเคราะห์เสร็จเมื่อครู่
    """
    fields: dict[str, object] = {
        "total_articles": _as_int(row.total_articles),
        "positive": _as_int(row.positive),
        "negative": _as_int(row.negative),
        "neutral": _as_int(row.neutral),
        "avg_confidence": _as_float(row.avg_confidence),
        "overall_sentiment": None if row.overall_sentiment is None else str(row.overall_sentiment),
        "score": _as_float(row.score),
        "created_at": row.created_at,
    }
    missing = sorted(key for key, value in fields.items() if value is None)

    return SentimentResponse(
        symbol="" if row.symbol is None else str(row.symbol),
        cached=False,
        missing_fields=missing,
        **fields,  # type: ignore[arg-type]
    )


@router.get("/sentiment/{symbol}", response_model=SentimentResponse)
def get_sentiment(
    symbol: str,
    db: Session = Depends(get_sentiment_db),
) -> SentimentResponse:
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")

    # ฐานข้อมูลล่ม = "ยังไม่รู้" ไม่ใช่ 500 เปล่า ๆ — ฝั่ง dashboard จัดการเคสนี้ได้อยู่แล้ว
    # (คืน None → "ไม่มีข้อมูล") backend ต้องบอกให้ชัดพอ ๆ กัน (AUDIT.md M17)
    try:
        row = (
            db.query(SentimentSummary)
            .filter(SentimentSummary.symbol == sym)
            .order_by(SentimentSummary.created_at.desc())
            .first()
        )
    except SQLAlchemyError as exc:
        logger.warning("sentiment DB ใช้ไม่ได้ (%s): %s", sym, exc)
        raise HTTPException(status_code=503, detail=_DB_DOWN_DETAIL) from exc

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No sentiment data yet for {sym}",
        )

    return _summary_to_response(row)
