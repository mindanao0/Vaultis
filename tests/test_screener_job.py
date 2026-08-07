# -*- coding: utf-8 -*-
"""AUDIT_2026-08-06 ข้อ B6 — งานสแกนรายวันของ screener.

สามอาการที่วัดได้ก่อนแก้ (สตับ ``yfinance.download`` ให้ ``time.sleep(0.03)``
แทน network จริง — ของจริงช้ากว่านี้มาก):

* **B6.1** ``run_daily_screener`` เป็น ``async def`` ที่ APScheduler รันบน event loop
  **เดียวกับ uvicorn** แต่ข้างในเรียก ``engine.run()`` ซึ่งยิง yfinance แบบ sync ตรง ๆ::

      screener ใช้เวลา 0.948s | heartbeat ตื่น 4 ครั้ง | loop ถูกบล็อกนานสุด 0.954s
      job_defaults = {'misfire_grace_time': 1, ...}   ← ดีฟอลต์ของ APScheduler
      listeners = []                                  ← งานที่ถูกข้าม/พังไม่มีใครรายงาน

  กฎนี้โปรเจกต์เขียนกำกับไว้เองแล้วที่ ``backend/routers/websocket.py``
  ("yfinance เป็น sync I/O — ต้องออกจาก event loop ไม่งั้น API ทั้งตัวค้าง")

* **B6.2** วน 4 พรีเซ็ต × 5 สัญลักษณ์ = ``{'VOO': 4, 'QQQM': 4, ...}`` รวม 20 คำขอ
  ต่อเช้า ทั้งที่ต้องการจริง 5 (ตัวคูณความเสี่ยงโดน rate limit)

* **B6.3** ``_compute_signal_strength`` บวกโบนัสทั้งสองฝั่งเสมอ โดยไม่รู้ว่าพรีเซ็ตนั้น
  มองหาอะไร และใช้เกณฑ์ 35/65 ซึ่งเป็นตัวเลขชุดที่สองซ้อนกับนิยามกลาง
  ``technical/signal_rules`` (30/70)::

      uptrend   RSI=58.48 -> 7.0
      hot       RSI=75.57 -> 8.0   ← พรีเซ็ตฝั่งซื้อให้คะแนน "ร้อนแรง" สูงกว่ากลาง ๆ
      ย่อเบา ๆ  RSI=32.28 -> 8.5   ← 32.28 ไม่ใช่ oversold ตามนิยามกลาง (< 30)

**ห้ามยิงของจริง** ทุกเคสสตับ yfinance / Telegram / Discord / LLM ครบ
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ต้องตั้งก่อน import backend.main — ห้ามให้ชุดเทสต์แตะฐาน SQLite ตัวจริงของผู้ใช้
# (AUDIT_2026-08-06 ข้อ 0.1 / H1 — ตาข่ายหลักคือ tests/test_db_isolation.py)
if "/data" in (os.getenv("VAULTIS_DB_PATH") or ""):
    os.environ["VAULTIS_DB_PATH"] = "/tmp/test_vaultis_screener_job.db"

import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from apscheduler.events import (  # noqa: E402
    EVENT_JOB_ERROR,
    EVENT_JOB_MISSED,
    JobExecutionEvent,
)
from fastapi.testclient import TestClient  # noqa: E402

import backend.screener.engine as engine_mod  # noqa: E402
import backend.screener.scheduler_job as scheduler_job  # noqa: E402
import technical.signal_rules as signal_rules  # noqa: E402
from analysis.ta_compat import ta  # noqa: E402
from backend.screener.engine import ScreenerEngine  # noqa: E402
from backend.screener.models import ScreenerPreset, ScreenerRule  # noqa: E402

# --- ข้อมูลสังเคราะห์: RSI ของแต่ละเฟรมวัดจาก analysis.ta_compat.ta.rsi ของจริง ---


def _frame(values: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(values), freq="B")
    return pd.DataFrame({"Close": values, "Volume": [1_000_000.0] * len(values)}, index=idx)


_BASE = [100 + 0.20 * i + (1.5 if i % 2 else 0.0) for i in range(245)]


def _with_tail(up: float, down: float, n: int = 20) -> pd.DataFrame:
    vals, v = [], _BASE[-1]
    for i in range(n):
        v += up if i % 3 else -down
        vals.append(v)
    return _frame(_BASE + vals)


def _with_dip(pct: float, n: int = 15) -> pd.DataFrame:
    import numpy as np

    return _frame(_BASE + list(np.linspace(_BASE[-1], _BASE[-1] * pct, n)))


def neutral_frame() -> pd.DataFrame:
    """RSI ≈ 58.5 — ไม่เข้าเงื่อนไขโบนัสฝั่งใดทั้งเกณฑ์เก่า (35/65) และใหม่ (30/70)."""
    return _frame([100 + 0.20 * i + (1.5 if i % 2 else 0.0) for i in range(260)])


def hot_frame() -> pd.DataFrame:
    """RSI ≈ 75.6 — overbought ตามนิยามกลาง (> 70)."""
    return _with_tail(up=1.2, down=0.4)


def mildly_hot_frame() -> pd.DataFrame:
    """RSI ≈ 69.7 — เกินเกณฑ์เก่า 65 แต่ยังไม่ถึงนิยามกลาง 70."""
    return _with_tail(up=0.8, down=0.4)


def oversold_frame() -> pd.DataFrame:
    """RSI ≈ 20.5 — oversold ตามนิยามกลาง (< 30)."""
    return _with_dip(0.88)


def mildly_weak_frame() -> pd.DataFrame:
    """RSI ≈ 32.3 — ต่ำกว่าเกณฑ์เก่า 35 แต่ยังไม่ถึงนิยามกลาง 30."""
    return _with_dip(0.95)


def _rsi(df: pd.DataFrame) -> float:
    return float(ta.rsi(df["Close"], length=14).iloc[-1])


PASS_RULE = ScreenerRule("price_vs_ma200", "gt", None, "Price above MA200")
FAIL_RULE = ScreenerRule("price_vs_ma200", "lt", None, "Price below MA200")

SYMBOLS = ["VOO", "QQQM", "SCHD", "XLV", "GLDM"]


def _preset(rules, logic="AND", **kw) -> ScreenerPreset:
    return ScreenerPreset(name="unit", rules=rules, logic=logic, description="เทสต์", **kw)


# --- สตับ: ห้ามยิง network / Telegram / Discord / LLM -------------------------


class _FakeHistory:
    def __init__(self) -> None:
        self.saved: list[str] = []

    async def save_results(self, results, preset_name):
        self.saved.append(preset_name)


class _FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def build_ai_summary(self, results, preset_name, user_initiated=False):
        return f"สรุปสตับ ({len(results)} สัญญาณ)"

    async def send_telegram(self, results, ai_summary):
        self.sent.append(ai_summary)
        return True


@pytest.fixture
def daily_job(monkeypatch):
    """สตับทุกทางออกของ ``run_daily_screener`` แล้วคืนตัวนับคำขอราคา."""
    calls: Counter[str] = Counter()
    frames: dict[str, pd.DataFrame] = {s: neutral_frame() for s in SYMBOLS}
    delay = {"seconds": 0.03}

    def _fake_download(symbol, **_kw):
        calls[symbol] += 1
        time.sleep(delay["seconds"])
        if symbol not in frames:
            return pd.DataFrame()  # yfinance คืนเฟรมว่างเมื่อดึงไม่ได้
        return frames[symbol]

    monkeypatch.setattr(engine_mod.yfinance, "download", _fake_download)
    history, notifier = _FakeHistory(), _FakeNotifier()
    monkeypatch.setattr(scheduler_job, "ScreenerHistoryService", lambda: history)
    monkeypatch.setattr(scheduler_job, "ScreenerNotifier", lambda: notifier)

    class _Handle:
        pass

    handle = _Handle()
    handle.calls = calls
    handle.frames = frames
    handle.delay = delay
    handle.history = history
    handle.notifier = notifier
    return handle


async def _heartbeat(stop: asyncio.Event, gaps: list[float], interval: float = 0.005):
    """ตัวแทน "คำขออื่นที่วิ่งอยู่บน loop เดียวกัน" — วัดว่า loop ถูกบล็อกนานแค่ไหน."""
    last = time.perf_counter()
    while not stop.is_set():
        await asyncio.sleep(interval)
        now = time.perf_counter()
        gaps.append(now - last)
        last = now


# --- B6.1 -------------------------------------------------------------------


class TestDailyJobLeavesTheEventLoopFree:
    def test_engine_runs_off_the_event_loop(self, daily_job, monkeypatch):
        """ตรวจแบบไม่พึ่งเวลา: ในเธรด worker จะไม่มี running loop ให้เห็น."""
        seen: list[bool] = []
        real_run = ScreenerEngine.run

        def _probe(self, symbols, preset, *a, **kw):
            try:
                asyncio.get_running_loop()
                seen.append(True)  # ยังอยู่บน event loop = บล็อกทั้ง API
            except RuntimeError:
                seen.append(False)
            return real_run(self, symbols, preset, *a, **kw)

        monkeypatch.setattr(ScreenerEngine, "run", _probe)
        asyncio.run(scheduler_job.run_daily_screener())

        assert seen, "engine.run ไม่ถูกเรียกเลย"
        assert not any(seen), "engine.run ยังทำงานบน event loop ของ FastAPI"

    def test_other_requests_keep_getting_served(self, daily_job):
        """heartbeat ต้องตื่นได้ตลอดที่ screener ทำงาน (เดิมถูกบล็อกยาว 0.95 วิรวดเดียว)."""

        async def _main():
            gaps: list[float] = []
            stop = asyncio.Event()
            beat = asyncio.create_task(_heartbeat(stop, gaps))
            await asyncio.sleep(0.02)
            await scheduler_job.run_daily_screener()
            stop.set()
            await beat
            return gaps

        gaps = asyncio.run(_main())
        worst = max(gaps) if gaps else float("inf")
        assert len(gaps) >= 10, f"heartbeat ตื่นแค่ {len(gaps)} ครั้ง — loop ถูกยึด"
        assert worst < 0.15, f"loop ถูกบล็อกยาว {worst:.3f}s"


class TestSchedulerSurvivesABusyLoop:
    def test_misfire_grace_is_not_the_one_second_default(self):
        from backend.main import scheduler

        grace = scheduler._job_defaults.get("misfire_grace_time")
        assert grace is not None and grace >= 3600, (
            f"misfire_grace_time={grace} — งาน 07:00 ที่ตื่นช้ากว่านี้ถูกข้ามเงียบ ๆ ทั้งวัน"
        )
        assert scheduler._job_defaults.get("coalesce") is True

    def test_missed_and_failed_jobs_have_a_listener(self):
        from backend.main import scheduler

        masks = [mask for _cb, mask in scheduler._listeners]
        assert any(m & EVENT_JOB_MISSED for m in masks), "ไม่มีใครรายงานงานที่ถูกข้าม"
        assert any(m & EVENT_JOB_ERROR for m in masks), "ไม่มีใครรายงานงานที่พัง"

    def test_missed_job_is_logged_loudly(self, caplog):
        from datetime import datetime

        from backend.main import scheduler

        event = JobExecutionEvent(EVENT_JOB_MISSED, "run_daily_screener", "default", datetime(2026, 8, 6, 7, 0))
        with caplog.at_level(logging.ERROR):
            for callback, mask in scheduler._listeners:
                if mask & EVENT_JOB_MISSED:
                    callback(event)
        assert any(
            r.levelno >= logging.ERROR and "run_daily_screener" in r.getMessage() for r in caplog.records
        ), "งานที่ถูกข้ามต้องดังออกมา ไม่ใช่หายเงียบ"

    def test_failed_job_is_logged_loudly(self, caplog):
        from datetime import datetime

        from backend.main import scheduler

        event = JobExecutionEvent(
            EVENT_JOB_ERROR,
            "run_daily_screener",
            "default",
            datetime(2026, 8, 6, 7, 0),
            exception=RuntimeError("ดึงราคาไม่สำเร็จ"),
        )
        with caplog.at_level(logging.ERROR):
            for callback, mask in scheduler._listeners:
                if mask & EVENT_JOB_ERROR:
                    callback(event)
        assert any(r.levelno >= logging.ERROR for r in caplog.records)


class TestEndpointsLeaveTheEventLoopFree:
    """``/api/screener/*`` เป็น ``async def`` ที่เรียก ``_engine.run()`` ตรง ๆ เช่นกัน."""

    @pytest.fixture
    def client(self, monkeypatch):
        from backend.main import app
        from backend.routers import screener as screener_router
        from backend.security import require_api_key

        seen: list[bool] = []

        def _probe(symbols, preset, *a, **kw):
            try:
                asyncio.get_running_loop()
                seen.append(True)
            except RuntimeError:
                seen.append(False)
            return []

        monkeypatch.setattr(screener_router._engine, "run", _probe)

        async def _no_ai(results, preset_name, user_initiated=False):
            return "สรุปสตับ"

        monkeypatch.setattr(screener_router._notifier, "build_ai_summary", _no_ai)
        app.dependency_overrides[require_api_key] = lambda: None
        # ไม่ใช้ ``with TestClient(app)`` เพราะนั่นจะรัน lifespan = สตาร์ท APScheduler จริง
        # ทุกเคส แล้วตัวที่สองเจอ "Event loop is closed" (scheduler ผูกกับ loop ของเคสแรก)
        try:
            yield TestClient(app), seen
        finally:
            app.dependency_overrides.pop(require_api_key, None)

    def test_run_endpoint_offloads_the_engine(self, client):
        c, seen = client
        resp = c.post("/api/screener/run", json={"symbols": ["VOO"], "preset": "oversold_momentum"})
        assert resp.status_code == 200, resp.text
        assert seen == [False], "POST /api/screener/run ยังยิง yfinance บน event loop"

    def test_custom_endpoint_offloads_the_engine(self, client):
        c, seen = client
        resp = c.post(
            "/api/screener/custom",
            json={
                "symbols": ["VOO"],
                "rules": [{"field": "price_vs_ma200", "operator": "gt", "value": None, "description": "x"}],
                "logic": "AND",
            },
        )
        assert resp.status_code == 200, resp.text
        assert seen == [False], "POST /api/screener/custom ยังยิง yfinance บน event loop"

    def test_response_separates_unreadable_symbols_from_no_signal(self, monkeypatch):
        """ยิงผ่านเอนจินจริง (สตับแค่ ``_fetch_df``) — ตัวที่ดึงไม่ได้ต้องไม่กลืนหายไป."""
        from backend.main import app
        from backend.routers import screener as screener_router
        from backend.security import require_api_key

        def _fetch(symbol: str):
            if symbol == "VOO":
                return neutral_frame()
            raise ValueError(f"ดึงข้อมูลราคา {symbol} ไม่สำเร็จ (ผลว่าง)")

        monkeypatch.setattr(screener_router._engine, "_fetch_df", _fetch)

        async def _no_ai(results, preset_name, user_initiated=False):
            return "สรุปสตับ"

        monkeypatch.setattr(screener_router._notifier, "build_ai_summary", _no_ai)
        app.dependency_overrides[require_api_key] = lambda: None
        try:
            resp = TestClient(app).post(
                "/api/screener/custom",
                json={
                    "symbols": ["VOO", "SCHD"],
                    "rules": [
                        {"field": "price_vs_ma200", "operator": "gt", "value": None, "description": "เหนือ MA200"}
                    ],
                    "logic": "AND",
                },
            )
        finally:
            app.dependency_overrides.pop(require_api_key, None)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [r["symbol"] for r in body["results"]] == ["VOO"]
        assert any("SCHD" in e for e in body["errors"]), "SCHD ตรวจไม่ได้แต่ response ไม่บอก"


# --- B6.2 -------------------------------------------------------------------


class TestPricesAreFetchedOncePerSymbol:
    def test_each_symbol_is_downloaded_once_per_morning(self, daily_job):
        asyncio.run(scheduler_job.run_daily_screener())
        assert dict(daily_job.calls) == {s: 1 for s in SYMBOLS}, (
            f"ดึงซ้ำ {sum(daily_job.calls.values())} ครั้งสำหรับ {len(SYMBOLS)} สัญลักษณ์"
        )

    def test_every_preset_still_sees_every_symbol(self, daily_job, monkeypatch):
        """ดึงครั้งเดียวแล้วใช้ซ้ำ — ห้ามกลายเป็น "พรีเซ็ตหลังไม่มีข้อมูล"."""
        seen: list[tuple[str, tuple[str, ...]]] = []
        real_run = ScreenerEngine.run

        def _probe(self, symbols, preset, *a, **kw):
            seen.append((preset.name, tuple(symbols)))
            return real_run(self, symbols, preset, *a, **kw)

        monkeypatch.setattr(ScreenerEngine, "run", _probe)
        asyncio.run(scheduler_job.run_daily_screener())

        assert len(seen) == 4, "ต้องรันครบทุกพรีเซ็ตเหมือนเดิม"
        for _name, symbols in seen:
            assert tuple(symbols) == tuple(SYMBOLS)

    def test_a_symbol_that_cannot_be_fetched_is_reported_not_dropped(self, daily_job, caplog):
        """"ดึงไม่สำเร็จ" ≠ "ไม่มีสัญญาณ" — สัญลักษณ์ที่หายต้องถูกรายงานออกไป."""
        del daily_job.frames["GLDM"]
        with caplog.at_level(logging.ERROR):
            asyncio.run(scheduler_job.run_daily_screener())
        assert any("GLDM" in r.getMessage() for r in caplog.records), (
            "GLDM ดึงไม่ได้แต่ไม่มีใครรายงาน — ผู้ใช้แยกไม่ออกจาก 'ไม่มีสัญญาณ'"
        )
        assert daily_job.calls["GLDM"] == 1


class TestEngineReportsUnreadableSymbols:
    """``run()`` ต้องมีช่องบอกผู้เรียกว่าสัญลักษณ์ไหนตรวจไม่ได้ (AUDIT ข้อ 0-D ข้อ 3)."""

    @pytest.fixture
    def engine(self, monkeypatch):
        eng = ScreenerEngine()
        frames: dict[str, pd.DataFrame] = {}

        def _fake_fetch(symbol: str) -> pd.DataFrame:
            if symbol not in frames:
                raise ValueError(f"ดึงข้อมูลราคา {symbol} ไม่สำเร็จ (ผลว่าง)")
            return frames[symbol]

        monkeypatch.setattr(eng, "_fetch_df", _fake_fetch)
        eng.frames = frames  # type: ignore[attr-defined]
        return eng

    def test_failed_symbol_appears_in_errors(self, engine):
        engine.frames["VOO"] = neutral_frame()
        results = engine.run(["VOO", "SCHD"], _preset([PASS_RULE]))
        assert [r.symbol for r in results] == ["VOO"]
        assert any("SCHD" in e for e in results.errors)

    def test_clean_run_has_no_errors(self, engine):
        engine.frames["VOO"] = neutral_frame()
        results = engine.run(["VOO"], _preset([PASS_RULE]))
        assert list(results.errors) == []

    def test_prefetched_frames_are_reused(self, engine):
        """ส่ง frames เข้ามา = ห้ามยิงใหม่ (เส้นทางที่ scheduler ใช้)."""

        def _boom(symbol):
            raise AssertionError(f"ต้องไม่ดึงซ้ำ: {symbol}")

        engine._fetch_df = _boom  # type: ignore[method-assign]
        results = engine.run(["VOO"], _preset([PASS_RULE]), {"VOO": neutral_frame()})
        assert [r.symbol for r in results] == ["VOO"]

    def test_symbol_missing_from_prefetched_frames_is_an_error_not_a_pass(self, engine):
        results = engine.run(["VOO", "SCHD"], _preset([PASS_RULE]), {"VOO": neutral_frame()})
        assert [r.symbol for r in results] == ["VOO"]
        assert any("SCHD" in e for e in results.errors)


# --- B6.3 -------------------------------------------------------------------


class TestSignalStrengthKnowsThePresetDirection:
    """โบนัส RSI ต้องเข้าข้างทิศของพรีเซ็ต และใช้เกณฑ์กลางชุดเดียว (30/70)."""

    @pytest.fixture
    def engine(self, monkeypatch):
        eng = ScreenerEngine()
        frames: dict[str, pd.DataFrame] = {}
        monkeypatch.setattr(
            eng, "_fetch_df", lambda symbol: frames[symbol]
        )
        eng.frames = frames  # type: ignore[attr-defined]
        return eng

    def _strength(self, engine, df, **preset_kw) -> float:
        """ความแรงของพรีเซ็ตกฎเดียวที่ "ผ่านแน่นอน" ⇒ base = 7.0 เสมอ เหลือแค่โบนัส RSI."""
        above_ma200 = df["Close"].iloc[-1] > df["Close"].rolling(200).mean().iloc[-1]
        rule = PASS_RULE if above_ma200 else FAIL_RULE
        engine.frames["X"] = df
        results = engine.run(["X"], _preset([rule], **preset_kw))
        assert results, "เฟรมทดสอบต้องผ่านกฎที่เลือกให้"
        return results[0].signal_strength

    def test_frames_land_in_the_intended_rsi_bands(self):
        """ตรึงสมมติฐานของเคสอื่นในคลาสนี้ไว้กับค่า RSI จริง."""
        assert 55 < _rsi(neutral_frame()) < 62
        assert _rsi(hot_frame()) > signal_rules.RSI_OVERBOUGHT
        assert 65 < _rsi(mildly_hot_frame()) < signal_rules.RSI_OVERBOUGHT
        assert _rsi(oversold_frame()) < signal_rules.RSI_OVERSOLD
        assert signal_rules.RSI_OVERSOLD < _rsi(mildly_weak_frame()) < 35

    def test_buy_preset_gives_no_bonus_to_an_overbought_symbol(self, engine):
        """RSI 75.6 ในพรีเซ็ตฝั่งซื้อเคยได้ +1.0 → 8.0 สูงกว่าตัวกลาง ๆ ที่ได้ 7.0."""
        hot = self._strength(engine, hot_frame(), direction="buy")
        plain = self._strength(engine, neutral_frame(), direction="buy")
        assert hot == pytest.approx(7.0)
        assert hot <= plain

    def test_buy_preset_still_rewards_a_real_oversold(self, engine):
        assert self._strength(engine, oversold_frame(), direction="buy") > 7.0

    def test_sell_preset_rewards_an_overbought_symbol(self, engine):
        assert self._strength(engine, hot_frame(), direction="sell") > 7.0

    def test_sell_preset_gives_no_bonus_to_an_oversold_symbol(self, engine):
        assert self._strength(engine, oversold_frame(), direction="sell") == pytest.approx(7.0)

    def test_thresholds_come_from_signal_rules_not_a_second_set(self, engine):
        """RSI 32.3 / 69.7 อยู่ในช่องว่างระหว่างเกณฑ์เก่า (35/65) กับนิยามกลาง (30/70)."""
        assert self._strength(engine, mildly_weak_frame(), direction="buy") == pytest.approx(7.0)
        assert self._strength(engine, mildly_hot_frame(), direction="sell") == pytest.approx(7.0)

    def test_moving_the_central_threshold_moves_the_bonus(self, engine, monkeypatch):
        """พิสูจน์ว่าอ่านจาก ``signal_rules`` จริง ไม่ใช่เลขที่พิมพ์ซ้ำในเอนจิน."""
        assert self._strength(engine, oversold_frame(), direction="buy") > 7.0
        monkeypatch.setattr(signal_rules, "RSI_OVERSOLD", 5.0)
        assert self._strength(engine, oversold_frame(), direction="buy") == pytest.approx(7.0)

    def test_shipped_presets_declare_a_direction(self):
        from backend.screener.presets import PRESETS

        directions = {name: p.direction for name, p in PRESETS.items()}
        assert directions["overbought_warning"] == "sell"
        assert directions["oversold_momentum"] == "buy"
        assert directions["golden_cross_alert"] == "buy"
        assert directions["dividend_dip"] == "buy"
        assert set(directions.values()) <= {"buy", "sell", "neutral"}

    def test_overbought_warning_still_outranks_a_plain_match(self, engine):
        """พรีเซ็ตเตือนฝั่งแพงต้องยังชูตัวที่ร้อนที่สุดขึ้นมาก่อน."""
        from backend.screener.presets import get_preset

        preset = get_preset("overbought_warning")
        engine.frames["HOT"] = hot_frame()
        engine.frames["MILD"] = mildly_hot_frame()
        hot = engine._compute_signal_strength(2, 2, hot_frame(), preset)
        mild = engine._compute_signal_strength(2, 2, mildly_hot_frame(), preset)
        assert hot > mild
