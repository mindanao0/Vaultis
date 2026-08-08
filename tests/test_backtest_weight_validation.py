# -*- coding: utf-8 -*-
"""G8 — น้ำหนักพอร์ตที่ไม่ใช่จำนวนจริงต้องตายตั้งแต่ด่านแรก ห้ามกลายเป็นเส้นแบน 0%.

อาการก่อนแก้ (AUDIT_ROUND2_2026-08-07 · รันจริงด้วย ``POST /api/analysis/backtest``)::

    A) backtest._normalize_weights({'VOO': inf})            -> {'VOO': nan}
    B) backtest._normalize_weights({'VOO': inf,'SCHD':1.0}) -> {'VOO': nan, 'SCHD': 0.0}
    C) dca._normalize_weights({'VOO': 0.5,'SCHD': nan})     -> {'VOO': 1.0, 'SCHD': nan}
    E) run_portfolio_backtest(weights={'VOO': inf}) rows=200 distinct=1 last=10000.0
       (ราคาสังเคราะห์ VOO ไต่ 100→200 คำตอบที่ถูกควรราว 20000)
    F) simulate_monthly_dca(weights={'VOO': inf}) -> Portfolio Value 0.0 / Profit-Loss -100%
    G) POST /api/analysis/backtest {"weights": {"VOO": Infinity}} -> 200 (เส้นแบนราบ)
    H) POST /api/dca/simulate      {"weights": {"VOO": Infinity}} -> 200 (ขาดทุน 100%)

ทางที่ ``inf`` เดินผ่าน: ``inf > 0`` เป็น True (ต่างจาก ``NaN > 0``) → ``weight_sum = inf``
ไม่ ≤ 0 → ``inf / inf`` = ``NaN`` → ``.sum(axis=1)`` ของ pandas ``skipna=True`` ยุบแถวที่
เป็น NaN ล้วนให้เป็น **0.0** ⇒ ผลตอบแทนรายวัน 0 ทุกวัน = กุตัวเลขบนเส้นทางเงิน
(รูปแบบเดียวกับ ``fillna(0)`` ที่ CLAUDE.md ห้าม เพียงแต่ซ่อนอยู่ใน default ของ pandas)

หลังแก้: ปฏิเสธที่ชั้น schema (422 พร้อมข้อความไทย) และที่ ``_normalize_weights``
ของทั้งสองไฟล์ (ValueError ที่ **บอกชื่อกองที่ผิด**) — แดชบอร์ดเรียกโมดูลตรง ๆ
ไม่ผ่าน API จึงต้องมีด่านทั้งสองชั้น
"""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from portfolio import backtest as backtest_mod
from portfolio import dca as dca_mod
from portfolio.dca import COVERAGE_ATTR, NO_PRICE_KEY, ZERO_WEIGHT_KEY, describe_coverage

INF = float("inf")
NAN = float("nan")

_NORMALIZERS = [
    pytest.param(backtest_mod._normalize_weights, id="backtest"),
    pytest.param(dca_mod._normalize_weights, id="dca"),
]


def _rising_prices() -> pd.DataFrame:
    """VOO ไต่จาก 100 → 200 (+100%) — ผลลัพธ์ที่ถูกจึงต้อง "ไม่แบน" เสมอ."""
    idx = pd.date_range("2020-01-01", periods=200, freq="B")
    return pd.DataFrame({"VOO": np.linspace(100.0, 200.0, len(idx))}, index=idx)


def _two_fund_prices() -> pd.DataFrame:
    """VOO ขึ้น 100% / SCHD ลง 50% — ถ้า SCHD หลุดเข้าไปในพอร์ต ตัวเลขจะต่างกันคนละโลก."""
    idx = pd.date_range("2020-01-01", periods=200, freq="B")
    return pd.DataFrame(
        {
            "VOO": np.linspace(100.0, 200.0, len(idx)),
            "SCHD": np.linspace(100.0, 50.0, len(idx)),
        },
        index=idx,
    )


# ---------------------------------------------------------------------------
# ชั้นโมดูล — _normalize_weights ทั้งสองไฟล์ต้องใช้กฎเดียวกัน
# ---------------------------------------------------------------------------


