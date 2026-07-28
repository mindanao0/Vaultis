# -*- coding: utf-8 -*-
"""scheduler ต้องไม่ตายเพราะไม่มี Discord webhook.

เดิม ``run_scheduler()`` raise ValueError ตอนไม่มี webhook แล้วถูก ``except Exception``
ของตัวเองจับ → ฟังก์ชันคืนค่า → process จบด้วย exit 0 ทันที
พอรันใน container ที่ตั้ง ``restart: unless-stopped`` มันจะเกิด-ตาย-เกิดใหม่เป็นวงไม่รู้จบ
งานที่ไม่ต้องพึ่ง Discord (ตรวจ price alert) ยังมีประโยชน์และต้องเดินต่อได้
"""

from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest
import schedule as schedule_lib

import main as scheduler_main

_BASE_CONFIG = {
    "dca": {"day_of_month": 1, "monthly_budget_thb": 5000.0},
    "notifications": {
        "discord_webhook_url": "",
        "weekly_summary": True,
        "dca_reminder": True,
        "rsi_alert": True,
    },
}


@pytest.fixture(autouse=True)
def _clean_schedule():
    schedule_lib.clear()
    yield
    schedule_lib.clear()


def _run(monkeypatch, webhook: str):
    """รัน run_scheduler หนึ่งรอบแล้วหยุด — คืนรายการงานที่ถูกลงทะเบียน"""
    cfg = deepcopy(_BASE_CONFIG)
    cfg["notifications"]["discord_webhook_url"] = webhook
    monkeypatch.setattr(scheduler_main, "load_config", lambda: cfg)

    def _stop(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(scheduler_main.time, "sleep", _stop)
    scheduler_main.run_scheduler()
    return [j.job_func for j in schedule_lib.jobs]


def _names(jobs) -> set[str]:
    out = set()
    for j in jobs:
        fn = getattr(j, "func", j)
        out.add(getattr(fn, "__name__", str(fn)))
    return out


class TestNoWebhook:
    def test_does_not_exit_early(self, monkeypatch):
        """ต้องเดินถึง loop จริง (เห็นได้จากการที่ time.sleep ถูกเรียก)"""
        jobs = _run(monkeypatch, "")
        assert jobs, "ไม่มีงานถูกลงทะเบียนเลย = ออกก่อนถึง loop"

    def test_price_alert_still_scheduled(self, monkeypatch):
        """งานที่ไม่ต้องใช้ Discord ต้องยังทำงาน"""
        assert "check_alerts" in _names(_run(monkeypatch, ""))

    def test_discord_jobs_are_skipped(self, monkeypatch):
        names = _names(_run(monkeypatch, ""))
        for job in (
            "run_monthly_ai_advisor_if_first_day",
            "check_and_send_dca_reminder",
            "generate_weekly_report_and_notify",
            "generate_daily_technical_alerts",
        ):
            assert job not in names, f"{job} ไม่ควรถูกตั้งเวลาเมื่อไม่มี webhook"

    def test_only_price_alert_jobs_registered(self, monkeypatch):
        assert _names(_run(monkeypatch, "")) == {"check_alerts"}


class TestWithWebhook:
    def test_all_jobs_registered(self, monkeypatch):
        names = _names(_run(monkeypatch, "https://discord.example/webhook"))
        assert {
            "run_monthly_ai_advisor_if_first_day",
            "check_and_send_dca_reminder",
            "generate_weekly_report_and_notify",
            "generate_daily_technical_alerts",
            "check_alerts",
        } <= names

    def test_notification_toggles_respected(self, monkeypatch):
        cfg = deepcopy(_BASE_CONFIG)
        cfg["notifications"]["discord_webhook_url"] = "https://discord.example/webhook"
        cfg["notifications"]["weekly_summary"] = False
        cfg["notifications"]["dca_reminder"] = False
        cfg["notifications"]["rsi_alert"] = False
        monkeypatch.setattr(scheduler_main, "load_config", lambda: cfg)

        def _stop(_seconds):
            raise KeyboardInterrupt

        monkeypatch.setattr(scheduler_main.time, "sleep", _stop)
        scheduler_main.run_scheduler()

        names = _names([j.job_func for j in schedule_lib.jobs])
        assert "check_and_send_dca_reminder" not in names
        assert "generate_weekly_report_and_notify" not in names
        assert "generate_daily_technical_alerts" not in names
        # monthly advisor ไม่มี toggle แยก — ผูกกับ webhook อย่างเดียว
        assert "run_monthly_ai_advisor_if_first_day" in names
        assert "check_alerts" in names
