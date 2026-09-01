# -*- coding: utf-8 -*-
"""anomaly ของกระแสเงินสดต้องบอกว่าตัวเลขมาจาก **เดือนไหน** และฐานกี่เดือน.

``detect_category_anomalies`` เทียบ "เดือนที่จบแล้วและมีธุรกรรมล่าสุด" กับค่าเฉลี่ย
ของเดือนก่อน ๆ — เดือนนั้น **ไม่ใช่ "เดือนที่แล้ว" เสมอไป** ถ้าผู้ใช้หยุดบันทึกไป
หลายเดือน มันคือเดือนเก่าที่สุดเท่าที่ยังมีข้อมูล

วัดจริง 2026-09-01 ก่อนแก้: ธุรกรรมล่าสุดเดือน 2026-03 วันนี้ 2026-09 API ตอบ
"ค่าอาหาร +76.5%" โดย**ไม่มีช่องไหนบอกว่าเป็นเดือนไหน** หน้าจอจึงแสดงเป็นข่าวของ
เดือนนี้ได้เต็มปาก — เป็นความผิดชนิดเดียวกับตาราง Risk ที่เทียบ ETF คนละช่วงเวลา
โดยไม่บอก (ตัวเลขที่ไม่มีหน้าต่างกำกับ = ตัวเลขที่อ่านผิดได้)
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.models.cashflow_models import TransactionItem  # noqa: E402
from backend.services.cashflow_service import detect_category_anomalies  # noqa: E402


def _tx(d: date, category: str, amount: float, kind: str = "expense") -> TransactionItem:
    return TransactionItem(
        date=d, description=category, amount=amount, category=category, type=kind
    )


def _stale_rows() -> list[TransactionItem]:
    """ธุรกรรมหยุดที่ 2026-03 — ค่าอาหารเดือนนั้นพุ่งจากฐาน 2 เดือนก่อนหน้า."""
    return [
        _tx(date(2026, 1, 10), "อาหาร", 5_000.0),
        _tx(date(2026, 2, 10), "อาหาร", 5_200.0),
        _tx(date(2026, 3, 10), "อาหาร", 9_000.0),
    ]


class TestAnomalyCarriesItsWindow:
    def test_บอกเดือนที่ตัวเลขมาจาก(self):
        rows = detect_category_anomalies(_stale_rows(), today=date(2026, 9, 1))
        assert rows, "ควรตรวจเจอ anomaly"
        assert rows[0].month == "2026-03", "ต้องบอกเดือนจริง ไม่ใช่ปล่อยให้เดาว่าเดือนที่แล้ว"

    def test_บอกจำนวนเดือนและช่วงของฐาน(self):
        row = detect_category_anomalies(_stale_rows(), today=date(2026, 9, 1))[0]
        assert row.baseline_months == 2
        assert row.baseline_start == "2026-01"
        assert row.baseline_end == "2026-02"

    def test_เดือนที่รายงานต้องไม่ใช่เดือนปัจจุบันเมื่อข้อมูลเก่า(self):
        row = detect_category_anomalies(_stale_rows(), today=date(2026, 9, 1))[0]
        assert row.month != "2026-09"
        assert row.month != "2026-08", "ไม่ใช่ 'เดือนที่แล้ว' ด้วย"

    def test_หมวดใหม่ก็ต้องพกหน้าต่างเหมือนกัน(self):
        rows = _stale_rows() + [_tx(date(2026, 3, 20), "ท่องเที่ยว", 8_000.0)]
        out = detect_category_anomalies(rows, today=date(2026, 9, 1))
        new_rows = [r for r in out if r.kind == "new_category"]
        assert new_rows, "หมวดที่เพิ่งโผล่ต้องถูกตรวจเจอ"
        assert new_rows[0].month == "2026-03"
        assert new_rows[0].baseline_months == 2

    def test_ตัวเลขเดิมไม่เปลี่ยน(self):
        """เพิ่มช่องบอกหน้าต่างต้องไม่ขยับตัวเลขที่คำนวณอยู่แล้ว."""
        row = detect_category_anomalies(_stale_rows(), today=date(2026, 9, 1))[0]
        assert row.avg_monthly == pytest.approx(5_100.0)
        assert row.last_month == pytest.approx(9_000.0)
        assert row.change_percent == pytest.approx(76.5)

    def test_ข้อมูลสดก็ยังบอกเดือนถูก(self):
        rows = [
            _tx(date(2026, 6, 10), "อาหาร", 5_000.0),
            _tx(date(2026, 7, 10), "อาหาร", 5_200.0),
            _tx(date(2026, 8, 10), "อาหาร", 9_000.0),
        ]
        out = detect_category_anomalies(rows, today=date(2026, 9, 1))
        assert out[0].month == "2026-08"
        assert out[0].baseline_end == "2026-07"
