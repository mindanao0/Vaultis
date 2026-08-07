# -*- coding: utf-8 -*-
"""วิเคราะห์ sentiment ข่าวเป็นชุด ผ่านชั้นกลาง ``analysis/llm.py``.

เดิมไฟล์นี้สร้าง ``Groq()`` เองตรง ๆ ซึ่งขัด convention ของโปรเจกต์ (ข้อยกเว้นเดียว
ที่อนุญาตให้เรียก client เองคือ slip OCR ที่ต้องใช้ vision) ผลคืองานนี้ไม่ log
โทเคน/ค่าใช้จ่าย และไม่มี fallback ไป Claude — ตอนนี้ไปผ่าน ``chat_text`` ทางเดียว
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, NamedTuple

from analysis.llm import LLMDisabledError, chat_text
from analysis.news_fetcher import STATUS_ERROR, get_news_with_status
from analysis.sentiment_aggregator import aggregate_sentiment
from analysis.sentiment_prompt import build_sentiment_prompt, parse_sentiment_response
from db.sentiment_models import SentimentResult, SentimentSummary, SessionLocal

logger = logging.getLogger(__name__)

DEFAULT_SENTIMENT_SYMBOLS: list[str] = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]

_BATCH_SIZE = 10
_SENTIMENT_SYSTEM = (
    "You classify the sentiment of financial news headlines. "
    "Return ONLY a JSON array — no preamble, no markdown fence."
)


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
    """คัดรูปแถวเดียวจากคำตอบโมเดล — **ห้ามเติมป้ายให้แถวที่ไม่มีป้าย**.

    เดิมแถวที่โมเดลไม่ได้ใส่คีย์ ``sentiment`` (หรือใส่ป้ายที่ระบบไม่รู้จัก เช่น
    ``bullish``) กลายเป็น ``neutral`` = กุคำตอบขึ้นมาเอง ตอนนี้ปล่อยเป็นค่าว่าง
    แล้วให้ ``aggregate_sentiment`` นับไว้ที่ ``unclassified``
    """
    return {
        "title": str(item.get("title", "") or ""),
        "sentiment": str(item.get("sentiment", "") or "").strip().lower(),
        "confidence": _as_confidence(item.get("confidence")),
        "reason": str(item.get("reason", "") or ""),
    }


class BatchAnalysis(NamedTuple):
    """ผลของ ``analyze_batch`` — แถวที่ได้ **พร้อมจำนวน batch ที่ล้ม**.

    ต้องคืนคู่กันเสมอ ไม่งั้นผู้เรียกแยกไม่ออกว่า "ข่าวน้อย" กับ "เรียกโมเดลไม่สำเร็จ"
    ต่างกันอย่างไร (AUDIT_2026-08-06 §B2.1)
    """

    rows: list[dict[str, Any]]
    failed_batches: int
    total_batches: int


def analyze_batch(
    articles: list[dict], symbol: str, *, user_initiated: bool = False
) -> BatchAnalysis:
    """แบ่งข่าวทีละ 10 รายการ ให้ LLM จัด sentiment แล้วรวมผล.

    ``user_initiated`` ส่งต่อให้ ``chat_text`` ตรง ๆ — งานอัตโนมัติต้องปล่อยเป็น False
    แล้วอาศัย ``VAULTIS_LLM_AUTO=1`` เป็นตัวเปิด (นโยบายคุมค่าใช้จ่ายใน CLAUDE.md)

    คืน ``BatchAnalysis`` ไม่ใช่ลิสต์เปล่า ๆ เพื่อบังคับให้ผู้เรียกเห็นจำนวน batch ที่ล้ม
    """
    batches = _chunks(list(articles or []), _BATCH_SIZE)
    merged: list[dict[str, Any]] = []
    failed = 0

    for i, batch in enumerate(batches):
        try:
            raw_text = chat_text(
                _SENTIMENT_SYSTEM,
                build_sentiment_prompt(batch, symbol),
                max_tokens=1000,
                temperature=0.1,
                user_initiated=user_initiated,
            )
        except LLMDisabledError:
            # LLM ปิดอยู่ = ไม่ใช่ error จริง แต่ทำต่อไม่ได้ → เลิกทั้งชุด ไม่วนจ่ายซ้ำ
            # (ไม่นับเป็น failed_batches แต่ coverage จะฟ้องเองว่าวิเคราะห์ไม่ครบ)
            logger.info("[%s] ข้าม sentiment — LLM ปิดอยู่เพื่อคุมค่าใช้จ่าย", symbol)
            return BatchAnalysis(merged, failed, len(batches))
        except Exception as exc:
            # ห้ามกลืนเงียบ: batch ที่พังต้องเห็นใน log ไม่งั้น sentiment จะดูเหมือน
            # "ข่าวน้อย" ทั้งที่จริงคือเรียกโมเดลไม่สำเร็จ
            logger.warning("[%s] sentiment batch %d/%d ล้มเหลว: %s", symbol, i + 1, len(batches), exc)
            failed += 1
            raw_text = ""

        for row in parse_sentiment_response(raw_text):
            if isinstance(row, dict):
                merged.append(_normalize_row(row))

        if i < len(batches) - 1:
            time.sleep(1)

    return BatchAnalysis(merged, failed, len(batches))


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


def _failed_sources_note(news: dict[str, Any]) -> str:
    """ชื่อแหล่งที่ล้มเหลว + เหตุผล — ต้องพิมพ์ออกไป ไม่ใช่รู้อยู่คนเดียว."""
    broken = [
        s
        for s in (news.get("sources") or [])
        if isinstance(s, dict) and str(s.get("status")) == STATUS_ERROR
    ]
    if not broken:
        return "ไม่ทราบแหล่งที่ล้มเหลว"
    return ", ".join(
        f"{s.get('name')}: {s.get('detail')}" if s.get("detail") else str(s.get("name"))
        for s in broken
    )


def _coverage_note(agg: dict[str, Any]) -> str:
    """ประโยคเดียวที่บอกว่า "ดึงมาเท่าไร วิเคราะห์ได้เท่าไร" — ต้องอยู่ในทุกบรรทัดสรุป."""
    pct = agg.get("coverage_pct")
    note = f"วิเคราะห์ได้ {agg['analyzed']} จาก {agg['fetched']} ข่าว"
    if pct is not None and not agg.get("complete"):
        note += f" ({pct}%)"
    if agg.get("failed_batches"):
        note += f" · batch ที่เรียกโมเดลไม่สำเร็จ {agg['failed_batches']}"
    if agg.get("unclassified"):
        note += f" · ป้ายที่อ่านไม่ออก {agg['unclassified']}"
    return note


def _process_symbol(sym: str) -> None:
    """ดึงข่าว → วิเคราะห์ → บันทึก 1 สัญลักษณ์ (แยกออกมาเพื่อให้เส้นทาง "ข้าม" อ่านง่าย)."""
    news = get_news_with_status(sym)
    articles = [a for a in (news.get("items") or []) if isinstance(a, dict)]

    if not articles:
        # "ดึงไม่สำเร็จ" ≠ "ไม่มีข่าว" — เดิม job นี้อ่านลิสต์ว่างเป็นอย่างหลังเสมอ
        if news.get("all_news_sources_failed") or news.get("has_error"):
            print(f"[{sym}] ดึงข่าวไม่สำเร็จ ({_failed_sources_note(news)}) — ข้ามรอบนี้")
        else:
            print(f"[{sym}] ไม่มีข่าวในรอบนี้ (ทุกแหล่งตอบปกติ) — ข้าม")
        return

    if news.get("all_news_sources_failed"):
        print(
            f"[{sym}] เตือน: แหล่งข่าวจริงล้มเหลวทั้งหมด ({_failed_sources_note(news)}) "
            "— เหลือแต่โพสต์โซเชียล"
        )
    elif news.get("has_error"):
        print(f"[{sym}] เตือน: บางแหล่งดึงไม่สำเร็จ ({_failed_sources_note(news)})")

    batch = analyze_batch(articles, sym)
    agg = aggregate_sentiment(
        batch.rows, fetched=len(articles), failed_batches=batch.failed_batches
    )
    coverage = _coverage_note(agg)

    if agg["score"] is None:
        # ไม่มีแถวที่จัดป้ายได้เลย = ยังไม่รู้ ห้ามเขียนสรุป "neutral score=0.0" ลงฐาน
        print(f"[{sym}] {coverage} — ไม่บันทึกสรุป (ยังไม่รู้ผล ไม่ใช่ 'กลาง ๆ')")
        return

    by_title: dict[str, Any] = {}
    for a in articles:
        t = _norm_title(str(a.get("title") or ""))
        if t:
            by_title[t] = a

    db = SessionLocal()
    saved = False
    try:
        for row in batch.rows:
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
        print(
            f"[{sym}] sentiment done: {agg['overall_sentiment']} "
            f"score={agg['score']:+.4f} — {coverage}"
        )


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

        _process_symbol(sym)

        if i < len(sym_list) - 1:
            time.sleep(2)
