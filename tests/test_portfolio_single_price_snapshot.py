# -*- coding: utf-8 -*-
"""AUDIT_ROUND2 G2 — ``/api/portfolio`` ต้องประกอบจากราคา **ชุดเดียว** ต่อหนึ่งคำขอ.

เดิม ``portfolio_service.get_portfolio_summary()`` ดึงราคาสองรอบที่ไม่เกี่ยวกัน:

1. ``get_holdings()`` → ``tracker.get_portfolio_summary()`` (ยิงราคา+FX รอบที่ 1)
   ให้ตัวเลขฝั่ง USD (``current_value_usd`` / ``pnl_usd`` / ``holdings_count``)
2. ``tracker.get_total_summary()`` → เรียก ``get_portfolio_summary()`` ซ้ำ
   (ยิงราคา+FX รอบที่ 2) ให้ตัวเลขฝั่ง THB + ``missing_prices`` + ``fx_rate_thb``

ถ้า yfinance ติด rate limit คั่นระหว่างสองรอบ (repo นี้มีประวัติจนต้องใส่แคช)
payload เดียวจะขัดกันเอง: มูลค่า USD ที่ดูสมบูรณ์ คู่กับ ``missing_prices=['VOO']``
ที่บอกว่าไม่มีราคา VOO — และ ``report_service._plain_narrative()`` จะพิมพ์
"มูลค่า 4,500.00 USD" พร้อม "⚠️ ดึงราคาไม่ได้: VOO" ในรายงานฉบับเดียวกัน
แล้วป้อนตัวเลขชุดนี้ให้ LLM อธิบายต่อ

กฎที่เทสต์ชุดนี้ตรึงไว้:

- **ยิงราคา/FX ครั้งเดียวต่อหนึ่งคำขอ** (ไม่ใช่แค่เรื่องประสิทธิภาพ — สอง snapshot
  คือสองความจริงบน payload เดียว)
- **"ไม่รู้" ต้องไม่รู้พร้อมกันทุกช่อง** ฝั่ง USD กับฝั่ง THB กับ ``missing_prices``
  ต้องมาจากราคาชุดเดียวกันเสมอ
"""

import pytest

from backend.services import portfolio_service
from portfolio import tracker

HEADER = "tx_id,date,ticker,shares,price_usd,fx_rate_thb,amount_thb,fee_thb,note,tx_type\n"
# 10 VOO @ 400 USD, อัตรา 34.00 → จ่ายจริง 136,000 บาท (ยอดสอดคล้องกัน ไม่มีแถวถูกตัด)
ONE_BUY = "t1,2026-01-05,VOO,10,400,34.0,136000,0,,buy\n"


class _PriceStub:
    """ผู้ให้ราคาที่ **นับจำนวนครั้งที่ถูกเรียก** และเปลี่ยนคำตอบได้ตามรอบ.

    ``responses`` คือคำตอบของการเรียกครั้งที่ 1, 2, ... (ครั้งถัด ๆ ไปใช้ตัวสุดท้าย)
    ใช้จำลอง rate limit ที่มาคั่นระหว่างการดึงราคาสองรอบในคำขอเดียว
    """

    def __init__(self, *responses: dict[str, float]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, tickers: list[str]) -> dict[str, float]:
        self.calls += 1
        index = min(self.calls, len(self.responses)) - 1
        return dict(self.responses[index])


