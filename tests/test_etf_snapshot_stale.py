# -*- coding: utf-8 -*-
"""AUDIT_2026-08-06 ข้อ A6 (H10 + M-ETF-1) — แท่งราคาที่ไม่มีจริง ห้ามกลายเป็น "+0.00%".

หลักที่คุม (กฎโปรเจกต์ข้อ 1–3):

- ``.ffill()`` ที่แหล่งกลาง (``_prices_df``) เติมราคาของเมื่อวานลงในวันที่ผู้ให้ข้อมูล
  ยังไม่ส่งแท่งมา → ``iloc[-1] == iloc[-2]`` เป๊ะ → ``change_pct 0.0`` + ประทับ ``date``
  เป็นวันล่าสุดของ **ticker อื่น** = ตัวเลขและวันที่ที่ถูกกุขึ้น
- แท่งปลอมยังไหลเข้า RSI/MA ของ ``get_etf_technical`` โดยติดป้าย ``data_ok: True``
- "ดึงไม่สำเร็จ" ≠ "ไม่มีข้อมูล" — ticker เดียวที่ไม่มีราคาต้องรายงานเป็นรายตัว
  ไม่ใช่ลาก endpoint ทั้งตัวลงไปเป็น 500 (M-ETF-1)

เทสต์ทั้งไฟล์ stub เฉพาะ ``fetch_adjusted_close_data`` — โค้ด service/router ของจริง
ทั้งหมด ไม่ยิงเน็ต ไม่แตะไฟล์ข้อมูลจริง
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from analysis.ta_compat import ta  # noqa: E402
from backend.services import etf_service  # noqa: E402

TICKERS = ["VOO", "SCHD", "GLDM"]
ROWS = 260  # > 200 แท่ง เพื่อให้ get_etf_technical คำนวณ MA200 ได้


def _base_frame(rows: int = ROWS) -> pd.DataFrame:
    """ราคาสมมติที่แกว่งจริง — ถ้าราบเรียบ RSI ก่อน/หลัง ffill จะเท่ากันโดยบังเอิญ."""
    idx = pd.date_range("2025-01-01", periods=rows, freq="B")
    steps = np.arange(rows, dtype=float)
    data = {
        "VOO": 400.0 + steps * 0.5 + np.sin(steps / 3.0) * 6.0,
        "SCHD": 80.0 + steps * 0.05 + np.cos(steps / 5.0) * 2.0,
        "GLDM": 55.0 + steps * 0.02 + np.sin(steps / 4.0) * 3.0,
    }
    return pd.DataFrame(data, index=idx)


@pytest.fixture
def stub_prices(monkeypatch):
    """ให้ ``_prices_df``/``_prices_df_for_returns`` ใช้เฟรมที่เรากำหนดเอง."""

    def _install(frame: pd.DataFrame):
        monkeypatch.setattr(etf_service, "get_tickers", lambda: list(frame.columns))
        monkeypatch.setattr(
            etf_service,
            "fetch_adjusted_close_data",
            lambda tickers=None, years=10: frame.copy(),
        )
        return frame

    return _install


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.routers import etf as etf_router

    app = FastAPI()
    app.include_router(etf_router.router)
    return TestClient(app, raise_server_exceptions=False)


# ------------------------------------------------------- H10: แหล่งกลางต้องไม่ ffill


def test_prices_df_keeps_missing_bars_as_nan(stub_prices):
    """ช่องที่ผู้ให้ข้อมูลไม่ส่งมา ต้องยังเป็น NaN ตอนออกจากแหล่งกลาง."""
    frame = _base_frame()
    frame.iloc[-3:, frame.columns.get_loc("GLDM")] = np.nan
    stub_prices(frame)

    out = etf_service._prices_df()

    assert out["GLDM"].isna().sum() == 3, "แหล่งกลางยัง ffill อยู่ — แท่งที่ไม่มีจริงถูกเติมขึ้นมา"
    assert out["VOO"].isna().sum() == 0


def test_stale_ticker_is_not_reported_as_zero_change(stub_prices):
    """GLDM ไม่มีแท่ง 3 วันท้าย → ห้ามได้ 0.00% และห้ามใช้วันที่ของ ticker อื่น."""
    frame = _base_frame()
    real_tail = frame["GLDM"].iloc[-4:-3]  # แท่งจริงล่าสุดของ GLDM
    frame.iloc[-3:, frame.columns.get_loc("GLDM")] = np.nan
    stub_prices(frame)

    snap = etf_service.get_etf_daily_eod_snapshot()

    gldm = snap["GLDM"]
    assert gldm["change_pct"] != 0.0, "แท่งที่ถูกเติมทำให้ราคาสองแท่งท้ายเท่ากัน → 0.00% ที่กุขึ้น"
    assert gldm["price"] == round(float(real_tail.iloc[0]), 2)
    assert gldm["date"] == pd.Timestamp(real_tail.index[0]).strftime("%d/%m/%Y"), (
        "date ถูกประทับเป็นวันล่าสุดของทั้งเฟรม (ของ ticker อื่น) ไม่ใช่ของ GLDM เอง"
    )
    assert gldm["stale"] is True
    assert gldm["data_ok"] is False

    fresh = snap["VOO"]
    assert fresh["stale"] is False
    assert fresh["data_ok"] is True
    assert fresh["date"] == pd.Timestamp(frame.index[-1]).strftime("%d/%m/%Y")


def test_single_real_bar_gives_null_change_not_zero(stub_prices):
    """เหลือแท่งจริงแท่งเดียว → เทียบกับอะไรไม่ได้ ต้องเป็น null ไม่ใช่ 0.0."""
    frame = _base_frame()
    col = frame.columns.get_loc("GLDM")
    frame.iloc[:-1, col] = np.nan  # เหลือแท่งเดียวที่แถวสุดท้าย
    stub_prices(frame)

    gldm = etf_service.get_etf_daily_eod_snapshot()["GLDM"]

    assert gldm["change_pct"] is None
    assert gldm["previous_close"] is None
    assert gldm["price"] == round(float(frame["GLDM"].iloc[-1]), 2)


def test_non_positive_previous_close_gives_null_change(stub_prices):
    """ราคาอ้างอิง <= 0 คือข้อมูลเสีย — ``chg = 0.0`` เดิมคือการกุตัวเลข."""
    frame = _base_frame()
    col = frame.columns.get_loc("GLDM")
    frame.iloc[-2, col] = 0.0
    stub_prices(frame)

    gldm = etf_service.get_etf_daily_eod_snapshot()["GLDM"]

    assert gldm["change_pct"] is None, "p_y <= 0 ถูกแปลงเป็น 0.00% แทนที่จะเป็น 'ไม่รู้'"


# ------------------------------------------------ M-ETF-1: ticker เดียวพัง ห้ามล้มทั้ง endpoint


def test_all_nan_ticker_reports_per_ticker_error(stub_prices):
    frame = _base_frame()
    frame["GLDM"] = np.nan
    stub_prices(frame)

    snap = etf_service.get_etf_daily_eod_snapshot()

    assert snap["GLDM"]["data_ok"] is False
    assert "error" in snap["GLDM"]
    assert snap["GLDM"]["price"] is None
    assert snap["VOO"]["data_ok"] is True, "ticker ที่ดึงได้ปกติต้องไม่หายไปด้วย"


@pytest.mark.parametrize("bad", [np.nan, np.inf])
def test_endpoint_stays_200_when_one_ticker_has_no_data(stub_prices, bad):
    """NaN และ inf ล้วนทำให้ ``JSONResponse`` (allow_nan=False) ล้มทั้ง endpoint."""
    frame = _base_frame()
    frame["GLDM"] = bad
    stub_prices(frame)

    resp = _client().get("/api/etf/daily-snapshot")

    assert resp.status_code == 200, f"ทั้ง endpoint ล้มเพราะ ticker เดียว: {resp.text}"
    body = resp.json()["data"]
    assert json.dumps(body)  # ต้องไม่มี NaN หลุดเข้า JSON
    assert body["VOO"]["price"] is not None
    assert body["GLDM"]["price"] is None
    assert "error" in body["GLDM"]


# ------------------------------------------------- H10 ชั้นที่ 2: RSI/MA ต้องมาจากแท่งจริง


def test_technical_uses_real_bars_only(stub_prices):
    frame = _base_frame()
    frame.iloc[-3:, frame.columns.get_loc("GLDM")] = np.nan
    stub_prices(frame)

    tech = etf_service.get_etf_technical()

    real = frame["GLDM"].dropna()
    assert tech["GLDM"]["ma50"] == pytest.approx(float(ta.sma(real, length=50).iloc[-1])), (
        "MA50 ถูกคำนวณบนแท่งที่ ffill เติมขึ้นมา (ราคาเดิมซ้ำ 3 วัน ถ่วงค่าเฉลี่ย)"
    )
    assert tech["GLDM"]["ma200"] == pytest.approx(float(ta.sma(real, length=200).iloc[-1]))
    assert tech["GLDM"]["price"] == pytest.approx(float(real.iloc[-1]))
    # RSI ของ Wilder ไม่ไวต่อแท่งที่ราคานิ่ง (gain/loss หารกันแล้วตัดกัน) จึงไม่ใช้เป็น
    # ตัวจับบั๊ก — ตรึงไว้เฉย ๆ ว่าต้องมาจากซีรีส์แท่งจริงเหมือนกัน
    assert tech["GLDM"]["rsi14"] == pytest.approx(float(ta.rsi(real, length=14).iloc[-1]))


def test_technical_flags_stale_ticker(stub_prices):
    frame = _base_frame()
    frame.iloc[-3:, frame.columns.get_loc("GLDM")] = np.nan
    stub_prices(frame)

    tech = etf_service.get_etf_technical()

    assert tech["GLDM"]["stale"] is True
    assert tech["GLDM"]["as_of"] == pd.Timestamp(frame["GLDM"].dropna().index[-1]).strftime(
        "%d/%m/%Y"
    )
    assert tech["VOO"]["stale"] is False


# --------------------------------------------- risk/correlation ต้องไม่เปลี่ยนค่าเงียบ ๆ


def test_risk_and_correlation_still_fill_gaps(stub_prices):
    """ย้าย ffill มาไว้ที่จุดที่ต้องใช้ — ตัวเลขความเสี่ยงต้องเท่าเดิมทุกหลัก."""
    from analysis.correlation import calculate_correlation_matrix
    from analysis.risk import calculate_risk_metrics
    from utils.cache import clear_all_caches

    frame = _base_frame()
    frame.iloc[-3:, frame.columns.get_loc("GLDM")] = np.nan
    stub_prices(frame)

    risk = etf_service.get_etf_risk()
    corr = etf_service.get_etf_correlation()

    clear_all_caches()
    expected_risk = calculate_risk_metrics(frame.ffill())
    clear_all_caches()
    expected_corr = calculate_correlation_matrix(frame.ffill())

    assert risk["Volatility"]["GLDM"] == pytest.approx(float(expected_risk["Volatility"]["GLDM"]))
    assert corr["VOO"]["GLDM"] == pytest.approx(float(expected_corr["VOO"]["GLDM"]))
