# -*- coding: utf-8 -*-
"""ตาราง "คะแนนรายมิติ" ที่ส่งให้ AI ต้องบอกได้ว่าคะแนนรวมมาจากไหน — และห้ามโกหกเพดาน.

เดิม ``_build_user_message`` ส่งเข้า LLM แค่ ``total_pct`` ตัวเดียว AI จึงเขียนได้แค่
"VOO คะแนน 72" อธิบายไม่ได้ว่ามาจากมิติไหน ทั้งที่ ``score_from_prices`` คำนวณครบ 8 มิติ
อยู่แล้ว (ยังคงกฎ "AI อธิบาย โค้ดคำนวณ" — ตัวเลขทุกตัวมาจาก Python ไม่ให้ AI บวกเอง)

สองอย่างที่ไฟล์นี้ตรึงไว้ เพราะพังแล้วไม่มีอะไรแดง:

* มิติที่ไม่มีข้อมูลต้องเขียนว่า **"ตัดออก" ไม่ใช่ 0** — 0 อ่านว่า "วัดแล้วได้ศูนย์"
  ซึ่งจะกลายเป็น AI เขียนว่ากองนั้นมีจุดอ่อนที่ไม่มีอยู่จริง (C1)
* เพดานของโมเมนตัมมาจาก ``momentum_max`` ของแถวนั้น ไม่ใช่ค่าคงที่ ``MOMENTUM_MAX``
  (FIX_PLAN 1.5 — หน้าต่างที่คำนวณไม่ได้หดทั้งคะแนนและเพดานพร้อมกัน)
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from analysis import ai_advisor  # noqa: E402
from analysis import financial_model as fm  # noqa: E402


def _full_row() -> dict:
    return {
        "ticker": "VOO",
        "data_ok": True,
        "price": 500.0,
        "ma50": 490.0,
        "ma200": 470.0,
        "rsi": 55.0,
        "total_pct": 72.0,
        "signal": "Buy",
        "trend_score": 40,
        "timing_score": 20,
        "momentum_score": 20,
        "momentum_max": fm.MOMENTUM_MAX,
        "momentum_available": True,
        "dividend_score": 4,
        "dividend_available": True,
        "volatility_score": 8,
        "valuation_score": 5,
        "valuation_available": True,
        "relative_strength_score": 3,
        "relative_strength_available": True,
        "expense_score": 5,
        "expense_available": True,
        "total_score": 105,
        "max_score": 130,
    }


def _dimension_table(rows: list[dict]) -> str:
    msg = ai_advisor._build_user_message(rows, {"vix": 14.0}, None)
    start = msg.index("=== คะแนนรายมิติ")
    return msg[start : msg.index("=== แผนจัดสรร")]


class TestDimensionTableReachesTheModel:
    def test_ทุกมิติมีคอลัมน์ของตัวเอง(self):
        table = _dimension_table([_full_row()])
        for name, _, _, _ in ai_advisor._SCORE_DIMENSIONS:
            assert name in table, f"มิติ {name} หายจากตารางที่ส่งให้ AI"

    def test_ช่องคะแนนเป็นรูป_คะแนนทับเพดาน(self):
        table = _dimension_table([_full_row()])
        # ยกมาจาก dict ตรง ๆ ไม่ให้ AI ต้องบวกเอง (AI อธิบาย โค้ดคำนวณ)
        assert "40/40" in table and "20/30" in table and "4/10" in table
        assert "3/5" in table and "8/10" in table
        assert "105/130" in table  # คะแนนรวม/เพดานรวมของแถว

    def test_มิติที่ไม่มีข้อมูลเขียนว่าตัดออก_ไม่ใช่ศูนย์(self):
        row = _full_row()
        row.update(
            {
                "ticker": "GLDM",
                "dividend_score": 0,
                "dividend_available": False,
                "valuation_score": 0,
                "valuation_available": False,
                "expense_score": 0,
                "expense_available": False,
            }
        )
        table = _dimension_table([row])
        assert table.count(ai_advisor._DIMENSION_EXCLUDED) >= 3
        # 0/10 และ 0/5 คือ "วัดแล้วได้ศูนย์" — ต้องไม่โผล่จากมิติที่ไม่มีข้อมูล
        assert "0/10" not in table and "0/5" not in table

    def test_โมเมนตัมที่คำนวณไม่ได้ไม่ถูกนับเป็นศูนย์(self):
        row = _full_row()
        row.update({"momentum_score": None, "momentum_max": 0, "momentum_available": False})
        table = _dimension_table([row])
        assert f"0/{fm.MOMENTUM_MAX}" not in table
        assert ai_advisor._DIMENSION_EXCLUDED in table

    def test_เพดานโมเมนตัมที่หดแล้วต้องแสดงตามจริง(self):
        """มีข้อมูลหน้าต่างเดียว: ได้ 10 จากเพดาน 10 ไม่ใช่ 10 จาก 20."""
        row = _full_row()
        half = fm.MOMENTUM_MAX // 2
        row.update({"momentum_score": half, "momentum_max": half, "momentum_available": True})
        table = _dimension_table([row])
        assert f"{half}/{half}" in table
        assert f"{half}/{fm.MOMENTUM_MAX}" not in table

    def test_คำอธิบายบอก_AI_ว่าตัดออกไม่ใช่จุดอ่อน(self):
        table = _dimension_table([_full_row()])
        assert "ไม่ใช่ได้ 0 คะแนน" in table

    def test_เพดานยกมาจาก_financial_model_ไม่ใช่เลขที่พิมพ์ซ้ำ(self):
        """ถ้าใครขยับเพดานที่ financial_model ตารางนี้ต้องขยับตามเอง."""
        by_key = {key: mx for _, key, _, mx in ai_advisor._SCORE_DIMENSIONS}
        assert by_key["trend_score"] == fm.TREND_MAX
        assert by_key["timing_score"] == fm.TIMING_MAX
        assert by_key["momentum_score"] == fm.MOMENTUM_MAX
        assert by_key["dividend_score"] == fm.DIVIDEND_MAX
        assert by_key["volatility_score"] == fm.VOLATILITY_MAX
        assert by_key["valuation_score"] == fm.VALUATION_MAX
        assert by_key["relative_strength_score"] == fm.RELATIVE_STRENGTH_MAX
        assert by_key["expense_score"] == fm.EXPENSE_MAX

    def test_ครบทั้งแปดมิติที่_score_from_prices_คำนวณ(self):
        assert len(ai_advisor._SCORE_DIMENSIONS) == 8


class TestSentimentUnknownIsNotTheSameAsChecked:
    """ลิสต์เตือนที่ว่างเปล่าเกิดได้จากสองเหตุ — prompt ต้องแยกให้ AI เห็น (C1).

    เดิมเขียนรวมเป็น "(ไม่มี ETF ที่ sentiment ลบรุนแรง **หรือ** ไม่มีข้อมูล sentiment)"
    ซึ่ง AI อ่านแล้วสรุปได้ว่า "ข่าวรอบนี้ไม่มีอะไรน่ากังวล" ทั้งที่ job อาจไม่เคยรันเลย
    — ความผิดชนิดเดียวกับ "ดึงข่าวไม่ได้ ≠ ไม่มีข่าว"
    """

    def _sentiment_section(self, ctx) -> str:
        msg = ai_advisor._build_user_message([_full_row()], {"vix": 14.0}, None, sentiment=ctx)
        start = msg.index("=== Sentiment warnings")
        return msg[start : msg.index("=== Macro ===")]

    def test_ไม่มีข้อมูลเลย_ต้องบอกว่ายังไม่รู้(self):
        section = self._sentiment_section(ai_advisor.SentimentContext())
        assert "ยังไม่รู้ผล" in section
        assert "ห้ามสรุปว่าข่าวรอบนี้เป็นกลาง" in section

    def test_ตรวจแล้วไม่เจอ_ต้องบอกจำนวนที่ตรวจ(self):
        ctx = ai_advisor.SentimentContext(warnings=[], symbols_with_data=5)
        section = self._sentiment_section(ctx)
        assert "ตรวจแล้ว 5 สัญลักษณ์" in section
        assert "ยังไม่รู้ผล" not in section

    def test_สองสถานะนี้ต้องไม่ให้ข้อความเดียวกัน(self):
        unknown = self._sentiment_section(ai_advisor.SentimentContext())
        checked = self._sentiment_section(
            ai_advisor.SentimentContext(warnings=[], symbols_with_data=5)
        )
        assert unknown != checked

    def test_มีตัวลบรุนแรง_ต้องขึ้นรายตัว(self):
        ctx = ai_advisor.SentimentContext(
            warnings=[{"ticker": "XLV", "score": -0.55, "total_articles": 12}],
            symbols_with_data=5,
        )
        section = self._sentiment_section(ctx)
        assert "XLV" in section and "-0.55" in section and "12 ข่าว" in section

    def test_available_ผูกกับจำนวนสัญลักษณ์ที่มีข้อมูล(self):
        assert ai_advisor.SentimentContext().available is False
        assert ai_advisor.SentimentContext(symbols_with_data=1).available is True

    def test_ฐานว่างต้องได้สถานะยังไม่รู้_ไม่ใช่ตรวจแล้ว(self, monkeypatch):
        monkeypatch.setattr(ai_advisor, "get_latest_sentiment_summaries", lambda t: [])
        ctx = ai_advisor._sentiment_context(["VOO"])
        assert ctx.available is False and ctx.warnings == []

    def test_มีข้อมูลแต่ไม่ถึงเกณฑ์_ต้องนับว่าตรวจแล้ว(self, monkeypatch):
        monkeypatch.setattr(
            ai_advisor,
            "get_latest_sentiment_summaries",
            lambda t: [{"symbol": "VOO", "score": -0.10, "total_articles": 30}],
        )
        ctx = ai_advisor._sentiment_context(["VOO"])
        assert ctx.available is True
        assert ctx.warnings == []

    def test_ข่าวน้อยเกินเกณฑ์ไม่นับเป็นคำเตือน(self, monkeypatch):
        """คะแนนลบแรงแต่มีข่าวไม่ถึงขั้นต่ำ = ยังไม่พอสรุป ห้ามขึ้นเตือน."""
        monkeypatch.setattr(
            ai_advisor,
            "get_latest_sentiment_summaries",
            lambda t: [
                {
                    "symbol": "VOO",
                    "score": -0.90,
                    "total_articles": ai_advisor.SENTIMENT_WARNING_MIN_ARTICLES - 1,
                }
            ],
        )
        assert ai_advisor._sentiment_context(["VOO"]).warnings == []