class _FxStub:
    def __init__(self, rate: float = 34.0) -> None:
        self.rate = rate
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        return self.rate


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    """สมุดบัญชีสังเคราะห์ใน tmp + ตัดเส้นทาง network ทิ้ง (ห้ามแตะสมุดจริงของผู้ใช้)."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    csv_path = data_dir / "transactions.csv"
    monkeypatch.setattr(tracker, "DATA_DIR", data_dir)
    monkeypatch.setattr(tracker, "TRANSACTIONS_FILE", csv_path)
    csv_path.write_text(HEADER + ONE_BUY, encoding="utf-8")
    return csv_path


def _install(monkeypatch, prices: _PriceStub, fx: _FxStub | None = None) -> _FxStub:
    fx = fx or _FxStub()
    monkeypatch.setattr(tracker, "_get_latest_prices", prices)
    monkeypatch.setattr(tracker, "_get_usdthb_rate", fx)
    return fx


class TestOneFetchPerRequest:
    """คำขอเดียว = การดึงราคา/FX ชุดเดียว."""

    def test_api_portfolio_fetches_prices_once(self, ledger, monkeypatch):
        prices = _PriceStub({"VOO": 450.0})
        fx = _install(monkeypatch, prices)

        portfolio_service.get_portfolio_summary()

        assert prices.calls == 1, (
            f"ยิงราคา {prices.calls} ครั้งต่อหนึ่งคำขอ /api/portfolio — "
            "ตัวเลขบน payload เดียวกันจึงมาจากคนละ snapshot ได้"
        )
        assert fx.calls == 1, (
            f"ยิงอัตราแลกเปลี่ยน {fx.calls} ครั้งต่อหนึ่งคำขอ — "
            "มูลค่าบาทกับ fx_rate_thb ที่รายงานออกไปอาจคนละอัตรา"
        )

    def test_empty_ledger_fetches_nothing(self, ledger, monkeypatch):
        """สมุดว่างต้องไม่ยิงราคาเลย (และตอบ 0 ไม่ใช่ None)."""
        ledger.write_text(HEADER, encoding="utf-8")
        prices = _PriceStub({})
        _install(monkeypatch, prices)

        summary = portfolio_service.get_portfolio_summary()

        assert prices.calls == 0
        assert summary["holdings_count"] == 0
        assert summary["current_value_usd"] == 0.0
        assert summary["current_value_thb"] == 0.0


class TestPayloadCannotContradictItself:
    """ราคาที่หายไประหว่างสองรอบต้องไม่ทำให้ payload เดียวขัดกันเอง."""

    def test_price_lost_after_first_fetch_does_not_split_payload(self, ledger, monkeypatch):
        """รอบแรกได้ราคา รอบสองโดน rate limit — ต้องไม่ได้มูลค่า USD คู่กับ missing_prices."""
        prices = _PriceStub({"VOO": 450.0}, {})
        _install(monkeypatch, prices)

        summary = portfolio_service.get_portfolio_summary()

        known_usd = summary["current_value_usd"] is not None
        known_thb = summary["current_value_thb"] is not None
        assert known_usd == known_thb, (
            "ฝั่ง USD กับฝั่ง THB รู้/ไม่รู้ไม่ตรงกัน — มาจากคนละ snapshot: "
            f"usd={summary['current_value_usd']} thb={summary['current_value_thb']}"
        )
        assert bool(summary["missing_prices"]) is not known_usd, (
            "รายงานว่าดึงราคาไม่ได้ พร้อมกับมูลค่าที่คิดจากราคานั้น: "
            f"missing_prices={summary['missing_prices']} "
            f"current_value_usd={summary['current_value_usd']}"
        )
        # snapshot เดียว = ของรอบแรก (ราคาที่ดึงได้จริง)
        assert summary["missing_prices"] == []
        assert summary["current_value_usd"] == pytest.approx(4500.0)
        assert summary["current_value_thb"] == pytest.approx(153000.0)
        assert summary["pnl_usd"] == pytest.approx(500.0)

    def test_price_missing_in_first_fetch_stays_missing(self, ledger, monkeypatch):
        """รอบแรกไม่ได้ราคา — ห้ามมีตัวเลขมูลค่าโผล่มาจากการดึงรอบหลัง."""
        prices = _PriceStub({}, {"VOO": 450.0})
        _install(monkeypatch, prices)

        summary = portfolio_service.get_portfolio_summary()

        assert summary["missing_prices"] == ["VOO"]
        assert summary["current_value_usd"] is None, "ไม่รู้ราคา ห้ามมีมูลค่า USD"
        assert summary["current_value_thb"] is None, "ไม่รู้ราคา ห้ามมีมูลค่า THB"
        assert summary["pnl_usd"] is None
        assert summary["pnl_thb"] is None
        assert summary["return_pct"] is None
        # เงินที่จ่ายไปแล้วยังรู้เสมอ
        assert summary["invested_usd"] == pytest.approx(4000.0)
        assert summary["invested_thb"] == pytest.approx(136000.0)

    def test_holdings_rows_agree_with_missing_prices(self, ledger, monkeypatch):
        """แถวใน ``holdings`` กับ ``missing_prices`` ต้องเป็นรายงานของ snapshot เดียวกัน."""
        ledger.write_text(
            HEADER + ONE_BUY + "t2,2026-01-06,SCHD,20,80,34.0,54400,0,,buy\n",
            encoding="utf-8",
        )
        prices = _PriceStub({"VOO": 450.0}, {"SCHD": 90.0})
        _install(monkeypatch, prices)

        summary = portfolio_service.get_portfolio_summary()
        holdings = portfolio_service.get_holdings()["holdings"]
        # get_holdings() เป็นคนละคำขอ จึงเทียบเฉพาะรูปแบบ ไม่ใช่ค่าตัวเดียวกัน
        assert {h["ticker"] for h in holdings} == {"VOO", "SCHD"}

        assert set(summary["missing_prices"]) == {"SCHD"}, (
            "ราคาที่หายต้องเป็นชุดเดียวกับที่ใช้คิดมูลค่า"
        )
        assert summary["current_value_usd"] == pytest.approx(4500.0)
        assert summary["invested_usd_priced"] == pytest.approx(4000.0)
        assert summary["invested_usd_all"] == pytest.approx(4000.0 + 1600.0)


class TestTrackerAcceptsPrecomputedHoldings:
    """``tracker.get_total_summary()`` ต้องรับ snapshot ที่คำนวณแล้วเข้าไปได้."""

    def test_passing_holdings_does_not_refetch(self, ledger, monkeypatch):
        prices = _PriceStub({"VOO": 450.0})
        fx = _install(monkeypatch, prices)

        holdings = tracker.get_portfolio_summary()
        assert prices.calls == 1 and fx.calls == 1

        totals = tracker.get_total_summary(holdings)

        assert prices.calls == 1, "ส่ง snapshot เข้าไปแล้วยังยิงราคาซ้ำ"
        assert fx.calls == 1, "ส่ง snapshot เข้าไปแล้วยังยิง FX ซ้ำ"
        assert totals["current_value_thb"] == pytest.approx(153000.0)
        assert totals["missing_prices"] == []
        # ที่มาอัตราแลกเปลี่ยน + รายงานแถว ต้องยังเดินทางมาจาก .attrs ของ snapshot เดิม
        assert totals["fx_rate_thb"] == pytest.approx(34.0)
        assert totals["skipped_rows"] == []

    def test_passing_holdings_matches_self_fetched_result(self, ledger, monkeypatch):
        prices = _PriceStub({"VOO": 450.0})
        _install(monkeypatch, prices)

        self_fetched = tracker.get_total_summary()
        passed_in = tracker.get_total_summary(tracker.get_portfolio_summary())

        assert passed_in == self_fetched

    def test_precomputed_holdings_carry_skipped_rows(self, ledger, monkeypatch):
        """แถวที่ถูกตัดต้องยังรายงานออกมาเมื่อส่ง snapshot เข้าไปเอง."""
        ledger.write_text(
            HEADER + ONE_BUY + "t3,2026-01-07,,5,100,34.0,17000,0,ไม่มี ticker,buy\n",
            encoding="utf-8",
        )
        prices = _PriceStub({"VOO": 450.0})
        _install(monkeypatch, prices)

        totals = tracker.get_total_summary(tracker.get_portfolio_summary())

        assert len(totals["skipped_rows"]) == 1
        assert totals["skipped_reason"], "แถวที่ถูกตัดต้องมีข้อความไทยกำกับเสมอ"

    def test_empty_precomputed_holdings_is_zero_not_unknown(self, ledger, monkeypatch):
        ledger.write_text(HEADER, encoding="utf-8")
        prices = _PriceStub({})
        _install(monkeypatch, prices)

        totals = tracker.get_total_summary(tracker.get_portfolio_summary())

        assert prices.calls == 0
        assert totals["current_value_thb"] == 0.0
        assert totals["total_pnl_thb"] == 0.0
        assert isinstance(totals["missing_prices"], list) and totals["missing_prices"] == []

    def test_non_dataframe_argument_fails_loudly(self, ledger, monkeypatch):
        """ส่งของผิดชนิดเข้ามาต้องดังทันที ไม่ใช่แอบไปดึงราคาเองแล้วตอบเลขคนละชุด."""
        prices = _PriceStub({"VOO": 450.0})
        _install(monkeypatch, prices)

        with pytest.raises(TypeError):
            tracker.get_total_summary({"holdings": []})
        assert prices.calls == 0


class TestSummaryAndHoldingsPair:
    """ผู้เรียกที่ต้องใช้ทั้งยอดรวมและรายตัว ต้องได้จาก snapshot เดียว."""

    def test_pair_fetches_once(self, ledger, monkeypatch):
        prices = _PriceStub({"VOO": 450.0})
        fx = _install(monkeypatch, prices)

        bundle = portfolio_service.get_summary_and_holdings()

        assert prices.calls == 1 and fx.calls == 1
        assert bundle["summary"]["current_value_usd"] == pytest.approx(4500.0)
        assert [h["ticker"] for h in bundle["holdings"]] == ["VOO"]

    def test_pair_agrees_when_price_fetch_is_flaky(self, ledger, monkeypatch):
        """ราคาหายหลังรอบแรก — ยอดรวมกับรายตัวต้องเล่าเรื่องเดียวกัน."""
        prices = _PriceStub({"VOO": 450.0}, {})
        _install(monkeypatch, prices)

        bundle = portfolio_service.get_summary_and_holdings()

        priced = [h for h in bundle["holdings"] if h["price_ok"]]
        assert [h["ticker"] for h in priced] == ["VOO"]
        assert bundle["summary"]["missing_prices"] == []
        assert bundle["summary"]["current_value_usd"] == pytest.approx(
            sum(h["current_value_usd"] for h in priced)
        )

    def test_monthly_report_reads_one_snapshot(self, ledger, monkeypatch):
        """``report_service`` ประกอบรายงาน/พรอมป์ LLM จากราคาชุดเดียว."""
        from backend.services import report_service

        prices = _PriceStub({"VOO": 450.0}, {})
        _install(monkeypatch, prices)

        payload = report_service.get_portfolio_summary(None)

        assert prices.calls == 1, (
            f"รายงานรายเดือนยิงราคา {prices.calls} ครั้ง — ยอดรวมกับ top_holdings "
            "อาจมาจากคนละ snapshot ในรายงานฉบับเดียว"
        )
        assert payload["missing_prices"] == []
        assert payload["current_value_usd"] == pytest.approx(4500.0)
        assert [h["ticker"] for h in payload["top_holdings"]] == ["VOO"]

    def test_monthly_report_does_not_mix_known_and_unknown(self, ledger, monkeypatch):
        """ดึงราคาไม่ได้เลย: ห้ามได้มูลค่าจากรอบหลังคู่กับคำเตือนของรอบแรก."""
        from backend.services import report_service

        prices = _PriceStub({}, {"VOO": 450.0})
        _install(monkeypatch, prices)

        payload = report_service.get_portfolio_summary(None)

        assert payload["missing_prices"] == ["VOO"]
        assert payload["current_value_usd"] is None
        assert payload["pnl_usd"] is None
        assert payload["top_holdings"] == [], "ไม่มีราคา = ไม่มีกองที่จัดอันดับได้"


class TestSummaryStillReportsEverything:
    """ตรึงคีย์ที่ผู้เรียกฝั่ง API/รายงานอ่านอยู่ ไม่ให้หายไปตอนรวมสองรอบเป็นรอบเดียว."""

    def test_payload_keys_unchanged(self, ledger, monkeypatch):
        prices = _PriceStub({"VOO": 450.0})
        _install(monkeypatch, prices)

        summary = portfolio_service.get_portfolio_summary()

        for key in (
            "holdings_count",
            "invested_usd",
            "invested_usd_all",
            "invested_usd_priced",
            "invested_thb",
            "invested_thb_all",
            "invested_thb_priced",
            "current_value_usd",
            "current_value_thb",
            "pnl_usd",
            "pnl_thb",
            "return_pct",
            "total_fee",
            "missing_prices",
            "fx_rate_thb",
            "fx_is_live",
            "skipped_rows",
            "skipped_reason",
            "derived_fx_rows",
            "derived_fx_reason",
            "inconsistent_rows",
            "inconsistent_reason",
        ):
            assert key in summary, f"คีย์ {key} หายจาก /api/portfolio"

    def test_get_holdings_still_standalone(self, ledger, monkeypatch):
        """``get_holdings()`` ยังใช้เดี่ยว ๆ ได้ (ผู้เรียกอื่นพึ่งอยู่) และยิงราคาครั้งเดียว."""
        prices = _PriceStub({"VOO": 450.0})
        _install(monkeypatch, prices)

        payload = portfolio_service.get_holdings()

        assert prices.calls == 1
        assert [h["ticker"] for h in payload["holdings"]] == ["VOO"]
        assert payload["holdings"][0]["price_ok"] is True
        assert isinstance(payload["skipped_rows"], list)


def test_ledger_fixture_never_touches_real_file(ledger):
    """กันพลาด: fixture ต้องชี้ไปที่สมุดชั่วคราวเสมอ ห้ามเป็นสมุดจริงของผู้ใช้."""
    path = str(tracker.TRANSACTIONS_FILE)
    assert path == str(ledger)
    assert "portfolio/data/transactions.csv" not in path
