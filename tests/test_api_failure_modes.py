# -*- coding: utf-8 -*-
"""คุมบั๊ก M16-M19 — ความล้มเหลวบางส่วนต้องไม่ทำให้ทั้ง endpoint พัง.

หลักที่ยึด (AUDIT.md C1): ช่องที่ไม่มีข้อมูลเป็น ``null`` ให้ผู้ใช้เห็นว่า "ไม่รู้"
ห้ามเป็น 0 (ตีความเป็นผลตอบแทนได้) และห้ามลาก endpoint ทั้งตัวลงไปเป็น 500
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import numpy as np
import pandas as pd
import pytest
from sqlalchemy.exc import OperationalError

from analysis import macro
from analysis.returns import RETURN_WINDOWS
from backend.services import etf_service
from backend.services.json_safe import frame_to_dict, frame_to_records, json_safe


class TestJsonSafeM16:
    """NaN ต้องกลายเป็น null ไม่ใช่ทำให้ JSONResponse โยน ValueError"""

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), None, pd.NaT, pd.NA])
    def test_unrepresentable_becomes_none(self, bad):
        assert json_safe(bad) is None

    def test_nan_is_not_turned_into_zero(self):
        """0 คือตัวเลขที่อ่านเป็นผลตอบแทนได้ — ห้ามใช้แทน 'ไม่มีข้อมูล'"""
        assert json_safe(float("nan")) is not 0  # noqa: F632
        assert json_safe(float("nan")) is None

    def test_real_numbers_pass_through(self):
        assert json_safe(1.5) == 1.5
        assert json_safe(0.0) == 0.0
        assert json_safe("VOO") == "VOO"

    def test_numpy_scalars_unwrapped(self):
        assert json_safe(np.float64(2.5)) == 2.5
        assert json_safe(np.int64(7)) == 7
        assert json_safe(np.float64("nan")) is None

    def test_frame_with_nan_serializes(self):
        df = pd.DataFrame({"VOO": [1.0, np.nan]}, index=["1M", "10Y"])
        out = frame_to_dict(df)
        assert json.dumps(out)  # ต้องไม่โยน ValueError
        assert out["VOO"]["10Y"] is None

    def test_empty_frame_is_empty_not_error(self):
        assert frame_to_dict(pd.DataFrame()) == {}
        assert frame_to_records(pd.DataFrame()) == []


class TestTimestampsM18:
    """DataFrame ที่มี index เป็นวันที่ต้อง serialize ได้ (เดิม /api/dca/simulate คืน 500)"""

    def test_timestamp_index_becomes_iso_string(self):
        df = pd.DataFrame(
            {"Portfolio Value": [100.0, 110.0]},
            index=pd.DatetimeIndex(["2026-01-01", "2026-02-01"], name="Date"),
        )
        records = frame_to_records(df)
        assert json.dumps(records)
        assert records[0]["Date"] == "2026-01-01T00:00:00"

    def test_nat_in_date_column_becomes_none(self):
        df = pd.DataFrame({"Date": [pd.Timestamp("2026-01-01"), pd.NaT], "v": [1.0, 2.0]})
        records = frame_to_records(df, reset_index=False)
        assert json.dumps(records)
        assert records[1]["Date"] is None


class TestReturnsWindowM16:
    """หน้าต่าง 10Y ต้องคำนวณได้จริง ไม่ใช่ NaN ทั้งแถวตลอดกาล"""

    def test_history_is_longer_than_longest_window(self):
        longest_rows = max(RETURN_WINDOWS.values())
        # ต้องมีแถวมากกว่าหน้าต่าง ไม่ใช่เท่ากัน (เงื่อนไขคือ len > window)
        assert etf_service._RETURNS_HISTORY_YEARS * 252 > longest_rows + 1

    def test_ten_year_window_needs_more_than_ten_years(self):
        """เอกสารเหตุผล: ข้อมูล 10 ปี = ~2,510 แถว < 2,520 → 10Y เป็น NaN เสมอ"""
        assert RETURN_WINDOWS["10Y"] == 2520
        assert 10 * 252 <= RETURN_WINDOWS["10Y"]  # นี่คือสาเหตุของบั๊ก
        assert etf_service._RETURNS_HISTORY_YEARS > 10

    def test_returns_frame_serializes_with_short_history_tickers(self, monkeypatch):
        """ETF ที่เกิดทีหลังต้องได้ null ในช่วงยาว ไม่ทำให้ endpoint พัง"""
        idx = pd.date_range("2015-01-01", periods=2800, freq="B")
        old = pd.Series(np.linspace(100, 300, 2800), index=idx)
        young = old.copy()
        young.iloc[:1500] = np.nan  # จำลอง QQQM ที่เพิ่งเกิด
        frame = pd.DataFrame({"VOO": old, "QQQM": young})

        monkeypatch.setattr(etf_service, "_prices_df_for_returns", lambda: frame)
        out = etf_service.get_etf_returns()

        assert json.dumps(out)
        assert out["VOO"]["10Y"] is not None
        assert out["QQQM"]["10Y"] is None


class TestThaiInflationM19:
    """URL ที่ผิดต้องถูกจับได้ — เทสต์เดิม mock ทั้ง response จึงไม่มีวันเห็น"""

    @pytest.fixture(autouse=True)
    def _reset(self):
        macro._thai_cpi_cache = None
        yield
        macro._thai_cpi_cache = None

    def test_indicator_code_has_dots(self):
        """World Bank ต้องการ FP.CPI.TOTL.ZG — เขียนติดกันจะโดนปฏิเสธเงียบ ๆ"""
        assert macro._WORLD_BANK_TH_CPI_INDICATOR == "FP.CPI.TOTL.ZG"
        assert macro._WORLD_BANK_TH_CPI_INDICATOR in macro._WORLD_BANK_TH_CPI_URL

    def test_api_error_envelope_is_detected(self, monkeypatch, caplog):
        """World Bank ตอบ 200 พร้อม message error — ต้องอ่านออก ไม่ใช่เห็นแค่ IndexError"""

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return [{"message": [{"id": "120", "key": "Invalid value"}]}]

        monkeypatch.setattr(macro.requests, "get", lambda url, timeout=10: _Resp())
        with caplog.at_level("WARNING"):
            assert macro.get_thai_inflation() is None
        assert any("World Bank ปฏิเสธ query" in r.getMessage() for r in caplog.records)


class TestSentimentDbDownM17:
    """ฐานข้อมูลล่ม = 503 พร้อมข้อความ ไม่ใช่ 500 เปล่า ๆ"""

    def _client(self):
        from fastapi.testclient import TestClient

        from backend.routers import sentiment as router_mod

        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router_mod.router)
        return TestClient(app, raise_server_exceptions=False), router_mod

    def test_operational_error_becomes_503(self):
        client, router_mod = self._client()

        class _DeadSession:
            def query(self, *a, **k):
                raise OperationalError("SELECT 1", {}, Exception("ENOTFOUND"))

            def close(self):
                pass

        client.app.dependency_overrides[router_mod.get_sentiment_db] = lambda: _DeadSession()
        resp = client.get("/api/sentiment/VOO")

        assert resp.status_code == 503
        assert "sentiment" in resp.json()["detail"].lower()

    def test_no_rows_is_still_404(self):
        client, router_mod = self._client()

        class _EmptySession:
            def query(self, *a, **k):
                return self

            def filter(self, *a, **k):
                return self

            def order_by(self, *a, **k):
                return self

            def first(self):
                return None

            def close(self):
                pass

        client.app.dependency_overrides[router_mod.get_sentiment_db] = lambda: _EmptySession()
        assert client.get("/api/sentiment/VOO").status_code == 404
