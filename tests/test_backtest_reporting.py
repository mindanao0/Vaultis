# -*- coding: utf-8 -*-
"""B3 — ``/api/backtest`` ต้องไม่กุตัวเลขและไม่ทิ้งบริบทเงียบ (AUDIT_2026-08-06 B3.1–B3.5).

ทุกเคสในไฟล์นี้ **ไม่แตะเน็ตและไม่เรียก LLM**: ``yf.download`` ถูก stub ด้วยซีรีส์
สังเคราะห์ และ endpoint ถูกเรียกผ่าน ``TestClient`` เสมอ (เรียก router function ตรง ๆ
จะได้ ``include_ai`` เป็นอ็อบเจกต์ ``Query(False)`` ซึ่ง truthy → เผาเงินจริง)

สิ่งที่ตรึงไว้
- B3.1 ``num_trades == 0`` = "ไม่นิยาม" ไม่ใช่ 0.0 → ห้ามสรุปว่าชนะดัชนี
- B3.2 ``_sharpe_for`` แยก "ไม่มีเทรด/NaN" (``None``) ออกจาก "Sharpe = 0.0" จริง
       และห้ามกลืน exception
- B3.3 ``train_sharpe`` ติดลบต้องรายงานติดลบ ห้ามปัดพื้นเป็น 0.0
- B3.4 ``strategy_used`` + ผลการ optimize ต้องไปถึงผู้ใช้
- B3.5 ดึงราคาไม่สำเร็จ = ``PriceDataUnavailableError`` → 503 ไม่ใช่ 400
"""

from __future__ import annotations

import math

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import analysis.backtest_engine as backtest_engine_module
import analysis.backtest_summary as backtest_summary
import backend.routers.backtest as backtest_router
from analysis.backtest_engine import BacktestEngine
from analysis.ta_compat import ta
from backend.main import app
from data.fetcher import PriceDataUnavailableError


# ---------------------------------------------------------------- ซีรีส์สังเคราะห์


def flat_rsi_frame(n: int = 260, start: float = 100.0) -> pd.DataFrame:
    """ราคาที่ RSI ค้างราว 31–39 (ไม่เคยหลุด 30) และเทรนด์ลง.

    ⇒ กลยุทธ์ไม่มีวันเข้าเทรด แต่ buy & hold ติดลบหนัก — เป็นรูปทรงเดียวกับ
    หน้าต่างจริง 11 หน้าต่างที่ผลตรวจพบว่า ``outperformed=True`` ทั้งที่ trades=0
    """
    pattern = [+1.0, -1.0, -1.0]
    prices = [start]
    for i in range(n - 1):
        prices.append(prices[-1] + pattern[i % 3])
    idx = pd.bdate_range("2020-01-01", periods=n)
    return pd.DataFrame(
        {"Open": prices, "High": prices, "Low": prices, "Close": prices, "Volume": [1000] * n},
        index=idx,
    )


@pytest.fixture(scope="module")
def no_trade_frame() -> pd.DataFrame:
    df = flat_rsi_frame()
    rsi = ta.rsi(df["Close"], length=14).dropna()
    assert rsi.min() > 30.0, f"ซีรีส์ทดสอบต้องไม่เคย oversold (min={rsi.min()})"
    assert rsi.max() < 70.0, f"ซีรีส์ทดสอบต้องไม่เคย overbought (max={rsi.max()})"
    return df


class _FakeTrades:
    def __init__(self, count: float, win_rate: float):
        self._count = count
        self._win_rate = win_rate

    def count(self):
        return self._count

    def win_rate(self):
        return self._win_rate


class _FakePortfolio:
    """แทน vbt.Portfolio เพื่อตรึงเคส 'มีเทรดแต่ Sharpe เป็น NaN' ให้ทำซ้ำได้"""

    def __init__(self, n_trades: int, sharpe: float, total_ret: float, mdd: float, wr: float):
        self.trades = _FakeTrades(n_trades, wr)
        self._sharpe = sharpe
        self._total = total_ret
        self._mdd = mdd

    def total_return(self):
        return self._total

    def sharpe_ratio(self):
        return self._sharpe

    def max_drawdown(self):
        return self._mdd


# ---------------------------------------------------------------- B3.1


