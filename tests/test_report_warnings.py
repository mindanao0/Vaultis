# -*- coding: utf-8 -*-
"""AUDIT_ROUND2_2026-08-07 — คำเตือนสมุดบัญชีต้องเดินทางถึงผู้ใช้ครบทุกชุด.

``portfolio/tracker.py`` ผลิตคำเตือน **สามชุดที่ห้ามยุบรวมกัน** เพราะความหมายต่างกัน:

* ``skipped_rows`` — ข้อมูลไม่ครบ ถูกตัดออก ⇒ ตัวเลขที่เห็น **น้อยกว่า** ความจริง
* ``derived_fx_rows`` — อัตราแลกเปลี่ยนถูกคำนวณย้อนจากยอดบาท ⇒ แถว **ยังถูกนับ**
  แต่ตัวเลขบาททั้งแถวคิดจากอัตราที่ระบบหาเอง ไม่ใช่ค่าที่ผู้ใช้บันทึก
* ``inconsistent_rows`` — ยอดบาทขัดกับ จำนวนหุ้น × ราคา × อัตรา + ค่าธรรมเนียม
  ในแถวเดียวกัน ⇒ **ยังถูกนับ** เช่นกัน และเป็นแถวที่ผู้ใช้ต้องไปแก้

ไฟล์นี้ตรึงสองด้าน:

1. **ด้านผลิต** (``portfolio/tracker.py`` + ``portfolio/fees.py``) — ค่าธรรมเนียมที่
   ไม่ได้บันทึกกับที่บันทึกเป็นค่าติดลบ **ห้ามถูกยุบเป็นค่าเดียวกัน** เดิมปิดท้ายด้วย
   ``fee.where(fee >= 0).fillna(0.0)`` ซึ่งเป็น ``fillna(0`` บนเส้นทางเงินตรง ๆ
   แถวที่กรอกค่าธรรมเนียมติดลบ (ข้อมูลผิดชัด ๆ) จึงให้อัตราย้อน "สวย ๆ" เท่ากับแถว
   ที่ไม่ได้กรอกเลย และไม่เข้าทั้ง ``skipped_rows``/``inconsistent_rows``
2. **ด้านส่งถึงผู้ใช้** (``backend/services/report_service.py``) — รายงานรายเดือน
   (cron วันที่ 1 → Telegram) กับพรอมป์ที่ส่งให้ LLM อธิบาย เคยพิมพ์แค่ชุดแรก

เทสต์ทั้งไฟล์ไม่แตะเน็ต ไม่แตะฐานข้อมูล ไม่เรียก LLM และไม่แตะสมุดบัญชีจริง
(ทุกเคสประกอบ DataFrame/สมุดสังเคราะห์ใน ``tmp_path`` เอง)
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from backend.services import report_service as rs
from portfolio import tracker
from portfolio.fees import DIME_FEE_RATE

HEADER = "tx_id,date,ticker,shares,price_usd,fx_rate_thb,amount_thb,fee_thb,note,tx_type\n"


# --------------------------------------------------------------------------- #
# ตัวช่วยฝั่ง tracker
# --------------------------------------------------------------------------- #
def _row(tx_id: str, **overrides: Any) -> dict[str, Any]:
    """ไม้ซื้อที่ยอดเงินสอดคล้องกันเป๊ะ: 1 หุ้น × 100 USD × 34.0 + ค่าธรรมเนียม 5.1 บาท."""
    base: dict[str, Any] = {
        "tx_id": tx_id,
        "date": "2026-03-05",
        "ticker": "VOO",
        "shares": 1.0,
        "price_usd": 100.0,
        "fx_rate_thb": 34.0,
        "amount_thb": 3405.1,
        "fee_thb": 5.1,
        "tx_type": tracker.TX_BUY,
    }
    base.update(overrides)
    return base


def _frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """DataFrame รูปแบบเดียวกับที่ ``_load_transactions()`` ส่งเข้าด่านต่าง ๆ."""
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    for col in ("shares", "price_usd", "fx_rate_thb", "amount_thb", "fee_thb"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    """สมุดสังเคราะห์ใน ``tmp_path`` — ห้ามแตะสมุดจริงของผู้ใช้เด็ดขาด."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    csv_path = data_dir / "transactions.csv"
    monkeypatch.setattr(tracker, "DATA_DIR", data_dir)
    monkeypatch.setattr(tracker, "TRANSACTIONS_FILE", csv_path)
    monkeypatch.setattr(tracker, "_get_latest_prices", lambda tickers: {})
    monkeypatch.setattr(tracker, "_get_usdthb_rate", lambda: 34.0)
    return csv_path


