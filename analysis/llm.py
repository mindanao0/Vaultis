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

**thinking ถูกปิดทุกการเรียก** (``_THINKING_DISABLED``) — บน Sonnet 5 การไม่ส่งฟิลด์นี้
แปลว่า "คิดแบบ adaptive" และ ``max_tokens`` เป็นเพดานรวมของ thinking + ข้อความตอบ
โควตา 512–2500 ของโปรเจกต์นี้จึงถูก thinking กินหมดได้ = จ่ายเงินแล้วไม่ได้คำตอบ
(ดู AUDIT_ROUND2_2026-08-07 และคอมเมนต์ที่ ``_THINKING_DISABLED``)

ทุกการเรียกจะ log จำนวนโทเคนและค่าใช้จ่ายโดยประมาณ เพื่อให้เห็นต้นทุนจริง
การเรียก Anthropic ที่ไม่ได้ผ่าน ``chat_text()`` (มีที่เดียว: slip OCR ซึ่งต้องใช้ vision)
ต้องรายงานต้นทุนผ่าน ``log_anthropic_usage()`` ไม่งั้นเงินก้อนนั้นหายไปจาก log
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[1]

# โหลด .env **ครั้งเดียวตอน import** — env ของโปรเซสคือแหล่งความจริงตอนรัน
# ไฟล์ .env เป็นแค่ค่าเริ่มต้นตอนบูตเท่านั้น
# เดิมเรียกซ้ำในทุก chat_text()/auto_enabled() ทำให้การ unset ตัวแปรในโปรเซส
# (เช่นถอด VAULTIS_LLM_AUTO ซึ่งเป็นสวิตช์คุมค่าใช้จ่าย หรือ ANTHROPIC_API_KEY)
# ไม่มีผลเลย เพราะไฟล์เติมกลับมาให้ทุกครั้ง = ไฟล์ชนะ env เสมอ
load_dotenv(dotenv_path=ROOT_DIR / ".env", override=False)

ANTHROPIC_MODEL = "claude-sonnet-5"

# ปิด "thinking" ทุกการเรียก — **จำเป็น ไม่ใช่การลดคุณภาพ** (AUDIT_ROUND2_2026-08-07)
#
# บน Sonnet 5 การ **ไม่ส่ง** ฟิลด์ ``thinking`` = เปิด adaptive thinking (พฤติกรรมนี้
# เปลี่ยนจาก Sonnet 4.6 ที่การไม่ส่ง = ไม่คิด) และ ``max_tokens`` บน Sonnet 5 เป็น
# เพดาน**รวม**ของ thinking + ข้อความตอบ  ผู้เรียกในโปรเจกต์นี้ขอโควตาแค่ 512–2500
# (ดีฟอลต์ 1500) ⇒ thinking กินโควตาจนไม่เหลือที่ให้ข้อความได้ ผลคือ
# ``stop_reason="max_tokens"`` โดยไม่มีเนื้อหา แล้วโค้ดด้านล่างต้อง retry ที่ 2 เท่า
# — **รอบแรกที่ถูกตัดยังถูกเรียกเก็บเงินอยู่** ทุกครั้งที่เกิดจึงจ่ายซ้ำซ้อน
# และรอบสองก็ยังพลาดได้อีก (RuntimeError)
#
# เหตุผลเชิงระบบที่ปิดได้โดยไม่เสียอะไร: กฎ "AI explains, code computes" ของโปรเจกต์
# แปลว่าตัวเลขทุกตัวคำนวณเสร็จใน Python แล้ว LLM มีหน้าที่เขียนคำอธิบายภาษาไทยอย่างเดียว
# งานนี้ไม่ต้องใช้การให้เหตุผลหลายขั้น
#
# ข้อควรระวัง: ``budget_tokens`` **ถูกถอดออกจาก Sonnet 5 แล้ว** ส่งไปได้ HTTP 400
# ห้ามใส่กลับมาไม่ว่ากรณีใด — ค่าคงที่นี้จึงมีแค่คีย์เดียว
_THINKING_DISABLED: dict[str, str] = {"type": "disabled"}

