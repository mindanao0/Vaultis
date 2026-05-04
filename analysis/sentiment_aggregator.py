"""รวม sentiment จากผล analyze_batch เป็นสรุปเดียว."""

from __future__ import annotations

from typing import Any


def aggregate_sentiment(results: list[dict]) -> dict[str, Any]:
    """รับผลจาก analyze_batch แล้วคืนสรุปจำนวน ค่าเฉลี่ย confidence และ score รวม."""
    rows = list(results or [])
    total = len(rows)

    positive = 0
    negative = 0
    neutral = 0
    conf_sum = 0.0
    conf_n = 0

    for r in rows:
        if not isinstance(r, dict):
            neutral += 1
            continue
        s = str(r.get("sentiment", "") or "").strip().lower()
        if s == "positive":
            positive += 1
        elif s == "negative":
            negative += 1
        else:
            neutral += 1

        try:
            c = float(r.get("confidence"))
        except (TypeError, ValueError):
            continue
        conf_sum += c
        conf_n += 1

    if total > 0:
        score = round((positive - negative) / total, 4)
    else:
        score = 0.0

    if conf_n > 0:
        avg_confidence = round(conf_sum / conf_n, 4)
    else:
        avg_confidence = 0.0

    if score > 0.1:
        overall = "positive"
    elif score < -0.1:
        overall = "negative"
    else:
        overall = "neutral"

    return {
        "symbol": "",
        "total_articles": total,
        "positive": positive,
        "negative": negative,
        "neutral": neutral,
        "avg_confidence": avg_confidence,
        "overall_sentiment": overall,
        "score": score,
    }
