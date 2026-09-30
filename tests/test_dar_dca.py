# -*- coding: utf-8 -*-
"""DAR-DCA — พอร์ตทดลองแยก: สูตร, ข้อมูล, เงิน, สมุด, งาน Discord.

ไม่มีเทสต์ไหนยิงเน็ต: ราคา/อัตราแลกเปลี่ยน/Discord ถูกฉีดเข้าไปทั้งหมด
ข้อที่สำคัญที่สุดคือ ``TestLockedFormula``: สูตรที่รันจริงต้องเท่ากับสูตรที่ถูก pre-register
ไว้ใน ``research/dar_dca/`` ทุกตัวเลข — ถ้าวันหนึ่งมีคนจูนค่าคงที่ตามผลของพอร์ตที่รันอยู่
เทสต์นี้ต้องแดง (การเปลี่ยนสูตรต้องผ่านการล็อก + ทดสอบบนข้อมูลใหม่ ไม่ใช่แก้ตรงนี้)
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from analysis import dar_dca as D
from data.fetcher import PriceDataUnavailableError
from jobs import dar_monthly as J
from portfolio import dar_ledger as L
from utils.fx import FxRate

REPO_ROOT = Path(__file__).resolve().parent.parent
RESEARCH = REPO_ROOT / "research" / "dar_dca"
BKK = ZoneInfo("Asia/Bangkok")


# ----------------------------------------------------------------------------- helpers
def _month_ends(n: int, end: str = "2026-08") -> pd.DatetimeIndex:
    return pd.period_range(end=end, periods=n, freq="M").to_timestamp("M")


def _series(n: int, g: float, shock: float = 0.0) -> np.ndarray:
    v = np.exp(g * np.arange(n))
    if shock:
        v[-60:] *= np.exp(shock * np.arange(1, 61) / 60)
    return v


# ----------------------------------------------------------------------------- formula
class TestSignal:
    def test_กองที่โตคงที่เป็นนิสัยได้สัญญาณเป็นศูนย์(self):
        idx = _month_ends(200)
        df = pd.DataFrame({"A": _series(200, 0.008), "C": _series(200, 0.002)}, index=idx)
        _, sig = D.dar_weights(df)
        by = {s.ticker: s for s in sig}
        assert abs(by["A"].dar) < 0.01 and abs(by["C"].dar) < 0.01, "นิสัยถาวร (drift) ต้องถูกหักออกจนเหลือ ~0"

    def test_ร่วงชั่วคราวเกินนิสัยได้สัญญาณบวกราวขนาดการร่วง(self):
        idx = _month_ends(200)
        df = pd.DataFrame({"A": _series(200, 0.008), "B": _series(200, 0.008, shock=-0.2)}, index=idx)
        w, sig = D.dar_weights(df)
        b = next(s for s in sig if s.ticker == "B")
        assert 0.15 < b.dar < 0.25
        assert w["B"] > w["A"]
        assert b.label.startswith("ตามหลังผิดปกติ")

    def test_น้ำหนักอยู่ในกรอบพื้นและเพดานเสมอ(self):
        rng = np.random.default_rng(0)
        idx = _month_ends(220)
        for n in (2, 3, 5, 8):
            df = pd.DataFrame(np.exp(np.cumsum(rng.normal(0.006, 0.05, (220, n)), axis=0)), index=idx,
                              columns=[f"F{i}" for i in range(n)])
            w, _ = D.dar_weights(df)
            assert w.sum() == pytest.approx(1.0, abs=1e-12)
            assert (w >= D.FLOOR_FRAC / n - 1e-12).all(), "ทุกกองต้องได้ขั้นต่ำ (ซื้อทุกกองทุกเดือน)"
            assert (w <= D.CAP_MULT / n + 1e-12).all(), "ไม่มีกองไหนเกินเพดาน 1.5 เท่าของส่วนเท่ากัน"

    def test_กองเดียวได้ทั้งหมด(self):
        df = pd.DataFrame({"A": _series(200, 0.01)}, index=_month_ends(200))
        w, _ = D.dar_weights(df)
        assert w["A"] == pytest.approx(1.0)

    def test_ประวัติไม่พอได้ส่วนกลางพร้อมเหตุผล_ไม่เดาสัญญาณ(self):
        idx = _month_ends(200)
        df = pd.DataFrame({"A": _series(200, 0.008), "B": _series(200, 0.008, -0.2), "N": _series(200, 0.01)}, index=idx)
        df.loc[df.index[:60], "N"] = np.nan  # 140 เดือน < 181
        _, sig = D.dar_weights(df)
        n = next(s for s in sig if s.ticker == "N")
        assert n.dar is None and n.z == 0.0
        assert "140" in n.neutral_reason and "181" in n.neutral_reason
        assert n.label.startswith("กลาง")

    def test_มีสัญญาณไม่ถึงสองกองแบ่งเท่ากัน(self):
        idx = _month_ends(150)
        df = pd.DataFrame({"A": _series(150, 0.01), "B": _series(150, 0.0)}, index=idx)
        w, sig = D.dar_weights(df)
        assert w.tolist() == pytest.approx([0.5, 0.5])

    def test_กองที่เกือบเหมือนกันไม่ถูกเอียงเพราะสัญญาณรบกวน(self):
        idx = _month_ends(200)
        a = _series(200, 0.008)
        df = pd.DataFrame({"VOO": a, "SPY": a * np.exp(0.002 * np.sin(np.arange(200) / 7))}, index=idx)
        w, _ = D.dar_weights(df)
        assert abs(w["VOO"] - 0.5) < 0.02, "SD_MIN ต้องกันไม่ให้ความต่างจิ๋วถูกขยายเป็นการเอียงเต็มขนาด"


class TestLockedFormula:
    """สูตรที่รันจริง == สูตรที่ล็อกไว้ใน research/dar_dca (PREREG.md + PREREG_v2.md)."""

    def test_ค่าคงที่ตรงกับที่ล็อกไว้(self):
        assert (D.HISTORY_MONTHS, D.AMP_FROM, D.AMP_TO) == (181, 54, 66)
        assert (D.DRIFT_COEF, D.K, D.FLOOR_FRAC, D.SD_MIN, D.CAP_MULT) == (0.5, 1.0, 0.2, 0.15, 1.5)

    def test_ผลเท่ากับสูตรอ้างอิงที่ล็อกไว้ทุกตัวเลข(self, monkeypatch):
        if not (RESEARCH / "dar_formula_v2.py").exists():
            pytest.skip("ไม่มี research/dar_dca ในเช็คเอาต์นี้")
        monkeypatch.syspath_prepend(str(RESEARCH))
        sys.modules.pop("dar_formula", None)
        sys.modules.pop("dar_formula_v2", None)
        import dar_formula_v2 as ref  # noqa: PLC0415

        rng = np.random.default_rng(42)
        for trial in range(25):
            n = int(rng.integers(2, 9))
            T = int(rng.integers(150, 260))
            data = np.exp(np.cumsum(rng.normal(0.006, 0.06, (T, n)), axis=0))
            df = pd.DataFrame(data, index=_month_ends(T), columns=[f"F{i}" for i in range(n)])
            if trial % 3 == 0:  # บางกองประวัติสั้น
                df.iloc[: int(rng.integers(10, 80)), 0] = np.nan
            live, _ = D.dar_weights(df)
            locked = ref.dar_weights_v2(df)
            assert np.abs(live.to_numpy() - locked.to_numpy()).max() < 1e-12, f"trial {trial}"


# ----------------------------------------------------------------------------- data
def _fake_daily(start: str, end: str, tickers: dict[str, tuple[str, float]]) -> pd.DataFrame:
    idx = pd.bdate_range(start, end)
    cols = {}
    for t, (first, drift) in tickers.items():
        s = pd.Series(np.exp(drift * np.arange(len(idx))), index=idx)
        s[s.index < pd.Timestamp(first)] = np.nan
        cols[t] = s
    return pd.DataFrame(cols)


class TestHistory:
    def test_ไม่ใช้ราคาของเดือนที่ซื้อ_ไม่มีการดูอนาคต(self):
        daily = _fake_daily("2005-01-03", "2026-10-15", {"VOO": ("2005-01-03", 0.0004), "XLV": ("2005-01-03", 0.0003)})
        calls = []

        def fetch(tickers, years):
            calls.append((tuple(tickers), years))
            return daily

        me, used, _ = D.load_month_end_history(["VOO", "XLV"], pd.Period("2026-10", "M"), fetch)
        assert me.index.max() == pd.Timestamp("2026-09-30")
        assert calls and calls[0][1] == D.FETCH_YEARS

    def test_ต่อประวัติกองพี่โดยปรับระดับที่วันเชื่อม(self):
        daily = _fake_daily("2005-01-03", "2026-09-30", {"QQQ": ("2005-01-03", 0.0005), "QQQM": ("2020-10-13", 0.0005)})
        daily["QQQM"] = daily["QQQM"] * 0.37  # ระดับราคาคนละขนาดกับกองพี่
        seen = {}

        def fetch(tickers, years):
            seen["tickers"] = list(tickers)
            return daily

        me, used, _ = D.load_month_end_history(["QQQM"], pd.Period("2026-10", "M"), fetch)
        assert "QQQ" in seen["tickers"] and used == {"QQQM": "QQQ"}
        s = me["QQQM"].dropna()
        assert len(s) >= D.HISTORY_MONTHS
        r = np.log(s).diff().dropna()
        assert r.abs().max() < 0.02, "ไม่มีเดือนกระโดดปลอมที่จุดเชื่อม (ผลตอบแทนรายเดือนคงที่ ~1%)"

    def test_บอกวันที่ของราคาจริง_ไม่ใช่ป้ายสิ้นเดือน(self):
        daily = _fake_daily("2005-01-03", "2026-09-29", {"VOO": ("2005-01-03", 0.0004), "XLV": ("2005-01-03", 0.0003)})
        plan = D.build_plan(["VOO", "XLV"], 5000, pd.Period("2026-10", "M"), fetch=lambda t, years: daily,
                            fx_fn=lambda: FxRate(33.0, True))
        assert plan.data_through == "2026-09-29", "เดือนยังไม่ปิด ห้ามอ้างว่าข้อมูลถึงวันที่ 30"

    def test_กองที่ไม่มีราคาเลยต้องล้มดัง(self):
        daily = _fake_daily("2005-01-03", "2026-09-30", {"VOO": ("2005-01-03", 0.0004)})
        with pytest.raises(PriceDataUnavailableError):
            D.load_month_end_history(["VOO", "SCHD"], pd.Period("2026-10", "M"), lambda t, years: daily)


class TestFetcher:
    """ตัวดึงราคาของ DAR — ต้องไม่ใช้ yf.download (ค้างเมื่ออีกเธรดใน dashboard ดาวน์โหลดพร้อมกัน)."""

    def _fake_yf(self, monkeypatch, behaviour):
        import yfinance as yf  # noqa: PLC0415

        calls = []

        class FakeTicker:
            def __init__(self, sym):
                self.sym = sym

            def history(self, **kw):
                calls.append((self.sym, kw))
                return behaviour(self.sym, kw)

        monkeypatch.setattr(yf, "Ticker", FakeTicker)
        monkeypatch.setattr(yf, "download", lambda *a, **k: pytest.fail("DAR ห้ามเรียก yf.download"))
        monkeypatch.setattr("time.sleep", lambda s: None)
        return calls

    def test_ประกอบราคาหลายกองและตัด_timezone(self, monkeypatch):
        idx = pd.date_range("2026-09-01", periods=3, freq="B", tz="America/New_York")

        def ok(sym, kw):
            assert kw["auto_adjust"] is True, "ต้องเป็นราคาปรับปันผล (total return)"
            return pd.DataFrame({"Close": [1.0, 2.0, 3.0] if sym == "VOO" else [5.0, 6.0, 7.0]}, index=idx)

        calls = self._fake_yf(monkeypatch, ok)
        df = D.fetch_total_return_history(["voo", "XLV", "VOO"], years=17)
        assert list(df.columns) == ["VOO", "XLV"] and [c[0] for c in calls] == ["VOO", "XLV"]
        assert df.index.tz is None and df["XLV"].iloc[-1] == 7.0

    def test_ล้มครบสามครั้งต้องล้มดัง(self, monkeypatch):
        def boom(sym, kw):
            raise ConnectionError("rate limited")

        calls = self._fake_yf(monkeypatch, boom)
        with pytest.raises(PriceDataUnavailableError, match="rate limited"):
            D.fetch_total_return_history(["VOO"], years=17)
        assert len(calls) == D.FETCH_ATTEMPTS

    def test_ได้ตารางว่างถือว่าล้ม_ไม่ใช่ราคาศูนย์(self, monkeypatch):
        self._fake_yf(monkeypatch, lambda sym, kw: pd.DataFrame({"Close": []}))
        with pytest.raises(PriceDataUnavailableError):
            D.fetch_total_return_history(["VOO"], years=17)

    def test_โมดูล_DAR_ไม่เรียก_yf_download_ที่ไหนเลย(self):
        for rel in ("analysis/dar_dca.py", "dashboard/dar_page.py", "jobs/dar_monthly.py", "portfolio/dar_ledger.py"):
            src = (REPO_ROOT / rel).read_text(encoding="utf-8")
            code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
            assert "fetch_adjusted_close_data" not in code and ".download(" not in code, rel


# ----------------------------------------------------------------------------- money
class TestMoney:
    def test_ปัดหลักร้อยรวมพอดีงบและทุกกองอย่างน้อยร้อยบาท(self):
        w = pd.Series({"A": 0.62, "B": 0.33, "C": 0.05})
        out = D.round_to_units(w, 5000)
        assert sum(out.values()) == 5000
        assert all(v % 100 == 0 and v >= 100 for v in out.values())
        tiny = D.round_to_units(pd.Series({"A": 0.97, "B": 0.02, "C": 0.01}), 1000)
        assert sum(tiny.values()) == 1000 and min(tiny.values()) == 100

    def test_งบไม่ลงตัวบอกเศษแทนการยัดเข้ากองใด(self):
        out = D.round_to_units(pd.Series({"A": 0.5, "B": 0.5}), 5050)
        assert sum(out.values()) == 5000

    @pytest.mark.parametrize("budget", [0, -100, float("nan"), float("inf")])
    def test_งบผิดรูปต้องล้มดัง(self, budget):
        with pytest.raises(ValueError):
            D.round_to_units(pd.Series({"A": 1.0}), budget)

    def test_งบน้อยกว่าจำนวนกองล้มดัง(self):
        with pytest.raises(ValueError, match="น้อยเกิน"):
            D.round_to_units(pd.Series({"A": 0.5, "B": 0.5}), 150)

    def test_หน่วยโดยประมาณหักค่าธรรมเนียมแล้ว(self):
        from portfolio.fees import DIME_FEE_RATE  # noqa: PLC0415

        assert D.estimate_units(3500, 35.0, 100.0) == pytest.approx(100 / 35 * 35 / (1 + DIME_FEE_RATE) / 100)
        assert D.estimate_units(3500, 35.0, None) is None
        assert D.estimate_units(3500, float("nan"), 10.0) is None

    def test_แผนบอกครบว่าซื้อกองไหนกี่บาทกี่หน่วย(self):
        daily = _fake_daily("2005-01-03", "2026-09-30",
                            {"VOO": ("2005-01-03", 0.0004), "XLV": ("2005-01-03", 0.0002), "GLDM": ("2005-01-03", 0.0006)})
        plan = D.build_plan(["VOO", "XLV", "GLDM"], 5000, pd.Period("2026-10", "M"),
                            fetch=lambda t, years: daily, fx_fn=lambda: FxRate(32.5, False), now=datetime(2026, 10, 1, 9, tzinfo=BKK))
        assert plan.total_thb == 5000 and plan.plan_month == "2026-10"
        assert plan.data_through == "2026-09-30"
        assert plan.fx_rate == 32.5 and plan.fx_is_live is False
        assert {ln.ticker for ln in plan.lines} == {"VOO", "XLV", "GLDM"}
        for ln in plan.lines:
            assert ln.amount_thb >= 100 and ln.units and ln.units > 0 and ln.price_usd > 0


class TestSettings:
    """รายชื่อกอง + งบของพอร์ต DAR ตายตัวในโค้ด ไม่อ่านจาก config.json (มติผู้ใช้ 2026-09-30)."""

    def test_ค่าตายตัวใช้ได้จริง(self):
        tickers = list(D.TICKERS)
        assert tickers and len(set(tickers)) == len(tickers)
        assert all(t and t == t.strip().upper() for t in tickers)
        amounts = D.round_to_units(pd.Series(1.0 / len(tickers), index=tickers), D.MONTHLY_BUDGET_THB)
        assert sum(amounts.values()) == D.MONTHLY_BUDGET_THB and min(amounts.values()) >= D.ALLOCATION_UNIT_THB

    def test_งาน_Discord_ไม่ตาม_config_แม้มีส่วน_dar(self, monkeypatch):
        import utils.config as C  # noqa: PLC0415

        fake = {"dar": {"tickers": ["SPY"], "monthly_budget_thb": 9999}, "etf": {"tickers": ["SPY"]},
                "dca": {"monthly_budget_thb": 9999}, "notifications": {}}
        monkeypatch.setattr(C, "load_config", lambda *a, **k: fake)
        seen = {}
        monkeypatch.setattr(D, "build_plan", lambda tickers, budget, month, **kw: seen.update(tickers=list(tickers), budget=budget) or "plan")
        assert J._default_build() == "plan"
        assert seen == {"tickers": list(D.TICKERS), "budget": D.MONTHLY_BUDGET_THB}

    @pytest.mark.parametrize("rel", ["analysis/dar_dca.py", "dashboard/dar_page.py"])
    def test_สูตรและหน้า_DAR_ไม่อ่านไม่เขียน_config_json(self, rel):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "load_config" not in src and "save_config" not in src, rel


# ----------------------------------------------------------------------------- ledger
def _row(**kw):
    base = {"date": "2026-10-02", "plan_month": "2026-10", "ticker": "VOO", "amount_thb": 1000, "fx_rate": 33.0,
            "price_usd": 500.0, "units": 0.06, "source": "plan"}
    base.update(kw)
    return base


class TestLedger:
    @pytest.fixture(autouse=True)
    def _sandbox(self, tmp_path, monkeypatch):
        monkeypatch.setattr(L, "DAR_LEDGER_PATH", tmp_path / "dar.csv")

    def test_เริ่มจากศูนย์_บันทึก_อ่าน_ลบ(self):
        assert L.load_dar_transactions().empty
        ids = L.add_dar_purchases([_row(), _row(ticker="XLV", amount_thb=500, units=0.1)])
        tx = L.load_dar_transactions()
        assert len(tx) == 2 and set(tx["ticker"]) == {"VOO", "XLV"}
        assert L.delete_dar_transaction(ids[0]) is True
        assert L.delete_dar_transaction("nope") is False
        assert len(L.load_dar_transactions()) == 1

    @pytest.mark.parametrize(
        "bad",
        [dict(fx_rate=900), dict(amount_thb=0), dict(amount_thb="abc"), dict(units=float("nan")), dict(date="not a date"),
         dict(source="auto"), dict(ticker=" "), dict(plan_month="2026/10")],
    )
    def test_ข้อมูลผิดรูปไม่ถูกบันทึกเลยทั้งชุด(self, bad):
        with pytest.raises(L.DarLedgerError):
            L.add_dar_purchases([_row(), _row(**bad)])
        assert L.load_dar_transactions().empty, "ชุดที่มีแถวเสียต้องไม่ถูกบันทึกบางส่วน"

    def test_tx_id_ซ้ำถูกปฏิเสธ(self):
        L.add_dar_purchases([_row(tx_id="abc")])
        with pytest.raises(L.DarLedgerError, match="ซ้ำ"):
            L.add_dar_purchases([_row(tx_id="abc")])

    def test_ไฟล์ขาดคอลัมน์ต้องล้มดัง(self):
        L.DAR_LEDGER_PATH.write_text("tx_id,date\n1,2026-01-01\n", encoding="utf-8")
        with pytest.raises(L.DarLedgerError, match="ขาดคอลัมน์"):
            L.load_dar_transactions()


class TestShadowComparison:
    def _prices(self):
        idx = pd.bdate_range("2026-10-01", "2026-12-31")
        return pd.DataFrame({"A": np.linspace(10, 20, len(idx)), "B": np.full(len(idx), 10.0)}, index=idx)

    def _tx(self, rows):
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df["tx_id"] = [str(i) for i in range(len(df))]
        return df

    def test_ตัวอย่างคำนวณมือ(self):
        tx = self._tx([
            {"date": "2026-10-01", "plan_month": "2026-10", "ticker": "A", "amount_thb": 300.0, "fx_rate": 30.0},
            {"date": "2026-10-01", "plan_month": "2026-10", "ticker": "B", "amount_thb": 100.0, "fx_rate": 30.0},
        ])
        out = L.compare_with_equal_shadow(tx, self._prices(), fx_now=30.0)
        # DAR: A 10$/10 = 1 หน่วย → 20$ ; B 3.333$/10 → 3.333$  = 23.333$
        # เงา: 13.333$ แบ่งเท่า 6.667$ → A 0.6667×20 = 13.333$ ; B 6.667$ = 20.0$
        assert out["dar_value_thb"] == pytest.approx(23.3333 * 30, rel=1e-4)
        assert out["shadow_value_thb"] == pytest.approx(20.0 * 30, rel=1e-4)
        assert out["diff_thb"] == pytest.approx(3.3333 * 30, rel=1e-4)
        assert out["invested_thb"] == 400.0 and out["rows_used"] == 2
        assert list(out["path"].columns) == ["dar_usd", "shadow_usd", "invested_usd"]

    def test_แบ่งเท่ากันจริงแล้วสองขาเท่ากันพอดี(self):
        tx = self._tx([
            {"date": "2026-10-01", "plan_month": "2026-10", "ticker": t, "amount_thb": 500.0, "fx_rate": 33.0} for t in "AB"
        ])
        out = L.compare_with_equal_shadow(tx, self._prices(), fx_now=33.0)
        assert out["diff_thb"] == pytest.approx(0.0, abs=1e-9)

    def test_แถวที่ไม่มีราคาถูกตัดทั้งสองขาพร้อมเหตุผล(self):
        tx = self._tx([
            {"date": "2026-10-01", "plan_month": "2026-10", "ticker": "A", "amount_thb": 500.0, "fx_rate": 33.0},
            {"date": "2026-10-01", "plan_month": "2026-10", "ticker": "Z", "amount_thb": 500.0, "fx_rate": 33.0},
        ])
        out = L.compare_with_equal_shadow(tx, self._prices(), fx_now=33.0)
        assert out["rows_used"] == 0 and len(out["rows_excluded"]) == 2
        assert "Z" in out["rows_excluded"][0]["reason"]


# ----------------------------------------------------------------------------- isolation
class TestIsolation:
    def test_ระหว่างรันเทสต์สมุดและสถานะไม่ชี้ไฟล์จริง(self):
        assert Path(L.DAR_LEDGER_PATH).resolve() != (REPO_ROOT / "portfolio" / "data" / "dar_transactions.csv").resolve()
        assert Path(J.DAR_STATE_PATH).resolve() != (REPO_ROOT / ".dar_scheduler_state.json").resolve()

    def test_compose_service_tests_ตั้ง_path_ของ_DAR_ไว้ใต้_tmp(self):
        text = "\n".join(l for l in (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8").splitlines()
                         if not l.lstrip().startswith("#"))
        block = re.search(r"^  tests:\n(.*?)(?=^\S|\Z)", text, re.MULTILINE | re.DOTALL).group(1)
        for key in ("VAULTIS_DAR_LEDGER_PATH", "VAULTIS_DAR_STATE_PATH"):
            m = re.search(rf"{key}:\s*(\S+)", block)
            assert m and m.group(1).startswith("/tmp/"), f"service tests ต้องตั้ง {key} ไว้ใต้ /tmp"

    def test_สมุด_DAR_ถูก_gitignore(self):
        text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "portfolio/data/dar_transactions.csv" in text and ".dar_scheduler_state.json" in text


# ----------------------------------------------------------------------------- Discord job
def _signal(t, z=0.0, reason=None):
    return D.FundSignal(t, 200, 0.1, 0.2, 0.2, 10.0, 50.0, z, 0.2, reason)


def _plan(fx_live=True):
    lines = [
        D.DarPlanLine("XLV", 1600, 0.32, 148.2, "2026-09-30", 0.33, _signal("XLV", 1.2)),
        D.DarPlanLine("GLDM", 200, 0.04, 78.0, "2026-09-30", 0.08, _signal("GLDM", -1.7)),
        D.DarPlanLine("SCHD", 800, 0.17, 27.5, "2026-09-30", 0.89, _signal("SCHD", 0.0, "มีประวัติ 180 เดือน ต้องการ 181")),
    ]
    return D.DarPlan("2026-10", "2026-09-30", 2600.0, 0.0, 32.8, fx_live, lines, {"GLDM": "GLD"})


class TestDiscordJob:
    @pytest.fixture(autouse=True)
    def _state(self, tmp_path, monkeypatch):
        monkeypatch.setattr(J, "DAR_STATE_PATH", tmp_path / "dar_state.json")
        monkeypatch.setattr(J, "_dar_sent_in_process", set())
        self.sent = []

    def _send_ok(self, url, title, desc):
        self.sent.append((title, desc))
        return {"success": True}

    def _run(self, when, **kw):
        kw.setdefault("build", _plan)
        kw.setdefault("compare", lambda: None)
        kw.setdefault("send", self._send_ok)
        return J.run_dar_plan_if_due("https://discord.example/webhook", now=when, **kw)

    def test_ไม่มี_webhook_ไม่ทำอะไร(self):
        assert J.run_dar_plan_if_due("", now=datetime(2026, 10, 1, 9, tzinfo=BKK)) == "no_webhook"

    def test_ก่อนแปดโมงวันที่หนึ่งยังไม่ส่ง(self):
        assert self._run(datetime(2026, 10, 1, 7, 59, tzinfo=BKK)) == "not_yet" and not self.sent

    def test_ติดตั้งครั้งแรกต้นเดือนส่งเลยแล้วไม่ส่งซ้ำ(self):
        assert self._run(datetime(2026, 10, 1, 8, 0, tzinfo=BKK)) == "sent"
        state = json.loads(J.DAR_STATE_PATH.read_text(encoding="utf-8"))
        assert state["dar_plan"]["sent_month"] == "2026-10"
        J._dar_sent_in_process.clear()  # จำลองรีสตาร์ต: ต้องจำได้จากไฟล์
        assert self._run(datetime(2026, 10, 1, 9, tzinfo=BKK)) == "already_sent"
        assert len(self.sent) == 1

    def test_ติดตั้งครั้งแรกกลางเดือนเริ่มเดือนหน้า(self):
        assert self._run(datetime(2026, 10, 15, 9, tzinfo=BKK)) == "seeded" and not self.sent
        assert self._run(datetime(2026, 11, 1, 8, 5, tzinfo=BKK)) == "sent"

    def test_คำนวณไม่ได้นับครั้งแล้วเลิกพร้อมแจ้งหนึ่งครั้ง(self):
        def broken():
            raise PriceDataUnavailableError("yahoo ล่ม")

        when = datetime(2026, 10, 2, 9, tzinfo=BKK)
        assert [self._run(when, build=broken) for _ in range(3)] == ["failed", "failed", "gave_up"]
        assert self._run(when, build=broken) == "gave_up"
        assert len(self.sent) == 1 and "ส่งไม่ได้" in self.sent[0][0] and "yahoo ล่ม" in self.sent[0][1]

    def test_discord_ล้มไม่นับว่าส่งแล้ว(self):
        status = self._run(datetime(2026, 10, 2, 9, tzinfo=BKK), send=lambda u, t, d: {"success": False, "error": "400"})
        assert status == "failed"
        assert "sent_month" not in json.loads(J.DAR_STATE_PATH.read_text(encoding="utf-8"))["dar_plan"]

    def test_ไฟล์สถานะเสียไม่ส่ง(self):
        J.DAR_STATE_PATH.write_text("{broken", encoding="utf-8")
        assert self._run(datetime(2026, 10, 2, 9, tzinfo=BKK)) == "state_unreadable" and not self.sent

    def test_force_ส่งแม้ส่งไปแล้วและจำว่าส่ง(self):
        self._run(datetime(2026, 10, 1, 9, tzinfo=BKK))
        assert J.run_dar_plan_if_due("https://x", now=datetime(2026, 10, 3, 9, tzinfo=BKK), force=True,
                                     build=_plan, compare=lambda: None, send=self._send_ok) == "sent"
        assert len(self.sent) == 2

    def test_ข้อความบอกกองไหนกี่บาทกี่หน่วย(self):
        title, desc = J.format_dar_plan_message(_plan(fx_live=False), {"rows_used": 3, "invested_thb": 5000.0,
                                                                        "dar_value_thb": 5100.0, "diff_thb": 20.0,
                                                                        "diff_pct_of_invested": 0.4})
        assert "ต.ค. 2026" in title
        assert "XLV** — 1,600 บาท" in desc and "0.3300 หน่วย" in desc and "@ $148.20" in desc
        assert desc.index("XLV") < desc.index("SCHD") < desc.index("GLDM"), "เรียงจากเงินมากไปน้อย"
        assert "ค่าสำรอง" in desc, "อัตราแลกเปลี่ยนสำรองต้องถูกเตือน"
        assert "SCHD (มีประวัติ 180 เดือน" in desc
        assert "เทียบพอร์ตเงาแบ่งเท่ากัน" in desc and "+20 บาท" in desc

    def test_เทียบผลพังแล้วแผนยังส่งพร้อมเหตุผล(self):
        def boom():
            raise ValueError("ไม่มีราคาล่าสุด")

        assert self._run(datetime(2026, 10, 2, 9, tzinfo=BKK), compare=boom) == "sent"
        assert "เทียบผลพอร์ต DAR ไม่ได้รอบนี้" in self.sent[0][1]


def test_หน้า_DAR_อยู่ในเมนูจากแหล่งเดียว():
    app = pytest.importorskip("dashboard.app")

    assert "DAR-DCA" in app.NAV_ITEMS
    import dashboard.dar_page as page  # noqa: PLC0415

    assert callable(page.render_dar_page)
