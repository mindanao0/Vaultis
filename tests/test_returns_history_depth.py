# -*- coding: utf-8 -*-
"""FIX_PLAN ข้อ 2.8 — แถว 10Y ต้องไม่เป็น ``N/A`` เพราะผู้เรียกขอข้อมูลมาไม่พอ.

**อาการ** หน้าต่างผลตอบแทนวัดเป็น **แท่ง** (``10Y`` = 2,520 + 1 แท่งอ้างอิง) แต่ผู้เรียกขอ
ข้อมูลเป็น **ปีปฏิทิน** (``years=10``) ซึ่งให้ ~2,512 แท่ง สั้นกว่าที่ต้องใช้ 9 แท่ง
⇒ ``period_return_pct`` คืน ``NaN`` **อย่างถูกต้องตามกฎ C1** แล้วแถว 10Y เป็น ``N/A``
ทั้งแถวทั้งบนจอและใน PDF — ความผิดอยู่ที่ผู้เรียก ไม่ใช่ที่สูตร

วัดจริง 2026-08-08 (5 กองของพอร์ตนี้)::

    years=10  แท่ง=2512  10Y → ทุกกองเป็น N/A
    years=11  แท่ง=2764  10Y → VOO +321.46% · SCHD +233.93% · XLV +160.98%
                                (QQQM/GLDM ยัง N/A เพราะ **ไม่มีประวัติ 10 ปีจริง ๆ**
                                 — นั่นคือ N/A ที่ถูกต้อง)

**เลขซ้ำสองที่คือต้นเหตุที่มันรอดมานาน** ``backend/services/etf_service.py`` แก้ไปแล้ว
ด้วยค่าคงที่ส่วนตัว ``_RETURNS_HISTORY_YEARS = 11`` (AUDIT.md M16) จึงตอบแถว 10Y ได้
แต่หน้าจอกับ PDF ยังขอ 10 ปี ⇒ endpoint ถูก แต่สองทางที่ผู้ใช้เห็นจริงผิด
ตอนนี้ ``analysis/returns.RETURNS_HISTORY_YEARS`` เป็นนิยามเดียว และคิดจาก
``RETURN_WINDOWS`` เอง — วันที่ใครเพิ่มหน้าต่าง ``15Y`` ตัวเลขต้องขยับตาม

**และห้ามแก้ด้วยการขยายเฟรมหลัก** — risk/correlation/backtest อ่านเฟรมนั้น การขยายช่วง
จะเปลี่ยน MaxDD/Volatility/correlation เงียบ ๆ ทั้งที่ไม่มีใครขอให้เปลี่ยน
"""

from __future__ import annotations

import inspect
import re

import numpy as np
import pandas as pd
import pytest

from analysis.returns import (
    MIN_BARS_REQUIRED,
    RETURN_WINDOWS,
    RETURNS_HISTORY_YEARS,
    calculate_period_returns,
    years_needed_for_windows,
)

app = pytest.importorskip("dashboard.app")

from test_dashboard_c2 import FakeSt  # noqa: E402


@pytest.fixture()
def fake_st(monkeypatch) -> FakeSt:
    fake = FakeSt()
    monkeypatch.setattr(app, "st", fake)
    return fake


def _bars(n: int, *, columns=("VOO",), start: float = 100.0, step: float = 0.05):
    index = pd.bdate_range("2005-01-03", periods=n)
    return pd.DataFrame({c: start + np.arange(n) * step for c in columns}, index=index)


