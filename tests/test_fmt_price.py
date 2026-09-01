# -*- coding: utf-8 -*-
"""``main._fmt_price`` — ราคาที่อ่านไม่ได้ต้องพิมพ์ ``$?`` ห้ามกลายเป็น ``$0.00``.

ทำไมไฟล์นี้ถึงมีอยู่ (AUDIT_ROUND2_2026-08-07):

    grep -rn '_fmt_price' tests/ → ไม่มีผลลัพธ์ (ศูนย์บรรทัด)
    mutation: `return "$?"` → `return "$0.00"`
    → 1297 passed, 5 deselected, 3 xfailed  (ไม่มีเทสต์ไหนแดง)

    บนจอจริง (format_price_alert_report ของ triggered ที่ target_price=None):
      MUTATED : • VOO above $0.00 (ราคาล่าสุด $0.00)
      PRISTINE: • VOO above $? (ราคาล่าสุด $?)

ฟังก์ชันนี้อยู่บนเส้นทางเงินจริง — ข้อความ triggered ที่พิมพ์ลง stdout ของ scheduler
และส่งเข้า Discord  ``$0.00`` = "ราคาเป้าหมาย 0 ดอลลาร์" คือตัวเลขที่ระบบแต่งขึ้นเอง
จากข้อมูลที่หายไป ผิดกฎข้อแรกของโปรเจกต์ ("ดึงไม่สำเร็จ" ≠ "0")

เทสต์แตะเฉพาะฟังก์ชันจัดรูปแบบ + ``format_price_alert_report`` (ฟังก์ชันบริสุทธิ์ทั้งคู่)
ไม่มีการอ่าน/เขียนคลัง alert จริง ไม่มีเน็ต ไม่มี webhook
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import main as scheduler_main  # noqa: E402


class TestUnreadablePriceStaysAQuestionMark:
    """ค่าที่ไม่ใช่ราคา ต้องออกมาเป็น ``$?`` — ห้ามเป็นตัวเลขใด ๆ ทั้งสิ้น."""

    @pytest.mark.parametrize(
        "value",
        [
            None,  # ไม่มีคีย์ราคาในระเบียน alert
            "n/a",
            "",
            "ห้าร้อย",
            float("nan"),  # ราคาที่คำนวณไม่ได้ — ไม่ใช่ราคา
            float("inf"),
            float("-inf"),
            [],
            {},
        ],
    )
    def test_returns_question_mark(self, value):
        assert scheduler_main._fmt_price(value) == "$?"

    @pytest.mark.parametrize("value", [None, "n/a", float("nan")])
    def test_never_fabricates_zero(self, value):
        """ตาข่ายตรงกับ mutation ที่รอดมาได้: ``$0.00`` ต้องไม่มีทางโผล่จากค่าที่อ่านไม่ได้."""
        assert "0.00" not in scheduler_main._fmt_price(value)
        assert "0" not in scheduler_main._fmt_price(value)


class TestRealPriceStillPrints:
    """อีกด้านของตาข่าย: ราคาจริงต้องยังพิมพ์เป็นตัวเลขเหมือนเดิม (ห้ามแก้จนกลายเป็น ``$?`` หมด)."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0.0, "$0.00"),  # ศูนย์ **จริง** ที่ส่งมาเป็นตัวเลข ยังพิมพ์ 0.00 ได้
            (78.5, "$78.50"),
            (1234.5, "$1,234.50"),
            ("400", "$400.00"),
            (400, "$400.00"),
        ],
    )
    def test_formats_numbers(self, value, expected):
        assert scheduler_main._fmt_price(value) == expected


class TestReportPathUsesIt:
    """เส้นทางที่ผู้ใช้เห็นจริง: บรรทัด triggered ใน ``format_price_alert_report``."""

    @staticmethod
    def _result(triggered: list[dict]) -> dict:
        return {
            "success": True,
            "store_error": False,
            "checked": len(triggered),
            "triggered": triggered,
            "unchecked": [],
            "store_status": {"status": "ok", "path": "/tmp/x.json", "pending": 0, "triggered": 1, "error": None},
            "daily_summary": "(summary)",
            "daily_discord_result": {"success": True},
        }

    def test_broken_prices_are_reported_as_unknown_not_zero(self):
        text = scheduler_main.format_price_alert_report(
            self._result(
                [
                    {
                        "id": "t1",
                        "ticker": "VOO",
                        "alert_type": "above",
                        "target_price": None,
                        "current_price": "n/a",
                    }
                ]
            )
        )
        assert "VOO above $?" in text
        assert "ราคาล่าสุด $?" in text
        assert "$0.00" not in text, "ราคาที่อ่านไม่ได้ห้ามถูกรายงานเป็น $0.00 เข้า Discord/stdout"

    def test_normal_prices_are_unchanged(self):
        text = scheduler_main.format_price_alert_report(
            self._result(
                [
                    {
                        "id": "t1",
                        "ticker": "SCHD",
                        "alert_type": "below",
                        "target_price": 80.0,
                        "current_price": 78.5,
                    }
                ]
            )
        )
        assert "SCHD below $80.00" in text
        assert "ราคาล่าสุด $78.50" in text
        assert "$?" not in text
