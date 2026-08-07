# -*- coding: utf-8 -*-
"""ทดสอบ shadow_benchmark และ xirr (Roadmap Phase 4 ข้อ 14)."""

import math

import pandas as pd
import pytest

from portfolio.benchmark import XIRR_HIGH, _npv, _npv_tolerance, shadow_benchmark, xirr


class TestShadowBenchmark:
    def _closes(self) -> pd.Series:
        idx = pd.to_datetime(["2025-01-02", "2025-06-02", "2026-01-02"])
        return pd.Series([50.0, 80.0, 100.0], index=idx)

    def test_same_money_same_day_into_benchmark(self):
        buys = pd.DataFrame(
            [
                {"date": "2025-01-02", "shares": 2.0, "price_usd": 50.0},   # 100 USD → 2 หุ้นเงา
                {"date": "2025-06-02", "shares": 1.0, "price_usd": 160.0},  # 160 USD → 2 หุ้นเงา
            ]
        )
        result = shadow_benchmark(buys, self._closes())
        assert result["rounds"] == 2
        assert result["invested_usd"] == pytest.approx(260.0)
        assert result["benchmark_shares"] == pytest.approx(4.0)
        assert result["benchmark_value_usd"] == pytest.approx(400.0)  # 4 × ราคาปัจจุบัน 100

    def test_buy_before_history_is_skipped_not_guessed(self):
        buys = pd.DataFrame(
            [
                {"date": "2024-01-01", "shares": 1.0, "price_usd": 100.0},
                {"date": "2025-06-02", "shares": 1.0, "price_usd": 80.0},
            ]
        )
        result = shadow_benchmark(buys, self._closes())
        assert result["skipped"] == 1
        assert result["rounds"] == 1
        assert result["invested_usd"] == pytest.approx(80.0)

    def test_empty_benchmark_prices_fail_loud(self):
        with pytest.raises(ValueError):
            shadow_benchmark(pd.DataFrame([{"date": "2025-01-02", "shares": 1, "price_usd": 1}]), pd.Series(dtype=float))


