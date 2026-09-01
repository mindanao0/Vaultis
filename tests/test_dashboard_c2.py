# -*- coding: utf-8 -*-
"""หน้าจอต้องไม่พูดสิ่งที่ตัวเลขไม่รองรับ (AUDIT_2026-08-06 หัวข้อ C2).

- H7  drift advisory ต้องเทียบกับเป้าหมาย**ทั้งพอร์ต** ไม่ใช่เป้าที่ normalize ใหม่
      บนเฉพาะกองที่ถืออยู่ (กองที่ยังไม่เคยซื้อ = ขาดทั้งกอง ไม่ใช่ drift 0)
- H8  XIRR ที่เอาไปลบเงินเฟ้อ**ไทย** ต้องเป็นผลตอบแทนฐาน**เงินบาท** ไม่ใช่ฐานดอลลาร์
- C2.2 แผนจัดสรรว่างเพราะ "ดึงข้อมูลไม่ได้" ห้ามกลายเป็น "โมเดลแนะนำถือเงินสด"
- C2.3 เหตุผลของไม้ที่เทียบ VOO ไม่ได้ต้องตรงชนิด + ต้องรายงาน as-of ของราคา VOO
- ของฝากจากเฟสก่อน: คลัง alert อ่านไม่ได้ / alert ที่ตรวจไม่ได้ / แถวสมุดที่ถูก
  "ซ่อม" หรือ "ขัดกันเอง" / น้ำหนักเป้าหมายที่ถูกปรับ / FX ที่เป็นค่าสำรอง
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

app = pytest.importorskip("dashboard.app")

from alerts.price_alert import AlertStoreUnavailable  # noqa: E402
from portfolio.benchmark import xirr  # noqa: E402
from portfolio.targets import InvalidTargetWeights, TargetWeights  # noqa: E402


class _FakeSlot:
    def __init__(self, log: list) -> None:
        self._log = log

    def __enter__(self) -> "_FakeSlot":
        return self

    def __exit__(self, *_exc) -> bool:
        return False

    def __getattr__(self, name: str):
        def _call(*args, **kwargs):
            self._log.append((name, args, kwargs))
            return None

        return _call


class FakeSt:
    """แทน ``streamlit`` — เก็บทุกอย่างที่หน้าจอ "พูด" ออกมา."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.session_state: dict = {}

    def __getattr__(self, name: str):
        def _call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return None

        return _call

    def columns(self, spec, **kwargs):
        count = spec if isinstance(spec, int) else len(spec)
        self.calls.append(("columns", (spec,), kwargs))
        return [_FakeSlot(self.calls) for _ in range(count)]

    def toggle(self, *args, **kwargs):
        self.calls.append(("toggle", args, kwargs))
        return False

    def button(self, *args, **kwargs):
        self.calls.append(("button", args, kwargs))
        return False

    def container(self, *args, **kwargs):
        self.calls.append(("container", args, kwargs))
        return _FakeSlot(self.calls)

    def expander(self, *args, **kwargs):
        self.calls.append(("expander", args, kwargs))
        return _FakeSlot(self.calls)

    def spinner(self, *args, **kwargs):
        self.calls.append(("spinner", args, kwargs))
        return _FakeSlot(self.calls)

    def texts(self, *kinds: str) -> list[str]:
        return [
            str(args[0]) if args else ""
            for name, args, _kwargs in self.calls
            if name in kinds
        ]

    def all_text(self) -> str:
        """ทุกอย่างที่หน้าจอพูด — **รวมค่าของ metric/delta** ไม่ใช่แค่ป้ายชื่อ."""
        return "\n".join(
            " ".join([str(a) for a in args] + [str(v) for v in kwargs.values()])
            for _name, args, kwargs in self.calls
        )

    def metrics(self) -> list[tuple[str, str, object]]:
        rows: list[tuple[str, str, object]] = []
        for name, args, kwargs in self.calls:
            if name != "metric":
                continue
            label = str(args[0]) if args else str(kwargs.get("label", ""))
            value = str(args[1]) if len(args) > 1 else str(kwargs.get("value", ""))
            rows.append((label, value, kwargs.get("delta")))
        return rows


