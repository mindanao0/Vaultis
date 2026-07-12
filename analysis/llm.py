# -*- coding: utf-8 -*-
"""ชั้นกลางเรียก LLM สำหรับงาน "อธิบายผล" (ห้ามใช้คิดเลข — ดู AUDIT.md C3).

ลำดับการเลือกโมเดล:
1. Claude Haiku 4.5 (ANTHROPIC_API_KEY) — คุณภาพภาษาไทย/การอธิบายการเงินดีกว่า
2. Groq llama-3.3-70b (GROQ_API_KEY) — fallback ฟรี ถ้าไม่มี key ของ Anthropic
   หรือถ้าเรียก Anthropic แล้ว error

ทุกคำตอบตรวจ truncation: ถ้าโดนตัดที่ max_tokens จะ retry ด้วยงบโทเคนคูณสอง
หนึ่งครั้ง แล้วถ้ายังโดนตัดจะติดหมายเหตุท้ายข้อความ (ไม่ปล่อยผ่านเงียบ ๆ — AUDIT.md H6)
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]

ANTHROPIC_MODEL = "claude-haiku-4-5"
GROQ_MODEL = "llama-3.3-70b-versatile"

_TRUNCATION_NOTE = "\n\n(หมายเหตุ: ข้อความจากโมเดลถูกตัดเพราะเกินความยาวที่ตั้งไว้)"


def _anthropic_available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())


def _groq_available() -> bool:
    key = os.getenv("GROQ_API_KEY", "").strip()
    return bool(key) and key != "your_key_here"


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
) -> str:
    """เรียก LLM ให้เขียนคำอธิบาย คืนข้อความ; ล้มเหลวทั้งสอง provider จะ raise (ไม่เงียบ)."""
    load_dotenv(dotenv_path=ROOT_DIR / ".env", override=False)

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
