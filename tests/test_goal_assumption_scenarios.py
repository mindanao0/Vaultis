# -*- coding: utf-8 -*-
"""FIX_PLAN เฟส 4① (ส่วนที่เหลือ) — สมมติฐานที่ป้อน Monte Carlo หน้า Goals.

สามอย่างที่ปิดในคอมมิตนี้ (ส่วน μ เลขคณิต/เรขาคณิต + ป้ายช่วงข้อมูลจริง ปิดไปแล้วที่ 86b54cc):

1. **ข้อมูลถูกตัดโดยไม่มีใครรู้** QQQM ลิสต์ 2020-10, GLDM 2018-06 ⇒ ``dropna`` ตัด
   ประวัติร่วมเหลือ ~5.8 ปี **ที่ไม่มีวิกฤตใหญ่สักรอบ** σ/maxDD จึงมองโลกสวยอย่างเป็นระบบ
   วัดจริง 2026-08-08 (น้ำหนักเท่ากัน): ยืดด้วยกองพี่แล้ว 5.8 ปี → **14.8 ปี**,
   maxDD **−35.0% → −42.6%** (ลึกขึ้น 7.6 จุดที่ผู้ใช้ควรเตรียมใจ)
2. **μ ที่วัดจากอดีตถูกใช้เป็น μ พยากรณ์** ตัวเลขเดียวบนจอถูกอ่านเป็นคำพยากรณ์เสมอ
   ตอนตรวจ: μ 15.08% → P 85.0% · 12% → 57.5% · 9% → 25.9% · 7% → 11.5%
   ⇒ **ต่าง 73 จุด** จากสมมติฐานตัวเดียว จึงต้องโชว์หลายฉากคู่กัน
3. **normal iid ล้วน** ไม่มีทั้งหางอ้วนและการเกาะกลุ่มของเดือนแย่ ๆ ที่ตลาดจริงมี
   block bootstrap จากผลตอบแทนจริงให้คำตอบต่ำกว่าอย่างเป็นระบบ (ตอนตรวจต่าง 18.9 จุด)

**ข้อบังคับที่ต้องไม่ลืม** ประวัติที่ยืดมาต้องบอกที่มาเสมอ — ช่วงก่อนวันลิสต์เป็นผลตอบแทน
ของ **กองพี่** ไม่ใช่ของกองที่ถืออยู่จริง ตัวเลขที่ยืดโดยไม่บอกที่มาคือการกุข้อมูล
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analysis.proxy_history import (
    PROXY_MAP,
    describe_proxies,
    proxy_tickers_for,
    splice_with_proxy,
)
from backend.services import goal_service


def _frame() -> pd.DataFrame:
    """OLD (กองพี่) มีประวัติเต็ม · NEW เพิ่งลิสต์ครึ่งทาง — เลียนแบบ QQQ/QQQM."""
    n = 500
    index = pd.bdate_range("2018-01-01", periods=n)
    proxy = 50.0 * np.cumprod(1.0 + np.linspace(0.0012, 0.0004, n))
    real = np.full(n, np.nan)
    real[250:] = proxy[250:] * 2.0  # ราคาต่อหน่วยคนละระดับ แต่ตามดัชนีเดียวกัน
    return pd.DataFrame({"QQQM": real, "QQQ": proxy}, index=index)


class TestProxyTickerSelection:
    def test_ขอเฉพาะกองพี่ที่ต้องใช้(self):
        assert proxy_tickers_for(["QQQM", "VOO"]) == ["QQQ"]
        assert proxy_tickers_for(["VOO", "SCHD"]) == []

    def test_ไม่ขอซ้ำถ้าถือกองพี่อยู่แล้ว(self):
        assert proxy_tickers_for(["QQQM", "QQQ"]) == []

    def test_ตารางกองพี่มีชุดเดียวทั้งระบบ(self):
        from portfolio.ab_backtest import PROXY_MAP as AB_MAP

        assert AB_MAP is PROXY_MAP, "ab_backtest ต้องอ่านตารางเดียวกัน ไม่ใช่ประกาศซ้ำ"


class TestSpliceIsContinuous:
    def test_ประวัติยาวขึ้นจริง(self):
        frame = _frame()
        extended, report = splice_with_proxy(frame, ["QQQM"])
        assert extended["QQQM"].notna().sum() > frame["QQQM"].notna().sum()
        assert report["proxied"]["QQQM"]["proxy"] == "QQQ"
        assert report["proxied"]["QQQM"]["added_days"] == 250

    def test_ไม่มีวันกระโดดปลอมที่รอยต่อ(self):
        """ต่อที่ระดับราคาดิบจะสร้างความผันผวนหนึ่งวันที่ไม่เคยเกิด แล้วไหลเข้า σ/maxDD."""
        extended, _ = splice_with_proxy(_frame(), ["QQQM"])
        returns = extended["QQQM"].pct_change(fill_method=None).dropna()
        assert returns.abs().max() < 0.05, f"มีวันกระโดด {returns.abs().max():.1%} ที่รอยต่อ"

    def test_ช่วงหลังวันลิสต์ต้องเป็นราคาจริงไม่ถูกแตะ(self):
        frame = _frame()
        extended, _ = splice_with_proxy(frame, ["QQQM"])
        after = frame["QQQM"].dropna().index
        pd.testing.assert_series_equal(extended.loc[after, "QQQM"], frame.loc[after, "QQQM"])

    def test_ผลตอบแทนช่วงที่ยืดมาเท่ากับของกองพี่(self):
        frame = _frame()
        extended, _ = splice_with_proxy(frame, ["QQQM"])
        head = extended.index[:200]
        pd.testing.assert_series_equal(
            extended.loc[head, "QQQM"].pct_change(fill_method=None).dropna(),
            frame.loc[head, "QQQ"].pct_change(fill_method=None).dropna(),
            check_names=False,
        )

    def test_คอลัมน์กองพี่ไม่ติดกลับไปเป็นกองที่ถือ(self):
        extended, _ = splice_with_proxy(_frame(), ["QQQM"])
        assert list(extended.columns) == ["QQQM"], "QQQ ที่ดึงมายืดประวัติต้องไม่กลายเป็นกองในพอร์ต"

    def test_ไม่มีกองพี่ในเฟรมต้องรายงานเหตุผลไม่ใช่เงียบ(self):
        frame = _frame()[["QQQM"]]
        _extended, report = splice_with_proxy(frame, ["QQQM"])
        assert "QQQM" in report["skipped"] and "QQQ" in report["skipped"]["QQQM"]
        assert not report["proxied"]

    def test_กองพี่ไม่มีประวัติก่อนหน้าก็รายงาน(self):
        frame = _frame()
        frame.loc[frame.index[:250], "QQQ"] = np.nan
        _extended, report = splice_with_proxy(frame, ["QQQM"])
        assert "QQQM" in report["skipped"]

    def test_ที่มาต้องมีประโยคให้ผู้ใช้อ่าน(self):
        _extended, report = splice_with_proxy(_frame(), ["QQQM"])
        note = describe_proxies(report)
        assert "QQQ" in note and "ไม่ใช่ของกองที่ถืออยู่จริง" in note

    def test_ไม่มีการยืดก็ไม่มีประโยค(self):
        assert describe_proxies({"proxied": {}, "skipped": {}}) == ""


class TestDeeperHistoryChangesTheAnswer:
    def test_ยืดประวัติแล้วความเสี่ยงลึกขึ้น(self):
        """ฉากจำลองต้องแสดงว่าตัวเลขเปลี่ยนจริง ไม่ใช่แค่ "รันได้"."""
        from analysis.risk import calculate_max_drawdown

        n = 500
        index = pd.bdate_range("2018-01-01", periods=n)
        proxy = np.concatenate(
            [np.linspace(100.0, 40.0, 120), np.linspace(40.0, 200.0, n - 120)]
        )  # ครัช −60% ก่อนกองจริงจะเกิด
        real = np.full(n, np.nan)
        real[250:] = proxy[250:]
        frame = pd.DataFrame({"QQQM": real, "QQQ": proxy}, index=index)

        before = float(calculate_max_drawdown(frame[["QQQM"]]).iloc[0])
        extended, _ = splice_with_proxy(frame, ["QQQM"])
        after = float(calculate_max_drawdown(extended).iloc[0])
        assert after < before - 0.3, f"ยืดแล้ว maxDD ต้องลึกขึ้นมาก: {before:.2%} → {after:.2%}"


class TestBlockBootstrap:
    HIST = list(np.random.default_rng(0).normal(0.008, 0.045, 180))

    def test_ทุกค่าที่สุ่มมาจากอดีตจริง(self):
        rng = np.random.default_rng(1)
        out = goal_service._block_bootstrap(rng, self.HIST, 20, 60)
        assert out.shape == (20, 60)
        assert set(np.round(out.ravel(), 12)) <= set(np.round(self.HIST, 12))

    def test_เก็บลำดับของเดือนที่ติดกัน(self):
        """หัวใจของ block bootstrap — สุ่มทีละเดือนจะทำลายการเกาะกลุ่มของเดือนแย่ ๆ."""
        rng = np.random.default_rng(2)
        row = goal_service._block_bootstrap(rng, self.HIST, 1, 24)[0]
        idx = [np.argmin(np.abs(np.asarray(self.HIST) - v)) for v in row[:6]]
        assert idx == list(range(idx[0], idx[0] + 6)), "หกเดือนแรกต้องเป็นลำดับติดกันจากอดีต"

    def test_ใช้แทน_normal_เมื่อมีข้อมูลจริงพอ(self):
        kw = dict(
            current=100_000.0,
            monthly_contribution=10_000.0,
            months=240,
            annual_return=0.12,
            target=8_000_000.0,
            volatility=0.15,
        )
        normal = goal_service.calculate_probability(**kw)
        boot = goal_service.calculate_probability(**kw, historical_monthly=self.HIST)
        assert boot != pytest.approx(normal), "ผลต้องต่างจริง ไม่งั้นแปลว่าไม่ได้ใช้ bootstrap"

    def test_ข้อมูลสั้นเกินไปถอยไปใช้_normal_ไม่ใช่ล้ม(self):
        kw = dict(
            current=100_000.0,
            monthly_contribution=10_000.0,
            months=120,
            annual_return=0.09,
            target=3_000_000.0,
            volatility=0.15,
        )
        assert goal_service.calculate_probability(
            **kw, historical_monthly=self.HIST[:10]
        ) == pytest.approx(goal_service.calculate_probability(**kw))
        assert goal_service.calculate_probability(**kw, historical_monthly=[]) == pytest.approx(
            goal_service.calculate_probability(**kw)
        )


class _Goal:
    id = 1
    target_amount_thb = 8_000_000.0
    current_amount_thb = 100_000.0
    monthly_contribution_thb = 10_000.0
    risk_profile = "moderate"
    target_date = (pd.Timestamp.today() + pd.DateOffset(years=20)).date().isoformat()
    name = "เกษียณ"


class TestScenariosReplaceTheSingleNumber:
    @pytest.fixture()
    def progress(self, monkeypatch):
        monkeypatch.setattr(
            goal_service,
            "real_portfolio_assumptions_with_status",
            lambda: {
                "status": goal_service.ASSUMPTIONS_OK,
                "mu": 0.1508,
                "mu_geometric": 0.1508,
                "mu_arithmetic": 0.1620,
                "sigma": 0.1576,
                "window": {"start": "2011-10-21", "end": "2026-08-07", "days": 3719,
                           "days_available": 3719, "years": 14.8, "tickers": ["VOO"],
                           "proxy": {}, "proxy_note": ""},
                "monthly_returns": [],
                "source": "พอร์ตจริง",
                "error": None,
                "data_ok": True,
            },
        )
        return goal_service._build_progress(_Goal())

    def test_มีฉากวัดจากอดีตและฉาก_preset_ครบ(self, progress):
        labels = [row["label"] for row in progress["scenarios"]]
        assert "อดีต" in labels[0], labels
        assert any("7%" in l for l in labels) and any("9%" in l for l in labels)

    def test_แต่ละฉากมีทั้งความน่าจะเป็นและเงินที่ต้องออม(self, progress):
        for row in progress["scenarios"]:
            assert 0.0 <= row["probability_of_success"] <= 1.0
            assert row["required_monthly_pmt"] > 0

    def test_สมมติฐานที่ต่ำกว่าให้ความน่าจะเป็นต่ำกว่าและต้องออมมากกว่า(self, progress):
        by_label = {row["label"]: row for row in progress["scenarios"]}
        low, high = by_label["ระมัดระวัง (7%)"], by_label["ก้าวร้าว (12%)"]
        assert low["probability_of_success"] < high["probability_of_success"]
        assert low["required_monthly_pmt"] > high["required_monthly_pmt"]

    def test_ช่องว่างระหว่างฉากต้องกว้างพอที่จะเห็นว่าสมมติฐานสำคัญ(self, progress):
        probs = [row["probability_of_success"] for row in progress["scenarios"]]
        assert max(probs) - min(probs) > 0.2, (
            f"ฉากทั้งหมดให้คำตอบใกล้กันเกินไป ({probs}) — ฉากที่ไม่ต่างกันไม่ได้บอกอะไร"
        )

    def test_เป้าหมายมีอำนาจซื้อจริงกำกับ(self, progress):
        assert progress["target_real_value_thb"] < _Goal.target_amount_thb
        assert progress["assumed_inflation_pct"] == pytest.approx(2.0)
        # 20 ปีที่ 2%/ปี ⇒ เหลืออำนาจซื้อราว 67%
        ratio = progress["target_real_value_thb"] / _Goal.target_amount_thb
        assert 0.66 <= ratio <= 0.69, ratio

    def test_บอกว่าใช้วิธีไหนจำลอง(self, progress):
        assert progress["probability_method"] in {"normal", "bootstrap"}


class TestAssumptionsPathAsksForProxies:
    """ตรึงเส้นทางจริง — เทสต์ฉากด้านบน stub สมมติฐานทิ้ง จึงไม่เคยเดินผ่านการดึงราคา."""

    def test_ดึงราคากองพี่มาด้วยและยืดประวัติจริง(self, monkeypatch):
        import pandas as pd_

        asked: dict = {}

        def _fake_fetch(tickers, years=10):
            asked["tickers"] = list(tickers)
            n = 400
            index = pd_.bdate_range("2019-01-01", periods=n)
            proxy = 50.0 * np.cumprod(1.0 + np.full(n, 0.0006))
            real = np.full(n, np.nan)
            real[200:] = proxy[200:] * 2.0
            return pd_.DataFrame(
                {"QQQM": real, "QQQ": proxy, "VOO": proxy * 1.5}, index=index
            )

        holdings = pd_.DataFrame(
            [
                {"Ticker": "QQQM", "Current Value (THB)": 50_000.0, "Price OK": True},
                {"Ticker": "VOO", "Current Value (THB)": 50_000.0, "Price OK": True},
            ]
        )
        # ``_compute_real_portfolio_assumptions`` import ทั้งสองตัวแบบ function-local
        # (กัน import วนและ FastAPI startup ช้า) จึงต้อง patch ที่โมดูลต้นทาง
        import data.fetcher as fetcher_mod
        import portfolio.tracker as tracker_mod

        monkeypatch.setattr(tracker_mod, "get_portfolio_summary", lambda: holdings)
        monkeypatch.setattr(fetcher_mod, "fetch_adjusted_close_data", _fake_fetch)
        monkeypatch.setattr(goal_service, "_real_assumptions_cache", None, raising=False)

        result = goal_service._compute_real_portfolio_assumptions()

        assert "QQQ" in asked["tickers"], (
            f"ไม่ได้ขอกองพี่มายืดประวัติเลย (ขอ {asked['tickers']}) — หน้าต่างจะสั้นเท่าเดิม"
        )
        assert result["status"] == goal_service.ASSUMPTIONS_OK
        window = result["window"]
        assert window["proxy"].get("QQQM", {}).get("proxy") == "QQQ"
        assert "QQQ" in window["proxy_note"]
        assert "QQQ" in result["source"], "ป้ายที่มาต้องบอกว่าประวัติบางส่วนมาจากกองพี่"
        assert "QQQ" not in window["tickers"], "กองพี่ต้องไม่กลายเป็นกองในพอร์ต"
        # ยืดแล้วหน้าต่างต้องยาวกว่าช่วงร่วมของกองจริง (200 แท่ง)
        assert window["days"] > 200

    def test_ไม่มีพอร์ตจริงต้องไม่มีฉากวัดจากอดีต(self, monkeypatch):
        monkeypatch.setattr(
            goal_service,
            "real_portfolio_assumptions_with_status",
            lambda: {
                "status": goal_service.ASSUMPTIONS_EMPTY,
                "mu": None,
                "mu_geometric": None,
                "mu_arithmetic": None,
                "sigma": None,
                "window": None,
                "monthly_returns": [],
                "source": "ยังไม่มีพอร์ตใน ledger",
                "error": None,
                "data_ok": False,
            },
        )
        progress = goal_service._build_progress(_Goal())
        labels = [row["label"] for row in progress["scenarios"]]
        assert labels, "ยังต้องมีฉาก preset ให้ผู้ใช้เห็น"
        assert all("อดีต" not in label for label in labels), (
            f"ไม่มีพอร์ตจริงแต่ยังโชว์ฉาก 'วัดจากอดีต': {labels}"
        )
