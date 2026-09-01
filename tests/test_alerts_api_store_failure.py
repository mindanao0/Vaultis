# -*- coding: utf-8 -*-
"""ผู้เรียกฝั่ง API ของคลัง alert (AUDIT_2026-08-06 ข้อ A1 — ขั้นตอน "ไล่ผู้เรียก").

A1 เปลี่ยนพฤติกรรมของ ``alerts/price_alert.py``: "อ่านคลังไม่ได้" ไม่คืนลิสต์ว่างอีกต่อไป
แต่โยน ``AlertStoreUnavailable`` ⇒ ``backend/routers/alerts.py`` ต้องแปลงให้เป็นคำตอบที่
ผู้ใช้อ่านรู้เรื่อง ไม่ใช่ปล่อยหลุดเป็น ``500 Internal Server Error`` เปล่า ๆ

อาการที่วัดได้ก่อนแก้ (คลังเสีย + ยิงผ่าน TestClient):

    DELETE /api/alerts/a1 → 500 {'detail': 'Internal Server Error'}   ← ไม่บอกอะไรเลย
    GET    /api/alerts    → 500 (ปนกับ "ระบบมีข้อผิดพลาด" ทั่วไป แยกไม่ออก)
    POST   /api/alerts    → 500 (เหมือนกัน)

ความปลอดภัย: ชี้ ``ALERTS_PATH`` ไป ``tmp_path`` ทุกเคส · ไม่มี network (stub ราคา) ·
ไม่มี webhook · ``VAULTIS_DB_PATH`` ชี้ /tmp ก่อน import ``backend.main``
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
os.environ.setdefault("VAULTIS_DB_PATH", str(Path(tempfile.gettempdir()) / "vaultis_test_alerts_api.db"))

from fastapi.testclient import TestClient  # noqa: E402

import alerts.price_alert as pa  # noqa: E402
from backend.main import app  # noqa: E402

REAL_STORE = _ROOT / "alerts" / "data" / "price_alerts.json"


@pytest.fixture
def broken_store(tmp_path, monkeypatch):
    """คลังที่อ่านไม่ออก (จำลองการเขียนที่ถูกขัดจังหวะ) + ไม่มีคีย์ API + ไม่มีเน็ต."""
    path = tmp_path / "price_alerts.json"
    full = json.dumps({"alerts": [{"id": "a1", "ticker": "VOO", "alert_type": "below", "price": 400.0}]})
    path.write_text(full[: len(full) // 2], encoding="utf-8")

    monkeypatch.setattr(pa, "ALERTS_PATH", path)
    assert path.resolve() != REAL_STORE.resolve()
    monkeypatch.delenv("VAULTIS_API_KEY", raising=False)  # TestClient นับเป็น localhost
    monkeypatch.setattr(pa, "get_current_prices", lambda tickers: {})
    monkeypatch.setattr(pa, "send_discord_webhook", lambda **kw: {"success": True})
    return path


@pytest.fixture
def client():
    # ไม่ใช้ context manager — ไม่จุด lifespan/APScheduler และไม่ยิง network
    return TestClient(app)


def _assert_store_error(response) -> None:
    assert response.status_code == 503, f"ต้องบอกว่าคลัง alert ใช้ไม่ได้ ไม่ใช่ {response.status_code} ทั่วไป"
    detail = str(response.json()["detail"])
    assert "alert" in detail.lower()
    assert "Internal Server Error" not in detail


class TestUnreadableStoreIsReportedNotSwallowed:
    def test_list_reports_store_failure(self, broken_store, client):
        _assert_store_error(client.get("/api/alerts"))

    def test_create_reports_store_failure(self, broken_store, client):
        response = client.post(
            "/api/alerts",
            json={"ticker": "VOO", "alert_type": "below", "target_price": 400.0},
        )
        _assert_store_error(response)

    def test_delete_reports_store_failure_instead_of_bare_500(self, broken_store, client):
        _assert_store_error(client.delete("/api/alerts/a1"))

    def test_store_is_never_overwritten_by_the_api(self, broken_store, client):
        before = broken_store.read_text(encoding="utf-8")
        client.get("/api/alerts")
        client.post("/api/alerts", json={"ticker": "VOO", "alert_type": "below", "target_price": 400.0})
        client.delete("/api/alerts/a1")
        assert broken_store.read_text(encoding="utf-8") == before


class TestCheckSurfacesUncheckedAlerts:
    """D1.1 — ``POST /api/alerts/check`` ต้องส่งต่อ ``unchecked`` ให้ผู้เรียกเห็น."""

    def test_price_failure_is_not_reported_as_checked(self, tmp_path, monkeypatch, client):
        path = tmp_path / "price_alerts.json"
        path.write_text(
            json.dumps(
                {
                    "alerts": [
                        {"id": "a1", "ticker": "VOO", "alert_type": "above", "price": 1.0, "triggered": False}
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(pa, "ALERTS_PATH", path)
        assert path.resolve() != REAL_STORE.resolve()
        monkeypatch.delenv("VAULTIS_API_KEY", raising=False)
        monkeypatch.setattr(pa, "get_price_snapshots", lambda tickers: {})
        monkeypatch.setattr(pa, "send_discord_webhook", lambda **kw: {"success": True})
        monkeypatch.setattr(pa, "load_config", lambda: {"notifications": {"discord_webhook_url": ""}})

        data = client.post("/api/alerts/check").json()["data"]

        assert data["checked"] == 0
        assert [row["ticker"] for row in data["unchecked"]] == ["VOO"]
