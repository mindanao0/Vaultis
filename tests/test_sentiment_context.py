# -*- coding: utf-8 -*-
"""ทดสอบ get_latest_sentiment_summaries (Roadmap Phase 3 ข้อ 8) — fail-soft เมื่อไม่มีฐาน."""

from db import sentiment_models


def test_returns_none_when_database_not_configured(monkeypatch):
    monkeypatch.setattr(sentiment_models, "SessionLocal", None)
    assert sentiment_models.get_latest_sentiment_summaries(["VOO"]) is None


def test_returns_none_when_connection_fails(monkeypatch):
    class _BrokenSession:
        def __call__(self):
            raise ConnectionError("db down")

    monkeypatch.setattr(sentiment_models, "SessionLocal", _BrokenSession())
    assert sentiment_models.get_latest_sentiment_summaries() is None
