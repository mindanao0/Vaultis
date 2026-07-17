# -*- coding: utf-8 -*-
"""ช่องทางแจ้งเตือน LINE Messaging API (Roadmap Phase 5 ข้อ 16) — เสริมข้าง Discord/Telegram.

env ที่ต้องตั้ง (secret — env เท่านั้น ห้ามลง config.json ตามนโยบายเดิม):
- ``LINE_CHANNEL_ACCESS_TOKEN``: token ของ Messaging API channel
- ``LINE_TARGET_ID``: userId/groupId ปลายทาง (ดูได้จาก webhook event หรือ LINE Developers console)

ไม่ตั้ง env = คืน ``skipped`` เงียบ ๆ เหมือนช่องทางอื่น — งานหลักห้ามพังเพราะช่องทางเสริม
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

import requests

logger = logging.getLogger(__name__)

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
_LINE_TEXT_LIMIT = 4900  # เพดานข้อความ LINE คือ 5000 ตัวอักษร — เผื่อขอบไว้


def line_configured() -> bool:
    """มี token + target ครบพอจะส่งได้ไหม."""
    return bool(os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()) and bool(
        os.getenv("LINE_TARGET_ID", "").strip()
    )


def send_line_message(text: str) -> Dict[str, Any]:
    """push ข้อความเข้า LINE; คืน dict รูปแบบเดียวกับ notifier อื่น.

    ``{"success": True}`` / ``{"success": False, "skipped": True}`` (ไม่ได้ตั้งค่า)
    / ``{"success": False, "error": ...}`` (ส่งจริงแล้วพลาด)
    """
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    target = os.getenv("LINE_TARGET_ID", "").strip()
    if not token or not target:
        return {
            "success": False,
            "skipped": True,
            "error": "LINE ไม่ได้ตั้งค่า (LINE_CHANNEL_ACCESS_TOKEN / LINE_TARGET_ID)",
        }
    try:
        response = requests.post(
            LINE_PUSH_URL,
            headers={"Authorization": f"Bearer {token}"},
            json={"to": target, "messages": [{"type": "text", "text": str(text)[:_LINE_TEXT_LIMIT]}]},
            timeout=10,
        )
        response.raise_for_status()
        return {"success": True}
    except Exception as exc:
        logger.warning("ส่งข้อความเข้า LINE ไม่สำเร็จ: %s", exc)
        return {"success": False, "error": str(exc)}