class TestNormalizeWeightsRejectsNonFinite:
    @pytest.mark.parametrize("normalize", _NORMALIZERS)
    @pytest.mark.parametrize(
        "weights",
        [
            {"VOO": INF},
            {"VOO": INF, "SCHD": 1.0},
            {"VOO": -INF, "SCHD": 1.0},
            {"VOO": NAN, "SCHD": 1.0},
        ],
        ids=["inf_only", "inf_with_good", "neg_inf", "nan_with_good"],
    )
    def test_non_finite_weight_raises_and_names_the_ticker(self, normalize, weights):
        with pytest.raises(ValueError) as excinfo:
            normalize(dict(weights))

        message = str(excinfo.value)
        assert "VOO" in message, (
            "ต้องบอกว่ากองไหนน้ำหนักผิด ไม่ใช่ข้อความรวม ๆ ที่ผู้ใช้แก้ตามไม่ได้ "
            f"(ได้: {message})"
        )

    @pytest.mark.parametrize("normalize", _NORMALIZERS)
    def test_negative_weight_raises_and_names_the_ticker(self, normalize):
        """ติดลบ = อินพุตผิด (แพลตฟอร์มนี้ long-only) ห้ามตัดทิ้งเงียบแล้ว normalize ต่อ."""
        with pytest.raises(ValueError) as excinfo:
            normalize({"VOO": -1.0, "SCHD": 2.0})

        assert "VOO" in str(excinfo.value)

    @pytest.mark.parametrize("normalize", _NORMALIZERS)
    def test_result_never_contains_nan_for_valid_input(self, normalize):
        result = normalize({"VOO": 0.6, "SCHD": 0.4})
        assert not result.isna().any(), "น้ำหนักที่ผ่านด่านแล้วต้องเป็นจำนวนจริงทุกตัว"
        assert float(result.sum()) == pytest.approx(1.0)

    @pytest.mark.parametrize("normalize", _NORMALIZERS)
    @pytest.mark.parametrize("weights", [{}, {"VOO": 0.0}, {"VOO": 0.0, "SCHD": 0.0}])
    def test_unusable_weights_still_fail_loud(self, normalize, weights):
        with pytest.raises(ValueError):
            normalize(dict(weights))

    @pytest.mark.parametrize("normalize", _NORMALIZERS)
    def test_sum_that_overflows_to_inf_is_rejected(self, normalize):
        """ผลรวมล้นเป็น ``inf`` ⇒ ``w / inf`` = 0.0 ทุกกอง = เส้นแบนราบแบบเดียวกัน."""
        with pytest.raises(ValueError):
            normalize({"VOO": 1e308, "SCHD": 1e308})


# ---------------------------------------------------------------------------
# ชั้นการคำนวณ — ห้ามคืนเส้นมูลค่าที่ระบบไม่เคยคำนวณ
# ---------------------------------------------------------------------------


class TestSimulatorsRefuseNonFiniteWeights:
    def test_backtest_raises_instead_of_flat_curve(self):
        with pytest.raises(RuntimeError) as excinfo:
            backtest_mod.run_portfolio_backtest(
                price_df=_rising_prices(), weights={"VOO": INF}, initial_capital=10000.0
            )
        assert "VOO" in str(excinfo.value)

    def test_backtest_still_computes_real_curve_for_valid_weights(self):
        """ตัวคุม: ราคาขึ้น 100% ต้องได้ราว 20000 ไม่ใช่ 10000 แบน ๆ."""
        result = backtest_mod.run_portfolio_backtest(
            price_df=_rising_prices(), weights={"VOO": 1.0}, initial_capital=10000.0
        )
        values = result["Portfolio Value"]
        assert values.nunique() > 1, "เส้นมูลค่าแบนราบ = ไม่ได้คำนวณอะไรเลย"
        assert float(values.iloc[-1]) == pytest.approx(20000.0, rel=0.02)

    def test_dca_raises_instead_of_reporting_total_loss(self):
        with pytest.raises(RuntimeError) as excinfo:
            dca_mod.simulate_monthly_dca(
                price_df=_rising_prices(), weights={"VOO": INF}, monthly_investment=1000.0
            )
        assert "VOO" in str(excinfo.value)

    def test_all_nan_return_row_stays_nan_not_zero(self):
        """ตาข่ายชั้นสอง: แถวที่ไม่มีค่าจริงเลยต้องเป็น NaN (= ไม่รู้) ไม่ใช่ 0.0 (= ไม่ขยับ).

        ``DataFrame.sum(axis=1)`` ของ pandas ใช้ ``skipna=True`` — นี่คือจุดที่แปลง
        น้ำหนัก NaN ให้กลายเป็น "ผลตอบแทน 0% ทุกวัน" ต้องใส่ ``min_count=1``
        """
        weighted = backtest_mod._weighted_daily_returns(
            _rising_prices(), pd.Series({"VOO": NAN})
        )
        assert weighted.isna().all(), (
            "น้ำหนัก NaN ต้องให้ผลตอบแทน NaN ทั้งเส้น ไม่ใช่ 0.0 ที่อ่านว่าพอร์ตไม่ขยับ"
        )

    def test_weighted_returns_still_sum_real_values(self):
        """ตัวคุมของข้อบน — ``min_count=1`` ต้องไม่ทำให้แถวปกติกลายเป็น NaN."""
        weighted = backtest_mod._weighted_daily_returns(
            _rising_prices(), pd.Series({"VOO": 1.0})
        )
        assert not weighted.iloc[1:].isna().any()
        assert float(weighted.iloc[1]) > 0.0


