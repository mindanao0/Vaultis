# -*- coding: utf-8 -*-
"""ทดสอบ C1 (AUDIT_2026-08-06) — ``portfolio/tracker.py`` + ``portfolio_service``.

* **H9** เงินลงทุนรวมนับ *ทุกแถว* แต่มูลค่า/กำไร/% นับ *เฉพาะแถวที่มีราคา*
  → ตัวเลข 3 ตัวบนจอเดียวกันบวกลบกันไม่ลงตัว และ ``total_return_pct``
  **สูงขึ้น** เมื่อข้อมูลหาย (ราคาที่ดึงไม่ได้ทำให้ผลตอบแทนดูดีขึ้น)
  ราคาหายทั้งพอร์ต → เดิมได้ ``pnl=0.0 / return=0.0`` = กุตัวเลขบนเส้นทางเงิน
* **C1.2** อัตราแลกเปลี่ยนที่บันทึกไว้ "ผิดแต่อยู่ในช่วง 20–50" ไม่เคยถูกเทียบกับ
  ``amount_thb`` ที่จ่ายจริง ทั้งที่ทุกแถวมีข้อมูลพอจะเช็คได้
* **C1.3** แถวที่อัตราถูก "ซ่อม" (derive ย้อนจากยอดบาท) มีแค่ ``logger.warning``
  ผู้ใช้ไม่มีทางเห็น
* **C1.4** ``get_dividend_summary()`` รายงาน ``skipped_rows`` ของ *ทั้งสมุด*
  ทำให้ผู้ใช้เข้าใจว่าปันผลหายไป ทั้งที่แถวที่ถูกตัดเป็นไม้ซื้อ
"""

import math

import pytest

from backend.services import portfolio_service
from portfolio import tracker

HEADER = "tx_id,date,ticker,shares,price_usd,fx_rate_thb,amount_thb,fee_thb,note,tx_type\n"