class TestRequirementIsDerivedNotGuessed:
    def test_แท่งที่ต้องมีคือหน้าต่างยาวสุดบวกแท่งอ้างอิง(self):
        assert MIN_BARS_REQUIRED == max(RETURN_WINDOWS.values()) + 1 == 2521

    def test_จำนวนปีต้องครอบแท่งที่ต้องมีจริง(self):
        """252 แท่ง/ปีเป็นค่าทฤษฎี ของจริงต่ำกว่า — สูตรต้องเผื่อ ไม่ใช่หารตรง ๆ."""
        years = years_needed_for_windows()
        assert years * 251 >= MIN_BARS_REQUIRED, (
            f"{years} ปี × 251 แท่ง/ปี = {years * 251} < {MIN_BARS_REQUIRED}"
        )

    def test_ต้องมากกว่าจำนวนปีที่ผู้เรียกเคยขอ(self):
        assert RETURNS_HISTORY_YEARS > 10, "ถ้ายังเป็น 10 แถว 10Y จะเป็น N/A เหมือนเดิม"
        assert RETURNS_HISTORY_YEARS == 11

    def test_เพิ่มหน้าต่างที่ยาวขึ้นแล้วตัวเลขต้องขยับตาม(self, monkeypatch):
        """ตรึง **ที่มา** ไม่ใช่ค่า — ค่าคงที่ที่ฮาร์ดโค้ดไว้จะไม่ขยับแล้วกลับไป N/A เงียบ ๆ."""
        from analysis import returns as returns_mod

        monkeypatch.setitem(returns_mod.RETURN_WINDOWS, "20Y", 5040)
        monkeypatch.setattr(returns_mod, "MIN_BARS_REQUIRED", 5041)
        assert returns_mod.years_needed_for_windows() > RETURNS_HISTORY_YEARS

    def test_เผื่อมากขึ้นได้ปีมากขึ้น(self):
        assert years_needed_for_windows(margin_years=3.0) > years_needed_for_windows()

    def test_แท่งต่อปีต้องไม่สูงเกินความจริง(self):
        """ตัวหารที่มองโลกในแง่ดีทำให้คำนวณได้จำนวนปีที่ไม่พอจริง.

        ตลาดสหรัฐมี ~252 วันทำการ/ปีเป็นค่า**ทฤษฎี** ของจริงต่ำกว่าเพราะวันหยุดพิเศษ
        (วัด yfinance 2026-08-08: ขอ 10 ปีได้ 2,512 แท่ง = 251.2 แท่ง/ปี)
        """
        from analysis.returns import _OBSERVED_BARS_PER_YEAR

        assert _OBSERVED_BARS_PER_YEAR <= 252.0, (
            f"{_OBSERVED_BARS_PER_YEAR} แท่ง/ปี สูงกว่าที่ตลาดมีจริง — จะได้จำนวนปีที่ไม่พอ"
        )

    def test_margin_มีผลจริงเมื่อความต้องการตกพอดีขอบปี(self, monkeypatch):
        """ตรึง **หน้าที่** ของ margin ไม่ใช่ค่าที่มันให้ในวันนี้.

        ที่ชุดหน้าต่างปัจจุบัน margin ไม่เปลี่ยนคำตอบ (10.04 ปี → 11 ทั้งมีและไม่มี) —
        เทสต์ที่ดูแต่ค่าปลายทางจึงจับการถอด margin ออกไม่ได้ ต้องจัดฉากให้ตกขอบพอดี
        """
        from analysis import returns as returns_mod

        exact = int(returns_mod._OBSERVED_BARS_PER_YEAR * 10)  # ต้องการพอดี 10 ปีเป๊ะ
        monkeypatch.setattr(returns_mod, "MIN_BARS_REQUIRED", exact)
        assert returns_mod.years_needed_for_windows(margin_years=0.0) == 10
        assert returns_mod.years_needed_for_windows() == 11, (
            "ไม่มี margin = ขอข้อมูลมาพอดีเป๊ะ ปีที่วันหยุดเยอะกว่าปกติจะขาดทันที"
        )


class TestWindowIsComputableAtTheStatedDepth:
    def test_แท่งพอดีเกณฑ์คำนวณได้(self):
        table = calculate_period_returns(_bars(MIN_BARS_REQUIRED))
        assert not pd.isna(table.loc["10Y", "VOO"])

    def test_ขาดไปแท่งเดียวยังคำนวณไม่ได้(self):
        """ขอบที่ทำให้บั๊กนี้รอดมานาน — สั้นกว่าเกณฑ์แค่แท่งเดียวก็ N/A ทั้งแถว."""
        table = calculate_period_returns(_bars(MIN_BARS_REQUIRED - 1))
        assert pd.isna(table.loc["10Y", "VOO"])

    def test_กองที่ไม่มีประวัติจริงยังต้องเป็น_NaN(self):
        """N/A ที่ถูกต้องต้องยังอยู่ — กองที่เพิ่งลิสต์ไม่ควรได้ตัวเลข 10Y ปลอม ๆ."""
        frame = _bars(MIN_BARS_REQUIRED, columns=("VOO", "NEW"))
        frame.iloc[: MIN_BARS_REQUIRED - 300, frame.columns.get_loc("NEW")] = np.nan
        table = calculate_period_returns(frame)
        assert not pd.isna(table.loc["10Y", "VOO"])
        assert pd.isna(table.loc["10Y", "NEW"])


