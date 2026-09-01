# -*- coding: utf-8 -*-
"""AUDIT_ROUND2_2026-08-07 — คำขอที่ยิงไป Anthropic ต้องปิด ``thinking`` เสมอ.

**อาการ** ``analysis/llm.py`` เรียก ``client.messages.create(...)`` โดยไม่ส่งฟิลด์
``thinking`` เลย บน Sonnet 5 การไม่ส่ง = เปิด adaptive thinking (เปลี่ยนจาก Sonnet 4.6
ที่การไม่ส่ง = ไม่คิด) และ ``max_tokens`` บน Sonnet 5 เป็นเพดาน**รวม**ของ
thinking + ข้อความตอบ

**ผลกระทบ** ผู้เรียกในโปรเจกต์นี้ขอโควตาแค่ 512–2500 (ดีฟอลต์ 1500) thinking จึงกิน
โควตาจนไม่เหลือที่ให้ข้อความได้ → ``stop_reason="max_tokens"`` โดยไม่มีเนื้อหา →
โค้ด retry ที่ 2 เท่า ทั้งที่ **รอบแรกที่ถูกตัดยังถูกเรียกเก็บเงินอยู่** = จ่ายซ้ำทุกครั้ง
และรอบสองก็ยังพลาดได้อีก (RuntimeError) นี่คือเงินจริงที่ออกจากบัญชีผู้ใช้

**ทำไมปิดได้โดยไม่เสียคุณภาพ** กฎ "AI explains, code computes" ของโปรเจกต์แปลว่า
ตัวเลขทุกตัวคำนวณเสร็จใน Python แล้ว LLM เขียนแค่คำอธิบายภาษาไทย ไม่ต้องให้เหตุผลหลายขั้น

**ห้ามส่ง ``budget_tokens``** — ถูกถอดออกจาก Sonnet 5 แล้ว ส่งไปได้ HTTP 400

ทุกเคสใช้โมดูล ``anthropic`` ปลอมที่ยัดใน ``sys.modules`` — **ไม่มีการเรียก API จริง
และไม่มีเงินออกจริง** (คอนเทนเนอร์เทสต์โหลด ``.env`` ที่มีคีย์จริงอยู่)
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from analysis import llm  # noqa: E402


# ------------------------------------------------------------------ stub ปลอม


class _FakeUsage:
    def __init__(self, input_tokens: int = 100, output_tokens: int = 20) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeTextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeResponse:
    def __init__(self, stop_reason: str, blocks: list) -> None:
        self.stop_reason = stop_reason
        self.content = blocks
        self.usage = _FakeUsage()


class _FakeMessages:
    def __init__(self, responses: list[_FakeResponse], calls: list[dict]) -> None:
        self._responses = list(responses)
        self._calls = calls

    def create(self, **kwargs):
        self._calls.append(kwargs)
        if not self._responses:
            raise AssertionError(f"stub เตรียมคำตอบไม่พอ — ถูกเรียกครั้งที่ {len(self._calls)}")
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses: list[_FakeResponse], calls: list[dict]) -> None:
        self.messages = _FakeMessages(responses, calls)


def _install_fake_anthropic(monkeypatch, responses: list[_FakeResponse]) -> list[dict]:
    """แทนโมดูล ``anthropic`` ทั้งก้อน แล้วคืน list ที่บันทึกทุก request ที่ยิงออกไป."""
    calls: list[dict] = []
    module = types.ModuleType("anthropic")
    module.Anthropic = lambda *args, **kwargs: _FakeClient(responses, calls)
    monkeypatch.setitem(sys.modules, "anthropic", module)
    return calls


@pytest.fixture(autouse=True)
def _llm_enabled(monkeypatch):
    """ผ่านด่านคุมค่าใช้จ่าย เพื่อทดสอบชั้นถัดไป (ยังไม่มีเงินไหลออกจริงเพราะ client ปลอม)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.delenv("VAULTIS_LLM_AUTO", raising=False)


# ------------------------------------------------------- ตัวช่วยอ่านคำขอที่ยิงจริง


