# -*- coding: utf-8 -*-
"""ทดสอบ get_thai_inflation (Roadmap Phase 2 ข้อ 6) — World Bank, fail-soft."""

import pytest

from analysis import macro


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _reset_cache():
    macro._thai_cpi_cache = None
    yield
    macro._thai_cpi_cache = None


def test_parses_world_bank_payload(monkeypatch):
    payload = [{"page": 1}, [{"date": "2025", "value": 1.23}]]
    monkeypatch.setattr(macro.requests, "get", lambda url, timeout=10: _FakeResponse(payload))
    result = macro.get_thai_inflation()
    assert result == {"inflation_pct": 1.23, "year": 2025, "source": "World Bank"}


def test_network_error_fails_soft_to_none(monkeypatch):
    def _boom(url, timeout=10):
        raise ConnectionError("no network")

    monkeypatch.setattr(macro.requests, "get", _boom)
    assert macro.get_thai_inflation() is None


def test_null_value_fails_soft_to_none(monkeypatch):
    payload = [{"page": 1}, [{"date": "2025", "value": None}]]
    monkeypatch.setattr(macro.requests, "get", lambda url, timeout=10: _FakeResponse(payload))
    assert macro.get_thai_inflation() is None


def test_success_is_cached_across_network_loss(monkeypatch):
    payload = [{"page": 1}, [{"date": "2025", "value": 2.0}]]
    monkeypatch.setattr(macro.requests, "get", lambda url, timeout=10: _FakeResponse(payload))
    first = macro.get_thai_inflation()

    def _boom(url, timeout=10):
        raise ConnectionError("no network")

    monkeypatch.setattr(macro.requests, "get", _boom)
    second = macro.get_thai_inflation()
    assert second == first  # ใช้ cache 24 ชม. ไม่ยิงซ้ำ
