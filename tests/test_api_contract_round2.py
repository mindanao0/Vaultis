# -*- coding: utf-8 -*-
"""AUDIT_ROUND2_2026-08-07 — สัญญาของ API ตอนอินพุตเสียหรือฐานข้อมูลเป็น NULL.

สี่ข้อในไฟล์นี้เป็นอาการเดียวกันทั้งหมด: **API เอาความผิดของผู้เรียก (หรือ "ไม่รู้" ของ
ฐานข้อมูล) ไปเล่าเป็นอย่างอื่น** — เป็นความล้มเหลวของแหล่งข้อมูล เป็นเซิร์ฟเวอร์พัง
หรือแย่ที่สุดคือเป็นตัวเลขที่ดูใช้ได้

- **832** ``POST /api/backtest`` รับ ``start="banana"`` ผ่าน Pydantic → ยิงเน็ตซ้ำ 3 ครั้ง
  → ตอบ 503 "ดึงราคาไม่สำเร็จ" ทั้งที่แหล่งข้อมูลไม่ได้เป็นอะไรเลย
- **1416** ``/api/sentiment/{symbol}`` แปลง NULL เป็น ``0`` / ``"neutral"`` ด้วยสำนวน ``or``
  ⇒ "ยังไม่มีผลวิเคราะห์" กลายเป็น "ตลาดเฉย ๆ ความเชื่อมั่น 0%"
- **1439** ``inf``/``NaN`` ใน ``/api/cashflow/scenario`` ไหลไปตายตอน serialize แล้วถูก
  ``except ValueError`` ของ router แปลงเป็น 422 ที่ ``detail`` เป็นสตริงอังกฤษ ผิดจาก
  openapi ที่ประกาศว่า ``detail`` เป็น array ของ object
- **1468** งบต่ำกว่า 100 บาท ได้ 500 ที่ ``/api/ai/advice`` แต่ได้ 422 ที่
  ``/api/analysis/full`` — อินพุตเดียวกัน สองคำตอบ

ทุกเคส **ไม่แตะเน็ตและไม่เรียก LLM**: ตัว engine / ``get_monthly_advice`` ถูก stub และ
หลายเคสตรึงไว้ตรง ๆ ว่า stub นั้น **ต้องไม่ถูกเรียกเลย** เพราะประเด็นของข้อ 832 คือ
"คำขอที่ใช้ไม่ได้ต้องไม่กลายเป็นการยิงเน็ต"

รอบเก็บตก (ท้ายไฟล์) — สองรูที่การแก้รอบแรกเปิดค้างไว้ที่ชั้น API:

- **1468/2** ``GET /api/analysis/full`` ยังถือด่านงบของตัวเอง (``ge=1`` + ประโยคไทยที่ลอกไป
  เขียนซ้ำ) ⇒ ``budget_thb=inf`` เดินผ่านทั้งสองเงื่อนไขไปตายเป็น 500 อังกฤษ ขณะที่
  ``POST /api/ai/advice`` ตอบ 422 ไทย
- **38/2** ``NoTargetForSubset`` (ตั้งใจไม่ได้สืบจาก ``InvalidTargetWeights``) ไม่มีใครดัก
  ที่ router จึงออกไปเป็น HTTP 500 เปล่า ๆ = "ดึงราคาไม่สำเร็จ" ถูกเล่าเป็น "เซิร์ฟเวอร์พัง"
  ซึ่งคือบั๊กเดิมของ G1 ย้ายมาโผล่อีกชั้นหนึ่ง
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ``backend.database`` สร้างไฟล์ SQLite ตอน import — ชี้ไป tmp เสมอ (ข้อ 0-A / H1)
os.environ.setdefault(
    "VAULTIS_DB_PATH", str(Path(tempfile.gettempdir()) / "vaultis_test_api_contract.db")
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import backend.routers.ai as ai_router  # noqa: E402
import backend.routers.backtest as backtest_router  # noqa: E402
import backend.routers.cashflow as cashflow_router  # noqa: E402
from analysis.financial_model import ALLOCATION_UNIT_THB  # noqa: E402
from backend.main import app  # noqa: E402
from backend.models.cashflow_models import ForecastMonth, ForecastResponse  # noqa: E402
from backend.routers.sentiment import _summary_to_response, get_sentiment_db  # noqa: E402


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch):
    """ไม่ตั้ง VAULTIS_API_KEY → security.py ยอมให้ TestClient (localhost) เรียกได้."""
    monkeypatch.delenv("VAULTIS_API_KEY", raising=False)


def _client() -> TestClient:
    # ไม่ใช้ ``with`` → ไม่จุด lifespan (APScheduler 07:00 + งานที่ยิง network)
    return TestClient(app)


def _detail_is_openapi_shaped(body: dict) -> bool:
    """422 ต้องมีรูปตามที่ openapi ประกาศ: ``detail`` เป็น array ของ object ที่มี ``loc``.

    ไคลเอนต์ที่อ่าน ``detail[0]["loc"]`` ตามเอกสารต้องไม่พังเพราะเราแอบตอบเป็นสตริง
    """
    detail = body.get("detail")
    if not isinstance(detail, list) or not detail:
        return False
    return all(isinstance(item, dict) and "loc" in item and "msg" in item for item in detail)


# ===========================================================================
# 832 — POST /api/backtest: วันที่ผิดรูปต้องเป็น 422 ก่อนแตะเน็ต ไม่ใช่ 503
# ===========================================================================

_GOOD_RESULT = {
    "symbol": "VOO",
    "start": "2022-03-09",
    "end": "2022-09-14",
    "strategy_used": "rsi_macd_3day_window",
    "total_return": 12.5,
    "sharpe_ratio": 0.8,
    "max_drawdown": -9.0,
    "win_rate": 55.0,
    "num_trades": 4,
    "benchmark_return": 10.0,
    "outperformed": True,
    "detail": None,
}


class _EngineSpy:
    """บันทึกว่า engine ถูกเรียกด้วยอะไรบ้าง — และคืนผลสำเร็จเสมอ (ไม่แตะเน็ต)."""

    def __init__(self) -> None:
        self.run_calls: list[tuple] = []
        self.optimize_calls: list[tuple] = []

    def run(self, symbol, start, end, strategy_params=None):
        self.run_calls.append((symbol, start, end))
        result = dict(_GOOD_RESULT)
        result["symbol"], result["start"], result["end"] = symbol, start, end
        return result

    def optimize(self, symbol, start, end):
        self.optimize_calls.append((symbol, start, end))
        return {"best_params": {"rsi_period": 10}}


@pytest.fixture()
def engine_spy(monkeypatch) -> _EngineSpy:
    spy = _EngineSpy()
    monkeypatch.setattr(backtest_router._engine, "run", spy.run)
    monkeypatch.setattr(backtest_router._engine, "optimize", spy.optimize)
    return spy


class TestBacktestDateContract:
    @pytest.mark.parametrize(
        "start,end,bad_field",
        [
            ("banana", "2026-01-01", "start"),
            ("2026-13-45", "2026-13-46", "start"),
            ("2026-01-01", "not-a-date", "end"),
            ("", "2026-01-01", "start"),
            ("06/08/2026", "2026-01-01", "start"),
        ],
    )
    def test_malformed_date_is_422_and_never_touches_the_network(
        self, engine_spy, start, end, bad_field
    ):
        """วันที่ผิดรูป = ความผิดของคำขอ ⇒ 422 และ engine ต้องไม่ถูกเรียกเลยสักครั้ง.

        เดิมได้ 503 "ดึงราคา VOO ไม่สำเร็จ" หลัง yfinance ถูกยิงจริง 3 รอบต่อคำขอ
        (คำอธิบายผิดทิศ + เผาโควตา) — AUDIT_ROUND2_2026-08-07 บรรทัด 832
        """
        res = _client().post(
            "/api/backtest", json={"symbol": "VOO", "start": start, "end": end}
        )

        assert res.status_code == 422, f"ควรเป็น 422 (คำขอผิด) ไม่ใช่ {res.status_code}: {res.text}"
        assert engine_spy.run_calls == [], "คำขอที่ใช้ไม่ได้ต้องไม่กลายเป็นการยิงเน็ต"
        assert engine_spy.optimize_calls == []

        body = res.json()
        assert _detail_is_openapi_shaped(body), f"422 ต้องเป็น array ตาม openapi: {body}"
        locs = [list(item["loc"]) for item in body["detail"]]
        assert ["body", bad_field] in locs, f"ต้องชี้ว่าฟิลด์ไหนผิด: {locs}"
        msg = " ".join(item["msg"] for item in body["detail"])
        assert "YYYY-MM-DD" in msg, f"ต้องบอกรูปแบบที่ถูกต้อง: {msg}"

    def test_error_message_is_not_a_data_outage_story(self, engine_spy):
        """ห้ามเล่าว่า "ดึงราคาไม่สำเร็จ" เพราะแหล่งข้อมูลไม่ได้เป็นอะไรเลย."""
        res = _client().post(
            "/api/backtest", json={"symbol": "VOO", "start": "banana", "end": "banana"}
        )
        assert res.status_code == 422
        assert "ดึงราคา" not in res.text, res.text

    def test_start_after_end_is_422_pointing_at_end(self, engine_spy):
        res = _client().post(
            "/api/backtest", json={"symbol": "VOO", "start": "2026-06-01", "end": "2026-01-01"}
        )
        assert res.status_code == 422, res.text
        assert engine_spy.run_calls == []
        body = res.json()
        assert _detail_is_openapi_shaped(body)
        assert ["body", "end"] in [list(i["loc"]) for i in body["detail"]]

    def test_valid_dates_reach_the_engine_unchanged(self, engine_spy):
        res = _client().post(
            "/api/backtest", json={"symbol": "VOO", "start": "2022-03-09", "end": "2022-09-14"}
        )
        assert res.status_code == 200, res.text
        assert engine_spy.run_calls == [("VOO", "2022-03-09", "2022-09-14")]

    def test_iso_shorthand_is_normalized_before_it_reaches_yfinance(self, engine_spy):
        """``date.fromisoformat`` (Py≥3.11) รับ ``"20220309"`` แต่ yfinance อ่านไม่ออก.

        ด่านนี้จึงต้อง **normalize** ไม่ใช่แค่ "ตรวจแล้วส่งสตริงเดิมต่อ" ไม่งั้นรูปแบบที่
        ผ่านด่านมาได้จะไปตายที่ชั้นล่างแล้วกลับมาเป็น 503 เหมือนเดิม
        """
        res = _client().post(
            "/api/backtest", json={"symbol": "VOO", "start": "20220309", "end": "20220914"}
        )
        assert res.status_code == 200, res.text
        assert engine_spy.run_calls == [("VOO", "2022-03-09", "2022-09-14")], (
            f"ต้องส่ง YYYY-MM-DD ลงไป ไม่ใช่รูปแบบดิบ: {engine_spy.run_calls}"
        )

    def test_optimize_path_also_gets_validated_dates(self, engine_spy):
        res = _client().post(
            "/api/backtest",
            json={
                "symbol": "VOO",
                "start": "20220309",
                "end": "2022-09-14",
                "run_optimization": True,
            },
        )
        assert res.status_code == 200, res.text
        assert engine_spy.optimize_calls == [("VOO", "2022-03-09", "2022-09-14")]


# ===========================================================================
# 1416 — /api/sentiment/{symbol}: NULL ต้องออกไปเป็น null ไม่ใช่ 0 / "neutral"
# ===========================================================================

_SENTIMENT_VALUE_FIELDS = (
    "total_articles",
    "positive",
    "negative",
    "neutral",
    "avg_confidence",
    "overall_sentiment",
    "score",
    "created_at",
)


def _row(**kwargs) -> SimpleNamespace:
    base = {"symbol": "VOO"}
    base.update({field: None for field in _SENTIMENT_VALUE_FIELDS})
    base.update(kwargs)
    return SimpleNamespace(**base)


class _FakeQuery:
    def __init__(self, row):
        self._row = row

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def first(self):
        return self._row


class _FakeSession:
    def __init__(self, row):
        self._row = row

    def query(self, *a, **k):
        return _FakeQuery(self._row)


class TestSentimentNullContract:
    def test_all_null_row_stays_null_and_is_flagged(self):
        """แถวที่ทุกคอลัมน์เป็น NULL ต้องไม่กลายเป็น sentiment ที่ดูสมบูรณ์แบบ."""
        out = _summary_to_response(_row())

        assert out.total_articles is None, "0 บทความ ≠ ไม่รู้ว่ามีกี่บทความ"
        assert out.avg_confidence is None
        assert out.score is None
        assert out.overall_sentiment is None, "NULL ห้ามกลายเป็นคำตัดสิน 'neutral'"
        assert out.created_at is None, "ห้ามถอยไปใช้ 'เวลาตอนนี้' ทำให้แถวเก่าดูสดใหม่"
        assert set(out.missing_fields) == set(_SENTIMENT_VALUE_FIELDS), out.missing_fields

    def test_real_zeros_are_not_swallowed_by_the_or_idiom(self):
        """สำนวน ``or`` กลืน ``0``/``0.0`` ที่เป็นคำตอบจริงเข้ากับ NULL — ต้องใช้ ``is None``."""
        out = _summary_to_response(
            _row(
                total_articles=0,
                positive=0,
                negative=0,
                neutral=0,
                avg_confidence=0.0,
                score=0.0,
                overall_sentiment="unknown",
                created_at=datetime(2026, 8, 1),
            )
        )

        assert out.total_articles == 0
        assert out.avg_confidence == 0.0
        assert out.score == 0.0
        assert out.overall_sentiment == "unknown"
        assert out.missing_fields == [], (
            f"ค่า 0 จริงต้องไม่ถูกนับว่า 'ไม่รู้': {out.missing_fields}"
        )

    def test_partial_row_flags_only_the_missing_columns(self):
        out = _summary_to_response(
            _row(total_articles=7, positive=3, negative=2, neutral=2, created_at=datetime(2026, 8, 1))
        )
        assert out.total_articles == 7
        assert out.score is None
        assert set(out.missing_fields) == {"avg_confidence", "overall_sentiment", "score"}

    def test_http_response_carries_nulls_not_zeros(self):
        """เส้นทางจริง (HTTP + response_model) ต้องปล่อย ``null`` ออกไปได้."""
        app.dependency_overrides[get_sentiment_db] = lambda: _FakeSession(_row())
        try:
            res = _client().get("/api/sentiment/VOO")
        finally:
            app.dependency_overrides.pop(get_sentiment_db, None)

        assert res.status_code == 200, res.text
        body = res.json()
        assert body["overall_sentiment"] is None, body
        assert body["score"] is None, body
        assert body["total_articles"] is None, body
        assert body["avg_confidence"] is None, body
        assert sorted(body["missing_fields"]) == sorted(_SENTIMENT_VALUE_FIELDS)

    def test_no_row_is_still_404_not_an_empty_sentiment(self):
        app.dependency_overrides[get_sentiment_db] = lambda: _FakeSession(None)
        try:
            res = _client().get("/api/sentiment/VOO")
        finally:
            app.dependency_overrides.pop(get_sentiment_db, None)
        assert res.status_code == 404, res.text


# ===========================================================================
# 1439 — /api/cashflow: inf/NaN ต้องถูกปฏิเสธที่ประตูด้วย 422 รูปมาตรฐาน
# ===========================================================================


def _months_back(n: int) -> list[str]:
    """คืน YYYY-MM ย้อนหลัง n เดือน (เดือนที่จบแล้วเท่านั้น)."""
    today = datetime.now()
    out = []
    year, month = today.year, today.month
    for _ in range(n):
        month -= 1
        if month == 0:
            month, year = 12, year - 1
        out.append(f"{year:04d}-{month:02d}")
    return out


def _full_months() -> list[dict]:
    rows: list[dict] = []
    for m in _months_back(4):
        rows += [
            {"date": f"{m}-25", "amount": 60_000, "category": "เงินเดือน", "type": "income"},
            {"date": f"{m}-05", "amount": 25_000, "category": "ที่พัก", "type": "expense"},
            {"date": f"{m}-10", "amount": 15_000, "category": "อาหาร", "type": "expense"},
        ]
    return rows


def _scenario_body(**overrides) -> dict:
    body = {"months": 3, "current_balance": 0, "transactions": _full_months(), "scenarios": []}
    body.update(overrides)
    return body


def _post_raw(path: str, body: dict):
    """ส่ง body ที่มี ``inf``/``NaN`` แบบดิบ.

    ใช้ ``content=`` ไม่ใช่ ``json=`` เพราะ httpx เข้ารหัสด้วย ``allow_nan=False`` จึงโยน
    ทิ้งตั้งแต่ฝั่งไคลเอนต์ — แต่ **Starlette แกะ body ด้วย ``json.loads`` ของ Python ซึ่ง
    รับทั้ง ``Infinity`` และ ``NaN``** นั่นคือรูตัวจริงที่ผู้เรียกเดินเข้ามาได้
    (``json.dumps`` ค่าเริ่มต้น ``allow_nan=True`` จึงเขียน literal เหล่านี้ออกมาได้)
    """
    return _client().post(
        path,
        content=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


@pytest.fixture(autouse=True)
def _clean_cashflow_store():
    cashflow_router._stored_transactions = []
    yield
    cashflow_router._stored_transactions = []


class TestCashflowNonFiniteContract:
    def test_infinite_amount_is_rejected_at_the_gate(self):
        """``1e400`` → ``inf`` ตอน ``json.loads`` เดิมไหลไปตายตอน serialize แล้วถูกเล่าเป็น 422
        ที่ ``detail`` เป็นสตริงอังกฤษของ json encoder."""
        bad = _full_months()
        bad[0]["amount"] = math.inf
        res = _post_raw("/api/cashflow/scenario", _scenario_body(transactions=bad))

        assert res.status_code == 422, res.text
        body = res.json()
        assert _detail_is_openapi_shaped(body), f"detail ต้องเป็น array ตาม openapi: {body}"
        assert ["body", "transactions", 0, "amount"] in [list(i["loc"]) for i in body["detail"]]
        msg = " ".join(i["msg"] for i in body["detail"])
        assert "inf" in msg and "NaN" in msg, msg
        assert "JSON compliant" not in res.text, "ห้ามเป็นข้อความอังกฤษของ json encoder"

    def test_literal_nan_in_body_is_rejected_with_the_documented_shape(self):
        """Starlette แกะ body ด้วย ``json.loads`` ของ Python ซึ่งรับ literal ``NaN``."""
        bad = _full_months()
        bad[2]["amount"] = math.nan
        res = _post_raw("/api/cashflow/scenario", _scenario_body(transactions=bad))

        assert res.status_code == 422, res.text
        assert _detail_is_openapi_shaped(res.json()), res.text
        assert ["body", "transactions", 2, "amount"] in [
            list(i["loc"]) for i in res.json()["detail"]
        ]

    def test_infinite_current_balance_is_rejected(self):
        """``Field(ge=0)`` ไม่กัน ``inf`` เพราะ ``inf >= 0`` เป็น True."""
        res = _post_raw("/api/cashflow/scenario", _scenario_body(current_balance=math.inf))
        assert res.status_code == 422, res.text
        body = res.json()
        assert _detail_is_openapi_shaped(body), body
        assert ["body", "current_balance"] in [list(i["loc"]) for i in body["detail"]]

    def test_infinite_query_balance_is_rejected_before_the_forecast(self):
        """เส้นทาง GET ก็มีรูเดียวกัน — ``Query(ge=0)`` ปล่อย ``inf`` ผ่าน."""
        res = _client().get("/api/cashflow/forecast?current_balance=inf")
        assert res.status_code == 422, res.text
        body = res.json()
        assert _detail_is_openapi_shaped(body), body
        assert ["query", "current_balance"] in [list(i["loc"]) for i in body["detail"]]

    def test_valid_request_still_works(self):
        res = _client().post("/api/cashflow/scenario", json=_scenario_body())
        assert res.status_code == 200, res.text
        assert res.json()["months"] == 3

    def test_service_value_error_is_still_422_with_the_thai_reason(self, monkeypatch):
        """422 ที่ตั้งใจไว้ (หมวดที่ scenario อ้างไม่มีจริง ฯลฯ) ต้องไม่หายไปกับการแก้นี้."""
        def boom(*a, **k):
            raise ValueError("scenario อ้างหมวด 'อาหารการกิน' ที่ไม่มีในข้อมูล")

        monkeypatch.setattr(cashflow_router.cashflow_service, "build_forecast_response", boom)
        res = _client().post("/api/cashflow/scenario", json=_scenario_body())
        assert res.status_code == 422, res.text
        assert "อาหารการกิน" in res.text

    def test_serialize_failure_is_500_not_a_caller_error(self, monkeypatch):
        """ผลลัพธ์ที่ serialize ไม่ได้ = บั๊กของเรา ⇒ 500 ห้ามถูกกลบเป็น "อินพุตผิด" (422).

        เดิม ``try`` คร่อม ``JSONResponse(...)`` ด้วย ``ValueError`` ของ json encoder จึงถูก
        แปลงเป็น 422 — บั๊กจริงที่ทำให้ผลลัพธ์เป็น ``inf`` ในอนาคตจะถูกซ่อนไว้แบบนั้น
        """
        def infinite_result(*a, **k):
            return ForecastResponse(
                current_balance=0.0,
                months=1,
                forecast=[
                    ForecastMonth(
                        month="2026-09",
                        projected_income=1.0,
                        projected_expense=1.0,
                        net_cashflow=0.0,
                        ending_balance=math.inf,
                    )
                ],
                anomalies=[],
                emergency_alert=False,
                emergency_message="",
                months_used=1,
            )

        monkeypatch.setattr(
            cashflow_router.cashflow_service, "build_forecast_response", infinite_result
        )
        res = _client().post("/api/cashflow/scenario", json=_scenario_body())

        assert res.status_code == 500, f"ต้องเป็น 500 (บั๊กของเรา) ไม่ใช่ {res.status_code}: {res.text}"
        assert "ไม่ใช่ของคำขอ" in res.text, res.text


# ===========================================================================
# 1468 — งบต่ำกว่าหน่วยจัดสรร: /api/ai/advice กับ /api/analysis/full ต้องตอบเหมือนกัน
# ===========================================================================


@pytest.fixture()
def advice_spy(monkeypatch):
    """stub ``get_monthly_advice`` — เรียกเมื่อไหร่แปลว่าด่านหน้าไม่ทำงาน (ของจริงยิง yfinance)."""
    calls: list[dict] = []

    def fake_advice(budget_thb: float = 5000, user_initiated: bool = False, **kwargs):
        calls.append({"budget_thb": budget_thb, "user_initiated": user_initiated})
        return {"ai_used": True, "advice_text": "ok", "budget_thb": budget_thb}

    monkeypatch.setattr(ai_router, "get_monthly_advice", fake_advice)
    monkeypatch.setattr(ai_router, "_get_history", lambda db: [])
    monkeypatch.setattr(ai_router, "_save_history", lambda db, history: None)
    ai_router._cache.clear()
    yield calls
    ai_router._cache.clear()


class TestAiBudgetContract:
    @pytest.mark.parametrize("budget", [1, 50, ALLOCATION_UNIT_THB - 1])
    def test_budget_below_one_allocation_unit_is_422_not_500(self, advice_spy, budget):
        """งบที่แจกไม่ลงสักกอง = อินพุตผิด ⇒ 422 (เดิมเป็น 500 "เซิร์ฟเวอร์พัง")."""
        res = _client().post("/api/ai/advice", json={"budget_thb": budget})

        assert res.status_code == 422, f"ควรเป็น 422 ไม่ใช่ {res.status_code}: {res.text}"
        assert advice_spy == [], "ต้องปฏิเสธก่อนที่ get_monthly_advice จะยิงราคาทุกกอง"
        body = res.json()
        assert _detail_is_openapi_shaped(body), body
        assert ["body", "budget_thb"] in [list(i["loc"]) for i in body["detail"]]
        assert str(ALLOCATION_UNIT_THB) in " ".join(i["msg"] for i in body["detail"]), body

    def test_same_budget_gets_the_same_status_from_analysis_full(self, advice_spy):
        """อินพุตเดียวกัน สอง endpoint ต้องได้รหัสสถานะเดียวกัน (เดิม 500 กับ 422)."""
        ai = _client().post("/api/ai/advice", json={"budget_thb": 50})
        full = _client().get("/api/analysis/full?budget_thb=50")

        assert ai.status_code == full.status_code == 422, (ai.status_code, full.status_code)
        assert advice_spy == []

    def test_exactly_one_unit_is_accepted(self, advice_spy):
        """ขอบเขตต้องเป็น ``>=`` ไม่ใช่ ``>`` — 100 บาทพอดีจัดสรรได้ 1 ก้อน."""
        res = _client().post("/api/ai/advice", json={"budget_thb": ALLOCATION_UNIT_THB})

        assert res.status_code == 200, res.text
        assert advice_spy == [{"budget_thb": float(ALLOCATION_UNIT_THB), "user_initiated": True}]

    def test_non_finite_budget_is_rejected_too(self, advice_spy):
        """``gt=0`` ไม่กัน ``inf`` — ``inf > 0`` เป็น True."""
        res = _client().post(
            "/api/ai/advice",
            content=json.dumps({"budget_thb": 1e400}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        assert res.status_code == 422, res.text
        assert advice_spy == []

    def test_threshold_comes_from_the_single_constant(self):
        """ตัวเลข 100 ต้องมาจาก ``financial_model.ALLOCATION_UNIT_THB`` ที่เดียว.

        ถ้ามีใครไปเขียน literal ซ้ำใน schema เทสต์นี้จะไม่จับ แต่การเลื่อนค่าคงที่แล้ว
        ขอบเขตของ endpoint เลื่อนตามคือหลักฐานว่าอ่านจากตัวเดียวกันจริง
        """
        from backend.schemas import validate_dca_budget

        assert validate_dca_budget(float(ALLOCATION_UNIT_THB)) == float(ALLOCATION_UNIT_THB)
        with pytest.raises(ValueError, match=str(ALLOCATION_UNIT_THB)):
            validate_dca_budget(ALLOCATION_UNIT_THB - 1)


# ===========================================================================
# 1468 (รอบเก็บตก) — ``GET /api/analysis/full`` ต้องใช้ "ด่านเดียวกัน" จริง ๆ
#
# รอบก่อนปิดรูที่ ``POST /api/ai/advice`` ด้วย ``schemas.validate_dca_budget`` แต่
# ``/api/analysis/full`` ยังถือด่านของตัวเองไว้ (``Query(..., ge=1)`` + ประโยคไทยที่ลอกไป
# เขียนซ้ำ) ผลคืออาการเดิมย้ายที่: ``budget_thb=inf`` ผ่านทั้ง ``inf >= 1`` และ
# ``inf < 100`` ⇒ ไหลลงไปตายตอน serialize เป็น **500 ข้อความอังกฤษ** ขณะที่
# ``/api/ai/advice`` ตอบ 422 ภาษาไทย — อินพุตเดียวกัน สองคำตอบ (อีกครั้ง)
# ===========================================================================


@pytest.fixture()
def full_analysis_spy(monkeypatch):
    """stub ``market_analysis_service.full_analysis`` — ของจริงยิง yfinance ครบทุกกอง.

    คืน ``calls`` ให้เทสต์ยืนยันว่าคำขอที่ใช้ไม่ได้ **ไม่กลายเป็นการยิงเน็ต**
    และรับ ``raises=`` ไว้จำลอง exception ที่ชั้นคำนวณโยนขึ้นมาจริง
    """
    from backend.services import market_analysis_service

    calls: list[float] = []
    box: dict[str, Exception | None] = {"raises": None}

    def fake_full(budget_thb: float):
        calls.append(budget_thb)
        if box["raises"] is not None:
            raise box["raises"]
        return {"analysis": {}, "allocation": {}, "timestamp": "2026-08-07T00:00:00"}

    monkeypatch.setattr(market_analysis_service, "full_analysis", fake_full)
    return SimpleNamespace(calls=calls, box=box)


def _budget_message(value: float) -> str:
    """ประโยคไทยต้นฉบับของด่านงบ — ดึงจาก ``validate_dca_budget`` ไม่ใช่พิมพ์ซ้ำในเทสต์."""
    from backend.schemas import validate_dca_budget

    try:
        validate_dca_budget(value)
    except ValueError as exc:
        return str(exc)
    raise AssertionError(f"{value} ควรถูกปฏิเสธ")


class TestAnalysisFullUsesTheSameBudgetGate:
    @pytest.mark.parametrize("raw", ["inf", "-inf", "nan", "Infinity", "NaN"])
    def test_non_finite_budget_is_422_not_500(self, full_analysis_spy, raw):
        """``inf``/``NaN`` = "ค่าที่ใช้ไม่ได้" ⇒ 422 ภาษาไทย ห้ามเป็น 500 อังกฤษ."""
        res = _client().get(f"/api/analysis/full?budget_thb={raw}")

        assert res.status_code == 422, f"ได้ {res.status_code}: {res.text[:300]}"
        assert full_analysis_spy.calls == [], "ต้องปฏิเสธก่อนยิงราคาทุกกอง"
        assert "Out of range float" not in res.text, "ห้ามหลุดข้อความอังกฤษของ json encoder"

    def test_the_two_endpoints_answer_a_bad_budget_the_same_way(self, full_analysis_spy):
        """งบเดียวกัน = รหัสสถานะเดียวกัน **และประโยคเดียวกัน** (นิยามเดียวจริง ๆ)."""
        expected = _budget_message(50)

        full = _client().get("/api/analysis/full?budget_thb=50")
        ai = _client().post("/api/ai/advice", json={"budget_thb": 50})

        assert full.status_code == ai.status_code == 422, (full.status_code, ai.status_code)
        assert expected in full.text, f"{expected!r} ไม่อยู่ใน {full.text[:300]}"
        assert expected in ai.text, f"{expected!r} ไม่อยู่ใน {ai.text[:300]}"
        assert full_analysis_spy.calls == []

    def test_a_usable_budget_still_goes_through(self, full_analysis_spy):
        """ด่านต้องไม่กันของดี — 100 บาทพอดีผ่าน และค่าที่ส่งต่อเป็น float ที่ตรวจแล้ว."""
        res = _client().get(f"/api/analysis/full?budget_thb={ALLOCATION_UNIT_THB}")

        assert res.status_code == 200, res.text
        assert full_analysis_spy.calls == [float(ALLOCATION_UNIT_THB)]


# ===========================================================================
# 38 (รอบเก็บตก) — "ราคาดึงไม่สำเร็จ" ห้ามออกจาก API เป็น 500 เปล่า ๆ
#
# ``NoTargetForSubset`` ไม่ได้สืบจาก ``InvalidTargetWeights`` (ตั้งใจ — คนละสาเหตุ)
# แต่ router ไม่ได้ดักไว้เลย มันจึงตกลง ``except Exception`` แล้วออกไปเป็น HTTP 500
# = บั๊ก G1 ตัวเดิม ("ดึงราคาไม่สำเร็จ" ถูกเล่าเป็นอย่างอื่น) โผล่ที่ชั้น API แทน
#
# เส้นแบ่งที่ตรึงไว้: สองสาเหตุ = สองรหัสสถานะ เพราะผู้ใช้ต้องทำคนละอย่าง
#   * ข้อมูลไม่พร้อมรอบนี้ → 503 (แค่รอ — เหมือน PriceDataUnavailableError)
#   * config.json ผิดจริง  → 422 (ต้องไปแก้ไฟล์)
# ===========================================================================


class TestTargetWeightErrorsKeepTheirCause:
    def test_price_failure_is_503_and_names_both_groups(self, full_analysis_spy):
        from portfolio.targets import NoTargetForSubset

        # ข้อความต้นทางตั้งใจให้ "ไร้ประโยชน์" เพื่อพิสูจน์ว่า router เขียนใหม่จากฟิลด์
        # ``requested``/``missing`` ไม่ใช่ส่งต่อ ``str(exc)`` ดื้อ ๆ
        full_analysis_spy.box["raises"] = NoTargetForSubset(
            "boom", requested=["GLDM", "QQQM", "XLV"], missing=["SCHD", "VOO"]
        )

        res = _client().get("/api/analysis/full?budget_thb=5000")

        assert res.status_code == 503, f"ได้ {res.status_code}: {res.text[:300]}"
        detail = res.json()["detail"]
        for ticker in ("GLDM", "QQQM", "XLV", "SCHD", "VOO"):
            assert ticker in detail, detail
        assert "ดึงราคาไม่สำเร็จ" in detail, detail
        assert "ลบคีย์" not in detail, "ห้ามชวนผู้ใช้ไปแก้คอนฟิกที่ไม่ได้ผิด"

    def test_a_real_config_mistake_is_422_not_503(self, full_analysis_spy):
        """อีกด้านของเส้นแบ่ง — คอนฟิกผิดจริงต้องยังชี้ไปที่ config.json."""
        from portfolio.targets import InvalidTargetWeights

        message = "portfolio.target_weights ตั้งเป็น 0 ทุก ticker — แก้ที่ config.json"
        full_analysis_spy.box["raises"] = InvalidTargetWeights(message)

        res = _client().get("/api/analysis/full?budget_thb=5000")

        assert res.status_code == 422, f"ได้ {res.status_code}: {res.text[:300]}"
        assert res.json()["detail"] == message

    def test_the_two_causes_do_not_share_a_status_code(self, full_analysis_spy):
        """ถ้าวันหนึ่งมีใครยุบสองเคสนี้เข้าด้วยกัน เทสต์นี้ต้องแดง."""
        from portfolio.targets import InvalidTargetWeights, NoTargetForSubset

        full_analysis_spy.box["raises"] = NoTargetForSubset(
            "x", requested=["GLDM"], missing=["VOO"]
        )
        data_status = _client().get("/api/analysis/full?budget_thb=5000").status_code

        full_analysis_spy.box["raises"] = InvalidTargetWeights("y")
        config_status = _client().get("/api/analysis/full?budget_thb=5000").status_code

        assert data_status != config_status, (
            "ข้อมูลไม่พร้อม กับ คอนฟิกผิด ต้องแยกกันได้จากรหัสสถานะ ไม่ใช่ให้ผู้ใช้เดา"
        )
        assert {data_status, config_status} == {503, 422}

    def test_an_unexpected_bug_is_still_a_500(self, full_analysis_spy):
        """ห้ามเหวี่ยงแหจนบั๊กจริงกลายเป็น 4xx/503 ที่ดูเหมือน "ปกติ"."""
        full_analysis_spy.box["raises"] = RuntimeError("บั๊กจริงในโค้ด")

        res = _client().get("/api/analysis/full?budget_thb=5000")

        assert res.status_code == 500, res.text
