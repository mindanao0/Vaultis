# -*- coding: utf-8 -*-
"""AUDIT_ROUND2_2026-08-07 — เส้นทางเงินและ "เข้าหน้าจอไม่ได้" ของ dashboard.

สี่ประเด็นที่คุมไว้ที่นี่ (ทั้งหมดเป็นเรื่อง "ความล้มเหลวถูกแปลงเป็นตัวเลข" หรือ
"ค่าที่มีอยู่แต่ใช้ไม่ได้" ซึ่ง CLAUDE.md ห้ามไว้ตรง ๆ):

- CRITICAL/T7  ``portfolio.target_weights`` ผิดคีย์เดียว = แดชบอร์ดดับทั้ง 13 หน้า
               รวมหน้า Settings ที่เป็นทางเดียวในแอปที่จะไปแก้ค่านั้น
- G1           "ดึงราคาไม่สำเร็จ" ถูกรายงานเป็น "คอนฟิกผิด" ที่หน้า Scorecard
- T3           กล่อง "ชนะ VOO ไหม" เทียบพอร์ตครึ่งเดียวกับเงา VOO เต็มพอร์ต
               และพิมพ์ ``0.00`` / ``−100.00%`` เมื่อดึงราคาไม่ได้เลย
- F2           โหมดแสดงผล USD แปลงต้นทุนบาทย้อนหลังด้วยอัตราวันนี้ ⇒ ไม่ตรงกับ API
               และสามช่องบนแถวเดียวกันบวกลบไม่ลง

Streamlit ``AppTest`` ตายด้วย SIGSEGV ที่หน้า Scorecard (AUDIT_ROUND2 M) จึงใช้
สตับ ``FakeSt`` แบบเดียวกับที่รายงานตรวจใช้พิสูจน์บั๊ก — บันทึกทุกอย่างที่หน้าจอ "พูด"
"""

from __future__ import annotations

import pandas as pd
import pytest

app = pytest.importorskip("dashboard.app")

from portfolio import tracker  # noqa: E402
from portfolio.benchmark import shadow_benchmark  # noqa: E402
from portfolio.targets import (  # noqa: E402
    InvalidTargetWeights,
    NoTargetForSubset,
)


# ---------------------------------------------------------------------------
# สตับ streamlit
# ---------------------------------------------------------------------------
class _FakeSlot:
    """คอลัมน์/กล่องของ streamlit — ทุกอย่างที่ถูกเรียกบนมันถูกบันทึกรวมกับของหลัก."""

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

    def button(self, *args, **kwargs):
        self.calls.append(("button", args, kwargs))
        return False

    def toggle(self, *args, **kwargs):
        self.calls.append(("toggle", args, kwargs))
        return False

    def number_input(self, *args, **kwargs):
        self.calls.append(("number_input", args, kwargs))
        return kwargs.get("value", 0.0)

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
        return "\n".join(
            " ".join([str(a) for a in args] + [str(v) for v in kwargs.values()])
            for _name, args, kwargs in self.calls
        )

    def metrics(self) -> dict[str, tuple[str, object]]:
        """ป้าย metric → (ค่า, delta) — ค่าบนจอ ไม่ใช่แค่ว่าถูกเรียก."""
        rows: dict[str, tuple[str, object]] = {}
        for name, args, kwargs in self.calls:
            if name != "metric":
                continue
            label = str(args[0]) if args else str(kwargs.get("label", ""))
            value = str(args[1]) if len(args) > 1 else str(kwargs.get("value", ""))
            rows[label] = (value, kwargs.get("delta"))
        return rows


@pytest.fixture()
def fake_st(monkeypatch) -> FakeSt:
    fake = FakeSt()
    monkeypatch.setattr(app, "st", fake)
    return fake


# ===========================================================================
# CRITICAL / T7 — target_weights ผิดรูป ต้องไม่ล็อกผู้ใช้ออกจากทั้งแดชบอร์ด
# ===========================================================================
_BAD_WEIGHTS = InvalidTargetWeights(
    "portfolio.target_weights[SCHD] = -0.1 ติดลบ — น้ำหนักเป้าหมายต้องไม่ติดลบ"
)


