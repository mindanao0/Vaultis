# -*- coding: utf-8 -*-
"""ชนิดคำตอบมาตรฐานของ backend — JSON ที่ประกาศ ``charset=utf-8`` เสมอ.

CLAUDE.md ประกาศไว้เองว่า "ทุก endpoint ที่อาจคืนข้อความไทยต้องใช้
``JSONResponse(..., media_type='application/json; charset=utf-8')``" แต่ router
ที่ประกาศ ``response_model`` คืน Response เองไม่ได้ (จะข้าม validation) จึงหลุด
สัญญานี้ไปหลายตัว (AUDIT_2026-08-06 D3.2)

ทางที่ใช้ได้ทั้งสองแบบคือตั้ง ``default_response_class`` ที่ระดับ router:

    router = APIRouter(prefix="/api", default_response_class=UTF8JSONResponse)

ผลจริงต่ำ (FastAPI ใช้ ``ensure_ascii=False`` อยู่แล้ว ไคลเอนต์ส่วนใหญ่เดา utf-8 ถูก)
แต่การประกาศ charset ให้ตรงคือสิ่งที่ทำให้ไคลเอนต์ที่ยึดตาม header ไม่ต้องเดา
"""

from __future__ import annotations

from fastapi.responses import JSONResponse


class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"
