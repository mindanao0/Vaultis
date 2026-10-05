# -*- coding: utf-8 -*-
"""TRADE (หุ้น 30 ตัว รายวัน + บัญชีกระดาษ) — ไม่แตะเน็ต: ราคาสังเคราะห์ + fetch/prophet ที่ฉีดเข้าไป."""
from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import pytest

from analysis import trade_lab as tl
from data.fetcher import PriceDataUnavailableError
from jobs import trade_daily
from portfolio import trade_ledger as ledger
from simulation import data as sim_data
from simulation import trade_sim

IDX = pd.bdate_range("2021-01-04", periods=1000)
ALL = (*tl.TICKERS, tl.BENCHMARK)


def _series(seed: int, idx=IDX, drift=0.0004, vol=0.012) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(drift, vol, len(idx)))), index=idx)


def _ohlc():
    close = pd.DataFrame({t: _series(i + 1) for i, t in enumerate(ALL)})
    rng = np.random.default_rng(7)
    open_ = close * (1 + rng.normal(0, 0.003, close.shape))
    return open_, close


def _fake_prophet(series: pd.Series) -> pd.Series:
    idx = pd.bdate_range(series.index.max() + pd.Timedelta(days=1), periods=330)
    return pd.Series(float(series.iloc[-1]) * np.linspace(1.0, 1.2, len(idx)), index=idx)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "TRADE_LEDGER_PATH", tmp_path / "trade.csv")
    monkeypatch.setattr(sim_data, "DATA_DIR", tmp_path / "sim")
    return tmp_path


# ----------------------------------------------------------------------------- ล็อก / แยก
def test_prereg_lock_and_constants_match_text():
    assert tl.lock_status()["ok"], tl.lock_status()["reason"]
    text = tl.PREREG_PATH.read_text(encoding="utf-8")
    for t in tl.TICKERS:
        assert t in text
    assert len(tl.TICKERS) == 30 and len(set(tl.TICKERS)) == 30
    assert tl.PREDICTORS == (*tl.BASE_PREDICTORS, "majority") and len(tl.PREDICTORS) == 6
    assert tl.N_MIN == {"1d": 252, "1w": 52, "1m": 12} and tl.MIN_EXCESS_ACC_PP == 2.0 and tl.MIN_WEEKS == 52
    assert tl.HORIZON_BARS == {"1d": 1, "1w": 5, "1m": 21}
    assert tl.N_TESTS == 18 and abs(tl.ALPHA - 0.05 / 18) < 1e-12 and abs(tl.ALPHA_ACCOUNT - 0.05 / 6) < 1e-12
    assert tl.FEE == 0.0015 and tl.SLOT_USD == 1000.0
    for must in ("0.05/18", "0.05/6", "ราคาเปิดของแท่งถัดไป", "ห้ามย้อนบันทึก", "30 ช่อง", "0.15% ต่อรายการ"):
        assert must in text, must


def test_trade_is_isolated_from_predict_and_other_modes():
    src = "".join(inspect.getsource(m) for m in (tl, ledger, trade_sim, trade_daily))
    for banned in ("predict_ledger", "predict_state", "predictor_sim", "predict_daily", "select_dca", "select_ledger", "dar_ledger",
                   "stock_ledger", "stock_pick", "load_config", ".download(", "chat_text", "anthropic"):
        assert banned not in src, banned
    from simulation import predictor_sim
    from jobs import predict_daily

    assert trade_sim.FILE != predictor_sim.FILE and trade_daily.STATE_FILE != predict_daily.STATE_FILE
    assert ledger.TRADE_LEDGER_PATH.name != "predict_log.csv"


# ----------------------------------------------------------------------------- ทำนาย
def test_reversal_direction():
    s = pd.Series(np.linspace(100, 90, 30), index=pd.bdate_range("2024-01-01", periods=30))
    assert tl.predict_reversal(s)[0] == 1                           # ตก 5 แท่ง ⇒ ทายเด้ง
    assert tl.predict_reversal(s[::-1].set_axis(s.index))[0] == -1
    assert tl.predict_reversal(s.iloc[:4])[0] is None