def _prices_frame() -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=40, freq="D")
    return pd.DataFrame({"VOO": range(400, 440), "SCHD": range(70, 110)}, index=index)


@pytest.fixture()
def dashboard_stubs(monkeypatch, fake_st):
    """render_dashboard() ที่ทุกอย่างพร้อม ยกเว้น ``target_weights`` ที่ผิดรูป."""
    monkeypatch.setattr(app, "get_tickers", lambda: ["VOO", "SCHD"])
    monkeypatch.setattr(app, "cached_prices", lambda *a, **k: _prices_frame())
    monkeypatch.setattr(
        app,
        "load_config",
        lambda: {"display": {"default_page": "Overview"}, "dca": {"monthly_budget_thb": 5000.0}},
    )

    def _raise(*_a, **_k):
        raise _BAD_WEIGHTS

    monkeypatch.setattr(app, "get_target_weights", _raise)

    visited: list[str] = []

    def _sidebar(default_page: str) -> None:
        visited.append(f"sidebar:{default_page}")
        app.st.session_state.setdefault("page", default_page)

    monkeypatch.setattr(app, "_render_custom_sidebar", _sidebar)
    for page_func in (
        "render_settings_page",
        "render_news_page",
        "render_price_alerts_page",
        "render_portfolio_page",
        "render_scorecard_page",
    ):
        monkeypatch.setattr(
            app, page_func, (lambda name: lambda *a, **k: visited.append(name))(page_func)
        )
    return visited


class TestBrokenTargetWeightsDoesNotLockUserOut:
    """คีย์เดียวใน config.json ผิด ต้องไม่ทำให้ทั้ง 13 หน้าดับพร้อมกัน (CRITICAL)."""

    @pytest.mark.parametrize(
        "page,expected",
        [
            ("Settings", "render_settings_page"),
            ("News", "render_news_page"),
            ("Price Alerts", "render_price_alerts_page"),
            ("Portfolio", "render_portfolio_page"),
        ],
    )
    def test_ทุกหน้าที่ไม่ได้ใช้น้ำหนักเป้าหมายต้องยังเข้าได้(
        self, fake_st, dashboard_stubs, page, expected
    ):
        fake_st.session_state["page"] = page

        app.render_dashboard()

        assert any(c.startswith("sidebar:") for c in dashboard_stubs), (
            "sidebar ไม่ถูกวาด = ผู้ใช้ไปหน้าไหนไม่ได้เลย (บั๊ก CRITICAL ตัวเดิม) "
            f"— หน้าจอพูดว่า: {fake_st.all_text()!r}"
        )
        assert expected in dashboard_stubs, f"หน้า {page} ไม่ถูกเรียก"
        assert not [t for t in fake_st.texts("error") if "เกิดข้อผิดพลาดใน dashboard" in t], (
            "exception หลุดไปที่ except Exception ตัวคลุมท้ายไฟล์ = ทั้งหน้าดับ"
        )

    def test_หน้า_Settings_ต้องเข้าถึงได้เพราะเป็นทางเดียวที่จะไปแก้ค่านั้น(
        self, fake_st, dashboard_stubs
    ):
        fake_st.session_state["page"] = "Settings"

        app.render_dashboard()

        assert "render_settings_page" in dashboard_stubs

    def test_เฉพาะสองหน้าที่ใช้น้ำหนักจริงเท่านั้นที่ต้องบอกว่าคอนฟิกผิด(
        self, fake_st, dashboard_stubs
    ):
        fake_st.session_state["page"] = "Backtest"

        app.render_dashboard()

        errors = "\n".join(fake_st.texts("error"))
        assert "config.json" in errors, "หน้าที่ใช้น้ำหนักจริงต้องบอกเหตุผลของตัวเอง"
        assert "เกิดข้อผิดพลาดใน dashboard" not in errors, (
            "ต้องเป็นข้อความเฉพาะหน้า ไม่ใช่ traceback ที่กลืนทั้งแดชบอร์ด"
        )
        assert any(c.startswith("sidebar:") for c in dashboard_stubs)

    def test_หน้า_Backtest_และ_DCA_รับ_None_ได้โดยไม่ระเบิด(self, fake_st):
        app.render_backtest_page(_prices_frame(), None, ["VOO", "SCHD"], _BAD_WEIGHTS)
        app.render_dca_simulator_page(_prices_frame(), None, ["VOO", "SCHD"], _BAD_WEIGHTS)

        errors = "\n".join(fake_st.texts("error"))
        assert errors.count("config.json") == 2, "ทั้งสองหน้าต้องบอกเหตุผลของตัวเอง"
        assert "ติดลบ" in errors, "ต้องบอกด้วยว่าค่าไหนผิดเพราะอะไร"