# ---------------------------------------------------------------------------
# T6 — กองที่หายไปจากพอร์ตต้องมีชื่อติดไปกับผลลัพธ์เสมอ (ตัดเงียบ = กุตัวเลข)
# ---------------------------------------------------------------------------


class TestDroppedTickersAreReported:
    """AUDIT_ROUND2_2026-08-07 T6 — ``normalized_weights[normalized_weights > 0]``.

    บรรทัดเดียวใน ``_normalize_weights`` ตัดกองที่น้ำหนัก 0 ออกทั้งดุ้น แล้ว
    normalize ที่เหลือให้รวมเป็น 1.0 ⇒ ผลลัพธ์คือ backtest ของ **พอร์ตอื่น**
    ที่ถูกนำเสนอเป็นคำตอบของพอร์ตที่ผู้ใช้กรอก โดยไม่มี warning ไม่มีคีย์รายงาน
    ไม่มี log สักบรรทัด (หลักฐานในรายงาน: ``{'VOO': 0.5, 'SCHD': 0.0}`` →
    ``{'VOO': 1.0}`` และ API ตอบ 200 เฉย ๆ)

    การตัดยัง **ถูกต้อง** อยู่ (กองที่ตั้งใจไม่ถือไม่ควรบีบช่วงเวลาให้สั้นลง)
    สิ่งที่ผิดคือความเงียบ — ชื่อกองจึงต้องเดินทางไปกับผลลัพธ์ผ่านสำนวนเดียวกับ
    ``portfolio/dca.py`` (``coverage[excluded_zero_weight]`` / ``[excluded_no_price]``
    + ``describe_coverage()``) ไม่ใช่สำนวนที่สองที่หน้าจอต้องเรียนรู้ใหม่
    """

    def _coverage(self, result) -> dict:
        coverage = result.attrs.get(COVERAGE_ATTR)
        assert coverage is not None, (
            "ผลลัพธ์ไม่มีรายงานว่าใครถูกตัดออกจากพอร์ตเลย — ตัดเงียบคือบั๊กที่ T6 สั่งปิด"
        )
        return coverage

    def test_zero_weight_fund_is_named_in_the_result(self):
        """SCHD ที่ตั้งไว้ 0% ต้องมีชื่ออยู่ในผลลัพธ์ ไม่ใช่หายไปเฉย ๆ."""
        result = backtest_mod.run_portfolio_backtest(
            price_df=_two_fund_prices(), weights={"VOO": 0.5, "SCHD": 0.0}
        )

        coverage = self._coverage(result)
        assert coverage[ZERO_WEIGHT_KEY] == ["SCHD"]
        assert coverage[NO_PRICE_KEY] == [], "SCHD มีราคาอยู่ — เหตุผลที่ถูกตัดคือน้ำหนัก 0"

    def test_zero_weight_warning_is_readable_thai_and_names_the_fund(self):
        """ข้อความที่หน้าจอเอาไปแสดงต้องบอกทั้ง **ชื่อกอง** และ **เหตุผล**."""
        result = backtest_mod.run_portfolio_backtest(
            price_df=_two_fund_prices(), weights={"VOO": 0.5, "SCHD": 0.0}
        )

        warning = describe_coverage(self._coverage(result))
        assert warning is not None, "มีกองหายไปจากพอร์ตแต่ไม่มีคำเตือนให้แสดง"
        assert "SCHD" in warning
        assert "0" in warning  # เหตุผล: ตั้งน้ำหนักไว้ 0 (เจตนา ไม่ใช่ข้อมูลขาด)

    def test_zero_weight_fund_really_is_excluded_from_the_numbers(self):
        """ตัวคุม: รายงานต้องตรงกับตัวเลขจริง — SCHD (−50%) ต้องไม่ถูกนับเข้าพอร์ต."""
        result = backtest_mod.run_portfolio_backtest(
            price_df=_two_fund_prices(), weights={"VOO": 0.5, "SCHD": 0.0}, initial_capital=10000.0
        )

        assert float(result["Portfolio Value"].iloc[-1]) == pytest.approx(20000.0, rel=0.02)

    def test_nothing_dropped_still_sets_both_keys(self):
        """ไม่มีใครถูกตัด = ลิสต์ว่าง **ไม่ใช่คีย์หาย** — ปลายทางต้องแยกสองอย่างนี้ได้."""
        result = backtest_mod.run_portfolio_backtest(
            price_df=_two_fund_prices(), weights={"VOO": 0.5, "SCHD": 0.5}
        )

        coverage = self._coverage(result)
        assert coverage[ZERO_WEIGHT_KEY] == []
        assert coverage[NO_PRICE_KEY] == []
        assert describe_coverage(coverage) is None, "ไม่มีอะไรหาย ต้องไม่ขึ้นคำเตือนหลอก ๆ"

    def test_held_fund_without_price_column_is_reported_separately(self):
        """"ตั้งใจไม่ถือ" กับ "ไม่มีข้อมูลราคา" เป็นคนละเรื่อง ต้องอยู่คนละช่อง.

        กลุ่มหลังแปลว่าเส้นมูลค่าที่ได้เป็นของพอร์ตที่ normalize ใหม่บนกองที่เหลือ
        (VOO 100%) ทั้งที่ผู้ใช้กรอกมา 50/50 — ร้ายแรงกว่ากลุ่มแรกมาก
        """
        result = backtest_mod.run_portfolio_backtest(
            price_df=_rising_prices(), weights={"VOO": 0.5, "SCHD": 0.5}
        )

        coverage = self._coverage(result)
        assert coverage[NO_PRICE_KEY] == ["SCHD"]
        assert coverage[ZERO_WEIGHT_KEY] == []

        warning = describe_coverage(coverage)
        assert warning is not None and "SCHD" in warning
        assert "ไม่ใช่พอร์ตตามสัดส่วนที่กรอกมา" in warning

    def test_no_matching_price_column_names_the_missing_fund(self):
        """ไม่มีกองไหนมีราคาเลย = ล้มดัง และต้องบอกชื่อกองที่หา ไม่ใช่ข้อความรวม ๆ."""
        with pytest.raises(RuntimeError) as excinfo:
            backtest_mod.run_portfolio_backtest(
                price_df=_rising_prices(), weights={"SCHD": 1.0}
            )

        assert "SCHD" in str(excinfo.value)

    def test_end_to_end_backtest_carries_coverage_and_warning(self, monkeypatch):
        """``run_backtest()`` คืน dict — ต้องมี ``coverage``/``coverage_warning``.

        รูปแบบเดียวกับ ``portfolio.dca.simulate_dca`` เป๊ะ ๆ เพื่อให้ผู้เรียก
        (แดชบอร์ด/service) อ่านด้วยโค้ดชุดเดียว ไม่ต้องรู้จักสองสำนวน
        """
        prices = _two_fund_prices()
        monkeypatch.setattr(
            backtest_mod, "fetch_adjusted_close_data", lambda **kwargs: prices.copy()
        )

        output = backtest_mod.run_backtest(
            weights={"VOO": 1.0, "SCHD": 0.0},
            initial_investment=10000.0,
            start_date="2020-01-01",
        )

        assert output["coverage"][ZERO_WEIGHT_KEY] == ["SCHD"]
        assert output["coverage"][NO_PRICE_KEY] == []
        assert "SCHD" in (output["coverage_warning"] or "")

    def test_end_to_end_backtest_has_no_warning_when_nothing_is_dropped(self, monkeypatch):
        """ตัวคุม: พอร์ตที่ครบถ้วนต้องไม่ขึ้นคำเตือน (คำเตือนหลอกทำให้คนเลิกอ่าน)."""
        prices = _two_fund_prices()
        monkeypatch.setattr(
            backtest_mod, "fetch_adjusted_close_data", lambda **kwargs: prices.copy()
        )

        output = backtest_mod.run_backtest(
            weights={"VOO": 0.6, "SCHD": 0.4},
            initial_investment=10000.0,
            start_date="2020-01-01",
        )

        assert output["coverage"][ZERO_WEIGHT_KEY] == []
        assert output["coverage_warning"] is None


