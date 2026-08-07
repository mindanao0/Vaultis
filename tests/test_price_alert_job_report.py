# -*- coding: utf-8 -*-
"""งาน price_alert ของ scheduler ต้องรายงาน **3 สถานะแยกกัน** (K7).

``alerts/price_alert.check_alerts()`` คืน ``checked`` / ``triggered`` / ``unchecked`` /
``store_error`` แยกกันแล้ว (A1/D1) แต่ ``main.py --job price_alert`` อ่านแค่
``checked`` กับ ``triggered`` ⇒ ทั้งสามกรณีข้างล่างพิมพ์ข้อความ **เหมือนกันเป๊ะ**
และ exit 0 เท่ากันหมด:

1. ไม่มี alert ค้างเลย (ปกติจริง)
2. มี alert ค้าง แต่ดึงราคาไม่ได้ทุกตัว (ระบบตาบอด)
3. อ่านไฟล์คลัง alert ไม่ได้เลย (ไฟล์เสีย — ไม่ได้ตรวจอะไรสักตัว)

ผู้ใช้อ่านแล้วสรุปว่า "ไม่มีอะไรถึงเงื่อนไข" ทั้งที่ข้อ 2/3 คือ "ยังไม่รู้"
— ผิดกฎ ``"ดึงไม่สำเร็จ" ≠ "ไม่มีข้อมูล"`` ตรง ๆ

**ไม่มีการยิงเน็ตในไฟล์นี้** — ``check_alerts`` ถูก stub ทุกเคส และ fixture
``_no_real_sends`` ระเบิดทันทีถ้ามีใครพยายามส่ง Discord/LINE/HTTP จริง
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest
import requests
import schedule as schedule_lib

import alerts.line_notifier as line_notifier
import alerts.notifier as notifier
import alerts.price_alert as price_alert
import main as scheduler_main

MAIN_PY = _ROOT / "main.py"


# ---------------------------------------------------------------- fixtures
@pytest.fixture(autouse=True)
def _no_real_sends(monkeypatch):
    """กันเทสต์ยิงของจริง — เครื่องนี้มี key/webhook จริงอยู่ใน .env"""

    def _boom(*_args, **_kwargs):
        raise AssertionError("เทสต์นี้ห้ามส่งของจริง (Discord/LINE/HTTP)")

    for module in (notifier, price_alert, scheduler_main):
        if hasattr(module, "send_discord_webhook"):
            monkeypatch.setattr(module, "send_discord_webhook", _boom)
    monkeypatch.setattr(line_notifier, "send_line_message", _boom)
    monkeypatch.setattr(scheduler_main, "send_line_message", _boom)
    monkeypatch.setattr(requests, "post", _boom)
    monkeypatch.setattr(requests, "get", _boom)


@pytest.fixture(autouse=True)
def _clean_schedule():
    schedule_lib.clear()
    yield
    schedule_lib.clear()


# ---------------------------------------------------------------- ผลลัพธ์จำลอง
_DISCORD_SKIPPED = {"success": False, "skipped": True, "error": "missing webhook_url"}
_DISCORD_OK = {"success": True}


def _result(**over: Any) -> dict[str, Any]:
    """โครงผลลัพธ์ตามสัญญาจริงของ ``check_alerts()`` (ดู alerts/price_alert.py)."""
    base: dict[str, Any] = {
        "success": True,
        "store_error": False,
        "checked": 0,
        "triggered": [],
        "unchecked": [],
        "daily_summary": "(summary)",
        "daily_discord_result": _DISCORD_OK,
    }
    base.update(over)
    return base


NOTHING_PENDING = _result()

BLIND = _result(
    checked=0,
    unchecked=[
        {"id": "a1", "ticker": "VOO", "reason": "ดึงราคาไม่ได้"},
        {"id": "a2", "ticker": "GLDM", "reason": "ดึงราคาไม่ได้"},
    ],
)

MIXED = _result(
    checked=4,
    triggered=[
        {
            "id": "t1",
            "ticker": "SCHD",
            "alert_type": "below",
            "target_price": 80.0,
            "current_price": 78.5,
        }
    ],
    unchecked=[{"id": "a2", "ticker": "GLDM", "reason": "ดึงราคาไม่ได้"}],
)

STORE_ERROR = _result(
    success=False,
    store_error=True,
    error="price_alerts.json: JSON ไม่ถูกต้อง (Expecting value: line 1 column 1)",
    daily_summary="(store error)",
    daily_discord_result=_DISCORD_SKIPPED,
)


def _stub(monkeypatch, result: dict[str, Any]) -> None:
    """แทน check_alerts ทั้ง 2 ที่ที่ถูกอ้างถึง (ตัวโมดูล + ชื่อที่ main.py import มาแล้ว)."""
    monkeypatch.setattr(price_alert, "check_alerts", lambda: result)
    monkeypatch.setattr(scheduler_main, "check_alerts", lambda: result)


# ---------------------------------------------------------------- 3 สถานะ
class TestThreeStatuses:
    def test_reports_triggered_not_triggered_and_unchecked(self):
        """หนึ่งรอบต้องบอกครบ 3 ตัวเลข: ถึงเงื่อนไข / ตรวจแล้วไม่ถึง / ตรวจไม่ได้"""
        text = scheduler_main.format_price_alert_report(MIXED)
        # checked=4 รวมตัวที่ trigger แล้ว ⇒ ตรวจแล้วไม่ถึง = 4 - 1 = 3
        assert "ถึงเงื่อนไข 1" in text
        assert "ไม่ถึง 3" in text
        assert "ตรวจไม่ได้ 1" in text

    def test_triggered_details_are_shown(self):
        text = scheduler_main.format_price_alert_report(MIXED)
        assert "SCHD" in text
        assert "78.5" in text

    def test_unchecked_details_and_reasons_are_shown(self):
        text = scheduler_main.format_price_alert_report(BLIND)
        assert "VOO" in text and "GLDM" in text
        assert "ดึงราคาไม่ได้" in text

    def test_unchecked_is_not_reported_as_all_clear(self):
        """ตาบอด 2 ตัว ต้องไม่ออกมาหน้าตาเหมือน 'ไม่มีอะไรถึงเงื่อนไข'"""
        blind = scheduler_main.format_price_alert_report(BLIND)
        clean = scheduler_main.format_price_alert_report(NOTHING_PENDING)
        assert blind != clean
        assert "⚠️" in blind
        assert "⚠️" not in clean

    def test_nothing_pending_says_store_was_readable(self):
        """'ไม่มี alert ค้าง' ต้องบอกด้วยว่าอ่านคลังได้ปกติ — คนละเรื่องกับ store_error"""
        text = scheduler_main.format_price_alert_report(NOTHING_PENDING)
        assert "ไม่มี alert" in text


# ---------------------------------------------------------------- store_error
class TestStoreError:
    def test_is_loud(self):
        text = scheduler_main.format_price_alert_report(STORE_ERROR)
        assert "🚨" in text
        assert "ตรวจไม่ได้" in text

    def test_shows_cause_and_file_path(self):
        text = scheduler_main.format_price_alert_report(STORE_ERROR)
        assert "JSON ไม่ถูกต้อง" in text
        assert "price_alerts.json" in text

    def test_says_it_does_not_mean_no_alert(self):
        """ประโยคปฏิเสธชัด ๆ — 'ตรวจไม่ได้' ไม่ใช่ 'ไม่มี alert ถึงเงื่อนไข'"""
        text = scheduler_main.format_price_alert_report(STORE_ERROR)
        assert "ไม่ได้แปลว่า" in text

    def test_differs_from_clean_run(self):
        """หัวใจของบั๊ก: เดิมทั้งสองกรณีพิมพ์บรรทัดเดียวกันเป๊ะ"""
        broken = scheduler_main.format_price_alert_report(STORE_ERROR)
        clean = scheduler_main.format_price_alert_report(NOTHING_PENDING)
        assert broken != clean

    def test_warns_when_discord_notice_did_not_go_out(self):
        """ไม่มี webhook = คำเตือนไม่ถึงผู้ใช้ทางอื่นเลย ต้องบอกใน log"""
        text = scheduler_main.format_price_alert_report(STORE_ERROR)
        assert "Discord" in text


class TestBrokenContract:
    """ผลลัพธ์ขาดคีย์ = สรุปสถานะไม่ได้ ห้ามเงียบเป็น 0 (กฎห้าม .get(x, 0))"""

    def test_missing_keys_are_loud(self):
        text = scheduler_main.format_price_alert_report({"triggered": []})
        assert "🚨" in text
        assert "store_error" in text  # บอกชื่อคีย์ที่ขาด

    def test_non_dict_is_loud(self):
        assert "🚨" in scheduler_main.format_price_alert_report(None)

    def test_unparseable_checked_is_loud(self):
        text = scheduler_main.format_price_alert_report(_result(checked="สาม"))
        assert "🚨" in text


# ---------------------------------------------------------------- run_price_alert_job
class TestRunJob:
    def test_prints_report_and_returns_result(self, monkeypatch, capsys):
        _stub(monkeypatch, MIXED)
        result = scheduler_main.run_price_alert_job()
        out = capsys.readouterr().out
        assert result is MIXED
        assert "ตรวจไม่ได้ 1" in out

    def test_store_error_reaches_stdout(self, monkeypatch, capsys):
        _stub(monkeypatch, STORE_ERROR)
        scheduler_main.run_price_alert_job()
        assert "🚨" in capsys.readouterr().out

    def test_scheduler_registers_reporting_job(self, monkeypatch):
        """งานตามเวลา 09:00/21:00 ต้องผ่านตัวรายงาน ไม่ใช่เรียก check_alerts ดิบ ๆ.

        เมื่อไม่ได้ตั้ง webhook ``check_alerts`` ไม่ส่ง Discord เลย — stdout
        ของ scheduler จึงเป็น **ช่องทางเดียว** ที่ผู้ใช้จะรู้ว่าคลัง alert เสีย
        """
        cfg = {
            "dca": {"day_of_month": 1, "monthly_budget_thb": 5000.0},
            "notifications": {
                "discord_webhook_url": "",
                "weekly_summary": True,
                "dca_reminder": True,
                "rsi_alert": True,
            },
        }
        monkeypatch.setattr(scheduler_main, "load_config", lambda: cfg)

        def _stop(_seconds):
            raise KeyboardInterrupt

        monkeypatch.setattr(scheduler_main.time, "sleep", _stop)
        scheduler_main.run_scheduler()

        names = {
            getattr(getattr(j.job_func, "func", j.job_func), "__name__", "")
            for j in schedule_lib.jobs
        }
        assert names == {"run_price_alert_job"}


# ---------------------------------------------------------------- CLI
def _run_cli(monkeypatch, result: dict[str, Any]) -> int:
    """รัน ``python main.py --job price_alert`` จริง ๆ (ผ่าน runpy) แล้วคืน exit code."""
    _stub(monkeypatch, result)
    monkeypatch.setattr(sys, "argv", ["main.py", "--job", "price_alert"])
    try:
        runpy.run_path(str(MAIN_PY), run_name="__main__")
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


class TestCli:
    def test_clean_run_exits_zero(self, monkeypatch, capsys):
        assert _run_cli(monkeypatch, NOTHING_PENDING) == 0
        assert "🚨" not in capsys.readouterr().out

    def test_unchecked_is_visible(self, monkeypatch, capsys):
        assert _run_cli(monkeypatch, BLIND) == 0
        out = capsys.readouterr().out
        assert "ตรวจไม่ได้ 2" in out
        assert "VOO" in out

    def test_store_error_exits_nonzero(self, monkeypatch, capsys):
        """cron/systemd/CI ต้องเห็นว่ารอบนี้ล้มเหลว ไม่ใช่ 'สำเร็จ ไม่มีอะไร trigger'"""
        code = _run_cli(monkeypatch, STORE_ERROR)
        assert code != 0
        assert "🚨" in capsys.readouterr().out

    def test_broken_contract_exits_nonzero(self, monkeypatch):
        assert _run_cli(monkeypatch, {"triggered": []}) != 0