@pytest.fixture()
def fake_st(monkeypatch) -> FakeSt:
    fake = FakeSt()
    monkeypatch.setattr(app, "st", fake)
    return fake


# ---------------------------------------------------------------------------
# H7 — drift advisory ต้องใช้เป้าหมายของ "ทั้งพอร์ต"
# ---------------------------------------------------------------------------
FULL_TARGETS = {"VOO": 0.35, "SCHD": 0.25, "QQQM": 0.20, "XLV": 0.10, "GLDM": 0.10}
ALL_TICKERS = list(FULL_TARGETS)


def _targets_like_production(tickers=None) -> dict[str, float]:
    """เลียนแบบ ``portfolio/targets.get_target_weights`` — normalize บนเซ็ตที่ส่งเข้ามา."""
    symbols = [str(t).upper() for t in (tickers or ALL_TICKERS)]
    subset = {t: FULL_TARGETS.get(t, 0.0) for t in symbols}
    total = sum(subset.values())
    if total <= 0:
        return subset
    return {t: w / total for t, w in subset.items()}


class TestDriftAdvisoryUsesWholePortfolioTargets:
    @pytest.fixture(autouse=True)
    def _stub_targets(self, monkeypatch):
        monkeypatch.setattr(app, "get_tickers", lambda: list(ALL_TICKERS))
        monkeypatch.setattr(app, "get_target_weights", _targets_like_production)

    def test_กองที่ยังไม่เคยซื้อต้องไม่ถูกอ่านเป็น_drift_ศูนย์(self, fake_st, monkeypatch):
        holdings = pd.DataFrame(
            [
                {"Ticker": "VOO", "Current Value (THB)": 165_000.0, "Price OK": True},
                {"Ticker": "QQQM", "Current Value (THB)": 94_285.71, "Price OK": True},
            ]
        )
        monkeypatch.setattr(app, "get_portfolio_summary", lambda: holdings)

        app._render_drift_advisory()

        text = fake_st.all_text()
        assert "ใกล้เป้าหมายทุกตัว" not in text, (
            "พอร์ตถือแค่ 2 กองจาก 5 — ห้ามสรุปว่าชิดเป้าเพราะ normalize เป้าใหม่บนเซ็ตย่อย"
        )
        for missing in ("SCHD", "XLV", "GLDM"):
            assert missing in text, f"{missing} ยังไม่ถืออยู่เลย = ขาดทั้งกอง ต้องอยู่ในรายการ drift"
        assert "VOO +28.6% จากเป้า" in text or "VOO +28.6%" in text, (
            "drift ของ VOO ต้องคิดจากเป้าจริง 35% ไม่ใช่ 63.6% ที่ normalize บนเซ็ตย่อย"
        )
        assert "SCHD -25.0%" in text

    def test_พอร์ตครบทุกกองและชิดเป้ายังต้องบอกว่าชิดเป้า(self, fake_st, monkeypatch):
        holdings = pd.DataFrame(
            [
                {"Ticker": t, "Current Value (THB)": w * 100_000.0, "Price OK": True}
                for t, w in FULL_TARGETS.items()
            ]
        )
        monkeypatch.setattr(app, "get_portfolio_summary", lambda: holdings)

        app._render_drift_advisory()

        assert "ใกล้เป้าหมายทุกตัว" in fake_st.all_text()

    def test_น้ำหนักเป้าหมายในคอนฟิกผิดรูปต้องเป็นข้อความไทยไม่ใช่_traceback(
        self, fake_st, monkeypatch
    ):
        holdings = pd.DataFrame(
            [{"Ticker": "VOO", "Current Value (THB)": 100.0, "Price OK": True}]
        )
        monkeypatch.setattr(app, "get_portfolio_summary", lambda: holdings)

        def _boom(_tickers=None):
            raise InvalidTargetWeights("target_weights ติดลบ")

        monkeypatch.setattr(app, "get_target_weights", _boom)

        app._render_drift_advisory()  # ต้องไม่โยนออกมา

        text = fake_st.all_text()
        assert "สัดส่วนพอร์ตเป้าหมาย" in text or "target_weights" in text
        assert "จากเป้า" not in text