# --------------------------------------------------------------------------- #
# ฝั่งผลิต — ค่าธรรมเนียมที่ "ไม่ได้บันทึก" ≠ "บันทึกเป็นค่าติดลบ"
# --------------------------------------------------------------------------- #
class TestRecordedFee:
    """``_recorded_fee_thb()`` ต้องแยกช่องว่างออกจากข้อมูลผิด."""

    def test_ค่าธรรมเนียมที่บันทึกไว้ถูกหักออกก่อนคำนวณอัตราย้อน(self):
        """``amount_thb`` รวมค่าธรรมเนียมไว้แล้ว ไม่หักออก = อัตราสูงเกินจริง."""
        df = _frame([_row("f1", fx_rate_thb=float("nan"))])
        derived = tracker._derive_fx_from_amount(df)

        assert derived.iloc[0] == pytest.approx(34.0), (
            "อัตราที่จ่ายจริงคือ (3405.1 − 5.1) / (1 × 100) = 34.0 "
            f"แต่ได้ {derived.iloc[0]!r} — ค่าธรรมเนียมไม่ได้ถูกหักออกก่อนหาร"
        )

    def test_ช่องว่างคือไม่ได้บันทึกจึงคิดเป็นศูนย์(self):
        df = _frame([_row("f2", fee_thb=float("nan"))])
        fee = tracker._recorded_fee_thb(df)

        assert float(fee.iloc[0]) == 0.0, (
            "แถวที่ไม่ได้บันทึกค่าธรรมเนียมต้องหารตรง ๆ ตามยอดที่มี "
            "(ห้ามประมาณค่าธรรมเนียมขึ้นมาหักเอง = กุตัวเลขซ้อนตัวเลข)"
        )

    def test_ค่าติดลบคือข้อมูลผิดห้ามกลืนเป็นศูนย์(self):
        """``fee.where(fee >= 0).fillna(0.0)`` ยุบสองกรณีนี้เป็นค่าเดียวกัน."""
        df = _frame([_row("f3", fee_thb=-5.1)])
        fee = tracker._recorded_fee_thb(df)

        assert pd.isna(fee.iloc[0]), (
            "ค่าธรรมเนียมติดลบแปลว่า 'ซื้อแล้วได้เงินคืน' ซึ่งไม่มีอยู่จริง — "
            f"ต้องเป็น NaN (คำนวณไม่ได้) ไม่ใช่ {fee.iloc[0]!r}"
        )

    def test_ค่าติดลบทำให้คำนวณอัตราย้อนไม่ได้แทนที่จะได้อัตราสวยๆ(self):
        """เดิมแถวติดลบให้ผลเท่ากับแถวที่ไม่ได้กรอกเลย = ข้อมูลผิดถูกทำให้เงียบสนิท."""
        df = _frame(
            [
                _row("neg", fee_thb=-5.1, fx_rate_thb=float("nan")),
                _row("blank", fee_thb=float("nan"), fx_rate_thb=float("nan")),
            ]
        )
        derived = tracker._derive_fx_from_amount(df)

        assert pd.isna(derived.iloc[0]), (
            f"แถวค่าธรรมเนียมติดลบยังได้อัตรา {derived.iloc[0]!r} ออกมาเป็นตัวเลขปกติ"
        )
        assert derived.iloc[1] == pytest.approx(34.051), (
            "แถวที่ไม่ได้บันทึกค่าธรรมเนียมยังต้องคำนวณย้อนได้ตามเดิม (สมมติ fee = 0)"
        )


