# -*- coding: utf-8 -*-
"""B9 (+ C1.5) — ``utils/fx.py``: ห้ามแคช "ดึงไม่สำเร็จ" ยาวเท่า "ดึงสำเร็จ"
และที่มาของอัตราแลกเปลี่ยน (``is_live``) ห้ามหายระหว่างทางไปหน้าจอ/API

อาการที่วัดได้ก่อนแก้ (AUDIT_2026-08-06 B9)::

    _cached = (fallback, False, now)   ← แคชผลของความล้มเหลวเหมือนผลสำเร็จ TTL 3600
      เรียกครั้งที่ 1 (ดึงสดล้ม)       → FxRate(rate=33.5, is_live=False)
      เรียกครั้งที่ 2 (ดึงสดกลับมาแล้ว) → FxRate(rate=33.5, is_live=False)  _fetch_live = 1 ครั้ง
      config 5.0   → FxRate(rate=5.0, is_live=False)     ← ข้าม band 20–50 ของตัวเอง
      config 900.0 → FxRate(rate=900.0, is_live=False)
      tracker.get_total_summary() / /api/portfolio → ไม่มีคีย์ fx_is_live เลย

"ดึงไม่สำเร็จ" (``is_live=False``) · "ไม่ทราบที่มา" (``None``) · "ค่าสด" (``True``)
เป็นคนละความหมายกันทั้งสามอย่าง ห้ามยุบรวมกัน
"""

from __future__ import annotations

import types

import pandas as pd
import pytest

from backend.services import portfolio_service
from portfolio import tracker
from utils import fx

LIVE_RATE = 32.1
CONFIG_RATE = 33.5


@pytest.fixture(autouse=True)
def _reset_fx_cache():
    """แคชของ ``utils/fx`` เป็น global ระดับโมดูล — ต้องล้างและคืนค่าทุกเคส."""
    saved = fx._cached
    fx._cached = None
    yield
    fx._cached = saved


@pytest.fixture
def clock(monkeypatch):
    """นาฬิกาปลอมของ ``utils/fx`` — ทดสอบ TTL ได้โดยไม่ต้อง sleep จริง."""
    state = {"t": 1_000.0}
    monkeypatch.setattr(fx, "time", types.SimpleNamespace(monotonic=lambda: state["t"]))
    return state


@pytest.fixture
def live(monkeypatch):
    """ควบคุมผลของการดึงสด + นับจำนวนครั้งที่ยิงออกไปจริง."""
    state = {"value": None, "calls": 0}

    def _fetch() -> float | None:
        state["calls"] += 1
        return state["value"]

    monkeypatch.setattr(fx, "_fetch_live", _fetch)
    return state


@pytest.fixture
def config_rate(monkeypatch):
    """ตั้งค่า ``display.default_fx_rate`` ที่ ``_config_fallback()`` อ่าน."""

    def _set(value: object) -> None:
        monkeypatch.setattr(fx, "load_config", lambda: {"display": {"default_fx_rate": value}})

    _set(CONFIG_RATE)
    return _set


