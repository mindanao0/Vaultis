"""Sentiment API: ข่าว + Claude + cache ใน PostgreSQL."""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from analysis.news_fetcher import get_news
from analysis.sentiment_aggregator import aggregate_sentiment
from analysis.sentiment_analyzer import analyze_batch
from db.sentiment_models import SentimentResult, SentimentSummary, SessionLocal

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


def _norm_title(s: str) -> str:
    return " ".join((s or "").split()).lower()


def _parse_published_at(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    text = str(value).strip()
    if not text:
        return None
    try:
        s = text
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


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

    articles = get_news(sym)
    if not articles:
        raise HTTPException(
            status_code=404,
            detail=f"No news found for {symbol}",
        )

    results = analyze_batch(articles, sym)
    agg = aggregate_sentiment(results)

    by_title = {}
    for a in articles:
        if not isinstance(a, dict):
            continue
        t = _norm_title(str(a.get("title") or ""))
        if t:
            by_title[t] = a

    for row in results:
        if not isinstance(row, dict):
            continue
        art = by_title.get(_norm_title(str(row.get("title") or "")))
        published_at = _parse_published_at((art or {}).get("published_at"))
        conf_val: float | None = None
        if row.get("confidence") is not None:
            try:
                conf_val = float(row.get("confidence"))
            except (TypeError, ValueError):
                conf_val = None
        db.add(
            SentimentResult(
                symbol=sym,
                title=str(row.get("title") or "") or None,
                sentiment=str(row.get("sentiment") or "") or None,
                confidence=conf_val,
                reason=str(row.get("reason") or "") or None,
                published_at=published_at,
            )
        )

    summary_row = SentimentSummary(
        symbol=sym,
        total_articles=agg["total_articles"],
        positive=agg["positive"],
        negative=agg["negative"],
        neutral=agg["neutral"],
        avg_confidence=agg["avg_confidence"],
        overall_sentiment=agg["overall_sentiment"],
        score=agg["score"],
    )
    db.add(summary_row)
    try:
        db.commit()
        db.refresh(summary_row)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return _summary_to_response(summary_row, sym, cached=False)