# ===========================================================================
# G1 — "ดึงราคาไม่สำเร็จ" ห้ามถูกรายงานว่าเป็น "คอนฟิกผิด" ที่หน้า Scorecard
# ===========================================================================
_SUBSET_ERROR = NoTargetForSubset(
    "ticker ที่มีข้อมูลรอบนี้ (QQQM, XLV, GLDM) ถูกตั้งเป้าไว้ 0% ทั้งหมด",
    requested=["QQQM", "XLV", "GLDM"],
    missing=["VOO", "SCHD"],
)


def _score_row(ticker: str, *, data_ok: bool = True) -> dict:
    if not data_ok:
        return {"ticker": ticker, "data_ok": False, "total_pct": None, "error": "rate limited"}
    return {
        "ticker": ticker,
        "data_ok": True,
        "total_pct": 62.0,
        "total_score": 62,
        "max_score": 100,
        "trend_score": 30,
        "timing_score": 15,
        "momentum_score": 12,
        "dividend_score": 5,
        "momentum_available": True,
        "dividend_available": True,
        "signal": "ทยอยสะสม",
        "technical_signal": "buy",
        "technical_signal_th": "ทยอยสะสม",
        "price": 100.0,
        "ma50": 98.0,
        "ma200": 90.0,
        "rsi": 55.0,
        "return_1m_pct": 1.0,
        "return_3m_pct": 3.0,
    }


@pytest.fixture()
def scorecard_stubs(monkeypatch, fake_st):
    monkeypatch.setattr(app, "get_tickers", lambda: ["VOO", "SCHD", "QQQM", "XLV", "GLDM"])
    monkeypatch.setattr(
        app,
        "load_config",
        lambda: {"dca": {"monthly_budget_thb": 5000.0}, "display": {"default_page": "Scorecard"}},
    )
    monkeypatch.setattr(
        app,
        "cached_etf_scores",
        lambda *_a, **_k: [
            _score_row("VOO", data_ok=False),
            _score_row("SCHD", data_ok=False),
            _score_row("QQQM"),
            _score_row("XLV"),
            _score_row("GLDM"),
        ],
    )
    monkeypatch.setattr(app, "_render_drift_advisory", lambda *a, **k: None)
    monkeypatch.setattr(app, "_render_score_audit_trail", lambda *a, **k: None)


# ตาข่ายของ G1 ("ดึงราคาไม่สำเร็จ ต้องไม่ถูกรายงานว่าเป็นคอนฟิกผิด") เคยอยู่ตรงนี้ในชื่อ
# ``TestScorecardBlamesTheRightThing`` โดย monkeypatch ที่ ``app.calculate_allocation``
#
# หน้า Scorecard ย้ายไปเรียก ``calculate_allocation_with_status()`` แทน (AUDIT_ROUND2 M11 —
# ต้องได้ ``excluded``/``notes`` มาบอกชื่อ ETF ที่ไม่ได้เงิน) seam เดิมจึงไม่มีอยู่บนโมดูลแล้ว
# ทั้ง 4 เคสถูกย้ายไปผูกกับ seam ใหม่ที่
# ``tests/test_dashboard_round2_ux.py::TestScorecardStillBlamesTheRightThing`` **ครบทุกเคส**
# ไม่ได้ทำสำเนาไว้สองที่โดยตั้งใจ — เทสต์ที่ patch ชื่อที่ไม่มีอยู่จริงคือตาข่ายที่ขาดอยู่แล้ว
# แต่ยังขึ้นเขียว ซึ่งอันตรายกว่าไม่มีเทสต์