def test_make_predictions_full_set_majority_and_no_lookahead():
    _, close = _ohlc()
    preds, skipped, bar = tl.make_predictions(close, prophet_fn=_fake_prophet)
    assert len(preds) == 30 * 6 * 3 and not skipped and bar == f"{close.index.max():%Y-%m-%d}"
    by = {(p.ticker, p.predictor, p.horizon): p for p in preds}
    for t in tl.TICKERS[:3]:
        for h in tl.HORIZON_BARS:
            votes = sum(by[(t, n, h)].direction for n in tl.BASE_PREDICTORS)
            assert by[(t, "majority", h)].direction == (1 if votes > 0 else -1)
    cut = close.index[-120]
    tampered = close.copy()
    tampered.loc[tampered.index > cut] *= 50
    a, _, _ = tl.make_predictions(close, asof=cut, prophet_fn=_fake_prophet)
    b, _, _ = tl.make_predictions(tampered, asof=cut, prophet_fn=_fake_prophet)
    assert [(p.ticker, p.predictor, p.horizon, p.direction, round(p.score, 8)) for p in a] == \
           [(p.ticker, p.predictor, p.horizon, p.direction, round(p.score, 8)) for p in b]


def test_prophet_failure_leaves_even_voters_so_majority_is_skipped_with_reason():
    def boom(_s):
        raise RuntimeError("stan พัง")

    preds, skipped, _ = tl.make_predictions(_ohlc()[1], prophet_fn=boom)
    assert not [p for p in preds if p.predictor in ("prophet", "majority")]
    assert "รัน Prophet ไม่ได้" in skipped["prophet:AAPL"] and "คี่" in skipped["majority:AAPL:1d"]


def test_stale_or_failed_ticker_skipped_not_guessed():
    _, close = _ohlc()
    close["TSLA"] = _series(5, idx=IDX[:-40]).reindex(IDX)
    preds, skipped, _ = tl.make_predictions(close, prophet_fn=_fake_prophet, failed={"NVDA": "boom"})
    assert "เก่า" in skipped["*:TSLA"] and "ดึงราคาไม่ได้" in skipped["*:NVDA"]
    assert {p.ticker for p in preds}.isdisjoint({"TSLA", "NVDA"})


def test_fetch_ohlc_failure_reported_and_voo_mandatory():
    def fetch(t, years):
        if t == "MSFT":
            raise PriceDataUnavailableError("down")
        s = _series(3)
        return pd.DataFrame({"Open": s, "Close": s})

    o, c, failed = tl.fetch_ohlc(fetch=fetch)
    assert "MSFT" in failed and "MSFT" not in c.columns and "VOO" in c.columns and o.shape == c.shape
    with pytest.raises(tl.TradeUnavailableError):
        tl.fetch_ohlc(fetch=lambda t, y: (_ for _ in ()).throw(PriceDataUnavailableError("x")) if t == "VOO" else pd.DataFrame({"Open": _series(1), "Close": _series(1)}))


# ----------------------------------------------------------------------------- สมุด
def _sets(n_days=2):
    _, close = _ohlc()
    out = []
    for k in range(n_days):
        out.append(tl.make_predictions(close, asof=close.index[-1 - (n_days - 1 - k)], prophet_fn=_fake_prophet))
    return out


def test_ledger_append_only_one_set_per_date_no_backfill(sandbox):
    sets = _sets(2)
    (p1, _, b1), (p2, _, b2) = sets
    today = pd.Timestamp(b2) + pd.Timedelta(days=1)
    assert ledger.record_predictions(p1, b1, today=today) == len(p1)
    assert ledger.record_predictions(p1, b1, today=today) is None
    assert ledger.record_predictions(p2, b2, today=today) == len(p2)
    log = ledger.load_log()
    assert len(log) == len(p1) + len(p2) and log["date"].nunique() == 2
    assert ledger.TRADE_LEDGER_PATH.read_text().count("date,ticker") == 1          # หัวตารางครั้งเดียว (ต่อท้ายจริง)
    with pytest.raises(ledger.TradeLedgerError, match="ย้อนบันทึก"):
        ledger.record_predictions(p1, pd.Timestamp(b1) - pd.Timedelta(days=3), today=today)
    with pytest.raises(ledger.TradeLedgerError, match="เก่ากว่า"):
        ledger.record_predictions(p2, b2, today=pd.Timestamp(b2) + pd.Timedelta(days=30))
    assert not [n for n in dir(ledger) if n.startswith(("delete", "update", "edit", "remove"))]


