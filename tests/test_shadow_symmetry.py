# -*- coding: utf-8 -*-
"""FIX_PLAN ข้อ 3.1 — "ชนะ VOO ไหม" ต้องเทียบสองขาบนกระแสเงินเข้า-ออกชุดเดียวกัน.

**ด่านหลักของไฟล์นี้คือความสมมาตร**: สมุดที่ซื้อ benchmark ล้วน วันเดียวกัน จำนวนเดียวกัน
ต้องได้ส่วนต่าง **0.00 เป๊ะ** ถ้าไม่ใช่ แปลว่าสองขาถูกวัดด้วยไม้บรรทัดคนละอัน — และเทสต์
ตระกูลนี้จะจับบั๊กชนิดนั้นได้ตลอดไปโดยไม่ต้องรู้ว่าบั๊กหน้าตายังไง

สามอาการที่ถูกปิดพร้อมกันด้วยโมเดลเดียว (ทุกอย่างวัดจากราคา VOO จริง 2026-08-08):

(ก) **เทียบคนละฐาน** ``cached_prices`` คืน **Adjusted Close** = total return ปันผลของ
    benchmark ถูกลงทุนต่อให้เองอยู่ในตัวเลข ขณะที่มูลค่าพอร์ตจริงคือ ``หุ้น × ราคาปิดดิบ``
    ปันผลที่ผู้ใช้รับเป็นเงินสดไม่ถูกนับกลับ · VOO 3 ปี total **79.29%** vs price **72.38%**
    ⇒ เอียงเข้าข้าง VOO **1.58 จุด/ปี ตลอดเวลา** สมุดที่ซื้อ VOO ล้วนยังโชว์ว่าแพ้ VOO
(ข) **DRIP ถูกนับเป็นเงินใหม่** ไม้ที่ซื้อด้วยเงินปันผลถูกเหมารวมเป็นเงินเข้าจากภายนอก
    ⇒ ขาเงาพองเกินจริง และตัวหารของ %พอร์ตจริงก็พองตาม — ลงโทษคนที่มีวินัยที่สุด
(ค) **ราคาหายกองเดียวทำ XIRR เพี้ยน** กระแสเงินสดนับเงินของทุกกอง แต่มูลค่าปลายทาง
    นับเฉพาะกองที่มีราคา ⇒ เงินออกมีแต่เงินกลับไม่มี = %ต่อปีต่ำกว่าจริงอย่างเป็นระบบ

โมเดลที่แก้ทั้งสามข้อ: **ตารางเงินเข้า-ออกจากภายนอกชุดเดียวกัน** — ไม้ซื้อ = เงินเข้า
(เงาซื้อ) · ปันผลที่รับ = เงินที่พอร์ตจริงคายออก (เงา**ขาย**เท่ากันวันเดียวกัน) แล้วเทียบ
เฉพาะมูลค่าปลายทาง · ไม้ DRIP กลายเป็น (ปันผลเข้า → ซื้อออก) ที่หักล้างกันพอดีในขาเงา

**เลขในฉากทดสอบไม่ใช่เลขสุ่ม** — ตัวคูณปรับราคา (adjustment factor) ที่ทำให้ Adjusted Close
เท่ากับการลงทุนปันผลต่อ (DRIP) **เป๊ะ ๆ** คือ ``f = P_ex / (P_ex + d)`` เมื่อ ``P_ex`` คือ
ราคาปิดวัน ex-date และ ``d`` คือปันผลต่อหุ้น (พิสูจน์: ถือ n หุ้นแล้ว DRIP ได้
``n(1 + d/P_ex)`` หุ้น ส่วนการมองผ่าน Adjusted Close ได้ ``n/f`` หน่วย — เท่ากันเมื่อ
``f = P_ex/(P_ex + d)``) ฉากนี้จึงพิสูจน์ความสมมาตรได้ถึงระดับทศนิยมสุดท้าย ไม่ใช่ "ใกล้เคียง"
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from portfolio.benchmark import shadow_benchmark, xirr

app = pytest.importorskip("dashboard.app")

from test_dashboard_round2_money import FakeSt  # noqa: E402

# --------------------------------------------------------------------------- #
# ฉากราคา — หนึ่งงวดปันผล ตัวเลขกลมเพื่อให้ตรวจด้วยตาได้
# --------------------------------------------------------------------------- #
_TODAY = pd.Timestamp.today().normalize()
_BUY_DAY = _TODAY - pd.Timedelta(days=730)
_EX_DAY = _TODAY - pd.Timedelta(days=365)

_PRICE_AT_BUY = 400.0
_PRICE_AT_EX = 500.0
_PRICE_TODAY = 600.0
_DIV_PER_SHARE = 10.0
#: ตัวคูณที่ทำให้ Adjusted Close ก่อน ex-date เท่ากับ DRIP เป๊ะ (ดู docstring ของโมดูล)
_ADJ_FACTOR = _PRICE_AT_EX / (_PRICE_AT_EX + _DIV_PER_SHARE)

_SHARES = 5.0
_COST_USD = _SHARES * _PRICE_AT_BUY  # 2,000
_DIVIDEND_USD = _SHARES * _DIV_PER_SHARE  # 50
_REAL_VALUE_USD = _SHARES * _PRICE_TODAY  # 3,000 — พอร์ตจริง: หุ้น × ราคาปิดดิบ
#: มูลค่าเงาถ้า **ไม่** บังคับให้คายปันผลออก = บั๊ก (ก) ในรูปตัวเลข
_SHADOW_IF_DIVIDEND_KEPT = _COST_USD / (_PRICE_AT_BUY * _ADJ_FACTOR) * _PRICE_TODAY  # 3,060

_FX = 35.0


def _voo_adjusted() -> pd.Series:
    """Adjusted Close รายวันของ benchmark — ก่อน ex-date ถูกคูณด้วย ``_ADJ_FACTOR``."""
    index = pd.date_range(_BUY_DAY - pd.Timedelta(days=30), _TODAY, freq="D")
    raw = pd.Series(_PRICE_AT_BUY, index=index, dtype=float)
    raw[index >= _EX_DAY] = _PRICE_AT_EX
    raw[index == _TODAY] = _PRICE_TODAY
    adjusted = raw.where(index >= _EX_DAY, raw * _ADJ_FACTOR)
    return adjusted.astype(float)


def _voo_prices_frame() -> pd.DataFrame:
    return pd.DataFrame({"VOO": _voo_adjusted()})


def _buy_rows(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _one_buy() -> pd.DataFrame:
    return _buy_rows([{"date": _BUY_DAY, "shares": _SHARES, "price_usd": _PRICE_AT_BUY}])


def _one_payout(amount: float = _DIVIDEND_USD, when: pd.Timestamp = _EX_DAY) -> pd.DataFrame:
    return pd.DataFrame([{"date": when, "amount_usd": amount}])


# =========================================================================== #
# ระดับฟังก์ชัน — portfolio/benchmark.shadow_benchmark
# =========================================================================== #
class TestSymmetry:
    """สมุดที่ซื้อ benchmark ล้วน ต้องได้ส่วนต่างศูนย์ — ด่านที่จับบั๊กทั้งตระกูลนี้."""

    def test_ฉากทดสอบต้องแยกสองฐานออกจากกันได้จริง(self):
        """กันเทสต์ที่ "ผ่าน" เพราะ Adjusted Close บังเอิญเท่ากับราคาดิบ."""
        adjusted = _voo_adjusted()
        assert adjusted.loc[_BUY_DAY] < _PRICE_AT_BUY, "ก่อน ex-date ต้องถูกปรับลง ไม่งั้นไม่มีอะไรให้ตรวจ"
        assert adjusted.iloc[-1] == pytest.approx(_PRICE_TODAY), (
            "แท่งล่าสุดของ Adjusted Close ต้องเท่าราคาปิดดิบ (yfinance เป็นแบบนี้จริง) "
            "ไม่งั้นมูลค่าปลายทางสองขาจะอยู่คนละฐานตั้งแต่ต้น"
        )
        assert _SHADOW_IF_DIVIDEND_KEPT == pytest.approx(_REAL_VALUE_USD + _DIVIDEND_USD * 1.2)

    def test_ซื้อ_benchmark_ล้วนแล้วคายปันผลเท่ากันต้องได้ส่วนต่างศูนย์เป๊ะ(self):
        result = shadow_benchmark(_one_buy(), _voo_adjusted(), payouts=_one_payout())
        assert result["benchmark_value_usd"] == pytest.approx(_REAL_VALUE_USD, abs=1e-9), (
            "สมุดที่ซื้อ benchmark ล้วนวันเดียวกันต้องได้มูลค่าเงาเท่าพอร์ตจริงเป๊ะ — "
            "ต่างเมื่อไรแปลว่าสองขาถูกวัดด้วยไม้บรรทัดคนละอัน"
        )
        assert result["benchmark_shares"] == pytest.approx(_SHARES, abs=1e-12)

    def test_ไม่คายปันผลออกคือบั๊กเดิม_เงาได้เปรียบฟรี(self):
        """ตรึง **ทิศทางและขนาด** ของบั๊ก (ก) ไม่ใช่แค่ "ต่างกัน"."""
        kept = shadow_benchmark(_one_buy(), _voo_adjusted())
        assert kept["benchmark_value_usd"] == pytest.approx(_SHADOW_IF_DIVIDEND_KEPT)
        assert kept["benchmark_value_usd"] > _REAL_VALUE_USD, (
            "ปันผลของ benchmark ถูกลงทุนต่อให้เองใน Adjusted Close — ไม่หักออก "
            "ขาเงาจะชนะพอร์ตที่ซื้อของเดียวกันเป๊ะ ๆ ทุกครั้ง"
        )
        assert kept["payout_usd"] == 0.0
        assert kept["net_external_usd"] == kept["invested_usd"]


class TestDripCancelsItself:
    """ไม้ DRIP = (ปันผลเข้า → ซื้อออก) วันเดียวกัน จำนวนเดียวกัน ⇒ หักล้างกันพอดี."""

    @staticmethod
    def _drip_buys() -> pd.DataFrame:
        return _buy_rows(
            [
                {"date": _BUY_DAY, "shares": _SHARES, "price_usd": _PRICE_AT_BUY},
                {
                    "date": _EX_DAY,
                    "shares": _DIVIDEND_USD / _PRICE_AT_EX,  # 0.1 หุ้น
                    "price_usd": _PRICE_AT_EX,
                },
            ]
        )

    def test_มูลค่าเงาเท่าพอร์ตจริงที่ลงทุนปันผลต่อ(self):
        result = shadow_benchmark(self._drip_buys(), _voo_adjusted(), payouts=_one_payout())
        real_value = (_SHARES + _DIVIDEND_USD / _PRICE_AT_EX) * _PRICE_TODAY  # 3,060
        assert result["benchmark_value_usd"] == pytest.approx(real_value, abs=1e-9)

    def test_เงินสุทธิจากภายนอกไม่นับไม้_DRIP_เป็นเงินใหม่(self):
        result = shadow_benchmark(self._drip_buys(), _voo_adjusted(), payouts=_one_payout())
        assert result["invested_usd"] == pytest.approx(_COST_USD + _DIVIDEND_USD)
        assert result["payout_usd"] == pytest.approx(_DIVIDEND_USD)
        assert result["net_external_usd"] == pytest.approx(_COST_USD), (
            "เงินที่ผู้ใช้ควักจากกระเป๋าจริงคือ 2,000 — ตัวหารที่เป็น 2,050 กดผลตอบแทน "
            "ของคนที่ลงทุนปันผลต่อลงโดยไม่มีเหตุผล (FIX_PLAN 3.1 ข)"
        )

    def test_ตัวหารที่พองทำให้ผลตอบแทนต่ำกว่าจริง(self):
        """เลขจริงของบั๊ก (ข) — ไม่ใช่แค่ "ค่าต่างกัน"."""
        result = shadow_benchmark(self._drip_buys(), _voo_adjusted(), payouts=_one_payout())
        real_value = (_SHARES + _DIVIDEND_USD / _PRICE_AT_EX) * _PRICE_TODAY
        correct_pct = (real_value / result["net_external_usd"] - 1.0) * 100.0
        inflated_pct = (real_value / result["invested_usd"] - 1.0) * 100.0
        assert correct_pct == pytest.approx(53.0)
        assert inflated_pct == pytest.approx(49.268292682926835)


class TestEventOrdering:
    def test_ซื้อมาก่อนปันผลในวันเดียวกัน(self):
        """วันแรกที่มีทั้งไม้ซื้อและปันผลต้องไม่ถูกอ่านว่า "ขายก่อนถือ"."""
        buys = _buy_rows([{"date": _EX_DAY, "shares": 1.0, "price_usd": _PRICE_AT_EX}])
        result = shadow_benchmark(buys, _voo_adjusted(), payouts=_one_payout(10.0, _EX_DAY))
        assert result["payout_rounds"] == 1
        assert result["benchmark_shares"] == pytest.approx((500.0 - 10.0) / 500.0)

    def test_ปันผลย้อนหลังต้องไม่ถูกจ่ายด้วยหุ้นที่ยังไม่ได้ซื้อ(self):
        """ปันผลลงวันก่อนไม้ซื้อแรก = สมุดขัดแย้งกันเอง ต้องดัง ไม่ใช่คืนหุ้นติดลบ."""
        with pytest.raises(ValueError, match="ปันผล"):
            shadow_benchmark(
                _one_buy(),
                _voo_adjusted(),
                payouts=_one_payout(50.0, _BUY_DAY - pd.Timedelta(days=1)),
            )

    def test_ปันผลเกินที่ถือต้องดังไม่ใช่หุ้นติดลบ(self):
        with pytest.raises(ValueError, match="เทียบไม่ได้"):
            shadow_benchmark(_one_buy(), _voo_adjusted(), payouts=_one_payout(999_999.0))

    def test_ปันผลที่ล้างพอร์ตพอดีไม่ใช่การขายเกิน(self):
        """เศษ float ติดลบระดับ 1e-16 ต้องไม่ถูกกล่าวหาว่าขายเกิน."""
        value_at_ex = _COST_USD / (_PRICE_AT_BUY * _ADJ_FACTOR) * _PRICE_AT_EX
        result = shadow_benchmark(_one_buy(), _voo_adjusted(), payouts=_one_payout(value_at_ex))
        assert result["benchmark_shares"] == pytest.approx(0.0, abs=1e-9)
        assert result["benchmark_shares"] >= 0.0, "มูลค่าเงาติดลบไม่มีความหมายทางการเงิน"
        assert result["benchmark_value_usd"] == pytest.approx(0.0, abs=1e-6)


class TestPayoutRowsAreReportedNotSwallowed:
    def test_แถวปันผลเสียถูกนับแยกจากไม้ซื้อเสีย(self):
        bad = pd.DataFrame(
            [
                {"date": "ไม่ใช่วันที่", "amount_usd": 10.0},
                {"date": _EX_DAY, "amount_usd": float("nan")},
                {"date": _EX_DAY, "amount_usd": -5.0},
                {"date": _EX_DAY, "amount_usd": 0.0},
            ]
        )
        result = shadow_benchmark(_one_buy(), _voo_adjusted(), payouts=bad)
        assert result["payouts_skipped_bad_row"] == 4
        assert result["payouts_skipped"] == 4
        assert result["skipped"] == 0, "แถวปันผลเสียต้องไม่ไปโผล่ในตัวนับของไม้ซื้อ"
        assert result["payout_rounds"] == 0

    def test_ปันผลก่อนช่วงที่มีราคาถูกนับเป็นไม่มีราคา(self):
        early = _one_payout(10.0, _BUY_DAY - pd.Timedelta(days=90))
        result = shadow_benchmark(_one_buy(), _voo_adjusted(), payouts=early)
        assert result["payouts_skipped_no_price"] == 1
        assert result["payouts_skipped_bad_row"] == 0

    def test_ทุกแถวปันผลต้องลงเอยที่ใช้แล้วหรือถูกนับว่าข้าม(self):
        """ไม่มีทางที่แถวปันผลจะหายไปเฉย ๆ — invariant ที่หน้าจอพึ่งพา."""
        mixed = pd.DataFrame(
            [
                {"date": _EX_DAY, "amount_usd": 10.0},
                {"date": None, "amount_usd": 10.0},
                {"date": _BUY_DAY - pd.Timedelta(days=90), "amount_usd": 10.0},
            ]
        )
        result = shadow_benchmark(_one_buy(), _voo_adjusted(), payouts=mixed)
        assert result["payout_rounds"] + result["payouts_skipped"] == len(mixed)


class TestBackwardCompatibility:
    def test_ไม่ส่ง_payouts_เท่ากับส่งตารางว่าง(self):
        without = shadow_benchmark(_one_buy(), _voo_adjusted())
        empty = shadow_benchmark(
            _one_buy(), _voo_adjusted(), payouts=pd.DataFrame(columns=["date", "amount_usd"])
        )
        for key in ("invested_usd", "benchmark_shares", "benchmark_value_usd", "rounds"):
            assert without[key] == empty[key]

    def test_คีย์ใหม่มีเสมอแม้ไม่มีปันผล(self):
        result = shadow_benchmark(_one_buy(), _voo_adjusted())
        for key in (
            "payout_usd",
            "net_external_usd",
            "payout_rounds",
            "payouts_skipped",
            "payouts_skipped_bad_row",
            "payouts_skipped_no_price",
        ):
            assert key in result, f"หน้าจออ่านคีย์ {key} — หายเมื่อไรจะได้ KeyError กลางหน้า"
        assert result["payout_usd"] == 0.0
        assert result["payout_rounds"] == 0

    def test_ปันผลไม่รบกวนตัวนับของไม้ซื้อ(self):
        with_payout = shadow_benchmark(_one_buy(), _voo_adjusted(), payouts=_one_payout())
        without = shadow_benchmark(_one_buy(), _voo_adjusted())
        for key in ("invested_usd", "rounds", "skipped", "skipped_bad_row", "skipped_no_price"):
            assert with_payout[key] == without[key]

    def test_ตัวเลขทุกตัวยัง_finite(self):
        result = shadow_benchmark(_one_buy(), _voo_adjusted(), payouts=_one_payout())
        for key in ("invested_usd", "payout_usd", "net_external_usd", "benchmark_value_usd"):
            assert math.isfinite(result[key])


class TestTimezoneMixedRows:
    def test_วันที่ติด_tz_ปนกับ_tz_naive_ต้องเรียงได้ไม่ระเบิด(self):
        """ปรับ tz ต้องเกิด **ก่อน** การเรียงเหตุการณ์ ไม่งั้นได้ TypeError กลางทาง."""
        buys = _buy_rows(
            [{"date": _BUY_DAY.tz_localize("Asia/Bangkok"), "shares": _SHARES, "price_usd": _PRICE_AT_BUY}]
        )
        payouts = pd.DataFrame([{"date": _EX_DAY, "amount_usd": _DIVIDEND_USD}])
        result = shadow_benchmark(buys, _voo_adjusted(), payouts=payouts)
        assert result["rounds"] == 1
        assert result["payout_rounds"] == 1
        assert result["benchmark_value_usd"] == pytest.approx(_REAL_VALUE_USD, abs=1e-9)


# =========================================================================== #
# ระดับหน้าจอ — dashboard._render_benchmark_section
# =========================================================================== #
def _ledger(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _voo_only_ledger() -> pd.DataFrame:
    return _ledger(
        [
            {
                "tx_id": "voo1",
                "date": _BUY_DAY,
                "ticker": "VOO",
                "tx_type": "buy",
                "shares": _SHARES,
                "price_usd": _PRICE_AT_BUY,
                "fx_rate_thb": _FX,
                "amount_thb": _COST_USD * _FX,
                "fee_thb": 0.0,
            },
            {
                "tx_id": "div1",
                "date": _EX_DAY,
                "ticker": "VOO",
                "tx_type": "dividend",
                "shares": 0.0,
                "price_usd": 0.0,
                "fx_rate_thb": _FX,
                "amount_thb": _DIVIDEND_USD * _FX,
                "fee_thb": 0.0,
            },
        ]
    )


def _voo_dividends() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": _EX_DAY,
                "ticker": "VOO",
                "amount_usd": _DIVIDEND_USD,
                "amount_thb": _DIVIDEND_USD * _FX,
                "fx_rate_thb": _FX,
            }
        ]
    )


def _voo_holdings(value_usd: float = _REAL_VALUE_USD) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Ticker": "VOO",
                "Current Value (USD)": value_usd,
                "Current Value (THB)": value_usd * _FX,
                "Price OK": True,
            }
        ]
    )


@pytest.fixture()
def fake_st(monkeypatch) -> FakeSt:
    fake = FakeSt()
    monkeypatch.setattr(app, "st", fake)
    return fake


def _stub_screen(monkeypatch, ledger: pd.DataFrame, dividends: pd.DataFrame, tickers=("VOO",)):
    monkeypatch.setattr(app, "get_transactions", lambda *a, **k: ledger)
    monkeypatch.setattr(app, "get_dividends", lambda *a, **k: dividends)
    monkeypatch.setattr(app, "cached_prices", lambda *a, **k: _voo_prices_frame())
    monkeypatch.setattr(app, "get_tickers", lambda: list(tickers))
    monkeypatch.setattr(app, "get_thai_inflation", lambda: None)


class TestScreenSymmetry:
    """สมุด VOO ล้วน + ปันผลที่บันทึกไว้ ⇒ หน้าจอต้องบอกว่าเสมอกันเป๊ะ."""

    @pytest.fixture(autouse=True)
    def _screen(self, monkeypatch):
        _stub_screen(monkeypatch, _voo_only_ledger(), _voo_dividends())

    def test_ส่วนต่างต้องเป็นศูนย์(self, fake_st):
        app._render_benchmark_section(_voo_holdings())
        metrics = fake_st.metrics()
        assert metrics["ส่วนต่าง"][0] == "+0.00 USD", (
            "ซื้อ VOO ล้วนวันเดียวกันแล้วยังแพ้/ชนะ VOO = สองขาเทียบคนละฐาน"
        )
        assert metrics["พอร์ตจริง (USD)"][0] == metrics["ถ้าซื้อ VOO ล้วน (USD)"][0]

    def test_เปอร์เซ็นต์สองช่องต้องเท่ากัน(self, fake_st):
        app._render_benchmark_section(_voo_holdings())
        metrics = fake_st.metrics()
        assert metrics["พอร์ตจริง (USD)"][1] == metrics["ถ้าซื้อ VOO ล้วน (USD)"][1]
        expected = (_REAL_VALUE_USD / (_COST_USD - _DIVIDEND_USD) - 1.0) * 100.0
        assert metrics["พอร์ตจริง (USD)"][1] == f"{expected:+.2f}%" == "+53.85%", (
            f"ฐานคือเงินสุทธิจากภายนอก {_COST_USD - _DIVIDEND_USD:,.0f} USD "
            f"(ไม้ซื้อ {_COST_USD:,.0f} − ปันผลที่รับ {_DIVIDEND_USD:,.0f}) "
            f"→ มูลค่า {_REAL_VALUE_USD:,.0f} USD"
        )

    def test_ไม่กลับไปเป็นบั๊กเดิมที่แพ้_VOO(self, fake_st):
        """ถ้าใครถอดขาปันผลออก ตัวเลขจะกลับไปเป็น −60.00 USD พอดี — ตรึงไว้."""
        app._render_benchmark_section(_voo_holdings())
        text = fake_st.all_text()
        assert "-60.00 USD" not in text and "−60.00" not in text
        assert f"{_SHADOW_IF_DIVIDEND_KEPT:,.2f}" not in text, (
            "มูลค่าเงา 3,060.00 คือค่าที่ได้จากการปล่อยให้ปันผลของ VOO ลงทุนต่อฟรี ๆ"
        )

    def test_บอกฐานที่ใช้คิดเปอร์เซ็นต์(self, fake_st):
        app._render_benchmark_section(_voo_holdings())
        captions = "\n".join(fake_st.texts("caption"))
        assert "เงินสุทธิจากภายนอก" in captions
        assert f"{_COST_USD - _DIVIDEND_USD:,.2f}" in captions


class TestScreenDrip:
    """ไม้ที่ซื้อด้วยเงินปันผลต้องไม่ถูกนับเป็นเงินใหม่จากภายนอก."""

    @pytest.fixture(autouse=True)
    def _screen(self, monkeypatch):
        drip_shares = _DIVIDEND_USD / _PRICE_AT_EX
        ledger = _voo_only_ledger()
        ledger = pd.concat(
            [
                ledger,
                pd.DataFrame(
                    [
                        {
                            "tx_id": "voo2",
                            "date": _EX_DAY,
                            "ticker": "VOO",
                            "tx_type": "buy",
                            "shares": drip_shares,
                            "price_usd": _PRICE_AT_EX,
                            "fx_rate_thb": _FX,
                            "amount_thb": _DIVIDEND_USD * _FX,
                            "fee_thb": 0.0,
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        _stub_screen(monkeypatch, ledger, _voo_dividends())

    def test_ส่วนต่างยังเป็นศูนย์เมื่อลงทุนปันผลต่อ(self, fake_st):
        app._render_benchmark_section(_voo_holdings((_SHARES + 0.1) * _PRICE_TODAY))
        assert fake_st.metrics()["ส่วนต่าง"][0] == "+0.00 USD"

    def test_ตัวหารคือเงินที่ควักจากกระเป๋าไม่ใช่ผลรวมไม้ซื้อ(self, fake_st):
        app._render_benchmark_section(_voo_holdings((_SHARES + 0.1) * _PRICE_TODAY))
        metrics = fake_st.metrics()
        assert metrics["พอร์ตจริง (USD)"][1] == "+53.00%", (
            "ตัวหารต้องเป็น 2,000 (เงินใหม่จริง) ไม่ใช่ 2,050 (รวมไม้ DRIP) "
            "ซึ่งจะให้ +49.27% — ลงโทษคนที่ลงทุนปันผลต่อ"
        )
        captions = "\n".join(fake_st.texts("caption"))
        assert "2,050.00" in captions and "2,000.00" in captions, (
            "ต้องโชว์ทั้งผลรวมไม้ซื้อและเงินสุทธิ ไม่งั้นผู้ใช้ตรวจตัวหารเองไม่ได้"
        )


class TestScreenMissingPriceKeepsOneBasis:
    """(ค) ราคาหายกองเดียว — กระแสเงินสดต้องมาจากไม้ชุดเดียวกับมูลค่าปลายทาง."""

    GLDM_COST_USD = 5_000.0
    GLDM_DIVIDEND_USD = 20.0

    @pytest.fixture(autouse=True)
    def _screen(self, monkeypatch):
        ledger = pd.concat(
            [
                _voo_only_ledger(),
                pd.DataFrame(
                    [
                        {
                            "tx_id": "gldm1",
                            "date": _BUY_DAY,
                            "ticker": "GLDM",
                            "tx_type": "buy",
                            "shares": 100.0,
                            "price_usd": 50.0,
                            "fx_rate_thb": _FX,
                            "amount_thb": self.GLDM_COST_USD * _FX,
                            "fee_thb": 0.0,
                        },
                        {
                            "tx_id": "gldmdiv",
                            "date": _EX_DAY,
                            "ticker": "GLDM",
                            "tx_type": "dividend",
                            "shares": 0.0,
                            "price_usd": 0.0,
                            "fx_rate_thb": _FX,
                            "amount_thb": self.GLDM_DIVIDEND_USD * _FX,
                            "fee_thb": 0.0,
                        },
                    ]
                ),
            ],
            ignore_index=True,
        )
        dividends = pd.concat(
            [
                _voo_dividends(),
                pd.DataFrame(
                    [
                        {
                            "date": _EX_DAY,
                            "ticker": "GLDM",
                            "amount_usd": self.GLDM_DIVIDEND_USD,
                            "amount_thb": self.GLDM_DIVIDEND_USD * _FX,
                            "fx_rate_thb": _FX,
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        _stub_screen(monkeypatch, ledger, dividends, tickers=("VOO", "GLDM"))

    @staticmethod
    def _holdings() -> pd.DataFrame:
        nan = float("nan")
        return pd.concat(
            [
                _voo_holdings(),
                pd.DataFrame(
                    [
                        {
                            "Ticker": "GLDM",
                            "Current Value (USD)": nan,
                            "Current Value (THB)": nan,
                            "Price OK": False,
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )

    def test_ส่วนต่างของกองที่เหลือยังเป็นศูนย์(self, fake_st):
        app._render_benchmark_section(self._holdings())
        assert fake_st.metrics()["ส่วนต่าง"][0] == "+0.00 USD", (
            "ตัด GLDM ออกจากทั้งสองขาแล้ว ส่วนที่เหลือคือ VOO ล้วน จึงต้องเสมอกันเป๊ะ"
        )

    def test_XIRR_คิดจากกระแสเงินสดของกองที่เทียบได้เท่านั้น(self, fake_st):
        app._render_benchmark_section(self._holdings())

        expected = xirr(
            [
                (_BUY_DAY, -_COST_USD * _FX),
                (_EX_DAY, _DIVIDEND_USD * _FX),
                (pd.Timestamp.today().normalize(), _REAL_VALUE_USD * _FX),
            ]
        )
        wrong = xirr(
            [
                (_BUY_DAY, -_COST_USD * _FX),
                (_BUY_DAY, -self.GLDM_COST_USD * _FX),
                (_EX_DAY, _DIVIDEND_USD * _FX),
                (_EX_DAY, self.GLDM_DIVIDEND_USD * _FX),
                (pd.Timestamp.today().normalize(), _REAL_VALUE_USD * _FX),
            ]
        )
        assert expected is not None and wrong is not None
        assert abs(expected - wrong) > 0.05, "ฉากต้องแยกสองสูตรออกจากกันได้จริง"

        text = fake_st.all_text()
        assert f"{expected * 100.0:+.1f}%/ปี" in text, (
            f"XIRR ต้องเป็นของกองที่เทียบได้ ({expected * 100:+.1f}%/ปี) ไม่ใช่ตัวเลขที่เอา"
            f"เงินของ GLDM ไปหารกับมูลค่าปลายทางที่ไม่มี GLDM ({wrong * 100:+.1f}%/ปี)"
        )
        assert f"{wrong * 100.0:+.1f}%/ปี" not in text

    def test_บอกว่าตัดทั้งเงินและปันผลไม่ใช่แค่มูลค่าปลายทาง(self, fake_st):
        app._render_benchmark_section(self._holdings())
        captions = "\n".join(fake_st.texts("caption"))
        assert "GLDM" in captions
        assert "ปันผล" in captions
        assert "ต่ำกว่าความจริง" not in captions, (
            "ข้อความเดิมบอกว่า %ต่อปี 'ต่ำกว่าความจริง' ซึ่งเป็นการอธิบายบั๊ก ไม่ใช่แก้บั๊ก"
        )


class TestScreenRefusesWhenPayoutsCannotBeApplied:
    def test_ปันผลที่ใช้ไม่ได้ต้องไม่มีคำตัดสินชนะแพ้(self, fake_st, monkeypatch):
        broken = pd.DataFrame(
            [{"date": _EX_DAY, "ticker": "VOO", "amount_usd": float("nan"), "amount_thb": 1.0}]
        )
        _stub_screen(monkeypatch, _voo_only_ledger(), broken)
        app._render_benchmark_section(_voo_holdings())

        assert "ส่วนต่าง" not in fake_st.metrics(), (
            "ข้ามแถวปันผลแล้วเดินต่อ = ขาเงาเก็บเงินที่ต้องคายไว้กับตัว เอียงเข้าข้าง VOO ทางเดียว"
        )
        errors = "\n".join(fake_st.texts("error"))
        # ต้องเป็นข้อความที่ **แยกชนิดของความล้มเหลว** ไม่ใช่ "เทียบไม่ได้" ลอย ๆ — ผู้ใช้
        # ต้องอ่านออกว่าไปแก้ที่สมุดบัญชี ไม่ใช่รอราคาอัปเดต (กติกาเดียวกับ skipped ของไม้ซื้อ)
        assert "แถวปันผล 1 รายการ" in errors, errors
        assert "ข้อมูลในสมุดใช้ไม่ได้ 1" in errors, errors
        assert "ไม่มีราคา VOO ณ วันรับ 0" in errors, errors

    def test_ไม่มีแถวปันผลเลยต้องเตือนว่าเอียงเข้าข้าง_VOO(self, fake_st, monkeypatch):
        _stub_screen(
            monkeypatch,
            _voo_only_ledger()[lambda d: d["tx_type"] == "buy"],
            pd.DataFrame(columns=["date", "ticker", "amount_usd", "amount_thb"]),
        )
        app._render_benchmark_section(_voo_holdings())

        warnings = "\n".join(fake_st.texts("warning"))
        assert "Adjusted Close" in warnings and "เอียงเข้าข้าง VOO" in warnings, (
            "ไม่มีแถวปันผล = ขาเงาได้ปันผลลงทุนต่อฟรี ๆ ขณะที่พอร์ตจริงตีด้วยราคาล้วน "
            "ผู้ใช้ต้องรู้ว่าตัวเลขที่เห็นเอียงทางไหน"
        )
        assert fake_st.metrics()["ส่วนต่าง"][0] == f"{-_DIVIDEND_USD * 1.2:+,.2f} USD", (
            "เตือนแล้วยังต้องแสดงตัวเลขตามจริง — ส่วนต่างที่เห็นคือขนาดของความเอียงพอดี"
        )

    def test_พอร์ตอายุน้อยยังไม่ต้องเตือนเรื่องปันผล(self, fake_st, monkeypatch):
        recent = _TODAY - pd.Timedelta(days=10)
        ledger = _ledger(
            [
                {
                    "tx_id": "voo1",
                    "date": recent,
                    "ticker": "VOO",
                    "tx_type": "buy",
                    "shares": 1.0,
                    "price_usd": _PRICE_AT_EX,
                    "fx_rate_thb": _FX,
                    "amount_thb": _PRICE_AT_EX * _FX,
                    "fee_thb": 0.0,
                }
            ]
        )
        _stub_screen(
            monkeypatch, ledger, pd.DataFrame(columns=["date", "ticker", "amount_usd", "amount_thb"])
        )
        app._render_benchmark_section(_voo_holdings(_PRICE_TODAY))

        warnings = "\n".join(fake_st.texts("warning"))
        assert "Adjusted Close" not in warnings, (
            "พอร์ตอายุ 10 วันยังไม่ควรมีปันผล — เตือนตรงนี้คือเสียงรบกวน"
        )