class TestFailureIsNotCachedLikeSuccess:
    """ค่าสำรองคือ "ยังดึงไม่ได้" ไม่ใช่คำตอบ — ห้ามค้างเป็นผลลัพธ์ 1 ชั่วโมง."""

    def test_live_rate_returns_as_soon_as_the_source_recovers(self, clock, live, config_rate):
        """หัวใจของ B9: ดึงสดล้มรอบแรก แล้วแหล่งข้อมูลกลับมา ต้องได้ค่าสดกลับมาเอง."""
        live["value"] = None
        first = fx.get_usdthb()
        assert first.is_live is False
        assert first.rate == CONFIG_RATE

        # 5 นาทีต่อมา — น้อยกว่า TTL 1 ชม. ของโค้ดเดิม จึงเป็นจุดที่บั๊กแสดงตัว
        live["value"] = LIVE_RATE
        clock["t"] += 300.0

        second = fx.get_usdthb()
        assert second.is_live is True, "ค่าสดกลับมาแล้วแต่โค้ดยังคืนค่าสำรองที่แคชไว้"
        assert second.rate == LIVE_RATE
        assert live["calls"] == 2, "ต้องลองดึงสดใหม่หลังหมดหน้าต่างสั้นของค่าสำรอง"

    def test_fallback_window_is_far_shorter_than_the_live_window(self):
        assert fx.FALLBACK_CACHE_TTL_SEC <= 60
        assert fx.FALLBACK_CACHE_TTL_SEC < fx.CACHE_TTL_SEC

    def test_fallback_is_reused_inside_its_window_so_the_source_is_not_hammered(
        self, clock, live, config_rate
    ):
        """ไม่แคชเลยก็ผิดคนละแบบ — ทุกครั้งที่เรนเดอร์จะยิง yfinance ใหม่."""
        live["value"] = None
        for _ in range(5):
            assert fx.get_usdthb() == fx.FxRate(CONFIG_RATE, False)
        assert live["calls"] == 1

    def test_live_rate_stays_cached_for_an_hour(self, clock, live, config_rate):
        live["value"] = LIVE_RATE
        assert fx.get_usdthb() == fx.FxRate(LIVE_RATE, True)

        clock["t"] += fx.CACHE_TTL_SEC - 1
        assert fx.get_usdthb() == fx.FxRate(LIVE_RATE, True)
        assert live["calls"] == 1, "ค่าสดที่ยังไม่หมดอายุต้องไม่ยิงซ้ำ"

        clock["t"] += 2
        live["value"] = 34.0
        assert fx.get_usdthb() == fx.FxRate(34.0, True)
        assert live["calls"] == 2

    def test_force_refresh_still_bypasses_every_cache(self, clock, live, config_rate):
        live["value"] = None
        fx.get_usdthb()
        live["value"] = LIVE_RATE
        assert fx.get_usdthb(force_refresh=True) == fx.FxRate(LIVE_RATE, True)


class TestConfigFallbackObeysTheSameBand:
    """``_config_fallback()`` ข้าม sanity band ของตัวเอง — ค่าสำรองพัง = ต้องดัง."""

    @pytest.mark.parametrize("bad", [5.0, 900.0, 0.0, -3.0, float("nan"), float("inf")])
    def test_out_of_band_config_rate_fails_loudly(self, clock, live, config_rate, bad):
        live["value"] = None
        config_rate(bad)
        with pytest.raises(fx.FxRateUnavailable) as exc:
            fx.get_usdthb()
        message = str(exc.value)
        assert "default_fx_rate" in message
        assert f"{fx.MIN_RATE:.0f}" in message and f"{fx.MAX_RATE:.0f}" in message

    def test_in_band_config_rate_is_used_and_flagged(self, clock, live, config_rate):
        live["value"] = None
        config_rate(21.0)
        assert fx.get_usdthb() == fx.FxRate(21.0, False)

    def test_unreadable_config_uses_the_in_band_default(self, clock, live, config_rate):
        live["value"] = None
        config_rate("ไม่ใช่ตัวเลข")
        result = fx.get_usdthb()
        assert result.is_live is False
        assert fx.MIN_RATE <= result.rate <= fx.MAX_RATE

    def test_bad_fallback_is_not_cached_as_an_answer(self, clock, live, config_rate):
        """ค่าสำรองที่ใช้ไม่ได้ต้องไม่ค้างในแคช — พอดึงสดได้ต้องกลับมาปกติทันที."""
        live["value"] = None
        config_rate(900.0)
        with pytest.raises(fx.FxRateUnavailable):
            fx.get_usdthb()

        live["value"] = LIVE_RATE
        assert fx.get_usdthb() == fx.FxRate(LIVE_RATE, True)


class TestSourceOfRate:
    """``source_of()`` = ถามที่มาโดยไม่ยิงเน็ตซ้ำ · ไม่รู้ต้องตอบ ``None`` ห้ามเดา."""

    def test_reports_live_and_fallback(self, clock, live, config_rate):
        live["value"] = LIVE_RATE
        assert fx.source_of(fx.get_usdthb().rate) is True

        fx._cached = None
        live["value"] = None
        assert fx.source_of(fx.get_usdthb().rate) is False

    def test_unknown_rate_is_none_not_false(self, clock, live, config_rate):
        live["value"] = LIVE_RATE
        fx.get_usdthb()
        assert fx.source_of(99.0) is None
        fx._cached = None
        assert fx.source_of(LIVE_RATE) is None

    def test_does_not_fetch(self, clock, live, config_rate):
        assert fx.source_of(33.0) is None
        assert live["calls"] == 0


