# -*- coding: utf-8 -*-
"""FIX_PLAN ข้อ 2.7 — ตาราง Risk เทียบ ETF คนละช่วงเวลาโดยไม่บอก.

ทุกสูตรใน ``analysis/risk.py`` ข้าม ``NaN`` รายคอลัมน์ (``std``/``mean``/``cummax``
ของ pandas) ⇒ แต่ละ ETF ถูกวัดด้วย **ช่วงเวลาของตัวเอง** แล้ววางเรียงกันในตารางเดียว
เหมือนเทียบกันได้ ซึ่งไม่จริง — กองที่ลิสต์ทีหลังยังไม่เคยเจอวิกฤตที่กองเก่าเจอมาแล้ว

วัดจริง 2026-08-08 (ราคา 10 ปี, ช่วงร่วม n=1,461 แท่ง เริ่ม 2020-10-13 ตาม QQQM)::

              MaxDD ตามที่โชว์   ช่วงร่วม        Δ
        GLDM        −26.27%      −26.27%    +0.00
        QQQM        −35.04%      −35.04%    +0.00
        SCHD        −33.37%      −16.84%   −16.52
        XLV         −28.40%      −17.11%   −11.30
        VOO         −33.99%      −24.52%    −9.47

สองอย่างที่พลิกเพราะฐานเวลา ไม่ใช่เพราะความเสี่ยง:

- บนจอ QQQM ดู "แย่กว่า VOO นิดเดียว" (ห่าง **1.05 จุด**) ทั้งที่บนช่วงเดียวกัน
  ห่างกัน **10.52 จุด**
- "ตัวที่ drawdown ตื้นสุด" เปลี่ยนจาก **GLDM** เป็น **SCHD**

หน้า Return Analysis ที่คอลัมน์ติดกันมี caption เตือนเรื่องนี้อยู่แล้ว ตาราง Risk ไม่มี
"""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import pytest

from analysis.risk import WINDOW_COLUMNS, calculate_risk_metrics

app = pytest.importorskip("dashboard.app")

from test_dashboard_c2 import FakeSt  # noqa: E402


@pytest.fixture()
def fake_st(monkeypatch) -> FakeSt:
    fake = FakeSt()
    monkeypatch.setattr(app, "st", fake)
    return fake


def _staggered() -> pd.DataFrame:
    """OLD มีประวัติเต็ม (และเคยครัชหนัก) · NEW เพิ่งลิสต์ครึ่งทาง (ยังไม่เคยเจอครัช)."""
    n = 600
    index = pd.bdate_range("2018-01-01", periods=n)
    old = np.concatenate(
        [
            np.linspace(100.0, 120.0, 100),
            np.linspace(120.0, 60.0, 60),   # ครัช −50% ก่อน NEW จะเกิด
            np.linspace(60.0, 150.0, n - 160),
        ]
    )
    new = np.concatenate([np.full(300, np.nan), np.linspace(100.0, 140.0, n - 300)])
    return pd.DataFrame({"OLD": old, "NEW": new}, index=index)


class TestWindowColumnsAlwaysTravel:
    def test_ตารางพกช่วงข้อมูลของแต่ละกองมาด้วยเสมอ(self):
        table = calculate_risk_metrics(_staggered())
        for column in WINDOW_COLUMNS:
            assert column in table.columns, f"ขาดคอลัมน์ {column} — ตารางกลับไปเงียบเรื่องฐานเวลา"

    def test_ช่วงข้อมูลตรงกับแท่งจริงของกองนั้น(self):
        frame = _staggered()
        table = calculate_risk_metrics(frame)
        assert table.loc["OLD", "Days"] == int(frame["OLD"].notna().sum())
        assert table.loc["NEW", "Days"] == int(frame["NEW"].notna().sum())
        assert table.loc["NEW", "Days"] < table.loc["OLD", "Days"]

    def test_วันเริ่มของกองที่ลิสต์ทีหลังต้องช้ากว่า(self):
        table = calculate_risk_metrics(_staggered())
        assert table.loc["NEW", "Data Start"] > table.loc["OLD", "Data Start"]
        assert table.loc["NEW", "Data End"] == table.loc["OLD", "Data End"]

    def test_ช่วงข้อมูลเป็นสตริงส่งออก_JSON_ได้(self):
        """``/api/etf/risk`` ส่งตารางนี้ออกไปตรง ๆ — Timestamp จะพา endpoint ลง 500."""
        table = calculate_risk_metrics(_staggered())
        for column in ("Data Start", "Data End"):
            assert table[column].map(lambda v: isinstance(v, str)).all()
        assert table["Days"].map(lambda v: isinstance(v, (int, np.integer))).all()