class TestShadowBenchmarkCorruptRows:
    """แถวสมุดที่จำนวนหุ้น/ราคาอ่านไม่ออก ต้องไม่กลายเป็น 0.0 และต้องไม่ทำให้ผลทั้งก้อนเป็น NaN.

    ``float(pd.to_numeric(...) or 0.0)`` ดัก NaN ไม่ได้เลย (``bool(nan) is True``)
    NaN จึงไหลผ่าน guard ``amount_usd <= 0`` (เทียบกับ NaN ได้ False เสมอ)
    แล้วกลืนไม้ที่ดีทั้งหมดไปเป็น NaN เงียบ ๆ
    """

    GOOD_ROW = {"date": "2025-06-02", "shares": 1.0, "price_usd": 160.0}  # 160 USD → 2 หุ้นเงา

    def _closes(self) -> pd.Series:
        idx = pd.to_datetime(["2025-01-02", "2025-06-02", "2026-01-02"])
        return pd.Series([50.0, 80.0, 100.0], index=idx)

    @pytest.mark.parametrize(
        "bad_row",
        [
            {"date": "2025-01-02", "shares": float("nan"), "price_usd": 50.0},
            {"date": "2025-01-02", "shares": 2.0, "price_usd": float("nan")},
            {"date": "2025-01-02", "shares": None, "price_usd": 50.0},
            {"date": "2025-01-02", "shares": 2.0, "price_usd": None},
            {"date": "2025-01-02", "shares": 2.0, "price_usd": "ไม่ใช่ตัวเลข"},
            {"date": "2025-01-02", "shares": "ว่าง", "price_usd": 50.0},
            {"date": "2025-01-02", "shares": float("inf"), "price_usd": 50.0},
            {"date": "2025-01-02", "shares": 2.0, "price_usd": float("inf")},
        ],
    )
    def test_corrupt_row_is_skipped_and_good_row_survives(self, bad_row):
        result = shadow_benchmark(pd.DataFrame([bad_row, self.GOOD_ROW]), self._closes())
        for key in ("invested_usd", "benchmark_shares", "benchmark_value_usd"):
            assert math.isfinite(result[key]), f"{key} ไม่ finite — NaN/inf เล็ดลอดออกไปถึงผู้เรียก"
        assert result["rounds"] == 1, "นับไม้เสียเป็นไม้ที่เทียบได้"
        assert result["skipped"] == 1
        assert result["invested_usd"] == pytest.approx(160.0)
        assert result["benchmark_shares"] == pytest.approx(2.0)
        assert result["benchmark_value_usd"] == pytest.approx(200.0)

    def test_corrupt_amount_is_never_replaced_by_zero(self):
        """0 หุ้น/0 บาท เป็นคำตอบที่ดูสมเหตุสมผลแต่เป็นเรื่องโกหก — ต้องข้ามและรายงาน ไม่ใช่แทนด้วย 0."""
        only_bad = pd.DataFrame([{"date": "2025-01-02", "shares": float("nan"), "price_usd": 50.0}])
        result = shadow_benchmark(only_bad, self._closes())
        assert result["rounds"] == 0, "ไม้เสียถูกนับเป็นไม้ที่เทียบได้ด้วยเงิน 0 USD"
        assert result["skipped"] == 1
        assert result["invested_usd"] == 0.0 and result["benchmark_shares"] == 0.0

    def test_skip_reasons_are_reported_separately(self):
        """ตัดข้อมูลทิ้งได้ แต่ต้องบอกผู้เรียกว่าตัดเพราะอะไร — สมุดเสีย ≠ ไม่มีราคา benchmark."""
        buys = pd.DataFrame(
            [
                {"date": "2025-01-02", "shares": float("nan"), "price_usd": 50.0},  # สมุดเสีย
                {"date": "2024-01-01", "shares": 1.0, "price_usd": 100.0},           # ก่อนมีราคา
                self.GOOD_ROW,
            ]
        )
        result = shadow_benchmark(buys, self._closes())
        assert result["skipped"] == 2
        assert result["skipped_bad_row"] == 1
        assert result["skipped_no_price"] == 1
        assert result["rounds"] == 1

    def test_corrupt_benchmark_prices_do_not_leak(self):
        """ราคา benchmark ที่เป็น 0/ติดลบ/inf ต้องไม่กลายเป็นตัวหารหรือมูลค่าปัจจุบัน."""
        idx = pd.to_datetime(["2025-01-02", "2025-06-02", "2026-01-02"])
        closes = pd.Series([50.0, 80.0, float("inf")], index=idx)
        result = shadow_benchmark(pd.DataFrame([self.GOOD_ROW]), closes)
        assert math.isfinite(result["benchmark_value_usd"])
        assert result["benchmark_value_usd"] == pytest.approx(160.0)  # ตีด้วยราคาจริงล่าสุด 80

    def test_all_benchmark_prices_corrupt_fail_loud(self):
        idx = pd.to_datetime(["2025-01-02", "2025-06-02"])
        closes = pd.Series([0.0, float("nan")], index=idx)
        with pytest.raises(ValueError):
            shadow_benchmark(pd.DataFrame([self.GOOD_ROW]), closes)

    def test_denormal_benchmark_price_does_not_explode_shares(self):
        """ราคา benchmark เล็กจนเป็น denormal → หารแล้วล้นเป็น inf ต้องข้าม ไม่ใช่ส่ง inf ออกไป."""
        idx = pd.to_datetime(["2025-01-02", "2026-01-02"])
        closes = pd.Series([5e-324, 100.0], index=idx)
        buys = pd.DataFrame([{"date": "2025-01-02", "shares": 1.0, "price_usd": 160.0}])
        result = shadow_benchmark(buys, closes)
        assert math.isfinite(result["benchmark_shares"])
        assert result["rounds"] == 0
        assert result["skipped_no_price"] == 1

    def test_overflowing_total_fails_loud_instead_of_returning_inf(self):
        idx = pd.to_datetime(["2025-01-02", "2026-01-02"])
        closes = pd.Series([1e-8, 100.0], index=idx)
        buys = pd.DataFrame([{"date": "2025-01-02", "shares": 1e150, "price_usd": 1e150}])
        with pytest.raises(ValueError):
            shadow_benchmark(buys, closes)

    def test_tz_aware_buy_date_does_not_raise(self):
        """วันที่ tz-aware ต้องเข้า contract (เทียบได้/ข้าม) ไม่ใช่โยน TypeError ทะลุขึ้นไป."""
        tz_row = {"date": pd.Timestamp("2025-06-02 09:30", tz="America/New_York"), "shares": 1.0, "price_usd": 160.0}
        result = shadow_benchmark(pd.DataFrame([tz_row]), self._closes())
        assert result["rounds"] == 1
        assert result["benchmark_shares"] == pytest.approx(2.0)

    def test_tz_aware_index_matches_naive_result(self):
        closes = self._closes()
        closes.index = closes.index.tz_localize("UTC")
        naive = shadow_benchmark(pd.DataFrame([self.GOOD_ROW]), self._closes())
        aware = shadow_benchmark(pd.DataFrame([self.GOOD_ROW]), closes)
        assert aware["rounds"] == naive["rounds"] == 1
        assert aware["benchmark_shares"] == pytest.approx(naive["benchmark_shares"])


