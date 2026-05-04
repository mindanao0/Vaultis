"""Sentiment API: ข่าว + Claude + cache ใน PostgreSQL."""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db.sentiment_models import SentimentSummary, SessionLocal

from ..schemas import SentimentResponse

router = APIRouter(prefix="/api", tags=["Sentiment"])


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


def _utc_naive_one_hour_ago() -> datetime:
    return (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None)


def _summary_to_response(row: SentimentSummary, sym: str, cached: bool) -> SentimentResponse:
    return SentimentResponse(
        symbol=sym,
        total_articles=int(row.total_articles or 0),
        positive=int(row.positive or 0),
        negative=int(row.negative or 0),
        neutral=int(row.neutral or 0),
        avg_confidence=float(row.avg_confidence or 0.0),
        overall_sentiment=str(row.overall_sentiment or "neutral"),
        score=float(row.score or 0.0),
        created_at=row.created_at or datetime.now(timezone.utc).replace(tzinfo=None),
        cached=cached,
    )


@router.get("/sentiment/{symbol}", response_model=SentimentResponse)
def get_sentiment(
    symbol: str,
    db: Session = Depends(get_sentiment_db),
) -> SentimentResponse:
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")

    cutoff = _utc_naive_one_hour_ago()
    cached_row = (
        db.query(SentimentSummary)
        .filter(SentimentSummary.symbol == sym)
        .filter(SentimentSummary.created_at > cutoff)
        .order_by(SentimentSummary.created_at.desc())
        .first()
    )
    if cached_row is not None:
        return _summary_to_response(cached_row, sym, cached=True)

    stale_row = (
        db.query(SentimentSummary)
        .filter(SentimentSummary.symbol == sym)
        .order_by(SentimentSummary.created_at.desc())
        .first()
    )
    if stale_row is not None:
        return _summary_to_response(stale_row, sym, cached=True)

    raise HTTPException(
        status_code=404,
        detail=(
            f"No sentiment data for {sym}. "
            "Run the sentiment job (e.g. analysis.sentiment_analyzer.run_sentiment_job) "
            "or try again after data is available."
        ),
    )
