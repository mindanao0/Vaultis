# -*- coding: utf-8 -*-
"""ชั้นกลางเรียก LLM สำหรับงาน "อธิบายผล" (ห้ามใช้คิดเลข — ดู AUDIT.md C3).

**นโยบายค่าใช้จ่าย: LLM ปิดโดยดีฟอลต์**
เรียกได้เฉพาะเมื่อผู้ใช้ "กดปุ่มเอง" (``user_initiated=True``) เท่านั้น
งานอัตโนมัติทั้งหมด (cron, scheduler, GitHub Actions) จะไม่เรียก LLM
แต่ยังส่ง "ตัวเลขจากโมเดล" ตามปกติ ซึ่งไม่มีค่าใช้จ่าย

เปิดให้งานอัตโนมัติเรียก LLM ได้ด้วย env ``VAULTIS_LLM_AUTO=1`` (ผู้ใช้ต้องตั้งเอง)

ลำดับการเลือกโมเดล:
1. Claude Haiku 4.5 (ANTHROPIC_API_KEY) — **มีค่าใช้จ่ายตามจริง**
2. Groq llama-3.3-70b (GROQ_API_KEY) — ฟรี ใช้เมื่อไม่มีคีย์ Anthropic หรือ Anthropic ล้มเหลว

ทุกการเรียกจะ log จำนวนโทเคนและค่าใช้จ่ายโดยประมาณ เพื่อให้เห็นต้นทุนจริง
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[1]

ANTHROPIC_MODEL = "claude-haiku-4-5"
GROQ_MODEL = "llama-3.3-70b-versatile"

# ราคา Claude Haiku 4.5 (USD ต่อ 1 ล้านโทเคน) — ใช้ประมาณค่าใช้จ่ายเพื่อแสดงให้ผู้ใช้เห็น
_HAIKU_INPUT_USD_PER_MTOK = 1.0
_HAIKU_OUTPUT_USD_PER_MTOK = 5.0
_USD_TO_THB = 33.0

_AUTO_ENV = "VAULTIS_LLM_AUTO"
_TRUNCATION_NOTE = "\n\n(หมายเหตุ: ข้อความจากโมเดลถูกตัดเพราะเกินความยาวที่ตั้งไว้)"

AI_DISABLED_MESSAGE = (
    "🔒 บทวิเคราะห์ AI ปิดอยู่เพื่อคุมค่าใช้จ่าย — ตัวเลขและสัญญาณทั้งหมดด้านบน "
    "คำนวณจากโมเดลในระบบ (ไม่มีค่าใช้จ่าย)\n"
    "กดปุ่มวิเคราะห์ในหน้าเว็บเพื่อให้ AI อธิบายเพิ่มเติม "
    "(หรือตั้ง VAULTIS_LLM_AUTO=1 ถ้าต้องการให้งานอัตโนมัติเรียก AI ด้วย)"
)


class LLMDisabledError(RuntimeError):
    """LLM ถูกปิดไว้เพื่อคุมค่าใช้จ่าย — ผู้เรียกควรใช้ตัวเลขจากโมเดลแทน (ไม่ใช่ error จริง)."""


def auto_enabled() -> bool:
    """งานอัตโนมัติได้รับอนุญาตให้เรียก LLM หรือไม่ (ดีฟอลต์: ไม่)."""
    load_dotenv(dotenv_path=ROOT_DIR / ".env", override=False)
    return os.getenv(_AUTO_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _anthropic_available() -> bool:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    return bool(key) and key != "your_key_here"


def _groq_available() -> bool:
    key = os.getenv("GROQ_API_KEY", "").strip()
    return bool(key) and key != "your_key_here"


def _log_cost(provider: str, input_tokens: int, output_tokens: int) -> None:
    if provider == "anthropic":
        usd = (
            input_tokens / 1_000_000 * _HAIKU_INPUT_USD_PER_MTOK
            + output_tokens / 1_000_000 * _HAIKU_OUTPUT_USD_PER_MTOK
        )
        logger.info(
            "LLM %s: in=%d out=%d tokens ≈ $%.4f (~%.2f บาท)",
            ANTHROPIC_MODEL,
            input_tokens,
            output_tokens,
            usd,
            usd * _USD_TO_THB,
        )
    else:
        logger.info("LLM %s (ฟรี): in=%d out=%d tokens", GROQ_MODEL, input_tokens, output_tokens)


def _chat_anthropic(system: str, user: str, max_tokens: int, temperature: float) -> str:
    import anthropic

    client = anthropic.Anthropic()
    budget = max_tokens
    for attempt in range(2):
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=budget,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        usage = response.usage
        _log_cost("anthropic", usage.input_tokens, usage.output_tokens)

        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        ).strip()
        if response.stop_reason != "max_tokens":
            return text
        if attempt == 0:
            budget = max_tokens * 2
            continue
        return text + _TRUNCATION_NOTE
    return ""  # unreachable


def _chat_groq(system: str, user: str, max_tokens: int, temperature: float) -> str:
    from groq import Groq

    client = Groq(api_key=os.getenv("GROQ_API_KEY", "").strip())
    budget = max_tokens
    for attempt in range(2):
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            temperature=temperature,
            max_tokens=budget,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        if response.usage:
            _log_cost("groq", response.usage.prompt_tokens, response.usage.completion_tokens)

        choice = response.choices[0]
        text = (choice.message.content or "").strip()
        if choice.finish_reason != "length":
            return text
        if attempt == 0:
            budget = max_tokens * 2
            continue
        return text + _TRUNCATION_NOTE
    return ""  # unreachable


def chat_text(
    system: str,
    user: str,
    *,
    max_tokens: int = 1500,
    temperature: float = 0.2,
    user_initiated: bool = False,
) -> str:
    """เรียก LLM ให้เขียนคำอธิบาย.

    ``user_initiated=True`` ใช้ได้เฉพาะเมื่อผู้ใช้กดปุ่มเองในหน้าเว็บ/ยิง API เอง
    งานอัตโนมัติต้องปล่อยเป็น False → จะ raise ``LLMDisabledError``
    (เว้นแต่ตั้ง ``VAULTIS_LLM_AUTO=1``)
    """
    load_dotenv(dotenv_path=ROOT_DIR / ".env", override=False)

    if not user_initiated and not auto_enabled():
        raise LLMDisabledError(AI_DISABLED_MESSAGE)

    errors: list[str] = []
    if _anthropic_available():
        try:
            text = _chat_anthropic(system, user, max_tokens, temperature)
            if text:
                return text
            errors.append("anthropic: empty response")
        except Exception as exc:  # ลอง fallback ต่อ ไม่กลืนเงียบ
            errors.append(f"anthropic: {exc}")

    if _groq_available():
        try:
            text = _chat_groq(system, user, max_tokens, temperature)
            if text:
                return text
            errors.append("groq: empty response")
        except Exception as exc:
            errors.append(f"groq: {exc}")
    elif not errors:
        errors.append("ไม่ได้ตั้งค่า ANTHROPIC_API_KEY หรือ GROQ_API_KEY")

    raise RuntimeError("เรียก LLM ไม่สำเร็จ: " + " | ".join(errors))
