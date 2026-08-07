# -*- coding: utf-8 -*-
"""หน้าจอต้องพูดความจริงเรื่องข้อมูลที่หายไป (FIX_PLAN เฟส 1 ฝั่ง dashboard).

ทุกเทสต์ในไฟล์นี้คุมกฎเดียวกัน: **"ดึงไม่สำเร็จ" ห้ามกลายเป็นตัวเลขหรือคำยืนยัน**
- แถวสมุดบัญชีที่ถูกตัด (ข้อ 1.2) ต้องโผล่บนจอ ไม่ใช่หายเงียบ
- ราคาที่ดึงไม่ได้ (ข้อ 1.3) ต้องหยุดแผน/ค่า drift ไม่ใช่คิดต่อจากพอร์ตที่ไม่ครบ
- โมเมนตัมที่คำนวณไม่ได้ (ข้อ 1.5) ต้องอ่านว่า "ไม่มีข้อมูล" ไม่ใช่ 0 คะแนน
- คำอธิบาย AI ที่ล้มเหลว (ข้อ 1.1) ต้องแสดงเป็น error ไม่ใช่กล่องข้อมูลปกติ
"""

from __future__ import annotations

import pandas as pd
import pytest

app = pytest.importorskip("dashboard.app")


class _FakeSlot:
    """คอลัมน์/บล็อกย่อยของ Streamlit — บันทึกทุกการเรียกลง log เดียวกัน."""

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
    """แทน ``streamlit`` ในเทสต์ — เก็บสิ่งที่หน้าจอ "พูด" ออกมาเป็นรายการ."""

    def __init__(self, toggle_value: bool = True) -> None:
        self.calls: list[tuple] = []
        self.toggle_value = toggle_value

    def __getattr__(self, name: str):
        def _call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return None

        return _call

    def columns(self, spec, **kwargs):
        count = spec if isinstance(spec, int) else len(spec)
        self.calls.append(("columns", (spec,), kwargs))
        return [_FakeSlot(self.calls) for _ in range(count)]

    def number_input(self, *args, **kwargs):
        """คืนค่าเริ่มต้นของช่องเหมือน Streamlit จริง (ผู้ใช้ยังไม่ได้พิมพ์อะไร)."""
        self.calls.append(("number_input", args, kwargs))
        return kwargs.get("value")

    def toggle(self, *args, **kwargs):
        self.calls.append(("toggle", args, kwargs))
        return self.toggle_value

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
            str(args[0]) if args else "" for _name, args, _kwargs in self.calls
        )


@pytest.fixture()
def fake_st(monkeypatch) -> FakeSt:
    fake = FakeSt()
    monkeypatch.setattr(app, "st", fake)
    return fake