class TestShadowBenchmarkTradeDate:
    """วันที่ซื้อต้องไม่ถูกเลื่อนเงียบ ๆ และ DST ต้องไม่ระเบิดทะลุขึ้นหน้าจอ.

    ``_align_tz()`` ใช้ ``tz_convert(None)`` = ย้ายไปเวลา UTC ก่อนถอด tz ซึ่ง**เลื่อนวันที่**
    ได้ทั้งขึ้นและลง ไม้จึงถูกตีด้วยราคาของ "วันอื่น" โดยไม่มีอะไรบอก — เงียบกว่าเดิม
    (ของเดิมโยน ``TypeError`` = ดังแต่ไม่โกหก) และ ``tz_localize()`` โยน
    ``pytz.NonExistentTimeError``/``AmbiguousTimeError`` ซึ่ง**ไม่ใช่ลูกของ ValueError**
    จึงหลุดทั้ง ``except (TypeError, ValueError)`` ในโมดูลนี้และ ``except ValueError`` ของหน้าจอ
    """

    def _closes(self) -> pd.Series:
        idx = pd.to_datetime(["2025-01-02", "2025-06-01", "2025-06-02", "2026-01-02"])
        return pd.Series([50.0, 40.0, 80.0, 100.0], index=idx)

    def test_morning_bangkok_buy_keeps_its_own_trade_date(self):
        """ไม้ที่ซื้อ 06:00 น. เวลาไทยวันที่ 2 คือไม้ของวันที่ 2 — ห้ามตีด้วยราคาวันที่ 1."""
        row = {
            "date": pd.Timestamp("2025-06-02 06:00", tz="Asia/Bangkok"),
            "shares": 1.0,
            "price_usd": 160.0,
        }
        result = shadow_benchmark(pd.DataFrame([row]), self._closes())
        assert result["rounds"] == 1
        # ราคา 2025-06-02 = 80 → 2 หุ้น (ถ้าเลื่อนไปวันที่ 1 ราคา 40 จะได้ 4 หุ้น = พองเท่าตัว)
        assert result["benchmark_shares"] == pytest.approx(2.0)

    def test_evening_new_york_buy_does_not_jump_to_next_day(self):
        row = {
            "date": pd.Timestamp("2025-06-01 21:00", tz="America/New_York"),
            "shares": 1.0,
            "price_usd": 160.0,
        }
        result = shadow_benchmark(pd.DataFrame([row]), self._closes())
        assert result["rounds"] == 1
        assert result["benchmark_shares"] == pytest.approx(4.0)  # ราคา 2025-06-01 = 40

    def test_tz_aware_buy_on_first_price_day_is_not_dropped(self):
        """เลื่อนวันย้อนหลังทำให้ไม้แรกตกนอกช่วงราคา แล้วหายไปจากการเทียบเงียบ ๆ."""
        row = {
            "date": pd.Timestamp("2025-01-02 06:00", tz="Asia/Bangkok"),
            "shares": 1.0,
            "price_usd": 160.0,
        }
        result = shadow_benchmark(pd.DataFrame([row]), self._closes())
        assert result["skipped_no_price"] == 0, "ไม้ที่มีราคาในวันเดียวกันถูกตัดเพราะเลื่อนวัน"
        assert result["rounds"] == 1

    @pytest.mark.parametrize(
        "buy_date",
        [
            pd.Timestamp("2025-03-09 02:30"),   # เวลาที่ไม่มีอยู่จริง (DST เดินหน้า)
            pd.Timestamp("2025-11-02 01:30"),   # เวลาที่ซ้ำสองรอบ (DST ถอยหลัง)
        ],
    )
    def test_dst_edge_does_not_escape_as_unhandled_error(self, buy_date):
        """หน้าจอจับแค่ ValueError — pytz DST error หลุดไปเป็นหน้าจอแดงทั้งหน้า."""
        idx = pd.to_datetime(["2025-03-01", "2025-11-01", "2025-12-01"]).tz_localize("America/New_York")
        closes = pd.Series([50.0, 80.0, 100.0], index=idx)
        buys = pd.DataFrame([{"date": buy_date, "shares": 1.0, "price_usd": 160.0}])
        try:
            result = shadow_benchmark(buys, closes)
        except ValueError:
            return  # ล้มดัง ๆ ในแบบที่ผู้เรียกจับได้ = รับได้
        assert result["rounds"] + result["skipped"] == 1


