# -*- coding: utf-8 -*-
"""สัดส่วนฐานแบบ ERC (มติผู้ใช้ 2026-09-30: เลิกใช้สัดส่วนตายตัว 35/25/20/10/10).

สิ่งที่ตรึงไว้:
1. คณิตศาสตร์ — ส่วนแบ่งความเสี่ยงเท่ากันจริง และตรงกับคำตอบปิดรูปของกรณีที่รู้คำตอบ
2. กองที่ขึ้นลงพร้อมกันต้องแชร์งบความเสี่ยง (เหตุผลที่ผู้ใช้เลือกสูตรนี้)
3. ดึงราคาไม่ได้ = ``RiskWeightsUnavailable`` **ห้ามถอยไปใช้ preset เงียบ ๆ**
4. แผน DCA / API ใช้ฐาน ERC จริง และบอกสาเหตุตามชนิด (ข้อมูล ≠ คอนฟิก)

ไม่ยิง network — ราคาเป็นข้อมูลสังเคราะห์ และ ``compute_erc_weights`` ถูกสตับในชั้น targets
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

import portfolio.risk_weights as rw
from data.fetcher import PriceDataUnavailableError
from portfolio import targets
from portfolio.targets import (
    InvalidTargetWeights,
    RiskWeightsUnavailable,
    TargetWeightsError,
    get_target_weights,
    get_target_weights_with_status,
)

FIVE = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]


def _cov(vols, corr):
    vols = np.asarray(vols, dtype=float)
    return np.outer(vols, vols) * np.asarray(corr, dtype=float)


# ---------------------------------------------------------------- คณิตศาสตร์
class TestErcMath:
    def test_uncorrelated_assets_get_inverse_vol(self):
        """ไม่ขึ้นลงพร้อมกันเลย → ERC = inverse-vol เป๊ะ (10% กับ 20% → 2/3 กับ 1/3)."""
        w = rw.erc_weights(_cov([0.10, 0.20], np.eye(2)))
        assert w == pytest.approx([2 / 3, 1 / 3], abs=1e-10)

    def test_risk_contributions_are_equal(self):
        rng = np.random.default_rng(7)
        a = rng.normal(size=(6, 6))
        cov = a @ a.T / 6 + np.eye(6) * 0.01
        w = rw.erc_weights(cov)
        assert w.sum() == pytest.approx(1.0)
        assert np.all(w > 0), "ทุกกองต้องได้เงิน (นโยบายซื้อทุกกองทุกเดือน)"
        assert rw.risk_contributions(cov, w) == pytest.approx(np.full(6, 1 / 6), abs=1e-9)

    def test_funds_that_move_together_share_one_risk_budget(self):
        """เหตุผลที่ผู้ใช้เลือก ERC: สองกองที่ขึ้นลงพร้อมกัน (0.95) ต้องได้น้อยกว่ากองอิสระ.

        ความผันผวนเท่ากันหมด — ถ้าสูตรไม่นับ correlation ทั้งสามจะได้ 1/3 เท่ากัน
        """
        corr = [[1.0, 0.95, 0.0], [0.95, 1.0, 0.0], [0.0, 0.0, 1.0]]
        w = rw.erc_weights(_cov([0.15, 0.15, 0.15], corr))
        assert w[0] == pytest.approx(w[1], abs=1e-12)
        assert w[2] > w[0] * 1.3
        assert w[0] + w[1] > w[2], "คู่ที่ซ้ำกันยังได้งบรวมมากกว่า — แชร์ ไม่ใช่ถูกตัดทิ้ง"

    def test_volatile_asset_gets_less(self):
        w = rw.erc_weights(_cov([0.12, 0.30], [[1, 0.1], [0.1, 1]]))
        assert w[0] > w[1]

    @pytest.mark.parametrize(
        "bad",
        [
            np.array([[0.04, np.nan], [np.nan, 0.09]]),
            np.array([[0.0, 0.0], [0.0, 0.09]]),
            np.array([[0.04, 0.01, 0.0]]),
            np.array([[0.04, 0.02], [0.0, 0.09]]),
        ],
    )
    def test_unusable_covariance_raises(self, bad):
        with pytest.raises(ValueError):
            rw.erc_weights(bad)


# ---------------------------------------------------------------- ประมาณ covariance จากราคา
def _prices(n: int, seed: int = 1, cols=("A", "B", "C")) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-01", periods=n)
    rets = rng.normal(0.0004, 0.01, size=(n, len(cols)))
    return pd.DataFrame(100 * np.cumprod(1 + rets, axis=0), index=idx, columns=list(cols))


class TestEstimateCovariance:
    def test_windows_are_one_year_vol_and_up_to_five_years_corr(self):
        cov, meta = rw.estimate_covariance(_prices(1500))
        assert meta["vol_window"]["days"] == rw.VOL_WINDOW_BARS
        assert meta["corr_window"]["days"] == rw.CORR_WINDOW_BARS
        assert cov.shape == (3, 3)

    def test_short_history_raises_instead_of_guessing(self):
        with pytest.raises(ValueError, match="อย่างน้อย"):
            rw.estimate_covariance(_prices(200))

    def test_missing_days_are_dropped_not_filled(self):
        """วันที่บางกองไม่มีราคาต้องหายทั้งแถว — ห้าม ffill (สร้างผลตอบแทน 0% ปลอม)."""
        prices = _prices(600)
        prices.iloc[300:320, 1] = np.nan
        _, meta = rw.estimate_covariance(prices)
        # 599 ผลตอบแทน − 20 วันที่ไม่มีราคา − 1 วันถัดไปที่ไม่มีฐานเทียบ
        assert meta["corr_window"]["days"] == 599 - 21

    def test_price_based_covariance_carries_the_co_movement(self):
        """B ขึ้นลงตาม A เกือบทุกวัน C เดินเอง — ความผันผวนเท่ากันหมด.

        ถ้าการประมาณจากราคาทิ้ง correlation (เหลือแค่ความผันผวน) ทั้งสามจะได้ 1/3 เท่ากัน
        """
        rng = np.random.default_rng(3)
        n = 600
        common = rng.normal(0, 0.01, n)
        rets = np.column_stack([
            common + rng.normal(0, 0.002, n),
            common + rng.normal(0, 0.002, n),
            rng.normal(0, 0.0102, n),
        ])
        prices = pd.DataFrame(100 * np.cumprod(1 + rets, axis=0),
                              index=pd.bdate_range("2021-01-01", periods=n), columns=["A", "B", "C"])
        result = rw.erc_from_prices(prices, ["A", "B", "C"])
        assert result["weights"]["C"] > 1.3 * result["weights"]["A"]

    def test_ticker_without_prices_is_named(self):
        with pytest.raises(ValueError, match="ZZZ"):
            rw.erc_from_prices(_prices(400), ["A", "ZZZ"])


# ---------------------------------------------------------------- ชั้น targets
@pytest.fixture
def configured(tmp_path, monkeypatch):
    from utils import config as cfg

    def _write(method="erc", target_weights=None, tickers=FIVE):
        path = tmp_path / "config.json"
        path.write_text(
            json.dumps(
                {
                    "etf": {"tickers": list(tickers)},
                    "portfolio": {
                        "weighting_method": method,
                        "risk_profile": "moderate",
                        "target_weights": target_weights or {},
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(cfg, "CONFIG_PATH", path)
        monkeypatch.setattr(cfg, "_cache", None)

    return _write


def _fake_erc(calls: list):
    def _compute(tickers, sector_cap=False):
        calls.append(tuple(tickers))
        n = len(tickers)
        # น้ำหนักที่รู้คำตอบ: ตัวแรกได้ครึ่งหนึ่ง ที่เหลือแบ่งเท่ากัน
        weights = {t: (0.5 if i == 0 else 0.5 / (n - 1)) for i, t in enumerate(tickers)}
        return {"weights": weights, "risk_share": {t: 1 / n for t in tickers}, "meta": {}}

    return _compute


class TestTargetsUseErc:
    def test_erc_is_the_default_and_replaces_the_fixed_preset(self, configured, monkeypatch):
        configured()
        calls: list = []
        monkeypatch.setattr(rw, "compute_erc_weights", _fake_erc(calls))
        status = get_target_weights_with_status(FIVE)
        assert status.method == "erc"
        assert status.weights["VOO"] == pytest.approx(0.5)
        assert status.weights != targets.RISK_PROFILES["moderate"]
        assert set(status.source.values()) == {"erc"}
        assert calls == [tuple(FIVE)]

    def test_missing_method_key_means_erc(self, tmp_path, monkeypatch):
        """config.json เก่าที่ไม่มีคีย์ = ใช้ค่าเริ่มต้นของระบบ (ERC) ไม่ใช่ preset."""
        from utils import config as cfg

        path = tmp_path / "config.json"
        path.write_text(json.dumps({"etf": {"tickers": FIVE}, "portfolio": {"risk_profile": "moderate"}}))
        monkeypatch.setattr(cfg, "CONFIG_PATH", path)
        monkeypatch.setattr(cfg, "_cache", None)
        monkeypatch.setattr(rw, "compute_erc_weights", _fake_erc([]))
        assert targets.get_weighting_method() == "erc"

    def test_default_holds_even_when_config_is_built_without_defaults(self, monkeypatch):
        """ผู้เรียกที่ประกอบ config เอง (ไม่ผ่าน utils.config merge) ก็ต้องได้ ERC."""
        monkeypatch.setattr(targets, "load_config", lambda: {"portfolio": {"risk_profile": "moderate"}})
        assert targets.get_weighting_method() == "erc"

    def test_data_failure_never_falls_back_to_the_preset(self, configured, monkeypatch):
        configured()

        def _down(_tickers, *_a):
            raise PriceDataUnavailableError("yfinance rate limit")

        monkeypatch.setattr(rw, "compute_erc_weights", _down)
        with pytest.raises(RiskWeightsUnavailable, match="ERC") as exc:
            get_target_weights(FIVE)
        assert isinstance(exc.value, TargetWeightsError)
        assert not isinstance(exc.value, InvalidTargetWeights), "ข้อมูลล่ม ≠ คอนฟิกผิด"

    def test_unknown_method_is_a_config_error(self, configured):
        configured(method="magic")
        with pytest.raises(InvalidTargetWeights, match="weighting_method"):
            get_target_weights(FIVE)

    def test_preset_is_still_selectable(self, configured, monkeypatch):
        configured(method="preset")
        monkeypatch.setattr(rw, "compute_erc_weights", lambda *a: pytest.fail("preset ต้องไม่เรียก ERC"))
        weights = get_target_weights(FIVE)
        assert weights["VOO"] == pytest.approx(0.35)

    def test_leftover_custom_weights_are_reported_not_silently_used(self, configured, monkeypatch):
        configured(target_weights={"VOO": 0.5})
        monkeypatch.setattr(rw, "compute_erc_weights", _fake_erc([]))
        status = get_target_weights_with_status(FIVE)
        assert any("target_weights" in n for n in status.notes)

    def test_partial_round_computes_on_the_usable_funds_and_says_so(self, configured, monkeypatch):
        configured()
        calls: list = []
        monkeypatch.setattr(rw, "compute_erc_weights", _fake_erc(calls))
        subset = ["VOO", "SCHD", "QQQM", "XLV"]
        status = get_target_weights_with_status(subset, partial=True)
        assert calls == [tuple(subset)]
        assert sum(status.weights.values()) == pytest.approx(1.0)
        assert any("GLDM" in n for n in status.notes)


class TestDcaPlanUsesErc:
    def test_allocation_base_is_erc_times_tilt(self, configured, monkeypatch):
        from analysis.financial_model import _score_tilt, calculate_allocation_with_status

        configured()
        monkeypatch.setattr(rw, "compute_erc_weights", _fake_erc([]))
        scores = {t: {"data_ok": True, "total_pct": 50.0} for t in FIVE}  # tilt 1.0 ทุกตัว
        assert _score_tilt(50.0) == pytest.approx(1.0)
        plan = calculate_allocation_with_status(scores, 5000)
        assert plan.allocation["VOO"]["amount_thb"] == 2500
        assert {plan.allocation[t]["amount_thb"] for t in FIVE[1:]} == {600, 700}
        assert plan.allocation["VOO"]["target_percent"] == pytest.approx(50.0)

    def test_erc_failure_reaches_the_dca_plan_loudly(self, configured, monkeypatch):
        from analysis.financial_model import calculate_allocation_with_status

        configured()
        monkeypatch.setattr(
            rw, "compute_erc_weights", lambda *a: (_ for _ in ()).throw(ValueError("ประวัติไม่พอ"))
        )
        with pytest.raises(RiskWeightsUnavailable):
            calculate_allocation_with_status({t: {"data_ok": True, "total_pct": 50.0} for t in FIVE}, 5000)


class TestErrorsAreRoutedByKind:
    def test_api_full_analysis_answers_503_not_422(self, monkeypatch):
        from fastapi import HTTPException

        from backend.routers import analysis as router

        def _raise(_budget):
            raise RiskWeightsUnavailable("ดึงราคาไม่สำเร็จ")

        monkeypatch.setattr(router.service, "full_analysis", _raise)
        with pytest.raises(HTTPException) as exc:
            router.get_full_financial_analysis(budget_thb=5000)
        assert exc.value.status_code == 503

    def test_rebalance_api_answers_503(self, monkeypatch):
        from fastapi import HTTPException

        from backend.routers import rebalance as router
        from backend.schemas import RebalanceRequest

        def _raise(**_kw):
            raise RiskWeightsUnavailable("ดึงราคาไม่สำเร็จ")

        monkeypatch.setattr(router.rebalance_service, "compute_rebalance", _raise)
        payload = RebalanceRequest(holdings=[], risk_profile="moderate", available_budget_thb=0)
        with pytest.raises(HTTPException) as exc:
            router.rebalance_portfolio(payload, include_ai=False)
        assert exc.value.status_code == 503


# ---------------------------------------------------------------- หน้า Settings + ตาข่ายออฟไลน์
class _Recorder:
    """แทน ``streamlit`` — จดทุกอย่างที่หน้าจอพูด."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        def _call(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return _call

    def said(self, *kinds: str) -> str:
        return "\n".join(str(a[0]) for n, a, _k in self.calls if n in kinds and a)