def _holdings(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# ข้อ 1.2 — แถวสมุดบัญชีที่ถูกตัดต้องเห็นบนจอ
# --------------------------------------------------------------------------
SKIPPED_FIXTURE = [
    {
        "tx_id": "abc123",
        "date": "2026-03-02",
        "ticker": "VOO",
        "missing_fields": ["fx_rate_thb"],
        "reason": "ข้อมูลไม่ครบ: อัตราแลกเปลี่ยน",
    }
]


class TestSkippedLedgerRowsAreVisible:
    def test_skipped_rows_render_as_error_with_details(self, fake_st):
        app._render_ledger_skipped_rows(SKIPPED_FIXTURE, "ข้ามธุรกรรม 1 แถวเพราะข้อมูลไม่ครบ")

        assert fake_st.texts("error"), "แถวที่ถูกตัดต้องขึ้นเป็น error ไม่ใช่หายเงียบ"
        assert "ข้ามธุรกรรม 1 แถว" in fake_st.all_text()
        rendered = [c for c in fake_st.calls if c[0] == "dataframe"]
        assert rendered, "ต้องมีตารางรายละเอียดแถวที่ถูกตัด"
        detail_df = rendered[0][1][0]
        assert "abc123" in detail_df.to_string()

    def test_message_is_built_when_backend_did_not_send_one(self, fake_st):
        app._render_ledger_skipped_rows(SKIPPED_FIXTURE, "")

        assert "1" in fake_st.all_text()
        assert fake_st.texts("error")

    def test_nothing_is_shown_when_no_row_was_skipped(self, fake_st):
        app._render_ledger_skipped_rows([], "")

        assert fake_st.calls == []

    def test_benchmark_section_does_not_call_a_gutted_ledger_empty(self, fake_st, monkeypatch):
        """ทุกไม้ถูกตัด ≠ "ยังไม่มีรายการซื้อ" — หน้าเทียบ VOO ต้องแยกสองอย่างนี้."""
        empty_ledger = pd.DataFrame(
            {"tx_type": [], "shares": [], "price_usd": [], "date": [], "ticker": []}
        )
        empty_ledger.attrs["skipped_rows"] = SKIPPED_FIXTURE
        monkeypatch.setattr(app, "get_transactions", lambda *a, **k: empty_ledger)

        app._render_benchmark_section(pd.DataFrame())

        text = fake_st.all_text()
        assert "ไม่รวม 1 ไม้" in text
        assert "ยังไม่มีรายการซื้อใน ledger" not in text

    def test_rows_are_read_from_dataframe_attrs(self):
        df = pd.DataFrame({"tx_id": []})
        df.attrs["skipped_rows"] = SKIPPED_FIXTURE

        assert app._ledger_skipped_rows(df) == SKIPPED_FIXTURE
        assert app._ledger_skipped_rows(pd.DataFrame()) == []


# --------------------------------------------------------------------------
# ข้อ 1.3 — ราคาที่ดึงไม่ได้ต้องหยุดแผน ไม่ใช่คิดต่อจากพอร์ตที่ไม่ครบ
# --------------------------------------------------------------------------
PARTIAL_HOLDINGS = [
    {"Ticker": "VOO", "Current Value (THB)": 10000.0, "Price OK": True},
    {"Ticker": "GLDM", "Current Value (THB)": float("nan"), "Price OK": False},
]


class TestDriftAdvisoryFailsClosed:
    def test_missing_price_blocks_the_drift_verdict(self, fake_st, monkeypatch):
        monkeypatch.setattr(app, "get_portfolio_summary", lambda: _holdings(PARTIAL_HOLDINGS))
        monkeypatch.setattr(app, "get_target_weights", lambda tickers: {"VOO": 1.0})

        app._render_drift_advisory()

        text = fake_st.all_text()
        assert "GLDM" in text, "ต้องบอกว่า ticker ไหนดึงราคาไม่ได้"
        assert "ใกล้เป้าหมายทุกตัว" not in text, "ห้ามยืนยันว่าพอร์ตชิดเป้าทั้งที่ราคาหายไปหนึ่งตัว"
        assert "พอร์ตจริงตอนนี้เอียง" not in text, "ห้ามสรุปว่าพอร์ตเอียงจากพอร์ตที่ไม่ครบ"
        assert "% จากเป้า" not in text, "ห้ามแสดงตัวเลข drift ที่คิดจากพอร์ตไม่ครบ"

    def test_full_prices_still_report_drift(self, fake_st, monkeypatch):
        holdings = _holdings(
            [
                {"Ticker": "VOO", "Current Value (THB)": 9000.0, "Price OK": True},
                {"Ticker": "GLDM", "Current Value (THB)": 1000.0, "Price OK": True},
            ]
        )
        monkeypatch.setattr(app, "get_portfolio_summary", lambda: holdings)
        monkeypatch.setattr(app, "get_target_weights", lambda tickers: {"VOO": 0.5, "GLDM": 0.5})

        app._render_drift_advisory()

        assert "จากเป้า" in fake_st.all_text()


class TestRebalanceModeFailsClosed:
    def test_missing_price_means_no_plan(self, fake_st, monkeypatch):
        monkeypatch.setattr(app, "get_portfolio_summary", lambda: _holdings(PARTIAL_HOLDINGS))
        monkeypatch.setattr(app, "get_target_weights", lambda tickers: {"VOO": 1.0})
        monkeypatch.setattr(app, "_render_execute_list", lambda *a, **k: None)

        shown = app._render_rebalance_mode(5000.0, {})

        assert shown is False, "ราคาไม่ครบ = ไม่แสดงแผน (ผู้เรียกกลับไปใช้แผน DCA ปกติ)"
        assert "GLDM" in fake_st.all_text()
        assert not [c for c in fake_st.calls if c[0] == "dataframe"], "ห้ามแสดงตารางแผนที่คิดจากพอร์ตไม่ครบ"

    def test_complete_prices_still_produce_a_plan(self, fake_st, monkeypatch):
        holdings = _holdings(
            [
                {"Ticker": "VOO", "Current Value (THB)": 9000.0, "Price OK": True},
                {"Ticker": "GLDM", "Current Value (THB)": 1000.0, "Price OK": True},
            ]
        )
        monkeypatch.setattr(app, "get_portfolio_summary", lambda: holdings)
        monkeypatch.setattr(app, "get_target_weights", lambda tickers: {"VOO": 0.5, "GLDM": 0.5})
        monkeypatch.setattr(app, "_render_execute_list", lambda *a, **k: None)

        assert app._render_rebalance_mode(5000.0, {}) is True
        assert [c for c in fake_st.calls if c[0] == "dataframe"]


# --------------------------------------------------------------------------
# AUDIT_2026-08-06 C2 / FIX_PLAN 3.4 — เป้าหมายต้องคิดจาก "ทั้งพอร์ตที่ระบบติดตาม"
# ห้าม normalize บนเซ็ตย่อยของกองที่ถืออยู่ (บั๊กที่ portfolio/targets.py ถูกสร้างมาแก้)
# --------------------------------------------------------------------------
PRESET_TARGETS = {"VOO": 0.35, "SCHD": 0.25, "QQQM": 0.20, "XLV": 0.10, "GLDM": 0.10}
TRACKED_TICKERS = list(PRESET_TARGETS)

# พอร์ตจริงที่ยังซื้อไม่ครบ — มีแค่ 2 ใน 5 กองที่ระบบติดตาม
PARTIAL_PORTFOLIO = [
    {"Ticker": "VOO", "Current Value (THB)": 9000.0, "Price OK": True},
    {"Ticker": "SCHD", "Current Value (THB)": 1000.0, "Price OK": True},
]


def _targets_like_the_real_module(seen: list[list[str]]):
    """เลียนแบบ ``portfolio.targets.get_target_weights()`` — normalize บนเซ็ตที่ส่งเข้ามา.

    หัวใจของบั๊ก: ส่งเซ็ตย่อยเข้าไป = ได้เป้าที่ถูกขยายให้รวมเป็น 1.0 บนเซ็ตย่อยนั้น
    (ส่ง ``["VOO", "SCHD"]`` → SCHD 41.7% ทั้งที่ตั้งไว้ 25%)
    """

    def _get(tickers=None) -> dict:
        symbols = [str(t).strip().upper() for t in (tickers if tickers else TRACKED_TICKERS)]
        seen.append(list(symbols))
        total = sum(PRESET_TARGETS.get(s, 0.0) for s in symbols)
        if total <= 0:
            return {s: 0.0 for s in symbols}
        return {s: PRESET_TARGETS.get(s, 0.0) / total for s in symbols}

    return _get


def _rendered_frames(fake_st: FakeSt) -> list[pd.DataFrame]:
    """ตารางที่หน้าจอวาดออกมา (คลาย Styler ออกให้เหลือ DataFrame)."""
    frames = []
    for name, args, _kwargs in fake_st.calls:
        if name == "dataframe" and args:
            frames.append(getattr(args[0], "data", args[0]))
    return frames


class TestRebalanceModeUsesWholePortfolioTargets:
    @pytest.fixture()
    def wired(self, monkeypatch):
        """พอร์ตซื้อไม่ครบ + เป้าหมายที่ normalize ตามเซ็ตที่ถูกถาม."""
        seen: list[list[str]] = []
        monkeypatch.setattr(app, "get_portfolio_summary", lambda: _holdings(PARTIAL_PORTFOLIO))
        monkeypatch.setattr(app, "get_tickers", lambda: list(TRACKED_TICKERS))
        monkeypatch.setattr(app, "get_target_weights", _targets_like_the_real_module(seen))
        monkeypatch.setattr(app, "_render_execute_list", lambda *a, **k: None)
        return seen

    def test_ถามเป้าหมายด้วยทุกกองที่ระบบติดตาม_ไม่ใช่เฉพาะกองที่ถืออยู่(self, fake_st, wired):
        assert app._render_rebalance_mode(5000.0, {}) is True

        assert wired, "ต้องถามสัดส่วนเป้าหมายจากแหล่งกลาง ไม่ใช่คิดเอง"
        assert all(asked == TRACKED_TICKERS for asked in wired), (
            f"ส่งเซ็ตย่อยเข้า get_target_weights() → เป้าถูก normalize ใหม่: {wired}"
        )

    def test_กองที่ยังไม่เคยซื้อต้องได้เงินเดือนนี้(self, fake_st, wired):
        app._render_rebalance_mode(5000.0, {})

        frames = _rendered_frames(fake_st)
        assert frames, "ต้องมีตารางแผน"
        plan = frames[0]
        for ticker in ("QQQM", "XLV", "GLDM"):
            assert ticker in set(plan["ETF"]), (
                f"{ticker} อยู่ในเป้าหมายแต่ไม่มีวันได้เงิน — โหมดนี้จึงซื้อเข้าเป้าไม่ได้จริง"
            )
        never_bought = plan[plan["ETF"] == "GLDM"].iloc[0]
        assert float(never_bought["เติมเดือนนี้ (บาท)"]) > 0

    def test_คอลัมน์เป้าหมายต้องตรงกับหน้า_Settings(self, fake_st, wired):
        app._render_rebalance_mode(5000.0, {})

        plan = _rendered_frames(fake_st)[0]
        by_ticker = {row["ETF"]: float(row["เป้าหมาย"]) for _, row in plan.iterrows()}
        assert by_ticker["SCHD"] == pytest.approx(25.0, abs=0.1), (
            "เป้าบนจอนี้ต้องเท่ากับที่ตั้งไว้ (25%) ไม่ใช่ 41.7% ที่ normalize บนกองที่ถืออยู่"
        )

    def test_drift_advisory_กับโหมด_rebalance_ถามเป้าหมายชุดเดียวกัน(self, fake_st, wired):
        """ห้ามมีสองสูตร — ทั้งสองหน้าจอต้องอ้างอิงรายชื่อเดียวกันเป๊ะ ๆ"""
        app._render_rebalance_mode(5000.0, {})
        from_rebalance = list(wired)
        wired.clear()

        app._render_drift_advisory()
        from_drift = list(wired)

        assert from_rebalance and from_drift
        assert set(map(tuple, from_rebalance)) == set(map(tuple, from_drift))

    def test_เศษที่แจกไม่ลงต้องบอกผู้ใช้(self, fake_st, wired):
        app._render_rebalance_mode(5050.0, {})

        assert "50" in fake_st.all_text(), "เงินที่ปัดหลักร้อยแล้วแจกไม่ลง ห้ามหายเงียบ"

    def test_กองที่ถืออยู่แต่ไม่มีเป้าหมายต้องถูกเอ่ยชื่อ(self, fake_st, monkeypatch):
        """VT ถูกลบออกจากรายการที่ติดตามแล้วแต่ยังถืออยู่ — มูลค่าอยู่ในตัวหาร แต่ไม่มีแถวในแผน"""
        held = PARTIAL_PORTFOLIO + [
            {"Ticker": "VT", "Current Value (THB)": 5000.0, "Price OK": True}
        ]
        monkeypatch.setattr(app, "get_portfolio_summary", lambda: _holdings(held))
        monkeypatch.setattr(app, "get_tickers", lambda: list(TRACKED_TICKERS))
        monkeypatch.setattr(app, "get_target_weights", _targets_like_the_real_module([]))
        monkeypatch.setattr(app, "_render_execute_list", lambda *a, **k: None)

        app._render_rebalance_mode(5000.0, {})

        assert "VT" in fake_st.all_text(), "ของที่ถืออยู่นอกเป้าหมายห้ามหายจากจอเงียบ ๆ"

    def test_คอนฟิกเป้าหมายผิดรูปต้องไม่มีแผน(self, fake_st, monkeypatch):
        def _boom(_tickers=None):
            raise app.InvalidTargetWeights("target_weights รวมกันได้ 7 ซึ่งอ่านไม่ออกว่าเป็นหน่วยอะไร")

        monkeypatch.setattr(app, "get_portfolio_summary", lambda: _holdings(PARTIAL_PORTFOLIO))
        monkeypatch.setattr(app, "get_tickers", lambda: list(TRACKED_TICKERS))
        monkeypatch.setattr(app, "get_target_weights", _boom)
        monkeypatch.setattr(app, "_render_execute_list", lambda *a, **k: None)

        assert app._render_rebalance_mode(5000.0, {}) is False
        assert fake_st.texts("error"), "คอนฟิกเป้าหมายพัง = ห้ามเดาเป้าแล้วทำแผนต่อ"
        assert "target_weights" in fake_st.all_text()
        assert not _rendered_frames(fake_st)


class TestRealTargetsModuleAgreesWithTheScreen:
    """ด่านสุดท้าย: เสียบ ``portfolio.targets.get_target_weights`` ตัวจริงเข้าไปเลย.

    เทสต์ข้างบนใช้ตัวปลอมที่ *เลียนแบบ* การ normalize — ถ้าโมดูลจริงเปลี่ยนพฤติกรรม
    เทสต์พวกนั้นจะยังเขียวทั้งที่หน้าจอเพี้ยน คลาสนี้จึงต่อของจริงเข้าตรง ๆ
    (แทนแค่ ``load_config`` เพื่อไม่แตะ config.json ของผู้ใช้)
    """

    @pytest.fixture()
    def wired_real(self, monkeypatch):
        from portfolio import targets as targets_module

        monkeypatch.setattr(
            targets_module,
            "load_config",
            lambda: {"portfolio": {"risk_profile": "moderate", "target_weights": {}}},
        )
        monkeypatch.setattr(app, "get_target_weights", targets_module.get_target_weights)
        monkeypatch.setattr(app, "get_tickers", lambda: list(TRACKED_TICKERS))
        monkeypatch.setattr(app, "get_portfolio_summary", lambda: _holdings(PARTIAL_PORTFOLIO))
        monkeypatch.setattr(app, "_render_execute_list", lambda *a, **k: None)

    def test_เป้าบนแผนคือเป้าของทั้งพอร์ต_ไม่ใช่ที่ถูกขยายบนกองที่ถืออยู่(self, fake_st, wired_real):
        assert app._render_rebalance_mode(5000.0, {}) is True

        plan = _rendered_frames(fake_st)[0]
        by_ticker = {row["ETF"]: float(row["เป้าหมาย"]) for _, row in plan.iterrows()}
        assert by_ticker["SCHD"] == pytest.approx(25.0, abs=0.1)
        assert by_ticker["QQQM"] == pytest.approx(20.0, abs=0.1)
        assert by_ticker["XLV"] == pytest.approx(10.0, abs=0.1)
        assert by_ticker["GLDM"] == pytest.approx(10.0, abs=0.1)

    def test_กองที่เกินเป้าจนไม่ได้เงินต้องถูกเอ่ยชื่อ(self, fake_st, wired_real):
        """VOO ถือไว้ 90% ของพอร์ต — ไม่ได้เงินเพราะเกินเป้า ไม่ใช่เพราะหลุดจากเป้าหมาย"""
        app._render_rebalance_mode(5000.0, {})

        plan = _rendered_frames(fake_st)[0]
        assert "VOO" not in set(plan["ETF"])
        assert "ไม่ได้รับเงินเดือนนี้" in fake_st.all_text()
        assert "VOO" in fake_st.all_text()

    def test_เป้าหมายที่ผู้ใช้ตั้งเองก็ต้องไม่ถูก_normalize_ซ้ำ(self, fake_st, monkeypatch):
        """ตั้ง GLDM = 0 ("ตั้งใจไม่ถือ") — ค่านั้นต้องรอดมาถึงจอ ไม่ถูกดันขึ้นเป็น 10%"""
        from portfolio import targets as targets_module

        monkeypatch.setattr(
            targets_module,
            "load_config",
            lambda: {
                "portfolio": {"risk_profile": "moderate", "target_weights": {"GLDM": 0.0}}
            },
        )
        monkeypatch.setattr(app, "get_target_weights", targets_module.get_target_weights)
        monkeypatch.setattr(app, "get_tickers", lambda: list(TRACKED_TICKERS))
        monkeypatch.setattr(app, "get_portfolio_summary", lambda: _holdings(PARTIAL_PORTFOLIO))
        monkeypatch.setattr(app, "_render_execute_list", lambda *a, **k: None)

        app._render_rebalance_mode(5000.0, {})

        plan = _rendered_frames(fake_st)[0]
        assert "GLDM" not in set(plan["ETF"]), "ตั้งเป้า 0% แล้วยังเทเงินเข้า = ไม่ฟังผู้ใช้"
        assert "XLV" in set(plan["ETF"]), "กองอื่นที่ไม่ได้ตั้งเองต้องยังได้เงินตามส่วนของ preset"

    def test_ถือของที่ตั้งเป้าไว้_0_ต้องบอกว่าทำไมไม่มีในแผน(self, fake_st, monkeypatch):
        """ตั้ง VOO = 0 แต่ยังถือ VOO อยู่ — "ไม่ขาย" ต้องพูดออกมา ไม่ใช่ให้แถวหายเฉย ๆ"""
        from portfolio import targets as targets_module

        monkeypatch.setattr(
            targets_module,
            "load_config",
            lambda: {
                "portfolio": {"risk_profile": "moderate", "target_weights": {"VOO": 0.0}}
            },
        )
        monkeypatch.setattr(app, "get_target_weights", targets_module.get_target_weights)
        monkeypatch.setattr(app, "get_tickers", lambda: list(TRACKED_TICKERS))
        monkeypatch.setattr(app, "get_portfolio_summary", lambda: _holdings(PARTIAL_PORTFOLIO))
        monkeypatch.setattr(app, "_render_execute_list", lambda *a, **k: None)

        app._render_rebalance_mode(5000.0, {})

        text = fake_st.all_text()
        assert "ตั้งเป้าไว้ 0% แต่ยังถืออยู่" in text
        assert "VOO" in text


class TestFallbackFxInputStaysInsideTheSanityBand:
    """ค่าสำรอง FX ที่กรอกได้ ต้องอยู่ในช่วงเดียวกับที่ ``utils/fx.py`` ยอมรับ.

    เดิมช่องนี้ ``min_value=1.0`` และไม่มี ``max_value`` — กรอก 1.5 หรือ 900 ก็บันทึกได้
    แล้ว ``_config_fallback()`` โยน ``FxRateUnavailable`` ตอนดึงค่าสดไม่ได้ ⇒ ทุกตัวเลขบาท
    ดับพร้อมกันโดยที่หน้าจอที่ทำให้เกิดไม่เคยเตือนอะไรเลย
    """

    def test_ช่วงของช่องผูกกับ_utils_fx(self, fake_st):
        app._render_fallback_fx_input(33.5)

        kwargs = [k for name, _a, k in fake_st.calls if name == "number_input"][0]
        assert kwargs["min_value"] == pytest.approx(app.FX_MIN_RATE)
        assert kwargs["max_value"] == pytest.approx(app.FX_MAX_RATE)

    def test_ค่าที่บันทึกไว้นอกช่วงต้องเตือนและหน้าจอต้องไม่พัง(self, fake_st):
        app._render_fallback_fx_input(900.0)

        assert fake_st.texts("warning"), "ค่าสำรองที่ใช้ไม่ได้ต้องเตือนตั้งแต่หน้าที่ตั้งค่า"
        assert "900" in fake_st.all_text()
        kwargs = [k for name, _a, k in fake_st.calls if name == "number_input"][0]
        assert app.FX_MIN_RATE <= kwargs["value"] <= app.FX_MAX_RATE

    def test_ค่าที่อ่านไม่ออกก็ต้องไม่พัง(self, fake_st):
        app._render_fallback_fx_input("ไม่ใช่ตัวเลข")

        assert fake_st.texts("warning")
        kwargs = [k for name, _a, k in fake_st.calls if name == "number_input"][0]
        assert app.FX_MIN_RATE <= kwargs["value"] <= app.FX_MAX_RATE

    def test_ค่าในช่วงไม่ต้องเตือน(self, fake_st):
        returned = app._render_fallback_fx_input(33.05)

        assert not fake_st.texts("warning")
        assert float(returned) == pytest.approx(33.05)


def _totals(**overrides) -> dict:
    totals = {
        "invested_thb_all": 10000.0,
        "invested_thb_priced": 10000.0,
        "current_value_thb": 11000.0,
        "total_pnl_thb": 1000.0,
        "total_return_pct": 10.0,
        "total_fee_thb": 15.0,
        "fx_rate_thb": 34.25,
        "fx_is_live": True,
    }
    totals.update(overrides)
    return totals


class TestPortfolioTotalsFlagFallbackFx:
    """ธง ``fx_is_live`` ที่ ``tracker.get_total_summary()`` ส่งมา ต้องโผล่บนจอ.

    (คำเตือน "ค่าสำรอง/ค่าสด" คุมไว้แล้วใน ``tests/test_dashboard_c2.py`` — ที่นี่เก็บ
    สองช่องว่างที่ยังไม่มีใครคุม: **ต้องบอกอัตราที่ใช้จริง** และ **"ไม่ทราบที่มา"
    ห้ามถูกอ่านเป็น "ค่าสำรอง"**)
    """

    def test_คำเตือนต้องบอกอัตราที่ใช้จริงด้วย(self, fake_st):
        app._render_portfolio_totals(_totals(fx_is_live=False, fx_rate_thb=33.5), "THB", 33.5)

        assert any("ค่าสำรอง" in t for t in fake_st.texts("warning"))
        assert "33.50" in fake_st.all_text(), "บอกว่าใช้ค่าสำรองแต่ไม่บอกว่าเท่าไร = ตรวจต่อไม่ได้"

    def test_ไม่ทราบที่มาไม่ใช่ค่าสำรอง(self, fake_st):
        """``None`` = ไม่มีการแปลงค่าเงินเกิดขึ้นเลย — ห้ามยุบเป็น "ใช้ค่าสำรอง"."""
        app._render_portfolio_totals(
            _totals(fx_is_live=None, fx_rate_thb=None), "THB", 34.25
        )

        assert not fake_st.texts("warning")


# --------------------------------------------------------------------------
# ข้อ 1.5 — โมเมนตัมที่คำนวณไม่ได้ ต้องไม่ถูกอ่านเป็น 0 คะแนน
# --------------------------------------------------------------------------
def _score_row(**overrides) -> dict:
    row = {
        "ticker": "GLDM",
        "price": 100.0,
        "ma50": 99.0,
        "ma200": 95.0,
        "rsi": 55.0,
        "trend_score": 40,
        "timing_score": 20,
        "momentum_score": None,
        "momentum_available": False,
        "momentum_max": 0,
        "dividend_score": 0,
        "dividend_available": False,
        "total_score": 60,
        "max_score": 70,
        "total_pct": 85.7,
        "return_1m_pct": None,
        "return_3m_pct": None,
        "technical_signal": "hold",
        "technical_signal_th": "ถือ",
        "signal": "HOLD",
        "data_ok": True,
    }
    row.update(overrides)
    return row


class TestMomentumUnavailableIsNotZero:
    def test_full_analysis_table_shows_na_not_zero(self):
        payload = {"analysis": {"GLDM": _score_row(dcf_available=False, dcf={})}}

        df = app._full_analysis_score_dcf_df(payload)

        assert df.loc[0, "Momentum"] is None or pd.isna(df.loc[0, "Momentum"]), (
            "โมเมนตัมที่คำนวณไม่ได้ต้องเป็น N/A ไม่ใช่ 0 (0 อ่านว่า 'ราคาไม่ขึ้น')"
        )

    def test_available_momentum_is_still_an_int(self):
        payload = {
            "analysis": {
                "VOO": _score_row(
                    ticker="VOO",
                    momentum_score=20,
                    momentum_available=True,
                    momentum_max=20,
                    dcf_available=False,
                    dcf={},
                )
            }
        }

        assert int(app._full_analysis_score_dcf_df(payload).loc[0, "Momentum"]) == 20

    def test_chip_says_no_data_instead_of_showing_zero(self):
        chips = app._score_reason_chips(_score_row())

        assert "โมเมนตัม" in chips and "ไม่มีข้อมูล" in chips

    def test_audit_trail_uses_the_real_denominator(self, fake_st):
        app._render_score_audit_trail(_score_row(momentum_score=10, momentum_available=True, momentum_max=10), None)

        text = fake_st.all_text()
        assert "Momentum 10/10" in text, "ตัวหารต้องเป็นเพดานจริงของตัวนั้น ไม่ใช่ค่าคงที่ 20"
        assert "/20" not in text

    def test_audit_trail_says_excluded_when_unavailable(self, fake_st):
        app._render_score_audit_trail(_score_row(), None)

        assert "ไม่มีข้อมูล" in fake_st.all_text()


# --------------------------------------------------------------------------
# ข้อ 1.1 — คำอธิบาย AI ที่ล้มเหลวต้องอ่านออกว่าล้มเหลว
# --------------------------------------------------------------------------
class TestFailedAiExplanationIsLoud:
    def test_failure_text_is_an_error_not_an_info_box(self, fake_st):
        app.show_result(
            {
                "etf_scores": [],
                "allocation": {},
                "ai_used": False,
                "advice_text": "⚠️ เรียก AI ไม่สำเร็จ: anthropic: โมเดลใช้โควตาหมด",
                "discord_result": {"skipped": True},
            }
        )

        errors = fake_st.texts("error")
        assert any("เรียก AI ไม่สำเร็จ" in t for t in errors), (
            "ข้อความล้มเหลวต้องเป็น st.error ไม่งั้นดูเหมือนคำแนะนำปกติ"
        )

    def test_disabled_message_stays_an_info_box(self, fake_st):
        app.show_result(
            {
                "etf_scores": [],
                "allocation": {},
                "ai_used": False,
                "advice_text": "🔒 บทวิเคราะห์ AI ปิดอยู่เพื่อคุมค่าใช้จ่าย",
                "discord_result": {"skipped": True},
            }
        )

        assert not fake_st.texts("error"), "การปิด AI ไว้ไม่ใช่ความล้มเหลว"
        assert any("ปิดอยู่" in t for t in fake_st.texts("info"))

    def test_ai_heading_names_the_model_actually_used(self, fake_st):
        app.show_result(
            {
                "etf_scores": [],
                "allocation": {},
                "ai_used": True,
                "advice_text": "คำอธิบาย",
                "discord_result": {"skipped": True},
            }
        )

        text = fake_st.all_text()
        assert app.ANTHROPIC_MODEL in text or "Sonnet 5" in text
        assert "Haiku" not in text, "ชื่อโมเดลบนจอต้องตรงกับที่เรียกจริง"


# --------------------------------------------------------------------------
# รอบเก็บกวาด C1 — แถวปันผลที่ถูกตัดต้องเห็นบนจอ แม้ไม่เหลือแถวไหนเลย
# --------------------------------------------------------------------------
def _dividend_summary(count: int, skipped: list[dict] | None = None) -> dict:
    """สรุปปันผลรูปแบบเดียวกับที่ ``tracker.get_dividend_summary()`` คืนมา"""
    skipped = skipped or []
    return {
        "total_thb": 340.0 if count else 0.0,
        "total_usd": 10.0 if count else 0.0,
        "this_year_thb": 340.0 if count else 0.0,
        "count": count,
        "by_ticker_thb": {"VOO": 340.0} if count else {},
        "skipped_rows": skipped,
        "skipped_reason": (
            f"ข้ามปันผล {len(skipped)} แถวเพราะข้อมูลไม่ครบ" if skipped else ""
        ),
    }


DIVIDEND_SKIPPED = [
    {
        "tx_id": "div001",
        "date": "2026-02-14",
        "ticker": "SCHD",
        "missing_fields": ["fx_rate_thb"],
        "reason": "ค่าที่บันทึกไว้ 0 ใช้ไม่ได้ ต้องอยู่ในช่วง 20–50",
    }
]


class TestDividendSkippedRowsAreVisible:
    def test_all_dividend_rows_skipped_still_warns(self, fake_st):
        """เคสที่เงียบที่สุด: ตัดหมดจน count = 0 — เดิมทั้งบล็อกถูกซ่อน ผู้ใช้ไม่เห็นอะไรเลย"""
        opened = app._render_dividend_section_header(_dividend_summary(0, DIVIDEND_SKIPPED))

        assert opened is True, "ตัดปันผลทิ้งหมดแล้วยังต้องเปิดหัวข้อเพื่อเตือน"
        assert fake_st.texts("error"), "แถวปันผลที่ถูกตัดต้องขึ้นเป็น error ไม่ใช่หายเงียบ"
        rendered = [c for c in fake_st.calls if c[0] == "dataframe"]
        assert rendered, "ต้องมีตารางบอกว่าแถวไหนถูกตัด"
        assert "SCHD" in rendered[0][1][0].to_string(), "ต้องบอกว่าแถวไหนถูกตัด"

    def test_skipped_rows_show_alongside_the_real_total(self, fake_st):
        opened = app._render_dividend_section_header(_dividend_summary(1, DIVIDEND_SKIPPED))

        assert opened is True
        assert fake_st.texts("error"), "ยอดที่โชว์น้อยกว่าจริง ต้องมีคำเตือนกำกับ"

    def test_clean_ledger_opens_the_section_without_noise(self, fake_st):
        opened = app._render_dividend_section_header(_dividend_summary(1))

        assert opened is True
        assert not fake_st.texts("error"), "ไม่มีแถวถูกตัด = ห้ามขึ้นคำเตือนหลอก"

    def test_no_dividend_and_nothing_skipped_stays_hidden(self, fake_st):
        """ยังไม่เคยรับปันผลจริง ๆ = ไม่ต้องเปิดหัวข้อว่างให้รก"""
        opened = app._render_dividend_section_header(_dividend_summary(0))

        assert opened is False
        assert not fake_st.calls, "ไม่มีอะไรต้องบอก ก็ไม่ต้องวาดอะไรเลย"