class TestSubsetErrorContract:
    """สัญญาที่หน้าจอพึ่งอยู่: ``requested``/``missing`` ต้องมีจริงและอ่านได้."""

    def test_ข้อความบนจอสร้างจาก_requested_และ_missing(self, fake_st):
        app._render_target_weights_problem(_SUBSET_ERROR)

        text = fake_st.all_text()
        assert "QQQM, XLV, GLDM" in text and "VOO, SCHD" in text

    def test_ชนิดอื่นยังใช้ข้อความคอนฟิกผิดเหมือนเดิม(self, fake_st):
        app._render_target_weights_problem(InvalidTargetWeights("พัง"))

        assert "config.json" in fake_st.all_text()


# ===========================================================================
# T3 — "ชนะ VOO ไหม" ต้องเทียบสองขาบนไม้ชุดเดียวกัน
# ===========================================================================
_TODAY = pd.Timestamp.today().normalize()
_BUY_VOO = _TODAY - pd.Timedelta(days=700)
_BUY_QQQM = _TODAY - pd.Timedelta(days=500)
_VOO_CLOSE = 400.0


def _mixed_ledger() -> pd.DataFrame:
    """ซื้อ VOO 5 หุ้น @400 และ QQQM 10 หุ้น @100 (รวม 3,000 USD)."""
    return pd.DataFrame(
        [
            {
                "tx_id": "voo1",
                "date": _BUY_VOO,
                "ticker": "VOO",
                "tx_type": "buy",
                "shares": 5.0,
                "price_usd": 400.0,
                "fx_rate_thb": 35.0,
                "amount_thb": 70000.0,
                "fee_thb": 105.0,
            },
            {
                "tx_id": "qqqm1",
                "date": _BUY_QQQM,
                "ticker": "QQQM",
                "tx_type": "buy",
                "shares": 10.0,
                "price_usd": 100.0,
                "fx_rate_thb": 36.0,
                "amount_thb": 36000.0,
                "fee_thb": 54.0,
            },
        ]
    )


def _empty_dividends() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "ticker", "amount_usd", "amount_thb"])


def _voo_prices() -> pd.DataFrame:
    index = pd.date_range(_TODAY - pd.Timedelta(days=900), _TODAY, freq="D")
    return pd.DataFrame({"VOO": pd.Series(_VOO_CLOSE, index=index)})


def _mixed_holdings(*, voo_priced: bool = True, qqqm_priced: bool = False) -> pd.DataFrame:
    nan = float("nan")
    return pd.DataFrame(
        [
            {
                "Ticker": "VOO",
                "Current Value (USD)": 2350.0 if voo_priced else nan,
                "Current Value (THB)": 77550.0 if voo_priced else nan,
                "Price OK": voo_priced,
            },
            {
                "Ticker": "QQQM",
                "Current Value (USD)": 1200.0 if qqqm_priced else nan,
                "Current Value (THB)": 39600.0 if qqqm_priced else nan,
                "Price OK": qqqm_priced,
            },
        ]
    )


@pytest.fixture()
def benchmark_stubs(monkeypatch):
    monkeypatch.setattr(app, "get_transactions", lambda *a, **k: _mixed_ledger())
    monkeypatch.setattr(app, "get_dividends", lambda *a, **k: _empty_dividends())
    monkeypatch.setattr(app, "cached_prices", lambda *a, **k: _voo_prices())
    monkeypatch.setattr(app, "get_tickers", lambda: ["VOO", "QQQM"])
    monkeypatch.setattr(app, "get_thai_inflation", lambda: None)


