"""รวม sentiment จากผล analyze_batch เป็นสรุปเดียว.

สัญญาของไฟล์นี้ต้องบอกได้ว่า **"ดึงข่าวมาเท่าไร วิเคราะห์ได้จริงเท่าไร"** ไม่ใช่คืน
แค่ตัวเลขที่คำนวณจากส่วนที่รอดมา: batch ที่เรียกโมเดลไม่สำเร็จเคยหายเงียบจนป้าย
sentiment พลิก (ข่าวลบ 10 ชิ้นหายไป → ``+0.50 positive`` ทั้งที่ความจริงคือกลาง ๆ)
และเมื่อทุก batch ล้มก็ยังคืน ``neutral score=0.0`` ซึ่งเป็นความล้มเหลวที่ปลอมตัว
เป็นข้อมูล (AUDIT_2026-08-06 §B2.1)
"""

from __future__ import annotations

from typing import Any

# ป้ายที่ระบบรู้จัก — ป้ายอื่น (หรือไม่มีป้ายเลย) คือ "อ่านไม่ออก" ไม่ใช่ "กลาง ๆ"
_KNOWN_LABELS = ("positive", "negative", "neutral")


def aggregate_sentiment(
    results: list[dict],
    *,
    fetched: int | None = None,
    failed_batches: int = 0,
) -> dict[str, Any]:
    """รับผลจาก ``analyze_batch`` แล้วคืนสรุปพร้อม "ความครบถ้วน" ของรอบนั้น.

    ``fetched`` = จำนวนข่าวที่ดึงมาได้ก่อนส่งเข้าโมเดล (ปล่อยว่าง = เท่ากับที่วิเคราะห์ได้)
    ``failed_batches`` = จำนวน batch ที่เรียกโมเดลไม่สำเร็จ

    คีย์ที่เพิ่มจากสัญญาเดิม:

    * ``analyzed`` / ``fetched`` / ``coverage_pct`` — วิเคราะห์ได้กี่ชิ้นจากกี่ชิ้น
      (``coverage_pct`` เป็น ``None`` เมื่อไม่มีข่าวเลย เพราะสัดส่วนนั้นไม่นิยาม
      ห้ามแทนด้วย 0.0 ซึ่งอ่านได้ว่า "ล้มเหลว 100%")
    * ``complete`` — ครบทุกชิ้นและไม่มี batch ล้ม
    * ``unclassified`` — แถวที่โมเดลไม่ได้ให้ป้าย หรือให้ป้ายที่ระบบไม่รู้จัก
      (ไม่เข้าตัวหารของ ``score`` — นับเป็น neutral คือการกุตัวเลข)

    ``overall_sentiment`` เป็น ``"unknown"`` และ ``score``/``avg_confidence`` เป็น
    ``None`` เมื่อไม่มีแถวที่จัดป้ายได้เลย — ผู้เรียกต้องแยก "ยังไม่รู้" ออกจาก "กลาง ๆ"
    """
    rows = list(results or [])
    analyzed = len(rows)

    positive = 0
    negative = 0
    neutral = 0
    unclassified = 0
    conf_sum = 0.0
    conf_n = 0

    for r in rows:
        if not isinstance(r, dict):
            unclassified += 1
            continue
        s = str(r.get("sentiment", "") or "").strip().lower()
        if s == "positive":
            positive += 1
        elif s == "negative":
            negative += 1
        elif s == "neutral":
            neutral += 1
        else:
            unclassified += 1

        try:
            c = float(r.get("confidence"))
        except (TypeError, ValueError):
            continue
        conf_sum += c
        conf_n += 1

    classified = positive + negative + neutral
    score = round((positive - negative) / classified, 4) if classified > 0 else None
    avg_confidence = round(conf_sum / conf_n, 4) if conf_n > 0 else None

    if score is None:
        overall = "unknown"
    elif score > 0.1:
        overall = "positive"
    elif score < -0.1:
        overall = "negative"
    else:
        overall = "neutral"

    fetched_n = analyzed if fetched is None else max(int(fetched), 0)
    failed_n = max(int(failed_batches or 0), 0)
    coverage_pct = round(analyzed / fetched_n * 100, 1) if fetched_n > 0 else None
    complete = failed_n == 0 and analyzed >= fetched_n

    return {
        "symbol": "",
        # total_articles = จำนวนที่วิเคราะห์ได้จริง (คงชื่อเดิมไว้ให้คอลัมน์ฐานข้อมูลเดิมอ่านได้)
        "total_articles": analyzed,
        "analyzed": analyzed,
        "fetched": fetched_n,
        "coverage_pct": coverage_pct,
        "complete": complete,
        "failed_batches": failed_n,
        "positive": positive,
        "negative": negative,
        "neutral": neutral,
        "unclassified": unclassified,
        "avg_confidence": avg_confidence,
        "overall_sentiment": overall,
        "score": score,
    }
