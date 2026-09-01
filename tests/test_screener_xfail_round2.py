# -*- coding: utf-8 -*-
"""AUDIT_ROUND2_2026-08-07 — พรีเซ็ต screener ที่ "นิยามผิด" ต้องดัง ห้ามเงียบ.

รอบนี้ตั้งคำถามว่า "xfail 3 ตัวใน ``tests/test_screener_engine.py`` เขียวได้หรือยัง"
คำตอบตอนตรวจคือ **ยัง** และบั๊กหนักกว่าที่เหตุผลใน marker เขียนไว้ — สองอาการที่วัดได้จริง
จาก ``ScreenerEngine`` ตัวจริงในคอนเทนเนอร์:

* **(ก) ชื่อฟิลด์พิมพ์ผิด** (``price_vs_ma_200`` แทน ``price_vs_ma200``)
  → ``results: []`` และ ``errors: []`` ⇒ ช่อง ``errors`` ที่สาย B6 เพิ่มมาเพื่อแยก
  "ตรวจไม่ได้" ออกจาก "ไม่มีสัญญาณ" **ไม่ครอบเคสนี้** ผู้ใช้อ่านหน้าจอได้อย่างเดียวว่า
  "วันนี้ไม่มีอะไรต้องทำ" ทั้งที่พรีเซ็ตนั้นตายไปแล้วตลอดกาล
* **(ข) logic สะกดผิด** (``"XOR"``) กฎผ่าน 1 จาก 2 ข้อ → ``['VOO']``
  ทั้งที่ AND ต้องได้ ``[]`` ⇒ สะกดผิดครั้งเดียวพลิกพรีเซ็ตทั้งใบเป็น OR แล้วยิงสัญญาณซื้อ
  เข้า Telegram ตอน 07:00 จากกฎที่ผ่านแค่ข้อเดียว

ไฟล์นี้ตรึงพฤติกรรมหลังแก้ที่ระดับ ``run()`` และระดับ HTTP (``/api/screener/custom``)
ส่วนเคสระดับ ``_evaluate_rule`` อยู่ใน ``tests/test_screener_engine.py``
(``TestUnknownDefinitions`` — marker ``xfail`` ถูกถอดออกในรอบเดียวกัน)

รวมข้อ [LOW] ``matched_rules`` เป็นสตริงว่างเมื่อผู้เรียกไม่ได้ใส่ ``description``
("มีกฎผ่าน 1 ข้อ แต่บอกไม่ได้ว่าข้อไหน") ไว้ด้วย เพราะเป็นความจริงเรื่องเดียวกัน:
รายงานผลต้องมีเนื้อหาเสมอ

**ห้ามยิงเน็ต** — ทุกเคสสตับ ``_fetch_df`` ด้วยเฟรมสังเคราะห์
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ต้องตั้งก่อน import backend.main — ห้ามให้ชุดเทสต์แตะฐาน SQLite ตัวจริงของผู้ใช้
# (AUDIT_2026-08-06 ข้อ 0.1 — ตาข่ายหลักคือ tests/test_db_isolation.py)
if "/data" in (os.getenv("VAULTIS_DB_PATH") or ""):
    os.environ["VAULTIS_DB_PATH"] = "/tmp/test_vaultis_screener_round2.db"

import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.screener.engine import ScreenerEngine, _rule_label  # noqa: E402
from backend.screener.models import ScreenerPreset, ScreenerRule  # noqa: E402


def _frame(values: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(values), freq="B")
    return pd.DataFrame({"Close": values, "Volume": [1_000_000.0] * len(values)}, index=idx)


def uptrend_frame() -> pd.DataFrame:
    """ขาขึ้น — ราคา 153.30 > MA200 132.65 · RSI ≈ 58.5 (ผ่าน ``price_vs_ma200 gt``)."""
    return _frame([100 + 0.20 * i + (1.5 if i % 2 else 0.0) for i in range(260)])


PASS_RULE = ScreenerRule("price_vs_ma200", "gt", None, "ราคาเหนือ MA200")
FAIL_RULE = ScreenerRule("price_vs_ma200", "lt", None, "ราคาต่ำกว่า MA200")
# ชื่อฟิลด์ที่พิมพ์ผิดจริงตามหลักฐานในรายงาน (ขาดตัว _ เกินมาหนึ่งตัว)
TYPO_RULE = ScreenerRule("price_vs_ma_200", "gt", None, "ราคาเหนือ MA200 (พิมพ์ชื่อฟิลด์ผิด)")


def _preset(logic: str, rules: list[ScreenerRule], name: str = "unit") -> ScreenerPreset:
    return ScreenerPreset(name=name, rules=rules, logic=logic, description="เทสต์")


@pytest.fixture
def engine(monkeypatch):
    """เอนจินที่ ``_fetch_df`` ถูกสตับ — ตั้งข้อมูลรายสัญลักษณ์ผ่าน ``.frames``."""
    eng = ScreenerEngine()
    frames: dict[str, pd.DataFrame] = {}

    def _fake_fetch(symbol: str) -> pd.DataFrame:
        if symbol not in frames:
            raise ValueError(f"ดึงข้อมูลราคา {symbol} ไม่สำเร็จ (ผลว่าง)")
        return frames[symbol]

    monkeypatch.setattr(eng, "_fetch_df", _fake_fetch)
    eng.frames = frames  # type: ignore[attr-defined]
    return eng


class TestPresetWithATypoIsReportedNotSilent:
    """อาการ (ก): พรีเซ็ตที่พิมพ์ชื่อฟิลด์ผิดต้องไม่กลายเป็น "ไม่มีสัญญาณ" เงียบ ๆ."""

    def test_typo_field_lands_in_errors_not_in_silence(self, engine):
        engine.frames["VOO"] = uptrend_frame()
        results = engine.run(["VOO"], _preset("AND", [TYPO_RULE]))
        assert list(results) == [], "กฎที่ประเมินไม่ได้ต้องไม่กลายเป็นสัญญาณ"
        assert results.errors, (
            "หลักฐานในรายงาน: results: [] errors: [] — ไม่มีสัญญาณใดบอกผู้ใช้ว่าพรีเซ็ตพัง"
        )
        assert any("VOO" in e and "price_vs_ma_200" in e for e in results.errors)

    def test_error_message_names_the_supported_fields(self, engine):
        """ข้อความต้องช่วยให้แก้ถูกที่ ไม่ใช่แค่บอกว่าพัง."""
        engine.frames["VOO"] = uptrend_frame()
        errors = engine.run(["VOO"], _preset("AND", [TYPO_RULE])).errors
        assert any("price_vs_ma200" in e for e in errors), errors

    def test_typo_operator_lands_in_errors_too(self, engine):
        engine.frames["VOO"] = uptrend_frame()
        rule = ScreenerRule("rsi", "less_than", 70, "RSI ต่ำกว่า 70")
        results = engine.run(["VOO"], _preset("AND", [rule]))
        assert list(results) == []
        assert any("less_than" in e for e in results.errors), results.errors

    def test_a_broken_rule_does_not_let_an_or_preset_fire(self, engine):
        """OR + กฎที่ประเมินไม่ได้ = "ตรวจไม่ได้" ทั้งสัญลักษณ์ ห้ามเคลมว่าผ่านเพราะอีกข้อ."""
        engine.frames["VOO"] = uptrend_frame()
        results = engine.run(["VOO"], _preset("OR", [PASS_RULE, TYPO_RULE]))
        assert list(results) == []
        assert results.errors

    def test_every_symbol_is_reported_not_just_the_first(self, engine):
        engine.frames["VOO"] = uptrend_frame()
        engine.frames["QQQM"] = uptrend_frame()
        results = engine.run(["VOO", "QQQM"], _preset("AND", [TYPO_RULE]))
        assert len(results.errors) == 2, results.errors

    def test_a_working_preset_still_reports_no_errors(self, engine):
        """ตาข่ายกันเผลอ: ต้องไม่กลายเป็น "มี error เสมอ" (ไม่งั้นเคสข้างบนไม่มีความหมาย)."""
        engine.frames["VOO"] = uptrend_frame()
        results = engine.run(["VOO"], _preset("AND", [PASS_RULE]))
        assert [r.symbol for r in results] == ["VOO"]
        assert list(results.errors) == []


class TestLogicMustBeAndOrExplicitly:
    """อาการ (ข): ``logic`` ที่ไม่ใช่ AND/OR ห้ามถูกตีความเป็น OR."""

    def test_misspelled_logic_raises_instead_of_becoming_or(self, engine):
        """หลักฐานในรายงาน: ``logic='XOR'`` กฎผ่าน 1/2 → ติดสัญญาณ ``['VOO']``."""
        engine.frames["VOO"] = uptrend_frame()
        with pytest.raises(ValueError, match="AND"):
            engine.run(["VOO"], _preset("XOR", [PASS_RULE, FAIL_RULE]))

    def test_empty_logic_raises(self, engine):
        engine.frames["VOO"] = uptrend_frame()
        with pytest.raises(ValueError):
            engine.run(["VOO"], _preset("", [PASS_RULE, FAIL_RULE]))

    def test_lowercase_and_still_means_and(self, engine):
        """สะกดถูกแต่คนละรูปแบบ = รับได้ — และต้อง **ไม่** กลายเป็น OR."""
        engine.frames["VOO"] = uptrend_frame()
        assert list(engine.run(["VOO"], _preset(" and ", [PASS_RULE, FAIL_RULE]))) == []

    def test_lowercase_or_still_means_or(self, engine):
        engine.frames["VOO"] = uptrend_frame()
        results = engine.run(["VOO"], _preset("or", [PASS_RULE, FAIL_RULE]))
        assert [r.symbol for r in results] == ["VOO"]

    def test_a_preset_with_no_rules_never_passes_everything(self, engine):
        """``all([])`` เป็น True — พรีเซ็ตเปล่ากับ AND จึง "ผ่าน" ทุกตัวโดยไม่ได้ตรวจอะไร."""
        engine.frames["VOO"] = uptrend_frame()
        with pytest.raises(ValueError, match="ไม่มีกฎ"):
            engine.run(["VOO"], _preset("AND", []))

    def test_bad_definition_is_raised_before_any_price_is_touched(self, engine):
        """นิยามผิด = ทั้งพรีเซ็ตใช้ไม่ได้ ต้องดังก่อน ไม่ใช่ไปนอนใน ``.errors`` รายสัญลักษณ์."""

        def _boom(symbol):  # pragma: no cover - ต้องไม่ถูกเรียก
            raise AssertionError("ต้องตรวจ logic ก่อนแตะข้อมูลราคา")

        engine._fetch_df = _boom  # type: ignore[method-assign]
        with pytest.raises(ValueError):
            engine.run(["VOO"], _preset("XOR", [PASS_RULE]))


class TestShippedPresetsUseTheKnownVocabulary:
    """ตาข่ายรุ่นถัดไป: พิมพ์ผิดใน ``presets.py`` ต้องแดงในชุดเทสต์ ไม่ใช่เงียบไปทั้งปี."""

    def test_every_shipped_rule_is_evaluatable(self):
        from backend.screener.engine import _ALLOWED_OPERATORS
        from backend.screener.presets import PRESETS

        for name, preset in PRESETS.items():
            for rule in preset.rules:
                assert rule.field in _ALLOWED_OPERATORS, f"{name}: ฟิลด์ {rule.field!r} ไม่มีตัวประเมิน"
                assert rule.operator in _ALLOWED_OPERATORS[rule.field], (
                    f"{name}: {rule.field!r} ไม่รองรับ {rule.operator!r}"
                )

    def test_every_shipped_preset_runs_without_a_definition_error(self, engine):
        """รันพรีเซ็ตจริงทุกใบบนเฟรมสังเคราะห์ — ต้องไม่มี error เรื่อง "นิยาม"."""
        from backend.screener.presets import PRESETS

        engine.frames["VOO"] = uptrend_frame()
        for name, preset in PRESETS.items():
            errors = engine.run(["VOO"], preset).errors
            assert not any("ไม่รู้จัก" in e or "ไม่รองรับ" in e for e in errors), f"{name}: {errors}"

    def test_every_shipped_rule_has_a_description(self):
        """เหตุผลที่แสดงต่อผู้ใช้ควรเป็นภาษาคน ไม่ใช่ข้อความสำรองที่ประกอบจากชื่อฟิลด์."""
        from backend.screener.presets import PRESETS

        for name, preset in PRESETS.items():
            for rule in preset.rules:
                assert rule.description.strip(), f"{name}: กฎ {rule.field!r} ไม่มีคำอธิบาย"


class TestMatchedRulesAlwaysCarryAReason:
    """[LOW] ``matched_rules`` ต้องไม่มีสมาชิกที่เป็นสตริงว่าง."""

    def test_rule_without_description_gets_a_generated_label(self, engine):
        engine.frames["VOO"] = uptrend_frame()
        rule = ScreenerRule("price_vs_ma200", "gt", None, "")
        results = engine.run(["VOO"], _preset("AND", [rule]))
        assert results[0].matched_rules == ["price_vs_ma200 gt"]

    def test_whitespace_only_description_counts_as_missing(self, engine):
        engine.frames["VOO"] = uptrend_frame()
        rule = ScreenerRule("rsi", "lt", 70, "   ")
        results = engine.run(["VOO"], _preset("AND", [rule]))
        assert results[0].matched_rules == ["rsi lt 70"]

    def test_caller_description_is_kept_verbatim(self, engine):
        engine.frames["VOO"] = uptrend_frame()
        results = engine.run(["VOO"], _preset("AND", [PASS_RULE]))
        assert results[0].matched_rules == ["ราคาเหนือ MA200"]

    def test_no_matched_rule_is_ever_blank(self, engine):
        engine.frames["VOO"] = uptrend_frame()
        rules = [ScreenerRule("price_vs_ma200", "gt", None, ""), ScreenerRule("rsi", "lt", 70, "")]
        results = engine.run(["VOO"], _preset("AND", rules))
        assert all(label.strip() for label in results[0].matched_rules)

    def test_label_helper_never_returns_empty(self):
        """แม้กฎเปล่าสนิท (ซึ่ง ``_evaluate_rule`` จะปฏิเสธอยู่แล้ว) ป้ายก็ต้องมีเนื้อหา."""
        assert _rule_label(ScreenerRule("", "", None, "")).strip()


class TestCustomEndpointExplainsWhichRuleMatched:
    """ระดับ HTTP — หลักฐานในรายงานเป็น response ของ ``/api/screener/custom``."""

    @pytest.fixture
    def client(self, monkeypatch):
        from backend.main import app
        from backend.routers import screener as screener_router
        from backend.security import require_api_key

        def _fetch(symbol: str) -> pd.DataFrame:
            if symbol == "VOO":
                return uptrend_frame()
            raise ValueError(f"ดึงข้อมูลราคา {symbol} ไม่สำเร็จ (ผลว่าง)")

        monkeypatch.setattr(screener_router._engine, "_fetch_df", _fetch)
        app.dependency_overrides[require_api_key] = lambda: None
        # ไม่ใช้ ``with TestClient(app)`` เพราะนั่นจะรัน lifespan = สตาร์ท APScheduler จริง
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.pop(require_api_key, None)

    def _post(self, client, rules, logic="AND", symbols=("VOO",)):
        return client.post(
            "/api/screener/custom",
            json={"symbols": list(symbols), "rules": rules, "logic": logic},
        )

    def test_missing_description_still_says_which_rule_matched(self, client):
        """หลักฐาน: ไม่ใส่ description → ``"matched_rules": [""]`` ทั้ง 5 สัญลักษณ์."""
        resp = self._post(client, [{"field": "rsi", "operator": "lt", "value": 70}])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["results"], body
        matched = body["results"][0]["matched_rules"]
        assert matched and all(m.strip() for m in matched), f"บุลเล็ตเปล่า: {matched}"
        assert matched == ["rsi lt 70"]

    def test_null_description_does_not_become_the_string_none(self, client):
        """``str(rule.get("description", ""))`` ให้ ``"None"`` เมื่อ JSON ส่ง null มา."""
        resp = self._post(client, [{"field": "rsi", "operator": "lt", "value": 70, "description": None}])
        assert resp.status_code == 200, resp.text
        matched = resp.json()["results"][0]["matched_rules"]
        assert "None" not in matched, matched
        assert matched == ["rsi lt 70"]

    def test_given_description_is_used(self, client):
        resp = self._post(
            client, [{"field": "rsi", "operator": "lt", "value": 90, "description": "RSI ต่ำกว่า 90"}]
        )
        assert resp.json()["results"][0]["matched_rules"] == ["RSI ต่ำกว่า 90"]

    def test_unknown_field_answers_with_errors_not_an_empty_result(self, client):
        resp = self._post(client, [{"field": "price_vs_ma_200", "operator": "gt"}])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_signals"] == 0
        assert body["errors"], "พรีเซ็ตพังแต่ response บอกแค่ว่าไม่มีสัญญาณ"

    def test_bad_logic_is_a_400_not_a_silent_or(self, client):
        resp = self._post(
            client,
            [
                {"field": "price_vs_ma200", "operator": "gt", "description": "เหนือ MA200"},
                {"field": "price_vs_ma200", "operator": "lt", "description": "ต่ำกว่า MA200"},
            ],
            logic="XOR",
        )
        assert resp.status_code == 400, resp.text
        assert "AND" in resp.json()["detail"]