class TestSingleDefinitionAcrossCallers:
    def test_backend_อ่านนิยามเดียวไม่มีเลขส่วนตัว(self):
        from backend.services import etf_service

        assert etf_service._RETURNS_HISTORY_YEARS == RETURNS_HISTORY_YEARS
        src = inspect.getsource(etf_service)
        assert not re.search(r"_RETURNS_HISTORY_YEARS\s*=\s*\d+", src), (
            "backend กลับไปมีเลขส่วนตัวแล้ว — เลขซ้ำสองที่ไม่พัง มันแค่เพี้ยนกัน"
        )

    def test_ตัวดึงของหน้าจอขอตามนิยามเดียว(self, monkeypatch):
        seen: dict[str, object] = {}
        monkeypatch.setattr(
            app, "cached_prices", lambda tickers, years=10: seen.update(years=years) or _bars(10)
        )
        app.cached_returns_prices(["VOO"])
        assert seen["years"] == RETURNS_HISTORY_YEARS

    def test_pdf_ขอตามนิยามเดียว(self):
        from utils import pdf_export

        src = inspect.getsource(pdf_export)
        assert "years=RETURNS_HISTORY_YEARS" in src
        assert "years=10)" not in src, "PDF ยังมีการขอ 10 ปีค้างอยู่"


class TestSharedFrameStaysAtTenYears:
    """ขยายเฟรมหลักคือการเปลี่ยนตัวเลขความเสี่ยงเงียบ ๆ — ห้ามเด็ดขาด."""

    def test_หน้าจอยังโหลดเฟรมหลัก_10_ปี(self):
        src = inspect.getsource(app)
        assert "prices = cached_prices(tickers, years=10)" in src, (
            "เฟรมหลักถูกขยาย ⇒ risk/correlation/backtest เปลี่ยนตัวเลขโดยไม่มีใครขอ"
        )

    def test_pdf_คิดความเสี่ยงจาก_10_ปีล่าสุด(self):
        from utils import pdf_export

        src = inspect.getsource(pdf_export)
        assert "_last_n_years(prices, 10)" in src

    def test_ตัวหั่นปีคิดจากวันที่จริงไม่ใช่จำนวนแถว(self):
        from utils.pdf_export import _last_n_years

        frame = _bars(3000)
        sliced = _last_n_years(frame, 10)
        span_days = (sliced.index[-1] - sliced.index[0]).days
        assert 3640 <= span_days <= 3660, f"ช่วงที่หั่นได้ {span_days} วัน"
        assert len(sliced) < len(frame)

    def test_ตัวหั่นปีไม่พังกับเฟรมว่างหรือดัชนีที่ไม่ใช่วันที่(self):
        from utils.pdf_export import _last_n_years

        empty = pd.DataFrame()
        assert _last_n_years(empty, 10) is empty
        plain = pd.DataFrame({"VOO": [1.0, 2.0]})
        assert _last_n_years(plain, 10) is plain


