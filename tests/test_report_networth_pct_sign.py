# -*- coding: utf-8 -*-
"""เปอร์เซ็นต์การเปลี่ยนแปลงบนฐานที่ **ไม่เป็นบวก** ต้องไม่ถูกรายงานเป็นตัวเลข.

``change_thb / previous_nw`` ใช้ได้ก็ต่อเมื่อฐานเป็นบวก มูลค่าสุทธิ**ติดลบ**ได้จริง
(หนี้บ้าน/หนี้เรียนมากกว่าทรัพย์สิน ซึ่งเป็นสถานะปกติของคนเริ่มต้น) และเมื่อฐานติดลบ
เครื่องหมายจะพลิก — วัดจริง 2026-09-01:

* −1,000 → −500 = **ดีขึ้น 500 บาท** แต่สูตรเดิมรายงาน **−50%** (อ่านว่าแย่ลง)
* −1,000 → −1,500 = **แย่ลง 500 บาท** แต่รายงาน **+50%** (อ่านว่าดีขึ้น)

อันตรายกว่าการหารด้วยศูนย์ตรงที่มัน **ได้ตัวเลขที่หน้าตาใช้ได้** ไม่มีอะไรพัง และ
ตัวเลขนี้ไหลเข้าทั้งรายงานที่ส่ง Telegram และพรอมป์ที่ให้ LLM อธิบาย ⇒ AI จะเขียน
บรรยายทิศทางที่กลับด้านจากความจริง

ยอดบาทยังถูกเสมอและต้องแสดงต่อ — สิ่งที่ตัดออกคือ "เปอร์เซ็นต์" เท่านั้น
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.services import report_service  # noqa: E402


class _Snap:
    def __init__(self, month: str, net_worth: float) -> None:
        self.snapshot_date = f"{month}-05"
        self.net_worth_thb = net_worth


def _change(monkeypatch, current: float, previous: float) -> dict:
    """เรียก get_networth_change โดยแทนที่ชั้นฐานข้อมูลด้วยสองสแนปช็อตที่กำหนดเอง."""
    from datetime import date

    this_month = date.today().strftime("%Y-%m")
    year, month = int(this_month[:4]), int(this_month[5:7])
    prev_month = f"{year - 1:04d}-12" if month == 1 else f"{year:04d}-{month - 1:02d}"

    monkeypatch.setattr(
        report_service.networth_service,
        "get_history",
        lambda db, months=3: [_Snap(this_month, current), _Snap(prev_month, previous)],
    )
    return report_service.get_networth_change(db=None)


class TestPositiveBaselineUnchanged:
    def test_ฐานบวกยังคิดเปอร์เซ็นต์ตามเดิม(self, monkeypatch):
        out = _change(monkeypatch, 110_000.0, 100_000.0)
        assert out["change_thb"] == pytest.approx(10_000.0)
        assert out["change_pct"] == pytest.approx(10.0)
        assert out["change_pct_note"] == ""

    def test_ฐานบวกขาดทุนก็ยังคิดได้(self, monkeypatch):
        out = _change(monkeypatch, 90_000.0, 100_000.0)
        assert out["change_pct"] == pytest.approx(-10.0)


class TestNonPositiveBaselineRefusesPercent:
    def test_ฐานติดลบและดีขึ้น_ต้องไม่รายงานเป็นลบ(self, monkeypatch):
        out = _change(monkeypatch, -500.0, -1_000.0)
        assert out["change_thb"] == pytest.approx(500.0), "ยอดบาทต้องยังถูกและเป็นบวก"
        assert out["change_pct"] is None, "สูตรเดิมให้ -50% ซึ่งอ่านว่าแย่ลง"
        assert "ติดลบ" in out["change_pct_note"]

    def test_ฐานติดลบและแย่ลง_ต้องไม่รายงานเป็นบวก(self, monkeypatch):
        out = _change(monkeypatch, -1_500.0, -1_000.0)
        assert out["change_thb"] == pytest.approx(-500.0)
        assert out["change_pct"] is None, "สูตรเดิมให้ +50% ซึ่งอ่านว่าดีขึ้น"

    def test_ฐานเป็นศูนย์ยังคงเป็น_None_พร้อมเหตุผลของตัวเอง(self, monkeypatch):
        out = _change(monkeypatch, 5_000.0, 0.0)
        assert out["change_pct"] is None
        assert "0" in out["change_pct_note"]

    def test_เหตุผลสองแบบต้องไม่ใช่ข้อความเดียวกัน(self, monkeypatch):
        zero = _change(monkeypatch, 5_000.0, 0.0)["change_pct_note"]
        negative = _change(monkeypatch, -500.0, -1_000.0)["change_pct_note"]
        assert zero != negative, "ฐานเป็น 0 กับฐานติดลบเป็นคนละสาเหตุ"


class TestRenderedLineTellsTheTruth:
    def test_บรรทัดรายงานไม่โกหกว่าฐานเป็นศูนย์(self):
        """เดิม ``_networth_txt`` ฮาร์ดโค้ดเหตุผลว่า "(ฐานเป็น 0)" เสมอ."""
        line = report_service._networth_txt(
            {
                "available": True,
                "has_baseline": True,
                "current_net_worth_thb": -500.0,
                "previous_net_worth_thb": -1_000.0,
                "change_thb": 500.0,
                "change_pct": None,
                "change_pct_note": "มูลค่าสุทธิเดือนก่อนติดลบ (-1,000 บาท) — ไม่แสดง",
            },
            prefix="Net Worth:",
            unit="บาท",
        )
        assert "ฐานเป็น 0" not in line
        assert "ติดลบ" in line
        assert "+500" in line, "ยอดบาทต้องยังอยู่บนบรรทัด"

    def test_ฐานบวกยังพิมพ์เปอร์เซ็นต์เหมือนเดิม(self):
        line = report_service._networth_txt(
            {
                "available": True,
                "has_baseline": True,
                "current_net_worth_thb": 110_000.0,
                "previous_net_worth_thb": 100_000.0,
                "change_thb": 10_000.0,
                "change_pct": 10.0,
                "change_pct_note": "",
            },
            prefix="Net Worth:",
            unit="บาท",
        )
        assert "+10.0%" in line
