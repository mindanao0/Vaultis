# -*- coding: utf-8 -*-
"""คุมว่างาน sentiment เรียก LLM ผ่าน ``analysis/llm.py`` ทางเดียว.

เดิมไฟล์ sentiment_analyzer สร้าง ``Groq()`` เองตรง ๆ → ไม่ log โทเคน/ค่าใช้จ่าย
ไม่มี fallback ไป Claude และหลุดจากด่านคุมค่าใช้จ่ายที่ ``chat_text`` ถืออยู่

หมายเหตุ 2026-08-07 (AUDIT §B2.1): ``analyze_batch`` คืน ``BatchAnalysis``
(``rows`` + ``failed_batches`` + ``total_batches``) แทนลิสต์เปล่า ๆ แล้ว เพราะผู้เรียก
ต้องแยก "ข่าวน้อย" ออกจาก "เรียกโมเดลไม่สำเร็จ" ให้ได้ — เทสต์ในไฟล์นี้จึงอ่าน
``.rows`` แทนการเทียบกับลิสต์ตรง ๆ (พฤติกรรมที่ตรวจยังเป็นเรื่องเดิมทุกข้อ)
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest

from analysis import llm, sentiment_analyzer as sa

_ARTICLES = [{"title": f"ข่าว {i}", "description": "", "url": f"u{i}"} for i in range(12)]
_REPLY = '[{"title": "ข่าว 0", "sentiment": "positive", "confidence": 0.8, "reason": "ok"}]'


class TestGoesThroughChatText:
    def test_no_direct_groq_client_in_module(self):
        assert not hasattr(sa, "Groq")
        assert not hasattr(sa, "_GROQ_MODEL")

    def test_calls_chat_text_once_per_batch(self, monkeypatch):
        calls: list[dict] = []

        def _fake(system, user, **kwargs):
            calls.append({"system": system, "user": user, **kwargs})
            return _REPLY

        monkeypatch.setattr(sa, "chat_text", _fake)

        result = sa.analyze_batch(_ARTICLES, "QQQM")

        # 12 ข่าว ÷ batch 10 = 2 รอบ
        assert len(calls) == 2
        assert len(result.rows) == 2
        assert (result.failed_batches, result.total_batches) == (0, 2)
        assert all(c["user_initiated"] is False for c in calls)
        assert all("QQQM" in c["user"] for c in calls)

    def test_user_initiated_is_threaded_through(self, monkeypatch):
        seen: list[bool] = []
        monkeypatch.setattr(
            sa, "chat_text", lambda s, u, **kw: seen.append(kw["user_initiated"]) or _REPLY
        )

        sa.analyze_batch(_ARTICLES[:3], "VOO", user_initiated=True)

        assert seen == [True]


class TestCostGuardStillApplies:
    def test_disabled_llm_returns_empty_without_crashing(self, monkeypatch):
        def _blocked(*a, **k):
            raise llm.LLMDisabledError("ปิดอยู่")

        monkeypatch.setattr(sa, "chat_text", _blocked)

        assert sa.analyze_batch(_ARTICLES, "VOO").rows == []

    def test_disabled_llm_stops_after_first_batch(self, monkeypatch):
        """ปิดอยู่แล้วต้องเลิกทั้งชุด ไม่วนเรียกซ้ำทุก batch."""
        calls = {"n": 0}

        def _blocked(*a, **k):
            calls["n"] += 1
            raise llm.LLMDisabledError("ปิดอยู่")

        monkeypatch.setattr(sa, "chat_text", _blocked)
        sa.analyze_batch(_ARTICLES, "VOO")

        assert calls["n"] == 1


class TestFailuresAreNotSilent:
    def test_provider_error_is_logged_and_batch_skipped(self, monkeypatch, caplog):
        def _boom(*a, **k):
            raise RuntimeError("provider 500")

        monkeypatch.setattr(sa, "chat_text", _boom)
        monkeypatch.setattr(sa.time, "sleep", lambda _s: None)

        with caplog.at_level("WARNING"):
            result = sa.analyze_batch(_ARTICLES, "VOO")

        assert result.rows == []
        assert result.failed_batches == 2
        assert any("provider 500" in r.getMessage() for r in caplog.records)

    def test_partial_failure_keeps_successful_batches(self, monkeypatch):
        state = {"n": 0}

        def _flaky(*a, **k):
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("แบตช์แรกพัง")
            return _REPLY

        monkeypatch.setattr(sa, "chat_text", _flaky)
        monkeypatch.setattr(sa.time, "sleep", lambda _s: None)

        assert len(sa.analyze_batch(_ARTICLES, "VOO").rows) == 1


class TestUnparsableReplyIsDropped:
    def test_garbage_reply_yields_no_rows(self, monkeypatch):
        monkeypatch.setattr(sa, "chat_text", lambda s, u, **kw: "ไม่ใช่ JSON เลย")
        assert sa.analyze_batch(_ARTICLES[:2], "VOO").rows == []

    def test_no_articles_makes_no_llm_call(self, monkeypatch):
        def _explode(*a, **k):
            raise AssertionError("ไม่ควรเรียก LLM เมื่อไม่มีข่าว")

        monkeypatch.setattr(sa, "chat_text", _explode)
        assert sa.analyze_batch([], "VOO").rows == []
