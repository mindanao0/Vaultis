# -*- coding: utf-8 -*-
"""AUDIT_ROUND2_2026-08-07 (รอบเก็บตก) — รายงาน "ใครหายไปจากพอร์ต" ต้องเดินทางถึงผู้ใช้จริง.

ชั้นไลบรารีถูกแก้ไปแล้วในรอบก่อน: ``portfolio/backtest.py`` คำนวณ
``coverage = {'excluded_zero_weight': [...], 'excluded_no_price': [...]}`` แล้วแนบไว้ที่
``DataFrame.attrs`` พร้อมข้อความไทยจาก ``portfolio.dca.describe_coverage()`` — **แต่
ผู้บริโภคทั้งสองทางโยนทิ้งทั้งคู่** จึงเป็นบั๊กเดิมเป๊ะ ๆ ที่เลื่อนออกไปอีกหนึ่งชั้น:

* ``backend/services/market_analysis_service.run_backtest()`` คืน
  ``frame_to_records(result)`` เฉย ๆ — ``.attrs`` ไม่ติดไปกับ ``to_dict(orient="records")``
  ยิงจริง: ``POST /api/analysis/backtest`` ด้วย ``{"VOO": 0.5, "SCHD": 0.5}`` ตอนที่ SCHD
  ไม่มีคอลัมน์ราคา ตอบ **200 พร้อมเส้นมูลค่าของ VOO 100%** และคำว่า SCHD ไม่โผล่ที่ไหน
  เลยในคำตอบ = API ตอบคำถามที่ผู้เรียกไม่ได้ถาม โดยไม่บอกว่าเปลี่ยนคำถามให้
* ``dashboard.render_backtest_page()`` ไม่เคยเรียก ``describe_coverage()`` เลย ทั้งที่หน้า
  DCA Simulator ที่อยู่ถัดลงไปไม่กี่บรรทัดในไฟล์เดียวกันเรียกอยู่ ⇒ ผู้ใช้ลากสไลเดอร์
  SCHD ลง 0% แล้วเห็นกราฟของพอร์ตที่เหลือถูกนำเสนอเป็นคำตอบของพอร์ตที่ตัวเองกรอก

เทสต์ชุดนี้จึงยิงผ่าน **route จริง** (``TestClient``) และ **ฟังก์ชันหน้าจอจริง** เท่านั้น —
``tests/test_backtest_weight_validation.py`` ตรึงชั้นไลบรารีไว้แล้ว แต่ ``portfolio.backtest
.run_backtest()`` ที่มันเรียกไม่มีผู้เรียกจริงนอกเทสต์ จึงเป็นเส้นทางที่ผู้ใช้ไม่มีวันเดิน

เก็บอีกสองเรื่องของหน้าจอไว้ที่นี่ด้วย เพราะเป็นตระกูลเดียวกัน ("ข้อความบนจอต้องตรงกับ
สิ่งที่ระบบจะทำจริง"):

* กล่องบริบท sentiment เคยเขียนว่า "รอ scheduled job รอบถัดไป" ทั้งที่งานนั้นปิดโดย
  ดีฟอลต์ (ต้องตั้ง repository variable ``VAULTIS_SENTIMENT_ENABLED=1``) และไม่มี
  scheduler ในเครื่องตัวไหนเรียก ``run_sentiment_job()`` เลย = สัญญาที่ไม่มีวันเกิด
* URL ของ WebSocket ที่เดาให้เมื่อ ``BACKEND_URL`` เป็นชื่อภายใน Docker เคยฮาร์ดโค้ด
  ``ws://`` ⇒ หน้าที่เสิร์ฟผ่าน https จะโดนเบราว์เซอร์บล็อกเพราะ mixed content และ
  ``127.0.0.1`` คือเครื่องของเบราว์เซอร์ ไม่ใช่เครื่องที่รัน Docker — ต้องพูดให้ตรง
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from portfolio.dca import NO_PRICE_KEY, ZERO_WEIGHT_KEY

app = pytest.importorskip("dashboard.app")


TICKERS = ["VOO", "SCHD"]


def _two_fund_prices() -> pd.DataFrame:
    """VOO +100% / SCHD −50% — ถ้ากองไหนหลุดเข้า/ออกจากพอร์ต ตัวเลขจะต่างกันคนละโลก."""
    index = pd.bdate_range("2020-01-01", "2021-12-31")
    return pd.DataFrame(
        {
            "VOO": np.linspace(100.0, 200.0, len(index)),
            "SCHD": np.linspace(100.0, 50.0, len(index)),
        },
        index=index,
    )


def _voo_only_prices() -> pd.DataFrame:
    """ชุดราคาที่ **ไม่มีคอลัมน์ SCHD** — จำลอง "ถือน้ำหนักอยู่แต่ดึงราคาไม่ได้"."""
    return _two_fund_prices()[["VOO"]]


# ===========================================================================
# GAP 1a — API: /api/analysis/backtest ต้องพารายงานติดไปกับผลลัพธ์
# ===========================================================================


@pytest.fixture()
def service_with_prices(monkeypatch):
    """คืนตัวช่วยชี้ ``_prices()`` ของ service ไปที่เฟรมที่เทสต์กำหนด (ไม่แตะเน็ต)."""
    from backend.services import market_analysis_service as service

    def _use(frame: pd.DataFrame):
        monkeypatch.setattr(service, "_prices", lambda: frame.copy())
        return service

    return _use


@pytest.fixture()
def api_client(monkeypatch):
    from fastapi.testclient import TestClient

    from backend.main import app as fastapi_app

    monkeypatch.setenv("VAULTIS_API_KEY", "test-key")
    # ไม่เข้า ``with`` = ไม่รัน lifespan (ไม่ปลุก APScheduler ของ backend.main)
    return TestClient(fastapi_app, raise_server_exceptions=False)


_HEADERS = {"X-API-Key": "test-key"}


def _post_backtest(client, weights: dict[str, float]) -> dict:
    response = client.post(
        "/api/analysis/backtest",
        json={"initial_capital": 10000.0, "weights": weights},
        headers=_HEADERS,
    )
    assert response.status_code == 200, response.text[:400]
    return response.json()["data"]


class TestApiCarriesCoverage:
    """หลักฐานเดิม: 200 + เส้น VOO 100% และคำว่า SCHD ไม่อยู่ในคำตอบเลย."""

    def test_held_fund_without_price_is_named_in_the_response(
        self, api_client, service_with_prices
    ):
        service_with_prices(_voo_only_prices())

        data = _post_backtest(api_client, {"VOO": 0.5, "SCHD": 0.5})

        assert isinstance(data, dict), (
            "คำตอบเป็นลิสต์ล้วน = ไม่มีที่ให้รายงานว่าใครถูกตัดออกจากพอร์ต "
            "(นี่คือรูปเดิมที่ทิ้ง .attrs ทั้งดุ้น)"
        )
        assert data["coverage"][NO_PRICE_KEY] == ["SCHD"]
        assert data["coverage"][ZERO_WEIGHT_KEY] == []
        assert "SCHD" in (data["warning"] or ""), "คำเตือนต้องบอกชื่อกองที่หายไป"
        assert "ไม่ใช่พอร์ตตามสัดส่วนที่กรอกมา" in data["warning"], (
            "ต้องบอกว่าเส้นที่เห็นเป็นพอร์ตที่ normalize ใหม่ ไม่ใช่พอร์ตที่ขอมา"
        )

    def test_the_word_schd_appears_somewhere_in_the_raw_response(
        self, api_client, service_with_prices
    ):
        """ตรึงหลักฐานตรง ๆ ของรายงาน: "คำว่า SCHD ไม่โผล่ที่ไหนเลยในคำตอบ"."""
        service_with_prices(_voo_only_prices())

        response = api_client.post(
            "/api/analysis/backtest",
            json={"initial_capital": 10000.0, "weights": {"VOO": 0.5, "SCHD": 0.5}},
            headers=_HEADERS,
        )

        assert "SCHD" in response.text, (
            "ผู้เรียกขอพอร์ต 50/50 แต่ได้เส้นของ VOO 100% กลับไปโดยไม่มีคำว่า SCHD "
            "อยู่ในคำตอบสักที่ = ตัดกองทิ้งเงียบผ่าน API"
        )

    def test_zero_weight_fund_is_named_in_the_response(self, api_client, service_with_prices):
        """น้ำหนัก 0 = เจตนาของผู้ใช้ ตัดได้ แต่ต้องบอกว่า "ไม่ได้ลืม"."""
        service_with_prices(_two_fund_prices())

        data = _post_backtest(api_client, {"VOO": 1.0, "SCHD": 0.0})

        assert data["coverage"][ZERO_WEIGHT_KEY] == ["SCHD"]
        assert data["coverage"][NO_PRICE_KEY] == []
        assert "SCHD" in (data["warning"] or "")

    def test_complete_portfolio_reports_empty_lists_not_missing_keys(
        self, api_client, service_with_prices
    ):
        """ไม่มีใครถูกตัด = ลิสต์ว่าง + ``warning`` เป็น null — **ไม่ใช่คีย์หาย**.

        คีย์หาย ≠ ลิสต์ว่าง: ปลายทางต้องแยก "ระบบยืนยันว่าครบ" ออกจาก "คำตอบรุ่นเก่า
        ที่ยังไม่มีฟิลด์นี้" ได้ และคำเตือนหลอก ๆ ทำให้คนเลิกอ่านคำเตือนจริง
        """
        service_with_prices(_two_fund_prices())

        data = _post_backtest(api_client, {"VOO": 0.6, "SCHD": 0.4})

        assert data["coverage"][ZERO_WEIGHT_KEY] == []
        assert data["coverage"][NO_PRICE_KEY] == []
        assert data["warning"] is None

    def test_the_curve_itself_still_travels(self, api_client, service_with_prices):
        """ตัวคุม: เพิ่มรายงานแล้วตัวเลขต้องยังอยู่ครบและไม่แบน."""
        service_with_prices(_two_fund_prices())

        data = _post_backtest(api_client, {"VOO": 1.0})

        history = data["history"]
        assert len(history) > 100
        values = {row["Portfolio Value"] for row in history}
        assert len(values) > 1, "ราคาขึ้น 100% แล้วเส้นมูลค่าต้องขยับ"
        assert max(values) == pytest.approx(20000.0, rel=0.02)

    def test_backtest_and_dca_use_the_same_key_names(self, service_with_prices):
        """สำนวนเดียวกันทั้งสอง endpoint — ไม่ใช่สองสำนวนที่สำนวนหนึ่งจะถูกลืม."""
        service = service_with_prices(_two_fund_prices())

        backtest_payload = service.run_backtest({"VOO": 0.6, "SCHD": 0.4}, 10000.0)
        dca_payload = service.simulate_dca({"VOO": 0.6, "SCHD": 0.4}, 1000.0)

        assert set(backtest_payload) == set(dca_payload) == {"history", "coverage", "warning"}


# ===========================================================================
# GAP 1b — หน้าจอ Backtest ต้องเตือนแบบเดียวกับหน้า DCA Simulator
# ===========================================================================


class _Slot:
    """คอลัมน์/กล่อง — ทุกอย่างที่ถูกเรียกบนมันถูกบันทึกรวมกับของหลัก."""

    def __init__(self, log: list) -> None:
        self._log = log

    def __enter__(self) -> "_Slot":
        return self

    def __exit__(self, *_exc) -> bool:
        return False

    def __getattr__(self, name: str):
        def _call(*args, **kwargs):
            self._log.append((name, args, kwargs))
            return None

        return _call


class FakeSt:
    """แทนโมดูล ``streamlit`` — บันทึกทุกอย่างที่หน้าจอ "พูด" ออกมา.

    เหตุผลที่ไม่ใช้ ``streamlit.testing.v1.AppTest`` อยู่ใน docstring ของ
    ``tests/test_dashboard_round2_ux.py`` (SIGSEGV ใน pyarrow ฆ่าโปรเซส pytest ทั้งตัว)
    """

    def __init__(self, pressed: set[str] | None = None) -> None:
        self.calls: list[tuple] = []
        self.pressed = pressed or set()

    def __getattr__(self, name: str):
        def _call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return None

        return _call

    def button(self, *args, **kwargs):
        self.calls.append(("button", args, kwargs))
        label = str(args[0]) if args else str(kwargs.get("label", ""))
        return label in self.pressed

    def number_input(self, *args, **kwargs):
        self.calls.append(("number_input", args, kwargs))
        return float(kwargs.get("value", 10000.0))

    def slider(self, *args, **kwargs):
        self.calls.append(("slider", args, kwargs))
        return float(kwargs.get("value", 1.0))

    def columns(self, spec, **kwargs):
        count = spec if isinstance(spec, int) else len(spec)
        self.calls.append(("columns", (spec,), kwargs))
        return [_Slot(self.calls) for _ in range(count)]

    def container(self, *args, **kwargs):
        self.calls.append(("container", args, kwargs))
        return _Slot(self.calls)

    def expander(self, *args, **kwargs):
        self.calls.append(("expander", args, kwargs))
        return _Slot(self.calls)

    def names(self) -> list[str]:
        return [name for name, _args, _kwargs in self.calls]

    def texts(self, *kinds: str) -> list[str]:
        return [
            str(args[0]) if args else ""
            for name, args, _kwargs in self.calls
            if not kinds or name in kinds
        ]

    def all_text(self) -> str:
        return "\n".join(self.texts())


@pytest.fixture()
def backtest_screen(monkeypatch):
    """เรนเดอร์หน้า Backtest จริงโดยกดปุ่ม "Run Backtest" ให้เลย."""

    def _render(prices: pd.DataFrame, weights: dict[str, float]) -> FakeSt:
        fake = FakeSt(pressed={"Run Backtest"})
        monkeypatch.setattr(app, "st", fake)
        app.render_backtest_page(prices, dict(weights), list(weights))
        return fake

    return _render


class TestBacktestPageWarns:
    def test_zero_weight_fund_is_named_on_screen(self, backtest_screen):
        """ลากสไลเดอร์ SCHD ลง 0 แล้วกราฟที่เห็นเป็นของ VOO ล้วน — ต้องเตือน."""
        fake = backtest_screen(_two_fund_prices(), {"VOO": 1.0, "SCHD": 0.0})

        assert "warning" in fake.names(), (
            "หน้า Backtest ไม่เคยเรียก describe_coverage() ⇒ กองที่หายไปจากพอร์ต "
            "ไม่มีทางไปถึงสายตาผู้ใช้ (หน้า DCA Simulator ในไฟล์เดียวกันทำถูกอยู่แล้ว)"
        )
        text = fake.all_text()
        assert "SCHD" in text
        assert "0" in text

    def test_held_fund_without_price_is_named_on_screen(self, backtest_screen):
        """ถือน้ำหนักอยู่แต่ไม่มีราคา = ร้ายแรงกว่า ต้องบอกว่าไม่ใช่พอร์ตที่กรอกมา."""
        fake = backtest_screen(_voo_only_prices(), {"VOO": 0.5, "SCHD": 0.5})

        assert "warning" in fake.names()
        assert "SCHD" in fake.all_text()
        assert "ไม่ใช่พอร์ตตามสัดส่วนที่กรอกมา" in fake.all_text()

    def test_warning_comes_before_the_numbers(self, backtest_screen):
        """คำเตือนที่อยู่ใต้ metric = อ่านตัวเลขจบไปแล้วถึงจะรู้ว่าตัวเลขนั้นของพอร์ตอื่น."""
        fake = backtest_screen(_voo_only_prices(), {"VOO": 0.5, "SCHD": 0.5})

        names = fake.names()
        assert names.index("warning") < names.index("plotly_chart")
        assert names.index("warning") < names.index("metric")

    def test_complete_portfolio_shows_no_warning(self, backtest_screen):
        """ตัวคุม: พอร์ตครบต้องไม่ขึ้นคำเตือนหลอก ๆ (คำเตือนเฝือ = ผู้ใช้เลิกอ่าน)."""
        fake = backtest_screen(_two_fund_prices(), {"VOO": 0.6, "SCHD": 0.4})

        assert "warning" not in fake.names()
        assert "metric" in fake.names(), "ตัวคุมต้องเดินจนจบหน้าจริง ๆ ไม่ใช่หลุดกลางทาง"

    def test_both_pages_read_the_same_warning_function(self, backtest_screen, monkeypatch):
        """นิยามข้อความมีที่เดียว — สองหน้าต้องได้ประโยคเดียวกันเป๊ะจากกองเดียวกัน."""
        backtest_text = backtest_screen(_voo_only_prices(), {"VOO": 0.5, "SCHD": 0.5}).texts(
            "warning"
        )

        from portfolio.dca import describe_coverage

        expected = describe_coverage({NO_PRICE_KEY: ["SCHD"], ZERO_WEIGHT_KEY: []})
        assert backtest_text == [expected]


# ===========================================================================
# GAP 2 — กล่องบริบท sentiment ต้องไม่สัญญารอบที่ไม่มีวันมา
# ===========================================================================


@pytest.fixture()
def sentiment_box(monkeypatch):
    """เรนเดอร์กล่องบริบท sentiment โดยกำหนดผลจากฐานข้อมูลได้เอง."""

    def _render(summaries) -> FakeSt:
        fake = FakeSt()
        monkeypatch.setattr(app, "st", fake)
        monkeypatch.setattr(app, "get_tickers", lambda: TICKERS)
        monkeypatch.setattr(app, "get_latest_sentiment_summaries", lambda _t: summaries)
        app._render_sentiment_context_box()
        return fake

    return _render


class TestSentimentContextMessage:
    def test_empty_table_says_the_job_is_off_not_wait_for_next_run(self, sentiment_box):
        """ตารางว่าง = งานปิดอยู่ ไม่ใช่ "กำลังจะมา"."""
        text = sentiment_box([]).all_text()

        assert "รอ scheduled job รอบถัดไป" not in text, (
            "งาน sentiment ปิดโดยดีฟอลต์ (ต้องตั้ง VAULTIS_SENTIMENT_ENABLED=1) และไม่มี "
            "scheduler ในเครื่องตัวไหนเรียก run_sentiment_job() ⇒ รอบถัดไปไม่มีวันมา"
        )
        assert "ปิด" in text, "ต้องบอกตรง ๆ ว่างานปิดอยู่"

    def test_empty_table_says_exactly_what_turns_it_on(self, sentiment_box):
        text = sentiment_box([]).all_text()

        assert "VAULTIS_SENTIMENT_ENABLED" in text, "ต้องบอกสวิตช์ของงานอัตโนมัติ"
        assert "VAULTIS_LLM_AUTO" in text, "ต้องบอกสวิตช์ของการรันเอง"

    def test_no_database_stays_a_different_message(self, sentiment_box):
        """"ต่อฐานไม่ได้" ≠ "ฐานว่าง" — สามสถานะสามข้อความ (กฎ C1 ของโปรเจกต์)."""
        text = sentiment_box(None).all_text()

        assert "DATABASE_URL" in text
        assert "VAULTIS_SENTIMENT_ENABLED" not in text, (
            "ยังไม่รู้ด้วยซ้ำว่าฐานว่างหรือไม่ — ห้ามตอบเรื่องสวิตช์ของ job"
        )

    def test_rows_present_still_render_the_expander(self, sentiment_box):
        """ตัวคุม: มีข้อมูลจริงต้องไม่ตกเข้าสาขา "ยังไม่มีข้อมูล"."""
        fake = sentiment_box(
            [
                {
                    "symbol": "VOO",
                    "overall_sentiment": "positive",
                    "score": 0.4,
                    "total_articles": 5,
                    "created_at": "2026-08-07",
                }
            ]
        )

        assert "expander" in fake.names()
        assert "VAULTIS_SENTIMENT_ENABLED" not in fake.all_text()


# ===========================================================================
# GAP 3 — URL ที่เดาให้ต้องใช้สคีมาเดียวกับหน้าเว็บ และบอกข้อจำกัดของการเดา
# ===========================================================================


@pytest.fixture()
def ws_url(monkeypatch):
    def _resolve(backend_url: str) -> tuple[str, str | None]:
        monkeypatch.delenv("VAULTIS_WS_URL", raising=False)
        monkeypatch.setattr(app, "BACKEND_URL", backend_url)
        return app._ws_prices_url_with_status()

    return _resolve


class TestGuessedWebSocketUrl:
    def test_https_backend_guesses_wss_not_ws(self, ws_url):
        """หน้าที่เสิร์ฟผ่าน https เปิด ``ws://`` ไม่ได้ — เบราว์เซอร์บล็อก mixed content."""
        url, note = ws_url("https://backend:8443")

        assert url == "wss://127.0.0.1:8443/ws/prices", (
            "สคีมาถูกฮาร์ดโค้ดเป็น ws:// ⇒ https ⇒ mixed content ⇒ เบราว์เซอร์บล็อก "
            "ตั้งแต่ก่อนถึงเครือข่าย ผู้ใช้เห็นแค่ ⚠️ โดยไม่มีทางรู้สาเหตุ"
        )
        assert note is not None

    def test_guess_never_downgrades_the_scheme(self, ws_url):
        """ตรึงกฎกลาง ไม่ใช่แค่พอร์ตเดียว: https ⇒ ห้ามได้ ``ws://`` ออกมาเด็ดขาด."""
        for backend_url in ("https://backend", "https://backend:9443", "https://vaultis-api"):
            url, _note = ws_url(backend_url)
            assert url.startswith("wss://"), f"{backend_url} → {url}"

    def test_http_backend_still_guesses_ws(self, ws_url):
        """ตัวคุม: http ต้องไม่ถูกยกเป็น wss (backend ไม่ได้เปิด TLS)."""
        url, note = ws_url("http://backend:8000")

        assert url == "ws://127.0.0.1:8000/ws/prices"
        assert note is not None

    def test_note_says_the_guess_needs_browser_on_the_docker_host(self, ws_url):
        """127.0.0.1 = เครื่องของเบราว์เซอร์ ไม่ใช่เครื่องที่รัน Docker — ต้องพูดออกมา."""
        _url, note = ws_url("http://backend:8000")

        assert note and "เครื่องเดียวกับที่รัน Docker" in note, (
            "ผู้ใช้ที่เปิดจากมือถือ/เครื่องอื่นจะยิงกลับเข้าเครื่องตัวเองและต่อไม่ติดตลอด "
            "โดยไม่มีอะไรบอกว่าทำไม"
        )
        assert "VAULTIS_WS_URL" in note, "ต้องบอกทางออกที่ถาวรด้วย"

    def test_reachable_host_is_untouched(self, ws_url):
        """ตัวคุม: โฮสต์ที่เบราว์เซอร์ใช้ได้อยู่แล้วต้องไม่ถูกเปลี่ยนเป็น 127.0.0.1."""
        assert ws_url("https://vaultis-backend.onrender.com") == (
            "wss://vaultis-backend.onrender.com/ws/prices",
            None,
        )


def test_json_payload_of_the_route_is_utf8_thai(api_client, service_with_prices):
    """คำเตือนเป็นภาษาไทย — ต้องอ่านออกหลัง round-trip ผ่าน JSONResponse."""
    service_with_prices(_voo_only_prices())

    response = api_client.post(
        "/api/analysis/backtest",
        json={"initial_capital": 10000.0, "weights": {"VOO": 0.5, "SCHD": 0.5}},
        headers=_HEADERS,
    )

    assert "charset=utf-8" in response.headers.get("content-type", "")
    decoded = json.loads(response.content.decode("utf-8"))
    assert "ไม่มีข้อมูลราคาของ SCHD" in decoded["data"]["warning"]