# ---------------------------------------------------------------------------
# ชั้น schema — API ต้องตอบ 422 พร้อมข้อความไทย ไม่ใช่ 200 พร้อมตัวเลขที่กุขึ้น
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_client(monkeypatch):
    from fastapi.testclient import TestClient

    from backend.main import app as fastapi_app
    from backend.services import market_analysis_service as service

    prices = _rising_prices()
    monkeypatch.setattr(service, "_prices", lambda: prices)
    monkeypatch.setenv("VAULTIS_API_KEY", "test-key")

    # ไม่เข้า ``with`` = ไม่รัน lifespan (ไม่ปลุก APScheduler ของ backend.main)
    return TestClient(fastapi_app, raise_server_exceptions=False)


_HEADERS = {"X-API-Key": "test-key", "Content-Type": "application/json"}

_BAD_BODIES = [
    pytest.param('{"weights": {"VOO": Infinity}}', id="inf"),
    pytest.param('{"weights": {"VOO": -Infinity}}', id="neg_inf"),
    pytest.param('{"weights": {"VOO": NaN}}', id="nan"),
    pytest.param('{"weights": {"VOO": Infinity, "SCHD": 1.0}}', id="inf_with_good"),
    pytest.param('{"weights": {"VOO": -1.0, "SCHD": 2.0}}', id="negative"),
    pytest.param('{"weights": {"VOO": 0.0, "SCHD": 0.0}}', id="all_zero"),
    pytest.param('{"weights": {}}', id="empty"),
]


