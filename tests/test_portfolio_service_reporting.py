# -*- coding: utf-8 -*-
"""ทดสอบ C2 — ``backend/services/portfolio_service.py`` ห้ามทิ้งรายงานแถวที่ถูกข้าม.

``portfolio/tracker.py`` (FIX_PLAN ข้อ 1.2) ตัดแถวที่ข้อมูลไม่ครบออกจากทุกตัวเลข
แล้วแนบรายงานไว้ที่ ``df.attrs['skipped_rows']`` — แต่ ``DataFrame.to_dict()``
**ทิ้ง ``.attrs`` ทั้งหมด** ผู้เรียก API จึงเห็นธุรกรรมน้อยกว่าที่บันทึกไว้จริง
โดยไม่มีอะไรบอกเลย ซึ่งผิดกฎ fail-loud พอ ๆ กับการกุตัวเลข
("ตัดข้อมูลทิ้งเงียบ ๆ" = ซ่อนข้อมูล)

เคสที่หนักที่สุดคือ **ทุกแถวเสีย**: เดิม service คืน ``[]`` ซึ่งแยกไม่ออกเลย
ระหว่าง "สมุดว่าง" กับ "สมุดมีธุรกรรมแต่ใช้ไม่ได้สักแถว"

ชื่อคีย์ต้องเป็นชุดเดียวกับข้อ 1.2 (``skipped_rows`` / ``skipped_reason``)
ห้ามตั้งชื่อใหม่
"""

import json

import pytest

from backend.routers import portfolio as portfolio_router
from backend.services import portfolio_service, report_service
from portfolio import tracker

HEADER = "tx_id,date,ticker,shares,price_usd,fx_rate_thb,amount_thb,fee_thb,note,tx_type\n"

GOOD_ROW = "ok1,2026-01-05,VOO,2,500,34.5,34500,0,ปกติ,buy\n"
# ไม่มีทั้งอัตราแลกเปลี่ยนและยอดบาท → คำนวณย้อนไม่ได้ → tracker ตัดทิ้ง + รายงาน
BAD_ROW = "bad1,2026-02-05,QQQM,1,200,,,0,ลืมกรอก FX และยอดบาท,buy\n"
# ราคา 0 (หุ้นแถม/พิมพ์ผิด) — ฟิลด์ครบจึงไม่ถูกตัด แต่ต้นทุนรวม = 0 → Return (%) = inf
FREE_SHARES_ROW = "free1,2026-03-05,FREE,5,0,34.5,0,0,หุ้นแถม,buy\n"


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


def _assert_reports_bad_row(payload: dict, *, expected_rows: int = 1) -> None:
    """ตรวจว่า payload พก ``skipped_rows``/``skipped_reason`` ที่อ่านรู้เรื่อง."""
    assert payload["skipped_rows"], "ตัดแถวทิ้งแล้วต้องรายงานออกมา ห้ามเงียบ"
    assert len(payload["skipped_rows"]) == expected_rows
    row = payload["skipped_rows"][0]
    assert row["tx_id"] == "bad1"
    assert row["ticker"] == "QQQM"
    assert row["missing_fields"], "ต้องบอกได้ว่าฟิลด์ไหนขาด"
    assert payload["skipped_reason"], "ต้องมีข้อความไทยพร้อมแสดงบนหน้าจอ"
    assert "QQQM" in payload["skipped_reason"]


class TestGetHistory:
    """``get_history()`` — ประวัติธุรกรรมที่หายไปต้องมีคนบอก."""

    def test_skipped_rows_survive_to_dict(self, ledger):
        ledger.write_text(HEADER + GOOD_ROW + BAD_ROW, encoding="utf-8")

        history = portfolio_service.get_history()

        assert [tx["tx_id"] for tx in history["transactions"]] == ["ok1"]
        _assert_reports_bad_row(history)

    def test_all_rows_dropped_is_not_the_same_as_empty_ledger(self, ledger):
        """เคสหนักสุด: เดิม ``if df.empty: return []`` กลืนรายงานทิ้งทั้งดุ้น."""
        ledger.write_text(HEADER + BAD_ROW, encoding="utf-8")

        history = portfolio_service.get_history()

        assert history["transactions"] == []
        _assert_reports_bad_row(history)

    def test_clean_ledger_reports_nothing(self, ledger):
        ledger.write_text(HEADER + GOOD_ROW, encoding="utf-8")

        history = portfolio_service.get_history()

        assert len(history["transactions"]) == 1
        assert history["skipped_rows"] == []
        assert history["skipped_reason"] == ""

    def test_empty_ledger_reports_nothing(self, ledger):
        ledger.write_text(HEADER, encoding="utf-8")

        history = portfolio_service.get_history()

        assert history["transactions"] == []
        assert history["skipped_rows"] == []
        assert history["skipped_reason"] == ""

    def test_payload_is_json_serializable(self, ledger):
        """NaN/Timestamp ต้องถูกแปลงแล้ว ไม่งั้น JSONResponse ระเบิด."""
        ledger.write_text(HEADER + GOOD_ROW + BAD_ROW, encoding="utf-8")

        json.dumps(portfolio_service.get_history())


