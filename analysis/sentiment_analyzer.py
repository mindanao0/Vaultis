"""วิเคราะห์ sentiment ข่าวเป็นชุดผ่าน Anthropic Claude."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from anthropic import Anthropic
from dotenv import load_dotenv

from analysis.sentiment_prompt import build_sentiment_prompt, parse_sentiment_response

ROOT_DIR = Path(__file__).resolve().parents[1]

_BATCH_SIZE = 10
_MODEL = "claude-sonnet-4-20250514"


def _chunks(items: list[dict], size: int) -> list[list[dict]]:
    if not items:
        return []
    return [items[i : i + size] for i in range(0, len(items), size)]


def _as_confidence(value: Any) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, x))


def _normalize_row(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(item.get("title", "") or ""),
        "sentiment": str(item.get("sentiment", "") or "neutral"),
        "confidence": _as_confidence(item.get("confidence")),
        "reason": str(item.get("reason", "") or ""),
    }


def analyze_batch(articles: list[dict], symbol: str) -> list[dict[str, Any]]:
    """แบ่งข่าวทีละ 10 รายการ เรียก Claude วิเคราะห์ sentiment รวมผลเป็นหนึ่งรายการ."""
    load_dotenv(ROOT_DIR / ".env")
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return []

    try:
        client = Anthropic(api_key=api_key)
    except (TypeError, ValueError):
        return []

    batches = _chunks(list(articles or []), _BATCH_SIZE)
    merged: list[dict[str, Any]] = []

    for i, batch in enumerate(batches):
        try:
            prompt = build_sentiment_prompt(batch, symbol)
            msg = client.messages.create(
                model=_MODEL,
                max_tokens=1000,
                temperature=0.1,
                messages=[{"role": "user", "content": prompt}],
            )
            text_parts: list[str] = []
            for block in msg.content:
                if getattr(block, "type", None) == "text" and hasattr(block, "text"):
                    text_parts.append(block.text)
            raw_text = "".join(text_parts)
            parsed = parse_sentiment_response(raw_text)
            for row in parsed:
                if isinstance(row, dict):
                    merged.append(_normalize_row(row))
        except Exception:
            pass
        if i < len(batches) - 1:
            time.sleep(1)

    return merged
