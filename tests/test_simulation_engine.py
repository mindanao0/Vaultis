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