def _body_text(response) -> str:
    return json.dumps(response.json(), ensure_ascii=False)


class TestApiRejectsBadWeights:
    @pytest.mark.parametrize("raw_body", _BAD_BODIES)
    @pytest.mark.parametrize("path", ["/api/analysis/backtest", "/api/dca/simulate"])
    def test_bad_weights_get_422_with_thai_message(self, api_client, path, raw_body):
        response = api_client.post(path, content=raw_body.encode("utf-8"), headers=_HEADERS)

        assert response.status_code == 422, (
            f"{path} ต้องปฏิเสธน้ำหนักที่ใช้ไม่ได้ที่ชั้น schema แต่ได้ "
            f"{response.status_code}: {_body_text(response)[:300]}"
        )
        text = _body_text(response)
        assert "น้ำหนัก" in text, f"ข้อความต้องอ่านออกเป็นภาษาไทย (ได้: {text[:300]})"

    @pytest.mark.parametrize(
        ("path", "extra"),
        [
            ("/api/analysis/backtest", '"initial_capital": 10000.0'),
            ("/api/dca/simulate", '"monthly_investment": 1000.0'),
        ],
    )
    def test_valid_weights_still_compute(self, api_client, path, extra):
        response = api_client.post(
            path,
            content=('{%s, "weights": {"VOO": 1.0}}' % extra).encode("utf-8"),
            headers=_HEADERS,
        )

        assert response.status_code == 200, _body_text(response)[:300]
        data = response.json()["data"]
        rows = data if isinstance(data, list) else data["history"]
        values = {row["Portfolio Value"] for row in rows}
        assert len(values) > 1, "ราคาขึ้น 100% แล้วมูลค่าพอร์ตต้องขยับ ไม่ใช่เส้นแบน"

    @pytest.mark.parametrize(
        ("path", "raw_body"),
        [
            ("/api/analysis/backtest", '{"initial_capital": Infinity, "weights": {"VOO": 1.0}}'),
            ("/api/dca/simulate", '{"monthly_investment": Infinity, "weights": {"VOO": 1.0}}'),
        ],
    )
    def test_non_finite_amount_is_rejected_too(self, api_client, path, raw_body):
        """เงินตั้งต้น/งบรายเดือนก็เป็นตัวเลขบนเส้นทางเงิน — ``gt=0`` ไม่กัน ``inf``."""
        response = api_client.post(path, content=raw_body.encode("utf-8"), headers=_HEADERS)

        assert response.status_code == 422, (
            f"{path} ต้องปฏิเสธจำนวนเงินที่เป็น inf แต่ได้ {response.status_code}: "
            f"{_body_text(response)[:300]}"
        )
