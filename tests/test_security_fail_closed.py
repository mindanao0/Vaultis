# -*- coding: utf-8 -*-
"""ตาข่ายของด่าน fail-closed ใน ``backend/security.py``.

ที่มา (AUDIT_ROUND2_2026-08-07 · HIGH "ตาข่าย fail-closed ของ auth ไม่มีเทสต์คุ้มกัน"):
มิวแทนต์ที่แก้ ``_is_local()`` ให้ ``return True`` กับทุก client รอดชีวิตจากชุดเทสต์ทั้ง
1296 ตัว  สาเหตุ: ``TestApiKeyGuard`` ใน tests/test_phase3.py มี 5 เทสต์ — 4 ตัวตั้ง
``VAULTIS_API_KEY`` (ยิงเข้าเส้น hmac ซึ่งไม่เรียก ``_is_local`` เลย) และอีก 1 ตัวเรียกจาก
host ``testclient`` ซึ่ง ``return True`` ก็ให้ 200 เหมือนกัน ⇒ เส้น "ไม่ตั้งคีย์ + client
ไม่ใช่ localhost" ไม่มีอะไรคุ้มเลย

สิ่งที่ไฟล์นี้ตรึง (สัญญาที่ CLAUDE.md + docker-compose.yml เขียนไว้):
- ไม่ตั้ง ``VAULTIS_API_KEY`` + คำขอมาจากภายนอก (Docker bridge 172.x / IP สาธารณะ) = **503**
- ไม่ตั้งคีย์ + เรียกจากเครื่องเดียวกัน = 200 (dev ในเครื่องยังสะดวก)
- ตั้งคีย์แล้ว = ตัดสินด้วยคีย์อย่างเดียว ต้นทางจะเป็น localhost หรือไม่ ไม่เกี่ยว
  (fail-closed ต้องไม่ทำให้ deploy ที่ตั้งคีย์ถูกต้องใช้ไม่ได้)

ไม่ยิง network ทั้งไฟล์
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from backend.security import _is_local, require_api_key

# ต้นทางที่ต้องถือว่า "ไม่ใช่เครื่องเดียวกัน" — สองอันแรกคือสถานการณ์จริงที่เจอ:
# Docker (คำขอมาจาก bridge IP 172.x) และ Render (IP สาธารณะ)
REMOTE_HOSTS = (
    "172.18.0.5",       # Docker bridge
    "10.0.0.9",         # LAN
    "203.0.113.7",      # อินเทอร์เน็ตสาธารณะ
    "127.0.0.1.evil",   # ชื่อที่ "ขึ้นต้นเหมือน" localhost แต่ไม่ใช่
    "0.0.0.0",
)


class _ForceClientHost:
    """ASGI middleware บาง ๆ ที่ปลอม ``scope["client"]`` ให้เป็นต้นทางที่กำหนด.

    starlette 0.41 ตรึง client ของ ``TestClient`` ไว้ที่ ``("testclient", 50000)`` และ
    ไม่มีพารามิเตอร์ให้เปลี่ยน — ชั้นนี้จึงเป็นวิธีที่ไม่ผูกกับเวอร์ชัน
    ``host=None`` = ASGI scope ที่ไม่มี client เลย (สเปกอนุญาต)
    """

    def __init__(self, app, host: str | None, port: int = 5000):
        self.app = app
        self.host = host
        self.port = port

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope = dict(scope)
            scope["client"] = None if self.host is None else (self.host, self.port)
        await self.app(scope, receive, send)


def _guarded_app() -> FastAPI:
    app = FastAPI()

    @app.get("/open")
    def open_route():
        return {"ok": True}

    @app.get("/guarded", dependencies=[Depends(require_api_key)])
    def guarded():
        return {"ok": True}

    return app


def _local_client() -> TestClient:
    """ค่าเดิมของ ``TestClient`` — client host = ``testclient`` ซึ่งนับเป็น local."""
    return TestClient(_guarded_app())


def _remote_client(host: str | None) -> TestClient:
    return TestClient(_ForceClientHost(_guarded_app(), host))


class TestIsLocalUnit:
    """ตรวจ ``_is_local()`` ตรง ๆ — มิวแทนต์ ``return True`` ตายที่นี่ทันที."""

    @pytest.mark.parametrize("host", REMOTE_HOSTS)
    def test_remote_hosts_are_not_local(self, host):
        assert _is_local(SimpleNamespace(client=SimpleNamespace(host=host))) is False, (
            f"{host} ต้องไม่ถูกนับเป็น localhost — ข้อยกเว้น 'ไม่ตั้งคีย์ก็เข้าได้' "
            "มีไว้สำหรับเครื่องเดียวกันเท่านั้น"
        )

    @pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "testclient"])
    def test_local_hosts_are_local(self, host):
        assert _is_local(SimpleNamespace(client=SimpleNamespace(host=host))) is True

    def test_missing_client_is_not_local(self):
        """ASGI scope ที่ไม่มี client (พร็อกซี/transport บางตัว) = ไม่รู้ต้นทาง = ไม่ใช่ local."""
        assert _is_local(SimpleNamespace(client=None)) is False


class TestFailClosedWithoutApiKey:
    """ไม่ตั้ง ``VAULTIS_API_KEY`` แล้ว deploy สาธารณะ ต้องปิดตาย ไม่ใช่เปิดหมด."""

    @pytest.mark.parametrize("host", REMOTE_HOSTS)
    def test_remote_request_gets_503_when_key_is_unset(self, monkeypatch, host):
        monkeypatch.delenv("VAULTIS_API_KEY", raising=False)
        resp = _remote_client(host).get("/guarded")

        assert resp.status_code == 503, (
            f"คำขอจาก {host} โดยไม่ตั้ง VAULTIS_API_KEY ต้องถูกปฏิเสธ 503 "
            f"(ได้ {resp.status_code}) — ไม่งั้น backend ที่ลืมตั้งคีย์จะเปิดสมุดบัญชี "
            "และ /api/ai/* ให้ทุกคน"
        )
        assert "VAULTIS_API_KEY" in resp.json()["detail"], (
            "ข้อความต้องบอกว่า 'คอนฟิกยังไม่ได้ตั้ง' ไม่ใช่ 'คีย์ผิด' — สองอย่างนี้คนละเรื่องกัน"
        )

    def test_request_without_client_info_gets_503_when_key_is_unset(self, monkeypatch):
        monkeypatch.delenv("VAULTIS_API_KEY", raising=False)
        resp = _remote_client(None).get("/guarded")
        assert resp.status_code == 503

    def test_remote_request_cannot_bypass_with_any_header_when_key_is_unset(
        self, monkeypatch
    ):
        """ไม่ตั้งคีย์ = ไม่มีคีย์ใดถูก — ส่ง X-API-Key มั่ว ๆ ต้องไม่เปิดประตู."""
        monkeypatch.delenv("VAULTIS_API_KEY", raising=False)
        resp = _remote_client("172.18.0.5").get("/guarded", headers={"X-API-Key": "x"})
        assert resp.status_code == 503

    def test_blank_key_env_is_treated_as_unset(self, monkeypatch):
        """``VAULTIS_API_KEY="   "`` = ยังไม่ได้ตั้ง — ห้ามกลายเป็นคีย์ว่างที่เดาถูกได้."""
        monkeypatch.setenv("VAULTIS_API_KEY", "   ")
        assert _remote_client("203.0.113.7").get("/guarded").status_code == 503
        assert (
            _remote_client("203.0.113.7")
            .get("/guarded", headers={"X-API-Key": ""})
            .status_code
            == 503
        )

    def test_local_dev_still_works_without_key(self, monkeypatch):
        """ตาข่ายคู่: fail-closed ต้องปิดเฉพาะทางที่เชื่อถือไม่ได้ ไม่ใช่ปิดทั้งแอป."""
        monkeypatch.delenv("VAULTIS_API_KEY", raising=False)
        assert _local_client().get("/guarded").status_code == 200

    @pytest.mark.parametrize("host", REMOTE_HOSTS)
    def test_open_routes_stay_open_for_remote_clients(self, monkeypatch, host):
        """เส้นทางอ่านอย่างเดียว (ราคา/สัญญาณ) ต้องไม่ถูกด่านนี้ปิดไปด้วย."""
        monkeypatch.delenv("VAULTIS_API_KEY", raising=False)
        assert _remote_client(host).get("/open").status_code == 200


class TestKeyDecidesWhenConfigured:
    """ตั้งคีย์แล้ว = ตัดสินด้วยคีย์ ต้นทางไม่เกี่ยว (ทั้งสองทิศ)."""

    def test_remote_request_with_correct_key_passes(self, monkeypatch):
        monkeypatch.setenv("VAULTIS_API_KEY", "secret123")
        resp = _remote_client("172.18.0.5").get(
            "/guarded", headers={"X-API-Key": "secret123"}
        )
        assert resp.status_code == 200

    def test_remote_request_without_key_header_is_401_not_503(self, monkeypatch):
        """ตั้งคีย์แล้วแต่ไม่ส่ง header = 'คีย์ผิด' (401) ไม่ใช่ 'ยังไม่ได้คอนฟิก' (503)."""
        monkeypatch.setenv("VAULTIS_API_KEY", "secret123")
        assert _remote_client("172.18.0.5").get("/guarded").status_code == 401

    def test_localhost_is_not_a_backdoor_once_a_key_is_configured(self, monkeypatch):
        """ตั้งคีย์แล้ว localhost ก็ต้องส่งคีย์ — ไม่มีทางลัดจากเครื่องเดียวกัน."""
        monkeypatch.setenv("VAULTIS_API_KEY", "secret123")
        assert _local_client().get("/guarded").status_code == 401