# ---------------------------------------------------------------------------
# H8 — XIRR ที่เอาไปหักเงินเฟ้อไทยต้องเป็นฐานเงินบาท
# ---------------------------------------------------------------------------
_TODAY = pd.Timestamp.today().normalize()
_BUY1 = _TODAY - pd.Timedelta(days=730)
_BUY2 = _TODAY - pd.Timedelta(days=365)

# ไม้จริง 2 ไม้: อัตราแลกเปลี่ยนวันซื้อ 35.00 / 34.69 · วันนี้ 33.05 (บาทแข็ง)
_FEE1 = 10 * 400.0 * 35.00 * 0.0015
_FEE2 = 8 * 520.0 * 34.69 * 0.0015
_AMOUNT1 = 10 * 400.0 * 35.00 + _FEE1
_AMOUNT2 = 8 * 520.0 * 34.69 + _FEE2
_TODAY_FX = 33.05
_VALUE_USD = 18 * 640.0
_VALUE_THB = _VALUE_USD * _TODAY_FX


def _benchmark_ledger() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "tx_id": "b1",
                "date": _BUY1,
                "ticker": "VOO",
                "tx_type": "buy",
                "shares": 10.0,
                "price_usd": 400.0,
                "fx_rate_thb": 35.00,
                "amount_thb": _AMOUNT1,
                "fee_thb": _FEE1,
            },
            {
                "tx_id": "b2",
                "date": _BUY2,
                "ticker": "VOO",
                "tx_type": "buy",
                "shares": 8.0,
                "price_usd": 520.0,
                "fx_rate_thb": 34.69,
                "amount_thb": _AMOUNT2,
                "fee_thb": _FEE2,
            },
        ]
    )


def _empty_dividends() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.Series(dtype="datetime64[ns]"),
            "ticker": pd.Series(dtype="object"),
            "amount_usd": pd.Series(dtype="float64"),
            "amount_thb": pd.Series(dtype="float64"),
        }
    )


def _voo_prices() -> pd.DataFrame:
    index = pd.date_range(_TODAY - pd.Timedelta(days=900), _TODAY, freq="D")
    return pd.DataFrame({"VOO": pd.Series(400.0, index=index)})


def _priced_holdings() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Ticker": "VOO",
                "Current Value (USD)": _VALUE_USD,
                "Current Value (THB)": _VALUE_THB,
                "Price OK": True,
            }
        ]
    )


@pytest.fixture()
def benchmark_stubs(monkeypatch):
    monkeypatch.setattr(app, "get_transactions", lambda *a, **k: _benchmark_ledger())
    monkeypatch.setattr(app, "get_dividends", lambda *a, **k: _empty_dividends())
    monkeypatch.setattr(app, "cached_prices", lambda *a, **k: _voo_prices())
    monkeypatch.setattr(app, "get_tickers", lambda: ["VOO"])
    monkeypatch.setattr(app, "get_thai_inflation", lambda: {"inflation_pct": 1.2})


class TestBenchmarkXirrCurrency:
    def test_real_return_ต้องคิดจากเงินบาทที่จ่ายจริง(self, fake_st, benchmark_stubs):
        thb_rate = xirr(
            [
                (_BUY1, -_AMOUNT1),
                (_BUY2, -_AMOUNT2),
                (_TODAY, _VALUE_THB),
            ]
        )
        usd_rate = xirr(
            [
                (_BUY1, -10 * 400.0),
                (_BUY2, -8 * 520.0),
                (_TODAY, _VALUE_USD),
            ]
        )
        assert thb_rate is not None and usd_rate is not None
        assert abs(usd_rate - thb_rate) * 100.0 > 1.0, "ฉากทดสอบต้องแยกสองฐานออกจากกันได้จริง"

        app._render_benchmark_section(_priced_holdings())

        text = fake_st.all_text()
        expected_real = thb_rate * 100.0 - 1.2
        wrong_real = usd_rate * 100.0 - 1.2
        assert f"real return ≈ {expected_real:+.1f}%/ปี" in text, (
            f"real return ต้องคิดจาก XIRR ฐานบาท ({thb_rate * 100:+.2f}%/ปี) "
            f"ไม่ใช่ฐานดอลลาร์ ({usd_rate * 100:+.2f}%/ปี) — ข้อความจริง: {text}"
        )
        assert f"real return ≈ {wrong_real:+.1f}%/ปี" not in text
        assert "ฐานเงินบาท" in text, "ต้องติดป้ายสกุลเงินของตัวเลขให้ผู้ใช้เห็น"

    def test_ตัวเลขฐานดอลลาร์ถ้าจะแสดงต้องติดป้ายว่าเป็นดอลลาร์(self, fake_st, benchmark_stubs):
        app._render_benchmark_section(_priced_holdings())

        text = fake_st.all_text()
        if "ฐานดอลลาร์" in text:
            assert "หักเงินเฟ้อไทย" in text
            # เงินเฟ้อไทยต้องถูกหักออกจากตัวเลขฐานบาทเท่านั้น
            usd_rate = xirr(
                [(_BUY1, -10 * 400.0), (_BUY2, -8 * 520.0), (_TODAY, _VALUE_USD)]
            )
            assert f"real return ≈ {usd_rate * 100.0 - 1.2:+.1f}%/ปี" not in text


