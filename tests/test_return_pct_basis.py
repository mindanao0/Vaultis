# -*- coding: utf-8 -*-
"""FIX_PLAN ข้อ 3.3 — ``Return (%)`` รายกองต้องอยู่ฐานเดียวกับตัวเลขที่วางข้าง ๆ.

**อาการ** ``get_portfolio_summary()`` คิด ``return_pct = pnl_usd / invested_usd``
(ฐานดอลลาร์) แล้ววางคอลัมน์นั้นไว้ในตารางเดียวกับ ``P&L (THB)`` ส่วน
``get_total_summary()["total_return_pct"]`` ใช้ฐาน**บาท** ⇒ จอเดียวโชว์ได้ว่า
"ขาดทุน 1,072 บาท" คู่กับ "+14.44%" และ %รายกองบวกกันไม่เท่ากับ %รวม

ต้นเหตุคือตัวหารกับตัวตั้งอยู่คนละยุคของอัตราแลกเปลี่ยน: ``invested_thb`` คิดด้วยอัตรา
**วันที่ซื้อ** ส่วน ``current_value_thb`` คิดด้วยอัตรา**วันนี้** — ช่วงที่ค่าเงินขยับพอ
สองฐานพลิกเครื่องหมายกันได้เลย ไม่ใช่แค่ต่างกันเล็กน้อย

ไหลออก **สี่ทางพร้อมกัน** และทั้งสี่ที่วางมันคู่กับตัวเลขฐานบาท: ตาราง holdings บนหน้า
Portfolio · ``/api/portfolio/*`` · AI advisor (``top_holdings``) · PDF รายเดือน

**แก้** ``Return (%)`` = ฐานบาท (สกุลที่ผู้ใช้จ่ายจริงและวัดผลจริง) และเก็บฐานดอลลาร์ไว้
เป็นคอลัมน์แยกชื่อ ``Return USD (%)`` ที่มีป้ายของตัวเอง — ไม่ทิ้งข้อมูล แต่เลิกใช้ชื่อ
กลาง ๆ ปนกัน
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

import portfolio.tracker as tracker
from backend.services import portfolio_service as psvc

FX_BUY_VOO = 34.0
FX_BUY_GLDM = 36.0
# บาท**แข็ง**ขึ้นจากวันซื้อมากพอที่ VOO จะกำไรฝั่งดอลลาร์แต่ขาดทุนฝั่งบาท:
# 2 หุ้น × 550 × 30 = 33,000 บาท เทียบต้นทุน 2 × 500 × 34 = 34,000 บาท ⇒ −1,000 บาท
# ขณะที่ฝั่งดอลลาร์ยังเป็น +100 — นี่คือเหตุผลที่ % ต้องติดป้ายสกุลให้ตรงกับตัวเลขข้าง ๆ
FX_NOW = 30.0


def _ledger() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-02", "2025-02-03"]),
            "ticker": ["VOO", "GLDM"],
            "shares": [2.0, 10.0],
            "price_usd": [500.0, 60.0],
            "fx_rate_thb": [FX_BUY_VOO, FX_BUY_GLDM],
            "amount_thb": [2.0 * 500.0 * FX_BUY_VOO, 10.0 * 60.0 * FX_BUY_GLDM],
            "fee_thb": [0.0, 0.0],
            "note": ["", ""],
            "tx_type": ["buy", "buy"],
        }
    )


@pytest.fixture()
def rows(monkeypatch) -> pd.DataFrame:
    monkeypatch.setattr(tracker, "_load_transactions", _ledger)
    monkeypatch.setattr(
        tracker, "_get_latest_prices", lambda tickers: {"VOO": 550.0, "GLDM": 54.0}
    )
    monkeypatch.setattr(tracker, "_get_usdthb_rate", lambda: FX_NOW)
    return tracker.get_portfolio_summary()


class TestReturnPctIsThbBased:
    def test_ฐานบาทคือ_pnl_thb_หาร_invested_thb(self, rows):
        indexed = rows.set_index("Ticker")
        for ticker in ("VOO", "GLDM"):
            row = indexed.loc[ticker]
            assert row["Return (%)"] == pytest.approx(
                row["P&L (THB)"] / row["Invested (THB)"] * 100.0
            )

    def test_ฐานดอลลาร์อยู่คอลัมน์แยกที่มีป้ายของตัวเอง(self, rows):
        indexed = rows.set_index("Ticker")
        for ticker in ("VOO", "GLDM"):
            row = indexed.loc[ticker]
            assert row["Return USD (%)"] == pytest.approx(
                row["P&L (USD)"] / row["Invested (USD)"] * 100.0
            )

    def test_สองฐานต้องต่างกันจริงในฉากที่ค่าเงินขยับ(self, rows):
        """กันเทสต์ที่ผ่านเพราะบังเอิญสองฐานเท่ากัน (FX วันซื้อ = FX วันนี้)."""
        indexed = rows.set_index("Ticker")
        for ticker in ("VOO", "GLDM"):
            thb = indexed.loc[ticker, "Return (%)"]
            usd = indexed.loc[ticker, "Return USD (%)"]
            assert abs(thb - usd) > 1.0, f"{ticker}: {thb} vs {usd} — ฉากแยกสองฐานไม่ออก"

    def test_ค่าเงินแข็งพอทำให้เครื่องหมายพลิกได้(self, rows):
        """VOO กำไรฝั่งดอลลาร์ แต่บาทแข็งจนฝั่งบาทขาดทุน — เหตุผลที่ต้องเลือกฐานให้ตรงป้าย."""
        voo = rows.set_index("Ticker").loc["VOO"]
        assert voo["Return USD (%)"] > 0
        assert voo["Return (%)"] < 0
        assert voo["P&L (THB)"] < 0 < voo["P&L (USD)"]


class TestReconcilesWithTotal:
    def test_ค่าเฉลี่ยถ่วงน้ำหนักของรายกองเท่ากับ_total_return_pct(self, rows):
        summary = tracker.get_total_summary(rows)
        priced = rows[rows["Price OK"]]
        weighted = float(
            (priced["Return (%)"] * priced["Invested (THB)"]).sum()
            / priced["Invested (THB)"].sum()
        )
        assert weighted == pytest.approx(float(summary["total_return_pct"]))

    def test_ฐานดอลลาร์ไม่กระทบสรุปรวม(self, rows):
        """สรุปรวมยังเป็นฐานบาทเหมือนเดิม — คอลัมน์ใหม่เป็นข้อมูลเพิ่ม ไม่ใช่การเปลี่ยนสัญญา."""
        summary = tracker.get_total_summary(rows)
        assert summary["total_return_pct"] == pytest.approx(
            summary["total_pnl_thb"] / summary["invested_thb_priced"] * 100.0
        )


class TestMissingPriceStaysUnknown:
    @pytest.fixture()
    def partial(self, monkeypatch) -> pd.DataFrame:
        monkeypatch.setattr(tracker, "_load_transactions", _ledger)
        monkeypatch.setattr(tracker, "_get_latest_prices", lambda tickers: {"VOO": 550.0})
        monkeypatch.setattr(tracker, "_get_usdthb_rate", lambda: FX_NOW)
        return tracker.get_portfolio_summary()

    def test_ทั้งสองฐานเป็น_NaN_ไม่ใช่ลบ_100(self, partial):
        gldm = partial.set_index("Ticker").loc["GLDM"]
        assert bool(gldm["Price OK"]) is False
        assert math.isnan(float(gldm["Return (%)"]))
        assert math.isnan(float(gldm["Return USD (%)"]))

    def test_กองที่มีราคายังคิดได้ปกติ(self, partial):
        voo = partial.set_index("Ticker").loc["VOO"]
        assert not math.isnan(float(voo["Return (%)"]))
        assert not math.isnan(float(voo["Return USD (%)"]))


class TestSchemaAndConsumers:
    def test_สมุดว่างก็ต้องมีคอลัมน์ครบ(self, monkeypatch):
        monkeypatch.setattr(tracker, "_load_transactions", lambda: _ledger().iloc[0:0])
        empty = tracker.get_portfolio_summary()
        assert "Return (%)" in empty.columns and "Return USD (%)" in empty.columns

    def test_api_ส่งออกทั้งสองฐาน(self, rows):
        payload = psvc._holdings_payload(rows)["holdings"]
        voo = next(h for h in payload if h["ticker"] == "VOO")
        assert voo["return_pct"] == pytest.approx(
            rows.set_index("Ticker").loc["VOO", "Return (%)"]
        )
        assert voo["return_pct_usd"] == pytest.approx(
            rows.set_index("Ticker").loc["VOO", "Return USD (%)"]
        )
        assert voo["return_pct"] != pytest.approx(voo["return_pct_usd"])

    def test_api_แปลง_inf_เป็น_None_ทั้งสองฐาน(self):
        """ต้นทุน 0 (หุ้นแถม/พิมพ์ผิด) → inf ซึ่ง JSON ไม่รองรับ — ต้องเป็น None ทั้งคู่."""
        frame = pd.DataFrame(
            [
                {
                    "Ticker": "FREE",
                    "Shares": 1.0,
                    "Avg Cost (USD)": 0.0,
                    "Current Price (USD)": 10.0,
                    "Invested (USD)": 0.0,
                    "Invested (THB)": 0.0,
                    "Current Value (USD)": 10.0,
                    "Current Value (THB)": 310.0,
                    "FX Rate (Buy)": float("nan"),
                    "Fee (THB)": 0.0,
                    "P&L (USD)": 10.0,
                    "P&L (THB)": 310.0,
                    "Return (%)": float("inf"),
                    "Return USD (%)": float("inf"),
                    "Price OK": True,
                }
            ]
        )
        row = psvc._holdings_payload(frame)["holdings"][0]
        assert row["return_pct"] is None and row["return_pct_usd"] is None

    def test_pdf_ติดป้ายสกุลของ_percent(self):
        """กระดาษไม่มีทางกดดูว่าเลขนี้ฐานอะไร — หัวตารางต้องบอกเอง."""
        src = (__import__("pathlib").Path(psvc.__file__).parents[2] / "utils" / "pdf_export.py").read_text(
            encoding="utf-8"
        )
        assert '"Return (THB %)"' in src, "หัวคอลัมน์ % ในตาราง Holdings ต้องระบุสกุล"

    def test_ตารางบนหน้าจอแสดงทั้งสองคอลัมน์(self):
        app = pytest.importorskip("dashboard.app")
        src = (__import__("pathlib").Path(app.__file__)).read_text(encoding="utf-8")
        table_block = src.split('display_holdings = holdings_df[')[1].split("].copy()")[0]
        assert '"Return (%)"' in table_block
        assert '"Return USD (%)"' in table_block, (
            "ฐานดอลลาร์ต้องยังเห็นได้บนจอ ไม่ใช่หายไปพร้อมการเปลี่ยนฐาน"
        )
