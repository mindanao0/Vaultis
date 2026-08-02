# -*- coding: utf-8 -*-
"""ชั้นกลางเรียก LLM สำหรับงาน "อธิบายผล" (ห้ามใช้คิดเลข — ดู AUDIT.md C3).

**นโยบายค่าใช้จ่าย: LLM ปิดโดยดีฟอลต์**
เรียกได้เฉพาะเมื่อผู้ใช้ "กดปุ่มเอง" (``user_initiated=True``) เท่านั้น
งานอัตโนมัติทั้งหมด (cron, scheduler, GitHub Actions) จะไม่เรียก LLM
แต่ยังส่ง "ตัวเลขจากโมเดล" ตามปกติ ซึ่งไม่มีค่าใช้จ่าย

เปิดให้งานอัตโนมัติเรียก LLM ได้ด้วย env ``VAULTIS_LLM_AUTO=1`` (ผู้ใช้ต้องตั้งเอง)

ผู้ให้บริการเดียว: Claude ผ่าน ``ANTHROPIC_API_KEY`` — **มีค่าใช้จ่ายตามจริง**

เดิมมี Groq llama-3.3-70b เป็น fallback ฟรี แต่ถอดออกแล้ว (มติผู้ใช้ 2026-08-02)
เหตุผลเชิงระบบ: การตกไปโมเดลอื่นเงียบ ๆ ทำให้ผู้ใช้ได้คำอธิบายจากโมเดลที่อ่อนกว่า
โดยไม่รู้ตัว ซึ่งขัดหลัก fail-loud ของโปรเจกต์ — ตอนนี้ Anthropic ล้มเหลว = แจ้งชัดเจน
และตัวเลข/สัญญาณทั้งหมดยังทำงานปกติเพราะคำนวณในโค้ด ไม่ได้พึ่ง LLM

ทุกการเรียกจะ log จำนวนโทเคนและค่าใช้จ่ายโดยประมาณ เพื่อให้เห็นต้นทุนจริง
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[1]

ANTHROPIC_MODEL = "claude-sonnet-5"

# ราคา (USD ต่อ 1 ล้านโทเคน) input/output — ใช้ประมาณค่าใช้จ่ายเพื่อแสดงให้ผู้ใช้เห็น
# ต้องอัปเดตพร้อมกับ ANTHROPIC_MODEL เสมอ ไม่งั้น log จะรายงานต้นทุนผิด
# หมายเหตุ: Sonnet 5 มีราคาแนะนำตัว $2/$10 ถึง 31 ส.ค. 2026 — ตารางนี้ใช้ราคาเต็ม
# โดยตั้งใจ เพื่อไม่ให้ตัวเลขที่โชว์ "ต่ำกว่าจริง" หลังหมดโปรโมชัน
_MODEL_PRICES_USD_PER_MTOK = {
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
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


def _log_cost(input_tokens: int, output_tokens: int) -> None:
    price_in, price_out = _MODEL_PRICES_USD_PER_MTOK.get(ANTHROPIC_MODEL, (0.0, 0.0))
    if not price_in and not price_out:
        # ไม่รู้ราคาของโมเดลนี้ = ห้ามเดาเป็นเลข (C1) บอกตรง ๆ ว่าไม่ทราบต้นทุน
        logger.info(
            "LLM %s: in=%d out=%d tokens (ไม่ทราบราคาโมเดลนี้ — เพิ่มใน _MODEL_PRICES_USD_PER_MTOK)",
            ANTHROPIC_MODEL,
            input_tokens,
            output_tokens,
        )
        return
    usd = input_tokens / 1_000_000 * price_in + output_tokens / 1_000_000 * price_out
    logger.info(
        "LLM %s: in=%d out=%d tokens ≈ $%.4f (~%.2f บาท)",
        ANTHROPIC_MODEL,
        input_tokens,
        output_tokens,
        usd,
        usd * _USD_TO_THB,
    )


def _chat_anthropic(system: str, user: str, max_tokens: int) -> str:
    """เรียก Claude — **ห้ามส่ง temperature/top_p/top_k**.

    Sonnet 5 (และ Opus 4.7 ขึ้นไป) ตอบ 400 ``temperature is deprecated for this model``
    ถ้าส่งค่าที่ไม่ใช่ค่าเริ่มต้น โมเดลรุ่นใหม่ให้คุมพฤติกรรมด้วย prompt แทน
    ``chat_text()`` ยังรับพารามิเตอร์ ``temperature`` ไว้เพื่อไม่ให้ผู้เรียกทั้ง 7 จุดพัง
    แต่จะไม่ถูกส่งต่อไปที่ API
    """
    import anthropic

    client = anthropic.Anthropic()
    budget = max_tokens
    for attempt in range(2):
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=budget,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        usage = response.usage
        _log_cost(usage.input_tokens, usage.output_tokens)

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

    ``temperature`` **ไม่มีผลแล้ว** — รับไว้เพื่อความเข้ากันได้กับผู้เรียกเดิมเท่านั้น
    Claude รุ่นใหม่ตอบ 400 ถ้าส่งค่านี้ไป (ดู ``_chat_anthropic``) ถ้าต้องการคุมโทน
    หรือความยาวคำตอบ ให้เขียนกำกับใน system prompt แทน
    """
    load_dotenv(dotenv_path=ROOT_DIR / ".env", override=False)

    if not user_initiated and not auto_enabled():
        raise LLMDisabledError(AI_DISABLED_MESSAGE)

    if not _anthropic_available():
        raise RuntimeError("เรียก LLM ไม่สำเร็จ: ไม่ได้ตั้งค่า ANTHROPIC_API_KEY")

    # ไม่มี fallback แล้ว — ล้มเหลวต้องดังและอ่านออก ห้ามเงียบหรือคืนข้อความปลอม
    try:
        text = _chat_anthropic(system, user, max_tokens)
    except Exception as exc:
        raise RuntimeError(f"เรียก LLM ไม่สำเร็จ: anthropic: {exc}") from exc
    if not text:
        raise RuntimeError("เรียก LLM ไม่สำเร็จ: anthropic: empty response")
    return text
