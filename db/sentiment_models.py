"""ORM สำหรับ sentiment (PostgreSQL) และ engine / session."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text, create_engine, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.sql import func

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

engine: Engine | None
SessionLocal: sessionmaker | None

if DATABASE_URL:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
else:
    engine = None
    SessionLocal = None

Base = declarative_base()


class SentimentResult(Base):
    __tablename__ = "sentiment_results"
    __table_args__ = (
        Index("ix_sentiment_results_symbol_created_at", "symbol", "created_at"),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    symbol = Column(String(20), nullable=False)
    title = Column(Text, nullable=True)
    sentiment = Column(String(10), nullable=True)
    confidence = Column(Float, nullable=True)
    reason = Column(Text, nullable=True)
    published_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=True)


class SentimentSummary(Base):
    __tablename__ = "sentiment_summary"
    __table_args__ = (
        Index("ix_sentiment_summary_symbol_created_at", "symbol", "created_at"),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    symbol = Column(String(20), nullable=False)
    total_articles = Column(Integer, nullable=True)
    positive = Column(Integer, nullable=True)
    negative = Column(Integer, nullable=True)
    neutral = Column(Integer, nullable=True)
    avg_confidence = Column(Float, nullable=True)
    overall_sentiment = Column(String(10), nullable=True)
    score = Column(Float, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=True)


def create_tables() -> None:
    """สร้างตาราง sentiment บน PostgreSQL (ต้องตั้ง DATABASE_URL ใน .env)."""
    if engine is None:
        raise RuntimeError("DATABASE_URL is not set in .env")
    Base.metadata.create_all(bind=engine)


def get_latest_sentiment_summaries(symbols: list[str] | None = None) -> list[dict] | None:
    """สรุป sentiment ล่าสุดต่อ symbol สำหรับใช้เป็น "บริบทข้าง ๆ" (Roadmap Phase 3 ข้อ 8).

    คืน ``None`` เมื่อไม่ได้ตั้ง DATABASE_URL หรือเชื่อมต่อไม่ได้ — ผู้เรียกต้องแสดง
    "ไม่มีข้อมูล sentiment" ตรง ๆ; **ค่าที่ได้ห้ามเข้าเลขคะแนน/จัดสรรเด็ดขาด** (invariant)
    """
    if SessionLocal is None:
        return None
    try:
        with SessionLocal() as session:
            latest_per_symbol = (
                session.query(
                    SentimentSummary.symbol,
                    func.max(SentimentSummary.created_at).label("max_created"),
                )
                .group_by(SentimentSummary.symbol)
                .subquery()
            )
            rows = (
                session.query(SentimentSummary)
                .join(
                    latest_per_symbol,
                    (SentimentSummary.symbol == latest_per_symbol.c.symbol)
                    & (SentimentSummary.created_at == latest_per_symbol.c.max_created),
                )
                .all()
            )
    except Exception:
        return None

    wanted = {s.strip().upper() for s in symbols} if symbols else None
    results = []
    for row in rows:
        symbol = str(row.symbol).upper()
        if wanted is not None and symbol not in wanted:
            continue
        results.append(
            {
                "symbol": symbol,
                "overall_sentiment": row.overall_sentiment,
                "score": row.score,
                "total_articles": row.total_articles,
                "positive": row.positive,
                "negative": row.negative,
                "neutral": row.neutral,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return sorted(results, key=lambda item: item["symbol"])