class TestShadowBenchmarkFactorSign:
    """ค่าที่ "มีอยู่แต่ใช้ไม่ได้" สองตัวคูณกันแล้วกลายเป็นไม้ที่ดูสมเหตุสมผล."""

    def _closes(self) -> pd.Series:
        idx = pd.to_datetime(["2025-01-02", "2025-06-02", "2026-01-02"])
        return pd.Series([50.0, 80.0, 100.0], index=idx)

    def test_negative_shares_times_negative_price_is_not_a_buy(self):
        buys = pd.DataFrame([{"date": "2025-06-02", "shares": -1.0, "price_usd": -160.0}])
        result = shadow_benchmark(buys, self._closes())
        assert result["rounds"] == 0, "หุ้นติดลบ × ราคาติดลบ ถูกนับเป็นไม้ซื้อ 160 USD"
        assert result["skipped_bad_row"] == 1
        assert result["invested_usd"] == 0.0

    def test_negative_price_alone_is_skipped(self):
        buys = pd.DataFrame([{"date": "2025-06-02", "shares": 1.0, "price_usd": -160.0}])
        result = shadow_benchmark(buys, self._closes())
        assert result["rounds"] == 0
        assert result["skipped_bad_row"] == 1


class TestShadowBenchmarkPriceReporting:
    """ราคา benchmark ที่ถูกคัดทิ้ง ต้องรายงานออกไป — ตัดข้อมูลทิ้งเงียบ ๆ ผิดพอกับกุตัวเลข."""

    def _closes(self) -> pd.Series:
        idx = pd.to_datetime(["2025-01-02", "2025-06-02", "2026-01-02"])
        return pd.Series([50.0, 80.0, 100.0], index=idx)

    def test_unusable_latest_price_is_reported_not_silently_stale(self):
        """ราคาล่าสุดใช้ไม่ได้ → ตีมูลค่าด้วยราคาเก่า 7 เดือน โดยผู้เรียกไม่รู้ตัว."""
        idx = pd.to_datetime(["2025-01-02", "2025-06-02", "2026-01-02"])
        closes = pd.Series([50.0, 80.0, float("inf")], index=idx)
        buys = pd.DataFrame([{"date": "2025-06-02", "shares": 1.0, "price_usd": 160.0}])
        result = shadow_benchmark(buys, closes)
        assert result["benchmark_prices_dropped"] == 1
        assert result["benchmark_asof"] == pd.Timestamp("2025-06-02"), (
            "ผู้เรียกต้องรู้ว่ามูลค่าเงาถูกตีด้วยราคาวันไหน ไม่งั้นเอาไปเทียบกับพอร์ตวันนี้แบบคนละวัน"
        )

    def test_clean_prices_report_zero_dropped_and_latest_date(self):
        result = shadow_benchmark(
            pd.DataFrame([{"date": "2025-06-02", "shares": 1.0, "price_usd": 160.0}]),
            self._closes(),
        )
        assert result["benchmark_prices_dropped"] == 0
        assert result["benchmark_asof"] == pd.Timestamp("2026-01-02")


