# -*- coding: utf-8 -*-
"""simulation เป็นงานหลัก: ต่อครบทุกทาง (scheduler job · API · ข้อความ Discord · dashboard) — ทั้งหมดออฟไลน์.

กติกาที่ล็อก: ถึงเวลาจริงเท่านั้นถึงดึงเน็ต (ไม่ยิงทุกรอบ) · ล้มเหลว = ok=False + ข้อมูล/ผลเดิมยังอยู่ ไม่ปลอมผล ·
ไม่มีผล/เก่า = ข้อความบอกตรง ๆ · คำขอซ้อนกัน = 409 · น้ำหนักของกองที่ไม่อยู่ในข้อมูลถูกปฏิเสธ ไม่ถูกทิ้งเงียบ ๆ
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

from jobs import simulation_refresh as job
from sim_synth import synthetic_raw
from simulation import data as sim_data
from simulation import service

TZ = timezone(timedelta(hours=7))


@pytest.fixture
def sim_env(tmp_path, monkeypatch, fake_erc):
    """โฟลเดอร์ข้อมูลชั่วคราว + ดึงข้อมูลสังเคราะห์แทนเน็ต + เส้นทางน้อยให้เทสต์เร็ว."""
    monkeypatch.setattr(sim_data, "DATA_DIR", tmp_path)
    monkeypatch.setattr(service, "DEFAULT_PATHS", 200)
    calls = {"fetch": 0, "fail": False}

    def _fetch(tickers):
        calls["fetch"] += 1
        if calls["fail"]:
            raise sim_data.SimulationDataError("FRED ล่ม (จำลอง)")
        raw = synthetic_raw(tickers=list(tickers), seed=1)
        raw.fetched_at = datetime.now(TZ).isoformat(timespec="seconds")  # "ดึงเมื่อตอนนี้" ไม่ผูกกับนาฬิกาวันที่เขียนเทสต์
        return raw

    monkeypatch.setattr(sim_data, "fetch_raw", _fetch)
    monkeypatch.setattr(job, "_workers", lambda: 1)
    return calls


def _now(days=0):
    return datetime.now(TZ) + timedelta(days=days)


# ---------------------------------------------------------------- scheduler job
def test_first_run_fetches_then_runs_the_plan_and_saves_it(sim_env, tmp_path):
    out = job.run_simulation_refresh()
    assert out["ok"] and out["fetched"] and sim_env["fetch"] == 1
    assert any("ดึงข้อมูลสด" in s for s in out["steps"]) and any("รันแผน" in s for s in out["steps"])
    last = service.load_last_plan(tmp_path)
    assert last and last["plan"]["method"] == "blend" and last["plan"]["plan_strategy"] == "BLEND"
    assert last["inputs"]["method"] == "blend" and last["limitations"], "ผลต้องพกข้อจำกัดของโมเดลไปด้วยเสมอ"
    assert set(last["worlds"]) == {"rw", "mom", "boot"}


def test_second_run_does_nothing_until_it_is_due(sim_env):
    job.run_simulation_refresh()
    again = job.run_simulation_refresh()
    assert again["ok"] and not again["fetched"] and sim_env["fetch"] == 1, "ข้อมูลยังใหม่ ต้องไม่ยิงเน็ตซ้ำ"
    assert any("ไม่ต้องรันใหม่" in s for s in again["steps"])


def test_stale_data_is_refetched(sim_env):
    job.run_simulation_refresh()
    later = job.run_simulation_refresh(now=_now(days=sim_data.STALE_AFTER_DAYS + 2))
    assert later["ok"] and later["fetched"] and sim_env["fetch"] == 2


def test_changed_plan_reruns_without_fetching(sim_env, monkeypatch):
    job.run_simulation_refresh()
    import portfolio.targets as targets

    monkeypatch.setattr(targets, "get_weighting_method", lambda: "erc")
    out = job.run_simulation_refresh()
    assert out["ok"] and not out["fetched"] and any("ERC" in s for s in out["steps"]), out
    assert service.load_last_plan()["plan"]["plan_strategy"] == "ERC"


def test_fetch_failure_is_loud_and_keeps_old_data(sim_env):
    job.run_simulation_refresh()
    sim_env["fail"] = True
    out = job.run_simulation_refresh(now=_now(days=30))
    assert out["ok"] is False and "FRED ล่ม" in out["error"]
    assert sim_data.data_status()["exists"], "ข้อมูลเดิมต้องยังอยู่ (แค่ถูกบอกว่าเก่า)"
    assert service.load_last_plan() is not None


def test_no_data_and_fetch_failure_does_not_invent_a_result(sim_env):
    sim_env["fail"] = True
    out = job.run_simulation_refresh()
    assert out["ok"] is False and service.load_last_plan() is None


def test_force_always_refetches(sim_env):
    job.run_simulation_refresh()
    assert job.run_simulation_refresh(force=True)["fetched"] and sim_env["fetch"] == 2


# ---------------------------------------------------------------- Discord
def test_discord_context_carries_the_simulation_summary(sim_env):
    from analysis import ai_advisor

    before = "\n".join(ai_advisor._base_context_lines([]))
    assert "ยังไม่มีผล" in before and "ไม่ได้แปลว่าแผนผ่านการจำลองแล้ว" in before, "ไม่มีผล = ต้องบอก ห้ามเงียบ"
    job.run_simulation_refresh()
    after = "\n".join(ai_advisor._base_context_lines([]))
    assert "Simulation แผน (BLEND)" in after and "10 ปี" in after and "20 ปี" in after and "ไม่ใช่พยากรณ์" in after


# ---------------------------------------------------------------- API
@pytest.fixture
def client(monkeypatch):
    from backend.main import app  # import ก่อน: ตอน import มันอ่าน .env (dotenv) ซึ่งอาจตั้ง VAULTIS_API_KEY กลับมา

    monkeypatch.delenv("VAULTIS_API_KEY", raising=False)
    return TestClient(app)


def test_api_status_and_last_when_nothing_has_run(sim_env, client):
    st = client.get("/api/simulation/status").json()["data"]
    assert st["data"]["exists"] is False and st["last_plan"] is None and st["limitations"]
    r = client.get("/api/simulation/last")
    assert r.status_code == 404 and "ยังไม่มีผล" in r.json()["detail"]


def test_api_run_without_data_is_503_not_a_made_up_answer(sim_env, client):
    r = client.post("/api/simulation/run", json={"paths": 100})
    assert r.status_code == 503 and "ยังไม่มีข้อมูล" in r.json()["detail"]


def test_api_run_whatif_and_last_after_data_exists(sim_env, client):
    job.run_simulation_refresh()
    run = client.post("/api/simulation/run", json={"paths": 100, "save": False})
    assert run.status_code == 200
    body = run.json()["data"]
    assert body["plan"]["plan_strategy"] == "BLEND" and any("10 ปี" in ln for ln in body["summary_lines"])
    what = client.post("/api/simulation/whatif", json={"weights": {"VOO": 0.5, "GLDM": 0.5}, "paths": 100})
    assert what.status_code == 200
    assert "สัดส่วนที่กำหนด" in what.json()["data"]["worlds"]["rw"]["horizons"]["240"]["strategies"]
    assert client.get("/api/simulation/last").status_code == 200
    assert client.get("/api/simulation/status").json()["data"]["last_plan"]["age_days"] < 1


def test_api_rejects_unknown_ticker_and_bad_paths(sim_env, client):
    job.run_simulation_refresh()
    bad = client.post("/api/simulation/whatif", json={"weights": {"ZZZZ": 1.0}, "paths": 100})
    assert bad.status_code == 400 and "ZZZZ" in bad.json()["detail"], "กองที่ไม่อยู่ในข้อมูลต้องถูกปฏิเสธ ไม่ถูกทิ้งเงียบ ๆ"
    assert client.post("/api/simulation/run", json={"paths": 5}).status_code == 422
    assert client.post("/api/simulation/run", json={"paths": 10_000_000}).status_code == 422


def test_api_overlapping_runs_get_409(sim_env, client):
    from backend.routers import simulation as router

    job.run_simulation_refresh()
    assert router._RUN_LOCK.acquire(blocking=False)
    try:
        r = client.post("/api/simulation/run", json={"paths": 100})
        assert r.status_code == 409
    finally:
        router._RUN_LOCK.release()


def test_api_requires_the_key_when_one_is_configured(sim_env, monkeypatch):
    from backend.main import app

    monkeypatch.setenv("VAULTIS_API_KEY", "secret")
    c = TestClient(app)
    assert c.get("/api/simulation/status").status_code in (401, 403)
    assert c.get("/api/simulation/status", headers={"X-API-Key": "secret"}).status_code == 200


# ---------------------------------------------------------------- dashboard
class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeSt:
    """แทน ``streamlit`` ในโมดูลแผง — จดสิ่งที่แสดง · ปุ่มไม่ถูกกด."""

    def __init__(self):
        self.said: list[str] = []
        self.frames: list = []

    def _say(self, *a, **k):
        if a:
            self.said.append(str(a[0]))

    info = warning = error = success = caption = markdown = _say

    def dataframe(self, df, **k):
        self.frames.append(df)

    def tabs(self, labels):
        return [_Ctx() for _ in labels]

    def columns(self, n):
        return [_Ctx() for _ in range(n if isinstance(n, int) else len(n))]

    def expander(self, *a, **k):
        return _Ctx()

    def button(self, *a, **k):
        return False

    def spinner(self, *a, **k):
        return _Ctx()


def test_dashboard_panel_says_so_when_there_is_no_data_or_result(sim_env, monkeypatch):
    import dashboard.simulation_panel as panel

    fake = _FakeSt()
    monkeypatch.setattr(panel, "st", fake)
    panel.render_simulation_panel()
    text = "\n".join(fake.said)
    assert "ยังไม่มีข้อมูล simulation" in text and "ไม่ได้แปลว่าแผนผ่านการจำลองแล้ว" in text


def test_dashboard_panel_shows_tables_limitations_and_plan_label(sim_env, monkeypatch):
    import dashboard.simulation_panel as panel

    job.run_simulation_refresh()
    fake = _FakeSt()
    monkeypatch.setattr(panel, "st", fake)
    panel.render_simulation_panel()
    text = "\n".join(fake.said)
    assert "BLEND" in text and "ข้อมูลถึง" in text and "ผลต่างระหว่างสูตรที่เล็กกว่า" in text
    frames = fake.frames
    assert len(frames) == 9, "3 โลก × 3 ขอบฟ้า (5, 10, 20 ปี) = 9 ตาราง"
    assert "กลยุทธ์" in frames[0].columns and set(frames[0]["กลยุทธ์"]) >= {"ERC", "BLEND", "1/N"}


def test_nav_has_the_simulation_page_and_it_is_dispatched():
    app = pytest.importorskip("dashboard.app")
    assert "Simulation" in app.NAV_ITEMS
    import inspect

    src = inspect.getsource(app)
    assert 'page == "Simulation"' in src and "render_simulation_page" in src
