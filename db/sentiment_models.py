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
