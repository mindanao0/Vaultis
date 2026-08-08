# -*- coding: utf-8 -*-
"""FIX_PLAN เฟส 4③ — ทะลุกองถึงรายหุ้น + correlation แบบเลื่อนหน้าต่าง.

**ข้อมูลนี้ระบบไม่เคยมีเลย และมันเปลี่ยนภาพพอร์ตทั้งใบ** หัวข้อ "การกระจายจริง &
ความทับซ้อน" มีแค่ correlation matrix กับข้อความบรรยาย ไม่มีตัวเลขความทับซ้อนสักตัว
ทั้งที่ yfinance ให้ฟรีผ่าน ``funds_data``

วัดจริง 2026-08-08 บนน้ำหนักเป้าหมายของพอร์ตนี้::

    ทะลุถึงรายหุ้น (ขอบล่าง — top-10 ของแต่ละกอง):
      NVDA 4.23% [QQQM,VOO]   AAPL 3.80% [QQQM,VOO]   MSFT 2.64% [QQQM,VOO]
      UNH  1.72% [SCHD,XLV]   MRK  1.64% [SCHD,XLV]
    ทะลุถึงเซกเตอร์: technology 28.28% · healthcare 19.16%

ผู้ใช้คิดว่าถือ healthcare 10% (XLV) จริงคือ **19.16%** และคิดว่ากระจาย 5 กอง ทั้งที่
**NVDA ตัวเดียวกิน 4.23%** ของพอร์ตทั้งใบ (และนี่คือขอบล่าง)

**correlation ค่าเดียวซ่อนกรณีเลวร้าย** วัดจริงเทียบ VOO (rolling 252 วัน)::

           ค่าเดียวที่โชว์   ต่ำสุด   เฉลี่ย   สูงสุด  ปัจจุบัน
    QQQM             0.94    +0.81   +0.94   +0.98    +0.93
    SCHD             0.86    +0.29   +0.83   +0.98    +0.29
    XLV              0.76    +0.22   +0.73   +0.95    +0.23
    GLDM             0.11    −0.34   +0.10   +0.32    +0.31

เกณฑ์เตือน ``>= 0.85`` ที่ดูค่าเดียวจับ XLV ไม่ได้ ทั้งที่มันเคยขึ้นถึง **0.95** —
ตัวที่ควรกระจายความเสี่ยงหยุดกระจายพอดีตอนที่ต้องการมันที่สุด

**invariant** ทุกตัวเลขที่นี่เป็นสถิติเชิงพรรณนา — ห้ามไหลเข้าเลขคะแนนหรือการจัดสรร DCA
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analysis.correlation import ROLLING_WINDOW_DAYS, rolling_correlation_summary
from portfolio import lookthrough
from portfolio.lookthrough import look_through, overlap_pairs


def _holdings(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    frame = pd.DataFrame(
        [{"Name": name, "Holding Percent": pct} for _sym, name, pct in rows],
        index=[sym for sym, _n, _p in rows],
    )
    frame.index.name = "Symbol"
    return frame


_FUNDS = {
    "VOO": (
        _holdings([("NVDA", "NVIDIA Corp", 0.075), ("AAPL", "Apple Inc", 0.066)]),
        {"technology": 0.35, "healthcare": 0.09},
    ),
    "QQQM": (
        _holdings([("NVDA", "NVIDIA Corp", 0.09), ("AAPL", "Apple Inc", 0.08)]),
        {"technology": 0.60},
    ),
    "XLV": (
        _holdings([("UNH", "UnitedHealth", 0.08)]),
        {"healthcare": 1.00},
    ),
}


@pytest.fixture()
def stub_funds(monkeypatch):
    def _fake(symbol):
        if symbol in _FUNDS:
            return (*_FUNDS[symbol], "")
        return None, None, "ผู้ให้ข้อมูลไม่มี funds_data ของกองนี้"

    monkeypatch.setattr(lookthrough, "_fund_data", _fake)


class TestLookThroughMath:
    def test_น้ำหนักหุ้นคือผลรวมข้ามกอง(self, stub_funds):
        result = look_through({"VOO": 0.5, "QQQM": 0.5})
        nvda = next(r for r in result["holdings"] if r["symbol"] == "NVDA")
        assert nvda["weight_pct"] == pytest.approx((0.5 * 0.075 + 0.5 * 0.09) * 100)
        assert nvda["via"] == ["QQQM", "VOO"]

    def test_เรียงจากมากไปน้อย(self, stub_funds):
        result = look_through({"VOO": 0.5, "QQQM": 0.5})
        weights = [r["weight_pct"] for r in result["holdings"]]
        assert weights == sorted(weights, reverse=True)

    def test_เซกเตอร์ก็รวมข้ามกอง(self, stub_funds):
        result = look_through({"VOO": 0.5, "XLV": 0.5})
        assert result["sectors"]["healthcare"] == pytest.approx((0.5 * 0.09 + 0.5 * 1.0) * 100)

    def test_น้ำหนักดิบถูก_normalize_ให้เอง(self, stub_funds):
        raw = look_through({"VOO": 5000.0, "QQQM": 5000.0})
        frac = look_through({"VOO": 0.5, "QQQM": 0.5})
        assert raw["holdings"][0]["weight_pct"] == pytest.approx(frac["holdings"][0]["weight_pct"])

    def test_น้ำหนักที่ใช้ไม่ได้ถูกตัดออกก่อน_normalize(self, stub_funds):
        result = look_through({"VOO": 1.0, "QQQM": 0.0, "BAD": -1.0})
        nvda = next(r for r in result["holdings"] if r["symbol"] == "NVDA")
        assert nvda["weight_pct"] == pytest.approx(7.5), "VOO ต้องกลายเป็น 100% ของที่เหลือ"

    def test_ไม่มีน้ำหนักที่ใช้ได้เลยต้องดัง(self):
        with pytest.raises(ValueError):
            look_through({})
        with pytest.raises(ValueError):
            look_through({"VOO": 0.0})


class TestUnavailableFundsAreReported:
    def test_กองที่ดึงไม่ได้ต้องถูกนับไม่ใช่หายจากตัวหาร(self, stub_funds):
        """หายจากตัวหารเงียบ ๆ = สัดส่วนกองที่เหลือพองขึ้น (บั๊กเดียวกับ rebalance)."""
        result = look_through({"VOO": 0.5, "GLDM": 0.5})
        assert "GLDM" in result["unavailable"]
        assert result["covered_weight"] == pytest.approx(0.5)
        nvda = next(r for r in result["holdings"] if r["symbol"] == "NVDA")
        assert nvda["weight_pct"] == pytest.approx(0.5 * 7.5), (
            "น้ำหนัก NVDA ต้องคิดจากพอร์ตทั้งใบ ไม่ใช่จากเฉพาะกองที่ดึงได้"
        )

    def test_บอกว่าคิดจากพอร์ตกี่เปอร์เซ็นต์(self, stub_funds):
        result = look_through({"VOO": 0.5, "GLDM": 0.5})
        assert "50.0%" in result["notes"]
        assert "GLDM" in result["notes"]

    def test_ครบทุกกองไม่ต้องเตือนเรื่องความครอบคลุม(self, stub_funds):
        result = look_through({"VOO": 1.0})
        assert result["covered_weight"] == pytest.approx(1.0)
        assert "คิดจากพอร์ตเพียง" not in result["notes"]

    def test_ต้องบอกเสมอว่าเป็นขอบล่าง(self, stub_funds):
        """yfinance ให้แค่ top-10 — นำเสนอเป็นสัดส่วนเต็มคือการโกหก."""
        assert "ขอบล่าง" in look_through({"VOO": 1.0})["notes"]


class TestOverlapIsTheHeadline:
    def test_จับเฉพาะหุ้นที่ถือผ่านหลายกอง(self, stub_funds):
        result = look_through({"VOO": 0.4, "QQQM": 0.3, "XLV": 0.3})
        symbols = {r["symbol"] for r in overlap_pairs(result)}
        assert "NVDA" in symbols and "AAPL" in symbols
        assert "UNH" not in symbols, "UNH มาจากกองเดียวในฉากนี้ ไม่ใช่ความทับซ้อน"

    def test_ตัดตัวที่เล็กเกินกว่าจะสำคัญ(self, stub_funds):
        result = look_through({"VOO": 0.5, "QQQM": 0.5})
        assert overlap_pairs(result, min_weight_pct=99.0) == []


class TestRollingCorrelation:
    @staticmethod
    def _frame() -> pd.DataFrame:
        """SAME วิ่งตาม BASE เกือบตลอด · FLIP กระจายความเสี่ยงตอนต้นแต่ไปเกาะตอนท้าย."""
        n = 900
        rng = np.random.default_rng(3)
        base_returns = rng.normal(0.0004, 0.01, n)
        same = base_returns + rng.normal(0.0, 0.001, n)
        flip = np.concatenate(
            [rng.normal(0.0004, 0.01, n // 2), base_returns[n // 2 :] + rng.normal(0, 0.0005, n - n // 2)]
        )
        index = pd.bdate_range("2020-01-01", periods=n)
        return pd.DataFrame(
            {
                "BASE": 100 * np.cumprod(1 + base_returns),
                "SAME": 100 * np.cumprod(1 + same),
                "FLIP": 100 * np.cumprod(1 + flip),
            },
            index=index,
        )

    def test_คืนสี่ค่าต่อกองไม่ใช่ค่าเดียว(self):
        summary = rolling_correlation_summary(self._frame(), "BASE")
        assert set(summary.columns) == {"min", "mean", "max", "current", "n_windows"}
        assert "BASE" not in summary.index

    def test_ค่าสูงสุดจับตัวที่เคยเกาะได้ทั้งที่ค่าเฉลี่ยต่ำ(self):
        """หัวใจของข้อนี้ — ตัวที่ 'เคยหยุดกระจายความเสี่ยง' ต้องมองเห็น."""
        summary = rolling_correlation_summary(self._frame(), "BASE")
        flip = summary.loc["FLIP"]
        assert flip["max"] > 0.85, "ช่วงที่มันเกาะ BASE ต้องปรากฏใน max"
        assert flip["mean"] < flip["max"] - 0.2, "ค่าเฉลี่ยกลบช่วงที่เกาะไปเกือบหมด"

    def test_ค่าอยู่ในกรอบและเรียงถูก(self):
        summary = rolling_correlation_summary(self._frame(), "BASE")
        for ticker in summary.index:
            row = summary.loc[ticker]
            assert -1.0 <= row["min"] <= row["mean"] <= row["max"] <= 1.0
            assert row["min"] <= row["current"] <= row["max"]

    def test_ข้อมูลสั้นกว่าหน้าต่างต้องดัง(self):
        short = self._frame().iloc[: ROLLING_WINDOW_DAYS - 5]
        with pytest.raises(ValueError, match="สั้นกว่าหน้าต่าง"):
            rolling_correlation_summary(short, "BASE")

    def test_ไม่มี_base_ต้องดัง(self):
        with pytest.raises(ValueError, match="NOPE"):
            rolling_correlation_summary(self._frame(), "NOPE")

    def test_หน้าต่างสั้นลงได้จำนวนหน้าต่างมากขึ้น(self):
        frame = self._frame()
        wide = rolling_correlation_summary(frame, "BASE")
        narrow = rolling_correlation_summary(frame, "BASE", window=60)
        assert narrow.loc["SAME", "n_windows"] > wide.loc["SAME", "n_windows"]


class TestDescriptiveOnlyInvariant:
    def test_ไม่มีใครเอาไปเข้าเลขคะแนนหรือการจัดสรร(self):
        """invariant เดียวกับ trend_channel/news — ตรวจด้วยการอ่านซอร์สจริง."""
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]
        targets = [
            root / "analysis" / "financial_model.py",
            root / "portfolio" / "targets.py",
            root / "portfolio" / "dca.py",
            root / "technical" / "signal_rules.py",
        ]
        for path in targets:
            src = path.read_text(encoding="utf-8")
            for banned in ("lookthrough", "rolling_correlation_summary"):
                assert banned not in src, f"{path.name} นำเข้า {banned} — ผิด invariant"