class TestCommonWindowMakesThemComparable:
    def test_กองที่ลิสต์ทีหลังไม่เปลี่ยนแต่กองเก่าเปลี่ยน(self):
        """หัวใจของข้อนี้ — ช่วงร่วมตัดวิกฤตที่กองใหม่ไม่เคยเจอออกจากกองเก่า."""
        frame = _staggered()
        full = calculate_risk_metrics(frame)
        common = calculate_risk_metrics(frame, common_window=True)
        assert common.loc["NEW", "Max Drawdown"] == pytest.approx(full.loc["NEW", "Max Drawdown"])
        assert common.loc["OLD", "Max Drawdown"] > full.loc["OLD", "Max Drawdown"], (
            "ช่วงร่วมต้องตัดครัชเก่าออกจาก OLD ⇒ drawdown ตื้นขึ้น"
        )

    def test_อันดับความเสี่ยงพลิกได้เพราะฐานเวลา(self):
        frame = _staggered()
        full = calculate_risk_metrics(frame)
        common = calculate_risk_metrics(frame, common_window=True)
        assert full["Max Drawdown"].idxmax() == "NEW"
        assert common["Max Drawdown"].idxmax() == "OLD", (
            "ฉากต้องแสดงว่า 'ตัวที่ drawdown ตื้นสุด' เปลี่ยนตัวเมื่อเทียบฐานเดียวกัน"
        )

    def test_ทุกกองใช้จำนวนแท่งเท่ากันในโหมดช่วงร่วม(self):
        common = calculate_risk_metrics(_staggered(), common_window=True)
        assert common["Days"].nunique() == 1
        assert common["Data Start"].nunique() == 1

    def test_ค่าเริ่มต้นยังเป็นช่วงเต็มของแต่ละกอง(self):
        """ห้ามเปลี่ยนตัวเลขที่มีอยู่โดยไม่มีใครขอ — โหมดใหม่ต้อง opt-in."""
        frame = _staggered()
        assert calculate_risk_metrics(frame).loc["OLD", "Max Drawdown"] == pytest.approx(
            calculate_risk_metrics(frame, common_window=False).loc["OLD", "Max Drawdown"]
        )
        assert calculate_risk_metrics(frame).loc["OLD", "Max Drawdown"] < -0.4

    def test_ไม่มีช่วงร่วมเลยต้องดังไม่ใช่คืนตารางว่าง(self):
        """ตารางว่างอ่านเหมือน "ความเสี่ยงเป็นศูนย์" — เทียบไม่ได้ต้องพูดออกมา."""
        n = 200
        index = pd.bdate_range("2020-01-01", periods=n)
        frame = pd.DataFrame(
            {"A": np.linspace(100.0, 120.0, n), "B": np.linspace(100.0, 120.0, n)}, index=index
        )
        frame.iloc[100:, frame.columns.get_loc("A")] = np.nan
        frame.iloc[:100, frame.columns.get_loc("B")] = np.nan
        with pytest.raises(ValueError, match="ช่วงร่วม"):
            calculate_risk_metrics(frame, common_window=True)

    def test_แคชแยกกันสองโหมด(self):
        """``cache_data_1h`` คีย์รวม kwargs — สองโหมดต้องไม่ใช้ผลของกันและกัน."""
        frame = _staggered()
        assert calculate_risk_metrics(frame, common_window=True).loc[
            "OLD", "Max Drawdown"
        ] != pytest.approx(calculate_risk_metrics(frame, common_window=False).loc["OLD", "Max Drawdown"])


class TestScreenNeverHidesTheTimeBase:
    @staticmethod
    def _render(fake_st, monkeypatch, *, toggle: bool):
        monkeypatch.setattr(app, "fetch_macro_data", lambda: pd.DataFrame())
        fake_st.toggle = lambda *a, **k: fake_st.calls.append(("toggle", a, k)) or toggle
        return _staggered()

    def test_มีสวิตช์ให้เทียบบนช่วงร่วม(self, fake_st):
        src = inspect.getsource(app)
        assert 'key="risk_common_window"' in src
        assert "common_window=common_only" in src

    def test_โหมดปกติต้องเตือนว่าคนละช่วงเวลา(self):
        src = inspect.getsource(app)
        assert "ช่วงเวลาของตัวเอง" in src
        assert "Data Start" in src, "คำเตือนต้องชี้ไปที่คอลัมน์ที่ผู้ใช้ตรวจเองได้"

    def test_ตารางบนจอต้องจัดรูปแบบทีละคอลัมน์(self):
        """เดิม ``.style.format("{:.4f}")`` ทั้งตาราง — คอลัมน์วันที่จะพังทันทีที่เพิ่มเข้ามา."""
        src = inspect.getsource(app)
        assert 'risk_df.style.format(\n' in src or '"Max Drawdown": "{:.4f}"' in src
        assert 'risk_df.style.format("{:.4f}")' not in src


class TestOtherCallersStillWork:
    def test_pdf_อ่านคอลัมน์ตัวเลขด้วยชื่อ(self):
        """คอลัมน์ใหม่ต้องไม่ทำให้กระดาษพัง — PDF หยิบด้วยชื่อ ไม่ใช่ตำแหน่ง."""
        from utils import pdf_export

        src = inspect.getsource(pdf_export)
        for column in ("Volatility", "Sharpe Ratio", "Max Drawdown"):
            assert f'"{column}"' in src

    def test_ตัวเลขความเสี่ยงเดิมไม่เปลี่ยน(self):
        """คอลัมน์ที่เพิ่มมาต้องเป็นข้อมูลเพิ่ม ไม่ใช่การเปลี่ยนคำตอบ."""
        frame = _staggered()
        table = calculate_risk_metrics(frame)
        from analysis.risk import calculate_max_drawdown, calculate_volatility

        assert table["Max Drawdown"].equals(calculate_max_drawdown(frame))
        assert table["Volatility"].equals(calculate_volatility(frame))
