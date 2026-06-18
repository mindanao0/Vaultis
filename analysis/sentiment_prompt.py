"""สร้าง prompt และ parse ผล sentiment จากข่าว."""

from __future__ import annotations

import json
import re
from typing import Any


def _article_line(title: str, description: str) -> str:
    t = (title or "").replace("\n", " ").strip()
    d = (description or "").replace("\n", " ").strip()
    return f"{t} — {d}"


def build_sentiment_prompt(articles: list[dict], symbol: str) -> str:
    """สร้างข้อความ user message สำหรับให้โมเดลวิเคราะห์ sentiment ต่อบทความ."""
    sym = (symbol or "").strip()
    lines: list[str] = [
        "---",
        f"Analyze the sentiment of the following news articles related to stock symbol: {sym}",
        "",
        "For each article return ONLY a JSON array, no explanation, no markdown. Format:",
        "[",
        "  {",
        '    "title": "...",',
        '    "sentiment": "positive" | "negative" | "neutral",',
        '    "confidence": 0.0–1.0,',
        '    "reason": "one sentence in English"',
        "  }",
        "]",
        "",
        "Articles:",
    ]
    for i, art in enumerate(articles or [], start=1):
        if not isinstance(art, dict):
            continue
        title = str(art.get("title", "") or "")
        desc = str(art.get("description", "") or "")
        lines.append(f"{i}. {_article_line(title, desc)}")
    lines.extend(
        [
            "",
            "Rules:",
            "- sentiment must be exactly one of: positive, negative, neutral",
            "- confidence is a float between 0.0 and 1.0",
            "- reason must be one sentence max",
            "- Return ONLY the JSON array, no preamble",
            "---",
        ]
    )
    return "\n".join(lines)


def parse_sentiment_response(response_text: str) -> list[dict[str, Any]]:
    """แกะ markdown fence ถ้ามี แล้ว parse JSON array; ล้มเหลวคืน []."""
    raw = (response_text or "").strip()
    if not raw:
        return []

    text = raw
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text)
    text = text.strip()

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []

    if not isinstance(data, list):
        return []

    out: list[dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict):
            out.append(item)
    return out