# ---------------------------------------------------------------------------
# C2.3 — เหตุผลของไม้ที่เทียบ VOO ไม่ได้ต้องตรงชนิด
# ---------------------------------------------------------------------------
class TestBenchmarkSkipReason:
    def test_แถวสมุดเสียเองต้องไม่ถูกรายงานว่าไม่มีราคา_VOO(self, fake_st, monkeypatch):
        ledger = pd.DataFrame(
            [
                {
                    "tx_id": "ok",
                    "date": _BUY1,
                    "ticker": "VOO",
                    "tx_type": "buy",
                    "shares": 10.0,
                    "price_usd": 400.0,
                    "fx_rate_thb": 35.0,
                    "amount_thb": _AMOUNT1,
                    "fee_thb": _FEE1,
                },
                {
                    "tx_id": "bad",
                    "date": _BUY2,
                    "ticker": "VOO",
                    "tx_type": "buy",
                    "shares": 5.0,
                    "price_usd": float("nan"),  # ราคาในสมุดอ่านไม่ออก = แถวเสียเอง
                    "fx_rate_thb": 34.0,
                    "amount_thb": 1000.0,
                    "fee_thb": 1.5,
                },
            ]
        )
        monkeypatch.setattr(app, "get_transactions", lambda *a, **k: ledger)
        monkeypatch.setattr(app, "get_dividends", lambda *a, **k: _empty_dividends())
        monkeypatch.setattr(app, "cached_prices", lambda *a, **k: _voo_prices())
        monkeypatch.setattr(app, "get_tickers", lambda: ["VOO"])
        monkeypatch.setattr(app, "get_thai_inflation", lambda: None)

        app._render_benchmark_section(_priced_holdings())

        text = fake_st.all_text()
        assert "1 ไม้" in text
        assert "ไม่มีราคา VOO ณ วันซื้อ" not in text, (
            "แถวที่เสียเองถูกรายงานเป็น 'ไม่มีราคา VOO' = บอกสาเหตุผิดชนิด"
        )
        assert "สมุด" in text or "ข้อมูลในสมุด" in text

    def test_ต้องบอกวันที่ของราคา_VOO_ที่ใช้ตีมูลค่าเงา(self, fake_st, benchmark_stubs):
        app._render_benchmark_section(_priced_holdings())

        assert _TODAY.strftime("%Y-%m-%d") in fake_st.all_text(), (
            "benchmark_asof ต้องเดินทางมาถึงหน้าจอ (docstring ของ shadow_benchmark สั่งไว้)"
        )


