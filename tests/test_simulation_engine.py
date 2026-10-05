# -*- coding: utf-8 -*-
"""แพ็กเกจ ``simulation/`` (งานหลักของระบบ) — ล็อก 4 เรื่อง ทั้งหมดออฟไลน์:

1. เอนจินตรงกับตัววิจัย ``research/dar_sim/sim.py`` (ที่ล็อกด้วย SHA-256) **ทุกตัวเลข** เมื่อใช้ห้ากองเดิม
2. รองรับกองกี่ตัวก็ได้ และเหตุการณ์ใหญ่ยังไม่เลื่อนค่าเฉลี่ย
3. สายข้อมูล: ข้อมูลดิบ → manifest/hash → แผงสอบเทียบ → เอนจิน (ข้อมูลสังเคราะห์) ล้มดังเมื่อข้อมูลไม่ครบ/ถูกแก้
4. บริการ: โครงผลลัพธ์ · ข้อความสรุปต้องบอกเมื่อไม่มีผล/เก่า · ห้ามกลยุทธ์ที่ไม่รู้จัก
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from simulation import data as sim_data
from simulation import engine, service
from simulation.universe import CORE_ASSETS, asset_for, is_guessed
from sim_synth import frame as _frame, synthetic_panel as _panel, synthetic_raw as _synthetic_raw

_RESEARCH = Path(__file__).resolve().parents[1] / "research" / "dar_sim" / "sim.py"
_spec = importlib.util.spec_from_file_location("dar_sim_research", _RESEARCH)
research = importlib.util.module_from_spec(_spec)
sys.modules["dar_sim_research"] = research
_spec.loader.exec_module(research)

FIVE = [a.ticker for a in CORE_ASSETS]


# ---------------------------------------------------------------- 1) parity กับตัววิจัย
@pytest.mark.parametrize("world", ["rw", "rev", "mom", "prem", "boot"])
@pytest.mark.parametrize("drift", ["mid", "low", "hist"])
def test_engine_equals_research_simulator_number_for_number(world, drift):
    panel = _panel(seed=3, hr=215)
    preset = np.array([0.35, 0.25, 0.20, 0.10, 0.10])
    common = dict(world=world, drift=drift, P=40, T=60, seed=17, horizons=(60,))
    ref = research.run_chunk(panel, research.Config(**common, arms=("DAR", "EQ", "ERC", "BLEND", "PRESET")))
    mine = engine.run_chunk(panel, engine.Config(**common, arms=("DAR", "EQ", "ERC", "BLEND"), fixed={"PRESET": preset}))
    for a in ("DAR", "EQ", "ERC", "BLEND", "PRESET"):
        for k in ("V", "irr", "maxdd", "V_real", "under"):
            assert np.allclose(ref["H"][60]["arms"][a][k], mine["H"][60]["arms"][a][k], rtol=1e-11, atol=1e-12), (world, drift, a, k)


def test_engine_constants_are_read_from_the_real_formula():
    from analysis import dar_dca

    assert (engine.AMP_FROM, engine.AMP_TO, engine.DRIFT_COEF, engine.SD_MIN) == (
        dar_dca.AMP_FROM, dar_dca.AMP_TO, dar_dca.DRIFT_COEF, dar_dca.SD_MIN)
    assert (engine.FLOOR_FRAC, engine.CAP_MULT, engine.HISTORY_MONTHS) == (
        dar_dca.FLOOR_FRAC, dar_dca.CAP_MULT, dar_dca.HISTORY_MONTHS)
    from portfolio.risk_weights import BLEND_ERC_SHARE
    assert BLEND_ERC_SHARE == 0.5


# ---------------------------------------------------------------- 2) กองกี่ตัวก็ได้
@pytest.mark.parametrize("n", [1 + 1, 3, 8, 12])
def test_engine_runs_for_any_number_of_funds(n):
    panel = _panel(n=n, seed=5, hr=215)
    cfg = engine.Config(world="rw", P=60, T=60, seed=2, horizons=(60,), arms=("DAR", "EQ", "ERC", "BLEND"))
    r = engine.run_chunk(panel, cfg)
    for a, x in r["H"][60]["arms"].items():
        assert np.isfinite(x["V"]).all() and (x["V"] > 0).all(), a
    for a, w in r["mean_weights"].items():
        assert sum(w) == pytest.approx(1.0, abs=1e-9) and len(w) == n


def test_too_many_funds_for_the_budget_is_refused_not_silently_wrong():
    panel = _panel(n=3, seed=1, hr=215)
    with pytest.raises(AssertionError, match="กอง"):
        engine.run_chunk(panel, engine.Config(P=20, T=24, horizons=(24,), arms=("EQ",), budget_thb=200.0))


def test_dar_needs_181_months_and_short_history_is_refused():
    panel = _panel(seed=2, hr=120)
    with pytest.raises(ValueError, match="181"):
        engine.run_chunk(panel, engine.Config(P=20, T=24, horizons=(24,), arms=("DAR",)))
    engine.run_chunk(panel, engine.Config(P=20, T=24, horizons=(24,), arms=("EQ", "ERC")))  # ไม่มี DAR = ใช้ได้


def test_events_do_not_shift_the_mean_return_for_any_kind():
    panel = _panel(n=8, seed=4, hr=215)
    means = {}
    for em in (0.0, 1.0):
        r = engine.run_chunk(panel, engine.Config(world="rw", drift="mid", event_mult=em, P=4000, T=240, seed=21, debug=4000,
                                                   arms=("EQ",), horizons=(240,)))
        Hr = panel["live_me_logs"].shape[0]
        g = (r["LL"][:, Hr - 1 + 240, :] - r["LL"][:, Hr - 1, :]) / 20.0
        means[em] = np.nanmean(g, axis=0)
    assert np.allclose(means[0.0], means[1.0], atol=0.004), (means[0.0], means[1.0])


def test_unknown_ticker_is_flagged_as_a_guess():
    assert is_guessed("ZZZZ") and asset_for("ZZZZ").kind == "other_equity"
    assert not is_guessed("VOO") and asset_for("VOO").calib_proxy == "SPY"


def test_events_table_has_a_value_for_every_kind():
    from simulation.universe import KINDS

    kinds = list(KINDS)
    ev = engine.build_events(_panel()["events_measured"], kinds)
    assert [e["name"] for e in ev] == engine.EVENT_NAMES
    for e in ev:
        assert len(e["shock"]) == len(kinds) and np.isfinite(e["shock"]).all()


# ---------------------------------------------------------------- 3) สายข้อมูลครบวง (ข้อมูลสังเคราะห์)
def test_month_completeness_rule():
    f = sim_data._last_complete_month_end
    assert f(pd.Timestamp("2026-10-02")) == pd.Timestamp("2026-09-30")     # เดือนยังไม่ปิด → เดือนก่อนหน้า
    assert f(pd.Timestamp("2026-09-30")) == pd.Timestamp("2026-09-30")     # วันสุดท้ายพอดี
    assert f(pd.Timestamp("2026-10-30")) == pd.Timestamp("2026-10-31")     # ศุกร์ก่อนวันเสาร์สิ้นเดือน = ปิดครบแล้ว


def test_raw_roundtrip_hash_and_staleness(tmp_path):
    raw = _synthetic_raw()
    manifest = sim_data.save_raw(raw, tmp_path)
    back = sim_data.load_raw(tmp_path)
    assert back.sha256 == manifest["sha256"] and list(back.daily.columns) == list(raw.daily.columns)
    st = sim_data.data_status(tmp_path, now=datetime(2026, 10, 6, tzinfo=timezone(timedelta(hours=7))))
    assert st["exists"] and not st["stale"] and st["last_bar"] == "2026-10-02"
    old = sim_data.data_status(tmp_path, now=datetime(2026, 10, 20, tzinfo=timezone(timedelta(hours=7))))
    assert old["stale"] and "เก่า" in old["reason"]
    # แก้ไฟล์ข้อมูลแล้ว hash ต้องไม่ตรง → ล้มดัง ไม่ใช้ข้อมูลที่ไม่รู้ที่มา
    with open(tmp_path / sim_data.RAW_FILE, "ab") as fh:
        fh.write(b"tamper")
    with pytest.raises(sim_data.SimulationDataError, match="hash"):
        sim_data.load_raw(tmp_path)


def test_missing_data_is_loud(tmp_path):
    assert sim_data.data_status(tmp_path)["exists"] is False
    with pytest.raises(sim_data.SimulationDataError, match="ยังไม่มีข้อมูล"):
        sim_data.load_raw(tmp_path)


def test_build_panel_end_to_end_then_run_the_engine():
    panel = sim_data.build_panel(_synthetic_raw(seed=1))
    assert panel["funds"] == FIVE and panel["meta"]["as_of"] == "2026-09-30" and panel["meta"]["plan_month"] == "2026-10"
    assert panel["meta"]["live_months"] >= 61 and panel["start_regime"] in range(4)
    assert panel["live_S"].shape == (60, 15) and not np.isnan(panel["live_S"]).any()
    assert all(len(p) > 0 for p in panel["pools"])
    r = engine.run_chunk(panel, engine.Config(world="rw", P=50, T=36, seed=3, horizons=(36,), arms=("EQ", "ERC", "BLEND")))
    for x in r["H"][36]["arms"].values():
        assert np.isfinite(x["irr"]).all()
    rb = engine.run_chunk(panel, engine.Config(world="boot", P=50, T=36, seed=3, horizons=(36,), arms=("EQ", "ERC"), fx_revert=0.0))
    assert np.isfinite(rb["H"][36]["arms"]["ERC"]["V"]).all()


def test_build_panel_refuses_a_fund_with_no_price_and_too_little_history():
    raw = _synthetic_raw()
    with pytest.raises(sim_data.SimulationDataError, match="ไม่มีราคา"):
        sim_data.build_panel(raw, FIVE + ["ZZZZ"])
    young = _synthetic_raw()
    young.daily["GLDM"] = young.daily["GLDM"].where(young.daily.index >= "2023-01-02")
    young.daily["GLD"] = young.daily["GLD"].where(young.daily.index >= "2023-01-02")
    with pytest.raises(sim_data.SimulationDataError):
        sim_data.build_panel(young)


# ---------------------------------------------------------------- 4) บริการ
def test_service_result_shape_and_determinism():
    panel = _panel(seed=6, hr=215)
    kw = dict(reference="ERC", budget_thb=5000.0, worlds=("rw", "boot"), horizons=(60, 120), paths=300, seed=11)
    strategies = {"ERC": "ERC", "BLEND": "BLEND", "1/N": "EQ", "ของฉัน": {"VOO": 0.5, "SCHD": 0.5}}
    a = service.simulate_strategies(panel, strategies, **kw)
    b = service.simulate_strategies(panel, strategies, **kw)
    assert a == b, "seed เดียวกันต้องได้ผลเหมือนเดิมทุกตัวเลข"
    rw = a["worlds"]["rw"]["horizons"]["120"]
    assert set(rw["strategies"]) == set(strategies)
    assert set(rw["vs_reference"]) == set(strategies) - {"ERC"}
    s = rw["strategies"]["BLEND"]
    assert s["irr_p5"] <= s["irr_p50"] <= s["irr_p95"] and 0 <= s["p_loss_nominal"] <= 1 and s["contributed_thb"] == 5000.0 * 120


def test_service_rejects_bad_inputs():
    panel = _panel(seed=6, hr=215)
    kw = dict(reference="ERC", budget_thb=5000.0, worlds=("rw",), horizons=(60,), paths=300)
    with pytest.raises(ValueError, match="กลยุทธ์"):
        service.simulate_strategies(panel, {"ERC": "ERC", "x": "MAGIC"}, **kw)
    with pytest.raises(ValueError, match="น้ำหนัก"):
        service.simulate_strategies(panel, {"ERC": "ERC", "bad": {"VOO": -1.0}}, **kw)
    with pytest.raises(ValueError, match="เส้นทาง"):
        service.simulate_strategies(panel, {"ERC": "ERC"}, **{**kw, "paths": 10})


def test_summary_lines_never_stay_silent():
    assert "ยังไม่มีผล" in service.summary_lines(None)[0]
    panel = _panel(seed=7, hr=215)
    res = service.simulate_strategies(panel, {"ERC": "ERC", "BLEND": "BLEND", "1/N": "EQ"}, reference="ERC", budget_thb=5000.0,
                                      worlds=("rw", "boot"), horizons=(120, 240), paths=300, seed=3)
    full = {"created_at": datetime.now(timezone(timedelta(hours=7))).isoformat(timespec="seconds"),
            "plan": {"method": "blend", "plan_strategy": "BLEND", "budget_thb": 5000.0}, "paths_per_world": 300, "worlds": res["worlds"],
            "data": {"as_of": "2026-09-30"}}
    text = "\n".join(service.summary_lines(full))
    assert "BLEND" in text and "10 ปี" in text and "20 ปี" in text and "ไม่ใช่พยากรณ์" in text and "ERC ล้วน" in text
    old = dict(full, created_at=(datetime.now(timezone(timedelta(hours=7))) - timedelta(days=30)).isoformat(timespec="seconds"))
    assert "เก่า" in "\n".join(service.summary_lines(old))
    broken = dict(full, worlds={"rw": {"horizons": {}}})
    assert "อ่านผลไม่ได้" in service.summary_lines(broken)[0]


def test_last_plan_roundtrip(tmp_path):
    assert service.load_last_plan(tmp_path) is None
    service.save_last_plan({"created_at": "2026-10-05T09:00:00+07:00", "x": [1, 2]}, tmp_path)
    assert service.load_last_plan(tmp_path)["x"] == [1, 2]
    (tmp_path / service.LAST_PLAN_FILE).write_text("{ไม่ใช่ json", encoding="utf-8")
    assert service.load_last_plan(tmp_path) is None  # อ่านไม่ได้ = None + log ERROR ไม่ใช่ crash


# ---------------------------------------------------------------- ต้นทุน: ภาษีปันผล + FX spread (ค่าเริ่มต้นของเอนจิน = 0 เพื่อ parity)
def _eq_value(panel, **kw):
    cfg = engine.Config(world="rw", drift="mid", P=300, T=120, seed=9, horizons=(120,), arms=("EQ",), **kw)
    return engine.run_chunk(panel, cfg)["H"][120]["arms"]["EQ"]["V"]


def test_withholding_tax_lowers_value_by_roughly_yield_times_rate():
    panel = _panel(seed=8, hr=215)
    base = _eq_value(panel)
    taxed = _eq_value(panel, withholding_pct=0.15)
    ratio = float(np.median(taxed / base))
    # ผลตอบแทนหายปีละ 0.15 × 2% = 0.3% — DCA 10 ปี เงินที่ลงเฉลี่ยอยู่ ~5 ปี → มูลค่าปลายทางหายราว 1.5%
    assert 0.975 < ratio < 0.995, ratio
    assert (taxed < base).all()


def test_fx_spread_scales_every_purchase_exactly():
    panel = _panel(seed=8, hr=215)
    base = _eq_value(panel)
    spread = _eq_value(panel, fx_spread_pct=0.25)
    assert np.allclose(spread / base, 1 - 0.0025, rtol=1e-12)


def test_withholding_needs_yields_and_never_guesses_them():
    panel = _panel(seed=8, hr=215)
    panel.pop("yields")
    with pytest.raises(ValueError, match="yield"):
        _eq_value(panel, withholding_pct=0.15)
    _eq_value(panel)  # ไม่หักภาษี = ไม่ต้องใช้ yield


def test_panel_measures_yields_from_real_dividends_and_flags_defaults():
    raw = _synthetic_raw(seed=2)
    panel = sim_data.build_panel(raw)
    assert panel["meta"]["yield_source"]["VOO"] == "measured" and 0.015 < panel["yields"]["VOO"] < 0.025
    assert panel["yields"]["GLDM"] == 0.0, "กองที่ไม่จ่ายปันผลต้อง yield 0 (ไม่ใช่ค่าเริ่มต้นตามชนิด)"
    raw.dividends.pop("SCHD")
    p2 = sim_data.build_panel(raw)
    assert p2["meta"]["yield_source"]["SCHD"] == "kind_default" and p2["yields"]["SCHD"] == sim_data.DEFAULT_YIELD_BY_KIND["us_dividend"]


def test_dividends_survive_the_raw_roundtrip(tmp_path):
    raw = _synthetic_raw(seed=3)
    manifest = sim_data.save_raw(raw, tmp_path)
    back = sim_data.load_raw(tmp_path)
    assert set(back.dividends) == set(raw.dividends) and manifest["dividends"]["VOO"] == len(raw.dividends["VOO"])
    assert float(back.dividends["VOO"].sum()) == pytest.approx(float(raw.dividends["VOO"].sum()))


def test_service_result_carries_costs_and_matching_limitations():
    panel = _panel(seed=6, hr=215)
    kw = dict(reference="ERC", budget_thb=5000.0, worlds=("rw",), horizons=(60,), paths=200, seed=2)
    taxed = service.simulate_strategies(panel, {"ERC": "ERC", "BLEND": "BLEND"}, **kw)
    assert taxed["costs"]["withholding_pct"] == 0.15 and taxed["costs"]["yields"]["VOO"] == 0.02
    assert any("หักภาษี" in ln for ln in taxed["limitations"]) and not any(ln.startswith("ไม่ได้หักภาษี") for ln in taxed["limitations"])
    plain = service.simulate_strategies(panel, {"ERC": "ERC", "BLEND": "BLEND"}, costs={"withholding_pct": 0.0, "fx_spread_pct": 0.0}, **kw)
    assert any(ln.startswith("ไม่ได้หักภาษี") for ln in plain["limitations"])
    a = taxed["worlds"]["rw"]["horizons"]["60"]["strategies"]["BLEND"]["value_med_thb"]
    b = plain["worlds"]["rw"]["horizons"]["60"]["strategies"]["BLEND"]["value_med_thb"]
    assert a < b, "หักต้นทุนแล้วมูลค่าปลายทางต้องต่ำกว่า"


# ---------------------------------------------------------------- สินทรัพย์เพิ่ม
def test_extra_funds_are_available_and_a_mixed_universe_builds():
    raw = _synthetic_raw(seed=4)
    sim_data.save_raw(raw, tmp := Path(__import__("tempfile").mkdtemp()))
    st = sim_data.data_status(tmp)
    assert {"BND", "VXUS"} <= set(st["funds_available"]) and set(FIVE) <= set(st["funds_available"])
    panel = sim_data.build_panel(raw, ["VOO", "BND", "VXUS"])
    assert panel["funds"] == ["VOO", "BND", "VXUS"] and panel["kinds"] == ["us_equity", "bond", "intl_equity"]
    # ช่วงสอบเทียบเริ่มหลังกองที่ประวัติสั้นสุด (VXUS ยืดด้วย VEU ตั้งแต่ 2007-03 → ผลตอบแทนแรก 2007-04)
    assert panel["stats"]["pool_range"][0] >= "2007-03"
    r = engine.run_chunk(panel, engine.Config(P=100, T=60, horizons=(60,), seed=1, arms=("EQ", "ERC", "BLEND"), withholding_pct=0.15))
    assert all(np.isfinite(x["V"]).all() for x in r["H"][60]["arms"].values())


def test_fetch_raw_skips_a_broken_extra_but_never_a_broken_core_fund(monkeypatch, real_fetch_raw):
    from data.fetcher import PriceDataUnavailableError

    real = _synthetic_raw(seed=5, extras=("BND", "VXUS"))

    def fake_prices(tickers, years):
        for t in tickers:
            if t == "VXUS":
                raise PriceDataUnavailableError("Yahoo 404 (จำลอง)")
        return real.daily[list(dict.fromkeys(tickers))]

    monkeypatch.setattr(sim_data, "fetch_total_return_history", fake_prices)
    monkeypatch.setattr(sim_data, "_fetch_fred", lambda sid: real.fred[sid])
    monkeypatch.setattr(sim_data, "fetch_dividends", lambda ts: {t: real.dividends.get(t, pd.Series(dtype=float)) for t in ts})
    monkeypatch.setattr(sim_data, "extra_tickers", lambda: ["BND", "VXUS"])
    raw = real_fetch_raw(FIVE)
    assert "BND" in raw.daily.columns and "VXUS" not in raw.daily.columns
    assert any("VXUS" in n and "ข้าม" in n for n in raw.notes), "ข้ามกองเสริมต้องบอกใน notes ไม่เงียบ"

    def core_down(tickers, years):
        if "VOO" in tickers:
            raise PriceDataUnavailableError("Yahoo ล่ม (จำลอง)")
        return fake_prices(tickers, years)

    monkeypatch.setattr(sim_data, "fetch_total_return_history", core_down)
    with pytest.raises(sim_data.SimulationDataError, match="ดึงราคา"):
        real_fetch_raw(FIVE)


def test_what_if_universe_validates_inputs(monkeypatch, tmp_path):
    monkeypatch.setattr(sim_data, "DATA_DIR", tmp_path)
    sim_data.save_raw(_synthetic_raw(seed=6), tmp_path)
    kw = dict(budget_thb=5000.0, paths=200, worlds=("rw",), horizons=(60,))
    with pytest.raises(ValueError, match="2 กอง"):
        service.simulate_universe({"VOO": 1.0}, **kw)
    with pytest.raises(ValueError, match="ZZZZ"):
        service.simulate_universe({"VOO": 1.0, "ZZZZ": 1.0}, **kw)
    res = service.simulate_universe({"VOO": 0.4, "BND": 0.6}, **kw)
    assert res["universe"]["tickers"] == ["BND", "VOO"] and res["universe"]["kinds"] == ["bond", "us_equity"]
    s = res["worlds"]["rw"]["horizons"]["60"]["strategies"]
    assert {"ERC", "BLEND", "1/N", "สัดส่วนที่กำหนด"} <= set(s)
    # พันธบัตร 60% ต้องนิ่งกว่าหุ้นล้วนเทียบ 1/N ที่มีหุ้นครึ่งหนึ่ง — ตรวจทิศทางของความเสี่ยง ไม่ใช่ตัวเลข
    assert s["สัดส่วนที่กำหนด"]["dd_med"] <= s["1/N"]["dd_med"] + 3.0


# ---------------------------------------------------------------- ความแม่นยำของโมเดล (validate)
def _calib(panel, **kw):
    from simulation import validate

    return validate.calibration_report(panel, paths=400, seed=3, **kw)


def test_calibration_report_shape_and_determinism():
    panel = _panel(seed=11, hr=215)
    a, b = _calib(panel), _calib(panel)
    assert a == b
    names = [c["name"] for c in a["checks"]]
    assert sum(n.startswith("ความผันผวนต่อปี") for n in names) == 5
    assert any("หางหนา" in n for n in names) and any("MaxDD" in n for n in names) and any("12 เดือนที่แย่สุด" in n for n in names)
    assert all(c["status"] for c in a["checks"]) and a["caveat"]
    assert set(a["flags_loose"]) <= set(names) and a["verdict"]


def test_calibration_flags_a_model_that_is_looser_than_history():
    """ประวัติผันผวนสูงกว่าที่โมเดลสร้างมาก → ต้องถูกจับว่า "หลวมกว่าอดีต" (ความเสี่ยงที่แสดงจะต่ำเกินจริง)."""
    panel = _panel(seed=11, hr=215)
    n = len(panel["funds"])
    wild = {**panel, "chron": {**panel["chron"], "X": panel["chron"]["X"].copy()}}
    wild["chron"]["X"][:, :n] *= 4.0  # ประวัติ "จริง" ผันผวนกว่าโมเดล 4 เท่า
    rep = _calib(wild)
    assert any("ความผันผวนต่อปี" in f for f in rep["flags_loose"]), rep["flags_loose"]
    assert rep["verdict"] != "ok"


def test_percentile_and_drawdown_helpers():
    from simulation import validate

    assert validate._percentile_of(5.0, np.arange(10.0)) == 50.0
    flat = np.zeros((1, 24))
    assert validate._max_drawdown(flat)[0] == pytest.approx(0.0)
    crash = np.array([[0.1, 0.1, -0.5, 0.0, 0.2]])
    assert validate._max_drawdown(crash)[0] == pytest.approx(1 - np.exp(-0.5))
    assert validate._worst_12m(np.full((1, 12), -0.01))[0] == pytest.approx(-0.12)


def test_calibration_lines_warn_only_when_loose_and_always_count():
    ok = {"checks": [{"name": "a", "status": "ok"}, {"name": "ความผันผวนเป็นกลุ่ม x", "status": "ok", "value": -0.09, "model": 0.1, "history": 0.2}],
          "flags_loose": []}
    text = "\n".join(service.calibration_lines(ok))
    assert "ผ่านการตรวจ 2/2 ข้อ" in text and "⚠️" not in text and "อ่อนกว่าอดีต" in text
    bad = {"checks": [{"name": "MaxDD", "status": "โมเดลหลวมกว่าอดีต"}], "flags_loose": ["MaxDD"]}
    assert "⚠️ โมเดลหลวมกว่าประวัติจริง" in "\n".join(service.calibration_lines(bad))
    assert service.calibration_lines(None) == []