def _thinking_of(kwargs: dict):
    """ค่า ``thinking`` ที่ถูกส่งไปจริง ไม่ว่าจะผ่านพารามิเตอร์ typed หรือ ``extra_body``.

    **ยอมรับสองทางได้ก็ต่อเมื่อมี ``test_ช่องทางส่ง_thinking_ต้องเป็นช่องที่ SDK จริงรับได้``
    คุมอยู่** — ไม่งั้นตัวช่วยนี้กลายเป็นรูโหว่: stub ในไฟล์นี้เป็น ``create(**kwargs)``
    ที่รับทุกอย่าง ถ้าใครย้ายไปส่ง ``thinking=`` แบบ typed เทสต์ทุกตัวจะยังเขียว
    แต่โปรดักชันพังทุกการเรียก เพราะ ``anthropic==0.42.0`` ไม่มีพารามิเตอร์นี้
    และไม่มี ``**kwargs`` ⇒ ``TypeError`` → ``RuntimeError`` ⇒ ปุ่ม AI ตายทั้งระบบ
    """
    if "thinking" in kwargs:
        return kwargs["thinking"]
    return (kwargs.get("extra_body") or {}).get("thinking")


def test_ช่องทางส่ง_thinking_ต้องเป็นช่องที่_SDK_จริงรับได้(monkeypatch) -> None:
    """กลไกที่โค้ดใช้ส่ง ``thinking`` ต้องผ่าน signature ของ SDK ที่ติดตั้งจริง.

    เทสต์อื่นในไฟล์นี้ใช้โมดูล ``anthropic`` ปลอมที่รับทุกคีย์เวิร์ด จึงพิสูจน์ได้แค่ว่า
    "ค่าถูกส่งไป" ไม่ได้พิสูจน์ว่า "ส่งด้วยช่องที่ของจริงรับ" — ช่องว่างนั้นปิดตรงนี้
    โดยตรวจกับ ``inspect.signature`` ของ SDK ตัวจริงที่ปักหมุดไว้
    """
    import inspect

    import anthropic as real_anthropic  # SDK จริง ไม่ใช่ตัวปลอม

    params = inspect.signature(real_anthropic.Anthropic().messages.create).parameters
    accepts_var_kw = any(p.kind is p.VAR_KEYWORD for p in params.values())

    calls = _install_fake_anthropic(
        monkeypatch, [_FakeResponse("end_turn", [_FakeTextBlock("คำอธิบาย")])]
    )
    llm.chat_text("system", "user", max_tokens=1500, user_initiated=True)
    sent = calls[0]

    if "thinking" in sent:
        assert "thinking" in params or accepts_var_kw, (
            "โค้ดส่ง thinking= เป็นพารามิเตอร์ typed แต่ SDK ที่ติดตั้งจริง "
            f"({real_anthropic.__version__}) ไม่มีพารามิเตอร์นี้และไม่รับ **kwargs "
            "⇒ โปรดักชันจะได้ TypeError ทุกการเรียก ให้ส่งผ่าน extra_body แทน"
        )
    else:
        assert "extra_body" in params, (
            "โค้ดส่ง thinking ผ่าน extra_body แต่ SDK ที่ติดตั้งจริงไม่มีช่องนี้"
        )


def _find_key(node, key: str) -> bool:
    """มีคีย์ชื่อนี้อยู่ที่ระดับไหนก็ตามในโครงสร้างคำขอหรือไม่."""
    if isinstance(node, dict):
        return key in node or any(_find_key(value, key) for value in node.values())
    if isinstance(node, (list, tuple)):
        return any(_find_key(item, key) for item in node)
    return False


# --------------------------------------------------------------- ตัวบั๊กจริง


