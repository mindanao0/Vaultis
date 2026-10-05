# -*- coding: utf-8 -*-
"""STOCK-DCA (เลือกหุ้นรายตัว, พอร์ตกระดาษ forward test) — ไม่แตะเน็ต: ราคาสังเคราะห์ + fetch ที่ฉีดเข้าไป."""
from __future__ import annotations

import hashlib
import re

import numpy as np
import pandas as pd
import pytest

from analysis import stock_pick
from data.fetcher import PriceDataUnavailableError
from portfolio import stock_ledger

IDX = pd.bdate_range("2022-01-03", periods=900)


def _series(vol_daily: float, seed: int, idx=IDX) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0003, vol_daily, len(idx)))), index=idx)


def _prices(extra: dict | None = None) -> pd.DataFrame:
    cols = {}
    for i, t in enumerate(stock_pick.TICKERS):
        cols[t] = _series(0.010 + 0.0005 * i, i)      # i ยิ่งน้อย ยิ่งผันผวนต่ำ
    cols["VOO"] = _series(0.009, 99)
    cols.update(extra or {})
    return pd.DataFrame(cols)


# ----------------------------------------------------------------------------- ล็อก
def test_prereg_lock_matches_file():
    lock = stock_pick.lock_status()
    assert lock["ok"], lock["reason"]


def test_constants_match_locked_prereg_text():
    text = stock_pick.PREREG_PATH.read_text(encoding="utf-8")
    assert stock_pick.K == 5 and "**K = 5**" in text
    assert str(stock_pick.VOL_WINDOW_BARS) in text and str(stock_pick.MIN_VOL_BARS) in text
    assert f"{int(stock_pick.MONTHLY_BUDGET_THB):,}" in text
    assert f"**{stock_pick.MIN_COHORTS} เดือน**" in text
    for t in stock_pick.TICKERS:
        assert re.search(rf"(?<![A-Z-]){re.escape(t)}(?![A-Z-])", text), t
    assert len(stock_pick.TICKERS) == 30 and len(set(stock_pick.TICKERS)) == 30


def test_module_does_not_read_config_or_other_portfolios():
    src = open(stock_pick.__file__, encoding="utf-8").read() + open(stock_ledger.__file__, encoding="utf-8").read()
    for banned in ("load_config", "select_dca", "select_ledger", "dar_ledger", "portfolio.targets", ".download("):
        assert banned not in src, banned


# ----------------------------------------------------------------------------- สูตร
def test_picks_five_lowest_vol_equal_money():
    plan = stock_pick.build_plan(_prices(), pd.Period("2026-10"), fx_rate=33.0)
    assert [ln.ticker for ln in plan.lines] == list(stock_pick.TICKERS[:5])
    assert [ln.amount_thb for ln in plan.lines] == [1000] * 5 and plan.total_thb == 5000
    assert [ln.rank for ln in plan.lines] == [1, 2, 3, 4, 5]
    assert all(plan.lines[i].vol_pct < plan.lines[i + 1].vol_pct for i in range(4))


def test_short_history_and_stale_price_are_skipped_with_reason_not_guessed():
    short = _series(0.001, 1, IDX[-300:])                 # ผันผวนต่ำสุดแต่ประวัติไม่พอ — ต้องไม่ถูกเลือก
    stale = _series(0.001, 2, IDX[:-40])                  # ราคาหยุดไปแล้ว (เลิกกิจการ?) — ต้องไม่ถูกเลือก
    plan = stock_pick.build_plan(_prices({"AAPL": short, "MSFT": stale}), pd.Period("2026-10"), fx_rate=33.0)
    assert "AAPL" in plan.skipped and "ประวัติไม่พอ" in plan.skipped["AAPL"]
    assert "MSFT" in plan.skipped and "เก่า" in plan.skipped["MSFT"]
    assert {"AAPL", "MSFT"}.isdisjoint(plan.universe) and {"AAPL", "MSFT"}.isdisjoint({ln.ticker for ln in plan.lines})


def test_fewer_than_k_eligible_raises():
    cols = {t: _series(0.01, i, IDX[-100:]) for i, t in enumerate(stock_pick.TICKERS)}
    cols["VOO"] = _series(0.009, 99)
    with pytest.raises(stock_pick.StockPickUnavailableError):
        stock_pick.build_plan(pd.DataFrame(cols), pd.Period("2026-10"), fx_rate=33.0)


def test_failed_fetch_is_reported_per_ticker_and_voo_is_mandatory():
    def fetch(tks, years):
        if tks == ["NVDA"]:
            raise PriceDataUnavailableError("boom")
        return pd.DataFrame({tks[0]: _series(0.01, 3)})

    prices, failed = stock_pick.fetch_prices(fetch=fetch)
    assert "NVDA" in failed and "NVDA" not in prices.columns and "VOO" in prices.columns

    def no_voo(tks, years):
        if tks == ["VOO"]:
            raise PriceDataUnavailableError("down")
        return pd.DataFrame({tks[0]: _series(0.01, 3)})

    with pytest.raises(stock_pick.StockPickUnavailableError):
        stock_pick.fetch_prices(fetch=no_voo)


