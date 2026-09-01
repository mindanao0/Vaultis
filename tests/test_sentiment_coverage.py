# -*- coding: utf-8 -*-
"""B2 — งาน sentiment ต้องรายงาน "ดึงมาเท่าไร วิเคราะห์ได้เท่าไร" ไม่ใช่กลืนเงียบ.

อาการเดิม (AUDIT_2026-08-06 §B2):

* batch ที่เรียกโมเดลไม่สำเร็จหายเงียบ — ข่าว 30 ชิ้น (บวก10/ลบ10/กลาง10) ถ้า batch 2
  ล้ม จะเหลือ ``total_articles=20 score=+0.5 overall=positive`` ทั้งที่ความจริงคือ
  "กลาง ๆ" → ป้ายพลิกเพราะข้อมูลหาย ไม่ใช่เพราะข่าวเปลี่ยน
* ทุก batch ล้ม → ``overall='neutral' score=0.0`` แล้ว **เขียนลงฐานจริง 1 แถว**
  = ความล้มเหลวปลอมตัวเป็นข้อมูล
* ป้ายที่โมเดลไม่ได้ให้/ให้มาแบบที่ไม่รู้จัก (เช่น ``bullish``) ถูกนับเป็น ``neutral``
* ``run_sentiment_job`` เรียก ``get_news()`` แล้วอ่าน "ลิสต์ว่าง" เป็น "ไม่มีข่าว"
  ทั้งที่ ``get_news_with_status()`` บอกได้ว่าแหล่งข่าวล้มเหลวทั้งหมด (กฎ C1)

เทสต์ทั้งไฟล์ **ห้ามแตะ LLM จริง** — ทุกเคสวาง tripwire ที่ ``analysis.llm._chat_anthropic``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest

from analysis import llm
from analysis import sentiment_analyzer as sa
from analysis.sentiment_aggregator import aggregate_sentiment

_ARTICLES = [{"title": f"ข่าว {i}", "description": "", "url": f"u{i}"} for i in range(30)]


def _reply(batch: list[dict], label: str) -> str:
    return json.dumps(
        [
            {"title": a["title"], "sentiment": label, "confidence": 0.9, "reason": "r"}
            for a in batch
        ]
    )


def _rows(label: str, n: int) -> list[dict]:
    return [
        {"title": f"t{i}", "sentiment": label, "confidence": 0.9, "reason": ""}
        for i in range(n)
    ]


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    """กันเงินออกจริงทุกเคสในไฟล์นี้ ต่อให้ stub ตัวใดตัวหนึ่งหลุด."""

    def _tripwire(*a, **k):  # pragma: no cover - ต้องไม่ถูกเรียก
        raise AssertionError("เทสต์เรียก Anthropic จริง — ห้ามเด็ดขาด")

    monkeypatch.setattr(llm, "_chat_anthropic", _tripwire)
    monkeypatch.setattr(sa.time, "sleep", lambda _s: None)


# ---------------------------------------------------------------- aggregator


class TestAggregateReportsCoverage:
    def test_partial_failure_is_visible(self):
        """20 จาก 30 = ต้องอ่านออกจากผลลัพธ์ ไม่ใช่รู้แค่ใน log."""
        agg = aggregate_sentiment(_rows("positive", 10) + _rows("neutral", 10), fetched=30, failed_batches=1)

        assert agg["analyzed"] == 20
        assert agg["fetched"] == 30
        assert agg["coverage_pct"] == 66.7
        assert agg["complete"] is False
        assert agg["failed_batches"] == 1

    def test_full_run_is_marked_complete(self):
        agg = aggregate_sentiment(_rows("neutral", 30), fetched=30, failed_batches=0)

        assert agg["complete"] is True
        assert agg["coverage_pct"] == 100.0

    def test_zero_analyzed_is_unknown_not_neutral(self):
        """ทุก batch ล้ม = "ยังไม่รู้" ห้ามกลายเป็น neutral score 0.0."""
        agg = aggregate_sentiment([], fetched=30, failed_batches=3)

        assert agg["overall_sentiment"] == "unknown"
        assert agg["score"] is None
        assert agg["complete"] is False
        assert agg["coverage_pct"] == 0.0

    def test_no_news_at_all_has_no_coverage_ratio(self):
        agg = aggregate_sentiment([], fetched=0)

        assert agg["coverage_pct"] is None
        assert agg["overall_sentiment"] == "unknown"

    def test_fetched_defaults_to_analyzed(self):
        agg = aggregate_sentiment(_rows("positive", 4))

        assert agg["fetched"] == 4
        assert agg["complete"] is True
        assert agg["score"] == 1.0

    def test_counts_and_score_unchanged_for_clean_input(self):
        """สัญญาเดิมต้องไม่เพี้ยน — คีย์เก่ายังอยู่และค่าเท่าเดิม."""
        agg = aggregate_sentiment(_rows("positive", 10) + _rows("negative", 10) + _rows("neutral", 10), fetched=30)

        assert agg["total_articles"] == 30
        assert (agg["positive"], agg["negative"], agg["neutral"]) == (10, 10, 10)
        assert agg["score"] == 0.0
        assert agg["overall_sentiment"] == "neutral"
        assert agg["avg_confidence"] == 0.9


class TestUnknownLabelIsNotNeutral:
    def test_missing_sentiment_key_is_not_filled_in(self):
        assert sa._normalize_row({"title": "x"})["sentiment"] == ""

    def test_unrecognized_label_goes_to_unclassified(self):
        agg = aggregate_sentiment(_rows("bullish", 3), fetched=3)

        assert agg["unclassified"] == 3
        assert agg["neutral"] == 0
        assert agg["overall_sentiment"] == "unknown"
        assert agg["score"] is None


# ------------------------------------------------------------- analyze_batch


class TestAnalyzeBatchReportsFailedBatches:
    def test_failed_batch_count_is_returned(self, monkeypatch):
        state = {"i": 0}

        def _flaky(system, user, **kw):
            i = state["i"]
            state["i"] += 1
            if i == 1:
                raise RuntimeError("429 rate_limit_error")
            return _reply(_ARTICLES[i * 10 : (i + 1) * 10], "positive")

        monkeypatch.setattr(sa, "chat_text", _flaky)

        result = sa.analyze_batch(_ARTICLES, "VOO")

        assert result.failed_batches == 1
        assert result.total_batches == 3
        assert len(result.rows) == 20

    def test_every_batch_failing_is_not_an_empty_success(self, monkeypatch):
        monkeypatch.setattr(sa, "chat_text", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("500")))

        result = sa.analyze_batch(_ARTICLES, "VOO")

        assert result.rows == []
        assert result.failed_batches == 3

    def test_llm_disabled_is_not_counted_as_failure(self, monkeypatch):
        def _blocked(*a, **k):
            raise llm.LLMDisabledError("ปิดอยู่")

        monkeypatch.setattr(sa, "chat_text", _blocked)

        result = sa.analyze_batch(_ARTICLES, "VOO")

        assert result.rows == []
        assert result.failed_batches == 0


# ----------------------------------------------------------- run_sentiment_job


class _FakeSession:
    def __init__(self, sink: list):
        self.sink = sink

    def add(self, obj):
        self.sink.append(obj)

    def commit(self):
        pass

    def rollback(self):  # pragma: no cover - ไม่ควรถูกเรียกในเคสเหล่านี้
        pass

    def close(self):
        pass


@pytest.fixture
def job_env(monkeypatch):
    """เปิด flag แบบ in-process + ปลอมฐานข้อมูล + ห้ามใช้ get_news ตัวเก่า."""
    added: list = []
    monkeypatch.setattr(llm, "auto_enabled", lambda: True)
    monkeypatch.setattr(sa, "SessionLocal", lambda: _FakeSession(added))

    def _forbidden(*a, **k):
        raise AssertionError("ต้องใช้ get_news_with_status() — get_news() แยก 'ดึงไม่ได้' ออกจาก 'ไม่มีข่าว' ไม่ได้")

    monkeypatch.setattr(sa, "get_news", _forbidden, raising=False)
    return added


def _status(items, *, has_error=False, all_failed=False, sources=None):
    return {
        "symbol": "VOO",
        "items": items,
        "sources": sources or [],
        "news_count": len(items),
        "social_count": 0,
        "has_error": has_error,
        "all_news_sources_failed": all_failed,
    }


class TestJobDoesNotStoreFabricatedSummaries:
    def test_no_summary_row_when_nothing_analyzed(self, monkeypatch, capsys, job_env):
        from db.sentiment_models import SentimentSummary

        monkeypatch.setattr(sa, "get_news_with_status", lambda s: _status(_ARTICLES), raising=False)
        monkeypatch.setattr(sa, "chat_text", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("500")))

        sa.run_sentiment_job(["VOO"])

        out = capsys.readouterr().out
        assert not [o for o in job_env if isinstance(o, SentimentSummary)]
        assert "0 จาก 30" in out

    def test_partial_run_reports_coverage(self, monkeypatch, capsys, job_env):
        from db.sentiment_models import SentimentSummary

        state = {"i": 0}

        def _flaky(system, user, **kw):
            i = state["i"]
            state["i"] += 1
            if i == 1:
                raise RuntimeError("429")
            return _reply(_ARTICLES[i * 10 : (i + 1) * 10], "positive" if i == 0 else "neutral")

        monkeypatch.setattr(sa, "get_news_with_status", lambda s: _status(_ARTICLES), raising=False)
        monkeypatch.setattr(sa, "chat_text", _flaky)

        sa.run_sentiment_job(["VOO"])

        out = capsys.readouterr().out
        summaries = [o for o in job_env if isinstance(o, SentimentSummary)]
        assert len(summaries) == 1
        assert summaries[0].total_articles == 20
        assert "วิเคราะห์ได้ 20 จาก 30" in out


class TestJobSeparatesFetchFailureFromEmptyNews:
    def test_all_sources_failed_is_not_reported_as_no_news(self, monkeypatch, capsys, job_env):
        monkeypatch.setattr(
            sa,
            "get_news_with_status",
            lambda s: _status(
                [],
                has_error=True,
                all_failed=True,
                sources=[{"name": "yahoo_rss", "kind": "news", "status": "error", "count": 0, "detail": "timeout"}],
            ),
            raising=False,
        )
        monkeypatch.setattr(
            sa, "chat_text", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ไม่ควรเรียก LLM"))
        )

        sa.run_sentiment_job(["VOO"])

        out = capsys.readouterr().out
        assert "ไม่สำเร็จ" in out
        assert "yahoo_rss" in out
        assert "no news" not in out

    def test_genuinely_empty_news_still_says_no_news(self, monkeypatch, capsys, job_env):
        monkeypatch.setattr(sa, "get_news_with_status", lambda s: _status([]), raising=False)
        monkeypatch.setattr(
            sa, "chat_text", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ไม่ควรเรียก LLM"))
        )

        sa.run_sentiment_job(["VOO"])

        out = capsys.readouterr().out
        assert "ไม่มีข่าว" in out
        assert "ไม่สำเร็จ" not in out