class TestBenchmarkSameBasis:
    def test_ขาเงา_VOO_ต้องสร้างจากไม้ของกองที่มีราคาวันนี้เท่านั้น(
        self, fake_st, benchmark_stubs
    ):
        ledger = _mixed_ledger()
        same_basis = shadow_benchmark(
            ledger[ledger["ticker"] == "VOO"], _voo_prices()["VOO"]
        )
        all_buys = shadow_benchmark(ledger, _voo_prices()["VOO"])
        assert all_buys["invested_usd"] != same_basis["invested_usd"], "ฉากต้องแยกสองฐานได้จริง"

        app._render_benchmark_section(_mixed_holdings())

        metrics = fake_st.metrics()
        actual_value, actual_delta = metrics["พอร์ตจริง (USD)"]
        shadow_value, _ = metrics["ถ้าซื้อ VOO ล้วน (USD)"]
        diff, _ = metrics["ส่วนต่าง"]

        expected_pct = (2350.0 / same_basis["invested_usd"] - 1.0) * 100.0
        wrong_pct = (2350.0 / all_buys["invested_usd"] - 1.0) * 100.0
        assert actual_value == "2,350.00"
        assert actual_delta == f"{expected_pct:+.2f}%", (
            f"% ต้องคิดจากเงินลงทุนของกองที่มีราคาเท่านั้น ({expected_pct:+.2f}%) "
            f"ไม่ใช่ของทุกไม้ ({wrong_pct:+.2f}%)"
        )
        assert shadow_value == f"{same_basis['benchmark_value_usd']:,.2f}"
        assert diff == f"{2350.0 - same_basis['benchmark_value_usd']:+,.2f} USD"

    def test_ต้องบอกว่ากองไหนถูกตัดออกจากทั้งสองขา(self, fake_st, benchmark_stubs):
        app._render_benchmark_section(_mixed_holdings())

        warnings = "\n".join(fake_st.texts("warning"))
        assert "QQQM" in warnings, "ตัดกองออกจากการเทียบแล้วไม่บอก = ผู้ใช้อ่านผลผิด"
        assert "ทั้งสองขา" in warnings

    def test_กองครบทุกตัวต้องไม่มีคำเตือนตัดกอง(self, fake_st, benchmark_stubs):
        app._render_benchmark_section(_mixed_holdings(qqqm_priced=True))

        warnings = "\n".join(fake_st.texts("warning"))
        assert "ทั้งสองขา" not in warnings
        metrics = fake_st.metrics()
        assert metrics["พอร์ตจริง (USD)"][0] == "3,550.00"

    def test_ดึงราคาไม่ได้สักกองห้ามพิมพ์_0_00_หรือ_ขาดทุน_100(
        self, fake_st, benchmark_stubs
    ):
        app._render_benchmark_section(_mixed_holdings(voo_priced=False, qqqm_priced=False))

        metrics = fake_st.metrics()
        assert "พอร์ตจริง (USD)" not in metrics, (
            "ไม่รู้มูลค่า = ไม่มีกล่องเทียบ ห้ามพิมพ์ 0.00 (AUDIT.md C1 ตัวเดิม)"
        )
        text = fake_st.all_text()
        assert "-100.00%" not in text and "0.00" not in text
        assert any("ดึงราคา" in t for t in fake_st.texts("warning"))


# ===========================================================================
# F2 — โหมด USD ต้องอ่านเลขฝั่งดอลลาร์จากแหล่งเดียวกับ API
# ===========================================================================
_SNAPSHOT_COLUMNS = [
    "Ticker",
    "Shares",
    "Avg Cost (USD)",
    "Current Price (USD)",
    "Invested (USD)",
    "Invested (THB)",
    "Current Value (USD)",
    "Current Value (THB)",
    "FX Rate (Buy)",
    "Fee (THB)",
    "P&L (USD)",
    "P&L (THB)",
    "Return (%)",
    "Price OK",
]


