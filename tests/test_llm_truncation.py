# -*- coding: utf-8 -*-
"""เทสต์เส้นทาง "โมเดลตอบไม่จบ" ของ ``analysis/llm.py``.

หลักที่ต้องคุ้มครอง: **คำตอบว่างเปล่าห้ามถูกส่งออกเป็นผลสำเร็จ**
เดิม ``_chat_anthropic`` คืน ``text + _TRUNCATION_NOTE`` ในรอบที่สอง ทำให้
``"" + หมายเหตุ`` เป็น truthy เสมอ → ด่าน ``if not text: raise`` ใน ``chat_text``
ยิงไม่ได้เลย ผู้เรียกจึงเข้าใจว่าสำเร็จ ทั้งที่จ่ายเงินไป 2 รอบแล้วได้แต่หมายเหตุ
(เกิดจริงเมื่อโมเดลใช้โควตาหมดไปกับ thinking แล้วไม่เหลือที่ให้ข้อความตอบ)

ทุกเคสใช้ client ปลอมที่ยัดใน ``sys.modules`` — **ไม่มีการเรียก API จริง**
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest

from analysis import llm


# ---------------------------------------------------------------- stub client


class _FakeUsage:
    def __init__(self, input_tokens: int = 120, output_tokens: int = 0) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeTextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeThinkingBlock:
    """บล็อกที่ไม่ใช่ text — ตัวแทนของ thinking ที่กินโควตาจนหมด."""

    type = "thinking"


class _FakeResponse:
    def __init__(self, stop_reason: str, blocks: list, output_tokens: int = 0) -> None:
        self.stop_reason = stop_reason
        self.content = blocks
        self.usage = _FakeUsage(output_tokens=output_tokens)


class _FakeMessages:
    def __init__(self, responses: list[_FakeResponse], calls: list[dict]) -> None:
        self._responses = list(responses)
        self._calls = calls

    def create(self, **kwargs):
        self._calls.append(kwargs)
        if not self._responses:
            raise AssertionError(
                f"stub เตรียมคำตอบไว้ไม่พอ — ถูกเรียกครั้งที่ {len(self._calls)}"
            )
        return self._responses.pop(0)


class _FakeAnthropicClient:
    def __init__(self, responses: list[_FakeResponse], calls: list[dict]) -> None:
        self.messages = _FakeMessages(responses, calls)


def _install_fake_anthropic(monkeypatch, responses: list[_FakeResponse]) -> list[dict]:
    """แทนโมดูล ``anthropic`` ทั้งก้อน แล้วคืน list ที่บันทึกทุก request ที่ยิงออกไป."""
    calls: list[dict] = []
    module = types.ModuleType("anthropic")
    module.Anthropic = lambda *args, **kwargs: _FakeAnthropicClient(responses, calls)
    monkeypatch.setitem(sys.modules, "anthropic", module)
    return calls


@pytest.fixture(autouse=True)
def _llm_enabled(monkeypatch):
    """เปิดด่านคุมค่าใช้จ่ายให้ผ่าน เพื่อทดสอบชั้นที่อยู่ถัดไป (ยังไม่มีเงินไหลออกจริง)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.delenv("VAULTIS_LLM_AUTO", raising=False)


# ------------------------------------------------------------------ ตัวบั๊กจริง


class TestEmptyTruncatedResponse:
    """โควตาหมดโดยไม่มีเนื้อหา = ล้มเหลว ห้ามคืนหมายเหตุเปล่า ๆ เป็นผลสำเร็จ."""

    def test_empty_content_both_attempts_raises(self, monkeypatch):
        calls = _install_fake_anthropic(
            monkeypatch,
            [
                _FakeResponse("max_tokens", [_FakeThinkingBlock()], output_tokens=1500),
                _FakeResponse("max_tokens", [_FakeThinkingBlock()], output_tokens=3000),
            ],
        )

        with pytest.raises(RuntimeError) as exc_info:
            llm.chat_text("system", "user", max_tokens=1500, user_initiated=True)

        message = str(exc_info.value)
        assert not isinstance(exc_info.value, llm.LLMDisabledError), "ไม่ใช่กรณีถูกปิดไว้"
        assert llm._TRUNCATION_NOTE.strip() not in message, "ห้ามคืนหมายเหตุแทนคำตอบ"
        # ข้อความต้องอ่านออกและบอกทางแก้
        assert "stop_reason=max_tokens" in message
        assert "เพิ่ม max_tokens" in message
        assert "3000" in message, "ต้องบอกโควตาที่ใช้ไปจริงในรอบสุดท้าย (2 เท่าของ 1500)"
        assert len(calls) == 2, "ต้องเห็นว่าจ่ายเงินไป 2 รอบ"

    def test_whitespace_only_content_also_raises(self, monkeypatch):
        """ข้อความมีแต่ช่องว่าง = ว่างเปล่าเหมือนกัน (``.strip()`` ทำให้เหลือ '')."""
        _install_fake_anthropic(
            monkeypatch,
            [
                _FakeResponse("max_tokens", [_FakeTextBlock("  \n ")]),
                _FakeResponse("max_tokens", [_FakeTextBlock("\n\t")]),
            ],
        )

        with pytest.raises(RuntimeError, match="stop_reason=max_tokens"):
            llm.chat_text("system", "user", max_tokens=800, user_initiated=True)

    def test_direct_call_reports_final_budget(self, monkeypatch):
        """เรียก ``_chat_anthropic`` ตรง ๆ ต้องได้ข้อความเดียวกัน (ไม่โดน chat_text ห่อ)."""
        _install_fake_anthropic(
            monkeypatch,
            [
                _FakeResponse("max_tokens", []),
                _FakeResponse("max_tokens", []),
            ],
        )

        with pytest.raises(RuntimeError) as exc_info:
            llm._chat_anthropic("system", "user", 500)

        assert "1000" in str(exc_info.value), "โควตารอบสุดท้าย = 2 เท่าของที่ขอมา"


