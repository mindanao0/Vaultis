# -*- coding: utf-8 -*-
"""วิธี ``blend`` (ERC ครึ่ง + 1/N ครึ่ง) — ค่าเริ่มต้นตั้งแต่ 2026-10-05 (มติผู้ใช้).

ล็อก 4 เรื่อง: (1) คณิตของการผสมตรงกับที่ simulation ทดสอบ (research/dar_sim: ``0.5·ERC + 0.5/N``)
(2) ส่วนแบ่งความเสี่ยงของน้ำหนักที่ผสมแล้วคำนวณใหม่จาก covariance (ERC ล้วนเท่ากันหมดโดยนิยาม blend ไม่เท่า)
(3) ดึงราคาไม่ได้ = ล้มดัง ไม่ถอยไป 1/N หรือ preset เงียบ ๆ (4) ข้อความ Discord บอกวิธีที่ใช้จริง
"""
from __future__ import annotations

import json

import numpy as np
import pytest

import portfolio.risk_weights as rw
from data.fetcher import PriceDataUnavailableError
from portfolio import targets
from portfolio.targets import RiskWeightsUnavailable, get_target_weights_with_status

FIVE = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]
ERC_LIKE = {"VOO": 0.22, "SCHD": 0.24, "QQQM": 0.17, "XLV": 0.20, "GLDM": 0.17}


def _cov():
    vol = np.array([0.15, 0.14, 0.19, 0.14, 0.15])
    corr = np.full((5, 5), 0.8)
    corr[4, :4] = corr[:4, 4] = 0.05
    np.fill_diagonal(corr, 1.0)
    return corr * np.outer(vol, vol)


def _erc_result():
    cov = _cov()
    w = rw.erc_weights(cov)
    return {
        "cov": cov.tolist(),
        "weights": dict(zip(FIVE, map(float, w))),
        "risk_share": dict(zip(FIVE, map(float, rw.risk_contributions(cov, w)))),
        "risk_per_pct": {},
        "meta": {"currency": "THB", "vol_pct": {t: 15.0 for t in FIVE}},
    }


def test_share_constant_is_one_half_like_the_simulation():
    assert rw.BLEND_ERC_SHARE == 0.5, "simulation ทดสอบที่ 0.5 เท่านั้น — เปลี่ยน = ต้องจำลองใหม่"


def test_blend_math_matches_what_the_simulation_tested():
    n = len(ERC_LIKE)
    blended = rw.blend_with_equal(ERC_LIKE)
    for t, w in ERC_LIKE.items():
        assert blended[t] == pytest.approx(0.5 * w + 0.5 / n)  # สูตรเดียวกับ arm BLEND ใน research/dar_sim/sim.py
    assert sum(blended.values()) == pytest.approx(1.0)
    assert all(v > 0 for v in blended.values())
    lo, hi = min(ERC_LIKE.values()), 1.0 / n
    assert all(min(ERC_LIKE[t], 1 / n) - 1e-12 <= blended[t] <= max(ERC_LIKE[t], 1 / n) + 1e-12 for t in blended)
    assert lo < hi + 1  # sanity ของข้อมูลทดสอบ


def test_blend_share_bounds_are_enforced():
    with pytest.raises(ValueError):
        rw.blend_with_equal(ERC_LIKE, 1.5)
    with pytest.raises(ValueError):
        rw.blend_with_equal({}, 0.5)
    assert rw.blend_with_equal(ERC_LIKE, 1.0) == pytest.approx(ERC_LIKE)
    assert all(v == pytest.approx(0.2) for v in rw.blend_with_equal(ERC_LIKE, 0.0).values())


def test_risk_shares_are_recomputed_for_the_blended_weights():
    erc = _erc_result()
    assert np.allclose(list(erc["risk_share"].values()), 0.2, atol=1e-6)  # ERC ล้วน = เท่ากันโดยนิยาม
    out = rw.blend_result(erc)
    shares = np.array([out["risk_share"][t] for t in FIVE])
    assert shares.sum() == pytest.approx(1.0)
    assert not np.allclose(shares, 0.2, atol=1e-3), "blend ไม่ใช่ ERC ส่วนแบ่งความเสี่ยงต้องไม่เท่ากันแล้ว"
    w = np.array([out["weights"][t] for t in FIVE])
    assert np.allclose(shares, rw.risk_contributions(_cov(), w))
    assert out["erc_weights"] == erc["weights"] and out["erc_share"] == 0.5