# ---------------------------------------------------------------------------
# C2.2 — แผนว่างเพราะดึงข้อมูลไม่ได้ ≠ โมเดลแนะนำถือเงินสด
# ---------------------------------------------------------------------------
class TestEmptyAllocationReason:
    def test_ดึงข้อมูลไม่ได้ทั้งหมดห้ามกลายเป็นคำแนะนำถือเงินสด(self, fake_st):
        app.show_result(
            {
                "budget_thb": 5000.0,
                "etf_scores": [
                    {"ticker": t, "data_ok": False, "error": "no price"}
                    for t in ALL_TICKERS
                ],
                "allocation": {},
                "unallocated_thb": 0.0,
                "no_data_tickers": list(ALL_TICKERS),
                "advice_text": "",
                "ai_used": False,
            }
        )

        text = fake_st.all_text()
        # ปฏิเสธเฉพาะรูปประโยคที่เป็น "คำแนะนำ" — ประโยคที่บอกว่า "ไม่ใช่คำแนะนำให้ถือเงินสด"
        # เป็นสิ่งที่เราต้องการให้มี
        assert "แนะนำถือเงินสด" not in text, "ความล้มเหลวของการดึงข้อมูลห้ามกลายเป็นคำแนะนำการลงทุน"
        assert "ไม่ใช่คำแนะนำให้ถือเงินสด" in text
        assert "เกณฑ์" not in text, "นโยบาย DCA ปัจจุบันไม่มีเกณฑ์คะแนนตัด ETF ออกอยู่แล้ว"
        assert "ดึงข้อมูลไม่ได้" in text

    def test_งบน้อยกว่าหนึ่งก้อนต้องบอกว่าเป็นเรื่องงบไม่ใช่เรื่องข้อมูล(self, fake_st):
        app.show_result(
            {
                "budget_thb": 50.0,
                "etf_scores": [
                    {"ticker": t, "data_ok": True, "total_pct": 70.0} for t in ALL_TICKERS
                ],
                "allocation": {},
                "unallocated_thb": 50.0,
                "no_data_tickers": [],
                "advice_text": "",
                "ai_used": False,
            }
        )

        text = fake_st.all_text()
        assert "ถือเงินสด" not in text
        assert "งบ" in text
        assert "ดึงข้อมูลไม่ได้" not in text
        assert "เกณฑ์" not in text


# ---------------------------------------------------------------------------
# ของฝาก D1.1 — ผลตรวจ alert ต้องแยก "ไม่ trigger" ออกจาก "ตรวจไม่ได้"
# ---------------------------------------------------------------------------
class TestAlertCheckResult:
    def test_ตรวจไม่ได้ต้องไม่ถูกรายงานว่าไม่มีอะไรถึงเงื่อนไข(self, fake_st):
        app._render_alert_check_result(
            {
                "success": True,
                "store_error": False,
                "checked": 0,
                "triggered": [],
                "unchecked": [
                    {"id": "1", "ticker": "VOO", "reason": "ดึงราคาไม่ได้"},
                    {"id": "2", "ticker": "SCHD", "reason": "ดึงราคาไม่ได้"},
                ],
            }
        )

        text = fake_st.all_text()
        assert "ยังไม่มี Alert ที่ถึงเงื่อนไข" not in text
        assert "VOO" in text and "SCHD" in text
        assert "ตรวจไม่ได้" in text or "ตรวจไม่สำเร็จ" in text

    def test_คลังเสียต้องขึ้นเป็น_error(self, fake_st):
        app._render_alert_check_result(
            {
                "success": False,
                "store_error": True,
                "error": "ไฟล์เสีย",
                "checked": 0,
                "triggered": [],
                "unchecked": [],
            }
        )

        assert fake_st.texts("error"), "อ่านคลัง alert ไม่ได้ = error ไม่ใช่ info"
        assert "ยังไม่มี Alert ที่ถึงเงื่อนไข" not in fake_st.all_text()

    def test_ตรวจครบและไม่มีอะไร_trigger_ยังบอกได้ตามเดิม(self, fake_st):
        app._render_alert_check_result(
            {
                "success": True,
                "store_error": False,
                "checked": 3,
                "triggered": [],
                "unchecked": [],
            }
        )

        assert "ยังไม่มี Alert ที่ถึงเงื่อนไข" in fake_st.all_text()
        assert not fake_st.texts("error")


class TestPriceAlertsPageStoreFailure:
    def test_คลังอ่านไม่ได้ต้องเป็นข้อความไทยไม่ใช่_traceback(self, fake_st, monkeypatch):
        monkeypatch.setattr(app, "get_tickers", lambda: ["VOO"])

        def _boom(*_a, **_k):
            raise AlertStoreUnavailable("price_alerts.json เสีย")

        monkeypatch.setattr(app, "list_alerts", _boom)
        monkeypatch.setattr(app, "get_active_alerts_with_distance", _boom)

        app.render_price_alerts_page()  # ต้องไม่โยนออกมา

        assert fake_st.texts("error"), "อ่านคลัง alert ไม่ได้ต้องขึ้น st.error ภาษาไทย"
        assert "Alert" in fake_st.all_text()


