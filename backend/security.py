# -*- coding: utf-8 -*-
"""Auth ของ backend (AUDIT.md H1).

เดิม backend เปิดสาธารณะโดยไม่มี auth และ CORS = ``*`` → ใครรู้ URL ก็:
- เพิ่ม/ลบธุรกรรมในสมุดบัญชีของเจ้าของได้
- เผา credit ของ Anthropic ผ่าน /api/transactions/upload-slip
- ยิง Groq/Claude ผ่าน /api/ai/*

นโยบายตอนนี้: ถ้าตั้ง ``VAULTIS_API_KEY`` ทุก endpoint ที่เปลี่ยนสถานะหรือเรียก LLM
ต้องส่ง header ``X-API-Key`` ให้ตรง — endpoint อ่านอย่างเดียว (ราคา, สัญญาณ) ยังเปิดได้

ถ้า **ไม่ได้ตั้ง** ``VAULTIS_API_KEY``: อนุญาตเฉพาะการเรียกจากเครื่องเดียวกัน (localhost)
เพื่อให้ dev ในเครื่องยังสะดวก แต่การ deploy สาธารณะโดยลืมตั้งคีย์จะไม่เปิดช่องให้คนอื่น
"""

from __future__ import annotations

import hmac
import logging
import os

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _configured_key() -> str:
    return os.getenv("VAULTIS_API_KEY", "").strip()


def _is_local(request: Request) -> bool:
    client = request.client
    return bool(client and client.host in _LOCAL_HOSTS)


async def require_api_key(request: Request) -> None:
    """FastAPI dependency — ใช้กับ router ที่เปลี่ยนสถานะหรือมีค่าใช้จ่าย."""
    expected = _configured_key()

    if not expected:
        if _is_local(request):
            return
        raise HTTPException(
            status_code=503,
            detail=(
                "backend นี้ยังไม่ได้ตั้งค่า VAULTIS_API_KEY จึงรับคำขอจากภายนอกไม่ได้ "
                "(ตั้ง env VAULTIS_API_KEY แล้วส่ง header X-API-Key)"
            ),
        )

    provided = request.headers.get("X-API-Key", "")
    if not provided or not hmac.compare_digest(provided, expected):
        logger.warning("ปฏิเสธคำขอที่ไม่มี/ผิด X-API-Key จาก %s", request.client.host if request.client else "?")
        raise HTTPException(status_code=401, detail="X-API-Key ไม่ถูกต้อง")


def allowed_origins() -> list[str]:
    """รายการ origin ที่อนุญาต — เดิมเป็น ``*`` ซึ่งเปิดให้เว็บใดก็ยิง API ได้."""
    raw = os.getenv("VAULTIS_ALLOWED_ORIGINS", "").strip()
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    return ["http://localhost:8501", "http://127.0.0.1:8501"]  # Streamlit ในเครื่อง