class TestThinkingIsDisabled:
    def test_first_request_disables_thinking(self, monkeypatch):
        calls = _install_fake_anthropic(
            monkeypatch, [_FakeResponse("end_turn", [_FakeTextBlock("คำอธิบาย")])]
        )

        llm.chat_text("system", "user", max_tokens=1500, user_initiated=True)

        assert len(calls) == 1
        assert _thinking_of(calls[0]) == {"type": "disabled"}, (
            "ไม่ได้ปิด thinking — บน Sonnet 5 การไม่ส่งฟิลด์นี้ = เปิด adaptive thinking "
            "ซึ่งกิน max_tokens ก้อนเดียวกับข้อความตอบ"
        )

    def test_retry_request_also_disables_thinking(self, monkeypatch):
        """รอบ retry ก็ต้องปิดด้วย — ไม่งั้นรอบที่จ่ายแพงกว่ากลับเป็นรอบที่เสี่ยงสุด."""
        calls = _install_fake_anthropic(
            monkeypatch,
            [
                _FakeResponse("max_tokens", [_FakeTextBlock("ครึ่งเดียว")]),
                _FakeResponse("end_turn", [_FakeTextBlock("เต็ม")]),
            ],
        )

        llm.chat_text("system", "user", max_tokens=1000, user_initiated=True)

        assert len(calls) == 2
        for index, kwargs in enumerate(calls):
            assert _thinking_of(kwargs) == {"type": "disabled"}, f"คำขอที่ {index + 1} ไม่ได้ปิด"

    def test_low_level_call_disables_thinking(self, monkeypatch):
        """เรียก ``_chat_anthropic`` ตรง ๆ ก็ต้องปิด (ไม่ได้อาศัย chat_text ห่อให้)."""
        calls = _install_fake_anthropic(
            monkeypatch, [_FakeResponse("end_turn", [_FakeTextBlock("ok")])]
        )

        llm._chat_anthropic("system", "user", 512)

        assert _thinking_of(calls[0]) == {"type": "disabled"}

    def test_constant_is_the_single_definition(self):
        """ค่าคงที่เดียวสำหรับ "ปิด thinking" — ห้ามเขียน literal ซ้ำที่อื่น."""
        assert llm._THINKING_DISABLED == {"type": "disabled"}


class TestBudgetTokensNeverSent:
    """``budget_tokens`` ถูกถอดออกจาก Sonnet 5 แล้ว — ส่งไป = HTTP 400 ทั้งคำขอ."""

    def test_budget_tokens_absent_from_every_request(self, monkeypatch):
        calls = _install_fake_anthropic(
            monkeypatch,
            [
                _FakeResponse("max_tokens", [_FakeTextBlock("ครึ่งเดียว")]),
                _FakeResponse("end_turn", [_FakeTextBlock("เต็ม")]),
            ],
        )

        llm.chat_text("system", "user", max_tokens=800, user_initiated=True)

        for index, kwargs in enumerate(calls):
            assert not _find_key(kwargs, "budget_tokens"), (
                f"คำขอที่ {index + 1} ส่ง budget_tokens ไปด้วย — Sonnet 5 ตอบ 400: {kwargs!r}"
            )

    def test_disabled_thinking_carries_no_other_key(self):
        """``{"type": "disabled"}`` เท่านั้น — คีย์เกินมา (โดยเฉพาะ budget_tokens) = 400."""
        assert set(llm._THINKING_DISABLED) == {"type"}


class TestSamplingParamsStillAbsent:
    """ด่านเดิมที่ต้องไม่หลุดไปพร้อมกัน: Sonnet 5 ตอบ 400 ถ้าส่ง temperature/top_p/top_k."""

    def test_no_sampling_params_even_when_caller_passes_temperature(self, monkeypatch):
        calls = _install_fake_anthropic(
            monkeypatch, [_FakeResponse("end_turn", [_FakeTextBlock("ok")])]
        )

        llm.chat_text("system", "user", temperature=0.7, user_initiated=True)

        for key in ("temperature", "top_p", "top_k"):
            assert not _find_key(calls[0], key), f"ส่ง {key} ไปด้วย — Sonnet 5 ตอบ 400"


class TestBudgetStillHonoured:
    """ปิด thinking แล้วโควตาที่ผู้เรียกขอต้องยังถูกส่งไปตามเดิม (ไม่แอบเปลี่ยนพฤติกรรม)."""

    def test_max_tokens_passed_through_and_doubled_on_retry(self, monkeypatch):
        calls = _install_fake_anthropic(
            monkeypatch,
            [
                _FakeResponse("max_tokens", [_FakeTextBlock("ครึ่ง")]),
                _FakeResponse("end_turn", [_FakeTextBlock("เต็ม")]),
            ],
        )

        llm.chat_text("system", "user", max_tokens=1234, user_initiated=True)

        assert calls[0]["max_tokens"] == 1234
        assert calls[1]["max_tokens"] == 2468