def _ledger() -> pd.DataFrame:
    """สมุดจำลอง VOO 10 หุ้น @400 USD (fx 35.00) — ไม่แตะไฟล์จริงของผู้ใช้."""
    return pd.DataFrame(
        {
            "tx_id": ["a1"],
            "date": [pd.Timestamp("2024-01-15")],
            "ticker": ["VOO"],
            "shares": [10.0],
            "price_usd": [400.0],
            "fx_rate_thb": [35.0],
            "amount_thb": [140210.0],
            "fee_thb": [210.0],
            "note": [""],
            "tx_type": ["buy"],
        }
    )


@pytest.fixture
def stub_ledger(monkeypatch):
    monkeypatch.setattr(tracker, "_load_transactions", _ledger)
    monkeypatch.setattr(tracker, "_get_latest_prices", lambda tickers: {"VOO": 500.0})


class TestCallersKeepTheSource:
    """C1.5 — ``tracker`` และ ``/api/portfolio`` ทิ้ง ``is_live`` ทั้งคู่."""

    def test_total_summary_reports_the_rate_and_that_it_is_a_fallback(
        self, clock, live, config_rate, stub_ledger
    ):
        live["value"] = None
        totals = tracker.get_total_summary()
        assert totals["fx_rate_thb"] == CONFIG_RATE
        assert totals["fx_is_live"] is False, (
            "มูลค่า/กำไรเป็นบาททั้งก้อนคิดจากค่าสำรอง ต้องบอกผู้ใช้ ห้ามเงียบ"
        )

    def test_total_summary_reports_a_live_rate(self, clock, live, config_rate, stub_ledger):
        live["value"] = LIVE_RATE
        totals = tracker.get_total_summary()
        assert totals["fx_rate_thb"] == LIVE_RATE
        assert totals["fx_is_live"] is True

    def test_rate_actually_used_is_the_rate_reported(self, clock, live, config_rate, stub_ledger):
        """เลขที่รายงานต้องเป็นเลขเดียวกับที่คูณเข้ามูลค่าจริง ไม่ใช่ดึงมาใหม่คนละครั้ง."""
        live["value"] = LIVE_RATE
        totals = tracker.get_total_summary()
        assert totals["current_value_thb"] == pytest.approx(
            10.0 * 500.0 * float(totals["fx_rate_thb"])
        )

    def test_unknown_source_is_none_not_false(self, monkeypatch, stub_ledger):
        """ผู้เรียกที่จัดหาอัตราเอง = **ไม่ทราบที่มา** ห้ามรายงานว่าเป็นค่าสำรอง."""
        monkeypatch.setattr(tracker, "_get_usdthb_rate", lambda: 34.0)
        totals = tracker.get_total_summary()
        assert totals["fx_rate_thb"] == 34.0
        assert totals["fx_is_live"] is None

    def test_empty_ledger_keeps_the_same_keys(self, monkeypatch):
        monkeypatch.setattr(tracker, "_load_transactions", lambda: _ledger().iloc[0:0])
        totals = tracker.get_total_summary()
        assert totals["fx_rate_thb"] is None
        assert totals["fx_is_live"] is None

    def test_api_summary_carries_the_source(self, clock, live, config_rate, stub_ledger):
        live["value"] = None
        payload = portfolio_service.get_portfolio_summary()
        assert payload["fx_rate_thb"] == CONFIG_RATE
        assert payload["fx_is_live"] is False

    def test_api_summary_reports_live_source(self, clock, live, config_rate, stub_ledger):
        live["value"] = LIVE_RATE
        payload = portfolio_service.get_portfolio_summary()
        assert payload["fx_rate_thb"] == LIVE_RATE
        assert payload["fx_is_live"] is True
