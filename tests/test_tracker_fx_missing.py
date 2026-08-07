# -*- coding: utf-8 -*-
"""ทดสอบ FIX_PLAN ข้อ 1.2 — tracker ห้ามเติมอัตราแลกเปลี่ยนเองเงียบ ๆ.

เดิม ``_load_transactions()`` ทำ ``fillna(DEFAULT_USDTHB)`` บนคอลัมน์ ``fx_rate_thb``
ซึ่งเป็นการกุตัวเลขบนเส้นทางเงินโดยตรง และทำให้ ``dropna(subset=[...])``
บรรทัดถัดไปไม่มีวันทำงานสำหรับคอลัมน์นี้

นโยบายที่ถูกต้อง (กฎ fail-loud ข้อ 1):
1. หาค่าจริงก่อนเดา — แถวที่มี ``amount_thb`` ครบ อัตราที่จ่ายจริงคือ
   ``(amount_thb - fee_thb) / (shares * price_usd)``
2. ที่เหลือปล่อยเป็น NaN ให้ถูกตัดออก **แต่ต้องรายงานออกไป** ทาง
   ``skipped_rows`` / ``skipped_reason`` แบบเดียวกับ ``missing_prices``

รอบเก็บกวาด C1 ปิดรูที่การแก้ข้อ 1.2 เปิดค้างไว้เอง:
- (ก) ``fx_rate_thb`` ที่ **มีค่าแต่ใช้ไม่ได้** (0 / ติดลบ / นอกช่วง 20–50 ของ
  ``utils/fx.py``) ยังไหลเข้าไปคิดเงิน — เดิมอุดเฉพาะกรณีค่าว่าง (NaN)
- (ข) ``get_dividend_summary()`` ตัดแถวเงียบ ไม่มีคีย์รายงานเลย
- (ค) สูตร derive ไม่หักค่าธรรมเนียมออกจากยอดที่จ่ายจริง → อัตราเพี้ยน ~0.15%
"""

import pandas as pd
import pytest

from portfolio import tracker