class TestGetHoldings:
    """``get_holdings()`` แปลง DataFrame เป็น dict เหมือนกัน → ทิ้ง ``.attrs`` เหมือนกัน."""

    def test_skipped_rows_survive_to_dict(self, ledger):
        ledger.write_text(HEADER + GOOD_ROW + BAD_ROW, encoding="utf-8")

        holdings = portfolio_service.get_holdings()

        assert [h["ticker"] for h in holdings["holdings"]] == ["VOO"]
        _assert_reports_bad_row(holdings)

    def test_all_rows_dropped_is_not_the_same_as_empty_portfolio(self, ledger):
        ledger.write_text(HEADER + BAD_ROW, encoding="utf-8")

        holdings = portfolio_service.get_holdings()

        assert holdings["holdings"] == []
        _assert_reports_bad_row(holdings)

    def test_clean_ledger_reports_nothing(self, ledger):
        ledger.write_text(HEADER + GOOD_ROW, encoding="utf-8")

        holdings = portfolio_service.get_holdings()

        assert holdings["holdings"][0]["ticker"] == "VOO"
        assert holdings["holdings"][0]["price_ok"] is True
        assert holdings["skipped_rows"] == []
        assert holdings["skipped_reason"] == ""


class TestGetPortfolioSummary:
    """สรุปพอร์ตรายงานอยู่แล้ว (ข้อ 1.2) — ตรึงไว้ไม่ให้หายไปตอนแก้ get_holdings()."""

    def test_summary_still_reports_skipped_rows(self, ledger):
        ledger.write_text(HEADER + GOOD_ROW + BAD_ROW, encoding="utf-8")

        summary = portfolio_service.get_portfolio_summary()

        assert summary["holdings_count"] == 1
        assert summary["invested_thb"] == pytest.approx(34500.0)
        _assert_reports_bad_row(summary)

    def test_summary_reports_when_everything_was_dropped(self, ledger):
        ledger.write_text(HEADER + BAD_ROW, encoding="utf-8")

        summary = portfolio_service.get_portfolio_summary()

        assert summary["holdings_count"] == 0
        _assert_reports_bad_row(summary)


class TestRouterCarriesReport:
    """ชั้น API ต้องส่งรายงานต่อ — เรียกฟังก์ชัน router ตรง ๆ (ข้าม auth/network)."""

    @staticmethod
    def _body(response) -> dict:
        return json.loads(response.body.decode("utf-8"))["data"]

    def test_history_endpoint(self, ledger):
        ledger.write_text(HEADER + GOOD_ROW + BAD_ROW, encoding="utf-8")

        data = self._body(portfolio_router.get_history())

        assert [tx["tx_id"] for tx in data["transactions"]] == ["ok1"]
        _assert_reports_bad_row(data)

    def test_holdings_endpoint(self, ledger):
        ledger.write_text(HEADER + GOOD_ROW + BAD_ROW, encoding="utf-8")

        data = self._body(portfolio_router.get_holdings())

        assert [h["ticker"] for h in data["holdings"]] == ["VOO"]
        _assert_reports_bad_row(data)

    def test_summary_endpoint(self, ledger):
        ledger.write_text(HEADER + GOOD_ROW + BAD_ROW, encoding="utf-8")

        data = self._body(portfolio_router.get_portfolio())

        _assert_reports_bad_row(data)

    def test_one_odd_row_must_not_kill_the_whole_response(self, ledger):
        """แถวต้นทุน 0 ทำให้ ``Return (%) = inf`` ซึ่ง JSON ไม่รองรับ.

        ``_clean()`` แปลงแค่ NaN → None จึงปล่อย ``inf`` ผ่านไปถึง ``JSONResponse``
        (``allow_nan=False``) แล้วระเบิดเป็น 500 — **ทั้ง endpoint หายไปทั้งก้อน
        รวมถึงรายงาน ``skipped_rows`` ที่เพิ่งอุดไว้** ค่าที่ "มีอยู่แต่ใช้ไม่ได้"
        ต้องกลายเป็น ``None`` เหมือน NaN ไม่ใช่ล้มทั้งคำขอ
        """
        # ฟิลด์ครบทุกช่อง (0 ไม่ใช่ NaN) → tracker ไม่ตัดแถวนี้
        ledger.write_text(HEADER + GOOD_ROW + FREE_SHARES_ROW, encoding="utf-8")

        data = self._body(portfolio_router.get_holdings())

        assert sorted(h["ticker"] for h in data["holdings"]) == ["FREE", "VOO"]
        odd = next(h for h in data["holdings"] if h["ticker"] == "FREE")
        assert odd["return_pct"] is None, "inf ต้องกลายเป็น None ห้ามกลายเป็นตัวเลขหลอก"


class TestReportServiceCarriesReport:
    """รายงานรายเดือน (Telegram/PDF/พรอมป์ LLM) ต้องบอกด้วยว่ามีแถวถูกตัดออก.

    ``report_service`` เตือนเรื่อง ``missing_prices`` อยู่แล้ว แต่กลืน
    ``skipped_rows`` ทิ้ง — ผู้ใช้จึงได้ยอดที่น้อยกว่าจริงโดยไม่มีอะไรบอก
    """

    def test_summary_keeps_skipped_rows(self, ledger):
        ledger.write_text(HEADER + GOOD_ROW + BAD_ROW, encoding="utf-8")

        payload = report_service.get_portfolio_summary(None)

        _assert_reports_bad_row(payload)

    def test_plain_narrative_warns(self, ledger):
        ledger.write_text(HEADER + GOOD_ROW + BAD_ROW, encoding="utf-8")

        all_data = {
            "portfolio": report_service.get_portfolio_summary(None),
            "networth": {"available": False},
            "screener": {"total_signals": 0, "symbols_with_signals": [], "by_preset": {}},
            "goals": {"total": 0, "on_track": [], "off_track": []},
        }

        narrative = report_service._plain_narrative(all_data, "2026-02")

        assert "ข้ามธุรกรรม" in narrative, "รายงานที่ส่งถึงผู้ใช้ต้องบอกว่าตัดแถวไหนออก"
