# -*- coding: utf-8 -*-
"""PREDICT (ทำนายตลาด, พอร์ตกระดาษ) — ไม่แตะเน็ต: ราคาสังเคราะห์ + fetch/prophet ที่ฉีดเข้าไป."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd
import pytest

from analysis import predict_lab as pl
from data.fetcher import PriceDataUnavailableError
from jobs import predict_daily
from portfolio import predict_ledger as ledger
from simulation import data as sim_data
from simulation import predictor_sim

IDX = pd.bdate_range("2021-01-04", periods=1000)


def _series(seed: int, drift=0.0004, vol=0.01, idx=IDX) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(drift, vol, len(idx)))), index=idx)


def _prices() -> pd.DataFrame:
    return pd.DataFrame({t: _series(i + 1) for i, t in enumerate(pl.TICKERS)})


def _fake_prophet(series: pd.Series) -> pd.Series:
    idx = pd.bdate_range(series.index.max() + pd.Timedelta(days=1), periods=330)
    return pd.Series(float(series.iloc[-1]) * np.linspace(1.0, 1.2, len(idx)), index=idx)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "PREDICT_LEDGER_PATH", tmp_path / "log.csv")
    monkeypatch.setattr(sim_data, "DATA_DIR", tmp_path / "sim")
    return tmp_path


# ----------------------------------------------------------------------------- ล็อก
def test_prereg_lock_and_constants_match_text():
    assert pl.lock_status()["ok"], pl.lock_status()["reason"]
    text = pl.PREREG_PATH.read_text(encoding="utf-8")
    for t in pl.TICKERS:
        assert t in text
    assert "0.05/12" in text and pl.N_TESTS == 12 and abs(pl.ALPHA - 0.05 / 12) < 1e-12
    assert pl.N_MIN == {"1m": 36, "6m": 12, "1y": 12} and pl.MIN_EXCESS_ACC_PP == 5.0
    assert pl.HORIZON_DAYS == {"1m": 30, "6m": 182, "1y": 365}
    assert "วันแรกที่บันทึกในเดือนนั้น" in text and "ห้ามย้อนบันทึก" in text


def test_modules_are_isolated_from_other_portfolios():
    import inspect

    from jobs import predict_daily as job

    src = "".join(inspect.getsource(m) for m in (pl, ledger, predictor_sim, job))
    for banned in ("select_dca", "select_ledger", "dar_ledger", "stock_ledger", "load_config", ".download(", "chat_text", "anthropic"):
        assert banned not in src, banned


# ----------------------------------------------------------------------------- ตัวทำนาย
def test_momentum_and_mean_reversion_directions_and_short_history():
    up = pd.Series(np.linspace(100, 200, 400), index=pd.bdate_range("2022-01-03", periods=400))
    down = up[::-1].set_axis(up.index)
    assert pl.predict_momentum(up)[0] == 1 and pl.predict_momentum(down)[0] == -1
    d, why = pl.predict_momentum(up.iloc[:100])[0], pl.predict_momentum(up.iloc[:100])[2]
    assert d is None and "ประวัติไม่พอ" in why
    long_up = pd.Series(np.exp(np.linspace(4, 5, 800)), index=pd.bdate_range("2020-01-01", periods=800))
    assert pl.predict_mean_reversion(long_up)[0] == -1            # ราคาอยู่เหนือค่าเฉลี่ย 3 ปี ⇒ ทายลง
    assert pl.predict_mean_reversion(long_up[::-1].set_axis(long_up.index))[0] == 1
    assert pl.predict_mean_reversion(long_up.iloc[:300])[0] is None


def test_make_predictions_full_set_and_no_lookahead():
    prices = _prices()
    preds, skipped, bar = pl.make_predictions(prices, prophet_fn=_fake_prophet)
    assert len(preds) == 5 * (3 * 3 + 3) and not skipped and bar == f"{prices.index.max():%Y-%m-%d}"
    cut = prices.index[-120]
    tampered = prices.copy()
    tampered.loc[tampered.index > cut] *= 50                         # อนาคตหลัง asof เปลี่ยนไปมาก
    a, _, _ = pl.make_predictions(prices, asof=cut, prophet_fn=_fake_prophet)
    b, _, _ = pl.make_predictions(tampered, asof=cut, prophet_fn=_fake_prophet)
    assert [(p.ticker, p.predictor, p.horizon, p.direction, round(p.score, 9)) for p in a] == \
           [(p.ticker, p.predictor, p.horizon, p.direction, round(p.score, 9)) for p in b]


def test_prophet_failure_drops_only_that_predictor_with_reason():
    def boom(_s):
        raise RuntimeError("stan พัง")

    preds, skipped, _ = pl.make_predictions(_prices(), prophet_fn=boom)
    assert not [p for p in preds if p.predictor == "prophet"] and len([p for p in preds if p.predictor == "scorecard"]) == 15
    assert all("รัน Prophet ไม่ได้" in skipped[f"prophet:{t}"] for t in pl.TICKERS)


def test_stale_or_failed_ticker_is_skipped_not_guessed():
    prices = _prices()
    prices["GLDM"] = _series(9, idx=IDX[:-40]).reindex(IDX)
    preds, skipped, _ = pl.make_predictions(prices, prophet_fn=_fake_prophet, failed={"XLV": "boom"})
    assert "เก่า" in skipped["*:GLDM"] and "ดึงราคาไม่ได้" in skipped["*:XLV"]
    assert {p.ticker for p in preds}.isdisjoint({"GLDM", "XLV"})


def test_fetch_prices_per_ticker_failure_reported():
    def fetch(tks, years):
        if tks == ["QQQM"]:
            raise PriceDataUnavailableError("down")
        return pd.DataFrame({tks[0]: _series(3)})

    prices, failed = pl.fetch_prices(fetch=fetch)
    assert "QQQM" in failed and "QQQM" not in prices.columns
    with pytest.raises(pl.PredictUnavailableError):
        pl.fetch_prices(fetch=lambda tks, y: (_ for _ in ()).throw(PriceDataUnavailableError("x")))


# ----------------------------------------------------------------------------- สมุด
def test_ledger_one_set_per_date_no_backfill_no_edit(sandbox):
    preds, _, bar = pl.make_predictions(_prices(), prophet_fn=_fake_prophet)
    today = pd.Timestamp(bar) + pd.Timedelta(days=1)
    assert ledger.record_predictions(preds, bar, today=today) == len(preds)
    assert ledger.record_predictions(preds, bar, today=today) is None            # วันเดียวกัน ไม่เขียนซ้ำ
    assert len(ledger.load_log()) == len(preds)
    with pytest.raises(ledger.PredictLedgerError, match="ย้อนบันทึก"):
        ledger.record_predictions(preds, pd.Timestamp(bar) - pd.Timedelta(days=3), today=today)   # เก่ากว่าชุดล่าสุด
    with pytest.raises(ledger.PredictLedgerError, match="เก่ากว่า"):
        ledger.record_predictions(preds, bar, today=pd.Timestamp(bar) + pd.Timedelta(days=30))
    assert not [n for n in dir(ledger) if n.startswith(("delete", "update", "edit", "remove"))]


def test_ledger_corrupt_row_fails_loud(sandbox):
    preds, _, bar = pl.make_predictions(_prices(), prophet_fn=_fake_prophet)
    ledger.record_predictions(preds, bar, today=pd.Timestamp(bar))
    p = ledger.PREDICT_LEDGER_PATH
    p.write_text(p.read_text().replace(",1,", ",abc,", 1))
    with pytest.raises(ledger.PredictLedgerError):
        ledger.load_log()


# ----------------------------------------------------------------------------- ให้คะแนน
def _log(dates, directions_fn=lambda t, d: 1, predictor="momentum_12_1", horizon="1m"):
    rows = []
    for d in dates:
        for t in pl.TICKERS:
            rows.append({"pred_id": f"{d:%Y%m%d}{t}", "date": d, "plan_month": f"{d:%Y-%m}", "ticker": t, "predictor": predictor,
                         "horizon": horizon, "direction": directions_fn(t, d), "score": 0.0, "price_usd": 1.0, "recorded_at": ""})
    return pd.DataFrame(rows, columns=ledger.COLUMNS)


def test_resolve_pending_resolved_unpriceable():
    prices = _prices()
    d0 = IDX[300]
    log = _log([d0])
    r = pl.resolve(log, prices, pd.Timestamp(d0))
    assert (r["status"] == "pending").all()
    r = pl.resolve(log, prices, pd.Timestamp(IDX[-1]))
    assert (r["status"] == "resolved").all() and r["correct"].isin([0.0, 1.0]).all()
    short = prices.copy()
    short.loc[short.index > d0 + pd.Timedelta(days=10), "VOO"] = np.nan
    r = pl.resolve(log, short, pd.Timestamp(IDX[-1]))
    assert (r[r["ticker"] == "VOO"]["status"] == "unpriceable").all()
    assert (r[r["ticker"] != "VOO"]["status"] == "resolved").all()


def test_daily_rows_do_not_multiply_cohorts_only_first_date_per_month_counts():
    prices = _prices()
    dates = list(IDX[300:330])                                   # ~6 สัปดาห์ รายวัน
    res = pl.resolve(_log(dates), prices, pd.Timestamp(IDX[-1]))
    scored = pl.score_all(res)["groups"]["momentum_12_1|1m"]
    assert scored["n_cohorts"] == len({f"{d:%Y-%m}" for d in dates}) < len(dates)
    assert scored["status"] == "ยังตอบไม่ได้" and "ห้ามอ่านว่าชนะหรือแพ้" in scored["reasons"][0]
    assert not pl.descriptive(res).empty                          # รายวันใช้พรรณนาได้ ไม่ใช้ตัดสิน


def test_unpriceable_forces_refusal_not_a_verdict():
    prices = _prices()
    prices.loc[prices.index > IDX[310], "XLV"] = np.nan
    res = pl.resolve(_log([IDX[300]]), prices, pd.Timestamp(IDX[-1]))
    g = pl.score_all(res)["groups"]["momentum_12_1|1m"]
    assert g["status"] == "ปฏิเสธ" and g["mean_excess_acc_pp"] is None


def _resolved_cohorts(n, excess_good: bool, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for k in range(n):
        d = pd.Timestamp("2018-01-01") + pd.DateOffset(months=k)
        went = rng.random(5) < 0.6
        correct = np.where(rng.random(5) < (0.95 if excess_good else 0.55), 1.0, 0.0)
        for i, t in enumerate(pl.TICKERS):
            direction = 1 if ((correct[i] == 1.0) == bool(went[i])) else -1
            rows.append({"date": d, "plan_month": f"{d:%Y-%m}", "ticker": t, "predictor": "momentum_12_1", "horizon": "1m", "direction": direction,
                         "status": "resolved", "ret_pct": 2.0 if went[i] else -2.0, "went_up": float(went[i]), "correct": float(correct[i])})
    return pd.DataFrame(rows)


def test_gate_requires_36_cohorts_then_judges():
    few = pl.score_all(_resolved_cohorts(35, True))["groups"]["momentum_12_1|1m"]
    assert few["status"] == "ยังตอบไม่ได้" and few["n_independent"] == 35
    good = pl.score_all(_resolved_cohorts(48, True))["groups"]["momentum_12_1|1m"]
    assert good["status"] == "ผ่าน" and all(r.startswith("✓") for r in good["reasons"])
    bad = pl.score_all(_resolved_cohorts(48, False))["groups"]["momentum_12_1|1m"]
    assert bad["status"] == "ไม่ผ่าน" and any(r.startswith("✗") for r in bad["reasons"])


def test_longer_horizons_use_non_overlapping_cohorts_only():
    df = _resolved_cohorts(48, True)
    df["horizon"] = "6m"
    g = pl.score_all(df)["groups"]["momentum_12_1|6m"]
    assert g["n_cohorts"] == 48 and g["n_independent"] == 8       # ทุกเดือนที่ 6 เท่านั้น
    assert g["status"] == "ยังตอบไม่ได้"                           # 8 < 12


# ----------------------------------------------------------------------------- simulation
def test_gate_simulation_null_world_rarely_passes_and_is_deterministic(sandbox):
    prices = _prices()
    a = predictor_sim.simulate_gate(prices, paths=400, seed=1)
    b = predictor_sim.simulate_gate(prices, paths=400, seed=1)
    assert a["results"] == b["results"]
    rw = a["results"]["rw|momentum_12_1"]
    assert rw["pass_rate_pct"] <= 5.0                              # ไม่มีใครทายได้ ⇒ ต้องแทบไม่ผ่าน
    assert abs(rw["mean_excess_acc_pp"]) < 3.0
    assert a["results"]["mom|momentum_12_1"]["mean_excess_acc_pp"] > rw["mean_excess_acc_pp"]   # สมมติเอดจ์ ⇒ ส่วนเกินสูงขึ้น
    assert rw["mde80_excess_acc_pp"] > pl.MIN_EXCESS_ACC_PP                       # เกณฑ์ต้องการส่วนเกินใหญ่กว่าที่ล็อกไว้มาก จึงจะจับได้ 80%
    assert rw["null_offset_pp"] < 0                                              # ทายสุ่มแพ้ "ขึ้นเสมอ" เสมอ (เส้นฐานเอนเข้าข้างตลาดขาขึ้น)
    assert any("Prophet" in x for x in a["limitations"])
    path = predictor_sim.save(a)
    assert predictor_sim.load()["paths"] == 400 and path.exists()


def test_simulation_rejects_missing_tickers():
    with pytest.raises(ValueError):
        predictor_sim.simulate_gate(_prices().drop(columns=["GLDM"]), paths=10)


# ----------------------------------------------------------------------------- งานรายวัน
def test_daily_job_end_to_end(sandbox, monkeypatch):
    prices = _prices()
    real_make = pl.make_predictions
    real_gate = predictor_sim.simulate_gate
    monkeypatch.setattr(pl, "fetch_prices", lambda **k: (prices, {}))
    monkeypatch.setattr(pl, "make_predictions", lambda p, asof=None, failed=None: real_make(p, asof, prophet_fn=_fake_prophet, failed=failed))
    monkeypatch.setattr(ledger, "MAX_BACKFILL_DAYS", 10**6)          # ราคาสังเคราะห์เป็นของอดีต
    monkeypatch.setattr(predictor_sim, "simulate_gate", lambda p, **k: real_gate(p, paths=150))
    first = predict_daily.run_predict_daily()
    assert first["ok"] and any("บันทึกคำทำนายชุด" in s for s in first["steps"]) and any("จำลองเกณฑ์ตัดสิน" in s for s in first["steps"])
    n_rows = len(ledger.load_log())
    second = predict_daily.run_predict_daily()
    assert second["ok"] and any("ไม่เขียนซ้ำ" in s for s in second["steps"]) and len(ledger.load_log()) == n_rows
    assert not any("จำลองเกณฑ์ตัดสิน" in s for s in second["steps"])      # ผลจำลองยังใหม่ ไม่รันซ้ำทุกวัน
    st = predict_daily.load_state()
    assert st["ok"] and st["days_recorded"] == 1 and st["records"] == n_rows and "momentum_12_1|1m" in st["groups"]
    assert predictor_sim.load() is not None


def test_daily_job_failure_is_reported_never_raised_and_keeps_old_state(sandbox, monkeypatch):
    prices = _prices()
    real_make = pl.make_predictions
    monkeypatch.setattr(pl, "fetch_prices", lambda **k: (prices, {}))
    monkeypatch.setattr(pl, "make_predictions", lambda p, asof=None, failed=None: real_make(p, asof, prophet_fn=_fake_prophet, failed=failed))
    monkeypatch.setattr(ledger, "MAX_BACKFILL_DAYS", 10**6)
    monkeypatch.setattr(predictor_sim, "simulate_gate", lambda p, **k: {"created_at": "2026-10-05T06:30:00+07:00", "paths": 1, "limitations": []})
    assert predict_daily.run_predict_daily()["ok"]

    def down(**k):
        raise pl.PredictUnavailableError("ดึงราคาไม่ได้เลย")

    monkeypatch.setattr(pl, "fetch_prices", down)
    out = predict_daily.run_predict_daily()
    assert out["ok"] is False and "ดึงราคาไม่ได้" in out["error"]
    st = predict_daily.load_state()
    assert st["ok"] is False and st["days_recorded"] == 1             # ผลเดิมยังอยู่ แต่ถูกติดป้ายว่าล้มเหลว


def test_registered_in_sandbox_and_compose():
    from pathlib import Path

    root = Path(__file__).parent
    assert '"portfolio.predict_ledger"' in (root / "conftest.py").read_text(encoding="utf-8")
    assert "VAULTIS_PREDICT_LEDGER_PATH: /tmp/" in (root.parent / "docker-compose.yml").read_text(encoding="utf-8")


def test_verdict_uses_only_first_recorded_date_of_each_month():
    first, later = pd.Timestamp("2024-03-04"), pd.Timestamp("2024-03-20")
    rows = []
    for d, correct in ((first, 0.0), (later, 1.0)):                  # วันแรกทายผิดหมด · วันหลังทายถูกหมด
        for t in pl.TICKERS:
            rows.append({"date": d, "plan_month": "2024-03", "ticker": t, "predictor": "momentum_12_1", "horizon": "1m", "direction": 1,
                         "status": "resolved", "ret_pct": 1.0, "went_up": 1.0, "correct": correct})
    g = pl.score_all(pd.DataFrame(rows))["groups"]["momentum_12_1|1m"]
    assert g["n_cohorts"] == 1 and g["mean_accuracy"] == 0.0         # นับเฉพาะวันแรก ไม่ถัวกับวันหลัง
    assert pl.descriptive(pd.DataFrame(rows)).iloc[0]["accuracy"] == 50.0   # พรรณนาเท่านั้นถึงจะถัวทุกวัน


def test_page_module_imports_and_is_read_only():
    # AppTest ถูกห้ามในชุดเทสต์ (pyarrow SIGSEGV — ดู test_dashboard_round2_ux.py) จึงตรวจแบบสถิต: หน้านี้ import ได้และไม่มีปุ่มบันทึก/แก้/ลบ
    import inspect

    from dashboard import predict_page

    assert callable(predict_page.render_predict_page)
    src = inspect.getsource(predict_page)
    for banned in ("st.button", "st.form", "record_predictions", "st.data_editor", "fetch_prices"):
        assert banned not in src, banned
    for must in ("ไม่ได้แปลว่า", "ยังตอบไม่ได้", "ไม่มีปุ่มแก้/ลบ"):
        assert must in src, must