def _snapshot() -> pd.DataFrame:
    """สมุดเดียวกับฉากในรายงานตรวจ: VOO มีราคา · QQQM ดึงราคาไม่ได้.

    ต้นทุนคิดด้วยอัตราของ **วันที่ซื้อ** (35.00 / 36.00) ส่วนมูลค่าวันนี้คิดด้วย 33.00
    — การหารยอดบาทด้วยอัตราวันนี้จึงให้ตัวเลขที่ไม่ใช่ดอลลาร์ที่จ่ายจริง
    """
    nan = float("nan")
    return pd.DataFrame(
        [
            {
                "Ticker": "VOO",
                "Shares": 5.0,
                "Avg Cost (USD)": 400.0,
                "Current Price (USD)": 470.0,
                "Invested (USD)": 2000.0,
                "Invested (THB)": 70000.0,
                "Current Value (USD)": 2350.0,
                "Current Value (THB)": 77550.0,
                "FX Rate (Buy)": 35.0,
                "Fee (THB)": 105.0,
                "P&L (USD)": 350.0,
                "P&L (THB)": 7550.0,
                "Return (%)": 17.5,
                "Price OK": True,
            },
            {
                "Ticker": "QQQM",
                "Shares": 10.0,
                "Avg Cost (USD)": 100.0,
                "Current Price (USD)": nan,
                "Invested (USD)": 1000.0,
                "Invested (THB)": 36000.0,
                "Current Value (USD)": nan,
                "Current Value (THB)": nan,
                "FX Rate (Buy)": 36.0,
                "Fee (THB)": 54.0,
                "P&L (USD)": nan,
                "P&L (THB)": nan,
                "Return (%)": nan,
                "Price OK": False,
            },
        ],
        columns=_SNAPSHOT_COLUMNS,
    )


_TODAY_FX = 33.0


def _api_summary(snapshot: pd.DataFrame) -> dict:
    """ตัวเลขชุดเดียวกับที่ ``/api/portfolio`` ตอบ จาก snapshot เดียวกัน."""
    from backend.services import portfolio_service as psvc

    holdings = psvc._holdings_payload(snapshot)["holdings"]
    totals = tracker.get_total_summary(snapshot)
    return psvc._summary_payload(holdings, totals)