# ---------------------------------------------------------------------------
# ของฝาก C1.2 / C1.3 — แถวที่ถูก "ซ่อม" และแถวที่ "ขัดกันเอง" ต้องถึงตาผู้ใช้
# ---------------------------------------------------------------------------
DERIVED_FIXTURE = [
    {
        "tx_id": "d1",
        "date": "2024-01-15",
        "ticker": "VOO",
        "tx_type": "buy",
        "recorded_fx": 5.0,
        "used_fx": 35.0,
        "reason": "อัตราที่บันทึกไว้ 5 ใช้ไม่ได้ — ใช้อัตราที่คำนวณย้อนจากยอดเงินบาท 35.0000 แทน",
    }
]
INCONSISTENT_FIXTURE = [
    {
        "tx_id": "i1",
        "date": "2024-01-15",
        "ticker": "VOO",
        "tx_type": "buy",
        "amount_thb": 140210.0,
        "implied_amount_thb": 133130.0,
        "recorded_fx": 33.23,
        "implied_fx": 35.0,
        "diff_pct": 5.05,
        "reason": "ยอดเงินที่บันทึกไว้ 140,210.00 บาท ไม่ตรงกับ ...",
    }
]


class TestLedgerAdvisoryRows:
    def test_แถวที่อัตราถูกคำนวณย้อนต้องแสดงบนจอ(self, fake_st):
        app._render_ledger_reports(
            {
                "skipped_rows": [],
                "skipped_reason": "",
                "derived_fx_rows": DERIVED_FIXTURE,
                "derived_fx_reason": "อัตราแลกเปลี่ยน 1 แถวถูกคำนวณย้อนจากยอดเงินบาท",
                "inconsistent_rows": [],
                "inconsistent_reason": "",
            }
        )

        text = fake_st.all_text()
        assert "คำนวณย้อน" in text
        tables = [c for c in fake_st.calls if c[0] == "dataframe"]
        assert tables, "ต้องมีตารางรายแถว ไม่ใช่แค่บรรทัดสรุป"
        assert "d1" in tables[0][1][0].to_string()

    def test_แถวที่ยอดเงินขัดกับอัตราต้องแสดงบนจอ(self, fake_st):
        app._render_ledger_reports(
            {
                "skipped_rows": [],
                "skipped_reason": "",
                "derived_fx_rows": [],
                "derived_fx_reason": "",
                "inconsistent_rows": INCONSISTENT_FIXTURE,
                "inconsistent_reason": "ยอดเงินบาทของ 1 แถวไม่ตรงกับ จำนวนหุ้น × ราคา × อัตราแลกเปลี่ยน",
            }
        )

        text = fake_st.all_text()
        assert "ไม่ตรงกับ" in text
        tables = [c for c in fake_st.calls if c[0] == "dataframe"]
        assert tables and "i1" in tables[0][1][0].to_string()

    def test_ไม่มีอะไรผิดปกติต้องเงียบสนิท(self, fake_st):
        app._render_ledger_reports(
            {
                "skipped_rows": [],
                "derived_fx_rows": [],
                "inconsistent_rows": [],
            }
        )

        assert fake_st.calls == []


