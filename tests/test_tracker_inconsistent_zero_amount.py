# -*- coding: utf-8 -*-
"""K4 — ด่าน C1.2 (``_collect_inconsistent_rows``) ต้องไม่ปล่อยยอดเงินที่ "เทียบไม่ได้" ผ่าน.

ด่านนี้เทียบ ``amount_thb`` กับ ``shares × price_usd × fx_rate_thb + fee_thb``
ด้วย **สัดส่วน** โดยใช้ ``amount_thb`` เป็นตัวหาร:

    ratio = |amount − implied| / amount.abs().where(amount.abs() > 0)

ตัวหารที่ใช้ไม่ได้ (0 / NaN / ±inf) ทำให้ ``ratio`` เป็น **NaN** และ
``NaN > threshold`` เป็น ``False`` เสมอ ⇒ แถวที่ยอดเงินขัดกับตัวเลขอื่นชัด ๆ
(จ่าย 0 บาททั้งที่ซื้อ 10 หุ้น × 400 USD × 35) **เดินผ่านด่านเงียบ ๆ**
(รูปแบบเดิมของโปรเจกต์: NaN เล็ดลอดผ่าน guard)

ผลจริงของการหลุด: ``Invested (THB)`` คิดจาก ``shares × price_usd × fx_rate_thb``
(``get_portfolio_summary()``) **ไม่ได้คิดจาก ``amount_thb``** ยอดเงินที่บันทึกไว้
จึงไม่เคยถูกเอาไปเทียบกับอะไรเลยถ้าด่านนี้ไม่จับ — สมุดที่ขัดกันเองในแถวเดียว
(จ่าย 0 บาท แต่ได้หุ้นมา 140,000 บาท) ผ่านฉลุยและไม่มีคำเตือนสักบรรทัด
ผู้ใช้ไม่มีทางรู้ว่าเลขไหนคือเลขที่กรอกผิด

"เทียบไม่ได้" ≠ "ผ่าน" — แถวแบบนี้ต้องถูกรายงานว่าไม่สอดคล้อง
แต่ห้ามรายงานเป็น % ที่หารด้วยศูนย์ขึ้นมาเอง (``diff_pct`` ต้องเป็น ``None``
ไม่ใช่ ``nan`` และข้อความเตือนห้ามมีคำว่า nan)

รอบสอง: ตัวเศษก็เล็ดลอดได้เหมือนกัน — ``gap = |amount − implied|`` เป็น ``NaN``
ได้เอง (``implied`` เป็น NaN จาก ``0 × inf`` หรือ ``inf − inf``) แล้ว
**ทั้ง** ``ratio > tol`` **และ** ``gap > 0`` เป็น ``False`` พร้อมกัน
ด่านจึงกลับไปเงียบอีกครั้งด้วยกลไกเดิมเป๊ะ ๆ
"""

import math

import pandas as pd
import pytest

from portfolio import tracker