class TestZeroTradeRunIsNotZero:
    def test_no_trade_window_does_not_report_beating_the_benchmark(self, monkeypatch, no_trade_frame):
        engine = BacktestEngine()
        monkeypatch.setattr(engine, "fetch_data", lambda *a, **k: no_trade_frame)

        res = engine.run("FAKE", "2020-01-01", "2021-01-01")

        assert res["num_trades"] == 0
        assert res["benchmark_return"] < 0, "ซีรีส์ทดสอบต้องเป็นขาลง"
        assert res["outperformed"] is None, (
            f"กลยุทธ์ที่ไม่เคยเทรดถูกสรุปว่า outperformed={res['outperformed']!r}"
        )

    def test_undefined_metrics_stay_none_not_zero(self, monkeypatch, no_trade_frame):
        engine = BacktestEngine()
        monkeypatch.setattr(engine, "fetch_data", lambda *a, **k: no_trade_frame)

        res = engine.run("FAKE", "2020-01-01", "2021-01-01")

        for key in ("total_return", "sharpe_ratio", "max_drawdown", "win_rate"):
            assert res[key] is None, f"{key} ควรเป็น None (ไม่นิยาม) แต่ได้ {res[key]!r}"

    def test_zero_trade_result_explains_itself_in_thai(self, monkeypatch, no_trade_frame):
        engine = BacktestEngine()
        monkeypatch.setattr(engine, "fetch_data", lambda *a, **k: no_trade_frame)

        detail = engine.run("FAKE", "2020-01-01", "2021-01-01").get("detail")

        assert detail, "ต้องบอกผู้ใช้ว่าทำไมทุกช่องว่าง"
        assert "0 เทรด" in detail and "Buy & Hold" in detail, detail

    def test_nan_sharpe_with_trades_is_none_not_zero(self, monkeypatch, no_trade_frame):
        """มีเทรดจริงแต่ Sharpe คำนวณไม่ได้ ≠ Sharpe = 0.0"""
        fake = _FakePortfolio(n_trades=4, sharpe=float("nan"), total_ret=0.1, mdd=-0.05, wr=float("nan"))
        monkeypatch.setattr(
            backtest_engine_module.vbt.Portfolio, "from_signals", staticmethod(lambda *a, **k: fake)
        )
        engine = BacktestEngine()
        monkeypatch.setattr(engine, "fetch_data", lambda *a, **k: no_trade_frame)

        res = engine.run("FAKE", "2020-01-01", "2021-01-01")

        assert res["num_trades"] == 4
        assert res["sharpe_ratio"] is None, res["sharpe_ratio"]
        assert res["win_rate"] is None, res["win_rate"]
        assert res["total_return"] == pytest.approx(10.0)


# ---------------------------------------------------------------- B3.2


class TestSharpeForSeparatesMeanings:
    def test_no_trade_gives_none_not_zero(self, no_trade_frame):
        engine = BacktestEngine()
        assert engine._sharpe_for(no_trade_frame, 14, 30.0) is None

    def test_too_short_series_gives_none_not_zero(self):
        engine = BacktestEngine()
        assert engine._sharpe_for(pd.DataFrame({"Close": [100.0, 101.0, 99.0]}), 14, 30.0) is None

    def test_real_error_is_not_swallowed(self, monkeypatch, no_trade_frame):
        def _boom(*_a, **_k):
            raise ZeroDivisionError("บั๊กจริงในการคำนวณสัญญาณ")

        engine = BacktestEngine()
        monkeypatch.setattr(engine, "rsi_macd_strategy", _boom)

        with pytest.raises(ZeroDivisionError):
            engine._sharpe_for(no_trade_frame, 14, 30.0)


