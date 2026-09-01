# -*- coding: utf-8 -*-
"""AUDIT_ROUND2_2026-08-07 — ประเด็นหน้าจอ/การใช้งานของ dashboard (W1b).

ตรึงไว้ที่นี่:

- T10 ``page_options``  หน้า Settings มีลิสต์หน้าจอเป็นสำเนามือที่ drift ไปแล้ว
                        ⇒ ตั้ง Correlation/News เป็นหน้าเริ่มต้นไม่ได้ และค่าที่ตั้งไว้
                        ถูกเขียนทับเงียบ ๆ ตอนกด "บันทึก Settings"
- T9  plotly title      ``_apply_plotly_dark_theme`` สร้าง ``layout.title`` ที่ไม่มี
                        ``text`` ⇒ กราฟ 10 อันขึ้นคำว่า "undefined" เป็นหัวกราฟ
- T9  sidebar CSS       ป้ายหมวดทับปุ่มนำทางเพราะ selector แพ้กฎรีเซ็ต p ของ sidebar
- T9  WebSocket URL     BACKEND_URL เป็นชื่อโฮสต์ภายใน Docker แต่ถูกยัดให้เบราว์เซอร์ต่อ
- T7  fx บนหน้า Portfolio อ่าน ``display.default_fx_rate`` ตรง (ผิดกฎ CLAUDE.md)
- T7  ของฝาก AllocationPlan  ETF ที่ไม่ได้เงินต้องถูกระบุชื่อ ไม่ใช่หายเงียบใต้คำโปรย
                        "ไม่มีการตัดตัวไหนออก"
- D1.1+ ผลตรวจ alert ที่ **ผิดสัญญา** ห้ามกลายเป็น "ยังไม่มี Alert ที่ถึงเงื่อนไข"
- ราคาพัง (rate limit) ต้องไม่ล็อกผู้ใช้ออกจากหน้าที่ไม่ได้ใช้ราคา

**ทำไมไม่ใช้ ``streamlit.testing.v1.AppTest``** (AUDIT_ROUND2_2026-08-07 M):
``AppTest.from_file("dashboard/app.py")`` ที่หน้า Scorecard ทำให้ interpreter ตายด้วย
SIGSEGV (exit 139) ใน pyarrow ``convert_column`` ระหว่างแปลง display values ของ Styler
เป็น Arrow — เกิดซ้ำ 100% และ **ฆ่าโปรเซส pytest ทั้งตัว** (ไม่ใช่เทสต์แดงหนึ่งตัว) จึง
ห้ามใช้ AppTest ในชุดเทสต์นี้จนกว่าคู่ numpy 1.26/pyarrow 25 จะถูกแก้ · วิธีที่ใช้แทนคือ
สตับ ``FakeSt`` ด้านล่าง: แทนโมดูล ``st`` ในเนมสเปซของ ``dashboard.app`` แล้วบันทึกทุก
อย่างที่หน้าจอ "พูด" ออกมา — เร็ว ไม่ต้องมี runtime ของ streamlit และตรวจข้อความไทยได้ตรง ๆ
(``tests/test_dashboard_round2_money.py`` ใช้แพตเทิร์นเดียวกัน)
"""

from __future__ import annotations

import inspect
import os
import re

import plotly.express as px
import plotly.graph_objects as go
import pytest

app = pytest.importorskip("dashboard.app")

from analysis.financial_model import (  # noqa: E402
    EXCLUDED_NO_DATA,
    EXCLUDED_ROUNDED_TO_ZERO,
    EXCLUDED_ZERO_TARGET,
    AllocationPlan,
    ExcludedTicker,
)
from portfolio.targets import InvalidTargetWeights, NoTargetForSubset  # noqa: E402
from utils.fx import FxRateUnavailable  # noqa: E402


