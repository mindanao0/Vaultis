"""Transactions router: POST /api/transactions/upload-slip"""

from __future__ import annotations

import base64
import json
import math
import os
from datetime import datetime
from typing import Any

import anthropic
from fastapi import APIRouter, HTTPException, UploadFile

from ..schemas import SlipUploadResponse

router = APIRouter(prefix="/api/transactions", tags=["transactions"])

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
CHUNK_SIZE = 256 * 1024  # อ่านทีละก้อน — เพดานหน่วยความจำของ handler ไม่ขึ้นกับขนาดที่ถูกส่งมา
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png"}
OVERSIZE_DETAIL = "ไฟล์ขนาดใหญ่เกิน 5MB"

# ต้องตรงกับชุดที่ ``_SYSTEM_PROMPT`` ประกาศไว้ (tests/test_slip_ocr_validation.py ตรึงไว้)
ALLOWED_CATEGORIES = ("บันเทิง", "ลงทุน", "โอนเงิน", "อื่นๆ")

# เพดานความสมเหตุสมผลของยอดในสลิปโอนเงินไทย 1 ใบ — OCR ที่อ่านเลขติดกันจะพุ่งทะลุค่านี้
# (ผลตรวจ D2.1: amount 999,999,999,999 ถูกตอบกลับเป็น success=true)
MAX_SLIP_AMOUNT = 100_000_000.0

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


_SYSTEM_PROMPT = (
    "You are a Thai bank transfer slip parser.\n"
    "Examine the image and return ONLY a JSON object — no markdown, no explanation.\n\n"
    "Schema:\n"
    "{\n"
    '  "is_slip": <boolean>,\n'
    '  "error": <null | "รูปไม่ชัด" | "ไม่ใช่สลิป">,\n'
    '  "amount": <number | null>,\n'
    '  "date": <"YYYY-MM-DD" | null>,\n'
    '  "sender": <string | null>,\n'
    '  "receiver": <string | null>,\n'
    '  "category": <"บันเทิง" | "ลงทุน" | "โอนเงิน" | "อื่นๆ">\n'
    "}\n\n"
    "Rules:\n"
    "- Unclear / blurry image → is_slip: false, error: 'รูปไม่ชัด'\n"
    "- Not a slip → is_slip: false, error: 'ไม่ใช่สลิป'\n"
    "- Valid slip → is_slip: true, error: null, fill all fields\n"
    "- category: 'โอนเงิน' for general transfers, 'ลงทุน' for investments, "
    "'บันเทิง' for entertainment, 'อื่นๆ' for others"
)


async def _read_capped(file: UploadFile) -> bytes:
    """อ่านไฟล์แบบมีเพดาน — ปฏิเสธก่อนโหลดทั้งก้อนเข้าหน่วยความจำ (ผลตรวจ D2.3).

    เดิม ``await file.read()`` ดูดทั้งไฟล์เข้า RAM ก่อนแล้วค่อยเทียบขนาด: อัปโหลด 200 MB
    ทำให้ peak ของ handler = 200 MB ทั้งที่เพดานคือ 5 MB (วัดด้วย ``tracemalloc``)

    สองด่าน: ``file.size`` ที่ starlette คำนวณให้ตอน parse multipart เป็นด่านแรก
    (ไม่ต้องอ่านสักไบต์) และการอ่านทีละ ``CHUNK_SIZE`` ที่ตัดทันทีที่เกินเพดานเป็นด่านสอง
    สำหรับ transport ที่ไม่ประกาศขนาดมา
    """
    declared = getattr(file, "size", None)
    if isinstance(declared, int) and declared > MAX_FILE_SIZE:
        raise HTTPException(status_code=422, detail=OVERSIZE_DETAIL)

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_FILE_SIZE:
            raise HTTPException(status_code=422, detail=OVERSIZE_DETAIL)
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_amount(raw: Any) -> float | None:
    """ยอดเงินที่ใช้ได้จริงเท่านั้น — ไม่งั้นคืน ``None`` ให้ผู้เรียกตอบว่าอ่านไม่ได้.

    ครอบสามอาการของผลตรวจ D2.1/D2.2 พร้อมกัน: สตริงมีคอมมา (เดิม 500), ค่าติดลบ/ศูนย์,
    ``NaN``/``inf`` (``json.loads`` รับ literal พวกนี้ได้) และยอดที่เกินความสมเหตุสมผล
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
    elif isinstance(raw, str):
        text = raw.strip()
        for token in (" ", " ", ",", "฿", "บาท", "THB", "thb"):
            text = text.replace(token, "")
        try:
            value = float(text)
        except ValueError:
            return None
    else:  # dict / list / อะไรก็ตามที่โมเดลคืนมานอกสัญญา
        return None

    if not math.isfinite(value) or value <= 0 or value > MAX_SLIP_AMOUNT:
        return None
    return value


def _parse_date(raw: Any) -> str | None:
    """คืน ``YYYY-MM-DD`` เมื่อ parse ได้จริงเท่านั้น.

    รับเฉพาะรูปแบบ ISO ตามที่ system prompt สั่งไว้ — ``05/08/2026`` ไม่รับเพราะแยกไม่ออก
    ว่าเป็นวัน/เดือน หรือเดือน/วัน (เดาผิดแล้วธุรกรรมไปอยู่ผิดเดือน)
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return None


