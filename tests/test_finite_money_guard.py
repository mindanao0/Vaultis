# -*- coding: utf-8 -*-
"""จำนวนเงินที่เป็น ``inf``/``NaN`` ต้องถูกปฏิเสธที่ประตู — และผลที่ล้นต้องดังเป็น 4xx.

``Field(gt=0)`` ของ pydantic **ไม่กัน ``inf``** เพราะ ``inf > 0`` เป็นจริง (K8 · G8)
โปรเจกต์รู้จักบั๊กชนิดนี้และแก้ไปแล้วสามช่อง (``budget_thb`` / ``initial_capital`` /
``monthly_investment``) แต่ **โมเดลอีกสามไฟล์ไม่เคยได้ด่านนี้เลย**

วัดจริง 2026-09-01 ก่อนแก้ — ``POST /api/networth/snapshot`` ด้วย ``value_thb: Infinity``:

1. ผ่าน ``Field(gt=0)``
2. **ถูก commit ลง SQLite** เป็น ``total_assets_thb = inf``
3. แล้วค่อยพังตอน serialize ⇒ ผู้ใช้เห็น HTTP 500 (ดูเหมือนบันทึกไม่สำเร็จ)
4. หลังจากนั้น ``GET /api/networth/history`` **โยน exception ทุกครั้ง** เพราะแถวพิษยังอยู่
   ⇒ คำขอผิดครั้งเดียวทำให้ประวัติมูลค่าสุทธิพังถาวร ผู้ใช้แก้เองจากหน้าจอไม่ได้

และอีกเส้นทางหนึ่งที่ด่านประตูกันไม่ได้: อินพุตที่ **จำกัดค่าถูกต้องทุกช่อง** แต่ผลคูณ
ล้น (``1e308 × 3.5``) — ต้องดังเป็น ``ValueError`` แล้ว router แปลงเป็น 422
ห้ามเป็น 500 ซึ่งโยนความผิดของคำขอไปให้เซิร์ฟเวอร์ (บั๊กชนิดเดียวกับ D3.1)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_PROFILE = {
    "job_stability": "stable",
    "dependents": 1,
    "income_type": "salary",
    "has_health_insurance": True,
    "industry": "other",
}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULTIS_API_KEY", "test-key")
    monkeypatch.setenv("VAULTIS_DB_PATH", str(tmp_path / "t.db"))
    from fastapi.testclient import TestClient

    from backend.database import Base, engine
    from backend.main import app

    Base.metadata.create_all(bind=engine)
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {"content-type": "application/json", "X-API-Key": "test-key"}


def _ef_body(expense: str, capacity: str = "1000.0") -> bytes:
    return (
        '{"profile":%s,"monthly_expense":%s,"current_savings":0.0,'
        '"monthly_saving_capacity":%s}' % (json.dumps(_PROFILE), expense, capacity)
    ).encode()


def _nw_body(value: str, date: str = "2026-01-05") -> bytes:
    return (
        '{"snapshot_date":"%s","assets":[{"name":"cash","type":"cash","value_thb":%s}],'
        '"liabilities":[]}' % (date, value)
    ).encode()


class TestDoorRejectsNonFinite:
    """ต้องปฏิเสธ **ตั้งแต่ประตู** ไม่ใช่ไปตายตอนคำนวณหรือตอนแสดงผล."""

    @pytest.mark.parametrize("token", ["Infinity", "-Infinity", "NaN"])
    def test_emergency_fund_ปฏิเสธค่าไม่จำกัด(self, client, token):
        r = client.post(
            "/api/emergency-fund/calculate", content=_ef_body(token), headers=_headers()
        )
        assert r.status_code == 422, f"{token} ต้องถูกปฏิเสธ ไม่ใช่ {r.status_code}"

    @pytest.mark.parametrize("token", ["Infinity", "NaN"])
    def test_networth_ปฏิเสธค่าไม่จำกัด(self, client, token):
        r = client.post("/api/networth/snapshot", content=_nw_body(token), headers=_headers())
        assert r.status_code == 422

    def test_ค่าปกติยังผ่านตามเดิม(self, client):
        assert client.post(
            "/api/emergency-fund/calculate", content=_ef_body("30000.0"), headers=_headers()
        ).status_code == 200
        assert client.post(
            "/api/networth/snapshot", content=_nw_body("50000.0"), headers=_headers()
        ).status_code in (200, 201)


class TestNoPoisonedRowSurvives:
    """หัวใจของบั๊ก: แถวพิษเคยถูก commit **ก่อน** จะพัง แล้วทำให้ประวัติพังถาวร."""

    def test_คำขอที่ถูกปฏิเสธต้องไม่เขียนแถวลงฐาน(self, client):
        """วัดส่วนต่างก่อน/หลัง — ``engine`` ถูกสร้างครั้งเดียวตอน import ฐานจึงใช้ร่วมกัน
        ทั้งไฟล์ การยืนยันว่า "ตารางว่าง" จึงวัดผลของเทสต์ตัวอื่นด้วย ไม่ใช่ของตัวเอง."""
        import math

        from backend.database import SessionLocal
        from backend.models.orm import NetWorthSnapshot

        def _rows() -> list[float]:
            db = SessionLocal()
            try:
                return [r.total_assets_thb for r in db.query(NetWorthSnapshot).all()]
            finally:
                db.close()

        before = _rows()
        client.post(
            "/api/networth/snapshot",
            content=_nw_body("Infinity", "2026-03-05"),
            headers=_headers(),
        )
        after = _rows()

        assert len(after) == len(before), "คำขอที่ถูกปฏิเสธต้องไม่ทิ้งแถวไว้ในฐาน"
        # หัวใจของบั๊กเดิม: แถวที่ commit ไปแล้วมีค่าเป็น inf และทำให้ทุกการอ่านหลังจากนั้นพัง
        assert all(v is None or math.isfinite(v) for v in after), f"มีแถวค่าไม่จำกัดในฐาน: {after}"

    def test_ประวัติยังอ่านได้หลังถูกยิงค่าพิษ(self, client):
        client.post(
            "/api/networth/snapshot", content=_nw_body("50000.0", "2026-01-05"), headers=_headers()
        )
        client.post(
            "/api/networth/snapshot", content=_nw_body("Infinity", "2026-02-05"), headers=_headers()
        )
        # เดิมบรรทัดนี้โยน ValueError ออกมาจากตัวแอปเลย ไม่ใช่แค่ตอบ 500
        assert client.get("/api/networth/history", headers=_headers()).status_code == 200


class TestOverflowFromFiniteInputIsClientError:
    """อินพุตจำกัดค่าดี ๆ แต่ผลล้น — ด่านประตูกันไม่ได้ ต้องดังเป็น 4xx ไม่ใช่ 500."""

    def test_ผลคูณล้นต้องเป็น_422_ไม่ใช่_500(self, client):
        r = client.post(
            "/api/emergency-fund/calculate",
            content=_ef_body("1e308", "0.0"),
            headers=_headers(),
        )
        assert r.status_code == 422, "ความผิดของตัวเลขที่กรอก ห้ามรายงานว่าเซิร์ฟเวอร์พัง"

    def test_ผลหารล้นต้องเป็น_422_ไม่ใช่_500(self, client):
        r = client.post(
            "/api/emergency-fund/calculate",
            content=_ef_body("1e308", "1e-300"),
            headers=_headers(),
        )
        assert r.status_code == 422

    def test_ข้อความบอกสาเหตุเป็นภาษาไทย(self, client):
        r = client.post(
            "/api/emergency-fund/calculate", content=_ef_body("1e308", "0.0"), headers=_headers()
        )
        assert "เกินช่วงที่คำนวณได้" in str(r.json()["detail"])


class TestSingleDefinition:
    """นิยามของ "จำกัดค่า" ต้องมีที่เดียว — ก่อนหน้านี้มีสองที่และอีกสามไฟล์ไม่มีเลย."""

    def test_schemas_ไม่เขียนเงื่อนไข_isfinite_เอง(self):
        source = (_ROOT / "backend" / "schemas.py").read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )
        assert "math.isfinite" not in code, "ต้องเรียก finite.py ไม่ใช่เขียนเงื่อนไขซ้ำ"

    def test_ไม่มี_money_field_ที่ยังเป็น_plain_float(self):
        """``x: float = Field(gt=0)`` คือรูปแบบที่มีรูโหว่ — ต้องไม่เหลือในโมเดล."""
        import re

        offenders: list[str] = []
        for rel in ("backend/schemas.py", "backend/models/networth_models.py",
                    "backend/models/emergency_fund_models.py", "backend/models/debt_models.py"):
            text = (_ROOT / rel).read_text(encoding="utf-8")
            for match in re.finditer(r"^\s+(\w+): float = Field\((gt|ge)=", text, re.M):
                offenders.append(f"{rel}:{match.group(1)}")
        assert offenders == [], f"ช่องที่ยังไม่มีด่าน finite: {offenders}"

    def test_debt_models_ใช้นิยามกลางไม่ใช่ของตัวเอง(self):
        source = (_ROOT / "backend" / "models" / "debt_models.py").read_text(encoding="utf-8")
        assert "from .finite import" in source
        assert "BeforeValidator" not in source, "ต้องไม่มีสำเนาที่สองของตัวตรวจ"