HEADER = "tx_id,date,ticker,shares,price_usd,fx_rate_thb,amount_thb,fee_thb,note,tx_type\n"


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    """สมุดบัญชีสังเคราะห์ + ตัดเส้นทาง network ออก (ห้ามแตะสมุดจริงของผู้ใช้)."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    csv_path = data_dir / "transactions.csv"
    monkeypatch.setattr(tracker, "DATA_DIR", data_dir)
    monkeypatch.setattr(tracker, "TRANSACTIONS_FILE", csv_path)
    monkeypatch.setattr(tracker, "_get_latest_prices", lambda tickers: {t: 600.0 for t in tickers})
    monkeypatch.setattr(tracker, "_get_usdthb_rate", lambda: 34.0)
    return csv_path


class TestDeriveFxFromAmountThb:
    """(ก) FX ว่าง + มี amount_thb → ต้องได้อัตราที่จ่ายจริง ไม่ใช่ค่า default."""

    def test_missing_fx_is_derived_from_amount_thb(self, ledger):
        # 2 หุ้น × 500 USD = 1,000 USD จ่ายจริง 34,500 บาท → อัตราที่จ่ายจริง = 34.50
        ledger.write_text(
            HEADER + "a1,2026-01-05,VOO,2,500,,34500,0,ลืมกรอก FX,buy\n",
            encoding="utf-8",
        )

        loaded = tracker._load_transactions()
        assert len(loaded) == 1, "แถวที่คำนวณ FX ย้อนได้ต้องไม่ถูกตัดทิ้ง"
        assert loaded["fx_rate_thb"].iloc[0] == pytest.approx(34.5), (
            "ต้องใช้อัตราที่จ่ายจริงจาก amount_thb ไม่ใช่ค่าเดาจาก config"
        )

        holdings = tracker.get_portfolio_summary()
        voo = holdings.loc[holdings["Ticker"] == "VOO"].iloc[0]
        assert voo["FX Rate (Buy)"] == pytest.approx(34.5)
        assert voo["Invested (THB)"] == pytest.approx(34500.0)

        totals = tracker.get_total_summary()
        assert totals["skipped_rows"] == [], "แถวนี้กู้ได้ ไม่ควรถูกรายงานว่าถูกข้าม"
        assert totals["total_invested_thb"] == pytest.approx(34500.0)

    def test_derived_rate_outside_plausible_band_is_not_used(self, ledger):
        """amount_thb ที่กรอกผิดหน่วยต้องไม่กลายเป็นอัตราแลกเปลี่ยน 1.0."""
        # 1,000 USD แต่กรอกยอดเป็น 1,000 (บาท?) → อัตราย้อนกลับ = 1.0 นอกช่วง 20–50
        ledger.write_text(
            HEADER + "b1,2026-02-02,SCHD,10,100,,1000,0,กรอกผิดหน่วย,buy\n",
            encoding="utf-8",
        )

        loaded = tracker._load_transactions()
        assert loaded.empty, "อัตราย้อนกลับที่ไม่สมเหตุสมผลต้องไม่ถูกใช้"

        totals = tracker.get_total_summary()
        assert len(totals["skipped_rows"]) == 1
        assert totals["skipped_rows"][0]["tx_id"] == "b1"
        assert totals["skipped_reason"], "ต้องมีข้อความอธิบายให้หน้าจอแสดง"


class TestSkippedRowsAreReported:
    """(ข) FX ว่าง + คำนวณย้อนไม่ได้ → ต้องถูกตัด **และ** ปรากฏในรายงาน."""

    def test_missing_fx_without_amount_is_skipped_and_reported(self, ledger):
        ledger.write_text(
            HEADER
            + "ok1,2026-01-05,VOO,2,500,34.5,34500,0,ครบถ้วน,buy\n"
            + "bad1,2026-01-20,QQQM,3,200,,,0,ไม่มีทั้ง FX และยอดเงิน,buy\n",
            encoding="utf-8",
        )

        loaded = tracker._load_transactions()
        assert list(loaded["tx_id"]) == ["ok1"], "แถวที่ข้อมูลไม่ครบต้องถูกตัดออก"

        totals = tracker.get_total_summary()
        assert len(totals["skipped_rows"]) == 1, "ตัดแถวเงียบ ๆ ไม่ได้ ต้องรายงานออกมา"
        skipped = totals["skipped_rows"][0]
        assert skipped["tx_id"] == "bad1"
        assert skipped["ticker"] == "QQQM"
        assert skipped["date"] == "2026-01-20"
        assert "fx_rate_thb" in skipped["missing_fields"]
        assert skipped["reason"], "ต้องบอกเหตุผลเป็นข้อความไทย"
        assert "1" in totals["skipped_reason"] and "QQQM" in totals["skipped_reason"]
        # ตัวเลขที่เหลือยังคำนวณจากแถวที่ครบเท่านั้น
        assert totals["total_invested_thb"] == pytest.approx(34500.0)

    def test_dividend_row_missing_fx_is_not_guessed(self, ledger):
        """แถวปันผลคำนวณ FX ย้อนไม่ได้ (shares=0) — ห้ามเดาแล้วรายงานยอด USD ปลอม."""
        ledger.write_text(
            HEADER + "d1,2026-03-01,SCHD,0,0,,1700,0,ปันผลที่ลืมกรอก FX,dividend\n",
            encoding="utf-8",
        )

        assert tracker.get_dividends().empty, "ยอดปันผล USD ต้องไม่ถูกกุจากอัตราเดา"
        totals = tracker.get_total_summary()
        assert [row["tx_id"] for row in totals["skipped_rows"]] == ["d1"]
        assert "SCHD" in totals["skipped_reason"]

    def test_all_rows_skipped_still_reports(self, ledger):
        """สมุดที่ทุกแถวใช้ไม่ได้ต้องไม่รายงานว่า 'พอร์ตว่าง' เฉย ๆ."""
        ledger.write_text(
            HEADER + "z1,2026-04-01,GLDM,5,60,,,0,ข้อมูลไม่ครบ,buy\n",
            encoding="utf-8",
        )

        totals = tracker.get_total_summary()
        assert totals["total_invested_thb"] == pytest.approx(0.0)
        assert len(totals["skipped_rows"]) == 1
        assert totals["skipped_reason"]


class TestCompleteLedgerUnchanged:
    """(ค) FX ครบทุกแถว → พฤติกรรมต้องไม่เปลี่ยนจากเดิมเลย."""

    def test_full_ledger_behaviour_is_identical(self, ledger):
        ledger.write_text(
            HEADER
            + "c1,2026-01-05,VOO,2,500,34.5,34500,51.75,,buy\n"
            + "c2,2026-02-05,VOO,1,520,35.0,18200,27.30,,buy\n"
            + "c3,2026-03-05,SCHD,10,80,34.0,27200,40.80,,buy\n"
            + "c4,2026-03-20,SCHD,0,0,34.0,340,0,ปันผล,dividend\n",
            encoding="utf-8",
        )

        loaded = tracker._load_transactions()
        assert len(loaded) == 4
        assert loaded["fx_rate_thb"].tolist() == [34.5, 35.0, 34.0, 34.0]
        assert loaded["fee_thb"].tolist() == [51.75, 27.30, 40.80, 0.0]

        holdings = tracker.get_portfolio_summary()
        voo = holdings.loc[holdings["Ticker"] == "VOO"].iloc[0]
        assert voo["Shares"] == pytest.approx(3.0)
        assert voo["Invested (USD)"] == pytest.approx(1520.0)
        assert voo["Invested (THB)"] == pytest.approx(34500.0 + 18200.0)
        # FX เฉลี่ยถ่วงน้ำหนักด้วยต้นทุน USD
        assert voo["FX Rate (Buy)"] == pytest.approx(
            (34.5 * 1000.0 + 35.0 * 520.0) / 1520.0
        )
        assert bool(voo["Price OK"]) is True

        totals = tracker.get_total_summary()
        assert totals["skipped_rows"] == []
        assert totals["skipped_reason"] == ""
        assert totals["missing_prices"] == []
        assert totals["total_invested_thb"] == pytest.approx(34500.0 + 18200.0 + 27200.0)
        assert totals["total_fee_thb"] == pytest.approx(51.75 + 27.30 + 40.80)

        dividends = tracker.get_dividends("SCHD")
        assert len(dividends) == 1
        assert dividends.iloc[0]["amount_usd"] == pytest.approx(10.0)

    def test_empty_ledger_reports_no_skips(self, ledger):
        ledger.write_text(HEADER, encoding="utf-8")
        totals = tracker.get_total_summary()
        assert totals["skipped_rows"] == []
        assert totals["skipped_reason"] == ""
        assert totals["total_invested_thb"] == pytest.approx(0.0)


class TestUnusableRecordedFx:
    """(ก) FX ที่บันทึกไว้แต่ใช้ไม่ได้ (0/ติดลบ/นอกช่วง) ต้องเดินเส้นทางเดียวกับ FX ว่าง.

    เดิมอุดเฉพาะค่าว่าง (NaN) ค่าที่ผิดรูปจึงยังไหลเข้าไปคิดเงิน:
    fx=0 → ลงทุน 0 บาท, fx=-34.5 → ลงทุน **ติดลบ**, fx=1.0 → ลงทุน 1,000 บาท
    ทั้งสามกรณี ``skipped_rows`` ว่างเปล่า (เงียบสนิท)
    """

    def test_zero_fx_is_recovered_from_amount_thb(self, ledger):
        """fx=0 แต่มียอดบาทครบ → ใช้อัตราที่จ่ายจริง ไม่ใช่คิดเงินด้วย 0."""
        ledger.write_text(
            HEADER + "z0,2026-01-05,VOO,2,500,0,34500,0,fx เป็นศูนย์,buy\n",
            encoding="utf-8",
        )

        loaded = tracker._load_transactions()
        assert len(loaded) == 1
        assert loaded["fx_rate_thb"].iloc[0] == pytest.approx(34.5)

        totals = tracker.get_total_summary()
        assert totals["total_invested_thb"] == pytest.approx(34500.0), (
            "fx=0 ต้องไม่ทำให้เงินที่ลงไปจริงหายเป็น 0"
        )
        assert totals["skipped_rows"] == [], "แถวนี้กู้ได้ ไม่ต้องรายงานว่าถูกข้าม"

    def test_negative_fx_is_recovered_from_amount_thb(self, ledger):
        """fx ติดลบต้องไม่กลายเป็นเงินลงทุนติดลบ."""
        ledger.write_text(
            HEADER + "n1,2026-01-06,VOO,2,500,-34.5,34500,0,fx ติดลบ,buy\n",
            encoding="utf-8",
        )

        totals = tracker.get_total_summary()
        assert totals["total_invested_thb"] == pytest.approx(34500.0)
        assert totals["skipped_rows"] == []

    def test_out_of_band_fx_is_replaced_by_derived_rate(self, ledger):
        """fx=1.0 (กรอกผิดหน่วย) แต่คำนวณย้อนได้ → ใช้ค่าที่คำนวณย้อน."""
        ledger.write_text(
            HEADER + "o1,2026-01-07,VOO,2,500,1.0,34500,0,fx ผิดหน่วย,buy\n",
            encoding="utf-8",
        )

        loaded = tracker._load_transactions()
        assert loaded["fx_rate_thb"].iloc[0] == pytest.approx(34.5)
        totals = tracker.get_total_summary()
        assert totals["total_invested_thb"] == pytest.approx(34500.0)

    def test_boundary_rates_stay_usable(self, ledger):
        """ขอบช่วง 20 และ 50 ยังเป็นค่าที่ใช้ได้ (นิยามเดียวกับ utils/fx.py)."""
        ledger.write_text(
            HEADER
            + "b20,2026-01-08,VOO,1,100,20.0,2000,0,ขอบล่าง,buy\n"
            + "b50,2026-01-09,VOO,1,100,50.0,5000,0,ขอบบน,buy\n",
            encoding="utf-8",
        )

        loaded = tracker._load_transactions()
        assert loaded["fx_rate_thb"].tolist() == [20.0, 50.0]
        assert tracker.get_total_summary()["skipped_rows"] == []

    def test_unusable_fx_without_amount_is_skipped_and_reports_old_value(self, ledger):
        """คำนวณย้อนไม่ได้ → ตัดออก **และ** บอกว่าค่าเดิมที่บันทึกไว้คืออะไร."""
        ledger.write_text(
            HEADER
            + "ok1,2026-01-05,VOO,2,500,34.5,34500,0,ครบถ้วน,buy\n"
            + "u1,2026-01-20,QQQM,3,200,0,,0,fx=0 และไม่มียอดเงิน,buy\n",
            encoding="utf-8",
        )

        loaded = tracker._load_transactions()
        assert list(loaded["tx_id"]) == ["ok1"]

        totals = tracker.get_total_summary()
        assert len(totals["skipped_rows"]) == 1, "ตัดแถวเงียบ ๆ ไม่ได้"
        skipped = totals["skipped_rows"][0]
        assert skipped["tx_id"] == "u1"
        assert "fx_rate_thb" in skipped["missing_fields"]
        assert "0" in skipped["reason"], "ต้องบอกค่าเดิมที่บันทึกไว้ในเหตุผล"
        assert "20" in skipped["reason"] and "50" in skipped["reason"], (
            "ต้องอ้างช่วงที่ใช้ได้จาก utils/fx.py"
        )
        assert totals["total_invested_thb"] == pytest.approx(34500.0)

    def test_negative_fx_without_amount_reports_old_value(self, ledger):
        ledger.write_text(
            HEADER + "u2,2026-02-01,SCHD,3,200,-34.5,,0,fx ติดลบ ไม่มียอดเงิน,buy\n",
            encoding="utf-8",
        )

        totals = tracker.get_total_summary()
        assert [row["tx_id"] for row in totals["skipped_rows"]] == ["u2"]
        assert "-34.5" in totals["skipped_rows"][0]["reason"]
        assert totals["skipped_reason"], "ต้องมีข้อความไทยพร้อมแสดงบนหน้าจอ"

    def test_out_of_band_fx_without_amount_reports_old_value(self, ledger):
        """fx=60 นอกช่วง 20–50 และคำนวณย้อนไม่ได้ → ตัดออก + รายงานค่าเดิม."""
        ledger.write_text(
            HEADER + "u3,2026-02-02,GLDM,3,200,60,,0,fx นอกช่วง,buy\n",
            encoding="utf-8",
        )

        totals = tracker.get_total_summary()
        assert [row["tx_id"] for row in totals["skipped_rows"]] == ["u3"]
        assert "60" in totals["skipped_rows"][0]["reason"]

    def test_dividend_with_zero_fx_is_skipped_not_infinite(self, ledger):
        """ปันผลที่ fx=0 เคยได้ยอด USD = inf — ตัวเลขที่กุขึ้นมาชัด ๆ."""
        ledger.write_text(
            HEADER + "dz,2026-03-01,SCHD,0,0,0,1700,0,ปันผล fx=0,dividend\n",
            encoding="utf-8",
        )

        assert tracker.get_dividends().empty, "แถวปันผลที่ fx ใช้ไม่ได้ต้องไม่เข้าผลรวม"
        summary = tracker.get_dividend_summary()
        assert summary["total_usd"] == pytest.approx(0.0)
        assert summary["total_thb"] == pytest.approx(0.0)
        assert [row["tx_id"] for row in summary["skipped_rows"]] == ["dz"]


class TestDividendSummaryReportsSkippedRows:
    """(ข) ``get_dividend_summary()`` ต้องรายงานแถวที่ถูกข้ามด้วยคีย์ชุดเดียวกับ ``get_total_summary()``."""

    def test_skipped_dividend_appears_in_summary(self, ledger):
        ledger.write_text(
            HEADER
            + "d1,2026-03-01,SCHD,0,0,,1700,0,ไม่มี fx,dividend\n"
            + "d2,2026-03-02,SCHD,0,0,34.0,340,0,ครบถ้วน,dividend\n",
            encoding="utf-8",
        )

        summary = tracker.get_dividend_summary()
        assert summary["count"] == 1
        assert summary["total_thb"] == pytest.approx(340.0)
        assert len(summary["skipped_rows"]) == 1, (
            "ยอดปันผลที่น้อยกว่าจริงต้องมีคำเตือนกำกับ ห้ามตัดเงียบ"
        )
        assert summary["skipped_rows"][0]["tx_id"] == "d1"
        assert "SCHD" in summary["skipped_reason"]

    def test_summary_keys_exist_even_when_no_dividend_row_survives(self, ledger):
        """สมุดที่ไม่เหลือแถวปันผลเลยก็ต้องมีคีย์รายงาน (รูปแบบเดียวกันเป๊ะ)."""
        ledger.write_text(
            HEADER + "d3,2026-03-05,VOO,0,0,,900,0,ปันผลไม่มี fx,dividend\n",
            encoding="utf-8",
        )

        summary = tracker.get_dividend_summary()
        assert summary["count"] == 0
        assert [row["tx_id"] for row in summary["skipped_rows"]] == ["d3"]
        assert summary["skipped_reason"]

    def test_complete_ledger_reports_no_skips(self, ledger):
        ledger.write_text(
            HEADER + "d4,2026-03-20,SCHD,0,0,34.0,340,0,ปันผล,dividend\n",
            encoding="utf-8",
        )

        summary = tracker.get_dividend_summary()
        assert summary["skipped_rows"] == []
        assert summary["skipped_reason"] == ""
        assert summary["total_usd"] == pytest.approx(10.0)


class TestDeriveSubtractsRecordedFee:
    """(ค) ``amount_thb`` คือเงินที่จ่ายจริง (รวมค่าธรรมเนียม) ต้องหัก fee ก่อนหาร."""

    def test_recorded_fee_is_subtracted_before_dividing(self, ledger):
        # 2 หุ้น × 500 USD = 1,000 USD; ค่าธรรมเนียม Dime 0.15% ที่ 34.50 = 51.75 บาท
        # จ่ายจริง 34,500 + 51.75 = 34,551.75 → อัตราที่จ่ายจริง = 34.50 พอดี
        ledger.write_text(
            HEADER + "f1,2026-01-05,VOO,2,500,,34551.75,51.75,จ่ายจริงรวม fee,buy\n",
            encoding="utf-8",
        )

        loaded = tracker._load_transactions()
        assert loaded["fx_rate_thb"].iloc[0] == pytest.approx(34.5), (
            "ไม่หัก fee จะได้ 34.55175 — คลาด 0.15% บนเส้นทางเงิน"
        )
        totals = tracker.get_total_summary()
        assert totals["total_invested_thb"] == pytest.approx(34500.0)

    def test_fee_matches_project_fee_formula(self, ledger):
        """ค่าที่หักออกต้องเป็นสูตรเดียวกับ portfolio/fees.py (ไม่ใช่เลขที่คิดเอง)."""
        from portfolio.fees import dime_fee_thb

        fee = dime_fee_thb(2 * 500.0, 34.5)
        assert fee == pytest.approx(51.75)
        ledger.write_text(
            HEADER + f"f2,2026-01-06,VOO,2,500,,{34500.0 + fee},{fee},,buy\n",
            encoding="utf-8",
        )
        assert tracker._load_transactions()["fx_rate_thb"].iloc[0] == pytest.approx(34.5)

    def test_zero_recorded_fee_changes_nothing(self, ledger):
        ledger.write_text(
            HEADER + "f3,2026-01-07,VOO,2,500,,34500,0,ไม่มีค่าธรรมเนียม,buy\n",
            encoding="utf-8",
        )
        assert tracker._load_transactions()["fx_rate_thb"].iloc[0] == pytest.approx(34.5)

    def test_missing_fee_column_value_is_not_invented(self, ledger):
        """ไม่มี fee บันทึกไว้ → ห้ามประมาณขึ้นมาหัก (จะเป็นการกุตัวเลขซ้อนตัวเลข).

        พฤติกรรมที่นิยามไว้: หารตรง ๆ ตามยอดที่บันทึก — ค่าอาจสูงกว่าจริงราว 0.15%
        ถ้ายอดนั้นรวมค่าธรรมเนียมอยู่ (ดูข้อจำกัดใน docstring ของ tracker)
        """
        ledger.write_text(
            HEADER + "f4,2026-01-08,VOO,2,500,,34551.75,,ไม่ได้บันทึก fee,buy\n",
            encoding="utf-8",
        )
        assert tracker._load_transactions()["fx_rate_thb"].iloc[0] == pytest.approx(34.55175)

    def test_fee_larger_than_amount_is_skipped_not_negative(self, ledger):
        """fee > ยอดที่จ่าย → อัตราติดลบ ต้องถูกตัด + รายงาน ไม่ใช่คิดเงินต่อ."""
        ledger.write_text(
            HEADER + "f5,2026-01-09,VOO,2,500,,1000,5000,ข้อมูลเพี้ยน,buy\n",
            encoding="utf-8",
        )

        totals = tracker.get_total_summary()
        assert totals["total_invested_thb"] == pytest.approx(0.0)
        assert [row["tx_id"] for row in totals["skipped_rows"]] == ["f5"]
