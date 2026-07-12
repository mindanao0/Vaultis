"""Transactions router: POST /api/transactions/upload-slip"""

from __future__ import annotations

import base64
import json
import os

import anthropic
from fastapi import APIRouter, HTTPException, UploadFile

from ..schemas import SlipUploadResponse

router = APIRouter(prefix="/api/transactions", tags=["transactions"])

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png"}

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


@router.post("/upload-slip", response_model=SlipUploadResponse)
async def upload_slip(file: UploadFile):
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=422,
            detail="รองรับเฉพาะไฟล์ JPEG หรือ PNG เท่านั้น",
        )

    contents = await file.read()

    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(status_code=422, detail="ไฟล์ขนาดใหญ่เกิน 5MB")

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

    if not data.get("is_slip"):
        return SlipUploadResponse(
            success=False,
            error=data.get("error") or "ไม่ใช่สลิป",
        )

    return SlipUploadResponse(
        success=True,
        amount=data.get("amount"),
        date=data.get("date"),
        sender=data.get("sender"),
        receiver=data.get("receiver"),
        category=data.get("category"),
    )