def test_blend_without_covariance_does_not_invent_risk_shares():
    out = rw.blend_result({"weights": dict(ERC_LIKE), "risk_share": {}, "meta": {}})
    assert out["risk_share"] == {} and sum(out["weights"].values()) == pytest.approx(1.0)


# ---------------------------------------------------------------- targets
@pytest.fixture
def blend_config(tmp_path, monkeypatch):
    from utils import config as cfg

    def _write(method="blend"):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"etf": {"tickers": FIVE}, "portfolio": {"weighting_method": method}}), encoding="utf-8")
        monkeypatch.setattr(cfg, "CONFIG_PATH", path)
        monkeypatch.setattr(cfg, "_cache", None)

    return _write


def test_blend_is_the_system_default():
    assert targets.DEFAULT_WEIGHTING == "blend"
    assert targets.WEIGHTING_BLEND in targets.WEIGHTING_METHODS
    shipped = json.loads((__import__("pathlib").Path(__file__).resolve().parents[1] / "config.json").read_text(encoding="utf-8"))
    assert shipped["portfolio"]["weighting_method"] == "blend", "config.json ที่ส่งมากับ repo ต้องตรงกับค่าเริ่มต้นในโค้ด"


def test_targets_blend_returns_blended_weights_and_says_so(blend_config, monkeypatch):
    blend_config("blend")
    monkeypatch.setattr(rw, "compute_erc_weights", lambda tickers, sector_cap=False: _erc_result())
    status = get_target_weights_with_status(FIVE)
    erc = _erc_result()["weights"]
    assert status.method == "blend" and set(status.source.values()) == {"blend"}
    for t in FIVE:
        assert status.weights[t] == pytest.approx(0.5 * erc[t] + 0.1)
    assert sum(status.weights.values()) == pytest.approx(1.0)
    assert status.details["erc_weights"] == erc and status.details["erc_share"] == 0.5
    assert not np.allclose(list(status.details["risk_share"].values()), 0.2, atol=1e-3)


def test_erc_is_still_selectable_and_unblended(blend_config, monkeypatch):
    blend_config("erc")
    monkeypatch.setattr(rw, "compute_erc_weights", lambda tickers, sector_cap=False: _erc_result())
    status = get_target_weights_with_status(FIVE)
    assert status.method == "erc"
    assert status.weights == pytest.approx(_erc_result()["weights"])


def test_data_failure_never_falls_back_to_equal_weights_or_preset(blend_config, monkeypatch):
    blend_config("blend")

    def _down(_tickers, *_a):
        raise PriceDataUnavailableError("yfinance rate limit")

    monkeypatch.setattr(rw, "compute_erc_weights", _down)
    with pytest.raises(RiskWeightsUnavailable, match="blend"):
        get_target_weights_with_status(FIVE)


def test_partial_round_blends_over_the_tickers_actually_bought(blend_config, monkeypatch):
    blend_config("blend")
    four = FIVE[:4]
    seen = []

    def _erc(tickers, sector_cap=False):
        seen.append(tuple(tickers))
        n = len(tickers)
        return {"weights": {t: 1.0 / n for t in tickers}, "risk_share": {}, "meta": {}}

    monkeypatch.setattr(rw, "compute_erc_weights", _erc)
    status = get_target_weights_with_status(four, partial=True)
    assert seen == [tuple(four)] and sum(status.weights.values()) == pytest.approx(1.0)
    assert any("blend" in n for n in status.notes), "ต้องบอกว่ารอบนี้คิดจากกองที่มีข้อมูลเท่านั้น"


# ---------------------------------------------------------------- Discord
def test_discord_context_line_names_the_blend_method(monkeypatch):
    from analysis import ai_advisor
    from portfolio import lookthrough

    status = targets.TargetWeights(weights=dict(ERC_LIKE), profile="moderate", method="blend")
    monkeypatch.setattr(targets, "get_target_weights_with_status", lambda *a, **k: status)
    monkeypatch.setattr(lookthrough, "_fund_data", lambda s: (None, None, "ออฟไลน์"))
    text = "\n".join(ai_advisor._base_context_lines([]))
    assert "blend" in text and "แบ่งเท่ากัน" in text and "ERC" in text
    assert "ℹ️ ฐานแบบ blend" in ai_advisor._BASE_METHOD_LINE["blend"]