# ---------------------------------------------------------------------------
# ของฝาก B10 / B9 — น้ำหนักเป้าหมายที่ถูกปรับ และ FX ที่เป็นค่าสำรอง
# ---------------------------------------------------------------------------
class TestSettingsTargetWeightNotes:
    def test_น้ำหนักที่ถูกปรับต้องมีคำเตือน(self, fake_st, monkeypatch):
        status = TargetWeights(
            weights={"VOO": 0.7, "GLDM": 0.3},
            profile="moderate",
            configured={"VOO": 0.7, "GLDM": 0.3},
            source={"VOO": "custom", "GLDM": "custom"},
            notes=["ผลรวมน้ำหนักที่ตั้งไว้ไม่เท่ากับ 1.0 — ปรับสัดส่วนให้รวมเป็น 1.0 แล้ว"],
            adjusted=True,
        )
        monkeypatch.setattr(app, "get_target_weights_with_status", lambda tickers: status)

        app._render_target_weights_table(["VOO", "GLDM"], {"VOO": 0.5, "GLDM": 0.5})

        assert fake_st.texts("warning"), "น้ำหนักที่ถูกปรับต้องเตือน ไม่ใช่แสดงเลขที่ปรับแล้วเฉย ๆ"
        assert "ปรับสัดส่วน" in fake_st.all_text()

    def test_คอนฟิกผิดรูปต้องเป็นข้อความไทย(self, fake_st, monkeypatch):
        def _boom(_tickers):
            raise InvalidTargetWeights("target_weights มีค่าติดลบ")

        monkeypatch.setattr(app, "get_target_weights_with_status", _boom)

        app._render_target_weights_table(["VOO"], {"VOO": 1.0})

        assert fake_st.texts("error")
        assert "target_weights" in fake_st.all_text()


class TestPortfolioTotalsHonesty:
    def _summary(self, **overrides) -> dict:
        base = {
            "invested_thb_all": 282_000.0,
            "invested_thb_priced": 174_000.0,
            "total_invested_thb": 282_000.0,
            "current_value_thb": 209_349.0,
            "total_pnl_thb": 35_349.0,
            "total_return_pct": 20.32,
            "total_fee_thb": 423.0,
            "missing_prices": ["QQQM"],
            "fx_rate_thb": 33.05,
            "fx_is_live": True,
        }
        base.update(overrides)
        return base

    def test_อัตราแลกเปลี่ยนที่เป็นค่าสำรองต้องเตือน(self, fake_st):
        app._render_portfolio_totals(self._summary(fx_is_live=False), "THB", 33.05)

        assert fake_st.texts("warning"), "ค่าสำรองทำให้ตัวเลขบาททั้งก้อนคลาดเคลื่อน ต้องเตือน"
        assert "ค่าสำรอง" in fake_st.all_text()

    def test_อัตราสดไม่ต้องเตือน(self, fake_st):
        app._render_portfolio_totals(self._summary(fx_is_live=True), "THB", 33.05)

        assert not fake_st.texts("warning")

    def test_ไม่รู้มูลค่าห้ามแสดงเป็นศูนย์หรือ_nan(self, fake_st):
        app._render_portfolio_totals(
            self._summary(
                invested_thb_priced=0.0,
                current_value_thb=float("nan"),
                total_pnl_thb=float("nan"),
                total_return_pct=float("nan"),
                missing_prices=["VOO", "GLDM"],
            ),
            "THB",
            33.05,
        )

        unknown_labels = [
            (label, value, delta)
            for label, value, delta in fake_st.metrics()
            if "มูลค่าปัจจุบัน" in label or "กำไร/ขาดทุน" in label
        ]
        assert len(unknown_labels) == 2, "ต้องยังมีช่องมูลค่า/กำไรอยู่ (ไม่ใช่ซ่อนทิ้ง)"
        for label, value, delta in unknown_labels:
            assert value == "ไม่ทราบ", (
                f"{label} ที่ไม่รู้ค่าห้ามแสดงเป็น {value!r} (0.00 อ่านได้ว่าเท่าทุนพอดี)"
            )
            assert delta is None, "% ผลตอบแทนที่ไม่รู้ห้ามแสดงเป็น 0.00%"
        assert "nan" not in fake_st.all_text().lower()
        assert "ไม่ทราบ" in fake_st.all_text()

    def test_ฐานเงินลงทุนสองฐานต้องติดป้ายแยกกัน(self, fake_st):
        app._render_portfolio_totals(self._summary(), "THB", 33.05)

        text = fake_st.all_text()
        assert "174,000" in text, "ฐานที่ใช้คิดกำไรต้องปรากฏ ไม่ใช่มีแต่ยอดที่จ่ายทั้งหมด"
        assert "282,000" in text


def test_ทุกตัวเลขในเทสต์นี้เป็นจำนวนจริง():
    """กันฉากทดสอบพังเงียบ: ยอดเงินที่ประกอบเองต้อง finite."""
    assert all(math.isfinite(v) for v in (_AMOUNT1, _AMOUNT2, _VALUE_THB, _VALUE_USD))
