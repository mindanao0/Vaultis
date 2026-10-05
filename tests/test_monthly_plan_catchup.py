# -*- coding: utf-8 -*-
"""แผน DCA ต้นเดือนต้องส่งย้อนหลังได้เมื่อคอมปิดอยู่ตอน 08:00 วันที่ 1 — และไม่ส่งซ้ำ.

เดิม scheduler เช็ค "วันนี้วันที่ 1 ไหม" วันละครั้งตอน 08:00 ⇒ เครื่องปิดตอนนั้น =
ไม่ได้แผนทั้งเดือนโดยไม่มีอะไรฟ้อง  ส่งซ้ำก็เสียเงินจริง (แต่ละครั้งอาจเรียก Claude)
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest

import main


@pytest.fixture
def env(monkeypatch, tmp_path):
    state_path = tmp_path / "scheduler_state.json"
    monkeypatch.setattr(main, "SCHEDULER_STATE_PATH", state_path)
    monkeypatch.setattr(main, "_monthly_plan_sent_in_process", set())
    sends: list[datetime] = []
    box = {"now": datetime(2026, 10, 1, 8, 0, tzinfo=main.BANGKOK_TZ), "ok": True}

    def fake_send() -> bool:
        sends.append(box["now"])
        return box["ok"]

    monkeypatch.setattr(main, "_now_bangkok", lambda: box["now"])
    monkeypatch.setattr(main, "generate_monthly_ai_advisor_and_notify", fake_send)

    def set_state(sent_month: str, **extra) -> None:
        state_path.write_text(json.dumps({"monthly_plan": {"sent_month": sent_month, **extra}}))

    def read_state() -> dict:
        return json.loads(state_path.read_text())["monthly_plan"]

    def at(y, m, d, h=8, minute=0):
        box["now"] = datetime(y, m, d, h, minute, tzinfo=main.BANGKOK_TZ)

    return type("Env", (), {"sends": sends, "box": box, "set_state": staticmethod(set_state),
                            "read_state": staticmethod(read_state), "at": staticmethod(at),
                            "path": state_path})


def test_sends_on_time(env):
    env.set_state("2026-09")
    assert main.run_monthly_plan_if_due() == "sent"
    assert len(env.sends) == 1
    assert env.read_state()["sent_month"] == "2026-10"


def test_not_before_eight_on_day_one(env):
    env.set_state("2026-09")
    env.at(2026, 10, 1, 7, 59)
    assert main.run_monthly_plan_if_due() == "not_yet"
    assert env.sends == []


@pytest.mark.parametrize("day,hour", [(1, 14), (3, 10), (31, 23)])
def test_catches_up_when_machine_was_off(env, day, hour):
    """คอมเปิดทีหลัง 08:00 วันที่ 1 (หรือหลายวันถัดมา) ต้องได้แผนของเดือนนั้น."""
    env.set_state("2026-09")
    env.at(2026, 10, day, hour)
    assert main.run_monthly_plan_if_due() == "sent"
    assert len(env.sends) == 1


def test_never_sends_twice_in_a_month(env):
    env.set_state("2026-09")
    main.run_monthly_plan_if_due()
    for hour in (9, 10, 11):
        env.at(2026, 10, 1, hour)
        assert main.run_monthly_plan_if_due() == "already_sent"
    assert len(env.sends) == 1


def test_state_survives_process_restart(env, monkeypatch):
    """container restart = หน่วยความจำหาย — ไฟล์สถานะต้องกันส่งซ้ำเอง."""
    env.set_state("2026-09")
    main.run_monthly_plan_if_due()
    monkeypatch.setattr(main, "_monthly_plan_sent_in_process", set())
    env.at(2026, 10, 5, 9)
    assert main.run_monthly_plan_if_due() == "already_sent"
    assert len(env.sends) == 1


def test_next_month_sends_again(env):
    env.set_state("2026-09")
    main.run_monthly_plan_if_due()
    env.at(2026, 11, 1, 8)
    assert main.run_monthly_plan_if_due() == "sent"
    assert len(env.sends) == 2


def test_first_install_mid_month_does_not_fire(env):
    """ไม่มีไฟล์สถานะ = ติดตั้งใหม่ ⇒ ห้ามยิงแผนเดือนที่ผ่านไปครึ่งทางตอน deploy."""
    env.at(2026, 9, 28, 14)
    assert main.run_monthly_plan_if_due() == "seeded"
    assert env.sends == []
    env.at(2026, 10, 1, 8)
    assert main.run_monthly_plan_if_due() == "sent"


def test_failure_does_not_mark_sent_and_retries(env):
    env.set_state("2026-09")
    env.box["ok"] = False
    assert main.run_monthly_plan_if_due() == "failed"
    assert env.read_state()["sent_month"] == "2026-09"
    env.box["ok"] = True
    env.at(2026, 10, 1, 9)
    assert main.run_monthly_plan_if_due() == "sent"
    assert len(env.sends) == 2


def test_gives_up_after_max_failures(env):
    """แต่ละครั้งที่ล้มอาจจ่ายค่า AI ไปแล้ว — ห้ามลองทุกชั่วโมงไม่รู้จบ."""
    env.set_state("2026-09")
    env.box["ok"] = False
    for hour in range(8, 8 + main.MONTHLY_PLAN_MAX_FAILED_ATTEMPTS):
        env.at(2026, 10, 1, hour)
        assert main.run_monthly_plan_if_due() == "failed"
    env.at(2026, 10, 1, 20)
    assert main.run_monthly_plan_if_due() == "gave_up"
    assert len(env.sends) == main.MONTHLY_PLAN_MAX_FAILED_ATTEMPTS
    # เดือนใหม่นับใหม่
    env.box["ok"] = True
    env.at(2026, 11, 1, 8)
    assert main.run_monthly_plan_if_due() == "sent"


def test_corrupt_state_file_does_not_send(env):
    """อ่านไม่ออก = ไม่รู้ว่าส่งแล้วหรือยัง → ไม่ส่ง (เดาว่า 'ยัง' = ส่งซ้ำทุกชั่วโมง)."""
    env.path.write_text("{not json")
    assert main.run_monthly_plan_if_due() == "state_unreadable"
    assert env.sends == []


def test_scheduler_registers_catchup_and_checks_at_startup(env, monkeypatch):
    import schedule as schedule_lib

    env.set_state("2026-09")
    cfg = {"dca": {"day_of_month": 1, "monthly_budget_thb": 5000.0},
           "notifications": {"discord_webhook_url": "https://discord.example/webhook",
                             "weekly_summary": False, "dca_reminder": False, "rsi_alert": False}}
    monkeypatch.setattr(main, "load_config", lambda: cfg)
    # งานตอนเริ่ม scheduler ที่ยิงเน็ตจริงเมื่อมี webhook (แผน DAR/SELECT ดึงราคา+FX, simulation ดึงข้อมูลสด) — เทสต์นี้ดูแค่การลงทะเบียนเวลา
    # และ sleep ที่ถูกสตับให้ KeyboardInterrupt จะหยุดมันกลางทาง ก่อนงานราคาถูกลงทะเบียน (ทำให้ modes ว่าง)
    for _name in ("run_dar_plan_if_due", "run_select_plan_if_due", "run_simulation_refresh", "run_predict_daily"):
        monkeypatch.setattr(main, _name, lambda *a, **k: None)

    def _stop(_s):
        raise KeyboardInterrupt

    monkeypatch.setattr(main.time, "sleep", _stop)
    schedule_lib.clear()
    try:
        main.run_scheduler()
        units = sorted(j.unit for j in schedule_lib.jobs
                       if getattr(j.job_func, "__name__", "") == "run_monthly_plan_if_due")
    finally:
        schedule_lib.clear()
    assert units == ["days", "hours"]
    assert len(env.sends) == 1, "เปิดเครื่องหลัง 08:00 วันที่ 1 ต้องส่งทันทีตอนเริ่ม ไม่ต้องรอชั่วโมงถัดไป"