class TestFeeAssumedZeroIsDisclosed:
    """สมมติฐาน "ค่าธรรมเนียม = 0" ต้องเดินทางไปกับแถวนั้นเสมอ."""

    def test_แถวที่ไม่ได้บันทึกค่าธรรมเนียมต้องติดหมายเหตุสมมติฐาน(self):
        df = _frame([_row("d1", fx_rate_thb=float("nan"), fee_thb=float("nan"))])
        recorded_fx = df["fx_rate_thb"].copy()
        usable_fx = recorded_fx.copy()
        df["fx_rate_thb"] = tracker._derive_fx_from_amount(df)

        rows = tracker._collect_derived_fx_rows(df, usable_fx, recorded_fx)

        assert [r["tx_id"] for r in rows] == ["d1"]
        assert rows[0]["fee_assumed_zero"] is True, (
            "ธงให้ผู้เรียกกรองได้โดยไม่ต้องแกะข้อความ"
        )
        assert f"{DIME_FEE_RATE * 100:.2f}%" in str(rows[0]["reason"]), (
            "หมายเหตุต้องบอกว่าอัตราอาจสูงกว่าจริงราวเท่าไร และตัวเลขนั้นต้องมาจาก "
            f"portfolio/fees.DIME_FEE_RATE ที่เดียว — ได้: {rows[0]['reason']}"
        )

    def test_แถวที่บันทึกค่าธรรมเนียมไว้ต้องไม่ติดหมายเหตุนั้น(self):
        """กันแก้เกิน — แถวที่มีค่าธรรมเนียมจริงไม่ได้ตั้งสมมติฐานอะไรเลย."""
        df = _frame([_row("d2", fx_rate_thb=float("nan"), fee_thb=5.1)])
        recorded_fx = df["fx_rate_thb"].copy()
        usable_fx = recorded_fx.copy()
        df["fx_rate_thb"] = tracker._derive_fx_from_amount(df)

        rows = tracker._collect_derived_fx_rows(df, usable_fx, recorded_fx)

        assert [r["tx_id"] for r in rows] == ["d2"]
        assert rows[0]["fee_assumed_zero"] is False
        assert f"{DIME_FEE_RATE * 100:.2f}%" not in str(rows[0]["reason"])


class TestNegativeFeeIsReported:
    """ข้อมูลผิดต้องดัง — ห้ามหายเงียบระหว่าง skipped/inconsistent."""

    def test_ค่าธรรมเนียมติดลบเข้ารายงานแถวที่ขัดกันเอง(self):
        rows = tracker._collect_inconsistent_rows(_frame([_row("n1", fee_thb=-5.1)]))

        assert [r["tx_id"] for r in rows] == ["n1"], (
            "แถวที่กรอกค่าธรรมเนียมติดลบไม่เข้ารายงานไหนเลย = ตัดข้อมูลทิ้งเงียบ"
        )
        assert rows[0]["recorded_fee_thb"] == pytest.approx(-5.1)
        # วงเล็บปิดหลัง "บาท" คือเหตุผลที่ ``_uncomparable_cause`` ผลิต — คนละที่กับ
        # หมายเหตุของ ``_negative_fee_note`` ที่ลงท้ายด้วย "จึงหักออกจากยอดเงินไม่ได้)"
        assert "(ค่าธรรมเนียมที่บันทึกไว้ติดลบ -5.10 บาท)" in str(rows[0]["reason"]), (
            f"เหตุผลต้องชี้ที่ช่อง fee_thb พร้อมค่าจริง ไม่ใช่เหมารวมว่า "
            f"'คำนวณเป็นตัวเลขจริงไม่ได้': {rows[0]['reason']}"
        )
        assert "nan" not in str(rows[0]["reason"]).lower()

    def test_เหตุผลของแถวที่เทียบไม่ได้ต้องชี้ฝั่งที่พังจริง(self):
        """``_uncomparable_cause`` ต้องแยกสามสาเหตุออกจากกัน — ชี้ผิดฝั่ง = กุคำอธิบาย."""
        assert (
            tracker._uncomparable_cause(3405.1, -5.1)
            == "ค่าธรรมเนียมที่บันทึกไว้ติดลบ -5.10 บาท"
        ), "ค่าธรรมเนียมติดลบถูกเหมารวมเป็นสาเหตุอื่น ผู้ใช้จะไล่หาเลขผิดเอาเอง"
        assert tracker._uncomparable_cause(0.0, -5.1) == "ยอดเงินต้องเป็นจำนวนบวก", (
            "ยอดเงินที่ใช้ไม่ได้ต้องมาก่อน — เป็นสาเหตุที่ตรงกว่า"
        )
        assert tracker._uncomparable_cause(3405.1, 5.1) == (
            "จำนวนหุ้น × ราคา × อัตราแลกเปลี่ยน + ค่าธรรมเนียม คำนวณเป็นตัวเลขจริงไม่ได้"
        )

    def test_ค่าธรรมเนียมบวกปกติต้องไม่ถูกธง(self):
        """กันแก้เกิน — แถวที่สอดคล้องกันดีต้องไม่มีคำเตือน."""
        assert tracker._collect_inconsistent_rows(_frame([_row("ok")])) == []

    def test_ค่าธรรมเนียมติดลบที่ทำให้แถวถูกตัดต้องบอกว่าเพราะค่าธรรมเนียม(self):
        """ไม่มีอัตราในสมุด + ค่าธรรมเนียมติดลบ ⇒ คำนวณย้อนไม่ได้ ⇒ ถูกตัด.

        ถ้าไม่ชี้ที่ค่าธรรมเนียม ผู้ใช้จะไปนั่งแก้อัตราแลกเปลี่ยนที่ไม่ได้ผิด
        """
        df = _frame([_row("s1", fx_rate_thb=float("nan"), fee_thb=-5.1)])
        recorded_fx = df["fx_rate_thb"].copy()
        unusable_fx = recorded_fx.where(recorded_fx.notna() & recorded_fx.isna())
        df["fx_rate_thb"] = tracker._derive_fx_from_amount(df)

        rows = tracker._collect_skipped_rows(df, unusable_fx)

        assert [r["tx_id"] for r in rows] == ["s1"]
        assert "ค่าธรรมเนียม" in str(rows[0]["reason"]), (
            f"เหตุผลชี้ผิดฝั่ง — ผู้ใช้จะไปแก้อัตราแลกเปลี่ยนที่ถูกอยู่แล้ว: {rows[0]['reason']}"
        )