class TestShadowBenchmarkRegressionGuard:
    """เคสปกติต้องได้ตัวเลขเดิมเป๊ะหลังแก้ขอบ."""

    def _closes(self) -> pd.Series:
        idx = pd.to_datetime(["2025-01-02", "2025-06-02", "2026-01-02"])
        return pd.Series([50.0, 80.0, 100.0], index=idx)

    def test_clean_ledger_numbers_unchanged(self):
        buys = pd.DataFrame(
            [
                {"date": "2025-01-02", "shares": 2.0, "price_usd": 50.0},
                {"date": "2025-06-02", "shares": 1.0, "price_usd": 160.0},
                {"date": "2026-01-02", "shares": 0.5, "price_usd": 300.0},
            ]
        )
        result = shadow_benchmark(buys, self._closes())
        # เพิ่ม benchmark_prices_dropped/benchmark_asof เข้ามาในรอบตรวจซ้ำ: ราคาที่ถูกคัดทิ้ง
        # ต้องรายงานออกไป ไม่งั้นราคาล่าสุดที่ใช้ไม่ได้จะกลายเป็นมูลค่าเงาลงวันที่เก่าเงียบ ๆ
        # ตัวเลขเงินทั้งสามยังเท่าเดิมเป๊ะ (410 / 5.5 / 550)
        assert result == {
            "invested_usd": pytest.approx(410.0),
            "benchmark_shares": pytest.approx(5.5),
            "benchmark_value_usd": pytest.approx(550.0),
            "rounds": 3,
            "skipped": 0,
            "skipped_bad_row": 0,
            "skipped_no_price": 0,
            "benchmark_prices_dropped": 0,
            "benchmark_asof": pd.Timestamp("2026-01-02"),
        }

    def test_zero_amount_row_still_skipped(self):
        buys = pd.DataFrame(
            [
                {"date": "2025-01-02", "shares": 0.0, "price_usd": 50.0},
                {"date": "2025-06-02", "shares": 1.0, "price_usd": 160.0},
            ]
        )
        result = shadow_benchmark(buys, self._closes())
        assert result["rounds"] == 1
        assert result["skipped"] == 1
        assert result["invested_usd"] == pytest.approx(160.0)


class TestXirr:
    def test_single_year_ten_percent(self):
        flows = [(pd.Timestamp("2025-01-01"), -100.0), (pd.Timestamp("2026-01-01"), 110.0)]
        rate = xirr(flows)
        assert rate == pytest.approx(0.10, abs=1e-3)

    def test_multiple_flows(self):
        flows = [
            (pd.Timestamp("2024-01-01"), -100.0),
            (pd.Timestamp("2025-01-01"), -100.0),
            (pd.Timestamp("2026-01-01"), 231.0),
        ]
        rate = xirr(flows)
        assert rate is not None and 0.05 < rate < 0.15

    def test_all_negative_returns_none(self):
        flows = [(pd.Timestamp("2025-01-01"), -100.0), (pd.Timestamp("2026-01-01"), -10.0)]
        assert xirr(flows) is None

    def test_insufficient_flows_return_none(self):
        assert xirr([]) is None
        assert xirr([(pd.Timestamp("2025-01-01"), -100.0)]) is None

    def test_total_loss_bounded(self):
        flows = [(pd.Timestamp("2025-01-01"), -100.0), (pd.Timestamp("2026-01-01"), 1.0)]
        rate = xirr(flows)
        assert rate is not None and rate < -0.9