class TestOverviewCardUsesGivenFrame:
    def test_ตัวเรนเดอร์ต้องไม่ยิงเน็ตเอง(self, fake_st, monkeypatch):
        """ผลลัพธ์ต้องขึ้นกับอาร์กิวเมนต์ที่รับมา ไม่ใช่ข้อมูลที่มันไปดึงเองข้างหลัง."""

        def _explode(*_a, **_k):
            raise AssertionError("_render_overview_metrics ไปดึงราคาเองแล้ว")

        monkeypatch.setattr(app, "cached_returns_prices", _explode)
        monkeypatch.setattr(app, "cached_prices", _explode)
        monkeypatch.setattr(app, "fetch_macro_data", lambda: pd.DataFrame())
        frame = _bars(300, columns=("VOO", "SCHD"))
        app._render_overview_metrics(frame, ["VOO", "SCHD"])
        assert fake_st.all_text()

    def test_เฟรมที่ส่งเข้ามาคือเฟรมที่ถูกใช้คำนวณจริง(self, fake_st, monkeypatch):
        """จัดฉากให้สองเฟรมให้ผู้ชนะ 1Y **คนละตัว** แล้วดูว่าการ์ดพูดถึงตัวไหน.

        เทียบผลของ ``calculate_period_returns`` เองไม่ได้พิสูจน์อะไรเลย — มันเป็นจริง
        ไม่ว่าตัวเรนเดอร์จะใช้เฟรมไหน (จับได้ตอนพิสูจน์ด้วย mutation)
        """
        monkeypatch.setattr(app, "fetch_macro_data", lambda: pd.DataFrame())
        n = 400
        index = pd.bdate_range("2020-01-01", periods=n)
        # เฟรมหลัก: A ชนะ · เฟรมที่ส่งเข้ามา: B ชนะ
        main = pd.DataFrame(
            {"A": 100.0 + np.arange(n) * 0.20, "B": 100.0 + np.arange(n) * 0.01}, index=index
        )
        supplied = pd.DataFrame(
            {"A": 100.0 + np.arange(n) * 0.01, "B": 100.0 + np.arange(n) * 0.20}, index=index
        )
        assert calculate_period_returns(main).loc["1Y"].idxmax() == "A"
        assert calculate_period_returns(supplied).loc["1Y"].idxmax() == "B"

        app._render_overview_metrics(main, ["A", "B"], returns_prices=supplied)
        text = fake_st.all_text()
        best = text.split("Best ETF (1Y)")[1].split("metric-value")[1][:40]
        assert ">B<" in best, f"การ์ด Best ใช้เฟรมหลักแทนเฟรมที่ส่งเข้ามา: {best!r}"

    def test_ดึงประวัติยาวไม่สำเร็จต้องบอกผู้ใช้ไม่ใช่เงียบ(self, fake_st, monkeypatch):
        monkeypatch.setattr(app, "fetch_macro_data", lambda: pd.DataFrame())
        app._render_overview_metrics(
            _bars(300), ["VOO"], returns_history_error="yfinance rate limit"
        )
        text = fake_st.all_text()
        assert "yfinance rate limit" in text
        assert "ไม่ใช่เพราะไม่มีผลตอบแทน" in text, (
            "N/A จาก 'ข้อมูลไม่พอ' ต้องแยกจาก N/A ที่แปลว่า 'ไม่มีผลตอบแทน' (C1)"
        )


class TestBasketCardReportsItsRealWindow:
    def test_เลิกติดป้าย_10Y_ตายตัว(self, fake_st, monkeypatch):
        """การ์ดนี้วัดจากช่วงที่ทุกกองมีข้อมูลพร้อมกัน — ไม่ใช่ 10 ปี."""
        monkeypatch.setattr(app, "fetch_macro_data", lambda: pd.DataFrame())
        frame = _bars(1000, columns=("VOO", "LATE"))
        frame.iloc[:700, frame.columns.get_loc("LATE")] = np.nan
        app._render_overview_metrics(frame, ["VOO", "LATE"])
        text = fake_st.all_text()
        assert "10Y blended performance" not in text, "ป้ายที่ไม่ตรงข้อมูลคือการกุข้อมูล"
        assert "นับจากวันที่ทุกกองมีข้อมูลครบ" in text

    def test_ป้ายบอกจำนวนปีที่สั้นลงตามกองที่ลิสต์ทีหลัง(self, fake_st, monkeypatch):
        monkeypatch.setattr(app, "fetch_macro_data", lambda: pd.DataFrame())
        frame = _bars(1000, columns=("VOO", "LATE"))
        frame.iloc[:700, frame.columns.get_loc("LATE")] = np.nan
        app._render_overview_metrics(frame, ["VOO", "LATE"])
        text = fake_st.all_text()
        # คิดจากเฟรมเอง ไม่ฮาร์ดโค้ด — ป้ายต้องบอกช่วง **ร่วม** ไม่ใช่ช่วงของทั้งเฟรม
        common = frame.ffill().dropna()
        expected = (common.index[-1] - common.index[0]).days / 365.25
        whole = (frame.index[-1] - frame.index[0]).days / 365.25
        assert expected < whole / 2, "ฉากต้องทำให้ช่วงร่วมสั้นกว่าทั้งเฟรมชัด ๆ"
        assert f"{expected:.1f} ปี" in text, text[:400]
        assert f"{whole:.1f} ปี" not in text, "ป้ายกำลังบอกช่วงของทั้งเฟรม ไม่ใช่ช่วงร่วม"

    def test_ไม่มีช่วงร่วมต้องบอกออกมาตรงๆ(self, fake_st, monkeypatch):
        monkeypatch.setattr(app, "fetch_macro_data", lambda: pd.DataFrame())
        frame = _bars(400, columns=("A", "B"))
        frame["B"] = np.nan  # กองที่ไม่มีราคาเลยสักแท่ง → ไม่มีช่วงร่วมจริง ๆ
        app._render_overview_metrics(frame, ["A", "B"])
        assert "ยังไม่มีช่วงที่ทุกกองมีข้อมูลครบพร้อมกัน" in fake_st.all_text()