class TestLedgerEndToEnd:
    """เส้นทางจริงจาก CSV → ``reports_of()`` (ไม่ใช่แค่เรียกด่านตรง ๆ)."""

    def test_สมุดที่มีค่าธรรมเนียมติดลบต้องมีคำเตือนออกมาจากสมุดจริง(self, ledger):
        ledger.write_text(
            HEADER
            + "n1,2026-03-05,VOO,1,100,34.0,3405.1,-5.1,,buy\n",
            encoding="utf-8",
        )
        reports = tracker.reports_of(tracker._load_transactions())

        assert [r["tx_id"] for r in reports["inconsistent_rows"]] == ["n1"]
        assert "ค่าธรรมเนียม" in str(reports["inconsistent_reason"])
        assert reports["skipped_rows"] == []

    def test_สมุดที่ไม่ได้บันทึกอัตราและค่าธรรมเนียมต้องบอกสมมติฐาน(self, ledger):
        ledger.write_text(
            HEADER + "d1,2026-03-05,VOO,1,100,,3405.1,,,buy\n",
            encoding="utf-8",
        )
        reports = tracker.reports_of(tracker._load_transactions())

        assert [r["tx_id"] for r in reports["derived_fx_rows"]] == ["d1"]
        assert f"{DIME_FEE_RATE * 100:.2f}%" in str(reports["derived_fx_reason"])


# --------------------------------------------------------------------------- #
# ฝั่งส่งถึงผู้ใช้ — report_service
# --------------------------------------------------------------------------- #
_SKIPPED_TXT = "ข้ามธุรกรรม 1 แถวเพราะข้อมูลไม่ครบ ตัวเลขสรุปไม่รวมแถวเหล่านี้: QQQM 2026-02-02"
_DERIVED_TXT = "อัตราแลกเปลี่ยน 1 แถวถูกคำนวณย้อนจากยอดเงินบาท: SCHD 2025-01-05"
_INCONSISTENT_TXT = "ยอดเงินบาทของ 1 แถวไม่ตรงกับ จำนวนหุ้น × ราคา × อัตราแลกเปลี่ยน: VOO 2024-11-11"


def _service_summary(**overrides: Any) -> dict[str, Any]:
    """payload ของ ``portfolio_service.get_summary_and_holdings()['summary']``."""
    summary = {
        "holdings_count": 3,
        "current_value_usd": 3020.0,
        "invested_usd": 2590.0,
        "pnl_usd": 430.0,
        "missing_prices": [],
        "skipped_rows": [],
        "skipped_reason": "",
        "derived_fx_rows": [],
        "derived_fx_reason": "",
        "inconsistent_rows": [],
        "inconsistent_reason": "",
    }
    summary.update(overrides)
    return summary


def _stub_portfolio(monkeypatch: pytest.MonkeyPatch, summary: dict[str, Any]) -> None:
    monkeypatch.setattr(
        rs.portfolio_service,
        "get_summary_and_holdings",
        lambda: {
            "summary": summary,
            "holdings": [
                {
                    "ticker": "VOO",
                    "current_value_usd": 3020.0,
                    "return_pct": 16.6,
                    "price_ok": True,
                }
            ],
        },
    )


