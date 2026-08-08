# -*- coding: utf-8 -*-
"""FIX_PLAN ข้อ 2.1 (ส่วนที่เหลือ) — "ตัดสินไม่ได้" ต้องไม่กลายเป็น "ไม่มีสัญญาณ".

**อาการ** ตัวตรวจจับใน ``backend/screener/crossover_detector.py`` คืน ``bool`` ล้วน:
ETF ที่ประวัติสั้นกว่า 200 วัน (MA200 เป็น NaN ทั้งเส้น) ได้ ``False`` **เท่ากับ** ETF ที่
คำนวณได้แล้วพบว่าไม่มีการตัด ทุกไบต์ ⇒ พรีเซ็ต ``golden_cross_alert`` ที่งาน 07:00 ใช้
รายงาน "ไม่มีสัญญาณ" ให้กองเหล่านั้น **ตลอดกาล** โดยไม่มีอะไรร้อง และผู้ใช้อ่านว่า
"ตรวจแล้วไม่มีอะไรต้องทำ"

ความไม่สอดคล้องอยู่ในเอนจินเดียวกัน: ``price_vs_ma200`` โยน ``ValueError`` เมื่อ MA200
เป็น NaN อยู่แล้ว แต่ ``golden_cross``/``death_cross``/``bb_squeeze`` กลืนเงียบ

**แก้** ตัวตรวจจับคืน ``None`` = คำนวณไม่ได้ · ``engine._decided()`` แปลงเป็น ``ValueError``
· ``run()`` เก็บลง ``.errors`` รายสัญลักษณ์ที่งาน 07:00 กับหน้าจอรายงานต่อ

เพิ่ม: ``_fetch_df`` ใช้ ``period="2y"`` แทน ``"1y"`` — 1 ปี (~250 แท่ง) เหลือ margin
เหนือ MA200 แค่ ~50 แท่ง ซึ่ง lookback 3 แท่งกับค่าเฉลี่ย bandwidth 50 แท่งกินต่อ

**ข้อที่ยังไม่แตะโดยตั้งใจ**: จุดอ้างอิงของ ``detect_price_drop_pct`` ยังเป็น
``iloc[-days]`` ตามเดิม — คอมมิตนี้เพิ่มเฉพาะด่าน "คำนวณไม่ได้" การขยับจุดอ้างอิงจะ
เปลี่ยนสัญญาณเงียบ ๆ ซึ่งเป็นคนละเรื่องกัน
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.screener.crossover_detector import NO_CROSS, CrossoverDetector
from backend.screener.engine import ScreenerEngine, _decided
from backend.screener.models import ScreenerPreset, ScreenerRule


def _frame(rows: int, *, start: float = 100.0, step: float = 0.15, volume: float = 1_000_000.0):
    """ราคาขาขึ้นเรียบ ๆ ``rows`` แท่ง — ยาวพอหรือไม่พอตามที่แต่ละเทสต์ต้องการ."""
    index = pd.bdate_range("2020-01-01", periods=rows)
    close = start + np.arange(rows) * step
    return pd.DataFrame({"Close": close, "Volume": [volume] * rows}, index=index)


def _golden_cross_frame():
    """ราคาที่ MA50 ตัดขึ้นเหนือ MA200 ที่ **แท่งสุดท้ายพอดี**.

    หาจุดตัดจากข้อมูลจริงแล้วตัดเฟรมให้จบที่นั่น — เขียนช่วงขาลง/ขาขึ้นด้วยตาเปล่าแล้ว
    หวังว่าจุดตัดจะอยู่ใน lookback 3 แท่งเป็นการเดา (ลองแล้วไม่เข้า)
    """
    rows = 700
    index = pd.bdate_range("2018-01-01", periods=rows)
    close = np.concatenate([np.linspace(200.0, 80.0, 450), np.linspace(80.0, 320.0, rows - 450)])
    frame = pd.DataFrame({"Close": close, "Volume": [1_000_000.0] * rows}, index=index)
    ma50 = frame["Close"].rolling(50).mean()
    ma200 = frame["Close"].rolling(200).mean()
    crossed = (ma50 > ma200) & (ma50.shift(1) <= ma200.shift(1))
    positions = np.flatnonzero(crossed.to_numpy())
    assert positions.size, "ฉากทดสอบต้องมีจุดตัดจริง"
    return frame.iloc[: positions[0] + 1]


DET = CrossoverDetector()


# =========================================================================== #
# ตัวตรวจจับ: None = คำนวณไม่ได้
# =========================================================================== #
class TestDetectorsReturnNoneWhenUndecidable:
    # แต่ละสูตรต้องการแท่งไม่เท่ากัน — golden/death cross กิน MA200 ส่วน bb_squeeze
    # กิน 20 (แบนด์) + 50 (ค่าเฉลี่ย bandwidth) = 70 · ใช้เลขเดียวกันทุกตัวจะได้เทสต์ที่
    # ผ่านด้วยเหตุผลผิด (120 แท่งพอสำหรับ bb แล้ว)
    @pytest.mark.parametrize(
        "call,rows",
        [
            (lambda df: DET.detect_golden_cross(df), 120),
            (lambda df: DET.detect_death_cross(df), 120),
            (lambda df: DET.detect_bb_squeeze(df), 40),
        ],
        ids=["golden_cross", "death_cross", "bb_squeeze"],
    )
    def test_ประวัติสั้นเกินไปต้องได้_None_ไม่ใช่_False(self, call, rows):
        result = call(_frame(rows))
        assert result is None, (
            f"ได้ {result!r} — ประวัติ {rows} แท่งตัดสินไม่ได้ แต่ False อ่านว่า 'ไม่มีสัญญาณ'"
        )

    def test_bb_squeeze_ที่แท่งพอดีเกณฑ์ตัดสินได้(self):
        """ขอบล่างของ bb_squeeze คือ 20 + 50 = 70 แท่ง — ต่ำกว่านั้นตัดสินไม่ได้."""
        assert DET.detect_bb_squeeze(_frame(69)) is None
        assert isinstance(DET.detect_bb_squeeze(_frame(70)), bool)

    @pytest.mark.parametrize(
        "call",
        [
            lambda df: DET.detect_golden_cross(df),
            lambda df: DET.detect_death_cross(df),
            lambda df: DET.detect_bb_squeeze(df),
        ],
        ids=["golden_cross", "death_cross", "bb_squeeze"],
    )
    def test_ประวัติพอต้องตัดสินได้จริง(self, call):
        result = call(_frame(400))
        assert isinstance(result, bool), f"ได้ {result!r} — ประวัติ 400 แท่งต้องตัดสินได้"

    def test_golden_cross_ที่มีจริงยังจับได้(self):
        assert DET.detect_golden_cross(_golden_cross_frame()) is True

    def test_ขาลงต้องไม่รายงาน_golden_cross(self):
        falling = _frame(400, start=200.0, step=-0.2)
        assert DET.detect_golden_cross(falling) is False
        assert DET.detect_death_cross(falling) in (True, False)

    # ---------------------------------------------------------------- #
    # ยาวพอแต่ **ค่าใช้ไม่ได้** — คนละเคสกับ "แท่งไม่พอ" และเป็นเคสที่เกิดจริงกว่า
    # (ผู้ให้ข้อมูลส่งแท่งขาดกลางเส้น) ด่านความยาวจับไม่ได้ ต้องมีด่าน NaN แยก
    # ---------------------------------------------------------------- #
    @pytest.mark.parametrize(
        "call",
        [lambda df: DET.detect_golden_cross(df), lambda df: DET.detect_death_cross(df)],
        ids=["golden_cross", "death_cross"],
    )
    def test_แท่งยาวพอแต่ราคาปลายเส้นเป็น_NaN_ต้องได้_None(self, call):
        df = _frame(400)
        df.iloc[-2:, df.columns.get_loc("Close")] = np.nan
        assert call(df) is None, "MA ในหน้าต่างที่ต้องอ่านเป็น NaN = ตัดสินไม่ได้ ไม่ใช่ 'ไม่มีการตัด'"

    @pytest.mark.parametrize(
        "call",
        [lambda df: DET.detect_golden_cross(df), lambda df: DET.detect_death_cross(df)],
        ids=["golden_cross", "death_cross"],
    )
    def test_ช่องว่างกลางเส้นที่ยังกินหน้าต่าง_MA_ก็ตัดสินไม่ได้(self, call):
        df = _frame(400)
        df.iloc[-30, df.columns.get_loc("Close")] = np.nan
        assert call(df) is None

    def test_bb_squeeze_แท่งยาวพอแต่ราคาเป็น_NaN_ต้องได้_None(self):
        df = _frame(300)
        df.iloc[-1, df.columns.get_loc("Close")] = np.nan
        assert DET.detect_bb_squeeze(df) is None

    def test_bb_squeeze_เส้นกลางเป็นศูนย์ต้องได้_None_ไม่ใช่ไม่บีบตัว(self):
        """ราคา 0 ทั้งช่วง ⇒ bandwidth หารศูนย์ — เป็นข้อมูลที่ใช้ไม่ได้ ไม่ใช่คำตอบ."""
        df = _frame(300)
        df.iloc[-80:, df.columns.get_loc("Close")] = 0.0
        assert DET.detect_bb_squeeze(df) is None

    def test_lookback_ที่ยาวขึ้นต้องการแท่งมากขึ้น(self):
        """หน้าต่างที่มองย้อนไกลกว่าต้องอ่าน MA ได้ครบทุกจุดที่จะใช้."""
        df = _frame(_MA_LONG_ROWS := 202)
        assert DET.detect_golden_cross(df, lookback_days=1) is not None
        assert DET.detect_golden_cross(df, lookback_days=50) is None


class TestMacdSeparatesNoCrossFromCannotCompute:
    def test_ข้อมูลไม่พอได้_None(self):
        assert DET.detect_macd_cross(_frame(2)) is None

    def test_ช่วงอุ่นเครื่องที่ยังเป็น_NaN_ได้_None(self):
        assert DET.detect_macd_cross(_frame(10)) is None

    def test_คำนวณได้แต่ไม่มีการตัดได้_NO_CROSS(self):
        result = DET.detect_macd_cross(_frame(300))
        assert result == NO_CROSS, f"ได้ {result!r}"
        assert result is not None, "'ไม่มีการตัด' ต้องแยกจาก 'คำนวณไม่ได้'"

    def test_สองสถานะนี้ห้ามเท่ากัน(self):
        assert DET.detect_macd_cross(_frame(2)) != DET.detect_macd_cross(_frame(300))


class TestVolumeAndDropGuards:
    def test_ไม่มีคอลัมน์_volume_ได้_None(self):
        df = _frame(100).drop(columns=["Volume"])
        assert DET.detect_volume_spike(df) is None

    def test_volume_เป็นศูนย์ทั้งเส้นต้องไม่รายงานว่าพุ่ง(self):
        """ค่าเฉลี่ย 0 ทำให้ทุกอย่าง "เกิน" — เป็นข้อมูลที่หาย ไม่ใช่ปริมาณพุ่ง."""
        assert DET.detect_volume_spike(_frame(100, volume=0.0)) is None

    def test_volume_พุ่งจริงยังจับได้(self):
        df = _frame(100)
        df.iloc[-1, df.columns.get_loc("Volume")] = 9_000_000.0
        assert DET.detect_volume_spike(df) is True

    def test_แท่งไม่พอสำหรับ_price_drop_ได้_None(self):
        assert DET.detect_price_drop_pct(_frame(5), days=10) is None
        assert DET.detect_price_drop_pct(_frame(20), days=0) is None

    def test_จุดอ้างอิงยังเป็น_iloc_ลบ_days_ตามเดิม(self):
        """ตรึงพฤติกรรมเดิมไว้ — คอมมิตนี้ไม่ได้ขยับจุดอ้างอิง."""
        df = _frame(50, start=100.0, step=0.0)
        df.iloc[-10, df.columns.get_loc("Close")] = 200.0  # แท่งอ้างอิงของ days=10
        assert DET.detect_price_drop_pct(df, pct=40.0, days=10) is True
        assert DET.detect_price_drop_pct(df, pct=60.0, days=10) is False


# =========================================================================== #
# เอนจิน: None → ValueError → .errors
# =========================================================================== #
class TestDecidedTranslatesNone:
    def test_None_ต้องดังพร้อมข้อความที่ปฏิเสธว่าเป็น_ไม่มีสัญญาณ(self):
        with pytest.raises(ValueError, match="ไม่ใช่ 'ไม่มีสัญญาณ'"):
            _decided(None, "golden cross")

    @pytest.mark.parametrize("value,expected", [(True, True), (False, False)])
    def test_ค่าที่ตัดสินได้ผ่านไปตามปกติ(self, value, expected):
        assert _decided(value, "x") is expected


class TestEngineReportsUndecidableAsError:
    ENGINE = ScreenerEngine()

    @staticmethod
    def _preset(field: str, operator: str, value: float = 3.0) -> ScreenerPreset:
        return ScreenerPreset(
            name=f"probe-{field}",
            description="",
            logic="AND",
            rules=[ScreenerRule(field=field, operator=operator, value=value, description=field)],
        )

    @pytest.mark.parametrize(
        "field,operator",
        [
            ("golden_cross", "cross_up"),
            ("death_cross", "cross_down"),
            ("bb_squeeze", "squeeze"),
            ("macd_cross", "cross_up"),
        ],
    )
    def test_ประวัติสั้นไปเป็น_error_ไม่ใช่ผลลัพธ์ว่าง(self, field, operator):
        results = self.ENGINE.run(
            ["SHORT"], self._preset(field, operator), frames={"SHORT": _frame(30)}
        )
        assert list(results) == [], "ตัดสินไม่ได้ = ไม่มีผลลัพธ์"
        assert len(results.errors) == 1, (
            f"{field}: ต้องมี error รายสัญลักษณ์ ไม่ใช่เงียบ — errors={results.errors}"
        )
        assert "SHORT" in results.errors[0]

    def test_สองกรณีให้ผลไม่เหมือนกันแล้ว(self):
        """หัวใจของข้อนี้: "ตัดสินไม่ได้" กับ "ตรวจแล้วไม่มีสัญญาณ" ต้องแยกออกจากกัน."""
        preset = self._preset("golden_cross", "cross_up")
        undecidable = self.ENGINE.run(["A"], preset, frames={"A": _frame(30)})
        decided_no_signal = self.ENGINE.run(
            ["B"], preset, frames={"B": _frame(400, start=200.0, step=-0.2)}
        )
        assert list(undecidable) == [] and list(decided_no_signal) == []
        assert undecidable.errors and not decided_no_signal.errors, (
            "ผลลัพธ์ว่างเหมือนกัน แต่ช่อง errors ต้องต่างกัน ไม่งั้นผู้ใช้แยกไม่ออก"
        )

    def test_สัญญาณจริงยังผ่านตามปกติ(self):
        results = self.ENGINE.run(
            ["GC"], self._preset("golden_cross", "cross_up"), frames={"GC": _golden_cross_frame()}
        )
        assert [r.symbol for r in results] == ["GC"]
        assert not results.errors

    def test_กองที่ตัดสินไม่ได้ไม่ลากกองอื่นตกไปด้วย(self):
        results = self.ENGINE.run(
            ["GC", "SHORT"],
            self._preset("golden_cross", "cross_up"),
            frames={"GC": _golden_cross_frame(), "SHORT": _frame(30)},
        )
        assert [r.symbol for r in results] == ["GC"]
        assert len(results.errors) == 1 and "SHORT" in results.errors[0]


class TestFetchWindowHasMarginOverMa200:
    def test_ดึงสองปีไม่ใช่หนึ่งปี(self, monkeypatch):
        """MA200 กิน 200 แท่ง — 1 ปี (~250) เหลือ margin ~50 แท่งซึ่งบางเกินไป."""
        captured: dict[str, object] = {}

        def _fake_download(symbol, **kwargs):
            captured.update(kwargs)
            return _frame(500).rename(columns={"Close": "Close"})

        from backend.screener import engine as engine_mod

        monkeypatch.setattr(engine_mod.yfinance, "download", _fake_download)
        engine_mod.ScreenerEngine()._fetch_df("VOO")
        assert captured["period"] == "2y", f"period={captured['period']!r}"
        assert captured["auto_adjust"] is True, "ราคา adjusted เป็นมาตรฐานเดียวทั้งระบบ (M1)"