class TestUsdDisplayModeMatchesApi:
    def test_เลขฝั่งดอลลาร์ต้องตรงกับที่_API_ตอบ(self, fake_st):
        snapshot = _snapshot()
        expected = _api_summary(snapshot)
        totals = tracker.get_total_summary(snapshot)

        app._render_portfolio_totals(totals, "USD", _TODAY_FX, snapshot)

        metrics = fake_st.metrics()
        assert metrics["เงินลงทุนรวม (USD)"][0] == f"{expected['invested_usd_all']:,.2f}"
        assert metrics["มูลค่าปัจจุบัน (USD)"][0] == f"{expected['current_value_usd']:,.2f}"
        assert metrics["กำไร/ขาดทุน (USD)"][0] == f"{expected['pnl_usd']:,.2f}"

        wrong = float(totals["invested_thb_all"]) / _TODAY_FX
        assert metrics["เงินลงทุนรวม (USD)"][0] != f"{wrong:,.2f}", (
            "ยอดบาทหารอัตราวันนี้ = ต้นทุนย้อนหลังถูกแปลงด้วยอัตราผิดยุค"
        )

    def test_สามช่องบนแถวเดียวกันต้องบวกลบกันลง(self, fake_st):
        snapshot = _snapshot()
        expected = _api_summary(snapshot)

        app._render_portfolio_totals(
            tracker.get_total_summary(snapshot), "USD", _TODAY_FX, snapshot
        )

        metrics = fake_st.metrics()
        value = float(metrics["มูลค่าปัจจุบัน (USD)"][0].replace(",", ""))
        pnl = float(metrics["กำไร/ขาดทุน (USD)"][0].replace(",", ""))
        assert value - pnl == pytest.approx(expected["invested_usd_priced"]), (
            "มูลค่า − กำไร ต้องเท่ากับฐานเงินลงทุนของกองที่มีราคา"
        )

    def test_delta_ต้องเป็น_percent_ฐานดอลลาร์ไม่ใช่ฐานบาท(self, fake_st):
        snapshot = _snapshot()
        expected = _api_summary(snapshot)
        totals = tracker.get_total_summary(snapshot)
        usd_pct = expected["pnl_usd"] / expected["invested_usd_priced"] * 100.0
        thb_pct = float(totals["total_return_pct"])
        assert abs(usd_pct - thb_pct) > 1.0, "ฉากต้องแยกสองฐานได้จริง"

        app._render_portfolio_totals(totals, "USD", _TODAY_FX, snapshot)

        assert fake_st.metrics()["กำไร/ขาดทุน (USD)"][1] == f"{usd_pct:.2f}%"

    def test_คำอธิบายกระทบยอดต้องเปลี่ยนหน่วยตามโหมด(self, fake_st):
        snapshot = _snapshot()

        app._render_portfolio_totals(
            tracker.get_total_summary(snapshot), "USD", _TODAY_FX, snapshot
        )

        captions = "\n".join(fake_st.texts("caption"))
        assert "ดอลลาร์" in captions, "metric เป็น USD แต่คำอธิบายอ้างเป็นบาท = กระทบยอดไม่ได้"
        assert "บาท" not in captions

    def test_ไม่มี_snapshot_ต้องตอบว่าไม่ทราบ_ไม่ใช่แปลงด้วยอัตราวันนี้(self, fake_st):
        snapshot = _snapshot()

        app._render_portfolio_totals(tracker.get_total_summary(snapshot), "USD", _TODAY_FX)

        metrics = fake_st.metrics()
        assert metrics["เงินลงทุนรวม (USD)"][0] == app._UNKNOWN_MONEY_TEXT
        assert fake_st.texts("warning")

    def test_โหมดบาทต้องไม่เปลี่ยนพฤติกรรมเดิม(self, fake_st):
        snapshot = _snapshot()
        totals = tracker.get_total_summary(snapshot)

        app._render_portfolio_totals(totals, "THB", _TODAY_FX, snapshot)

        metrics = fake_st.metrics()
        assert metrics["เงินลงทุนรวม (THB)"][0] == f"{totals['invested_thb_all']:,.2f}"
        assert metrics["มูลค่าปัจจุบัน (THB)"][0] == f"{totals['current_value_thb']:,.2f}"
        assert metrics["กำไร/ขาดทุน (THB)"][1] == f"{totals['total_return_pct']:.2f}%"


class TestUsdTotalsRules:
    """``_usd_totals`` ต้องแยก "ไม่รู้" ออกจาก "ศูนย์" แบบเดียวกับ portfolio_service."""

    def test_สมุดว่างเป็นศูนย์จริงไม่ใช่ไม่รู้(self):
        empty = pd.DataFrame(columns=_SNAPSHOT_COLUMNS)

        assert app._usd_totals(empty)["current_value_usd"] == 0.0

    def test_ดึงราคาไม่ได้เลยสักกองคือไม่รู้ไม่ใช่ศูนย์(self):
        snapshot = _snapshot()
        snapshot["Price OK"] = False

        totals = app._usd_totals(snapshot)
        assert totals["current_value_usd"] is None
        assert totals["pnl_usd"] is None
        assert totals["invested_usd_all"] == 3000.0, "เงินที่จ่ายไปแล้วยังรู้อยู่เสมอ"

    def test_ช่องเดียวอ่านไม่ออกต้องไม่ถูกข้ามเงียบ_ๆ(self):
        snapshot = _snapshot()
        snapshot.loc[snapshot["Ticker"] == "QQQM", "Invested (USD)"] = float("nan")

        assert app._usd_totals(snapshot)["invested_usd_all"] is None