def test_ledger_corrupt_row_fails_loud(sandbox):
    (p, _, b), = _sets(1)
    ledger.record_predictions(p, b, today=pd.Timestamp(b))
    f = ledger.TRADE_LEDGER_PATH
    f.write_text(f.read_text().replace(",1,", ",abc,", 1))
    with pytest.raises(ledger.TradeLedgerError):
        ledger.load_log()


# ----------------------------------------------------------------------------- ให้คะแนน
def _log_for(dates, horizon="1d", predictor="momentum_12_1", direction=1, tickers=tl.TICKERS):
    rows = [{"date": d, "ticker": t, "predictor": predictor, "horizon": horizon, "direction": direction, "score": 0.0, "price_usd": 1.0, "recorded_at": ""}
            for d in dates for t in tickers]
    return pd.DataFrame(rows, columns=ledger.COLUMNS)


def test_resolve_counts_bars_not_calendar_days():
    _, close = _ohlc()
    d0 = IDX[300]
    for horizon, bars in tl.HORIZON_BARS.items():
        r = tl.resolve(_log_for([d0], horizon=horizon, tickers=("AAPL",)), close, pd.Timestamp(IDX[-1]))
        exp = close["AAPL"].iloc[300 + bars] / close["AAPL"].iloc[300] - 1
        assert r["status"].iat[0] == "resolved" and abs(r["ret_pct"].iat[0] - exp * 100) < 1e-9
    last = _log_for([IDX[-1]], tickers=("AAPL",))
    assert tl.resolve(last, close, pd.Timestamp(IDX[-1]))["status"].iat[0] == "pending"        # พรุ่งนี้ยังไม่เกิด
    gone = close.copy()
    gone.loc[gone.index > IDX[310], "AAPL"] = np.nan
    assert tl.resolve(_log_for([IDX[500]], tickers=("AAPL",)), gone, pd.Timestamp(IDX[-1]))["status"].iat[0] == "unpriceable"


def test_prediction_whose_stock_stopped_trading_is_unpriceable_not_pending_forever():
    _, close = _ohlc()
    gone = close.copy()
    gone.loc[gone.index > IDX[310], "AAPL"] = np.nan                # หุ้นหยุดซื้อขายหลังแท่งที่ 310
    r = tl.resolve(_log_for([IDX[309]], horizon="1w", tickers=("AAPL",)), gone, pd.Timestamp(IDX[-1]))
    assert r["status"].iat[0] == "unpriceable"                       # ครบกำหนดตั้งนานแล้วแต่ไม่มีแท่งที่ 5 ให้อ่านผล
    r2 = tl.resolve(_log_for([IDX[309]], horizon="1w", tickers=("AAPL",)), gone, pd.Timestamp(IDX[312]))
    assert r2["status"].iat[0] == "pending"                          # ยังไม่เก่าเกิน MAX_STALE_DAYS ⇒ ยังรอได้