@pytest.fixture
def screen(monkeypatch):
    app = pytest.importorskip("dashboard.app")
    rec = _Recorder()
    monkeypatch.setattr(app, "st", rec)
    return app, rec


class TestSettingsScreen:
    def test_erc_table_shows_weight_volatility_risk_share_and_window(self, screen, monkeypatch):
        app, rec = screen
        result = {
            "weights": dict(zip(FIVE, [0.21, 0.27, 0.15, 0.21, 0.16])),
            "risk_share": {t: 0.2 for t in FIVE},
            "risk_per_pct": dict(zip(FIVE, [11.3, 9.0, 15.7, 11.9, 15.1])),
            "meta": {
                "vol_pct": dict(zip(FIVE, [12.9, 11.2, 19.8, 16.0, 29.4])),
                "vol_window": {"start": "2025-09-30", "end": "2026-09-29", "days": 252},
                "corr_window": {"start": "2021-10-01", "end": "2026-09-29", "days": 1260},
            },
        }
        monkeypatch.setattr(rw, "compute_erc_weights", lambda *a: result)
        app._render_erc_weights_table(FIVE)

        frames = [a[0] for n, a, _k in rec.calls if n == "dataframe"]
        assert frames and len(frames[0]) == 5
        row = frames[0].set_index("ETF").loc["SCHD"]
        assert row["สัดส่วนฐาน"] == "27.0%"
        assert row["ความเสี่ยงที่เพิ่มต่อเงิน 1%"] == "9.0"
        assert "ส่วนแบ่งความเสี่ยง" not in frames[0].columns, (
            "ERC ไม่มีเพดานชน = ส่วนแบ่งเท่ากันหมดโดยนิยาม คอลัมน์นั้นไม่บอกอะไร (ผู้ใช้ถาม 2026-09-30)"
        )
        caption = rec.said("caption")
        assert "2021-10-01" in caption and "2026-09-29" in caption, "ต้องบอกช่วงข้อมูลที่ใช้จริง"

    def test_data_failure_is_a_data_message_not_a_config_message(self, screen, monkeypatch):
        app, rec = screen

        def _down(*_a):
            raise PriceDataUnavailableError("rate limit")

        monkeypatch.setattr(rw, "compute_erc_weights", _down)
        app._render_erc_weights_table(FIVE)
        assert "ERC" in rec.said("error")
        assert "ไม่ต้องแก้ config.json" in rec.said("info")
        assert not [c for c in rec.calls if c[0] == "dataframe"], "ห้ามโชว์ตารางสัดส่วนตอนคำนวณไม่ได้"

    def test_problem_router_does_not_send_erc_failures_to_config_advice(self, screen):
        app, rec = screen
        app._render_target_weights_problem(RiskWeightsUnavailable("ดึงราคาไม่สำเร็จ"))
        assert "ไม่ต้องแก้ config.json" in rec.said("info")
        assert "portfolio.target_weights" not in rec.said("error", "info")


class TestSuiteStaysOffline:
    def test_live_erc_fetch_is_blocked_inside_the_suite(self):
        """ตาข่ายใน conftest: เทสต์ที่ลืมสตับต้องล้มดัง ๆ ไม่ใช่ไปดึงราคาจริงแล้ว 'ผ่าน'."""
        with pytest.raises(AssertionError, match="ERC"):
            rw.compute_erc_weights(("VOO", "GLDM"))
