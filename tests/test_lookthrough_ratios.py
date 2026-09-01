# -*- coding: utf-8 -*-
"""อัตราส่วนพื้นฐานที่ทะลุกอง ETF ลงไปคิดจากหุ้นข้างใน.

P/E หรือ ROE ของ ticker ``VOO`` เองไม่มีความหมาย — กองไม่ได้ทำธุรกิจ มันถือหุ้น
หลายร้อยตัว ตัวเลขที่แปลได้จึงต้องถ่วงน้ำหนักจากของข้างในเท่านั้น

สามอย่างที่ไฟล์นี้ตรึงไว้ เพราะพังแล้ว "ยังได้ตัวเลขที่ดูปกติ":

1. **price multiple ต้องรวมแบบฮาร์มอนิก** ระดับพอร์ตคือ ``ΣP/ΣE`` ซึ่งคือค่าเฉลี่ย
   ฮาร์มอนิกถ่วงน้ำหนัก ไม่ใช่เลขคณิต — วัดกับตัวอย่างมือ P/E 10 กับ 30 น้ำหนักเท่ากัน
   ให้ 15.0 ส่วนเลขคณิตให้ 20.0 คือแพงกว่าความจริง 33%
2. **ค่าที่ผู้ให้ข้อมูลส่งมาผิดต้องถูกตัด** วัดจริง 2026-08-31: yfinance คืน
   ``priceToBook`` ของ BRK-B เป็น ``0.00096`` (ของจริง ~1.6) ค่าเดียวนี้ลากค่าเฉลี่ย
   ฮาร์มอนิกทั้งพอร์ตจาก **7.35 เหลือ 0.072** เพราะฮาร์มอนิกไวกับค่าที่เล็กที่สุด
3. **"ไม่มีข้อมูล" กับ "มีค่าแต่ใช้ไม่ได้" เป็นคนละเรื่อง** และไม่มีอันไหนกลายเป็น 0
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from portfolio import lookthrough as lt  # noqa: E402


def _result(*pairs: tuple[str, float]) -> dict:
    return {
        "holdings": [
            {"symbol": sym, "name": sym, "weight_pct": w, "via": ["VOO"]} for sym, w in pairs
        ],
        "unavailable": {},
    }


def _with_infos(infos: dict[str, dict]):
    return patch.object(lt, "_stock_info", lambda symbol: infos.get(symbol, {}))


class TestHarmonicVsArithmetic:
    def test_pe_รวมแบบฮาร์มอนิก_ไม่ใช่เลขคณิต(self):
        infos = {"A": {"trailingPE": 10.0}, "B": {"trailingPE": 30.0}}
        with _with_infos(infos):
            out = lt.weighted_ratios(_result(("A", 50.0), ("B", 50.0)))
        assert out["ratios"]["pe"]["value"] == pytest.approx(15.0)
        assert out["ratios"]["pe"]["value"] != pytest.approx(20.0), "20.0 คือค่าเฉลี่ยเลขคณิต"

    def test_ฮาร์มอนิกถ่วงน้ำหนักตามสัดส่วนจริง(self):
        """น้ำหนัก 75/25 บน P/E 10/30 → 1/((0.75/10)+(0.25/30)) = 12.0."""
        infos = {"A": {"trailingPE": 10.0}, "B": {"trailingPE": 30.0}}
        with _with_infos(infos):
            out = lt.weighted_ratios(_result(("A", 75.0), ("B", 25.0)))
        assert out["ratios"]["pe"]["value"] == pytest.approx(12.0)

    def test_roe_รวมแบบเลขคณิต(self):
        infos = {"A": {"returnOnEquity": 0.20}, "B": {"returnOnEquity": 0.10}}
        with _with_infos(infos):
            out = lt.weighted_ratios(_result(("A", 50.0), ("B", 50.0)))
        assert out["ratios"]["roe"]["value"] == pytest.approx(15.0)  # เป็น % แล้ว

    def test_วิธีรวมถูกรายงานออกมาให้หน้าจออ่านได้(self):
        with _with_infos({"A": {"trailingPE": 10.0, "returnOnEquity": 0.2}}):
            out = lt.weighted_ratios(_result(("A", 100.0)))
        assert out["ratios"]["pe"]["method"] == "harmonic"
        assert out["ratios"]["roe"]["method"] == "arithmetic"


class TestBadProviderValues:
    def test_ค่าที่เล็กจนเป็นไปไม่ได้ถูกตัด(self):
        """เคสจริงของ BRK-B — ถ้าไม่ตัด ค่าเฉลี่ยจะพังทั้งพอร์ต."""
        infos = {"GOOD": {"priceToBook": 8.0}, "BRK-B": {"priceToBook": 0.00096532935}}
        with _with_infos(infos):
            out = lt.weighted_ratios(_result(("GOOD", 50.0), ("BRK-B", 50.0)))
        pb = out["ratios"]["pb"]
        assert pb["value"] == pytest.approx(8.0), "ต้องเหลือแต่ตัวที่ใช้ได้"
        assert "BRK-B" in pb["not_meaningful"]
        assert pb["weight_pct"] == pytest.approx(50.0), "ตัวหารต้องหดตามของที่ตัดออก"

    def test_ถ้าไม่ตัดค่าเพี้ยนผลจะพัง_พิสูจน์ว่าด่านนี้ทำงาน(self):
        """ยืนยันขนาดของปัญหา: ค่าเพี้ยนตัวเดียวลากผลจาก 8.0 ลงต่ำกว่า 0.01."""
        w, good, bad = 0.5, 8.0, 0.00096532935
        unguarded = 1.0 / (w / good + w / bad)
        assert unguarded < 0.01

    def test_pe_ติดลบ_ของบริษัทขาดทุนถูกตัดและรายงาน(self):
        infos = {"A": {"trailingPE": 10.0}, "LOSS": {"trailingPE": -5.0}}
        with _with_infos(infos):
            out = lt.weighted_ratios(_result(("A", 50.0), ("LOSS", 50.0)))
        pe = out["ratios"]["pe"]
        assert pe["value"] == pytest.approx(10.0)
        assert pe["not_meaningful"] == ["LOSS"]

    def test_roe_ติดลบเป็นข้อเท็จจริง_ต้องไม่ถูกตัด(self):
        """ต่างจาก P/E: ROE ติดลบคือบริษัทขาดทุนจริง ไม่ใช่ข้อมูลเสีย."""
        infos = {"A": {"returnOnEquity": 0.20}, "B": {"returnOnEquity": -0.10}}
        with _with_infos(infos):
            out = lt.weighted_ratios(_result(("A", 50.0), ("B", 50.0)))
        roe = out["ratios"]["roe"]
        assert roe["value"] == pytest.approx(5.0)
        assert roe["not_meaningful"] == []

    def test_ค่าที่ใหญ่เกินจริงถูกตัดด้วย(self):
        infos = {"A": {"trailingPE": 20.0}, "B": {"trailingPE": 5000.0}}
        with _with_infos(infos):
            out = lt.weighted_ratios(_result(("A", 50.0), ("B", 50.0)))
        assert out["ratios"]["pe"]["not_meaningful"] == ["B"]

    def test_ค่าที่แปลกแต่เป็นไปได้จริงต้องผ่าน(self):
        """ธนาคาร P/B 0.3 และหุ้นเติบโต P/E 300 เป็นของจริง ห้ามตัด."""
        with _with_infos({"BANK": {"priceToBook": 0.3, "trailingPE": 300.0}}):
            out = lt.weighted_ratios(_result(("BANK", 100.0)))
        assert out["ratios"]["pb"]["not_meaningful"] == []
        assert out["ratios"]["pe"]["not_meaningful"] == []


class TestMissingIsNotZero:
    def test_ไม่มีข้อมูลเลยต้องได้_None_ไม่ใช่ศูนย์(self):
        with _with_infos({"A": {"trailingPE": 10.0}}):
            out = lt.weighted_ratios(_result(("A", 100.0)))
        assert out["ratios"]["roe"]["value"] is None
        assert out["ratios"]["roe"]["weight_pct"] == 0.0

    def test_แยก_ไม่มีข้อมูล_ออกจาก_ใช้ไม่ได้(self):
        infos = {"A": {"trailingPE": 10.0}, "NODATA": {}, "LOSS": {"trailingPE": -1.0}}
        with _with_infos(infos):
            out = lt.weighted_ratios(_result(("A", 40.0), ("NODATA", 30.0), ("LOSS", 30.0)))
        pe = out["ratios"]["pe"]
        assert pe["missing"] == ["NODATA"]
        assert pe["not_meaningful"] == ["LOSS"]

    def test_ตัวหารของแต่ละอัตราส่วนเป็นของตัวเอง(self):
        infos = {"A": {"trailingPE": 10.0, "returnOnEquity": 0.2}, "B": {"trailingPE": 20.0}}
        with _with_infos(infos):
            out = lt.weighted_ratios(_result(("A", 50.0), ("B", 50.0)))
        assert out["ratios"]["pe"]["weight_pct"] == pytest.approx(100.0)
        assert out["ratios"]["roe"]["weight_pct"] == pytest.approx(50.0)

    def test_ไม่มีหุ้นเลยต้อง_raise_ไม่ใช่ตารางว่าง(self):
        with pytest.raises(ValueError):
            lt.weighted_ratios({"holdings": [], "unavailable": {}})


class TestCoverageMustBeVisible:
    def test_coverage_บอกน้ำหนักรวมของหุ้นที่ทะลุเจอ(self):
        with _with_infos({"A": {"trailingPE": 10.0}, "B": {"trailingPE": 20.0}}):
            out = lt.weighted_ratios(_result(("A", 25.0), ("B", 14.0)))
        assert out["coverage_pct"] == pytest.approx(39.0)
        assert out["stocks"] == 2

    def test_notes_บอกทั้งขอบล่างและวิธีรวม(self):
        with _with_infos({"A": {"trailingPE": 10.0}}):
            out = lt.weighted_ratios(_result(("A", 30.0)))
        assert "top-10" in out["notes"]
        assert "ฮาร์มอนิก" in out["notes"]

    def test_notes_บอกด้วยเมื่อมีกองที่ดึงโครงสร้างไม่ได้(self):
        result = _result(("A", 30.0))
        result["unavailable"] = {"GLDM": "ไม่มี funds_data"}
        with _with_infos({"A": {"trailingPE": 10.0}}):
            out = lt.weighted_ratios(result)
        assert "GLDM" in out["notes"]


class TestRatiosStayDescriptive:
    """ตัวเลขชุดนี้ห้ามไหลเข้าเลขคะแนนหรือการจัดสรร DCA."""

    def test_financial_model_ไม่เรียก_weighted_ratios(self):
        source = (_ROOT / "analysis" / "financial_model.py").read_text(encoding="utf-8")
        assert "weighted_ratios" not in source and "lookthrough" not in source

    def test_targets_และ_dca_ไม่เรียก_weighted_ratios(self):
        for rel in ("portfolio/targets.py", "portfolio/dca.py"):
            source = (_ROOT / rel).read_text(encoding="utf-8")
            assert "weighted_ratios" not in source