class TestXirrCorruptCashflows:
    """ข้อมูลกระแสเงินเสีย ต้องได้ ``None`` — ห้ามคืนขอบบนของช่วงค้นหา (10.0 = +1000%/ปี)."""

    def test_nan_amount_returns_none_not_upper_bound(self):
        # ก่อนแก้: bisection ไม่ลู่เข้าเพราะ NPV เป็น NaN แล้วคืน (low+high)/2 = 10.0
        flows = [
            (pd.Timestamp("2025-01-01"), -100.0),
            (pd.Timestamp("2025-06-01"), float("nan")),
            (pd.Timestamp("2026-01-01"), 110.0),
        ]
        rate = xirr(flows)
        assert rate != pytest.approx(XIRR_HIGH), "คืนขอบบนของช่วงค้นหาเป็นคำตอบ = เดาเลข +1000%/ปี"
        assert rate is None

    def test_nan_amount_is_not_silently_dropped(self):
        """กรอง NaN ทิ้งแล้วคำนวณต่อ = ตอบ XIRR ของพอร์ตที่ไม่มีอยู่จริง — ต้องไม่ทำ."""
        good = [
            (pd.Timestamp("2024-01-01"), -100.0),
            (pd.Timestamp("2026-01-01"), 121.0),
        ]
        with_nan = good + [(pd.Timestamp("2025-01-01"), float("nan"))]
        assert xirr(good) is not None
        assert xirr(with_nan) is None

    def test_inf_amount_returns_none(self):
        flows = [
            (pd.Timestamp("2025-01-01"), -100.0),
            (pd.Timestamp("2025-06-01"), float("inf")),
            (pd.Timestamp("2026-01-01"), 110.0),
        ]
        assert xirr(flows) is None

    def test_unparseable_amount_returns_none(self):
        flows = [
            (pd.Timestamp("2025-01-01"), -100.0),
            (pd.Timestamp("2025-06-01"), "ไม่ใช่ตัวเลข"),
            (pd.Timestamp("2026-01-01"), 110.0),
        ]
        assert xirr(flows) is None

    def test_missing_date_returns_none_not_partial_answer(self):
        """วันที่เสีย = ไม้นั้นหายไปจากไทม์ไลน์ ผลลัพธ์จึงไม่ใช่ XIRR ของพอร์ตจริง."""
        flows = [
            (pd.Timestamp("2025-01-01"), -100.0),
            (None, -50.0),
            (pd.Timestamp("2026-01-01"), 170.0),
        ]
        assert xirr(flows) is None

    def test_all_positive_has_no_root(self):
        flows = [
            (pd.Timestamp("2025-01-01"), 100.0),
            (pd.Timestamp("2026-01-01"), 110.0),
        ]
        assert xirr(flows) is None

    def test_no_corrupt_input_ever_yields_search_bound(self):
        nan = float("nan")
        corrupt_cases = [
            [(pd.Timestamp("2025-01-01"), -100.0), (pd.Timestamp("2026-01-01"), nan), (pd.Timestamp("2026-06-01"), 110.0)],
            [(pd.Timestamp("2025-01-01"), nan), (pd.Timestamp("2026-01-01"), 110.0), (pd.Timestamp("2026-06-01"), -50.0)],
            [(pd.NaT, -100.0), (pd.Timestamp("2026-01-01"), 110.0), (pd.Timestamp("2026-06-01"), 5.0)],
        ]
        for flows in corrupt_cases:
            assert xirr(flows) is None, f"เดาเลขแทนที่จะยอมรับว่าคำนวณไม่ได้: {flows}"

    def test_extreme_date_span_does_not_crash(self):
        """ช่วงเวลายาวจนตัวหาร underflow ต้องไม่ระเบิด (ZeroDivisionError) และคำตอบต้องถูก."""
        flows = [(pd.Timestamp("1900-01-01"), -100.0), (pd.Timestamp("2026-01-01"), 110.0)]
        rate = xirr(flows)
        assert rate is not None
        # (1+r)^126 = 1.1 → r ≈ 0.000757
        assert rate == pytest.approx(0.000757, abs=1e-5)