# ---------------------------------------------------------------------------
# สตับ streamlit (ดูเหตุผลที่ไม่ใช้ AppTest ใน docstring ด้านบน)
# ---------------------------------------------------------------------------
class _FakeSlot:
    """คอลัมน์/กล่อง/ฟอร์ม — ทุกอย่างที่ถูกเรียกบนมันถูกบันทึกรวมกับของหลัก."""

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
    """แทนโมดูล ``streamlit`` — เก็บทุกอย่างที่หน้าจอ "พูด" ออกมา."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.session_state: dict = {}
        # ป้ายปุ่มที่ต้องการให้ "ถูกกด" ในเทสต์นั้น ๆ
        self.pressed: set[str] = set()

    def __getattr__(self, name: str):
        def _call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return None

        return _call

    # --- widget ที่ต้องคืนค่าเหมือนของจริง ---
    @property
    def sidebar(self) -> _FakeSlot:
        self.calls.append(("sidebar", (), {}))
        return _FakeSlot(self.calls)

    def columns(self, spec, **kwargs):
        count = spec if isinstance(spec, int) else len(spec)
        self.calls.append(("columns", (spec,), kwargs))
        return [_FakeSlot(self.calls) for _ in range(count)]

    def container(self, *args, **kwargs):
        self.calls.append(("container", args, kwargs))
        return _FakeSlot(self.calls)

    def expander(self, *args, **kwargs):
        self.calls.append(("expander", args, kwargs))
        return _FakeSlot(self.calls)

    def spinner(self, *args, **kwargs):
        self.calls.append(("spinner", args, kwargs))
        return _FakeSlot(self.calls)

    def form(self, *args, **kwargs):
        self.calls.append(("form", args, kwargs))
        return _FakeSlot(self.calls)

    def form_submit_button(self, *args, **kwargs):
        self.calls.append(("form_submit_button", args, kwargs))
        return False

    def button(self, *args, **kwargs):
        self.calls.append(("button", args, kwargs))
        label = str(args[0]) if args else str(kwargs.get("label", ""))
        return label in self.pressed

    def selectbox(self, label, options, index=0, **kwargs):
        options = list(options)
        self.calls.append(("selectbox", (label, options), {"index": index, **kwargs}))
        return options[index] if options else None

    def radio(self, label, options=(), index=0, **kwargs):
        options = list(options) or list(kwargs.get("options") or [])
        self.calls.append(("radio", (label, options), {"index": index, **kwargs}))
        return options[index] if options else None

    def checkbox(self, label, value=False, **kwargs):
        self.calls.append(("checkbox", (label,), {"value": value, **kwargs}))
        return bool(value)

    def number_input(self, *args, **kwargs):
        self.calls.append(("number_input", args, kwargs))
        return kwargs.get("value", 0.0)

    def text_input(self, *args, **kwargs):
        self.calls.append(("text_input", args, kwargs))
        return str(kwargs.get("value", ""))

    def toggle(self, *args, **kwargs):
        self.calls.append(("toggle", args, kwargs))
        return False

    # --- ตัวช่วยอ่านผล ---
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

    def button_labels(self) -> list[str]:
        return [
            str(args[0]) if args else str(kwargs.get("label", ""))
            for name, args, kwargs in self.calls
            if name == "button"
        ]


@pytest.fixture()
def fake_st(monkeypatch) -> FakeSt:
    fake = FakeSt()
    monkeypatch.setattr(app, "st", fake)
    return fake


# ===========================================================================
# T10 — รายชื่อหน้าจอต้องมีแหล่งเดียว (NAV_GROUPS)
# ===========================================================================
def _full_config(default_page: str = "Overview") -> dict:
    return {
        "dca": {"monthly_budget_thb": 5000.0, "day_of_month": 1},
        "etf": {"tickers": ["VOO", "SCHD"]},
        "portfolio": {"risk_profile": "moderate", "target_weights": {}},
        "notifications": {
            "discord_webhook_url": "",
            "weekly_summary": True,
            "dca_reminder": True,
            "rsi_alert": True,
        },
        "display": {"default_page": default_page, "currency": "THB", "default_fx_rate": 33.5},
    }


@pytest.fixture()
def settings_stubs(monkeypatch, fake_st):
    """หน้า Settings แบบไม่แตะดิสก์/เน็ต — คืนลิสต์ที่ ``save_config`` ได้รับจริง."""
    saved: list[dict] = []
    monkeypatch.setattr(app, "get_tickers", lambda: ["VOO", "SCHD"])
    monkeypatch.setattr(app, "get_risk_profile", lambda: "moderate")
    monkeypatch.setattr(app, "_render_target_weights_table", lambda *a, **k: None)
    monkeypatch.setattr(app, "save_config", lambda cfg: saved.append(cfg))
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://example.invalid/hook")
    return saved


class TestNavigationHasOneSource:
    """แถบข้าง + ตัวเลือกหน้าเริ่มต้น ต้องมาจาก ``NAV_GROUPS`` ชุดเดียวกัน."""

    def test_ปุ่มในแถบข้างต้องตรงกับ_NAV_ITEMS_ทุกตัว(self, fake_st):
        app._render_custom_sidebar("Overview")

        assert fake_st.button_labels() == app.NAV_ITEMS, (
            "ปุ่มนำทางถูกฮาร์ดโค้ดแยกจาก NAV_GROUPS = มีลิสต์หน้าจอสองชุดอีกครั้ง"
        )

    def test_ตัวเลือกหน้าเริ่มต้นในหน้า_Settings_ต้องเท่ากับ_NAV_ITEMS(
        self, fake_st, settings_stubs, monkeypatch
    ):
        monkeypatch.setattr(app, "load_config", lambda: _full_config("Overview"))

        app.render_settings_page()

        options = [
            args[1]
            for name, args, _k in fake_st.calls
            if name == "selectbox" and "หน้าเริ่มต้น" in str(args[0])
        ]
        assert options, "ไม่พบ selectbox หน้าเริ่มต้น"
        assert options[0] == app.NAV_ITEMS, (
            "ลิสต์หน้าจอในหน้า Settings ไม่ตรงกับแถบข้าง — เคส Correlation/News หายไปแบบเดิม"
        )

    @pytest.mark.parametrize("saved_page", ["Correlation", "News"])
    def test_หน้าที่เคยตกหล่นต้องตั้งเป็นหน้าเริ่มต้นได้และไม่ถูกเขียนทับ(
        self, fake_st, settings_stubs, monkeypatch, saved_page
    ):
        monkeypatch.setattr(app, "load_config", lambda: _full_config(saved_page))
        fake_st.pressed.add("บันทึก Settings")

        app.render_settings_page()

        assert settings_stubs, "ไม่ได้เรียก save_config"
        assert settings_stubs[-1]["display"]["default_page"] == saved_page, (
            f"กดบันทึกแล้ว default_page ถูกเปลี่ยนจาก {saved_page} เงียบ ๆ "
            "— ค่าที่ผู้ใช้ตั้งไว้หายโดยไม่มีคำเตือน"
        )
        assert not fake_st.texts("warning"), "ค่านี้ถูกต้อง ไม่ควรมีคำเตือน"

    def test_ค่าที่บันทึกไว้ไม่มีในเมนูแล้วต้องเตือนก่อนเขียนทับ(
        self, fake_st, settings_stubs, monkeypatch
    ):
        monkeypatch.setattr(app, "load_config", lambda: _full_config("หน้าที่ถูกลบไปแล้ว"))

        app.render_settings_page()

        warning = "\n".join(fake_st.texts("warning"))
        assert "หน้าที่ถูกลบไปแล้ว" in warning, "ต้องบอกค่าเดิมที่เจอ"
        assert "เขียนทับ" in warning, "ต้องบอกว่ากดบันทึกแล้วค่าเดิมจะหาย"


# ===========================================================================
# T9 — หัวกราฟ "undefined"
# ===========================================================================
class TestPlotlyTitleNeverUndefined:
    def _layout_title(self, fig: go.Figure):
        return fig.to_plotly_json()["layout"].get("title")

    def test_กราฟที่ไม่ได้ตั้งชื่อต้องไม่มี_title_ที่ขาด_text(self):
        fig = px.line(x=[1, 2, 3], y=[1, 2, 3])

        app._apply_plotly_dark_theme(fig)

        title = self._layout_title(fig)
        assert not title or title.get("text"), (
            "layout.title ที่มีแต่ font ทำให้ plotly.js พิมพ์คำว่า 'undefined' เป็นหัวกราฟ "
            f"— ได้ {title!r}"
        )

    def test_กราฟที่ตั้งชื่อไว้ต้องยังได้ทั้งชื่อและฟอนต์ธีมมืด(self):
        fig = px.pie(names=["VOO", "SCHD"], values=[1, 2], title="น้ำหนักเดือนนี้ (บาท)")

        app._apply_plotly_dark_theme(fig)

        title = self._layout_title(fig)
        assert title and title.get("text") == "น้ำหนักเดือนนี้ (บาท)"
        assert title["font"]["color"] == app.THEME["text_primary"]

    def test_ทุกกราฟบนหน้าจอผ่านฟังก์ชันเดียวกันนี้จึงต้องปลอดภัยทั้งหมด(self):
        for fig in (
            go.Figure(),
            px.bar(x=["VOO"], y=[1]),
            px.imshow([[1, 0], [0, 1]]),
        ):
            app._apply_plotly_dark_theme(fig)
            title = fig.to_plotly_json()["layout"].get("title")
            assert not title or title.get("text")


# ===========================================================================
# T9 — ป้ายหมวดใน sidebar ทับปุ่ม
# ===========================================================================
def _css_rules(css: str) -> list[tuple[str, str]]:
    """(selector, body) ของทุกกฎ — ตัดคอมเมนต์ทิ้งก่อน ไม่งั้น selector ที่ยกมาอธิบาย
    ในคอมเมนต์จะถูกนับเป็นส่วนหนึ่งของ selector จริงแล้วเทสต์เขียวทั้งที่ควรแดง."""
    css = re.sub(r"/\*.*?\*/", " ", css, flags=re.S)
    return [
        (m.group(1).strip(), m.group(2))
        for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css)
    ]


def _specificity(selector: str) -> tuple[int, int]:
    """ค่าคร่าว ๆ (คลาส/แอตทริบิวต์, ชื่อแท็ก) — พอสำหรับเทียบสองกฎนี้."""
    classes = len(re.findall(r"\.[a-zA-Z_-]+", selector)) + len(re.findall(r"\[[^\]]+\]", selector))
    tags = len(re.findall(r"(?:^|\s|>)([a-zA-Z]+)(?![\w-]*\()", selector))
    return classes, tags


class TestSidebarLabelDoesNotOverlap:
    @pytest.fixture()
    def css(self, fake_st) -> str:
        app._inject_premium_theme()
        return "\n".join(fake_st.texts("markdown"))

    def test_กฎของป้ายหมวดต้องชนะกฎรีเซ็ต_p_ของ_sidebar(self, css):
        rules = _css_rules(css)
        reset = [s for s, _b in rules if "stMarkdownContainer" in s and s.rstrip().endswith(" p")]
        nav = [s for s, _b in rules if "nav-group" in s]
        assert reset and nav, "ไม่พบกฎที่ต้องเทียบ"
        assert _specificity(nav[0]) >= _specificity(reset[0]), (
            f"selector ของป้ายหมวด {nav[0]!r} แพ้กฎรีเซ็ต {reset[0]!r} "
            "⇒ margin/padding ถูกล้างเป็น 0 แล้วป้ายไปทับตัวอักษรของปุ่มบรรทัดถัดไป "
            "(!important เท่ากันทั้งคู่ ตัดสินกันที่ specificity)"
        )

    @pytest.mark.parametrize("klass", ["nav-group", "logo"])
    def test_ป้ายต้องมีระยะห่างด้านล่างจริง(self, css, klass):
        body = [b for s, b in _css_rules(css) if klass in s][0]
        margin = re.search(r"margin:\s*([^;!]+)", body)
        padding = re.search(r"padding:\s*([^;!]+)", body)
        gap_values = []
        for match in (margin, padding):
            if not match:
                continue
            parts = match.group(1).split()
            # margin/padding shorthand: bottom = ตัวที่ 3 (4 ค่า) หรือตัวที่ 1 (2 ค่า)
            bottom = parts[2] if len(parts) >= 3 else parts[0]
            gap_values.append(bottom)
        assert any(v not in ("0", "0px") for v in gap_values), (
            f".{klass} ไม่มีระยะห่างด้านล่าง — ป้ายจะชนปุ่มบรรทัดถัดไป (gap ของ sidebar = 0rem)"
        )


# ===========================================================================
# T9 — WebSocket URL ฝั่งเบราว์เซอร์
# ===========================================================================
class TestBrowserReachableWebSocketUrl:
    def test_ชื่อ_service_ของ_docker_ต้องไม่ถูกส่งให้เบราว์เซอร์ต่อ(self, monkeypatch):
        monkeypatch.delenv("VAULTIS_WS_URL", raising=False)
        monkeypatch.setattr(app, "BACKEND_URL", "http://backend:8000")

        url, note = app._ws_prices_url_with_status()

        assert "//backend:" not in url, (
            "ws://backend:8000 เบราว์เซอร์บนโฮสต์ resolve ไม่ได้ (ERR_NAME_NOT_RESOLVED) "
            "⇒ แถบราคาขึ้น ⚠️ ครบทุกตัวตลอดเวลาในโหมด Docker"
        )
        assert url == "ws://127.0.0.1:8000/ws/prices"
        assert note and "VAULTIS_WS_URL" in note, "ต้องบอกผู้ใช้ว่าตั้งค่าอะไรถึงจะแก้ได้"

    def test_ตั้ง_VAULTIS_WS_URL_เองแล้วต้องใช้ค่านั้นตรง_ๆ(self, monkeypatch):
        monkeypatch.setenv("VAULTIS_WS_URL", "wss://vaultis.example.com/ws/prices")
        monkeypatch.setattr(app, "BACKEND_URL", "http://backend:8000")

        url, note = app._ws_prices_url_with_status()

        assert url == "wss://vaultis.example.com/ws/prices"
        assert note is None, "ผู้ใช้ตั้งเองแล้ว ไม่ต้องเตือน"

    @pytest.mark.parametrize(
        "backend,expected",
        [
            ("http://localhost:8000", "ws://localhost:8000/ws/prices"),
            ("http://127.0.0.1:8000", "ws://127.0.0.1:8000/ws/prices"),
            ("https://vaultis-backend.onrender.com", "wss://vaultis-backend.onrender.com/ws/prices"),
        ],
    )
    def test_โฮสต์ที่เบราว์เซอร์เข้าถึงได้ต้องแปลงตามเดิมและไม่มีคำเตือน(
        self, monkeypatch, backend, expected
    ):
        monkeypatch.delenv("VAULTIS_WS_URL", raising=False)
        monkeypatch.setattr(app, "BACKEND_URL", backend)

        assert app._ws_prices_url_with_status() == (expected, None)

    def test_คำเตือนต้องขึ้นบนหน้าจอ_ไม่ใช่แค่ใน_console_ของเบราว์เซอร์(
        self, fake_st, monkeypatch
    ):
        monkeypatch.delenv("VAULTIS_WS_URL", raising=False)
        monkeypatch.setattr(app, "BACKEND_URL", "http://backend:8000")
        monkeypatch.setattr(app, "components", type("C", (), {"html": staticmethod(lambda *a, **k: None)}))

        app._render_realtime_price_ticker_bar()

        assert "VAULTIS_WS_URL" in fake_st.all_text()


# ===========================================================================
# T7 — หน้า Portfolio ต้องใช้ utils/fx แหล่งเดียว
# ===========================================================================
class TestPortfolioFxSingleSource:
    def test_หน้า_Portfolio_ต้องไม่อ่าน_default_fx_rate_ตรง_ๆ(self):
        source = inspect.getsource(app.render_portfolio_page)
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )
        assert "default_fx_rate" not in code, (
            "CLAUDE.md: FX มีแหล่งเดียวคือ utils/fx.get_usdthb() — "
            "การอ่าน config โดยตรงข้าม band check 20–50 ของ _config_fallback()"
        )

    def test_FX_ใช้ไม่ได้ต้องขึ้น_error_ไทยและไม่เปิดฟอร์มบันทึกธุรกรรม(
        self, fake_st, monkeypatch
    ):
        monkeypatch.setattr(app, "load_config", lambda: _full_config())
        monkeypatch.setattr(app, "_render_pdf_export_panel", lambda *a, **k: None)

        def _raise() -> float:
            raise FxRateUnavailable("อัตราสำรอง 900.0 อยู่นอกช่วงที่ใช้ได้ 20–50")

        monkeypatch.setattr(app, "get_today_fx_rate_thb", _raise)

        app.render_portfolio_page()

        errors = "\n".join(fake_st.texts("error"))
        assert "900" in errors and "อัตราแลกเปลี่ยน" in errors, (
            "ต้องบอกสาเหตุจริงเป็นภาษาไทย ไม่ใช่ traceback หรือเงียบไปเฉย ๆ"
        )
        assert "portfolio_buy_form" not in fake_st.all_text(), (
            "ไม่มีอัตราที่เชื่อถือได้ ห้ามเปิดฟอร์มให้กรอกอัตราเดาเองแล้วบันทึกลงสมุดจริง"
        )


# ===========================================================================
# T7 (ของฝาก) — ETF ที่ไม่ได้เงินต้องถูกระบุชื่อบนหน้า Scorecard
# ===========================================================================
def _plan_with_exclusions() -> AllocationPlan:
    return AllocationPlan(
        allocation={"VOO": {"amount_thb": 5000, "percent": 100.0, "target_percent": 100.0}},
        excluded=[
            ExcludedTicker("GLDM", EXCLUDED_ZERO_TARGET, "GLDM: เป้าหมายเป็น 0% จึงไม่อยู่ในแผนเดือนนี้"),
            ExcludedTicker("XLV", EXCLUDED_ROUNDED_TO_ZERO, "XLV: ส่วนแบ่งไม่ถึงก้อนละ 100 บาท"),
            ExcludedTicker("SCHD", EXCLUDED_NO_DATA, "SCHD: ข้อมูลไม่พร้อม (rate limited)"),
        ],
        notes=["น้ำหนักที่ตั้งไว้ใช้ครบ 100% แล้ว — GLDM จึงได้ 0%"],
    )


class TestScorecardNamesWhatItDropped:
    def test_ทุกตัวที่ไม่ได้เงินต้องมีชื่อบนจอพร้อมเหตุผลของตัวเอง(self, fake_st):
        app._render_allocation_exclusions(_plan_with_exclusions(), already_named=set())

        text = fake_st.all_text()
        for ticker in ("GLDM", "XLV", "SCHD"):
            assert ticker in text, f"{ticker} หลุดแผนแบบไม่มีใครบอก"
        assert "เป้าหมาย 0%" in text, "'ตั้งใจไม่ถือ' ต้องไม่ถูกอ่านเป็น 'ข้อมูลขาด'"
        assert "ไม่ถึงหนึ่งก้อน" in text, "'งบไม่พอ' เป็นคนละเรื่องและผู้ใช้แก้ได้เอง"
        assert "น้ำหนักที่ตั้งไว้ใช้ครบ 100% แล้ว" in text, "notes จาก targets.py ต้องถึงจอ"

    def test_ตัวที่เพิ่งเตือนไปแล้วด้านบนต้องไม่ถูกพิมพ์ซ้ำ(self, fake_st):
        app._render_allocation_exclusions(_plan_with_exclusions(), already_named={"SCHD"})

        assert "ดึงข้อมูลไม่สำเร็จรอบนี้: SCHD" not in fake_st.all_text()

    def test_ไม่มีใครถูกตัดออกจึงพูดได้ว่าไม่มีใครถูกตัดออก(self, fake_st):
        app._render_allocation_exclusions(
            AllocationPlan(allocation={"VOO": {"amount_thb": 5000}}), already_named=set()
        )

        assert "ไม่มี ETF ตัวไหนถูกตัดออก" in fake_st.all_text()

    def test_หน้า_Scorecard_ต้องเรียกใช้จริง_ไม่ใช่แค่มีฟังก์ชันไว้เฉย_ๆ(
        self, fake_st, scorecard_stubs, monkeypatch
    ):
        monkeypatch.setattr(
            app, "calculate_allocation_with_status", lambda *_a, **_k: _plan_with_exclusions()
        )
        monkeypatch.setattr(app, "_render_rebalance_mode", lambda *a, **k: False)
        monkeypatch.setattr(app, "_render_execute_list", lambda *a, **k: None)

        app.render_scorecard_page()

        text = fake_st.all_text()
        assert "เป้าหมาย 0%" in text and "GLDM" in text, (
            "หน้า Scorecard ไม่ได้เอา plan.excluded ขึ้นจอ — ETF ที่ตั้งใจไม่ถือหายเงียบเหมือนเดิม"
        )
        assert "ไม่ถึงหนึ่งก้อน" in text and "XLV" in text

    def test_คำโปรยต้องไม่ประกาศลอย_ๆ_ว่าไม่ตัดตัวไหนออก(self):
        source = inspect.getsource(app.render_scorecard_page)
        assert "ไม่มีการเลือกตัวเดียวหรือตัดตัวไหนออก" not in source, (
            "คำโปรยนี้ถูกพิมพ์ทุกครั้งโดยไม่ดูแผนจริง — ขัดกับ AllocationPlan.excluded"
        )
        assert "calculate_allocation_with_status" in source, (
            "ต้องเรียกตัว _with_status ไม่งั้นเหตุผลของตัวที่ไม่ได้เงินไม่มีทางถึงจอ"
        )


@pytest.fixture()
def scorecard_stubs(monkeypatch, fake_st):
    """หน้า Scorecard แบบไม่แตะเน็ต — VOO/SCHD ดึงราคาไม่ได้, ที่เหลือปกติ."""

    def _row(ticker: str, ok: bool = True) -> dict:
        if not ok:
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
            "price": 100.0,
            "return_1m_pct": 1.0,
            "return_3m_pct": 3.0,
        }

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
            _row("VOO", ok=False),
            _row("SCHD", ok=False),
            _row("QQQM"),
            _row("XLV"),
            _row("GLDM"),
        ],
    )
    monkeypatch.setattr(app, "_render_drift_advisory", lambda *a, **k: None)
    monkeypatch.setattr(app, "_render_score_audit_trail", lambda *a, **k: None)


class TestScorecardStillBlamesTheRightThing:
    """ตาข่ายเดิมของ G1 ย้ายมาผูกกับ seam ใหม่ (``calculate_allocation_with_status``)."""

    def test_ดึงราคาไม่สำเร็จต้องไม่ถูกเขียนว่าเป็นคอนฟิกผิด(
        self, fake_st, scorecard_stubs, monkeypatch
    ):
        def _raise(*_a, **_k):
            raise NoTargetForSubset(
                "ticker ที่มีข้อมูลรอบนี้ (QQQM, XLV, GLDM) ถูกตั้งเป้าไว้ 0% ทั้งหมด",
                requested=["QQQM", "XLV", "GLDM"],
                missing=["VOO", "SCHD"],
            )

        monkeypatch.setattr(app, "calculate_allocation_with_status", _raise)

        app.render_scorecard_page()

        text = fake_st.all_text()
        assert "สัดส่วนพอร์ตเป้าหมายใน config.json ใช้ไม่ได้" not in text
        assert "ดึงราคาไม่สำเร็จ" in text
        assert "VOO, SCHD" in text

    def test_คำเตือนไม่มีข้อมูลต้องขึ้นจอถึงแม้การจัดสรรจะล้ม(
        self, fake_st, scorecard_stubs, monkeypatch
    ):
        def _raise(*_a, **_k):
            raise NoTargetForSubset(
                "ticker ที่มีข้อมูลรอบนี้ ถูกตั้งเป้าไว้ 0% ทั้งหมด",
                requested=["QQQM", "XLV", "GLDM"],
                missing=["VOO", "SCHD"],
            )

        monkeypatch.setattr(app, "calculate_allocation_with_status", _raise)

        app.render_scorecard_page()

        warnings = "\n".join(fake_st.texts("warning"))
        assert "ไม่มีข้อมูล: VOO, SCHD" in warnings
        assert "จัดสรรไม่ได้ — ไม่มี ETF ที่ข้อมูลพร้อม หรืองบเป็นศูนย์" not in warnings

    def test_หน้าไม่ตายทั้งหน้า_คะแนนที่คำนวณได้ต้องยังแสดง(
        self, fake_st, scorecard_stubs, monkeypatch
    ):
        def _raise(*_a, **_k):
            raise NoTargetForSubset(
                "ticker ที่มีข้อมูลรอบนี้ ถูกตั้งเป้าไว้ 0% ทั้งหมด",
                requested=["QQQM", "XLV", "GLDM"],
                missing=["VOO", "SCHD"],
            )

        monkeypatch.setattr(app, "calculate_allocation_with_status", _raise)

        app.render_scorecard_page()  # ต้องไม่โยนออกไปให้ except Exception ของ render_dashboard

        assert "คะแนน 0-100 แยกองค์ประกอบ" in fake_st.all_text()

    def test_คอนฟิกผิดจริงยังต้องบอกให้ไปแก้_config_json(
        self, fake_st, scorecard_stubs, monkeypatch
    ):
        def _raise(*_a, **_k):
            raise InvalidTargetWeights("portfolio.target_weights[VOO] = 'ห้าสิบ' ไม่ใช่ตัวเลข")

        monkeypatch.setattr(app, "calculate_allocation_with_status", _raise)

        app.render_scorecard_page()

        text = fake_st.all_text()
        assert "config.json" in text and "ห้าสิบ" in text


# ===========================================================================
# D1.1+ — ผลตรวจ alert ที่ผิดสัญญา / ไม่มีคลัง
# ===========================================================================
class TestAlertCheckResultContract:
    def test_ผลผิดสัญญาต้องบอกว่าไม่ทราบผล_ไม่ใช่ไม่มีอะไรถึงเงื่อนไข(self, fake_st):
        # ขาดคีย์ ``checked``/``unchecked`` — เดิม .get(...) เติมค่าให้จนอ่านเป็น "ตรวจแล้วเรียบร้อย"
        app._render_alert_check_result({"success": True, "store_error": False, "triggered": []})

        text = fake_st.all_text()
        assert "ยังไม่มี Alert ที่ถึงเงื่อนไข" not in text, (
            "ผลลัพธ์ที่ผิดสัญญาถูกอ่านเป็นคำยืนยันว่าราคายังไม่ถึง = กุข้อสรุป"
        )
        assert fake_st.texts("error"), "ไม่ทราบผล = error"
        assert "ไม่ทราบผล" in text

    def test_ไม่มีไฟล์คลังต้องแยกจากตรวจแล้วไม่มีอะไร(self, fake_st):
        app._render_alert_check_result(
            {
                "success": True,
                "store_error": False,
                "checked": 0,
                "triggered": [],
                "unchecked": [],
                "store_status": {
                    "status": "missing",
                    "path": "/app/alerts/data/price_alerts.json",
                    "pending": None,
                    "triggered": None,
                    "error": None,
                },
            }
        )

        text = fake_st.all_text()
        assert "ยังไม่มี Alert ที่ถึงเงื่อนไข" not in text
        assert "ไม่มีไฟล์คลัง" in text and "price_alerts.json" in text

    def test_คลังปกติและตรวจครบยังบอกได้ตามเดิม(self, fake_st):
        app._render_alert_check_result(
            {
                "success": True,
                "store_error": False,
                "checked": 3,
                "triggered": [],
                "unchecked": [],
                "store_status": {
                    "status": "ok",
                    "path": "/app/alerts/data/price_alerts.json",
                    "pending": 3,
                    "triggered": 0,
                    "error": None,
                },
            }
        )

        assert "ยังไม่มี Alert ที่ถึงเงื่อนไข" in fake_st.all_text()
        assert not fake_st.texts("error")


# ===========================================================================
# ราคาพังต้องไม่ล็อกผู้ใช้ออกจากหน้าที่ไม่ได้ใช้ราคา
# ===========================================================================
@pytest.fixture()
def dashboard_stubs(monkeypatch, fake_st):
    """render_dashboard() ที่ ``cached_prices`` พังทุกครั้ง (yfinance rate limit)."""
    from data.fetcher import PriceDataUnavailableError

    visited: list[str] = []

    def _prices_boom(*_a, **_k):
        raise PriceDataUnavailableError("yfinance rate limited: VOO, SCHD")

    monkeypatch.setattr(app, "get_tickers", lambda: ["VOO", "SCHD"])
    monkeypatch.setattr(app, "cached_prices", _prices_boom)
    monkeypatch.setattr(app, "load_config", lambda: _full_config("Overview"))
    monkeypatch.setattr(app, "_tracked_target_weights", lambda: {"VOO": 0.5, "SCHD": 0.5})

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
        "render_dcf_analysis_page",
        "render_ai_advisor_page",
        "render_macro_page",
    ):
        monkeypatch.setattr(
            app, page_func, (lambda name: lambda *a, **k: visited.append(name))(page_func)
        )
    return visited


class TestPriceOutageDoesNotLockUserOut:
    @pytest.mark.parametrize(
        "page,expected",
        [
            ("News", "render_news_page"),
            ("Price Alerts", "render_price_alerts_page"),
            ("Settings", "render_settings_page"),
            ("Portfolio", "render_portfolio_page"),
            ("Scorecard", "render_scorecard_page"),
            ("DCF Analysis", "render_dcf_analysis_page"),
            ("AI Advisor", "render_ai_advisor_page"),
            ("Macro", "render_macro_page"),
        ],
    )
    def test_หน้าที่ไม่ได้ใช้ราคาต้องยังเข้าได้ตอน_yfinance_ล่ม(
        self, fake_st, dashboard_stubs, page, expected
    ):
        fake_st.session_state["page"] = page

        app.render_dashboard()

        assert any(c.startswith("sidebar:") for c in dashboard_stubs), (
            "sidebar ต้องถูกวาดก่อนแตะราคา ไม่งั้น rate limit ครั้งเดียวล็อกผู้ใช้ออกทั้งแอป"
        )
        assert expected in dashboard_stubs, f"หน้า {page} ไม่ถูกเรียกทั้งที่ไม่ได้ใช้ราคา"
        assert not fake_st.texts("error"), (
            f"หน้า {page} ไม่ได้ใช้ราคาย้อนหลัง ไม่ควรเห็น error ของราคา — "
            f"จอพูดว่า: {fake_st.texts('error')!r}"
        )

    def test_หน้าที่ใช้ราคาจริงยังต้องดับพร้อมบอกสาเหตุ(self, fake_st, dashboard_stubs):
        fake_st.session_state["page"] = "Overview"

        app.render_dashboard()

        errors = "\n".join(fake_st.texts("error"))
        assert "ดึงข้อมูลราคา ETF ไม่สำเร็จ" in errors, (
            "หน้าที่ใช้ราคาห้ามเดินต่อด้วยข้อมูลว่าง (AUDIT.md C1)"
        )
        assert "rate limited" in errors, "ต้องยกสาเหตุจริงมาแสดง"


# ===========================================================================
# M — ห้ามใช้ streamlit AppTest ในชุดเทสต์นี้ (มันฆ่าโปรเซส pytest ทั้งตัว)
# ===========================================================================
class TestAppTestStaysBanned:
    def test_ไม่มีไฟล์เทสต์ไหนใช้_streamlit_testing(self):
        # จับเฉพาะ "การใช้งานจริง" ไม่ใช่การพูดถึงในคอมเมนต์/docstring ที่อธิบายว่าทำไมห้ามใช้
        usage = re.compile(r"(?:^|\n)\s*(?:from|import)\s+streamlit\.testing|AppTest\s*\(")
        tests_dir = os.path.dirname(os.path.abspath(__file__))
        offenders = []
        for name in sorted(os.listdir(tests_dir)):
            if not name.endswith(".py") or name == os.path.basename(__file__):
                continue
            with open(os.path.join(tests_dir, name), encoding="utf-8") as handle:
                body = handle.read()
            if usage.search(body):
                offenders.append(name)
        assert not offenders, (
            f"{offenders} ใช้ streamlit AppTest — หน้า Scorecard ทำให้ interpreter ตายด้วย "
            "SIGSEGV ใน pyarrow (numpy 1.26 + pyarrow 25) ซึ่งฆ่าโปรเซส pytest ทั้งตัว "
            "ไม่ใช่แค่เทสต์แดงหนึ่งตัว (AUDIT_ROUND2_2026-08-07) — ใช้สตับ FakeSt แทน "
            "ดูตัวอย่างใน tests/test_dashboard_round2_ux.py"
        )