def _parse_category(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    return text if text in ALLOWED_CATEGORIES else None


def _clean_text(raw: Any) -> str | None:
    """ชื่อผู้โอน/ผู้รับ — ชนิดอื่นที่โมเดลคืนมาต้องไม่ทำให้ response model โยน 500"""
    if isinstance(raw, str):
        return raw.strip() or None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(raw):
        return str(raw)
    return None


@router.post("/upload-slip", response_model=SlipUploadResponse)
async def upload_slip(file: UploadFile):
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=422,
            detail="รองรับเฉพาะไฟล์ JPEG หรือ PNG เท่านั้น",
        )

    contents = await _read_capped(file)

    image_b64 = base64.standard_b64encode(contents).decode("utf-8")

    try:
        response = _get_client().messages.create(
            # Haiku 4.5 อ่านสลิปได้แม่นใกล้เคียง Opus ที่ ~1/5 ของราคา (AUDIT.md L7)
            model="claude-haiku-4-5",
            max_tokens=512,
            system=_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": file.content_type,
                                "data": image_b64,
                            },
                        },
                        {"type": "text", "text": "Parse this image and return JSON only."},
                    ],
                }
            ],
        )
    except anthropic.APIError as exc:
        raise HTTPException(status_code=502, detail=f"Claude API error: {exc}") from exc

    raw_text = next(
        (block.text for block in response.content if block.type == "text"), ""
    )

    # กัน markdown fence และข้อความห่อหุ้ม: ตัดเอาเฉพาะช่วง { ... } ตัวนอกสุด
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start : end + 1]

    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return SlipUploadResponse(success=False, error="parse JSON ไม่ได้")

    if not isinstance(data, dict):
        return SlipUploadResponse(success=False, error="parse JSON ไม่ได้")

    if not data.get("is_slip"):
        return SlipUploadResponse(
            success=False,
            error=_clean_text(data.get("error")) or "ไม่ใช่สลิป",
        )

    # ผลตรวจ D2.1: เดิมส่งค่าที่โมเดลคืนมาต่อเป็น success=true โดยไม่ตรวจอะไรเลย
    # ยอดเงินติดลบ วันที่ที่ parse ไม่ได้ และหมวดหมู่นอกรายการจึงกลายเป็น "อ่านสลิปสำเร็จ"
    # "อ่านไม่ได้" ต้องไม่กลายเป็นตัวเลขในสมุดบัญชี — ตอบ success=false พร้อมเหตุผลไทย
    amount = _parse_amount(data.get("amount"))
    date = _parse_date(data.get("date"))
    category = _parse_category(data.get("category"))

    problems = [
        message
        for value, message in (
            (amount, "อ่านยอดเงินจากสลิปไม่ได้"),
            (date, "อ่านวันที่จากสลิปไม่ได้"),
            (category, "อ่านหมวดหมู่จากสลิปไม่ได้"),
        )
        if value is None
    ]
    if problems:
        # ไม่คืนฟิลด์ที่เหลือ: ใบที่อ่านได้ไม่ครบยังบันทึกเป็นธุรกรรมไม่ได้อยู่ดี
        # และค่าที่ค้างมาครึ่งใบเสี่ยงถูก UI หยิบไปใช้ต่อ
        return SlipUploadResponse(success=False, error=" · ".join(problems))

    return SlipUploadResponse(
        success=True,
        amount=amount,
        date=date,
        sender=_clean_text(data.get("sender")),
        receiver=_clean_text(data.get("receiver")),
        category=category,
    )