def _resolved(n_days, horizon, good: bool, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for k in range(n_days):
        d = pd.Timestamp("2018-01-01") + pd.offsets.BDay(k)
        went = rng.random(30) < 0.55
        ok = rng.random(30) < (0.75 if good else 0.50)
        for i, t in enumerate(tl.TICKERS):
            rows.append({"date": d, "ticker": t, "predictor": "momentum_12_1", "horizon": horizon, "direction": 1 if (ok[i] == bool(went[i])) else -1,
                         "status": "resolved", "ret_pct": 1.0 if went[i] else -1.0, "went_up": float(went[i]), "correct": float(ok[i])})
    return pd.DataFrame(rows)


def test_accuracy_gate_needs_252_days_then_judges():
    few = tl.score_all(_resolved(251, "1d", True))["groups"]["momentum_12_1|1d"]
    assert few["status"] == "ยังตอบไม่ได้" and few["n_independent"] == 251
    good = tl.score_all(_resolved(300, "1d", True))["groups"]["momentum_12_1|1d"]
    assert good["status"] == "ผ่าน" and all(r.startswith("✓") for r in good["reasons"])
    bad = tl.score_all(_resolved(300, "1d", False))["groups"]["momentum_12_1|1d"]
    assert bad["status"] == "ไม่ผ่าน" and any(r.startswith("✗") for r in bad["reasons"])


def test_longer_horizons_use_every_nth_recorded_date_only():
    g = tl.score_all(_resolved(300, "1w", True))["groups"]["momentum_12_1|1w"]
    assert g["n_days"] == 300 and g["n_independent"] == 60 and g["status"] == "ผ่าน"      # 300/5 = 60 ≥ 52
    m = tl.score_all(_resolved(300, "1m", True))["groups"]["momentum_12_1|1m"]
    assert m["n_independent"] == 15 and m["status"] == "ผ่าน"                            # ceil(300/21) = 15 ≥ 12
    short = tl.score_all(_resolved(100, "1w", True))["groups"]["momentum_12_1|1w"]
    assert short["n_independent"] == 20 and short["status"] == "ยังตอบไม่ได้"


def test_unpriceable_refuses_accuracy_verdict():
    df = _resolved(300, "1d", True)
    df.loc[df.index[:3], "status"] = "unpriceable"
    g = tl.score_all(df)["groups"]["momentum_12_1|1d"]
    assert g["status"] == "ปฏิเสธ" and g["mean_excess_acc_pp"] is None


# ----------------------------------------------------------------------------- บัญชีกระดาษ
def _flat_prices(n=40):
    idx = pd.bdate_range("2024-01-02", periods=n)
    base = pd.DataFrame(100.0, index=idx, columns=list(ALL))
    return base.copy(), base.copy()


def _signal_log(dates_dir: dict, predictor="majority"):
    rows = []
    for d, aapl_dir in dates_dir.items():
        for t in tl.TICKERS:
            rows.append({"date": d, "ticker": t, "predictor": predictor, "horizon": "1d", "direction": aapl_dir if t == "AAPL" else -1,
                         "score": 0.0, "price_usd": 100.0, "recorded_at": ""})
    return pd.DataFrame(rows, columns=ledger.COLUMNS)


def test_account_buys_next_open_with_fee_sells_on_flip_and_holds_otherwise():
    open_, close = _flat_prices()
    idx = close.index
    open_.loc[idx[1], "AAPL"] = 100.0
    close.loc[idx[1], "AAPL"] = 110.0
    open_.loc[idx[2], "AAPL"] = 120.0
    close.loc[idx[2], "AAPL"] = 120.0
    open_.loc[idx[3]:, "AAPL"] = 120.0
    close.loc[idx[3]:, "AAPL"] = 120.0
    log = _signal_log({idx[0]: 1, idx[1]: 1, idx[2]: -1})            # ซื้อ → ถือต่อ → ขาย
    res = tl.replay_accounts(log, open_, close)
    acc = res["accounts"]["majority"]
    tr = acc["trades"]
    assert list(tr["side"]) == ["BUY", "SELL"] and list(tr["ticker"]) == ["AAPL", "AAPL"]
    assert tr["date"].iat[0] == idx[1] and tr["price"].iat[0] == 100.0           # ซื้อที่ "เปิดของแท่งถัดไป" หลังวันที่บันทึก
    shares = 1000.0 * (1 - tl.FEE) / 100.0
    assert abs(acc["nav"].loc[idx[1]] - (29000.0 + shares * 110.0)) < 1e-6        # วันซื้อ: เงินสดอีก 29 ช่อง + หุ้นตามราคาปิด
    assert tr["date"].iat[1] == idx[3] and tr["price"].iat[1] == 120.0           # ขายที่เปิดแท่งถัดจากชุดที่ทายลง
    cash_after = shares * 120.0 * (1 - tl.FEE)
    assert abs(acc["nav"].iloc[-1] - (29000.0 + cash_after)) < 1e-6
    assert abs(acc["fees_paid_usd"] - (1000.0 * tl.FEE + shares * 120.0 * tl.FEE)) < 1e-6 and acc["n_trades"] == 2
    assert not acc["positions"]


def test_pending_orders_show_what_to_do_at_tomorrows_open():
    open_, close = _flat_prices(10)
    idx = close.index
    log = _signal_log({idx[-3]: -1, idx[-2]: -1, idx[-1]: 1})        # ชุดล่าสุด (วันนี้) บอกให้ซื้อ AAPL — ยังไม่มีแท่งเปิดวันถัดไป
    acc = tl.replay_accounts(log, open_, close)["accounts"]["majority"]
    p = {x["ticker"]: x["action"] for x in acc["pending"]}
    assert p["AAPL"] == "BUY" and p["MSFT"] == "STAY_CASH" and acc["n_trades"] == 0
    log2 = _signal_log({idx[-3]: 1, idx[-2]: 1, idx[-1]: -1})        # ซื้อไปแล้ว วันนี้ทายลง ⇒ พรุ่งนี้ขาย
    acc2 = tl.replay_accounts(log2, open_, close)["accounts"]["majority"]
    assert {x["ticker"]: x["action"] for x in acc2["pending"]}["AAPL"] == "SELL" and list(acc2["positions"]) == ["AAPL"]


def test_held_stock_with_vanished_price_makes_nav_nan_and_verdict_refused():
    open_, close = _flat_prices(30)
    idx = close.index
    close.loc[idx[10]:, "AAPL"] = np.nan
    log = _signal_log({idx[0]: 1})
    res = tl.replay_accounts(log, open_, close)
    assert res["accounts"]["majority"]["nav"].isna().any() and "AAPL" in res["accounts"]["majority"]["unpriceable"]
    v = tl.account_verdicts(res)["majority"]
    assert v["status"] == "ปฏิเสธ" and "AAPL" in v["reasons"][0]


def test_account_verdict_waits_for_52_weeks_and_benchmarks_pay_entry_fee():
    open_, close = _ohlc()
    open_, close = open_.iloc[:200], close.iloc[:200]            # ≈ 39 สัปดาห์ < 52
    log = _signal_log({close.index[5]: 1, close.index[6]: 1})
    res = tl.replay_accounts(log, open_, close)
    v = tl.account_verdicts(res)["majority"]
    assert v["status"] == "ยังตอบไม่ได้" and "ห้ามอ่านว่าชนะหรือแพ้" in v["reasons"][0] and v["weeks"] < tl.MIN_WEEKS
    e0 = close.index[6]
    ew0 = res["benchmarks"]["ew30"].loc[e0]
    expect = ((1000.0 * (1 - tl.FEE)) / open_.loc[e0, list(tl.TICKERS)] * close.loc[e0, list(tl.TICKERS)]).sum()
    assert abs(ew0 - expect) < 1e-6 and res["benchmarks"]["ew30"].iloc[0] == 30000.0


def test_always_down_signal_stays_cash_with_no_trades_and_no_fees():
    open_, close = _ohlc()
    log = _signal_log({close.index[10]: -1}, predictor="majority")
    acc = tl.replay_accounts(log, open_, close)["accounts"]["majority"]
    assert acc["n_trades"] == 0 and (acc["nav"] == 30000.0).all()           # ไม่มีสัญญาณซื้อ ⇒ เงินสดล้วน ไม่มีค่าธรรมเนียม


# ----------------------------------------------------------------------------- simulation
def test_gate_simulation_null_world_and_fee_drag(sandbox):
    _, close = _ohlc()
    a = trade_sim.simulate_gate(close, paths=100, seed=1)
    b = trade_sim.simulate_gate(close, paths=100, seed=1)
    assert a["results"] == b["results"]
    rw = a["results"]["rw|reversal_5d"]
    assert rw["pass_rate_pct"] <= 5.0 and rw["mde80_excess_acc_pp"] > 0
    assert rw["trades_per_slot_year"] > a["results"]["rw|momentum_12_1"]["trades_per_slot_year"]     # กลับตัว 5 วันพลิกบ่อยกว่าโมเมนตัมมาก
    assert abs(rw["fee_drag_pct_year"] - rw["trades_per_slot_year"] * tl.FEE * 100.0) < 1e-9
    assert a["results"]["mom|momentum_12_1"]["mean_excess_acc_pp"] > a["results"]["rw|momentum_12_1"]["mean_excess_acc_pp"]
    assert any("Prophet" in x for x in a["limitations"])
    trade_sim.save(a)
    assert trade_sim.load()["paths"] == 100


def test_simulation_rejects_missing_tickers():
    with pytest.raises(ValueError):
        trade_sim.simulate_gate(_ohlc()[1].drop(columns=["ADBE"]), paths=10)


# ----------------------------------------------------------------------------- งานรายวัน
def _patch_job(monkeypatch, open_, close):
    real_make, real_gate = tl.make_predictions, trade_sim.simulate_gate
    monkeypatch.setattr(tl, "fetch_ohlc", lambda **k: (open_, close, {}))
    monkeypatch.setattr(tl, "make_predictions", lambda c, asof=None, failed=None: real_make(c, asof, prophet_fn=_fake_prophet, failed=failed))
    monkeypatch.setattr(ledger, "MAX_BACKFILL_DAYS", 10**6)
    monkeypatch.setattr(trade_sim, "simulate_gate", lambda c, **k: real_gate(c, paths=60))


def test_daily_job_end_to_end_idempotent_and_state(sandbox, monkeypatch):
    open_, close = _ohlc()
    _patch_job(monkeypatch, open_, close)
    first = trade_daily.run_trade_daily()
    assert first["ok"] and any("บันทึกคำทำนายชุด" in s for s in first["steps"]) and any("จำลองเกณฑ์ตัดสิน" in s for s in first["steps"])
    n = len(ledger.load_log())
    second = trade_daily.run_trade_daily()
    assert second["ok"] and any("ไม่เขียนซ้ำ" in s for s in second["steps"]) and len(ledger.load_log()) == n
    assert not any("จำลองเกณฑ์ตัดสิน" in s for s in second["steps"])
    st = trade_daily.load_state()
    assert st["ok"] and st["days_recorded"] == 1 and len(st["tomorrow"]) == 30 and set(st["accounts"]) == set(tl.PREDICTORS)
    assert all(a["pending"] for a in st["accounts"].values())
    assert {"1d", "1w", "1m"} == {g["horizon"] for g in st["groups"].values()}
    row = st["tomorrow"][0]
    assert {"ticker", "price_usd", "majority", "prophet"} <= set(row)


def test_daily_job_failure_reported_never_raised_keeps_old_state(sandbox, monkeypatch):
    open_, close = _ohlc()
    _patch_job(monkeypatch, open_, close)
    assert trade_daily.run_trade_daily()["ok"]

    def down(**k):
        raise tl.TradeUnavailableError("ดึงราคาไม่ได้เลย")

    monkeypatch.setattr(tl, "fetch_ohlc", down)
    out = trade_daily.run_trade_daily()
    assert out["ok"] is False and "ดึงราคาไม่ได้" in out["error"]
    st = trade_daily.load_state()
    assert st["ok"] is False and st["days_recorded"] == 1


def test_registered_in_sandbox_compose_and_gitignore():
    from pathlib import Path

    root = Path(__file__).parent
    assert '"portfolio.trade_ledger"' in (root / "conftest.py").read_text(encoding="utf-8")
    assert "VAULTIS_TRADE_LEDGER_PATH: /tmp/" in (root.parent / "docker-compose.yml").read_text(encoding="utf-8")
    assert "portfolio/data/trade_log.csv" in (root.parent / ".gitignore").read_text(encoding="utf-8")


def test_page_module_imports_is_read_only_and_says_the_hard_truths():
    # AppTest ถูกห้ามในชุดเทสต์ (pyarrow SIGSEGV — ดู test_dashboard_round2_ux.py) จึงตรวจแบบสถิต
    from dashboard import trade_page

    assert callable(trade_page.render_trade_page)
    src = inspect.getsource(trade_page)
    for banned in ("st.button", "st.form", "record_predictions", "st.data_editor", "fetch_ohlc", "predict_page", "predict_daily", "predictor_sim"):
        assert banned not in src, banned
    for must in ("ยังไม่มีหลักฐาน", "ไม่ใช่คำแนะนำลงทุน", "ไม่มีปุ่มแก้/ลบ", "ค่าธรรมเนียม", "≠", "ไม่มีฝีมือ"):
        assert must in src, must
