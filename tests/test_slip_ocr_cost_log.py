# -*- coding: utf-8 -*-
"""AUDIT_ROUND2_2026-08-07 — เงินที่จ่ายให้ slip OCR ต้องโผล่ใน log ต้นทุน.

**อาการ** CLAUDE.md บังคับว่าการเรียก LLM ทุกครั้งต้องผ่าน ``analysis/llm.chat_text()``
โดยมีข้อยกเว้นเดียวคือ slip OCR ใน ``backend/routers/transactions.py`` ที่ต้องใช้ vision
(``chat_text()`` รับแต่ข้อความ ส่งรูปไม่ได้) — แต่เพราะข้ามชั้นนั้นไป มันจึงข้าม
``_log_cost()`` ไปด้วย ⇒ **เงินที่จ่ายให้ OCR ทุกใบไม่เคยโผล่ใน log ต้นทุนเลย**
ผู้ใช้เห็นค่าใช้จ่ายของระบบต่ำกว่าจริงมาตลอด (log คือหลักฐานชิ้นเดียวที่บอกว่าจ่ายไปเท่าไร)

**สิ่งที่ไฟล์นี้ตรึงไว้ 3 ชั้น**

1. ``log_anthropic_usage()`` — "ไม่ทราบต้นทุน" ต้องไม่กลายเป็น ``$0.0000`` (กฎ C1:
   ความล้มเหลวห้ามกลายเป็นตัวเลข) และการบันทึกต้นทุนต้องไม่โยน exception ใส่คำขอที่
   สำเร็จไปแล้ว — ผู้ใช้อ่านสลิปได้อยู่แล้ว การ log พังต้องไม่ลากคำขอนั้นล้มตาม
2. ``OCR_MODEL`` ต้องมีอยู่ในตาราง ``_MODEL_PRICES_USD_PER_MTOK`` เสมอ — ชื่อนี้เป็นทั้ง
   "โมเดลที่ยิงจริง" และ "คีย์เปิดตารางราคา" เปลี่ยนที่เดียวแล้ว log จะรายงานราคาของ
   โมเดลผิดตัวโดยไม่มีอะไรร้อง
3. เส้นทาง OCR จริงต้องบันทึก **ทุกครั้งที่เงินออก** รวมถึงใบที่ parse ไม่ผ่าน ซึ่งเป็น
   เคสที่เสียเงินเท่ากันทุกบาท — โค้ดจึงเรียก log ก่อนแตะผลลัพธ์ และเทสต์ตรึงลำดับนั้นไว้

ทุกเคส stub ไคลเอนต์ Anthropic — **ห้ามยิงจริง** (คอนเทนเนอร์เทสต์โหลด ``.env``
ที่มี ``ANTHROPIC_API_KEY`` จริงอยู่ = เงินออกจริงถ้าพลาด)
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ``backend.*`` แตะฐาน SQLite ตอน import ในบางเส้นทาง — ชี้ไป tmp ไม่ให้แตะฐานจริงของผู้ใช้
os.environ.setdefault("VAULTIS_DB_PATH", str(Path(tempfile.gettempdir()) / "vaultis_test_slip.db"))

from analysis import llm  # noqa: E402
from backend.routers import transactions  # noqa: E402

_LLM_LOGGER = llm.logger.name

# เลขเงินในข้อความ log เช่น ``$0.0004`` — ใช้ตรวจว่า "ไม่ทราบต้นทุน" ไม่ได้ถูกเขียนเป็นตัวเลข
_MONEY_RE = re.compile(r"\$\s*[\d.]+")


# --------------------------------------------------------------------------- #
# ของปลอม — ไม่มีทางหลุดไปเรียก Anthropic จริง
# --------------------------------------------------------------------------- #
class _Usage:
    """usage object แบบที่ SDK คืนมา (มีแค่ 2 ฟิลด์ที่โค้ดอ่านจริง)"""

    def __init__(self, input_tokens=1200, output_tokens=340) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    def __repr__(self) -> str:  # ให้ log อ่านออกตอนเป็นเคสผิดปกติ
        return f"Usage(in={self.input_tokens!r}, out={self.output_tokens!r})"


class _EmptyUsage:
    """object ที่ไม่มีฟิลด์โทเคนเลย — provider เปลี่ยนสัญญา/SDK คนละรุ่น"""


class _Block:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _ExplodingBlock:
    """บล็อกที่ระเบิดทันทีที่มีคนอ่านเนื้อหา — ใช้ตรึง "log ก่อน parse"."""

    type = "text"

    @property
    def text(self) -> str:
        raise RuntimeError("โค้ดแตะผลลัพธ์ก่อนบันทึกต้นทุน")


_DEFAULT_USAGE = object()


class _FakeResponse:
    """response ปลอม — ``with_usage=False`` จำลอง provider ที่ไม่ส่งฟิลด์ usage กลับมาเลย"""

    def __init__(
        self, blocks: list, usage: object = _DEFAULT_USAGE, *, with_usage: bool = True
    ) -> None:
        self.content = blocks
        if with_usage:
            self.usage = _Usage() if usage is _DEFAULT_USAGE else usage


class _FakeMessages:
    def __init__(self, response) -> None:
        self._response = response
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class _FakeClient:
    def __init__(self, response) -> None:
        self.messages = _FakeMessages(response)


class _StubUpload:
    """UploadFile ปลอมขนาดเล็ก — ผ่านด่านชนิดไฟล์/ขนาดของ ``_read_capped`` ตามปกติ"""

    def __init__(self, content_type: str = "image/jpeg") -> None:
        self.content_type = content_type
        self.filename = "slip.jpg"
        self._body = b"\xff\xd8\xff\xe0" + b"0" * 64
        self.size = len(self._body)
        self._pos = 0

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self._body) - self._pos
        chunk = self._body[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk


@pytest.fixture(autouse=True)
def _never_call_anthropic(monkeypatch):
    """กันพลาดขั้นสุดท้าย: เคสไหนลืม stub ให้ระเบิดแทนที่จะเสียเงินจริง"""
    monkeypatch.setattr(transactions, "_client", None)
    monkeypatch.setattr(
        transactions,
        "_get_client",
        lambda: pytest.fail("เทสต์เรียก Anthropic จริง — ต้อง stub ทุกเคส"),
    )


def _install_client(monkeypatch, response) -> _FakeClient:
    fake = _FakeClient(response)
    monkeypatch.setattr(transactions, "_client", fake)
    monkeypatch.setattr(transactions, "_get_client", lambda: fake)
    return fake


def _slip_json(**overrides) -> str:
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


def _llm_records(caplog) -> list[logging.LogRecord]:
    return [rec for rec in caplog.records if rec.name == _LLM_LOGGER]


def _llm_text(caplog) -> str:
    return "\n".join(rec.getMessage() for rec in _llm_records(caplog))


# --------------------------------------------------------------------------- #
# 1. log_anthropic_usage() — ทางเข้าเดียวของการเรียก Anthropic ที่ไม่ผ่าน chat_text()
# --------------------------------------------------------------------------- #
class TestUsageIsReportedAsMoney:
    def test_normal_usage_logs_model_tokens_and_cost(self, caplog):
        with caplog.at_level(logging.INFO, logger=_LLM_LOGGER):
            llm.log_anthropic_usage("claude-haiku-4-5", _Usage(1_000_000, 1_000_000))

        records = _llm_records(caplog)
        assert len(records) == 1, records
        message = records[0].getMessage()
        assert records[0].levelno == logging.INFO, message
        assert "claude-haiku-4-5" in message, message
        assert "1000000" in message, f"ต้องบอกจำนวนโทเคนที่ใช้จริง: {message}"
        # อ่านราคาจากตารางแทนการฮาร์ดโค้ดตัวเลข: เทสต์นี้ตรึง "ค่าใช้จ่ายมาจากตารางราคา"
        # ไม่ใช่ตรึงราคาของ Anthropic (ซึ่งเปลี่ยนได้และไม่ใช่บั๊ก)
        price_in, price_out = llm._MODEL_PRICES_USD_PER_MTOK["claude-haiku-4-5"]
        expected = price_in + price_out  # 1 ล้านโทเคนต่อฝั่งพอดี
        assert f"${expected:.4f}" in message, f"ตัวเลขค่าใช้จ่ายไม่ตรงตารางราคา: {message}"
        assert "บาท" in message, "ผู้ใช้เป็นคนไทย — ต้องมีตัวเลขบาทให้เทียบด้วย"

    def test_label_identifies_which_caller_spent_the_money(self, caplog):
        """log รวมของทั้งระบบ — ถ้าไม่บอกว่าเป็น OCR ก็แยกไม่ออกว่าเงินก้อนไหนของใคร"""
        with caplog.at_level(logging.INFO, logger=_LLM_LOGGER):
            llm.log_anthropic_usage("claude-haiku-4-5", _Usage(), label="slip OCR")

        assert "slip OCR" in _llm_text(caplog), _llm_text(caplog)

    def test_zero_tokens_reported_by_provider_is_still_a_number(self, caplog):
        """0 ที่ provider **ส่งมาจริง** เป็นข้อมูล ไม่ใช่ความล้มเหลว — ต้องต่างจาก "ไม่ทราบ"."""
        with caplog.at_level(logging.INFO, logger=_LLM_LOGGER):
            llm.log_anthropic_usage("claude-haiku-4-5", _Usage(0, 0))

        records = _llm_records(caplog)
        assert [rec.levelno for rec in records] == [logging.INFO], _llm_text(caplog)
        assert "$0.0000" in records[0].getMessage(), records[0].getMessage()


class TestUnknownCostNeverBecomesZero:
    """C1 — "ไม่ได้รับจำนวนโทเคน" ≠ "ใช้ไป 0 โทเคน" ≠ "ไม่มีค่าใช้จ่าย"."""

    # bool เป็น subclass ของ int ใน Python — ``isinstance(True, int)`` เป็นจริง
    # ถ้าไม่กันไว้ ``True`` จะถูกอ่านเป็น 1 โทเคนแล้วกลายเป็นตัวเลขค่าใช้จ่ายที่แต่งขึ้น
    @pytest.mark.parametrize(
        "usage",
        [
            None,
            _EmptyUsage(),
            _Usage(None, None),
            _Usage(500, None),
            _Usage(None, 500),
            _Usage(-1, 20),
            _Usage(10, -20),
            _Usage(True, True),
            _Usage(False, False),
            _Usage(True, 20),
            _Usage("1200", "340"),
            _Usage({"input": 5}, [340]),
        ],
        ids=[
            "none",
            "no-attrs",
            "both-null",
            "output-null",
            "input-null",
            "negative-input",
            "negative-output",
            "bool-true",
            "bool-false",
            "bool-mixed",
            "strings",
            "junk-types",
        ],
    )
    def test_missing_or_bogus_tokens_warn_without_inventing_a_price(self, caplog, usage):
        with caplog.at_level(logging.INFO, logger=_LLM_LOGGER):
            llm.log_anthropic_usage("claude-haiku-4-5", usage, label="slip OCR")

        records = _llm_records(caplog)
        assert records, "เงินออกไปแล้วแต่ log เงียบสนิท — ต้องเตือนว่าไม่ทราบต้นทุน"
        assert any(rec.levelno >= logging.WARNING for rec in records), (
            f"ต้องเป็น WARNING ไม่ใช่บรรทัดปกติ: {_llm_text(caplog)}"
        )
        text = _llm_text(caplog)
        assert not _MONEY_RE.search(text), (
            f"ไม่ทราบต้นทุนแล้วยังพิมพ์ตัวเลขเงินออกมา (C1: ความล้มเหลวห้ามกลายเป็นตัวเลข): {text}"
        )
        assert "claude-haiku-4-5" in text, text
        assert "slip OCR" in text, text

    @pytest.mark.parametrize(
        "usage",
        [None, _EmptyUsage(), _Usage(None, None), _Usage(-5, -5), _Usage(True, False), object()],
    )
    def test_logging_never_raises_into_a_request_that_already_succeeded(self, usage):
        """สลิปอ่านสำเร็จแล้ว — การบันทึกต้นทุนพังต้องไม่ลากคำขอนั้นล้มตาม"""
        assert llm.log_anthropic_usage("claude-haiku-4-5", usage, label="slip OCR") is None

    def test_unknown_model_says_so_instead_of_reporting_zero_dollars(self, caplog):
        """โมเดลที่ไม่มีในตารางราคา = ไม่ทราบราคา ห้ามคิดเป็น $0.0000 (ซึ่งอ่านว่า "ฟรี")"""
        with caplog.at_level(logging.INFO, logger=_LLM_LOGGER):
            llm.log_anthropic_usage("claude-รุ่นที่ยังไม่ได้ใส่ราคา", _Usage(1000, 200))

        text = _llm_text(caplog)
        assert text, "ต้องยังบอกจำนวนโทเคนที่ใช้ไป แม้ไม่รู้ราคา"
        assert not _MONEY_RE.search(text), f"รายงานเป็นตัวเลขเงินทั้งที่ไม่รู้ราคา: {text}"
        assert "ไม่ทราบราคา" in text, text
        assert "1000" in text and "200" in text, f"โทเคนที่ใช้จริงยังต้องบันทึกไว้: {text}"


# --------------------------------------------------------------------------- #
# 2. ชื่อโมเดลกับตารางราคา — สองที่ที่ต้องตรงกันเสมอ
# --------------------------------------------------------------------------- #
class TestOcrModelIsPriced:
    """เทสต์ที่สำคัญที่สุดของไฟล์นี้.

    ``OCR_MODEL`` ถูกใช้สองบทบาทพร้อมกัน: ชื่อโมเดลที่ยิงไป Anthropic จริง และคีย์ที่เปิด
    ตาราง ``_MODEL_PRICES_USD_PER_MTOK`` ถ้าเปลี่ยนที่เดียว (อัปเกรดโมเดลแล้วลืมตาราง)
    ระบบจะยังทำงานได้ปกติทุกอย่าง เพียงแต่ log บอกว่า "ไม่ทราบราคา" ตลอดกาล — ไม่มีอะไรร้อง
    """

    def test_ocr_model_exists_in_the_price_table(self):
        assert transactions.OCR_MODEL in llm._MODEL_PRICES_USD_PER_MTOK, (
            f"{transactions.OCR_MODEL!r} ไม่มีในตารางราคาของ analysis/llm.py — "
            "ค่าใช้จ่ายของ slip OCR จะถูก log ว่า 'ไม่ทราบราคา' ทุกใบ "
            f"(ตารางมี: {sorted(llm._MODEL_PRICES_USD_PER_MTOK)})"
        )

    def test_price_entry_is_a_real_price_not_a_placeholder(self):
        """ใส่ ``(0.0, 0.0)`` เพื่อให้ผ่านด่านบนได้ แต่นั่นแปลว่า "ฟรี" ซึ่งไม่จริง"""
        price_in, price_out = llm._MODEL_PRICES_USD_PER_MTOK[transactions.OCR_MODEL]
        assert price_in > 0 and price_out > 0, (price_in, price_out)

    async def test_the_model_that_is_billed_is_the_same_constant_that_is_logged(
        self, monkeypatch
    ):
        """คำขอที่ยิงจริงกับชื่อที่ส่งเข้า log ต้องมาจากค่าคงที่ตัวเดียวกัน"""
        logged: list[tuple] = []
        monkeypatch.setattr(
            transactions,
            "log_anthropic_usage",
            lambda model, usage, **kwargs: logged.append((model, usage, kwargs)),
        )
        fake = _install_client(monkeypatch, _FakeResponse([_Block(_slip_json())]))

        await transactions.upload_slip(_StubUpload())

        assert fake.messages.calls[0]["model"] == transactions.OCR_MODEL
        assert logged[0][0] == fake.messages.calls[0]["model"], (
            f"ยิงโมเดล {fake.messages.calls[0]['model']!r} แต่ log ราคาของ {logged[0][0]!r}"
        )


# --------------------------------------------------------------------------- #
# 3. เส้นทาง OCR จริง — ทุกใบที่เสียเงินต้องถูกบันทึก
# --------------------------------------------------------------------------- #
class TestEveryPaidCallIsLogged:
    async def test_successful_slip_puts_a_cost_line_in_the_log(self, monkeypatch, caplog):
        """เส้นทางเต็มโดยไม่ stub ตัว log — พิสูจน์ว่าเงินก้อนนี้โผล่ใน log ต้นทุนจริง"""
        _install_client(monkeypatch, _FakeResponse([_Block(_slip_json())], _Usage(1500, 120)))

        with caplog.at_level(logging.INFO, logger=_LLM_LOGGER):
            result = await transactions.upload_slip(_StubUpload())

        assert result.success is True, result
        text = _llm_text(caplog)
        assert transactions.OCR_MODEL in text, f"log ต้นทุนไม่มีการเรียก OCR เลย: {text!r}"
        assert "1500" in text and "120" in text, text
        assert _MONEY_RE.search(text), f"ไม่มีตัวเลขค่าใช้จ่ายใน log: {text!r}"
        assert "slip OCR" in text, text

    @pytest.mark.parametrize(
        "model_output",
        [
            _slip_json(),
            "{ไม่ใช่ JSON เลย",
            "```json\n{\"is_slip\": true,\n```",
            json.dumps(["ไม่ใช่ dict"]),
            _slip_json(amount=None),
            _slip_json(amount=-500),
            _slip_json(date="05/08/2026"),
            _slip_json(category="Food"),
            json.dumps({"is_slip": False, "error": "รูปไม่ชัด"}, ensure_ascii=False),
            "",
        ],
        ids=[
            "valid",
            "broken-json",
            "truncated-fence",
            "not-a-dict",
            "null-amount",
            "negative-amount",
            "unparseable-date",
            "bad-category",
            "not-a-slip",
            "empty-text",
        ],
    )
    async def test_cost_is_logged_even_when_the_result_is_unusable(
        self, monkeypatch, model_output
    ):
        """ใบที่ parse ไม่ผ่านเสียเงินเท่ากับใบที่สำเร็จ — จะหายไปจาก log ไม่ได้"""
        logged: list[tuple] = []
        monkeypatch.setattr(
            transactions,
            "log_anthropic_usage",
            lambda model, usage, **kwargs: logged.append((model, usage, kwargs)),
        )
        _install_client(monkeypatch, _FakeResponse([_Block(model_output)], _Usage(900, 40)))

        await transactions.upload_slip(_StubUpload())

        assert len(logged) == 1, f"ควรบันทึกต้นทุนครั้งเดียวต่อคำขอ แต่ได้ {len(logged)} ครั้ง"
        model, usage, kwargs = logged[0]
        assert model == transactions.OCR_MODEL
        assert getattr(usage, "input_tokens", None) == 900, "ต้องส่ง usage จริงเข้าไป ไม่ใช่ค่าว่าง"
        assert kwargs.get("label"), "ต้องระบุ label เพื่อแยกว่าเงินก้อนนี้เป็นของ OCR"

    async def test_cost_is_logged_before_the_response_is_touched(self, monkeypatch):
        """ลำดับสำคัญ: เงินออกตั้งแต่ API ตอบกลับ ไม่ใช่ตอน parse สำเร็จ.

        ถ้ามีใครย้ายบรรทัด log ไปไว้หลังการ parse (หรือไปอยู่ในสาขาที่สำเร็จเท่านั้น)
        เคสนี้จะแดง เพราะบล็อกข้อความระเบิดตั้งแต่ถูกอ่านครั้งแรก
        """
        logged: list[tuple] = []
        monkeypatch.setattr(
            transactions,
            "log_anthropic_usage",
            lambda model, usage, **kwargs: logged.append((model, usage, kwargs)),
        )
        _install_client(monkeypatch, _FakeResponse([_ExplodingBlock()], _Usage(700, 10)))

        with pytest.raises(RuntimeError):
            await transactions.upload_slip(_StubUpload())

        assert logged, "คำขอนี้จ่ายเงินไปแล้วแต่ล้มตอน parse — ต้นทุนต้องถูกบันทึกไปก่อนแล้ว"
        assert logged[0][0] == transactions.OCR_MODEL

    async def test_response_without_usage_warns_but_still_returns_the_slip(
        self, monkeypatch, caplog
    ):
        """provider ไม่ส่งโทเคนกลับมา = ไม่ทราบต้นทุน แต่ผู้ใช้ต้องยังได้ผลอ่านสลิปตามปกติ"""
        _install_client(monkeypatch, _FakeResponse([_Block(_slip_json())], with_usage=False))

        with caplog.at_level(logging.INFO, logger=_LLM_LOGGER):
            result = await transactions.upload_slip(_StubUpload())

        assert result.success is True, result
        assert result.amount == pytest.approx(1234.56)
        text = _llm_text(caplog)
        assert any(rec.levelno >= logging.WARNING for rec in _llm_records(caplog)), text
        assert not _MONEY_RE.search(text), f"ไม่รู้ต้นทุนแล้วยังพิมพ์ตัวเลขเงิน: {text}"
