# -*- coding: utf-8 -*-
"""สัญญาผลลัพธ์ของ ``check_alerts()`` ต้องมีด่านตรวจ **ตัวเดียว** ที่ผู้เรียกทุกคนใช้ร่วมกัน.

สองข้อจาก AUDIT_ROUND2_2026-08-07 ที่ไฟล์นี้ตรึงไว้:

1. **ผลลัพธ์ผิดสัญญาถูกแปลงเป็น "ตรวจแล้วไม่มีอะไร"** — ``main.py`` มีด่านตรวจของตัวเอง
   (``_price_alert_contract_error``) แต่ ``alert_service.check_alerts()`` เติมค่าดีฟอลต์เอง
   ครบทุกคีย์ (``.get("checked", 0)``, ``.get("unchecked", [])``) วัดผลจริงได้ดังนี้::

       stub price_alert.check_alerts → {"success": True, "triggered": []}   (ขาด 3 คีย์)
       [main.py]            🚨 ผลลัพธ์จาก check_alerts() ผิดสัญญา — ขาดคีย์ ...
       [/api/alerts/check]  200 {"checked":0,"triggered":[],"unchecked":[],"store_error":false}

   ⇒ scheduler โวยถูกต้อง แต่ปุ่มบนแดชบอร์ด/API ยืนยันกับผู้ใช้ว่า "ยังไม่มี Alert
   ที่ถึงเงื่อนไข" ทั้งที่ระบบไม่ได้ตรวจอะไรเลย  ตอนนี้ด่านตรวจย้ายไปอยู่ข้าง **ผู้ผลิต**
   (``alerts/price_alert.check_result_contract_error``) แล้วผู้เรียกทุกรายเรียกตัวเดียวกัน

2. **"เครื่องนี้ไม่มีคลัง alert เลย" ถูกยุบเข้ากับ "อ่านคลังได้ 0 รายการ"** — ทั้งสองกรณี
   ให้ payload หน้าตาเหมือนกันทุกช่อง (``store_error=False, checked=0, triggered=[],
   unchecked=[]``) scheduler จึงพิมพ์ "(อ่านคลัง alert ได้ปกติ)" ทั้งที่ไม่เคยมีคลังให้อ่าน
   — ยืนยันบนของจริง: ``ls alerts/data`` มีแต่ ``.gitkeep`` และ log รอบ 21:00 พิมพ์บรรทัดนั้น
   ตอนนี้ ``check_alerts()`` แนบ ``store_status`` มาด้วยเสมอ

ความปลอดภัย: ทุกเคสชี้ ``pa.ALERTS_PATH`` ไป ``tmp_path`` · stub ราคา/webhook/config
(คอนเทนเนอร์โหลด ``.env`` จริงที่มี ``DISCORD_WEBHOOK_URL``) · ``VAULTIS_DB_PATH`` ชี้ /tmp
ก่อน import ``backend.main``
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ``backend.database`` สร้างไฟล์ SQLite ตอน import — ห้ามให้ไปโดนฐานจริงของผู้ใช้
os.environ.setdefault("VAULTIS_DB_PATH", str(Path(tempfile.gettempdir()) / "vaultis_test_alert_contract.db"))

from fastapi.testclient import TestClient  # noqa: E402

import alerts.price_alert as pa  # noqa: E402
import main as scheduler_main  # noqa: E402
from backend.main import app  # noqa: E402
from backend.services import alert_service  # noqa: E402

REAL_STORE = _ROOT / "alerts" / "data" / "price_alerts.json"

# ผลลัพธ์ที่ "ผิดสัญญา" แบบเดียวกับที่โพรบของ audit ใช้ — ขาด store_error/checked/unchecked
BROKEN_RESULT = {"success": True, "triggered": []}


def _ok_result(**over) -> dict:
    base = {
        "success": True,
        "store_error": False,
        "checked": 0,
        "triggered": [],
        "unchecked": [],
        "store_status": {"status": "ok", "path": "/tmp/x.json", "pending": 0, "triggered": 0, "error": None},
        "daily_summary": "(summary)",
        "daily_discord_result": {"success": True},
    }
    base.update(over)
    return base


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """คลังชั่วคราว (ยัง**ไม่มีไฟล์**) + ไม่มี webhook + ไม่มีเน็ต + ไม่มีคีย์ API."""
    path = tmp_path / "price_alerts.json"
    monkeypatch.setattr(pa, "ALERTS_PATH", path)
    assert path.resolve() != REAL_STORE.resolve()
    monkeypatch.setattr(pa, "send_discord_webhook", lambda **kw: {"success": True})
    monkeypatch.setattr(pa, "load_config", lambda: {"notifications": {"discord_webhook_url": ""}})
    monkeypatch.setattr(pa, "get_price_snapshots", lambda tickers: {})
    monkeypatch.delenv("VAULTIS_API_KEY", raising=False)  # TestClient นับเป็น localhost
    return path


@pytest.fixture
def client():
    # ไม่ใช้ context manager — ไม่จุด lifespan/APScheduler และไม่ยิง network
    return TestClient(app)


# ------------------------------------------------------------------ ด่านตรวจตัวเดียว


class TestOneCheckerForEveryCaller:
    """"ย้ายไปไว้ข้างผู้ผลิต" ต้องเป็นของจริง ไม่ใช่ก๊อปวางไว้สองที่แล้วค่อย ๆ ต่างกัน."""

    def test_scheduler_uses_the_store_modules_checker(self):
        assert scheduler_main.check_result_contract_error is pa.check_result_contract_error

    def test_scheduler_has_no_private_copy_left(self):
        assert not hasattr(scheduler_main, "_price_alert_contract_error"), (
            "ด่านตรวจสัญญาต้องมีนิยามเดียวใน alerts/price_alert.py — สำเนาใน main.py "
            "คือเหตุที่ผู้เรียกรายอื่นไม่รู้จักสัญญานี้ตั้งแต่แรก"
        )

    def test_service_uses_the_store_modules_checker(self, monkeypatch):
        """พิสูจน์ด้วยพฤติกรรม: เปลี่ยนคำตอบของด่านตรวจตัวกลาง แล้ว service ต้องเปลี่ยนตาม."""
        monkeypatch.setattr(pa, "check_alerts", lambda: _ok_result())
        monkeypatch.setattr(pa, "check_result_contract_error", lambda result: "เหตุผลจากด่านตรวจกลาง")

        with pytest.raises(pa.AlertCheckContractError) as excinfo:
            alert_service.check_alerts()

        assert "เหตุผลจากด่านตรวจกลาง" in str(excinfo.value)


class TestCheckerRules:
    def test_full_result_passes(self):
        assert pa.check_result_contract_error(_ok_result()) is None

    def test_missing_keys_are_named(self):
        message = pa.check_result_contract_error(BROKEN_RESULT)
        assert message is not None
        for key in ("store_error", "checked", "unchecked"):
            assert key in message

    def test_non_dict_is_rejected(self):
        assert pa.check_result_contract_error(None) is not None
        assert pa.check_result_contract_error([]) is not None

    def test_unparseable_checked_is_rejected(self):
        assert pa.check_result_contract_error(_ok_result(checked="เยอะ")) is not None

    def test_malformed_store_status_is_rejected(self):
        assert pa.check_result_contract_error(_ok_result(store_status="ok")) is not None
        assert pa.check_result_contract_error(_ok_result(store_status={"status": "ดีอยู่"})) is not None

    def test_absent_store_status_is_tolerated_but_never_read_as_ok(self):
        """คีย์เสริม: ผู้เรียกที่ประกอบ dict เองยังผ่านด่านได้ **แต่** ต้องไม่ถูกอ่านว่า "อ่านคลังได้ปกติ".

        เจตนาต่างจากที่ audit เสนอ (บังคับให้มี ``store_status``) เพราะการบังคับจะทำให้
        ผลลัพธ์ที่ผู้เรียกภายนอกประกอบเองกลายเป็น "ผิดสัญญา" ทั้งที่ข้อมูลครบ — สิ่งที่
        ต้องกันจริง ๆ คือ "ไม่มีคีย์" ถูกอ่านเป็น "ok" ซึ่งตรึงไว้ในเทสต์บรรทัดล่าง
        """
        result = _ok_result()
        result.pop("store_status")
        assert pa.check_result_contract_error(result) is None

        text = scheduler_main.format_price_alert_report(result)
        assert "อ่านคลัง alert ได้ปกติ" not in text
        assert "ไม่ทราบ" in text


class TestBrokenContractIsNotNormalized:
    """ผลลัพธ์ผิดสัญญาต้องดังทุกด่าน — ห้ามด่านไหนแปลงเป็น "ตรวจแล้วไม่มีอะไร"."""

    def test_scheduler_report_is_loud(self):
        text = scheduler_main.format_price_alert_report(BROKEN_RESULT)
        assert "🚨" in text
        assert "ผิดสัญญา" in text
        assert "ยังไม่รู้" in text

    def test_service_raises_instead_of_filling_defaults(self, monkeypatch):
        monkeypatch.setattr(pa, "check_alerts", lambda: dict(BROKEN_RESULT))

        with pytest.raises(pa.AlertCheckContractError) as excinfo:
            alert_service.check_alerts()

        message = str(excinfo.value)
        assert "store_error" in message  # บอกชื่อคีย์ที่ขาด
        assert "ไม่ใช่ 'ไม่มี'" in message

    def test_api_answers_503_not_an_all_clear_200(self, sandbox, monkeypatch, client):
        monkeypatch.setattr(pa, "check_alerts", lambda: dict(BROKEN_RESULT))

        response = client.post("/api/alerts/check")

        assert response.status_code == 503, (
            f"ผลลัพธ์ผิดสัญญาต้องไม่ออกเป็น {response.status_code} ที่มี checked=0 "
            "ซึ่งหน้าจออ่านว่า 'ยังไม่มี Alert ที่ถึงเงื่อนไข'"
        )
        detail = str(response.json()["detail"])
        assert "alert" in detail.lower()
        assert "ยังไม่รู้" in detail
        assert "Internal Server Error" not in detail

    def test_api_still_answers_200_for_a_valid_result(self, sandbox, monkeypatch, client):
        """อีกด้านของตาข่าย — ด่านใหม่ต้องไม่ทำให้ผลลัพธ์ปกติกลายเป็น 503."""
        monkeypatch.setattr(pa, "check_alerts", lambda: _ok_result(checked=2))

        data = client.post("/api/alerts/check").json()["data"]

        assert data["checked"] == 2
        assert data["store_error"] is False


# ------------------------------------------------- "ไม่มีคลังให้ตรวจ" ≠ "ไม่มี alert ถึงเงื่อนไข"


class TestMissingStoreIsNotAnAllClear:
    def test_check_result_carries_the_store_status(self, sandbox):
        result = pa.check_alerts()

        assert not sandbox.exists(), "การตรวจต้องไม่สร้างคลังเปล่าให้เอง"
        assert result["store_status"]["status"] == "missing"
        assert result["checked"] == 0

    def test_report_says_nothing_was_checked_this_round(self, sandbox):
        text = scheduler_main.format_price_alert_report(pa.check_alerts())

        assert "อ่านคลัง alert ได้ปกติ" not in text, (
            "ไม่มีไฟล์คลังเลย ≠ อ่านคลังได้แล้วไม่มี alert ค้าง"
        )
        assert "ไม่มีไฟล์คลัง" in text
        assert "ไม่ได้แปลว่า" in text
        assert str(sandbox) in text, "ต้องบอก path ที่ระบบมองหา ไม่งั้นผู้ใช้ไล่ต่อไม่ได้"

    def test_report_confirms_only_when_the_store_was_really_read(self, sandbox):
        sandbox.write_text(json.dumps({"alerts": []}, ensure_ascii=False), encoding="utf-8")

        result = pa.check_alerts()
        text = scheduler_main.format_price_alert_report(result)

        assert result["store_status"]["status"] == "ok"
        assert "อ่านคลัง alert ได้ปกติ" in text
        assert "ไม่มีไฟล์คลัง" not in text

    def test_missing_and_empty_store_do_not_print_the_same_line(self, sandbox):
        missing_text = scheduler_main.format_price_alert_report(pa.check_alerts())
        sandbox.write_text(json.dumps({"alerts": []}, ensure_ascii=False), encoding="utf-8")
        empty_text = scheduler_main.format_price_alert_report(pa.check_alerts())

        assert missing_text != empty_text, "หัวใจของบั๊ก: สองสถานะนี้เคยพิมพ์บรรทัดเดียวกันเป๊ะ"

    def test_store_status_reaches_the_api_payload(self, sandbox, client):
        data = client.post("/api/alerts/check").json()["data"]

        assert data["store_status"]["status"] == "missing"

    def test_unreadable_store_reports_error_status(self, sandbox):
        sandbox.write_text('{"alerts": [', encoding="utf-8")

        result = pa.check_alerts()

        assert result["store_error"] is True
        assert result["store_status"]["status"] == "error"
