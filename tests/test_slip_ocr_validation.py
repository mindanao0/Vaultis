# -*- coding: utf-8 -*-
"""AUDIT_2026-08-06 ข้อ D2 — สัญญาของ ``POST /api/transactions/upload-slip``.

3 อาการที่เอกสารผลตรวจวัดไว้:

* **D2.1** ผลลัพธ์จากโมเดลถูกส่งต่อเป็น ``success=true`` โดยไม่ตรวจอะไรเลย
  (``amount`` ติดลบ / เป็น ``null`` ทั้งใบ / มหาศาล, ``date`` มั่ว, ``category`` นอกรายการ)
* **D2.2** ``amount`` ที่เป็นสตริงมีคอมมา (``"1,234.56"``) → HTTP 500 ภาษาอังกฤษ
* **D2.3** ไฟล์ใหญ่เกินเพดานถูกอ่านเข้าหน่วยความจำทั้งก้อนก่อนจะถูกปฏิเสธ

หลักที่ยึด: **"ดึงไม่สำเร็จ" ≠ "ได้ค่ามา"** — ยอดเงินที่อ่านไม่ได้ต้องไม่กลายเป็นตัวเลข
ในสมุดบัญชี ต้องตอบ ``success=false`` พร้อมเหตุผลภาษาไทย

ทุกเคส stub ไคลเอนต์ Anthropic — ห้ามยิงจริง (คอนเทนเนอร์เทสต์โหลด ``.env`` ที่มีคีย์จริง)
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

# ``backend.database`` สร้างไฟล์ SQLite ตอน import — ชี้ไป tmp ไม่ให้แตะฐานจริงของผู้ใช้
os.environ.setdefault("VAULTIS_DB_PATH", str(Path(tempfile.gettempdir()) / "vaultis_test_slip.db"))

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402
from backend.routers import transactions  # noqa: E402

_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


# --------------------------------------------------------------------------- #
# ไคลเอนต์ปลอม — ไม่มีทางหลุดไปเรียก Anthropic จริง
# --------------------------------------------------------------------------- #
class _Block:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.content = [_Block(text)]


class _FakeMessages:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse(self._text)


class _FakeClient:
    def __init__(self, text: str) -> None:
        self.messages = _FakeMessages(text)


@pytest.fixture(autouse=True)
def _local_only(monkeypatch):
    """ไม่ตั้ง VAULTIS_API_KEY → security.py ยอมให้ TestClient (localhost) เรียกได้"""
    monkeypatch.delenv("VAULTIS_API_KEY", raising=False)
    # กันพลาดขั้นสุดท้าย: ถ้าเคสไหนลืม stub ให้ระเบิดแทนที่จะเสียเงิน
    monkeypatch.setattr(transactions, "_client", None)
    monkeypatch.setattr(
        transactions,
        "_get_client",
        lambda: pytest.fail("เทสต์เรียก Anthropic จริง — ต้อง stub ทุกเคส"),
    )


def _post(monkeypatch, model_output: str, *, body: bytes = _JPEG, content_type: str = "image/jpeg"):
    fake = _FakeClient(model_output)
    monkeypatch.setattr(transactions, "_client", fake)
    monkeypatch.setattr(transactions, "_get_client", lambda: fake)
    client = TestClient(app, raise_server_exceptions=False)  # ไม่เข้า context manager = ไม่จุด scheduler
    return client.post(
        "/api/transactions/upload-slip",
        files={"file": ("slip.jpg", body, content_type)},
    )


def _slip(**overrides) -> str:
    payload = {
        "is_slip": True,
        "error": None,
        "amount": 1234.56,
        "date": "2026-08-05",
        "sender": "นาย ก",
        "receiver": "นาง ข",
        "category": "ลงทุน",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# D2.1 — ค่าที่โมเดลคืนมาต้องถูกตรวจก่อนกลายเป็น success=true
# --------------------------------------------------------------------------- #
class TestD21ValueContract:
    def test_valid_slip_still_passes(self, monkeypatch):
        """คุมไม่ให้ด่านตรวจใหม่ไปปฏิเสธสลิปที่ถูกต้อง"""
        res = _post(monkeypatch, _slip())
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["success"] is True, body
        assert body["amount"] == 1234.56
        assert body["date"] == "2026-08-05"
        assert body["category"] == "ลงทุน"

    @pytest.mark.parametrize(
        "amount",
        [-5000.0, 0, None, float("nan"), float("inf"), 999_999_999_999.0],
        ids=["negative", "zero", "null", "nan", "inf", "absurd"],
    )
    def test_unusable_amount_is_not_reported_as_success(self, monkeypatch, amount):
        # json.dumps ปล่อย NaN/Infinity ออกมาแบบ non-standard ได้ และ json.loads ก็รับกลับได้
        res = _post(monkeypatch, _slip(amount=amount))
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["success"] is False, f"ยอดเงิน {amount!r} ถูกส่งต่อเป็นสำเร็จ: {body}"
        assert body["amount"] is None, body
        assert body["error"], body
        # ข้อความต้องเป็นภาษาไทย (มีอักขระในช่วง ก-๙)
        assert any("฀" <= ch <= "๿" for ch in body["error"]), body["error"]

    def test_all_fields_null_is_not_success(self, monkeypatch):
        res = _post(
            monkeypatch,
            json.dumps(
                {
                    "is_slip": True,
                    "error": None,
                    "amount": None,
                    "date": None,
                    "sender": None,
                    "receiver": None,
                    "category": None,
                }
            ),
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["success"] is False, body
        assert body["error"], body

    @pytest.mark.parametrize("bad_date", ["not-a-date", "2026-13-45", "05/08/2026", "", None])
    def test_unparseable_date_is_not_success(self, monkeypatch, bad_date):
        res = _post(monkeypatch, _slip(date=bad_date))
        body = res.json()
        assert body["success"] is False, f"วันที่ {bad_date!r} ผ่านด่าน: {body}"
        assert body["date"] is None, body
        assert any("฀" <= ch <= "๿" for ch in body["error"] or ""), body

    def test_iso_datetime_date_is_normalised(self, monkeypatch):
        res = _post(monkeypatch, _slip(date="2026-08-05T09:30:00"))
        body = res.json()
        assert body["success"] is True, body
        assert body["date"] == "2026-08-05", body

    @pytest.mark.parametrize("bad_category", ["Food", "หมวดที่ไม่มีจริง", "", None, 7])
    def test_category_outside_declared_set_is_rejected(self, monkeypatch, bad_category):
        """system prompt ประกาศไว้ 4 ค่า — ค่าอื่นคือสัญญาที่ผิด ห้ามส่งต่อเงียบ ๆ"""
        res = _post(monkeypatch, _slip(category=bad_category))
        body = res.json()
        assert body["success"] is False, f"category {bad_category!r} ผ่านด่าน: {body}"
        assert body["error"], body

    def test_declared_categories_match_system_prompt(self):
        for cat in transactions.ALLOWED_CATEGORIES:
            assert cat in transactions._SYSTEM_PROMPT, cat


# --------------------------------------------------------------------------- #
# D2.2 — amount ที่เป็นสตริงต้องไม่ทำให้ทั้ง endpoint เป็น 500
# --------------------------------------------------------------------------- #
class TestD22AmountNormalisation:
    def test_amount_with_thousand_separator_is_parsed(self, monkeypatch):
        res = _post(monkeypatch, _slip(amount="1,234.56"))
        assert res.status_code == 200, res.text  # เดิม: 500 Internal Server Error
        body = res.json()
        assert body["success"] is True, body
        assert body["amount"] == pytest.approx(1234.56), body

    def test_numeric_string_amount_still_works(self, monkeypatch):
        res = _post(monkeypatch, _slip(amount="1234.56"))
        body = res.json()
        assert body["success"] is True, body
        assert body["amount"] == pytest.approx(1234.56), body

    def test_amount_with_currency_symbol_is_parsed(self, monkeypatch):
        res = _post(monkeypatch, _slip(amount="฿ 1,234.56"))
        body = res.json()
        assert body["success"] is True, body
        assert body["amount"] == pytest.approx(1234.56), body

    @pytest.mark.parametrize("junk", ["abc", "-", {"value": 1}, [1, 2]])
    def test_unparseable_amount_is_thai_failure_not_500(self, monkeypatch, junk):
        res = _post(monkeypatch, _slip(amount=junk))
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["success"] is False, body
        assert body["amount"] is None, body
        assert any("฀" <= ch <= "๿" for ch in body["error"] or ""), body

    def test_non_string_sender_does_not_500(self, monkeypatch):
        """ฟิลด์ข้อความที่โมเดลคืนมาเป็นชนิดอื่นต้องไม่ลาก endpoint ลงไปเป็น 500"""
        res = _post(monkeypatch, _slip(sender=12345, receiver={"name": "x"}))
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["success"] is True, body
        assert isinstance(body["sender"], str), body

    def test_non_string_error_field_does_not_500(self, monkeypatch):
        res = _post(monkeypatch, json.dumps({"is_slip": False, "error": {"code": 3}}))
        assert res.status_code == 200, res.text
        assert res.json()["success"] is False


# --------------------------------------------------------------------------- #
# D2.3 — ไฟล์ใหญ่ต้องถูกตัดก่อนโหลดเข้าหน่วยความจำทั้งก้อน
# --------------------------------------------------------------------------- #
class _StubUpload:
    """UploadFile ปลอมที่บันทึกว่า handler ขอข้อมูลกี่ไบต์ต่อครั้ง"""

    def __init__(self, total: int, *, declared_size: int | None) -> None:
        self.content_type = "image/jpeg"
        self.size = declared_size
        self.filename = "slip.jpg"
        self._total = total
        self._pos = 0
        self.read_sizes: list[int | None] = []

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if size is None or size < 0:  # อ่านทั้งก้อน = พฤติกรรมที่ห้ามเกิด
            chunk = b"\x00" * (self._total - self._pos)
        else:
            chunk = b"\x00" * max(0, min(size, self._total - self._pos))
        self._pos += len(chunk)
        return chunk

    @property
    def bytes_read(self) -> int:
        return self._pos


class TestD23Streaming:
    def test_declared_oversize_is_rejected_without_reading_body(self):
        stub = _StubUpload(200 * 1024 * 1024, declared_size=200 * 1024 * 1024)
        with pytest.raises(HTTPException) as exc:
            _run(transactions._read_capped(stub))
        assert exc.value.status_code == 422
        assert "5MB" in exc.value.detail
        assert stub.read_sizes == [], "starlette บอกขนาดมาแล้ว ไม่มีเหตุผลต้องอ่านไฟล์"

    def test_body_is_read_in_bounded_chunks_and_aborted_early(self):
        oversize = transactions.MAX_FILE_SIZE * 3
        stub = _StubUpload(oversize, declared_size=None)  # ไม่ประกาศขนาด = ต้องอ่านแบบมีเพดาน
        with pytest.raises(HTTPException) as exc:
            _run(transactions._read_capped(stub))
        assert exc.value.status_code == 422
        assert all(
            isinstance(n, int) and 0 < n <= transactions.CHUNK_SIZE for n in stub.read_sizes
        ), stub.read_sizes
        # ต้องหยุดทันทีที่เกินเพดาน ไม่ใช่โหลดครบ 15MB ก่อนแล้วค่อยเช็ค
        assert stub.bytes_read <= transactions.MAX_FILE_SIZE + transactions.CHUNK_SIZE, (
            f"อ่านเข้าหน่วยความจำ {stub.bytes_read} ไบต์ ทั้งที่เพดานคือ {transactions.MAX_FILE_SIZE}"
        )

    def test_file_within_limit_is_read_whole(self):
        stub = _StubUpload(1024, declared_size=1024)
        assert _run(transactions._read_capped(stub)) == b"\x00" * 1024

    def test_oversize_upload_over_http_is_422_in_thai(self, monkeypatch):
        res = _post(monkeypatch, _slip(), body=b"\x00" * (transactions.MAX_FILE_SIZE + 1024))
        assert res.status_code == 422, res.text
        assert "5MB" in res.json()["detail"]

    def test_wrong_content_type_is_still_422(self, monkeypatch):
        res = _post(monkeypatch, _slip(), content_type="application/pdf")
        assert res.status_code == 422, res.text


def _run(coro):
    import asyncio

    return asyncio.run(coro)
