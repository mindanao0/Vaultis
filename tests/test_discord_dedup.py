# -*- coding: utf-8 -*-
"""ข้อความ Discord ต้องไม่ซ้ำและไม่ชวนเข้าใจผิด (ผู้ใช้แจ้ง 2026-09-30).

อาการก่อนแก้ — สรุปราคา "Daily Price Check" มาจาก 3 ที่ที่ไม่รู้จักกัน:

    09:00 ทุกวัน   Docker scheduler → check_alerts() ส่งสรุปทุกครั้ง
    21:00 ทุกวัน   ตัวเดียวกัน
    ~02:00 จ.–ศ.   GitHub Actions → jobs/daily_check.py

≈ 19 ใบ/สัปดาห์ ทั้งที่มีราคาปิดใหม่แค่ 5 วัน (อาทิตย์/จันทร์ 09:00 = ราคาวันศุกร์ซ้ำ)
และวัน DCA = วันที่ 1 ⇒ reminder วันที่ 30 กับแผนต้นเดือนวันที่ 1 แนบแผนเดียวกันสองใบ
ส่วนบรรทัดแผนพิมพ์ ``[1.07× ของเป้า 35%]`` ทั้งที่ VOO ได้จริง 34% ต่ำกว่าเป้า

ห้ามยิงของจริง: ทุก ``send_discord_webhook`` ถูกสตับ ไฟล์สถานะกับคลัง alert อยู่ใน tmp
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, time as dtime
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd
import pytest
import schedule as schedule_lib

import alerts.price_alert as pa
import main as scheduler_main
from analysis import ai_advisor
from analysis.ai_advisor import format_allocation_line

WEBHOOK = "https://discord.invalid/webhook"
TRACKED = list(pa.DAILY_CHECK_TICKERS)
SUMMARY_TITLE = "Daily Price Check"


def _snapshots(day: str | None = "2026-09-29", skip: tuple[str, ...] = ()) -> dict:
    return {
        t: {"latest_price": 100.0 + i, "previous_close": 99.0 + i, "as_of": day}
        for i, t in enumerate(TRACKED)
        if t not in skip
    }


def _alert(ticker: str, price: float = 1.0, alert_type: str = "below") -> dict:
    return {"id": f"id-{ticker}", "ticker": ticker, "alert_type": alert_type, "price": price,
            "note": "", "triggered": False, "created_at": "2026-09-01T00:00:00",
            "triggered_at": None, "triggered_price": None}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """คลัง alert + ไฟล์สถานะใน tmp · มี webhook (ปลอม) · ราคาเป็นสตับ · นับทุกข้อความที่ส่ง."""
    alerts_path = tmp_path / "price_alerts.json"
    state_path = tmp_path / "scheduler_state.json"
    monkeypatch.setattr(pa, "ALERTS_PATH", alerts_path)
    monkeypatch.setattr(scheduler_main, "SCHEDULER_STATE_PATH", state_path)
    monkeypatch.setattr(scheduler_main, "_monthly_plan_sent_in_process", set())

    cfg = {"notifications": {"discord_webhook_url": WEBHOOK}}
    monkeypatch.setattr(pa, "load_config", lambda: cfg)
    monkeypatch.setattr(scheduler_main, "load_config", lambda: cfg)

    ns = SimpleNamespace(
        sent=[], alerts_path=alerts_path, state_path=state_path,
        snapshots=_snapshots(), discord_ok=True,
    )

    def _send(**kw):
        ns.sent.append(kw)
        return {"success": True} if ns.discord_ok else {"success": False, "error": "HTTP 500"}

    monkeypatch.setattr(pa, "send_discord_webhook", _send)
    monkeypatch.setattr(scheduler_main, "send_discord_webhook", _send)
    monkeypatch.setattr(pa, "get_price_snapshots", lambda tickers: ns.snapshots)
    return ns


def _titles(sent: list[dict]) -> list[str]:
    return [kw.get("title") for kw in sent]


def _summaries(sent: list[dict]) -> int:
    return _titles(sent).count(SUMMARY_TITLE)


def _write_alerts(path: Path, alerts: list[dict]) -> None:
    path.write_text(json.dumps({"alerts": alerts}, ensure_ascii=False), encoding="utf-8")


def _morning(env) -> dict:
    return scheduler_main.run_price_alert_job(daily_summary=scheduler_main.DAILY_SUMMARY_NEW_CLOSE)


def _evening(env) -> dict:
    return scheduler_main.run_price_alert_job(daily_summary=scheduler_main.DAILY_SUMMARY_OFF)


# ---------------------------------------------------------------- check_alerts()
class TestCheckAlertsFlag:
    def test_default_still_sends_the_summary(self, env):
        """ทางเข้าที่ผู้ใช้สั่งเอง (``--job price_alert``/API/แดชบอร์ด) ทำงานเหมือนเดิม."""
        pa.check_alerts()
        assert _summaries(env.sent) == 1

    def test_flag_off_sends_no_summary_but_reports_bar_date(self, env):
        result = pa.check_alerts(send_daily_summary=False)
        assert env.sent == []
        assert result["latest_bar_date"] == "2026-09-29"
        assert result["unpriced_tickers"] == []
        assert result["daily_discord_result"].get("reason"), "ต้องบอกว่าตั้งใจไม่ส่ง ไม่ใช่ไม่มี webhook"

    def test_unpriced_ticker_is_listed(self, env):
        env.snapshots = _snapshots(skip=("GLDM",))
        assert pa.check_alerts(send_daily_summary=False)["unpriced_tickers"] == ["GLDM"]

    def test_triggered_alert_is_still_sent_when_summary_is_off(self, env):
        _write_alerts(env.alerts_path, [_alert("VOO", price=500.0)])  # VOO 100 <= 500 → trigger
        pa.check_alerts(send_daily_summary=False)
        assert _titles(env.sent) == ["Price Alert"]


# ---------------------------------------------------------------- 09:00 — ใบเดียวต่อราคาปิด
class TestMorningSummaryOncePerClose:
    def test_same_close_is_not_sent_twice(self, env, capsys):
        _morning(env)
        _morning(env)
        assert _summaries(env.sent) == 1
        assert "สรุปไปแล้ว" in capsys.readouterr().out, "log ต้องบอกว่าทำไมไม่ส่ง"

    def test_weekend_sends_friday_once_and_tuesday_again(self, env):
        env.snapshots = _snapshots("2026-09-25")  # เสาร์ 09:00 = ราคาปิดศุกร์
        _morning(env)
        _morning(env)  # อาทิตย์
        _morning(env)  # จันทร์ (ตลาดยังไม่เปิดรอบใหม่)
        env.snapshots = _snapshots("2026-09-28")  # อังคาร 09:00 = ราคาปิดจันทร์
        _morning(env)
        assert _summaries(env.sent) == 2

    def test_restart_does_not_resend(self, env):
        """คอนเทนเนอร์เริ่มใหม่ทุกครั้งที่เปิดเครื่อง — จำต้องอยู่ในไฟล์ ไม่ใช่หน่วยความจำ."""
        _morning(env)
        saved = json.loads(env.state_path.read_text(encoding="utf-8"))
        assert saved[scheduler_main.PRICE_SUMMARY_STATE_KEY]["last_bar_date"] == "2026-09-29"
        _morning(env)
        assert _summaries(env.sent) == 1

    def test_missing_price_is_sent_even_on_the_same_close(self, env):
        _morning(env)
        env.snapshots = _snapshots(skip=("GLDM",))
        _morning(env)
        assert _summaries(env.sent) == 2, "ดึงราคาไม่ได้ต้องออกไปให้เห็น ห้ามถูกตัดเพราะ 'ซ้ำ'"

    def test_unchecked_alert_is_sent_even_on_the_same_close(self, env):
        _morning(env)
        _write_alerts(env.alerts_path, [_alert("AAPL")])  # ไม่มีราคา AAPL → ตรวจไม่ได้
        _morning(env)
        assert _summaries(env.sent) == 2

    def test_no_prices_at_all_is_always_sent(self, env):
        env.snapshots = {}
        _morning(env)
        _morning(env)
        assert _summaries(env.sent) == 2

    def test_failed_delivery_is_not_remembered(self, env):
        env.discord_ok = False
        _morning(env)
        env.discord_ok = True
        _morning(env)
        assert _summaries(env.sent) == 2, "ส่งไม่ออกแล้วจำว่าส่งแล้ว = สรุปวันนั้นหายไปเลย"

    def test_unreadable_state_sends_and_never_overwrites(self, env):
        env.state_path.write_text("{broken", encoding="utf-8")
        _morning(env)
        _morning(env)
        assert _summaries(env.sent) == 2
        assert env.state_path.read_text(encoding="utf-8") == "{broken", (
            "ไฟล์เดียวกันเก็บสถานะแผน DCA — เขียนทับแล้วแผน+ค่า AI อาจถูกส่งซ้ำ"
        )

    def test_monthly_plan_state_survives(self, env):
        env.state_path.write_text(json.dumps({"monthly_plan": {"sent_month": "2026-09"}}), encoding="utf-8")
        _morning(env)
        saved = json.loads(env.state_path.read_text(encoding="utf-8"))
        assert saved["monthly_plan"] == {"sent_month": "2026-09"}


class TestSharedStateFile:
    def test_summary_written_first_still_counts_as_first_install(self, env, monkeypatch):
        """ไฟล์ที่มีแค่ price_summary = แผนรายเดือนยังไม่เคยทำงาน ⇒ seed ไม่ใช่ส่งทันที."""
        env.state_path.write_text(
            json.dumps({"price_summary": {"last_bar_date": "2026-10-02"}}), encoding="utf-8"
        )
        monkeypatch.setattr(
            scheduler_main, "_now_bangkok",
            lambda: datetime(2026, 10, 5, 10, 0, tzinfo=scheduler_main.BANGKOK_TZ),
        )

        def _explode():
            raise AssertionError("ติดตั้งครั้งแรกกลางเดือนต้องไม่ส่งแผนของเดือนนี้")

        monkeypatch.setattr(scheduler_main, "generate_monthly_ai_advisor_and_notify", _explode)

        assert scheduler_main.run_monthly_plan_if_due() == "seeded"
        saved = json.loads(env.state_path.read_text(encoding="utf-8"))
        assert saved["price_summary"] == {"last_bar_date": "2026-10-02"}
        assert saved["monthly_plan"]["sent_month"] == "2026-10"


# ---------------------------------------------------------------- 21:00 — alert อย่างเดียว
class TestEveningRun:
    def test_nothing_to_report_sends_nothing(self, env):
        _evening(env)
        assert env.sent == []

    def test_unchecked_alert_still_reaches_discord(self, env):
        _write_alerts(env.alerts_path, [_alert("AAPL")])
        _evening(env)
        assert len(env.sent) == 1
        assert "ตรวจไม่ได้" in env.sent[0]["title"]
        assert "AAPL" in env.sent[0]["description"]

    def test_triggered_alert_is_sent_without_a_summary(self, env):
        _write_alerts(env.alerts_path, [_alert("VOO", price=500.0)])
        _evening(env)
        assert _titles(env.sent) == ["Price Alert"]

    def test_evening_does_not_touch_the_dedup_state(self, env):
        _evening(env)
        assert not env.state_path.exists()


# ---------------------------------------------------------------- การลงทะเบียนงาน
class TestSchedule:
    def test_morning_summarises_evening_only_checks(self, env, monkeypatch):
        schedule_lib.clear()
        cfg = {
            "dca": {"day_of_month": 1, "monthly_budget_thb": 5000.0},
            "notifications": {"discord_webhook_url": WEBHOOK, "weekly_summary": True,
                              "dca_reminder": True, "rsi_alert": True},
        }
        monkeypatch.setattr(scheduler_main, "load_config", lambda: cfg)
        monkeypatch.setattr(scheduler_main, "generate_monthly_ai_advisor_and_notify",
                            lambda: pytest.fail("เทสต์นี้ห้ามส่งแผนจริง"))
        # งานตอนเริ่ม scheduler ที่ยิงเน็ตจริงเมื่อมี webhook (แผน DAR/SELECT ดึงราคา+FX, simulation ดึงข้อมูลสด) — เทสต์นี้ดูแค่การลงทะเบียนเวลา
        # และ sleep ที่ถูกสตับให้ KeyboardInterrupt จะหยุดมันกลางทาง ก่อนงานราคาถูกลงทะเบียน (ทำให้ modes ว่าง)
        for _name in ("run_dar_plan_if_due", "run_select_plan_if_due", "run_simulation_refresh", "run_predict_daily"):
            monkeypatch.setattr(scheduler_main, _name, lambda *a, **k: None)

        def _stop(_seconds):
            raise KeyboardInterrupt

        monkeypatch.setattr(scheduler_main.time, "sleep", _stop)
        try:
            scheduler_main.run_scheduler()
            modes = {
                job.at_time: job.job_func.keywords.get("daily_summary")
                for job in schedule_lib.jobs
                if getattr(job.job_func.func, "__name__", "") == "run_price_alert_job"
            }
        finally:
            schedule_lib.clear()
        assert modes == {
            dtime(9, 0): scheduler_main.DAILY_SUMMARY_NEW_CLOSE,
            dtime(21, 0): scheduler_main.DAILY_SUMMARY_OFF,
        }

    def test_unknown_mode_is_rejected(self, env):
        with pytest.raises(ValueError):
            scheduler_main.run_price_alert_job(daily_summary="sometimes")


# ---------------------------------------------------------------- วันที่ของราคาในข้อความ
class TestMessageCarriesBarDate:
    def test_header_says_which_close_the_prices_are(self):
        msg = pa._build_daily_status_message(TRACKED, _snapshots("2026-09-25"), [], [])
        assert "25/09/2026" in msg

    def test_ticker_behind_the_others_carries_its_own_date(self):
        snaps = _snapshots("2026-09-29")
        snaps["GLDM"]["as_of"] = "2026-09-26"
        line = [ln for ln in pa._build_daily_status_message(TRACKED, snaps, [], []).splitlines()
                if ln.startswith("GLDM")][0]
        assert "26/09/2026" in line

    def test_snapshot_reports_the_bar_date(self, monkeypatch):
        frame = pd.DataFrame(
            {("VOO", "Close"): [700.0, 702.5]},
            index=pd.to_datetime(["2026-09-28", "2026-09-29"]),
        )
        frame.columns = pd.MultiIndex.from_tuples(frame.columns)
        monkeypatch.setattr(pa.yf, "download", lambda **kwargs: frame)
        assert pa.get_price_snapshots(["VOO"])["VOO"]["as_of"] == "2026-09-29"


# ---------------------------------------------------------------- DCA reminder
INCIDENT_ALLOCATION = {
    "VOO": {"amount_thb": 1700, "percent": 34.0, "target_percent": 35.0, "tilt": 1.07},
    "SCHD": {"amount_thb": 1300, "percent": 26.0, "target_percent": 25.0, "tilt": 1.14},
    "QQQM": {"amount_thb": 1100, "percent": 22.0, "target_percent": 20.0, "tilt": 1.14},
    "XLV": {"amount_thb": 500, "percent": 10.0, "target_percent": 10.0, "tilt": 1.18},
    "GLDM": {"amount_thb": 400, "percent": 8.0, "target_percent": 10.0, "tilt": 0.83},
}


@pytest.fixture
def reminder(monkeypatch):
    ns = SimpleNamespace(advice_calls=[], sent=[])

    def _advice(**kw):
        ns.advice_calls.append(kw)
        return {"allocation": INCIDENT_ALLOCATION, "unallocated_thb": 0.0, "no_data_tickers": []}

    monkeypatch.setattr(scheduler_main, "get_monthly_advice", _advice)
    monkeypatch.setattr(scheduler_main, "get_today_fx_rate_thb", lambda: 33.52)
    monkeypatch.setattr(scheduler_main, "send_dca_reminder",
                        lambda **kw: (ns.sent.append(kw), {"success": True})[1])

    def _at(day_of_month: int, now: datetime) -> None:
        cfg = {"dca": {"day_of_month": day_of_month, "monthly_budget_thb": 5000.0}}
        monkeypatch.setattr(scheduler_main, "load_config", lambda: cfg)
        monkeypatch.setattr(scheduler_main, "_now_bangkok",
                            lambda: now.replace(tzinfo=scheduler_main.BANGKOK_TZ))
        scheduler_main.check_and_send_dca_reminder(WEBHOOK)

    ns.at = _at
    return ns


class TestDcaReminder:
    def test_day_one_reminder_does_not_repeat_the_monthly_plan(self, reminder):
        reminder.at(1, datetime(2026, 9, 30, 8, 0))
        assert reminder.advice_calls == [], "แผนต้นเดือนส่งพรุ่งนี้ 08:00 อยู่แล้ว — ไม่ต้องคำนวณซ้ำ"
        assert len(reminder.sent) == 1
        plan = reminder.sent[0]["ai_advice"]
        assert "08:00" in plan
        assert "บาท" not in plan

    def test_other_dca_days_still_attach_the_plan(self, reminder):
        reminder.at(15, datetime(2026, 9, 14, 8, 0))
        plan = reminder.sent[0]["ai_advice"]
        assert "VOO: 1,700 บาท · 34% (เป้า 35%)" in plan
        assert "×" not in plan


class TestAllocationLine:
    def test_shows_actual_share_next_to_target(self):
        """VOO ตัวคูณ 1.07 แต่ได้จริง 34% < เป้า 35% — ต้องเห็นสองตัวเลขนี้ ไม่ใช่ 1.07×."""
        line = format_allocation_line("VOO", INCIDENT_ALLOCATION["VOO"])
        assert line == "VOO: 1,700 บาท · 34% (เป้า 35%)"

    def test_monthly_plan_message_uses_the_same_line(self):
        lines = ai_advisor._allocation_summary_lines(INCIDENT_ALLOCATION, 5000.0, 0.0, [])
        assert "• XLV: 500 บาท · 10% (เป้า 10%)" in lines
        assert not any("×" in ln for ln in lines)


# ---------------------------------------------------------------- CI
class TestCiDoesNotPostToDiscord:
    def test_every_discord_step_is_behind_the_switch(self):
        """Docker scheduler เป็นผู้ส่ง Discord — step ใน CI ที่ถือ webhook ต้องปิดโดยดีฟอลต์."""
        text = (_ROOT / ".github" / "workflows" / "scheduler.yml").read_text(encoding="utf-8")
        body = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
        steps = re.split(r"\n(?=\s*- (?:name|uses):)", body)
        discord_steps = [s for s in steps if "DISCORD_WEBHOOK_URL" in s]
        assert discord_steps, "หา step ที่ส่ง Discord ไม่เจอ — ตัวแยก step พัง"
        ungated = [
            re.search(r"name:\s*(.+)", s).group(1)
            for s in discord_steps
            if "vars.VAULTIS_CI_DISCORD_JOBS == '1'" not in s
        ]
        assert not ungated, f"step ที่ส่ง Discord โดยไม่ผ่านสวิตช์: {ungated}"