# ฉากตามหลักฐานในผลตรวจ: VOO 140,000 + SCHD 34,000 + QQQM 108,000 = 282,000 บาท
LEDGER_282K = (
    HEADER
    + "t1,2026-01-05,VOO,10,400,35.0,140000,0,,buy\n"
    + "t2,2026-01-06,SCHD,10,100,34.0,34000,0,,buy\n"
    + "t3,2026-01-07,QQQM,10,300,36.0,108000,0,,buy\n"
)
FX_TODAY = 34.0
PRICED = {"VOO": 450.0, "SCHD": 120.0}  # QQQM ดึงราคาไม่ได้


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    """สมุดสังเคราะห์ + ตัดเส้นทาง network (ห้ามแตะสมุดจริงของผู้ใช้)."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    csv_path = data_dir / "transactions.csv"
    monkeypatch.setattr(tracker, "DATA_DIR", data_dir)
    monkeypatch.setattr(tracker, "TRANSACTIONS_FILE", csv_path)
    monkeypatch.setattr(tracker, "_get_latest_prices", lambda tickers: {})
    monkeypatch.setattr(tracker, "_get_usdthb_rate", lambda: FX_TODAY)
    return csv_path


@pytest.fixture()
def prices(monkeypatch):
    """ตั้งราคาที่ดึงได้ทีละเคส (default ของ fixture ``ledger`` คือดึงไม่ได้เลย)."""

    def _set(mapping: dict[str, float]) -> None:
        monkeypatch.setattr(
            tracker, "_get_latest_prices", lambda tickers: {t: mapping[t] for t in tickers if t in mapping}
        )

    return _set


class TestInvestedBasisIsLabelled:
    """H9 — ต้องแยก "ยอดที่จ่ายไปจริงทั้งหมด" ออกจาก "ฐานที่ใช้คิดกำไร" ให้ชัด."""

    def test_two_named_bases_exist(self, ledger, prices):
        ledger.write_text(LEDGER_282K, encoding="utf-8")
        prices(PRICED)

        totals = tracker.get_total_summary()

        assert totals["invested_thb_all"] == pytest.approx(282_000.0), (
            "ยอดที่จ่ายไปจริงทั้งหมดต้องนับทุกแถวที่ใช้ได้"
        )
        assert totals["invested_thb_priced"] == pytest.approx(174_000.0), (
            "ฐานที่ใช้คิดกำไรต้องนับเฉพาะกองที่มีราคาปัจจุบัน"
        )
        assert totals["missing_prices"] == ["QQQM"]

    def test_pnl_reconciles_against_its_own_base(self, ledger, prices):
        """invariant: ``current_value − invested_thb_priced == total_pnl`` เป๊ะ."""
        ledger.write_text(LEDGER_282K, encoding="utf-8")
        prices(PRICED)

        totals = tracker.get_total_summary()

        # VOO 10×450×34 = 153,000 · SCHD 10×120×34 = 40,800
        assert totals["current_value_thb"] == pytest.approx(193_800.0)
        assert totals["total_pnl_thb"] == pytest.approx(19_800.0)
        assert float(totals["current_value_thb"]) - float(totals["invested_thb_priced"]) == pytest.approx(
            float(totals["total_pnl_thb"])
        )
        assert totals["total_return_pct"] == pytest.approx(19_800.0 / 174_000.0 * 100.0)

    def test_missing_price_must_not_inflate_return_pct(self, ledger, prices):
        """ผลตอบแทนที่คิดจากฐานย่อยต้องติดป้ายว่าเป็นฐานย่อย ไม่ใช่ของทั้งพอร์ต."""
        ledger.write_text(LEDGER_282K, encoding="utf-8")
        prices(PRICED)
        partial = tracker.get_total_summary()

        prices({**PRICED, "QQQM": 330.0})
        full = tracker.get_total_summary()

        assert full["invested_thb_priced"] == pytest.approx(full["invested_thb_all"])
        # ฉากราคาครบ ฐานเดียวกันทั้งสองข้าง — ตัวเลขต้องประกอบกลับได้
        assert float(full["current_value_thb"]) - float(full["invested_thb_all"]) == pytest.approx(
            float(full["total_pnl_thb"])
        )
        # ฉากราคาหาย ฐานของ % ต้องเป็น invested_thb_priced ไม่ใช่ invested_thb_all
        assert float(partial["total_return_pct"]) == pytest.approx(
            float(partial["total_pnl_thb"]) / float(partial["invested_thb_priced"]) * 100.0
        )

    def test_no_price_at_all_is_unknown_not_zero(self, ledger):
        """ราคาหายทั้งพอร์ต → มูลค่า/กำไร/% ต้องเป็น "ไม่รู้" ห้ามเป็น 0.0."""
        ledger.write_text(LEDGER_282K, encoding="utf-8")

        totals = tracker.get_total_summary()

        assert totals["invested_thb_all"] == pytest.approx(282_000.0), "เงินที่จ่ายไปจริงยังรู้อยู่"
        assert totals["invested_thb_priced"] == pytest.approx(0.0)
        assert sorted(totals["missing_prices"]) == ["QQQM", "SCHD", "VOO"]
        assert math.isnan(float(totals["current_value_thb"])), "มูลค่าที่ไม่รู้ ห้ามเป็น 0.00"
        assert math.isnan(float(totals["total_pnl_thb"])), "กำไรที่ไม่รู้ ห้ามเป็น 0.00"
        assert math.isnan(float(totals["total_return_pct"])), "ผลตอบแทนที่ไม่รู้ ห้ามเป็น 0.00%"

    def test_empty_ledger_still_reports_zero(self, ledger):
        """สมุดว่างคนละเรื่องกับราคาหาย — 0 ที่นี่คือคำตอบจริง."""
        ledger.write_text(HEADER, encoding="utf-8")

        totals = tracker.get_total_summary()

        assert totals["invested_thb_all"] == pytest.approx(0.0)
        assert totals["invested_thb_priced"] == pytest.approx(0.0)
        assert totals["total_pnl_thb"] == pytest.approx(0.0)


class TestPortfolioServiceMirrorsBases:
    """H9 ฝั่ง API — ``/api/portfolio`` ทำพลาดแบบเดียวกันในหน่วย USD."""

    def test_usd_side_has_two_named_bases(self, ledger, prices):
        ledger.write_text(LEDGER_282K, encoding="utf-8")
        prices(PRICED)

        summary = portfolio_service.get_portfolio_summary()

        # VOO 4,000 + SCHD 1,000 + QQQM 3,000 = 8,000 USD
        assert summary["invested_usd_all"] == pytest.approx(8_000.0)
        assert summary["invested_usd_priced"] == pytest.approx(5_000.0)
        assert summary["invested_thb_all"] == pytest.approx(282_000.0)
        assert summary["invested_thb_priced"] == pytest.approx(174_000.0)
        # 10×450 + 10×120 = 5,700 USD
        assert summary["current_value_usd"] == pytest.approx(5_700.0)
        assert float(summary["current_value_usd"]) - float(summary["invested_usd_priced"]) == pytest.approx(
            float(summary["pnl_usd"])
        )

    def test_no_price_at_all_is_none_not_zero(self, ledger):
        ledger.write_text(LEDGER_282K, encoding="utf-8")

        summary = portfolio_service.get_portfolio_summary()

        assert summary["invested_usd_all"] == pytest.approx(8_000.0)
        assert summary["current_value_usd"] is None, "มูลค่าที่ไม่รู้ต้องเป็น None ห้ามเป็น 0"
        assert summary["pnl_usd"] is None, "กำไรที่ไม่รู้ต้องเป็น None ห้ามเป็น 0"
        assert summary["pnl_thb"] is None
        assert summary["return_pct"] is None
        assert sorted(summary["missing_prices"]) == ["QQQM", "SCHD", "VOO"]

    def test_empty_ledger_is_zero_on_both_currency_sides(self, ledger):
        """สมุดว่าง = 0 **ทั้งสองสกุล** — "ไม่รู้" (None) เป็นคำตอบผิดที่นี่.

        ``tracker.get_total_summary()`` ตอบ 0.0 ให้ฝั่งบาทพร้อมเหตุผลกำกับไว้ว่า
        "สมุดว่าง = 0 คือคำตอบจริง (คนละเรื่องกับดึงราคาไม่ได้)" แต่ฝั่ง USD ใช้
        ``_sum_or_none([])`` ซึ่งคืน ``None`` ⇒ payload เดียวกันมี
        ``current_value_thb = 0.0`` คู่กับ ``current_value_usd = None``

        ผลจริงที่วัดได้: ``report_service._plain_narrative()`` ซึ่ง format
        ``pf['current_value_usd']:,.2f`` ระเบิดเป็น ``TypeError`` ⇒ รายงานรายเดือน
        สร้างไม่ได้เลยสำหรับพอร์ตเปล่า (= สถานะจริงของสมุดผู้ใช้ตอนนี้)
        ทั้งที่ก่อนแก้ H9 มันทำงานได้และตอบถูกด้วย
        """
        ledger.write_text(HEADER, encoding="utf-8")

        summary = portfolio_service.get_portfolio_summary()

        assert summary["holdings_count"] == 0
        assert summary["current_value_thb"] == pytest.approx(0.0), "ฝั่งบาทตอบ 0 อยู่แล้ว"
        assert summary["current_value_usd"] == pytest.approx(0.0), (
            "สมุดว่างมีมูลค่า 0 จริง ๆ ห้ามตอบ None ให้ขัดกับฝั่งบาทใน payload เดียวกัน"
        )
        assert summary["pnl_usd"] == pytest.approx(0.0)
        assert summary["invested_usd_all"] == pytest.approx(0.0)
        assert summary["missing_prices"] == []


class TestRecordedFxIsCheckedAgainstAmountPaid:
    """C1.2 — อัตราที่ "ผิดแต่อยู่ในช่วง" ต้องถูกจับด้วยยอดบาทที่จ่ายจริง."""

    # VOO 10 หุ้น @400 USD จ่ายจริง 140,210 บาท (อัตราวันนั้น 35.00 + fee 210)
    WRONG_FX = HEADER + "w1,2024-01-15,VOO,10,400,33.23,140210,210,ใช้อัตราวันนี้,buy\n"
    RIGHT_FX = HEADER + "r1,2024-01-15,VOO,10,400,35.0,140210,210,อัตราวันซื้อ,buy\n"

    def test_inconsistent_rate_is_reported(self, ledger, prices):
        ledger.write_text(self.WRONG_FX, encoding="utf-8")
        prices({"VOO": 500.0})

        totals = tracker.get_total_summary()

        rows = totals["inconsistent_rows"]
        assert [row["tx_id"] for row in rows] == ["w1"], (
            "ยอดบาทที่จ่ายจริงขัดกับ shares × ราคา × อัตรา ต้องถูกรายงาน ไม่ใช่ผ่านเงียบ"
        )
        assert rows[0]["ticker"] == "VOO"
        assert totals["inconsistent_reason"], "ต้องมีข้อความไทยพร้อมแสดงบนหน้าจอ"
        assert "VOO" in totals["inconsistent_reason"]

    def test_inconsistent_row_is_warned_not_dropped(self, ledger, prices):
        """เตือน ไม่ตัดทิ้ง — ระบบบันทึกตามที่ผู้ใช้บอก ไม่ใช่ข้อมูลไม่ครบ."""
        ledger.write_text(self.WRONG_FX, encoding="utf-8")
        prices({"VOO": 500.0})

        totals = tracker.get_total_summary()
        holdings = tracker.get_portfolio_summary()

        assert totals["skipped_rows"] == [], "แถวนี้ข้อมูลครบ ห้ามถูกตัดออกจากยอดรวม"
        assert list(holdings["Ticker"]) == ["VOO"]
        assert totals["invested_thb_all"] == pytest.approx(10 * 400 * 33.23)

    def test_consistent_rate_is_not_flagged(self, ledger, prices):
        ledger.write_text(self.RIGHT_FX, encoding="utf-8")
        prices({"VOO": 500.0})

        totals = tracker.get_total_summary()

        assert totals["inconsistent_rows"] == []
        assert totals["inconsistent_reason"] == ""

    def test_unrecorded_fee_stays_within_tolerance(self, ledger, prices):
        """ยอดที่รวม fee ไว้แต่ไม่ได้บันทึก fee ต่างกัน 0.15% — ต่ำกว่าเกณฑ์ 1%."""
        ledger.write_text(
            HEADER + "n1,2026-01-05,VOO,2,500,34.5,34551.75,,รวม fee แต่ไม่บันทึก,buy\n",
            encoding="utf-8",
        )
        prices({"VOO": 500.0})

        assert tracker.get_total_summary()["inconsistent_rows"] == []

    def test_dividend_row_is_never_flagged(self, ledger):
        """ปันผล shares=0/price=0 — ไม่มีอะไรให้เทียบ ห้ามรายงานเป็นข้อมูลขัดกัน."""
        ledger.write_text(
            HEADER + "d1,2026-03-20,SCHD,0,0,34.0,340,0,ปันผล,dividend\n",
            encoding="utf-8",
        )

        assert tracker.get_total_summary()["inconsistent_rows"] == []


class TestDerivedFxRowsAreReported:
    """C1.3 — แถวที่ถูก "ซ่อม" อัตราต้องโผล่ให้ผู้ใช้เห็น ไม่ใช่แค่ใน log."""

    def test_unusable_recorded_rate_is_listed(self, ledger, prices):
        ledger.write_text(
            HEADER + "o1,2026-01-07,VOO,2,500,1.0,34500,0,fx ผิดหน่วย,buy\n",
            encoding="utf-8",
        )
        prices({"VOO": 500.0})

        totals = tracker.get_total_summary()

        rows = totals["derived_fx_rows"]
        assert [row["tx_id"] for row in rows] == ["o1"]
        assert rows[0]["recorded_fx"] == pytest.approx(1.0)
        assert rows[0]["used_fx"] == pytest.approx(34.5)
        assert totals["derived_fx_reason"], "ต้องมีข้อความไทยพร้อมแสดงบนหน้าจอ"

    def test_blank_rate_is_listed_with_no_recorded_value(self, ledger, prices):
        ledger.write_text(
            HEADER + "b1,2026-01-05,VOO,2,500,,34500,0,ลืมกรอก FX,buy\n",
            encoding="utf-8",
        )
        prices({"VOO": 500.0})

        rows = tracker.get_total_summary()["derived_fx_rows"]

        assert [row["tx_id"] for row in rows] == ["b1"]
        assert rows[0]["recorded_fx"] is None
        assert rows[0]["used_fx"] == pytest.approx(34.5)

    def test_clean_ledger_lists_nothing(self, ledger, prices):
        ledger.write_text(
            HEADER + "c1,2026-01-05,VOO,2,500,34.5,34500,0,,buy\n",
            encoding="utf-8",
        )
        prices({"VOO": 500.0})

        totals = tracker.get_total_summary()

        assert totals["derived_fx_rows"] == []
        assert totals["derived_fx_reason"] == ""

    def test_skipped_row_is_not_reported_as_repaired(self, ledger):
        """แถวที่กู้ไม่ได้อยู่ใน ``skipped_rows`` เท่านั้น ห้ามนับซ้ำเป็นแถวที่ซ่อมแล้ว."""
        ledger.write_text(
            HEADER + "u1,2026-01-20,QQQM,3,200,0,,0,fx=0 และไม่มียอดเงิน,buy\n",
            encoding="utf-8",
        )

        totals = tracker.get_total_summary()

        assert [row["tx_id"] for row in totals["skipped_rows"]] == ["u1"]
        assert totals["derived_fx_rows"] == []


class TestDividendSummaryFiltersSkippedRows:
    """C1.4 — สรุปปันผลต้องรายงานเฉพาะแถวปันผลที่ถูกตัด ไม่ใช่ของทั้งสมุด."""

    LEDGER = (
        HEADER
        + "b1,2026-02-05,VOO,1,200,,,0,ไม้ซื้อที่ fx/ยอดเงินว่าง,buy\n"
        + "d1,2026-03-20,SCHD,0,0,34.5,345,0,ปันผลปกติ,dividend\n"
    )

    def test_buy_row_does_not_appear_under_dividends(self, ledger):
        ledger.write_text(self.LEDGER, encoding="utf-8")

        summary = tracker.get_dividend_summary()

        assert summary["count"] == 1
        assert summary["total_thb"] == pytest.approx(345.0)
        assert summary["skipped_rows"] == [], (
            "ไม้ซื้อที่ถูกตัดต้องไม่โผล่ใต้หัวข้อปันผล ผู้ใช้จะเข้าใจว่าปันผลหาย"
        )
        assert summary["skipped_reason"] == ""

    def test_total_summary_still_reports_the_buy_row(self, ledger):
        """แถวเดียวกันต้องยังถูกรายงานในที่ของมัน — กรอง ไม่ใช่กลืน."""
        ledger.write_text(self.LEDGER, encoding="utf-8")

        totals = tracker.get_total_summary()

        assert [row["tx_id"] for row in totals["skipped_rows"]] == ["b1"]

    def test_skipped_dividend_still_appears(self, ledger):
        ledger.write_text(
            HEADER
            + "b1,2026-02-05,VOO,1,200,,,0,ไม้ซื้อที่ fx/ยอดเงินว่าง,buy\n"
            + "d2,2026-03-01,SCHD,0,0,,1700,0,ปันผลที่ลืมกรอก FX,dividend\n",
            encoding="utf-8",
        )

        summary = tracker.get_dividend_summary()

        assert [row["tx_id"] for row in summary["skipped_rows"]] == ["d2"]
        assert "SCHD" in summary["skipped_reason"]


class TestInconsistencyCheckEdgeCases:
    """C1.2 — ด่านตรวจเองต้องไม่ระเบิด/ไม่กุตัวเลขในแถวที่ตัวเลขผิดรูป."""

    def test_zero_trade_value_does_not_divide_by_zero(self, ledger, prices):
        """หุ้นแถม (ราคา 0) แต่มียอดเงิน — คำนวณอัตราย้อนไม่ได้ ต้องเป็น None."""
        ledger.write_text(
            HEADER + "z1,2026-03-05,FREE,5,0,34.5,5000,0,ราคา 0 แต่มียอดเงิน,buy\n",
            encoding="utf-8",
        )
        prices({"FREE": 10.0})

        rows = tracker.get_total_summary()["inconsistent_rows"]

        assert [row["tx_id"] for row in rows] == ["z1"]
        assert rows[0]["implied_fx"] is None, "หารด้วยศูนย์ต้องไม่กลายเป็นตัวเลข"

    def test_reason_does_not_claim_zero_when_trade_value_is_negative(self, ledger, prices):
        """ข้อความเตือนห้ามพูดตัวเลขที่ไม่จริง — ``shares`` ติดลบ ⇒ หุ้น × ราคา ≠ 0.

        ด่าน C1.2 ใช้ ``trade_value_usd > 0`` เป็นเงื่อนไขเดียว แล้วเขียนเหตุผลว่า
        "จำนวนหุ้น × ราคา = 0" ให้ทุกเคสที่ตกเงื่อนไข รวมถึงเคสติดลบซึ่งค่าจริงคือ
        -4,000 — คำอธิบายที่ระบบแต่งขึ้นเองบนเส้นทางเงิน ผู้ใช้จะไปตามหา
        "ราคา 0" ที่ไม่มีอยู่จริงแทนที่จะเห็นว่าจำนวนหุ้นติดลบ
        """
        ledger.write_text(
            HEADER + "m1,2026-03-05,VOO,-10,400,35.0,140210,210,shares ติดลบ,buy\n",
            encoding="utf-8",
        )
        prices({"VOO": 500.0})

        rows = tracker.get_total_summary()["inconsistent_rows"]

        assert [row["tx_id"] for row in rows] == ["m1"]
        assert rows[0]["implied_fx"] is None, "อัตราที่คำนวณย้อนไม่ได้ต้องเป็น None"
        assert "= 0" not in str(rows[0]["reason"]), (
            "ห้ามบอกว่า จำนวนหุ้น × ราคา = 0 ทั้งที่ค่าจริงคือ -4,000"
        )
        assert "-4,000" in str(rows[0]["reason"]) or "-4000" in str(rows[0]["reason"])

    def test_zero_amount_row_is_not_flagged(self, ledger, prices):
        """ยอดเงิน 0 ไม่มีอะไรให้เทียบสัดส่วน — ห้ามรายงานว่าต่างกัน 100%."""
        ledger.write_text(
            HEADER + "z2,2026-03-05,FREE,5,0,34.5,0,0,หุ้นแถม,buy\n",
            encoding="utf-8",
        )
        prices({"FREE": 10.0})

        assert tracker.get_total_summary()["inconsistent_rows"] == []