HEADER = "tx_id,date,ticker,shares,price_usd,fx_rate_thb,amount_thb,fee_thb,note,tx_type\n"
FX_TODAY = 34.0


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    """สมุดสังเคราะห์ + ตัดเส้นทาง network (ห้ามแตะสมุดจริงของผู้ใช้)."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    csv_path = data_dir / "transactions.csv"
    monkeypatch.setattr(tracker, "DATA_DIR", data_dir)
    monkeypatch.setattr(tracker, "TRANSACTIONS_FILE", csv_path)
    monkeypatch.setattr(tracker, "_get_latest_prices", lambda tickers: {})
    monkeypatch.setattr(tracker, "_get_usdthb_rate", lambda: FX_TODAY)
    return csv_path


@pytest.fixture()
def prices(monkeypatch):
    """ตั้งราคาที่ดึงได้ทีละเคส (default ของ fixture ``ledger`` คือดึงไม่ได้เลย)."""

    def _set(mapping: dict[str, float]) -> None:
        monkeypatch.setattr(
            tracker,
            "_get_latest_prices",
            lambda tickers: {t: mapping[t] for t in tickers if t in mapping},
        )

    return _set


def _frame(rows: list[dict]) -> pd.DataFrame:
    """DataFrame รูปแบบเดียวกับที่ ``_load_transactions()`` ส่งเข้าด่าน C1.2."""
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    for col in ("shares", "price_usd", "fx_rate_thb", "amount_thb", "fee_thb"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _row(tx_id: str, **overrides) -> dict:
    base = {
        "tx_id": tx_id,
        "date": "2026-03-05",
        "ticker": "VOO",
        "shares": 10.0,
        "price_usd": 400.0,
        "fx_rate_thb": 35.0,
        "amount_thb": 140_000.0,
        "fee_thb": 0.0,
        "tx_type": tracker.TX_BUY,
    }
    base.update(overrides)
    return base


class TestAmountThatCannotBeComparedIsReported:
    """ตัวหารที่ใช้ไม่ได้ต้องกลายเป็น "ไม่สอดคล้อง" ไม่ใช่ "ผ่าน"."""

    def test_zero_amount_with_real_trade_value_is_flagged(self):
        """จ่าย 0 บาท ทั้งที่ 10 × 400 × 35 = 140,000 — ต้องถูกรายงาน."""
        rows = tracker._collect_inconsistent_rows(_frame([_row("z0", amount_thb=0.0)]))

        assert [r["tx_id"] for r in rows] == ["z0"], (
            "ยอดเงิน 0 ทำให้ตัวหารเป็น NaN → NaN > threshold = False → หลุดด่านเงียบ"
        )
        assert rows[0]["implied_amount_thb"] == pytest.approx(140_000.0)

    def test_negative_amount_is_flagged(self):
        rows = tracker._collect_inconsistent_rows(_frame([_row("n0", amount_thb=-140_000.0)]))

        assert [r["tx_id"] for r in rows] == ["n0"]

    def test_infinite_amount_is_flagged(self):
        """``inf`` ผ่านเงื่อนไข ``amount.abs() > 0`` แต่ inf/inf = NaN → หลุดด่านเหมือนกัน."""
        rows = tracker._collect_inconsistent_rows(
            _frame([_row("i0", amount_thb=float("inf"))])
        )

        assert [r["tx_id"] for r in rows] == ["i0"]

    def test_infinite_implied_is_flagged(self):
        """implied ที่คำนวณออกมาไม่ใช่ตัวเลขจริง ก็คือ "เทียบไม่ได้" เช่นกัน."""
        rows = tracker._collect_inconsistent_rows(
            _frame([_row("i1", price_usd=float("inf"))])
        )

        assert [r["tx_id"] for r in rows] == ["i1"]


class TestGapThatIsNotARealNumberIsReported:
    """ส่วนต่างที่ไม่ใช่ตัวเลขจริง = "เทียบไม่ได้เลย" ต้องเตือน ไม่ใช่ผ่าน.

    เคสเหล่านี้ ``implied`` (หรือ ``gap``) ออกมาเป็น ``NaN`` ทำให้ **ทั้ง**
    ``ratio > tol`` และ ``gap > 0`` เป็น ``False`` พร้อมกัน — รูเดียวกับที่
    ยอดเงิน 0 เคยหลุด แค่ย้ายจากตัวหารมาอยู่ที่ตัวเศษ
    """

    def test_nan_implied_from_infinite_shares_is_flagged(self):
        """``inf × 0 = NaN`` — จำนวนหุ้นพัง แต่ยอดเงินปกติดี."""
        rows = tracker._collect_inconsistent_rows(
            _frame([_row("g0", shares=float("inf"), price_usd=0.0)])
        )

        assert [r["tx_id"] for r in rows] == ["g0"], (
            "implied เป็น NaN → gap เป็น NaN → ทุกการเทียบเป็น False → หลุดด่านเงียบ"
        )

    def test_nan_implied_from_infinite_price_is_flagged(self):
        rows = tracker._collect_inconsistent_rows(
            _frame([_row("g1", shares=0.0, price_usd=float("inf"))])
        )

        assert [r["tx_id"] for r in rows] == ["g1"]

    def test_infinite_on_both_sides_is_flagged(self):
        """``inf − inf = NaN`` — ยอดเงินและ implied พังคนละทางแต่หักล้างกันพอดี."""
        rows = tracker._collect_inconsistent_rows(
            _frame([_row("g2", amount_thb=float("inf"), price_usd=float("inf"))])
        )

        assert [r["tx_id"] for r in rows] == ["g2"]

    def test_reason_blames_the_side_that_is_actually_broken(self):
        """เหตุผลต้องชี้ตัวที่พังจริง — ยอดเงิน 140,000 ปกติดี ห้ามโทษยอดเงิน.

        เหตุผลที่ชี้ผิดตัวคือการกุคำอธิบาย ผู้ใช้จะไปนั่งแก้ยอดเงินที่ถูกอยู่แล้ว
        แทนที่จะเห็นว่าจำนวนหุ้นในสมุดเป็น ``inf``
        """
        rows = tracker._collect_inconsistent_rows(
            _frame([_row("g0", shares=float("inf"), price_usd=0.0)])
        )
        reason = str(rows[0]["reason"])

        assert "nan" not in reason.lower(), f"ข้อความเตือนพิมพ์ nan ให้ผู้ใช้อ่าน: {reason}"
        assert "ยอดเงินต้องเป็นจำนวนบวก" not in reason, (
            f"ยอดเงินคือ 140,000 ซึ่งเป็นจำนวนบวก — โทษผิดตัว: {reason}"
        )
        assert "140,000.00" in reason, "ต้องบอกยอดเงินที่บันทึกไว้จริง"
        assert rows[0]["diff_pct"] is None
        assert rows[0]["implied_fx"] is None


class TestNoNonFiniteNumberEscapesInThePayload:
    """ตัวเลขในรายงานต้องเป็น ``None`` เมื่อ "ไม่ทราบ" — ห้ามเป็น ``nan``/``inf``.

    ทั้งสองค่านี้แปลงเป็น JSON ที่ถูกต้องไม่ได้ (``json.dumps`` พ่น ``NaN``/``Infinity``
    ซึ่งไม่ใช่ JSON) และหลุดไปโผล่ในตาราง ``_render_ledger_inconsistent_rows``
    บนหน้าจอผู้ใช้ — เป็นรูเดียวกับที่ ``diff_pct`` ถูกแก้ไปแล้ว แค่คนละคีย์
    """

    @staticmethod
    def _finite_or_none(value: object) -> bool:
        return value is None or math.isfinite(float(value))  # type: ignore[arg-type]

    NUMERIC_KEYS = ("amount_thb", "implied_amount_thb", "recorded_fx", "implied_fx", "diff_pct")

    @pytest.mark.parametrize(
        ("case", "overrides"),
        [
            ("implied เป็น NaN", {"shares": float("inf"), "price_usd": 0.0}),
            ("ยอดเงินเป็น inf", {"amount_thb": float("inf")}),
            ("inf ทั้งสองฝั่ง", {"amount_thb": float("inf"), "price_usd": float("inf")}),
            ("ยอดเงินเป็น 0", {"amount_thb": 0.0}),
        ],
    )
    def test_every_numeric_field_is_finite_or_none(self, case, overrides):
        rows = tracker._collect_inconsistent_rows(_frame([_row("x", **overrides)]))
        assert rows, f"{case}: ต้องถูกรายงานก่อน"

        bad = {
            key: rows[0][key]
            for key in self.NUMERIC_KEYS
            if not self._finite_or_none(rows[0][key])
        }
        assert not bad, f"{case}: ค่าที่ไม่ใช่ตัวเลขจริงหลุดออกไปเป็นตัวเลข — {bad}"


class TestUncomparableRowNeverInventsNumbers:
    """รายงานของแถวที่เทียบสัดส่วนไม่ได้ ห้ามมีตัวเลขที่กุขึ้นมา/``nan`` โผล่บนจอ."""

    @pytest.fixture()
    def zero_amount_row(self) -> dict:
        rows = tracker._collect_inconsistent_rows(_frame([_row("z0", amount_thb=0.0)]))
        assert rows, "ต้องถูกรายงานก่อนถึงจะตรวจเนื้อหารายงานได้"
        return rows[0]

    def test_diff_pct_is_none_not_nan(self, zero_amount_row):
        diff_pct = zero_amount_row["diff_pct"]
        assert diff_pct is None, "หารด้วยศูนย์ต้องเป็น None (ไม่ทราบ) ห้ามเป็น nan/ตัวเลขที่กุขึ้น"

    def test_implied_fx_is_none(self, zero_amount_row):
        assert zero_amount_row["implied_fx"] is None, (
            "อัตราที่คำนวณย้อนจากยอดเงิน 0 คือ 0.0000 ซึ่งไม่ใช่อัตราแลกเปลี่ยนจริง"
        )

    def test_reason_has_no_nan_text(self, zero_amount_row):
        reason = str(zero_amount_row["reason"])
        assert "nan" not in reason.lower(), f"ข้อความเตือนพิมพ์ nan ให้ผู้ใช้อ่าน: {reason}"
        assert "0.00" in reason, "ต้องบอกยอดที่บันทึกไว้จริง"
        assert "140,000.00" in reason, "ต้องบอกยอดที่ควรเป็นเพื่อให้ผู้ใช้ไล่ตามได้"


class TestNormalRowsAreUnchanged:
    """เคสปกติต้องไม่เปลี่ยนพฤติกรรม (ไม่ over-flag)."""

    def test_consistent_row_is_not_flagged(self):
        assert tracker._collect_inconsistent_rows(_frame([_row("ok")])) == []

    def test_wrong_fx_still_reports_percentage(self):
        rows = tracker._collect_inconsistent_rows(
            _frame([_row("w1", fx_rate_thb=33.23, amount_thb=140_210.0, fee_thb=210.0)])
        )

        assert [r["tx_id"] for r in rows] == ["w1"]
        assert rows[0]["diff_pct"] == pytest.approx(
            abs(140_210.0 - (10 * 400 * 33.23 + 210.0)) / 140_210.0 * 100.0
        )
        assert rows[0]["implied_fx"] == pytest.approx(35.0)

    def test_free_shares_with_zero_amount_and_zero_value_is_not_flagged(self):
        """ราคา 0 + ยอดเงิน 0 = ไม่มีอะไรขัดกัน (implied ก็ 0) — ห้ามเตือนมั่ว."""
        rows = tracker._collect_inconsistent_rows(
            _frame([_row("z2", shares=5.0, price_usd=0.0, amount_thb=0.0)])
        )

        assert rows == []

    def test_dividend_row_with_zero_amount_is_not_flagged(self):
        rows = tracker._collect_inconsistent_rows(
            _frame(
                [
                    _row(
                        "d1",
                        shares=0.0,
                        price_usd=0.0,
                        amount_thb=0.0,
                        tx_type=tracker.TX_DIVIDEND,
                    )
                ]
            )
        )

        assert rows == []


class TestZeroAmountRowThroughRealLedger:
    """เส้นทางจริงตั้งแต่ CSV → ``get_total_summary()`` — คำเตือนต้องถึงหน้าจอ."""

    LEDGER = HEADER + "z0,2026-03-05,VOO,10,400,35.0,0,0,ลืมกรอกยอดเงิน,buy\n"

    def test_summary_reports_the_row(self, ledger, prices):
        ledger.write_text(self.LEDGER, encoding="utf-8")
        prices({"VOO": 500.0})

        totals = tracker.get_total_summary()

        assert [r["tx_id"] for r in totals["inconsistent_rows"]] == ["z0"]
        assert totals["inconsistent_reason"], "ต้องมีข้อความไทยพร้อมแสดงบนหน้าจอ"
        assert "VOO" in totals["inconsistent_reason"]
        assert "nan" not in totals["inconsistent_reason"].lower()

    def test_row_is_warned_not_dropped(self, ledger, prices):
        """เตือนอย่างเดียว — ตัวเลขยังนับแถวนี้อยู่เหมือนเดิม (ไม่ใช่ข้อมูลไม่ครบ)."""
        ledger.write_text(self.LEDGER, encoding="utf-8")
        prices({"VOO": 500.0})

        totals = tracker.get_total_summary()

        assert totals["skipped_rows"] == [], "ข้อมูลครบ ห้ามตัดออกจากยอดรวม"
        # เงินลงทุนคิดจาก shares × price_usd × fx_rate_thb (``cost_thb``)
        # **ไม่ใช่** ``amount_thb`` — ยอด 0 ที่กรอกไว้จึงไม่โผล่ในตัวเลขไหนเลย
        # นี่คือเหตุผลที่คำเตือนคือสิ่งเดียวที่ยืนอยู่ระหว่างผู้ใช้กับสมุดที่ขัดกันเอง
        assert totals["invested_thb_all"] == pytest.approx(140_000.0)
        assert list(tracker.get_portfolio_summary()["Ticker"]) == ["VOO"]

    def test_diff_pct_survives_json_shaped_payload(self, ledger, prices):
        """``diff_pct`` ต้องเป็น ``None`` ไม่ใช่ ``nan`` — nan แปลงเป็น JSON ไม่ได้."""
        ledger.write_text(self.LEDGER, encoding="utf-8")
        prices({"VOO": 500.0})

        row = tracker.get_total_summary()["inconsistent_rows"][0]

        assert row["diff_pct"] is None or math.isfinite(float(row["diff_pct"]))