class TestOptimizeRanksOnlyRealNumbers:
    def test_zero_trade_combo_never_beats_a_real_negative_sharpe(self, monkeypatch, no_trade_frame):
        """คอมโบที่ไม่เทรดเลยต้องไม่ถูกจัดอันดับเหนือคอมโบที่เทรดจริงแล้วขาดทุน"""
        engine = BacktestEngine()
        monkeypatch.setattr(engine, "fetch_data", lambda *a, **k: no_trade_frame)

        def _fake_sharpe(_df, rsi_period, rsi_oversold):
            if (rsi_period, rsi_oversold) == (21, 35):
                return -0.42  # เทรดจริง ขาดทุนน้อยที่สุด
            return None  # ไม่เทรดเลย

        monkeypatch.setattr(engine, "_sharpe_for", _fake_sharpe)
        opt = engine.optimize("FAKE", "2020-01-01", "2021-01-01")

        assert opt["best_params"] == {"rsi_period": 21, "rsi_oversold": 35}, opt["best_params"]
        assert opt["train_sharpe"] == pytest.approx(-0.42)

    def test_no_combo_trades_at_all_is_reported_not_zeroed(self, monkeypatch, no_trade_frame):
        """ด่าน 'ไม่พบพารามิเตอร์ที่ให้สัญญาณเลย' ต้องเป็นโค้ดที่วิ่งได้จริง"""
        engine = BacktestEngine()
        monkeypatch.setattr(engine, "fetch_data", lambda *a, **k: no_trade_frame)
        monkeypatch.setattr(engine, "_sharpe_for", lambda *a, **k: None)

        opt = engine.optimize("FAKE", "2020-01-01", "2021-01-01")

        assert opt["best_params"] == {}
        assert opt["train_sharpe"] is None, opt["train_sharpe"]
        assert opt["test_sharpe"] is None, opt["test_sharpe"]
        assert "ไม่พบพารามิเตอร์" in opt["note"]
        assert all(r["train_sharpe"] is None for r in opt["all_results"])


# ---------------------------------------------------------------- B3.3


class TestTrainSharpeIsNotFloored:
    def test_all_negative_combos_report_the_real_negative_number(self, monkeypatch, no_trade_frame):
        engine = BacktestEngine()
        monkeypatch.setattr(engine, "fetch_data", lambda *a, **k: no_trade_frame)
        monkeypatch.setattr(
            engine, "_sharpe_for", lambda _df, rsi_period, rsi_oversold: -1.0 - rsi_period / 100.0
        )

        opt = engine.optimize("FAKE", "2020-01-01", "2021-01-01")

        assert opt["train_sharpe"] == pytest.approx(-1.07), (
            f"train_sharpe ติดลบทุกคอมโบแต่รายงาน {opt['train_sharpe']!r}"
        )
        assert opt["train_sharpe"] < 0

    def test_out_of_sample_no_trade_is_none_and_said_out_loud(self, monkeypatch, no_trade_frame):
        engine = BacktestEngine()
        monkeypatch.setattr(engine, "fetch_data", lambda *a, **k: no_trade_frame)

        calls: list[int] = []

        def _fake_sharpe(df, rsi_period, rsi_oversold):
            calls.append(len(df))
            return -1.1 if len(calls) <= 12 else None  # 12 คอมโบแรก = train, ครั้งถัดไป = test

        monkeypatch.setattr(engine, "_sharpe_for", _fake_sharpe)
        opt = engine.optimize("FAKE", "2020-01-01", "2021-01-01")

        assert opt["test_sharpe"] is None, opt["test_sharpe"]
        assert "ไม่ส่งสัญญาณ" in opt["note"], opt["note"]


# ---------------------------------------------------------------- B3.4


_NO_TRADE_RESULT = {
    "symbol": "VOO",
    "start": "2022-03-09",
    "end": "2022-09-14",
    "strategy_used": "rsi_only_fallback",
    "total_return": None,
    "sharpe_ratio": None,
    "max_drawdown": None,
    "win_rate": None,
    "num_trades": 0,
    "benchmark_return": -7.3344,
    "outperformed": None,
    "detail": "กลยุทธ์ไม่ส่งสัญญาณเข้าซื้อเลยในช่วงนี้ (0 เทรด)",
}
_PAYLOAD = {"symbol": "VOO", "start": "2022-03-09", "end": "2022-09-14"}


