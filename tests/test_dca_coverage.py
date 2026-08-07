# -*- coding: utf-8 -*-
"""B8 — DCA Simulator ห้ามตัดช่วงเวลาทิ้งเงียบ (AUDIT_2026-08-06 §B8).

``simulate_monthly_dca()`` ใช้ ``resample("MS").first().dropna(how="any")`` ซึ่งตัด
**ทั้งเดือน** ถ้ามี ticker ตัวใดยังไม่เกิด ผลจริงที่วัดได้: พอร์ต 5 กองมีข้อมูล
2016-08 → 2026-08 (121 เดือน) แต่จำลองจริงแค่ 71 เดือน เพราะ QQQM เพิ่งเทรด
2020-10-13 — หน้าจอโชว์ ``Total Invested $71,000`` โดยไม่มีอะไรบอกว่า 50 เดือนหายไป

กฎที่คุม: **"ตัดข้อมูลทิ้งเงียบ" ผิดพอกับ "กุตัวเลข"** — ตัดได้ แต่ต้องรายงานออกไป
(เทียบ ``portfolio/backtest.py::run_portfolio_backtest`` ที่อธิบายการตัดช่วงไว้แล้ว)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from portfolio.dca import COVERAGE_ATTR, describe_coverage, simulate_monthly_dca

WEIGHTS = {"VOO": 0.4, "SCHD": 0.3, "QQQM": 0.3}
TICKERS = list(WEIGHTS)


def _prices(qqqm_start: str | None = "2020-10-13") -> pd.DataFrame:
    """ราคารายวัน 2016-01 → 2020-12 โดย QQQM เพิ่งมีราคาวันที่ ``qqqm_start``."""
    index = pd.bdate_range("2016-01-04", "2020-12-31")
    frame = pd.DataFrame(index=index)
    frame["VOO"] = np.linspace(180.0, 330.0, len(index))
    frame["SCHD"] = np.linspace(35.0, 70.0, len(index))
    frame["QQQM"] = np.nan
    if qqqm_start is not None:
        mask = frame.index >= pd.Timestamp(qqqm_start)
        frame.loc[mask, "QQQM"] = np.linspace(100.0, 130.0, int(mask.sum()))
    return frame


def _full_prices() -> pd.DataFrame:
    frame = _prices(qqqm_start=None)
    frame["QQQM"] = np.linspace(100.0, 130.0, len(frame.index))
    return frame


class TestCoverageIsReported:
    """ผลลัพธ์ต้องพกช่วงเวลาที่จำลองจริง + จำนวนเดือนที่ถูกตัดติดมาด้วย."""

    def test_dropped_months_are_reported(self):
        result = simulate_monthly_dca(_prices(), WEIGHTS, monthly_investment=1000.0)

        coverage = result.attrs.get(COVERAGE_ATTR)
        assert coverage, "ผลจำลองต้องพกรายงานช่วงเวลามาด้วย ไม่ใช่ตัดเงียบ"
        assert coverage["months_available"] == 60
        assert coverage["months_simulated"] == 3
        assert coverage["months_dropped"] == 57
        assert coverage["available_from"] == "2016-01"
        assert coverage["simulated_from"] == "2020-10"
        assert coverage["simulated_to"] == "2020-12"

    def test_limiting_ticker_is_named_with_its_first_price_date(self):
        coverage = simulate_monthly_dca(_prices(), WEIGHTS, monthly_investment=1000.0).attrs[
            COVERAGE_ATTR
        ]

        assert coverage["limited_by"] == {"QQQM": "2020-10-13"}, (
            "ต้องบอกว่ากองไหนเป็นตัวตัดช่วง และมีราคาแรกวันไหน"
        )

    def test_full_history_reports_nothing_dropped(self):
        coverage = simulate_monthly_dca(_full_prices(), WEIGHTS, monthly_investment=1000.0).attrs[
            COVERAGE_ATTR
        ]

        assert coverage["months_dropped"] == 0
        assert coverage["limited_by"] == {}
        assert coverage["simulated_from"] == coverage["available_from"] == "2016-01"

    def test_numbers_of_the_simulation_are_unchanged(self):
        """รายงานเพิ่มเข้ามาเฉย ๆ — ตัวเลขที่จำลองต้องเท่าเดิมทุกช่อง."""
        result = simulate_monthly_dca(_prices(), WEIGHTS, monthly_investment=1000.0)
        coverage = result.attrs[COVERAGE_ATTR]

        assert list(result.columns) == [
            "Total Invested",
            "Portfolio Value",
            "Profit/Loss",
            "Profit/Loss %",
        ]
        assert len(result) == coverage["months_simulated"]
        assert result["Total Invested"].iloc[-1] == pytest.approx(
            1000.0 * coverage["months_simulated"]
        )

    def test_ticker_without_any_price_fails_loudly(self):
        """ทุกเดือนถูกตัด = จำลองไม่ได้ ต้องบอกเหตุผลอ่านรู้เรื่อง ไม่ใช่ KeyError 'Date'."""
        with pytest.raises(RuntimeError) as excinfo:
            simulate_monthly_dca(_prices(qqqm_start=None), WEIGHTS, monthly_investment=1000.0)

        assert "QQQM" in str(excinfo.value)


class TestCoverageMessage:
    """ข้อความไทยหนึ่งที่ ใช้ร่วมกันทั้งหน้าจอและ API (นิยามมีที่เดียว)."""

    def test_message_names_range_count_and_ticker(self):
        coverage = simulate_monthly_dca(_prices(), WEIGHTS, monthly_investment=1000.0).attrs[
            COVERAGE_ATTR
        ]

        message = describe_coverage(coverage)
        assert message
        assert "2020-10" in message
        assert "57" in message
        assert "QQQM" in message
        assert "2020-10-13" in message

    def test_no_message_when_nothing_was_dropped(self):
        coverage = simulate_monthly_dca(_full_prices(), WEIGHTS, monthly_investment=1000.0).attrs[
            COVERAGE_ATTR
        ]

        assert describe_coverage(coverage) is None


# ---------------------------------------------------------------------------
# หน้าจอ — คำเตือนต้องอยู่เหนือ metric ทั้งสามช่อง
# ---------------------------------------------------------------------------


class _Slot:
    def __init__(self, log: list) -> None:
        self._log = log

    def __enter__(self) -> "_Slot":
        return self

    def __exit__(self, *_exc) -> bool:
        return False

    def __getattr__(self, name: str):
        def _call(*args, **kwargs):
            self._log.append((name, args, kwargs))
            return None

        return _call


class FakeSt:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __getattr__(self, name: str):
        def _call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return None

        return _call

    def number_input(self, *args, **kwargs):
        self.calls.append(("number_input", args, kwargs))
        return float(kwargs.get("value", 1000.0))

    def slider(self, *args, **kwargs):
        self.calls.append(("slider", args, kwargs))
        return float(kwargs.get("value", 1.0))

    def columns(self, spec, **kwargs):
        count = spec if isinstance(spec, int) else len(spec)
        self.calls.append(("columns", (spec,), kwargs))
        return [_Slot(self.calls) for _ in range(count)]

    def names(self) -> list[str]:
        return [name for name, _args, _kwargs in self.calls]

    def all_text(self) -> str:
        return "\n".join(str(args[0]) if args else "" for _n, args, _k in self.calls)


class TestDashboardWarns:
    def test_warning_is_shown_above_the_metrics(self, monkeypatch):
        app = pytest.importorskip("dashboard.app")
        fake = FakeSt()
        monkeypatch.setattr(app, "st", fake)

        app.render_dca_simulator_page(_prices(), dict(WEIGHTS), TICKERS)

        names = fake.names()
        assert "warning" in names, "ช่วงที่ถูกตัดต้องขึ้นเป็นคำเตือน ไม่ใช่หายเงียบ"
        assert names.index("warning") < names.index("metric"), "คำเตือนต้องอยู่เหนือ metric"
        text = fake.all_text()
        assert "QQQM" in text and "57" in text

    def test_no_warning_when_range_is_complete(self, monkeypatch):
        app = pytest.importorskip("dashboard.app")
        fake = FakeSt()
        monkeypatch.setattr(app, "st", fake)

        app.render_dca_simulator_page(_full_prices(), dict(WEIGHTS), TICKERS)

        assert "warning" not in fake.names()


# ---------------------------------------------------------------------------
# API — /api/dca/simulate ต้องมีฟิลด์บอกช่วงที่จำลองจริง
# ---------------------------------------------------------------------------


class TestApiExposesCoverage:
    def test_service_returns_history_and_coverage(self, monkeypatch):
        from backend.services import market_analysis_service as service

        monkeypatch.setattr(service, "_prices", lambda: _prices())

        payload = service.simulate_dca(dict(WEIGHTS), 1000.0)

        assert isinstance(payload, dict), "ผลต้องมีที่ว่างให้รายงานช่วงเวลา ไม่ใช่ลิสต์เปล่า ๆ"
        assert len(payload["history"]) == 3
        assert payload["coverage"]["months_dropped"] == 57
        assert "QQQM" in payload["warning"]

    def test_endpoint_serializes_coverage(self, monkeypatch):
        from fastapi.testclient import TestClient

        from backend.main import app as fastapi_app
        from backend.services import market_analysis_service as service

        monkeypatch.setattr(service, "_prices", lambda: _prices())
        monkeypatch.setenv("VAULTIS_API_KEY", "test-key")

        with TestClient(fastapi_app) as client:
            response = client.post(
                "/api/dca/simulate",
                json={"monthly_investment": 1000.0, "weights": WEIGHTS},
                headers={"X-API-Key": "test-key"},
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["coverage"]["simulated_from"] == "2020-10"
        assert data["coverage"]["months_dropped"] == 57
        assert data["history"][0]["Date"].startswith("2020-10-01")