# ราคา (USD ต่อ 1 ล้านโทเคน) input/output — ใช้ประมาณค่าใช้จ่ายเพื่อแสดงให้ผู้ใช้เห็น
# ต้องอัปเดตพร้อมกับ ANTHROPIC_MODEL เสมอ ไม่งั้น log จะรายงานต้นทุนผิด
# ต้องครอบ **ทุกโมเดลที่โปรเจกต์เรียกจริง** ไม่ใช่แค่ตัวที่ chat_text() ใช้ —
# slip OCR ใน ``backend/routers/transactions.py`` ใช้ ``claude-haiku-4-5`` (vision)
# และรายงานค่าใช้จ่ายผ่าน ``log_anthropic_usage()`` ด้านล่าง
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
    """งานอัตโนมัติได้รับอนุญาตให้เรียก LLM หรือไม่ (ดีฟอลต์: ไม่).

    อ่านจาก env ของโปรเซสล้วน ๆ — ``.env`` ถูกโหลดไปแล้วตอน import โมดูลนี้
    """
    return os.getenv(_AUTO_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _anthropic_available() -> bool:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    return bool(key) and key != "your_key_here"


def _log_cost(
    input_tokens: int,
    output_tokens: int,
    *,
    model: str | None = None,
    label: str = "",
) -> None:
    """บันทึกโทเคน + ค่าใช้จ่ายโดยประมาณของการเรียก Anthropic หนึ่งครั้ง.

    ``model`` ปล่อยว่างได้เมื่อเป็นการเรียกผ่าน ``chat_text()`` (= ``ANTHROPIC_MODEL``)
    ผู้เรียกที่ใช้โมเดลอื่น (slip OCR ใช้ vision จึงต้องเป็น Haiku) ต้องส่งชื่อมาเอง
    ไม่งั้น log จะรายงานราคาของโมเดลผิดตัว
    """
    model = model or ANTHROPIC_MODEL
    suffix = f" [{label}]" if label else ""
    price_in, price_out = _MODEL_PRICES_USD_PER_MTOK.get(model, (0.0, 0.0))
    if not price_in and not price_out:
        # ไม่รู้ราคาของโมเดลนี้ = ห้ามเดาเป็นเลข (C1) บอกตรง ๆ ว่าไม่ทราบต้นทุน
        logger.info(
            "LLM %s%s: in=%d out=%d tokens "
            "(ไม่ทราบราคาโมเดลนี้ — เพิ่มใน _MODEL_PRICES_USD_PER_MTOK)",
            model,
            suffix,
            input_tokens,
            output_tokens,
        )
        return
    usd = input_tokens / 1_000_000 * price_in + output_tokens / 1_000_000 * price_out
    logger.info(
        "LLM %s%s: in=%d out=%d tokens ≈ $%.4f (~%.2f บาท)",
        model,
        suffix,
        input_tokens,
        output_tokens,
        usd,
        usd * _USD_TO_THB,
    )


def log_anthropic_usage(model: str, usage: object, *, label: str = "") -> None:
    """บันทึกต้นทุนของการเรียก Anthropic ที่ **ไม่ได้** ผ่าน ``chat_text()``.

    ที่มา (AUDIT_ROUND2_2026-08-07): slip OCR ใน ``backend/routers/transactions.py``
    เป็นข้อยกเว้นเดียวที่ CLAUDE.md อนุญาตให้สร้าง ``anthropic.Anthropic()`` เอง
    (ต้องใช้ vision) แต่เพราะข้ามชั้นนี้ไป มันจึงข้าม ``_log_cost()`` ไปด้วย —
    เงินที่จ่ายให้ OCR ทุกใบ **ไม่เคยโผล่ใน log ต้นทุนเลย** ผู้ใช้เห็นค่าใช้จ่ายต่ำกว่าจริง
    ฟังก์ชันนี้คือทางเข้าเดียวที่ทำให้ทุกการเรียก Anthropic ของโปรเจกต์ถูกนับครบ

    ``usage`` รับ object จาก SDK ตรง ๆ (หรือ ``None``) — **ไม่มีโทเคน ≠ ใช้ไป 0 โทเคน**
    ถ้าผู้ให้บริการไม่ส่งตัวเลขมา จะเตือนระดับ WARNING ว่าไม่ทราบต้นทุนรอบนี้
    ห้ามบันทึกเป็น 0 (C1: ความล้มเหลวห้ามกลายเป็นตัวเลข) และห้ามโยน exception —
    การบันทึกต้นทุนต้องไม่ทำให้คำขอที่สำเร็จแล้วพังตาม
    """
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    valid = [
        value
        for value in (input_tokens, output_tokens)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
    ]
    if len(valid) != 2:
        logger.warning(
            "LLM %s%s: เรียกสำเร็จแต่ไม่ได้รับจำนวนโทเคนกลับมา (usage=%r) — "
            "ค่าใช้จ่ายรอบนี้จึงไม่ถูกบันทึก (ไม่ใช่ 0)",
            model,
            f" [{label}]" if label else "",
            usage,
        )
        return
    _log_cost(input_tokens, output_tokens, model=model, label=label)


def _chat_anthropic(system: str, user: str, max_tokens: int) -> str:
    """เรียก Claude — **ห้ามส่ง temperature/top_p/top_k** และต้องปิด thinking เสมอ.

    Sonnet 5 (และ Opus 4.7 ขึ้นไป) ตอบ 400 ``temperature is deprecated for this model``
    ถ้าส่งค่าที่ไม่ใช่ค่าเริ่มต้น โมเดลรุ่นใหม่ให้คุมพฤติกรรมด้วย prompt แทน
    ``chat_text()`` ยังรับพารามิเตอร์ ``temperature`` ไว้เพื่อไม่ให้ผู้เรียกทั้ง 7 จุดพัง
    แต่จะไม่ถูกส่งต่อไปที่ API

    ``thinking`` ส่งผ่าน ``extra_body`` เพราะ SDK ที่ปักหมุดไว้ (``anthropic==0.42.0``)
    ยังไม่มีพารามิเตอร์นี้แบบ typed แต่ส่งฟิลด์ดิบเข้า request body ได้
    (ห้าม unpin เพื่อเรื่องนี้ — requirements.txt ปักทั้ง transitive closure ไว้)
    เหตุผลว่าทำไมต้องปิด อยู่ที่ ``_THINKING_DISABLED`` ด้านบน
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
            extra_body={"thinking": dict(_THINKING_DISABLED)},
        )
        # เงินออกไปแล้ว — การ "บันทึกต้นทุน" ต้องไม่ทำให้คำขอที่สำเร็จพังตาม
        # เดิมอ่าน ``response.usage.input_tokens`` ตรง ๆ: ถ้าผู้ให้บริการไม่ส่ง usage
        # กลับมา จะได้ AttributeError แล้วถูกห่อเป็น RuntimeError ⇒ จ่ายเงินแล้ว
        # ผู้ใช้ได้ error แทนคำอธิบาย ทั้งที่โมเดลตอบมาครบ  ``log_anthropic_usage()``
        # คือด่านเดียวกับที่เส้นทาง slip OCR ใช้อยู่แล้ว (ไม่รู้จำนวนโทเคน = WARNING
        # ว่าไม่ทราบต้นทุน ห้ามบันทึกเป็น 0 และห้ามโยน exception)
        log_anthropic_usage(ANTHROPIC_MODEL, getattr(response, "usage", None))

        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        ).strip()
        if response.stop_reason != "max_tokens":
            return text
        if attempt == 0:
            budget = max_tokens * 2
            continue
        # โควตาหมดรอบสองแล้วยังไม่มีเนื้อหาเลย = จ่ายเงินไป 2 รอบได้ศูนย์
        # ห้ามคืนหมายเหตุเปล่า ๆ เป็นผลสำเร็จ
        # (`"" + _TRUNCATION_NOTE` เป็น truthy ทำให้ด่าน `if not text` ใน chat_text ยิงไม่ได้)
        #
        # เดิมคอมเมนต์ตรงนี้เขียนว่า "มักเกิดเมื่อ thinking กินโควตาไปหมด" ซึ่งเป็นสาเหตุจริง
        # ในตอนนั้น — แต่แก้ที่ปลายทางด้วยการ retry (จ่ายซ้ำ) แทนที่จะกันไม่ให้เกิด
        # ตอนนี้ thinking ถูกปิดที่ต้นทางแล้ว (``_THINKING_DISABLED``) เส้นทางนี้จึงเหลือ
        # ความหมายเดียว: prompt + คำอธิบายยาวเกินโควตาที่ตั้งไว้จริง ๆ  ยัง retry ต่อไป
        # เพราะเป็นสาเหตุที่แก้ได้ด้วยการเพิ่มโควตา ไม่ใช่ความล้มเหลวถาวร
        if not text:
            raise RuntimeError(
                f"โมเดลใช้โควตา {budget} tokens หมดโดยไม่มีเนื้อหาตอบกลับ "
                "(stop_reason=max_tokens) — thinking ถูกปิดไว้แล้ว จึงแปลว่า prompt "
                "หรือคำตอบยาวเกินโควตาจริง ๆ ให้เพิ่ม max_tokens หรือย่อ prompt"
            )
        return text + _TRUNCATION_NOTE
    raise RuntimeError("เรียก LLM ไม่สำเร็จ: ลูป retry จบโดยไม่ได้คำตอบ (ไม่ควรเกิดขึ้น)")


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

    โยน ``RuntimeError`` เมื่อเรียกไม่สำเร็จหรือได้คำตอบว่าง — ผู้เรียกที่เป็น
    งานอัตโนมัติต้องดักให้ครบ (``LLMDisabledError`` เป็นลูกของ ``RuntimeError``
    การดักเฉพาะตัวลูกจะ **ไม่** ครอบคลุมความล้มเหลวจริงของ provider)
    """
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