class TestTruncationMessageIsAccurate:
    """ข้อความ error ต้องไม่ชี้สาเหตุผิดอีกต่อไป.

    thinking ถูกปิดที่ต้นทางแล้ว ดังนั้น "โควตาหมดโดยไม่มีเนื้อหา" เหลือความหมายเดียว:
    prompt/คำตอบยาวเกินโควตาจริง ๆ — ข้อความต้องบอกแบบนั้น ไม่ใช่ปล่อยให้คนอ่านไปไล่หา
    สาเหตุที่ถูกกำจัดไปแล้ว
    """

    def test_message_states_thinking_is_already_off(self, monkeypatch):
        _install_fake_anthropic(
            monkeypatch,
            [_FakeResponse("max_tokens", []), _FakeResponse("max_tokens", [])],
        )

        with pytest.raises(RuntimeError) as exc_info:
            llm.chat_text("system", "user", max_tokens=600, user_initiated=True)

        message = str(exc_info.value)
        assert "thinking ถูกปิดไว้แล้ว" in message, message
        assert "stop_reason=max_tokens" in message, message
        assert "1200" in message, "ต้องบอกโควตารอบสุดท้ายที่ใช้จริง"


class TestCostLoggingNeverBreaksAPaidCall:
    """เงินออกไปแล้ว — การบันทึกต้นทุนต้องไม่ทำให้คำขอที่สำเร็จพังตาม.

    เดิม ``_chat_anthropic`` อ่าน ``response.usage.input_tokens`` ตรง ๆ ถ้าผู้ให้บริการ
    ไม่ส่ง ``usage`` กลับมา จะได้ ``AttributeError`` แล้ว ``chat_text`` ห่อเป็น
    ``RuntimeError`` ⇒ **จ่ายเงินไปแล้วแต่ผู้ใช้ได้ error แทนคำอธิบาย** ทั้งที่โมเดล
    ตอบมาครบ — ตรงข้ามกับกฎที่ ``log_anthropic_usage()`` ถูกเขียนขึ้นมาบังคับใน
    คอมมิตเดียวกัน (เส้นทาง slip OCR ตรึงไว้แล้วว่าต้อง WARNING แล้วไปต่อ)
    """

    def test_ไม่มี_usage_ต้องยังได้คำตอบและเตือนว่าไม่ทราบต้นทุน(self, monkeypatch, caplog) -> None:
        response = _FakeResponse("end_turn", [_FakeTextBlock("คำอธิบายภาษาไทย")])
        del response.usage  # ผู้ให้บริการไม่ส่งจำนวนโทเคนกลับมา

        _install_fake_anthropic(monkeypatch, [response])

        with caplog.at_level("WARNING"):
            text = llm.chat_text("system", "user", max_tokens=1500, user_initiated=True)

        assert text == "คำอธิบายภาษาไทย", "คำตอบที่จ่ายเงินซื้อมาแล้วต้องไม่หายไป"
        assert any(
            "ไม่ได้รับจำนวนโทเคน" in record.message or "ไม่ได้รับจำนวนโทเคน" in record.getMessage()
            for record in caplog.records
        ), "ต้องเตือนว่าไม่ทราบต้นทุนรอบนี้ (ห้ามเงียบ และห้ามบันทึกเป็น 0)"

    def test_ต้นทุนที่ไม่ทราบต้องไม่ถูกบันทึกเป็นศูนย์(self, monkeypatch, caplog) -> None:
        response = _FakeResponse("end_turn", [_FakeTextBlock("ok")])
        del response.usage
        _install_fake_anthropic(monkeypatch, [response])

        with caplog.at_level("INFO"):
            llm.chat_text("system", "user", max_tokens=1500, user_initiated=True)

        logged = " ".join(record.getMessage() for record in caplog.records)
        assert "$0.0000" not in logged, "ไม่ทราบต้นทุน ≠ ต้นทุน 0 (กฎ C1)"
        assert "in=0 out=0" not in logged