class TestPartialTruncatedResponse:
    """ถูกตัดแต่ยังมีเนื้อหา = ส่งต่อได้ พร้อมหมายเหตุตามเดิม."""

    def test_partial_text_returns_with_note(self, monkeypatch):
        calls = _install_fake_anthropic(
            monkeypatch,
            [
                _FakeResponse("max_tokens", [_FakeTextBlock("ครึ่งแรก")]),
                _FakeResponse("max_tokens", [_FakeTextBlock("ครึ่งแรกและอีกหน่อย")]),
            ],
        )

        result = llm.chat_text("system", "user", max_tokens=1000, user_initiated=True)

        assert result.startswith("ครึ่งแรกและอีกหน่อย")
        assert result.endswith(llm._TRUNCATION_NOTE)
        assert calls[1]["max_tokens"] == 2 * calls[0]["max_tokens"], "รอบสองต้องเพิ่มโควตา"

    def test_second_attempt_completes_without_note(self, monkeypatch):
        _install_fake_anthropic(
            monkeypatch,
            [
                _FakeResponse("max_tokens", [_FakeTextBlock("ยังไม่จบ")]),
                _FakeResponse("end_turn", [_FakeTextBlock("คำอธิบายเต็ม")]),
            ],
        )

        result = llm.chat_text("system", "user", max_tokens=1000, user_initiated=True)

        assert result == "คำอธิบายเต็ม"
        assert llm._TRUNCATION_NOTE not in result


class TestNormalResponse:
    def test_single_call_when_not_truncated(self, monkeypatch):
        calls = _install_fake_anthropic(
            monkeypatch,
            [_FakeResponse("end_turn", [_FakeTextBlock("สรุปสั้น ๆ")], output_tokens=42)],
        )

        result = llm.chat_text("system", "user", user_initiated=True)

        assert result == "สรุปสั้น ๆ"
        assert len(calls) == 1, "ไม่ถูกตัด = ต้องไม่ยิงซ้ำ (ไม่จ่ายซ้ำ)"

    def test_empty_end_turn_still_fails_loudly(self, monkeypatch):
        """จบเองแต่ไม่มีเนื้อหา = ด่านเดิมใน ``chat_text`` ต้องยังทำงาน."""
        _install_fake_anthropic(monkeypatch, [_FakeResponse("end_turn", [])])

        with pytest.raises(RuntimeError, match="empty response"):
            llm.chat_text("system", "user", user_initiated=True)


class TestCallersSurviveRuntimeError:
    """``LLMDisabledError`` เป็นลูกของ ``RuntimeError`` — ที่ไหน catch เฉพาะตัวลูก
    จะไม่ครอบคลุมความล้มเหลวจริงของ LLM งานอัตโนมัติต้องไม่พังทั้งงาน
    เพราะคำอธิบายหาย (ตัวเลขคำนวณเสร็จแล้วใน Python ไม่ได้พึ่ง LLM)."""

    def test_monthly_advice_keeps_numbers_when_llm_fails(self, monkeypatch):
        import pandas as pd

        from analysis import ai_advisor

        scores = [
            {"ticker": "VOO", "data_ok": True, "total_pct": 70.0, "price": 690.0,
             "ma50": 680.0, "ma200": 650.0, "rsi": 55.0, "signal": "Strong Buy"},
            {"ticker": "GLDM", "data_ok": True, "total_pct": 30.0, "price": 80.0,
             "ma50": 85.0, "ma200": 88.0, "rsi": 43.0, "signal": "Neutral"},
        ]
        monkeypatch.setattr(ai_advisor, "get_tickers", lambda: ["VOO", "GLDM"])
        monkeypatch.setattr("analysis.financial_model.build_etf_scores", lambda t: scores)
        monkeypatch.setattr("analysis.macro.get_macro_snapshot", lambda: {"vix": 15.0})
        monkeypatch.setattr("portfolio.tracker.get_portfolio_summary", lambda: pd.DataFrame())
        monkeypatch.setattr(
            ai_advisor, "load_config", lambda: {"notifications": {"discord_webhook_url": ""}}
        )

        def _llm_failed(*args, **kwargs):
            raise RuntimeError("เรียก LLM ไม่สำเร็จ: anthropic: โมเดลใช้โควตา 3000 tokens หมด")

        monkeypatch.setattr(ai_advisor, "get_ai_advice", _llm_failed)

        result = ai_advisor.get_monthly_advice(
            budget_thb=5000, send_discord=False, user_initiated=True
        )

        assert result["ai_used"] is False
        assert result["allocation"], "แผนจัดสรรต้องยังคำนวณให้ แม้ AI ล้ม"
        assert sum(i["amount_thb"] for i in result["allocation"].values()) <= 5000
        # ต้องไม่เงียบ — ผู้ใช้ต้องเห็นว่าคำอธิบายหายไปเพราะอะไร
        assert "ไม่สำเร็จ" in result["advice_text"]
