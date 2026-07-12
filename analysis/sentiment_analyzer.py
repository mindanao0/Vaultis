# -*- coding: utf-8 -*-
"""วิเคราะห์ sentiment ข่าวเป็นชุดผ่าน Groq."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from groq import Groq

from analysis.news_fetcher import get_news
from analysis.sentiment_aggregator import aggregate_sentiment
from analysis.sentiment_prompt import build_sentiment_prompt, parse_sentiment_response
from db.sentiment_models import SentimentResult, SentimentSummary, SessionLocal

ROOT_DIR = Path(__file__).resolve().parents[1]

DEFAULT_SENTIMENT_SYMBOLS: list[str] = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]

_BATCH_SIZE = 10
_GROQ_MODEL = "llama-3.3-70b-versatile"


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
    """แบ่งข่าวทีละ 10 รายการ เรียก Groq วิเคราะห์ sentiment รวมผลเป็นหนึ่งรายการ."""
    load_dotenv(ROOT_DIR / ".env")
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return []

    try:
        client = Groq(api_key=api_key)
    except (TypeError, ValueError):
        return []

    batches = _chunks(list(articles or []), _BATCH_SIZE)
    merged: list[dict[str, Any]] = []

    for i, batch in enumerate(batches):
        try:
            prompt = build_sentiment_prompt(batch, symbol)
            response = client.chat.completions.create(
                model=_GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1000,
            )
            raw_text = response.choices[0].message.content or ""
            parsed = parse_sentiment_response(raw_text)
            for row in parsed:
                if isinstance(row, dict):
                    merged.append(_normalize_row(row))
        except Exception:
            pass
        if i < len(batches) - 1:
            time.sleep(1)

    return merged


def _norm_title(s: str) -> str:
    return " ".join((s or "").split()).lower()


def _parse_published_at(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    text = str(value).strip()
    if not text:
        return None
    try:
        s = text
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def run_sentiment_job(symbols: list[str] | None = None) -> None:
    """ดึงข่าว วิเคราะห์เป็นชุด สรุป และบันทึกลง PostgreSQL ต่อสัญลักษณ์.

    เป็นงานอัตโนมัติที่เรียก LLM หลายครั้งต่อรอบ (ข่าวละชุด × 5 สัญลักษณ์)
    → ปิดโดยดีฟอลต์เพื่อคุมค่าใช้จ่าย เปิดด้วย ``VAULTIS_LLM_AUTO=1``
    """
    from analysis.llm import auto_enabled

    if not auto_enabled():
        print(
            "[sentiment job] ข้ามการวิเคราะห์ sentiment — LLM ปิดอยู่เพื่อคุมค่าใช้จ่าย "
            "(ตั้ง VAULTIS_LLM_AUTO=1 ถ้าต้องการเปิด)"
        )
        return

    sym_list = list(DEFAULT_SENTIMENT_SYMBOLS) if symbols is None else list(symbols)
    if SessionLocal is None:
        print("[sentiment job] DATABASE_URL not set; aborting.")
        return

    for i, raw in enumerate(sym_list):
        sym = (raw or "").strip().upper()
        if not sym:
            continue

        articles = get_news(sym)
        if not articles:
            print(f"[{sym}] no news; skip")
            if i < len(sym_list) - 1:
                time.sleep(2)
            continue

        results = analyze_batch(articles, sym)
        agg = aggregate_sentiment(results)

        by_title: dict[str, Any] = {}
        for a in articles:
            if not isinstance(a, dict):
                continue
            t = _norm_title(str(a.get("title") or ""))
            if t:
                by_title[t] = a

        db = SessionLocal()
        saved = False
        try:
            for row in results:
                if not isinstance(row, dict):
                    continue
                art = by_title.get(_norm_title(str(row.get("title") or "")))
                published_at = _parse_published_at((art or {}).get("published_at"))
                conf_val: float | None = None
                if row.get("confidence") is not None:
                    try:
                        conf_val = float(row.get("confidence"))
                    except (TypeError, ValueError):
                        conf_val = None
                db.add(
                    SentimentResult(
                        symbol=sym,
                        title=str(row.get("title") or "") or None,
                        sentiment=str(row.get("sentiment") or "") or None,
                        confidence=conf_val,
                        reason=str(row.get("reason") or "") or None,
                        published_at=published_at,
                    )
                )

            db.add(
                SentimentSummary(
                    symbol=sym,
                    total_articles=agg["total_articles"],
                    positive=agg["positive"],
                    negative=agg["negative"],
                    neutral=agg["neutral"],
                    avg_confidence=agg["avg_confidence"],
                    overall_sentiment=agg["overall_sentiment"],
                    score=agg["score"],
                )
            )
            db.commit()
            saved = True
        except Exception as exc:
            db.rollback()
            print(f"[{sym}] sentiment job DB error: {exc}")
        finally:
            db.close()

        if saved:
            overall = str(agg.get("overall_sentiment") or "neutral")
            score = agg.get("score", 0.0)
            print(f"[{sym}] sentiment done: {overall} score={score}")

        if i < len(sym_list) - 1:
            time.sleep(2)