class TestResponseKeepsContext:
    @pytest.fixture(autouse=True)
    def _no_api_key(self, monkeypatch):
        monkeypatch.delenv("VAULTIS_API_KEY", raising=False)

    @staticmethod
    def _client() -> TestClient:
        return TestClient(app)

    def test_strategy_used_reaches_the_user(self, monkeypatch):
        monkeypatch.setattr(
            backtest_router._engine, "run", lambda *a, **k: dict(_NO_TRADE_RESULT)
        )
        body = self._client().post("/api/backtest", json=_PAYLOAD).json()

        assert body.get("strategy_used") == "rsi_only_fallback", (
            f"กลยุทธ์ที่ใช้จริงถูกทิ้งก่อนถึงผู้ใช้: {sorted(body)}"
        )

    def test_zero_trade_response_is_null_not_zero(self, monkeypatch):
        monkeypatch.setattr(
            backtest_router._engine, "run", lambda *a, **k: dict(_NO_TRADE_RESULT)
        )
        res = self._client().post("/api/backtest", json=_PAYLOAD)

        assert res.status_code == 200, res.text
        body = res.json()
        assert body["outperformed"] is None, body["outperformed"]
        assert body["total_return"] is None
        assert body["win_rate"] is None
        assert body["detail"]

    def test_optimization_block_reaches_the_user(self, monkeypatch):
        opt = {
            "best_params": {"rsi_period": 10, "rsi_oversold": 25},
            "train_period": "2021-01-04 – 2022-06-30",
            "test_period": "2022-07-01 – 2023-06-01",
            "train_sharpe": -1.0947,
            "test_sharpe": None,
            "all_results": [{"rsi_period": 10, "rsi_oversold": 25, "train_sharpe": -1.0947}],
            "note": "จูนแล้วแพ้ทุกชุด",
        }
        monkeypatch.setattr(backtest_router._engine, "optimize", lambda *a, **k: opt)
        monkeypatch.setattr(
            backtest_router._engine, "run", lambda *a, **k: dict(_NO_TRADE_RESULT)
        )
        body = self._client().post(
            "/api/backtest", json={**_PAYLOAD, "run_optimization": True}
        ).json()

        assert body.get("optimization"), f"ผลการ optimize หายก่อนถึงผู้ใช้: {sorted(body)}"
        assert body["optimization"]["train_sharpe"] == pytest.approx(-1.0947)
        assert body["optimization"]["test_sharpe"] is None
        assert body["optimization"]["note"] == "จูนแล้วแพ้ทุกชุด"
        assert body["best_params"] == opt["best_params"]


class TestSummaryPromptDoesNotInventNumbers:
    def test_missing_metrics_are_not_formatted_as_zero(self, monkeypatch):
        captured: dict[str, str] = {}

        def _fake_chat(system, user, **kwargs):
            captured["user"] = user
            return "ok"

        monkeypatch.setattr(backtest_summary, "chat_text", _fake_chat)
        backtest_summary.generate_summary(dict(_NO_TRADE_RESULT), "VOO", user_initiated=True)

        prompt = captured["user"]
        assert "0.00%" not in prompt, f"ค่าที่ไม่นิยามถูกเล่าเป็น 0.00%:\n{prompt}"
        assert "ไม่มีข้อมูล" in prompt
        assert "ชนะ Benchmark: ไม่ใช่" not in prompt, prompt


# ---------------------------------------------------------------- B3.5


class TestPriceFailureIsUpstreamNotCallerError:
    def test_empty_download_raises_price_unavailable_after_three_tries(self, monkeypatch):
        calls: list[tuple] = []

        def _empty(*args, **kwargs):
            calls.append((args, kwargs))
            return pd.DataFrame()

        monkeypatch.setattr(backtest_engine_module, "_RETRY_SLEEP_SEC", 0)
        monkeypatch.setattr(backtest_engine_module.yf, "download", _empty)

        with pytest.raises(PriceDataUnavailableError):
            BacktestEngine().fetch_data("VOO", "2020-01-01", "2021-01-01")

        assert len(calls) == 3, f"ต้อง retry 3 ครั้งเหมือน data/fetcher แต่ยิงไป {len(calls)} ครั้ง"

    def test_api_reports_503_not_400_when_prices_are_unavailable(self, monkeypatch):
        monkeypatch.delenv("VAULTIS_API_KEY", raising=False)
        monkeypatch.setattr(backtest_engine_module, "_RETRY_SLEEP_SEC", 0)
        monkeypatch.setattr(
            backtest_engine_module.yf, "download", lambda *a, **k: pd.DataFrame()
        )

        res = TestClient(app).post("/api/backtest", json=_PAYLOAD)

        assert res.status_code == 503, res.text
        assert "ดึงราคา" in res.json()["detail"]


def test_no_nan_leaks_into_the_payload(monkeypatch, no_trade_frame):
    """กันการกลับไปใช้ NaN แทน None (NaN ทำให้ JSON ไม่ถูกต้องและ truthy)"""
    engine = BacktestEngine()
    monkeypatch.setattr(engine, "fetch_data", lambda *a, **k: no_trade_frame)
    res = engine.run("FAKE", "2020-01-01", "2021-01-01")
    for key, value in res.items():
        if isinstance(value, float):
            assert not math.isnan(value), f"{key} เป็น NaN"
