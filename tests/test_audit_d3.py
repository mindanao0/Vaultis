# -*- coding: utf-8 -*-
"""AUDIT_2026-08-06 ข้อ D3 — ตาข่ายของ "ข้ออื่นระดับ low (ตารางรวม)".

ทุกเคสในไฟล์นี้ผูกกับข้อย่อยหนึ่งข้อในตาราง D3 และเขียนให้ **แดงก่อนแก้**
ไม่มีเคสไหนยิง network / LLM / webhook จริง — ข้อมูลราคาเป็นชุดสังเคราะห์ทั้งหมด
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ``backend.database`` สร้างไฟล์ SQLite ตอน import — ชี้ไป tmp เสมอ (ข้อ 0-A / H1)
os.environ.setdefault("VAULTIS_DB_PATH", str(Path(tempfile.gettempdir()) / "vaultis_test_d3.db"))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402
from backend.security import allowed_origins  # noqa: E402


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch):
    """ไม่ตั้ง VAULTIS_API_KEY → security.py ยอมให้ TestClient (localhost) เรียกได้."""
    monkeypatch.delenv("VAULTIS_API_KEY", raising=False)


def _client() -> TestClient:
    # ไม่ใช้ ``with`` → ไม่จุด lifespan (APScheduler 07:00 + งานที่ยิง network)
    return TestClient(app)


# ===========================================================================
# D3.1 — GET /api/analysis/dcf/{ticker} ตอบ 500 เมื่อ ticker ไม่มีจริง
# ===========================================================================
# `dcf_valuation` เรียก `yf.Ticker(t).info` ก่อนแตะราคา; ticker ที่ไม่มีจริงทำให้
# curl_cffi โยน HTTPError("HTTP Error 404:") ซึ่งไม่ใช่ ValueError และไม่ใช่
# PriceDataUnavailableError → หลุดออกไปเป็น 500 + traceback


class _FakeHTTPError(OSError):
    """แทน ``curl_cffi.requests.exceptions.HTTPError`` (subclass ของ OSError)."""


class TestD3_1_DcfUnknownTicker:
    def test_ticker_ไม่มีจริงต้องไม่เป็น_500(self, monkeypatch):
        from backend.services import market_analysis_service as service

        def _boom(ticker: str):
            raise _FakeHTTPError("HTTP Error 404: ")

        monkeypatch.setattr(service, "dcf_for_ticker", _boom)
        resp = _client().get("/api/analysis/dcf/ZZZZNOTAREALTICKER")

        assert resp.status_code == 404, (
            f"ticker ที่ไม่มีจริงต้องได้ 404 พร้อมข้อความไทย ไม่ใช่ {resp.status_code}"
        )
        detail = resp.json()["detail"]
        assert "ZZZZNOTAREALTICKER" in detail
        assert any("฀" <= ch <= "๿" for ch in detail), "ข้อความต้องเป็นภาษาไทย"

    def test_ดึงไม่สำเร็จชั่วคราวต้องเป็น_503_ไม่ใช่_404(self, monkeypatch):
        """"ดึงไม่สำเร็จ" ≠ "ไม่มี ticker นี้" — ห้ามยุบสองความหมายเป็นอันเดียว."""
        from backend.services import market_analysis_service as service

        def _boom(ticker: str):
            raise _FakeHTTPError("Connection timed out")

        monkeypatch.setattr(service, "dcf_for_ticker", _boom)
        resp = _client().get("/api/analysis/dcf/VOO")
        assert resp.status_code == 503, f"ได้ {resp.status_code}: {resp.text[:200]}"

    def test_สินทรัพย์ไม่มี_PE_ยังเป็น_422_เหมือนเดิม(self, monkeypatch):
        """กันการแก้เกิน — เส้นทาง GLDM (ValueError) ต้องไม่เปลี่ยนสถานะ."""
        from backend.services import market_analysis_service as service

        monkeypatch.setattr(
            service,
            "dcf_for_ticker",
            lambda ticker: (_ for _ in ()).throw(ValueError("GLDM ไม่มีข้อมูล P/E")),
        )
        assert _client().get("/api/analysis/dcf/GLDM").status_code == 422


# ===========================================================================
# D3.2 — endpoint ที่คืนภาษาไทยไม่ตั้ง charset=utf-8
# ===========================================================================


def _charset_ok(resp) -> bool:
    return "charset=utf-8" in resp.headers.get("content-type", "").lower()


class TestD3_2_Utf8Charset:
    def test_screener_presets(self):
        resp = _client().get("/api/screener/presets")
        assert resp.status_code == 200, resp.text[:200]
        assert _charset_ok(resp), f"content-type = {resp.headers.get('content-type')!r}"

    def test_etf_analysis(self, monkeypatch):
        from backend.models.etf_models import ETFAnalysis, ETFInfo, TechnicalIndicators
        from backend.routers import etf_analysis as mod

        async def _stub(sym: str) -> ETFAnalysis:
            return ETFAnalysis(
                symbol=sym,
                info=ETFInfo(symbol=sym, name="กองทุนทดสอบ"),
                technical=TechnicalIndicators(symbol=sym, price=100.0, signal="neutral"),
                overall_signal="hold",
                ai_summary=None,
                updated_at=datetime.now(UTC),
            )

        monkeypatch.setattr(mod, "_build_core_analysis", _stub)
        resp = _client().get("/api/etf/VOO")
        assert resp.status_code == 200, resp.text[:200]
        assert _charset_ok(resp), f"content-type = {resp.headers.get('content-type')!r}"

    def test_networth_current(self, monkeypatch):
        from backend.models.networth_models import NetWorthResponse
        from backend.services import networth_service

        monkeypatch.setattr(
            networth_service,
            "get_current",
            lambda db: NetWorthResponse(
                snapshot_date="2026-08-06",
                assets=[],
                liabilities=[],
                total_assets_thb=0.0,
                total_liabilities_thb=0.0,
                net_worth_thb=0.0,
                warnings=["ยังไม่มี snapshot"],
            ),
        )
        resp = _client().get("/api/networth/current")
        assert resp.status_code == 200, resp.text[:200]
        assert _charset_ok(resp), f"content-type = {resp.headers.get('content-type')!r}"

    def test_forecast(self, monkeypatch, stub_forecast):
        resp = _client().get("/api/forecast/VOO?days=7")
        assert resp.status_code == 200, resp.text[:200]
        assert _charset_ok(resp), f"content-type = {resp.headers.get('content-type')!r}"


# ===========================================================================
# D3.3 — CORS บล็อก PUT ทั้งที่ประกาศ PUT /api/goals/{id}/contribute
# ===========================================================================


class TestD3_3_CorsMethods:
    def test_preflight_put_ผ่าน(self):
        origin = allowed_origins()[0]
        resp = _client().options(
            "/api/goals/1/contribute",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert resp.status_code == 200, (
            f"preflight PUT ถูกปฏิเสธ ({resp.status_code}) — "
            f"allow-methods = {resp.headers.get('access-control-allow-methods')!r}"
        )
        allowed = resp.headers.get("access-control-allow-methods", "")
        assert "PUT" in allowed, f"allow-methods = {allowed!r}"

    def test_ทุกเมธอดที่แอปประกาศต้องอยู่ใน_allow_methods(self):
        """ตาข่ายกันเพิ่ม route เมธอดใหม่แล้วลืมเปิด CORS ให้."""
        declared: set[str] = set()
        for route in app.routes:
            for method in getattr(route, "methods", set()) or set():
                if method not in {"HEAD", "OPTIONS"}:
                    declared.add(method)

        origin = allowed_origins()[0]
        client = _client()
        for method in sorted(declared):
            resp = client.options(
                "/health",
                headers={"Origin": origin, "Access-Control-Request-Method": method},
            )
            assert resp.status_code == 200, (
                f"CORS ปฏิเสธ preflight ของเมธอด {method} ที่แอปประกาศใช้จริง"
            )


# ===========================================================================
# D3.4 / D3.5 — scripts/ เปิดฐาน SQLite ผิดตัว (path สัมพัทธ์)
# ===========================================================================


def _run_script(name: str, db_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["VAULTIS_DB_PATH"] = str(db_path)
    env["PYTHONPATH"] = str(_ROOT)
    env.pop("DATABASE_URL", None)
    return subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / name), *args],
        cwd=tempfile.gettempdir(),  # จงใจรันนอก repo — จุดที่บั๊กเดิมโผล่
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _make_goals_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE investment_goals (id INTEGER PRIMARY KEY, name TEXT, target_date TEXT)"
    )
    conn.execute("INSERT INTO investment_goals VALUES (1, 'บ้าน', '2030-01-01')")
    conn.execute("INSERT INTO investment_goals VALUES (2, 'ขยะ', 'string')")
    conn.commit()
    conn.close()


class TestD3_5_CheckDb:
    def test_อ่านฐานที่_VAULTIS_DB_PATH_ชี้อยู่(self, tmp_path):
        db = tmp_path / "real.db"
        _make_goals_db(db)
        proc = _run_script("check_db.py", db)
        assert proc.returncode == 0, proc.stderr[-800:]
        assert "investment_goals" in proc.stdout, (
            f"สคริปต์ไม่เห็นตารางในฐานจริง — stdout:\n{proc.stdout}"
        )
        assert str(db) in proc.stdout, "ต้องพิมพ์ path ของฐานที่เปิดจริงให้ผู้ใช้ตรวจได้"

    def test_ไม่สร้างไฟล์ฐานเปล่าทิ้งไว้(self, tmp_path):
        missing = tmp_path / "not_there.db"
        proc = _run_script("check_db.py", missing)
        assert not missing.exists(), "สคริปต์สร้างฐานเปล่า 0 ไบต์ทิ้งไว้"
        assert proc.returncode != 0, "ไม่มีไฟล์ฐาน = ต้องล้มดัง ๆ ไม่ใช่รายงาน 'ไม่มีตารางเลย'"


class TestD3_4_FixGoals:
    def test_ดีฟอลต์เป็น_dry_run_ไม่ลบจริง(self, tmp_path):
        db = tmp_path / "real.db"
        _make_goals_db(db)
        proc = _run_script("fix_goals.py", db)
        assert proc.returncode == 0, proc.stderr[-800:]

        conn = sqlite3.connect(db)
        remaining = conn.execute("SELECT COUNT(*) FROM investment_goals").fetchone()[0]
        conn.close()
        assert remaining == 2, "โหมดดีฟอลต์ต้องไม่ลบข้อมูล (dry-run)"
        assert "--apply" in proc.stdout

    def test_apply_ลบแถวที่วันที่ใช้ไม่ได้_และสำรองไฟล์ก่อน(self, tmp_path):
        db = tmp_path / "real.db"
        _make_goals_db(db)
        proc = _run_script("fix_goals.py", db, "--apply")
        assert proc.returncode == 0, proc.stderr[-800:]

        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT id FROM investment_goals").fetchall()
        conn.close()
        assert [r[0] for r in rows] == [1], "ต้องลบเฉพาะแถวที่ target_date ไม่ใช่วันที่จริง"

        backups = list(tmp_path.glob("real.db.bak-*"))
        assert backups, "ต้องสำรองไฟล์ฐานก่อนลบ"

    def test_ไม่มีไฟล์ฐานต้องล้มดัง_ไม่สร้างฐานเปล่า(self, tmp_path):
        missing = tmp_path / "not_there.db"
        proc = _run_script("fix_goals.py", missing)
        assert not missing.exists(), "สคริปต์สร้างฐานเปล่า 0 ไบต์ทิ้งไว้"
        assert proc.returncode != 0

    def test_ไม่มีตาราง_investment_goals_ต้องบอกตรง_ไม่ใช่_traceback(self, tmp_path):
        db = tmp_path / "empty.db"
        sqlite3.connect(db).close()  # ฐานที่มีอยู่จริงแต่ยังไม่มีตาราง
        proc = _run_script("fix_goals.py", db)
        assert proc.returncode != 0
        assert "Traceback" not in proc.stderr
        assert "investment_goals" in proc.stderr


# ===========================================================================
# D3.6 — Prophet พยากรณ์วันที่ตลาดปิด (freq='D')
# ===========================================================================


def _synthetic_history(n: int = 320) -> pd.DataFrame:
    index = pd.bdate_range("2024-01-01", periods=n)
    y = pd.Series(range(n), dtype=float) * 0.1 + 100.0
    return pd.DataFrame(
        {
            "ds": index,
            "y": y.to_numpy(),
            "volume": 1_000_000.0,
            "rsi": 50.0,
            "volume_ma20": 1_000_000.0,
        }
    )


class TestD3_6_ForecastCalendar:
    def test_ไม่พยากรณ์วันเสาร์อาทิตย์(self, monkeypatch):
        from analysis.forecaster import PriceForecaster

        monkeypatch.setattr(
            PriceForecaster, "fetch_data", lambda self, symbol, period="2y": _synthetic_history()
        )
        result = PriceForecaster().forecast("VOO", days=30)

        dates = [pd.Timestamp(p["date"]) for p in result["predictions"]]
        weekend = [d.date().isoformat() for d in dates if d.weekday() >= 5]
        assert not weekend, f"พยากรณ์วันที่ตลาดปิด {len(weekend)} วัน: {weekend[:5]}"
        assert len(dates) == 30, "จำนวนวันพยากรณ์ต้องเท่ากับที่ขอ"


# ===========================================================================
# D3.8 — ta.rsi คืนค่าเร็วไป 1 แท่งเป็น 0/100 ปลอม
# ===========================================================================


class TestD3_8_RsiWarmup:
    def test_ราคา_14_จุดต้องยังไม่มี_RSI(self):
        from analysis.ta_compat import ta

        series = pd.Series([100.0 + i for i in range(14)])
        rsi = ta.rsi(series, length=14)
        assert rsi.isna().all(), (
            f"RSI โผล่ก่อนครบ warm-up: iloc[13] = {rsi.iloc[13]} "
            "(ขาขึ้นล้วน 13 การเปลี่ยนแปลง → 100.0 ปลอม → zone=overbought)"
        )

    def test_แท่งแรกที่มีค่าอยู่ที่_index_14(self):
        from analysis.ta_compat import ta

        series = pd.Series([100.0 + i for i in range(30)])
        assert ta.rsi(series, length=14).first_valid_index() == 14

    def test_ta_กับ_fallback_ต้องเป็นสูตรเดียวกัน(self):
        """"นิยามมีที่เดียว" — ตัวชี้วัดต้องไม่ขึ้นกับว่ามี library `ta` ติดตั้งไหม."""
        from analysis.ta_compat import _rsi_fallback, ta

        rng = pd.Series(
            [100.0 + ((i * 7919) % 23) - 11 for i in range(120)], dtype=float
        ).cumsum()
        a = ta.rsi(rng, length=14)
        b = _rsi_fallback(rng, length=14)
        assert list(a.isna()) == list(b.isna()), "ช่วง warm-up ต้องยาวเท่ากัน"
        pd.testing.assert_series_equal(a.dropna(), b.dropna(), check_names=False)


# ===========================================================================
# D3.9 — เศษงบ <100 บาทหายไปโดยไม่รายงาน
# ===========================================================================


class TestD3_9_CashflowResidual:
    HOLD = {"A": 5_000.0, "B": 5_000.0}
    TARGET = {"A": 0.5, "B": 0.5}

    def test_เศษที่แจกไม่ลงต้องถูกรายงาน(self):
        from portfolio.cashflow_rebalance import rebalance_with_new_money

        plan = rebalance_with_new_money(self.HOLD, self.TARGET, 9_999.0)
        allocated = sum(item["amount_thb"] for item in plan.values())
        assert allocated == 9_900
        assert getattr(plan, "unallocated_thb", None) == 99.0, (
            "เศษ 99 บาทหายเงียบ — ต้องรายงานออกมาให้ผู้เรียกแสดงต่อได้"
        )
        assert allocated + plan.unallocated_thb == 9_999.0

    def test_งบไม่ถึงหนึ่งก้อนต้องล้มดัง_ไม่คืนแผนว่าง(self):
        from portfolio.cashflow_rebalance import rebalance_with_new_money

        with pytest.raises(ValueError) as exc:
            rebalance_with_new_money(self.HOLD, self.TARGET, 99.0)
        assert "100" in str(exc.value)

    def test_งบลงตัวพอดีต้องไม่มีเศษ(self):
        from portfolio.cashflow_rebalance import rebalance_with_new_money

        plan = rebalance_with_new_money(self.HOLD, self.TARGET, 10_000.0)
        assert sum(item["amount_thb"] for item in plan.values()) == 10_000
        assert plan.unallocated_thb == 0.0


# ===========================================================================
# D3.10 — งบ DCA 1–99 บาทคืนแผนว่าง แล้วรายงานสาเหตุผิด
# ===========================================================================


def _score(ticker: str, pct: float) -> dict:
    return {"ticker": ticker, "data_ok": True, "total_pct": pct}


class TestD3_10_TinyBudget:
    SCORES = {"VOO": _score("VOO", 60.0), "SCHD": _score("SCHD", 55.0)}
    TARGETS = {"VOO": 0.6, "SCHD": 0.4}

    def test_งบต่ำกว่าหนึ่งก้อนต้องบอกสาเหตุจริง(self):
        from analysis.financial_model import calculate_allocation

        with pytest.raises(ValueError) as exc:
            calculate_allocation(self.SCORES, 50.0, target_weights=self.TARGETS)
        message = str(exc.value)
        assert "100" in message
        assert "ดึงข้อมูลไม่ได้" not in message, "ห้ามโทษข้อมูลทั้งที่ข้อมูลครบ"

    def test_งบ_100_บาทยังจัดสรรได้(self):
        from analysis.financial_model import calculate_allocation

        allocation = calculate_allocation(self.SCORES, 100.0, target_weights=self.TARGETS)
        assert sum(i["amount_thb"] for i in allocation.values()) == 100

    def test_api_full_ตอบ_422_พร้อมเหตุผลไทย(self):
        resp = _client().get("/api/analysis/full?budget_thb=50")
        assert resp.status_code == 422, f"ได้ {resp.status_code}: {resp.text[:200]}"
        assert "100" in resp.json()["detail"]


# ===========================================================================
# D3.11 — ab_backtest แขน benchmark VOO ซื้อเดือนแรกคนละวัน
# ===========================================================================


def _misaligned_prices() -> pd.DataFrame:
    """VOO มีราคาตั้งแต่วันแรกของเดือน แต่ SCHD เพิ่งมีตั้งแต่วันทำการที่ 13.

    ⇒ แขนหลายสินทรัพย์ซื้อเดือนแรกวันที่ 13 (@110) ส่วนแขน VOO อย่างเดียว
    เคยซื้อวันแรก (@100) = ได้ของถูกกว่าโดยไม่มีเหตุผลเชิงกลยุทธ์
    """
    index = pd.bdate_range("2020-11-02", periods=66)  # ~3 เดือน
    voo = [100.0] * 12 + [110.0] * (len(index) - 12)
    schd = [float("nan")] * 12 + [50.0] * (len(index) - 12)
    return pd.DataFrame({"VOO": voo, "SCHD": schd}, index=index)


class TestD3_11_BenchmarkAlignment:
    def test_แขน_VOO_ซื้อวันเดียวกับแขนอื่น(self):
        from portfolio.ab_backtest import run_ab_backtest

        results = run_ab_backtest(
            {"real": _misaligned_prices()},
            monthly_amount=1_000.0,
            target_weights={"VOO": 0.5, "SCHD": 0.5},
        )
        arms = results["real"]["arms"]
        assert arms["voo_only"]["n_months"] == arms["plain"]["n_months"]
        # เมื่อซื้อวันเดียวกับอีกสองแขน ทุกไม้ตกที่ราคา 110 ซึ่งไม่เปลี่ยนอีกเลย
        # ⇒ มูลค่าปลายทางต้องเท่ากับเงินที่ลงไปพอดี (กำไร 0)
        assert arms["voo_only"]["final_value"] == pytest.approx(
            arms["voo_only"]["total_invested"]
        ), "แขน benchmark ซื้อเดือนแรกคนละวันกับแขนอื่น (ได้ราคา 100 แทน 110)"
        assert arms["voo_only"]["pl_pct"] == pytest.approx(0.0)


# ===========================================================================
# D3.12 — portfolio/rebalance.py เป็นโค้ดตายที่ประกาศ threshold ชุดที่สาม
# ===========================================================================


class TestD3_12_DeadRebalanceModule:
    def test_โมดูลโค้ดตายต้องไม่มีอยู่แล้ว(self):
        assert importlib.util.find_spec("portfolio.rebalance") is None, (
            "portfolio/rebalance.py ไม่มีผู้เรียกเลยและประกาศ threshold=0.05 "
            "ชุดที่สามซ้อนกับ rebalance_service — ต้องถูกลบทิ้ง"
        )


# ===========================================================================
# D3.7 — days ของ /api/forecast ไม่มี validation
# ===========================================================================


class _StubBacktester:
    def run(self, symbol: str) -> dict:
        return {"mae": 1.0, "rmse": 1.0, "mape": 1.0, "n_folds": 1, "note": ""}


class _StubForecaster:
    def __init__(self) -> None:
        self._forecast_df = pd.DataFrame()
        self._hist_df = pd.DataFrame()

    def forecast(self, symbol: str, days: int = 30) -> dict:
        return {
            "last_price": 100.0,
            "predictions": [{"date": "2026-08-07", "yhat": 1.0, "yhat_lower": 0.0, "yhat_upper": 2.0}],
            "trend": "sideways",
            "trend_pct": 0.0,
            "disclaimer": "เพื่อการศึกษาเท่านั้น",
        }


@pytest.fixture
def stub_forecast(monkeypatch):
    """ตัดทุกเส้นทางที่ยิง network ของ /api/forecast ออก (Prophet + yfinance)."""
    from backend.routers import forecast as mod

    monkeypatch.setattr(mod, "PriceForecaster", _StubForecaster)
    monkeypatch.setattr(mod, "WalkForwardBacktester", _StubBacktester)
    monkeypatch.setattr(mod, "generate_forecast_chart", lambda *a, **k: "stub-base64")
    mod._cache._cache.clear()
    mod._cache._expiry.clear()
    yield
    mod._cache._cache.clear()
    mod._cache._expiry.clear()


class TestD3_7_ForecastDaysValidation:
    @pytest.mark.parametrize("days", [0, -5, 100000])
    def test_days_นอกช่วงต้องเป็น_422(self, days, stub_forecast):
        resp = _client().get(f"/api/forecast/VOO?days={days}")
        assert resp.status_code == 422, f"days={days} ได้ {resp.status_code}: {resp.text[:200]}"

    def test_days_ปกติยังใช้ได้(self, stub_forecast):
        resp = _client().get("/api/forecast/VOO?days=30")
        assert resp.status_code == 200, resp.text[:200]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