# ----------------------------------------------------------------------------- สมุด
def _plan(month="2026-10", prices=None):
    return stock_pick.build_plan(prices if prices is not None else _prices(), pd.Period(month), fx_rate=33.0)


def test_record_once_per_month_and_only_current_month(tmp_path, monkeypatch):
    monkeypatch.setattr(stock_ledger, "STOCK_LEDGER_PATH", tmp_path / "s.csv")
    plan = _plan()
    with pytest.raises(stock_ledger.StockLedgerError, match="เดือนปัจจุบัน"):
        stock_ledger.record_plan(plan, today=pd.Timestamp("2026-11-03"))
    ids = stock_ledger.record_plan(plan, today=pd.Timestamp("2026-10-05"))
    assert len(ids) == 5 and len(stock_ledger.load_stock_transactions()) == 5
    with pytest.raises(stock_ledger.StockLedgerError, match="เดือนละครั้ง"):
        stock_ledger.record_plan(plan, today=pd.Timestamp("2026-10-20"))
    assert len(stock_ledger.load_stock_transactions()) == 5     # ล้มแล้วไม่เขียนครึ่ง ๆ กลาง ๆ


def test_corrupt_ledger_row_fails_loud(tmp_path, monkeypatch):
    p = tmp_path / "s.csv"
    monkeypatch.setattr(stock_ledger, "STOCK_LEDGER_PATH", p)
    stock_ledger.record_plan(_plan(), today=pd.Timestamp("2026-10-05"))
    p.write_text(p.read_text().replace("1000.0", "abc", 1))
    with pytest.raises(stock_ledger.StockLedgerError):
        stock_ledger.load_stock_transactions()


def _tx_for_months(months, universe, picks, fx=33.0, amount=1000.0):
    rows = []
    for m in months:
        d = pd.Timestamp(m + "-05")
        for t in picks:
            rows.append({"tx_id": f"{m}{t}", "date": d, "plan_month": m, "ticker": t, "amount_thb": amount, "fx_rate": fx,
                         "price_usd": 1.0, "units": 1.0, "universe": ",".join(universe), "rule": stock_pick.RULE, "recorded_at": ""})
    return pd.DataFrame(rows, columns=stock_ledger.COLUMNS)


def test_verdict_cannot_be_given_before_36_cohorts():
    prices = _prices()
    uni = list(stock_pick.TICKERS)
    tx = _tx_for_months(["2024-01", "2024-02"], uni, uni[:5])
    res = stock_ledger.compare(tx, prices, 33.0)
    assert res["status"] == "ยังตอบไม่ได้" and res["n_cohorts"] == 2
    assert res["diff_vs_shadow_pct"] is not None            # ตัวเลขแสดงได้ แต่สถานะต้องไม่เป็นผ่าน/ไม่ผ่าน


def test_picking_whole_universe_equals_shadow_exactly():
    prices = _prices()
    five = list(stock_pick.TICKERS[:5])
    tx = _tx_for_months(["2024-01", "2024-02"], five, five)
    res = stock_ledger.compare(tx, prices, 33.0)
    assert abs(res["diff_vs_shadow_pct"]) < 1e-9 and abs(res["picks_value_thb"] - res["shadow_value_thb"]) < 1e-6


def test_stale_holding_refuses_verdict_instead_of_ignoring_it():
    gone = _series(0.01, 7, IDX[:-100])                      # หุ้นที่ถูกเลือกไว้ แล้วราคาหายไป
    prices = _prices({"JPM": gone})
    uni = list(stock_pick.TICKERS)
    tx = _tx_for_months(["2024-01"], uni, ["JPM", "AAPL", "MSFT", "NVDA", "GOOGL"])
    res = stock_ledger.compare(tx, prices, 33.0)
    assert res["status"] == "ปฏิเสธ" and "JPM" in res["reasons"][0]
    assert res["picks_value_thb"] is None and res["diff_vs_voo_pct"] is None


def test_36_cohorts_gate_requires_every_criterion():
    prices = _prices()
    uni = list(stock_pick.TICKERS)
    months = [str(p) for p in pd.period_range("2022-04", periods=36, freq="M") if pd.Timestamp(str(p) + "-05") <= IDX[-1]]
    if len(months) < 36:
        pytest.skip("ชุดสังเคราะห์สั้นกว่า 36 เดือน")
    res = stock_ledger.compare(_tx_for_months(months, uni, uni[:5]), prices, 33.0)
    assert res["status"] in ("ผ่าน", "ไม่ผ่าน") and len(res["reasons"]) == 3
    assert (res["status"] == "ผ่าน") == all(r.startswith("✓") for r in res["reasons"])


def test_ledger_registered_in_test_sandbox():
    from pathlib import Path

    src = (Path(__file__).parent / "conftest.py").read_text(encoding="utf-8")
    assert '"portfolio.stock_ledger"' in src and '"STOCK_LEDGER_PATH"' in src
    compose = (Path(__file__).parent.parent / "docker-compose.yml").read_text(encoding="utf-8")
    assert "VAULTIS_STOCK_LEDGER_PATH: /tmp/" in compose