def _all_data(pf: dict[str, Any]) -> dict[str, Any]:
    return {
        "portfolio": pf,
        "networth": {"available": False},
        "screener": {
            "available": True,
            "total_signals": 0,
            "symbols_with_signals": [],
            "by_preset": {},
            "undated_records": 0,
        },
        "goals": {"total": 0, "on_track": [], "off_track": []},
    }


def _dirty_payload(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    _stub_portfolio(
        monkeypatch,
        _service_summary(
            skipped_rows=[{"tx_id": "q1", "ticker": "QQQM", "date": "2026-02-02"}],
            skipped_reason=_SKIPPED_TXT,
            derived_fx_rows=[{"tx_id": "d1", "ticker": "SCHD", "date": "2025-01-05"}],
            derived_fx_reason=_DERIVED_TXT,
            inconsistent_rows=[{"tx_id": "i1", "ticker": "VOO", "date": "2024-11-11"}],
            inconsistent_reason=_INCONSISTENT_TXT,
        ),
    )
    return rs.get_portfolio_summary(None)


class TestReportServicePassesAllThree:
    def test_ทั้งสามชุดต้องอยู่ใน_payload_ของรายงาน(self, monkeypatch: pytest.MonkeyPatch):
        payload = _dirty_payload(monkeypatch)

        for key in (
            "skipped_rows",
            "skipped_reason",
            "derived_fx_rows",
            "derived_fx_reason",
            "inconsistent_rows",
            "inconsistent_reason",
        ):
            assert key in payload, f"คีย์ {key} หายจาก payload ของรายงานรายเดือน: {sorted(payload)}"
        assert payload["derived_fx_reason"] == _DERIVED_TXT
        assert payload["inconsistent_reason"] == _INCONSISTENT_TXT
        assert [r["tx_id"] for r in payload["inconsistent_rows"]] == ["i1"]

    def test_รายงานที่ส่ง_telegram_ต้องพิมพ์ครบทั้งสามชุด(self, monkeypatch: pytest.MonkeyPatch):
        """cron วันที่ 1 ไม่ใช้ AI — เส้นทางนี้คือช่องทางเดียวของงานอัตโนมัติ."""
        narrative = rs._plain_narrative(_all_data(_dirty_payload(monkeypatch)), "2026-08")

        for expected in (_SKIPPED_TXT, _DERIVED_TXT, _INCONSISTENT_TXT):
            assert expected in narrative, (
                f"คำเตือน {expected!r} ไม่ถึงผู้ใช้เลย — รายงานเสนอยอดว่าสะอาด:\n{narrative}"
            )

    def test_พรอมป์ที่ส่งให้_AI_ต้องรู้ทั้งสามชุด(self, monkeypatch: pytest.MonkeyPatch):
        """AI อธิบายอย่างเดียว แต่ต้องได้รู้ว่าตัวเลขที่ให้มาน่าสงสัยตรงไหน."""
        prompt = rs._build_prompt(_all_data(_dirty_payload(monkeypatch)), "2026-08")

        for expected in (_SKIPPED_TXT, _DERIVED_TXT, _INCONSISTENT_TXT):
            assert expected in prompt, (
                f"พรอมป์ไม่ได้บอก AI เรื่อง {expected!r} — บทสรุปที่ผู้ใช้จ่ายเงินซื้อ "
                f"จะบอกว่าตัวเลขสะอาด:\n{prompt}"
            )

    def test_สมุดสะอาดต้องไม่มีคำเตือนมาปน(self, monkeypatch: pytest.MonkeyPatch):
        """กันแก้เกิน — ไม่มีแถวน่าสงสัยแล้วห้ามมีบรรทัดเตือนขึ้นมาเอง."""
        _stub_portfolio(monkeypatch, _service_summary())
        payload = rs.get_portfolio_summary(None)
        narrative = rs._plain_narrative(_all_data(payload), "2026-08")

        assert rs._ledger_warning_lines(payload) == []
        assert "แถว" not in narrative, f"มีคำเตือนรายแถวโผล่มาทั้งที่สมุดสะอาด:\n{narrative}"

    def test_คำเตือนสามชุดต้องไม่ยุบรวมเป็นบรรทัดเดียว(self, monkeypatch: pytest.MonkeyPatch):
        lines = rs._ledger_warning_lines(_dirty_payload(monkeypatch))

        assert len(lines) == 3, f"สามชุดถูกยุบรวมกัน: {lines}"
        assert len(set(lines)) == 3
