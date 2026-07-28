"""Sentiment API: อ่านสรุป sentiment จาก PostgreSQL เท่านั้น."""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timezone

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


def _summary_to_response(row: SentimentSummary) -> SentimentResponse:
    return SentimentResponse(
        symbol=str(row.symbol or ""),
        total_articles=int(row.total_articles or 0),
        positive=int(row.positive or 0),
        negative=int(row.negative or 0),
        neutral=int(row.neutral or 0),
        avg_confidence=float(row.avg_confidence or 0.0),
        overall_sentiment=str(row.overall_sentiment or "neutral"),
        score=float(row.score or 0.0),
        created_at=row.created_at or datetime.now(timezone.utc).replace(tzinfo=None),
        cached=False,
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
