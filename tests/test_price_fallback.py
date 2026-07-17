# -*- coding: utf-8 -*-
"""เทสต์แหล่งราคาสำรอง (data/fallback.py) — mock ทุกชั้น ห้ามยิง network จริง."""

from __future__ import annotations

import pytest

import data.fallback as fallback
from data.fetcher import PriceDataUnavailableError

TICKERS = ["VOO", "SCHD"]


def test_empty_input_returns_empty_dict(monkeypatch):
    """ไม่มี ticker → {} เฉย ๆ ไม่ raise."""
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    assert fallback.get_latest_prices_with_fallback([]) == {}


def test_yfinance_ok_does_not_call_stooq(monkeypatch):
    """(ก) yfinance ได้ครบ → ใช้ค่าจาก yfinance และห้ามเรียก Stooq."""
    monkeypatch.setattr(fallback, "_yf_latest_close", lambda tickers: {"SCHD": 30.0, "VOO": 500.0})

    def _must_not_call(tickers):
        raise AssertionError("ห้ามเรียก Stooq เมื่อ yfinance ได้ราคาครบ")

    monkeypatch.setattr(fallback, "fetch_latest_close_stooq", _must_not_call)
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)

    assert fallback.get_latest_prices_with_fallback(TICKERS) == {"SCHD": 30.0, "VOO": 500.0}


def test_yfinance_down_uses_stooq(monkeypatch):
    """(ข) yfinance ล่มทั้งหมด → ได้ราคาจาก Stooq."""
    monkeypatch.setattr(fallback, "_yf_latest_close", lambda tickers: {})
    monkeypatch.setattr(
        fallback, "fetch_latest_close_stooq", lambda tickers: {"VOO": 501.0, "SCHD": 29.5}
    )
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)

    assert fallback.get_latest_prices_with_fallback(TICKERS) == {"VOO": 501.0, "SCHD": 29.5}


def test_all_sources_down_raises_fail_loud(monkeypatch):
    """(ค) ล่มทุกแหล่ง → ต้อง raise PriceDataUnavailableError ห้ามคืนเลขปลอม."""
    monkeypatch.setattr(fallback, "_yf_latest_close", lambda tickers: {})
    monkeypatch.setattr(fallback, "fetch_latest_close_stooq", lambda tickers: {})
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)

    with pytest.raises(PriceDataUnavailableError):
        fallback.get_latest_prices_with_fallback(TICKERS)


def test_stooq_fills_only_missing_tickers(monkeypatch):
    """(ง) yfinance ได้บางตัว → Stooq ถูกเรียกด้วยเฉพาะตัวที่ขาด."""
    calls: dict[str, list[str]] = {}
    monkeypatch.setattr(fallback, "_yf_latest_close", lambda tickers: {"VOO": 500.0})

    def _stooq(tickers):
        calls["stooq_args"] = list(tickers)
        return {"SCHD": 29.5}

    monkeypatch.setattr(fallback, "fetch_latest_close_stooq", _stooq)
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)

    prices = fallback.get_latest_prices_with_fallback(TICKERS)
    assert prices == {"VOO": 500.0, "SCHD": 29.5}
    assert calls["stooq_args"] == ["SCHD"]


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self) -> None:
        return None


def test_stooq_nd_ticker_dropped_not_zero(monkeypatch):
    """(จ) Stooq ตอบ N/D → ticker นั้นหายจากผล ไม่ใช่กลายเป็น 0.0."""
    csv_by_symbol = {
        "voo.us": (
            "Symbol,Date,Time,Open,High,Low,Close,Volume\n"
            "VOO.US,2026-07-15,22:00:00,560.0,565.0,559.0,563.21,123456\n"
        ),
        "schd.us": (
            "Symbol,Date,Time,Open,High,Low,Close,Volume\n"
            "SCHD.US,N/D,N/D,N/D,N/D,N/D,N/D,N/D\n"
        ),
    }

    def _fake_get(url, params=None, timeout=None):
        assert timeout is not None, "ต้องตั้ง timeout เสมอ"
        return _FakeResponse(csv_by_symbol[params["s"]])

    monkeypatch.setattr(fallback.requests, "get", _fake_get)

    prices = fallback.fetch_latest_close_stooq(["VOO", "SCHD"])
    assert prices == {"VOO": 563.21}
    assert "SCHD" not in prices


def test_alphavantage_only_as_last_layer_with_key(monkeypatch):
    """Alpha Vantage ถูกเรียกเฉพาะเมื่อมี key และเฉพาะตัวที่สองชั้นแรกไม่ได้ราคา."""
    monkeypatch.setattr(fallback, "_yf_latest_close", lambda tickers: {})
    monkeypatch.setattr(fallback, "fetch_latest_close_stooq", lambda tickers: {"VOO": 500.5})
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "demo-key")

    called: dict[str, object] = {}

    def _av(tickers, api_key):
        called["args"] = (list(tickers), api_key)
        return {"SCHD": 29.9}

    monkeypatch.setattr(fallback, "_fetch_latest_close_alphavantage", _av)

    prices = fallback.get_latest_prices_with_fallback(TICKERS)
    assert prices == {"VOO": 500.5, "SCHD": 29.9}
    assert called["args"] == (["SCHD"], "demo-key")


def test_get_current_prices_keeps_legacy_contract(monkeypatch):
    """จุด wire (alerts.price_alert.get_current_prices) คง contract เดิม: ล่มหมด → {} ไม่ raise."""
    from alerts import price_alert

    def _raise(tickers):
        raise PriceDataUnavailableError("ทุกแหล่งล่ม")

    monkeypatch.setattr(price_alert, "get_latest_prices_with_fallback", _raise)
    assert price_alert.get_current_prices(["VOO"]) == {}