class TestXirrScaleIndependence:
    """เกณฑ์ยอมรับรากต้องสัมพันธ์กับขนาดกระแสเงิน ไม่ใช่ค่าคงที่."""

    def _shape(self, scale: float) -> list[tuple[pd.Timestamp, float]]:
        return [
            (pd.Timestamp("2024-01-01"), -1.0 * scale),
            (pd.Timestamp("2025-01-01"), -1.0 * scale),
            (pd.Timestamp("2026-01-01"), 2.31 * scale),
        ]

    def test_thousand_times_bigger_portfolio_gives_same_rate(self):
        small = xirr(self._shape(1_000.0))       # พอร์ตหลักพัน
        big = xirr(self._shape(1_000_000.0))     # พอร์ตหลักล้าน (ใหญ่กว่า 1000 เท่า)
        assert small is not None and big is not None
        assert small == pytest.approx(big, abs=1e-9)
        assert small == pytest.approx(0.09997309, abs=1e-6)

    def test_tolerance_scales_with_cashflow_size(self):
        small = self._shape(1_000.0)
        big = self._shape(1_000_000.0)
        assert _npv_tolerance(big) == pytest.approx(_npv_tolerance(small) * 1_000.0)
        assert _npv_tolerance(small) > 0.0

    def _dca_ledger(self, scale: float, rounds: int = 54) -> list[tuple[pd.Timestamp, float]]:
        """สมุด DCA รายเดือน ``rounds`` ไม้ + มูลค่าปัจจุบันตอนท้าย."""
        start = pd.Timestamp("2020-01-15")
        flows = [(start + pd.DateOffset(months=i), -1_000.0 * scale) for i in range(rounds)]
        flows.append((start + pd.DateOffset(months=rounds), 1_000.0 * scale * rounds * 1.35))
        return flows

    def test_million_scale_ledger_would_be_rejected_by_a_fixed_tolerance(self):
        """เกณฑ์คงที่ 1e-6 จะปฏิเสธพอร์ตหลักล้านที่คำตอบถูกต้อง — จึงต้องใช้เกณฑ์สัมพัทธ์."""
        big = self._dca_ledger(1_000_000.0)
        rate = xirr(big)
        assert rate is not None, "พอร์ตใหญ่ที่มีรากจริงถูกปฏิเสธ — เกณฑ์ยอมรับตึงเกินขนาดกระแสเงิน"

        residual = abs(_npv(rate, big, big[0][0]))
        assert residual > 1e-6, "เคสนี้ต้องเหลือ residual เกินเกณฑ์คงที่ ไม่งั้นเทสต์ไม่ได้พิสูจน์อะไร"
        assert residual <= _npv_tolerance(big)

        small = self._dca_ledger(1.0)  # สมุดหน้าตาเดียวกัน เล็กกว่าล้านเท่า
        assert xirr(small) == pytest.approx(rate, abs=1e-9)

    def test_accepted_root_really_zeroes_npv_at_both_scales(self):
        for scale in (1_000.0, 1_000_000.0):
            flows = self._shape(scale)
            rate = xirr(flows)
            assert rate is not None
            residual = _npv(rate, flows, flows[0][0])
            assert math.isfinite(residual)
            assert abs(residual) <= _npv_tolerance(flows)


class TestXirrRegressionGuard:
    """เคสปกติต้องได้ตัวเลขเดิมเป๊ะหลังแก้ขอบ (แกนคณิตของ solver ไม่ถูกแตะ)."""

    @pytest.mark.parametrize(
        "flows, expected",
        [
            (
                [(pd.Timestamp("2025-01-01"), -100.0), (pd.Timestamp("2026-01-01"), 110.0)],
                0.1000718113939972,
            ),
            (
                [
                    (pd.Timestamp("2024-01-01"), -100.0),
                    (pd.Timestamp("2025-01-01"), -100.0),
                    (pd.Timestamp("2026-01-01"), 231.0),
                ],
                0.09997309463828508,
            ),
            (
                [(pd.Timestamp("2025-01-01"), -100.0), (pd.Timestamp("2026-01-01"), 1.0)],
                -0.9900314925681399,
            ),
        ],
    )
    def test_known_rates_unchanged(self, flows, expected):
        assert xirr(flows) == pytest.approx(expected, abs=1e-12)

    def test_zero_amount_rows_still_ignored(self):
        flows = [
            (pd.Timestamp("2025-01-01"), -100.0),
            (pd.Timestamp("2025-06-01"), 0.0),
            (pd.Timestamp("2026-01-01"), 110.0),
        ]
        assert xirr(flows) == pytest.approx(0.1000718113939972, abs=1e-12)
