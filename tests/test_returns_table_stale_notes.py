# -*- coding: utf-8 -*-
"""G7 (AUDIT_ROUND2_2026-08-07) — หน้าจอต้องบอกว่า ticker ไหน "ดึงไม่ได้/ของวันเก่า".

ตาราง Returns เป็นตัวเลขล้วน ไม่มีช่องให้ติดธง ``stale``/``data_ok`` เหมือน snapshot
เมื่อ ``calculate_period_returns`` เลิก ``ffill`` แล้ว ตัวเลขของ ETF ที่ผู้ให้ข้อมูล
หยุดส่งแท่งจะเป็นผลตอบแทนของ**แท่งจริง**ของมันเอง (ไม่ใช่ 0.00% ที่ถูกกุ) — แต่มันคือ
ผลตอบแทน "ถึงวันที่หยุดส่ง" ที่วางปนกับกองอื่นซึ่งเป็นของวันล่าสุด จึงต้องมีคำเตือนกำกับ
และกองที่ถูกตัดออกจากการจัดอันดับ Best/Worst ต้องถูกพูดถึง ไม่ใช่หายเงียบ ๆ (กฎข้อ 2)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

app = pytest.importorskip("dashboard.app")

from test_dashboard_c2 import FakeSt  # noqa: E402

ROWS = 300


def _frame() -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=ROWS, freq="B")
    steps = np.arange(ROWS, dtype=float)
    return pd.DataFrame(
        {
            "VOO": 400.0 + steps * 0.5 + np.sin(steps / 3.0) * 6.0,
            "GLDM": 55.0 + steps * 0.02 + np.sin(steps / 4.0) * 3.0,
        },
        index=idx,
    )


class TestStalePriceNotes:
    def test_ไม่มีคำเตือนเมื่อทุกกองมีแท่งของวันล่าสุด(self):
        assert app._stale_price_notes(_frame()) == []

    def test_กองที่หยุดส่งแท่งต้องมีคำเตือนพร้อมวันที่ของแท่งจริง(self):
        frame = _frame()
        frame.iloc[-40:, frame.columns.get_loc("GLDM")] = np.nan
        real_last = frame["GLDM"].dropna().index[-1]

        notes = app._stale_price_notes(frame)

        assert len(notes) == 1, f"ควรเตือนเฉพาะ GLDM: {notes}"
        assert "GLDM" in notes[0]
        assert pd.Timestamp(real_last).strftime("%d/%m/%Y") in notes[0]
        assert pd.Timestamp(frame.index[-1]).strftime("%d/%m/%Y") in notes[0]

    def test_กองที่ไม่มีแท่งเลยต้องบอกว่าดึงไม่ได้_ไม่ใช่_ศูนย์(self):
        frame = _frame()
        frame["GLDM"] = np.nan

        notes = app._stale_price_notes(frame)

        assert len(notes) == 1
        assert "GLDM" in notes[0] and "ดึงราคาไม่ได้" in notes[0]
        assert "0%" in notes[0], "ต้องเขียนให้ชัดว่าไม่ใช่ 0%"

    def test_เฟรมว่างไม่ทำให้พัง(self):
        assert app._stale_price_notes(pd.DataFrame()) == []


class TestOverviewMetricsSpeakAboutMissingTickers:
    @pytest.fixture()
    def fake_st(self, monkeypatch) -> FakeSt:
        fake = FakeSt()
        monkeypatch.setattr(app, "st", fake)
        monkeypatch.setattr(app, "fetch_macro_data", lambda: pd.DataFrame())
        return fake

    def test_กองที่ไม่มีผลตอบแทน_1Y_ต้องถูกพูดถึงไม่ใช่หายจากการจัดอันดับเงียบๆ(
        self, fake_st
    ):
        frame = _frame()
        col = frame.columns.get_loc("GLDM")
        frame.iloc[15:, col] = np.nan  # เหลือแท่งจริง 15 แท่ง → ทุกหน้าต่างเป็น NaN

        app._render_overview_metrics(frame, list(frame.columns))

        text = fake_st.all_text()
        assert "GLDM" in text, "กองที่ถูกตัดออกจาก Best/Worst หายไปจากหน้าจอทั้งดุ้น"
        assert "Best/Worst" in text
        assert "ดึงราคาไม่ได้" in text or "แท่งราคา" in text

    def test_กองที่ข้อมูลครบไม่มีคำเตือนโผล่มาหลอน(self, fake_st):
        app._render_overview_metrics(_frame(), ["VOO", "GLDM"])

        text = fake_st.all_text()
        assert "⚠️" not in text
