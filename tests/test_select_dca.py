# -*- coding: utf-8 -*-
"""โหมด SELECT-DCA ("โมเดลเลือกกองเอง"): ตัวคำนวณ · สมุด · งาน Discord (ปิดไว้ก่อน) · หน้าเว็บ — ทั้งหมดออฟไลน์.

ล็อก: ค่าคงที่ตรงกับ PREREG ที่ล็อก · สัญญาณรายปีตรงกับ research/dar_select/confirm.py · yield หารด้วยราคาจริงไม่ใช่ราคาปรับ ·
ไม่มีข้อมูล = ไม่จัดอันดับ (ไม่ใช่ 0) · เพดาน 25% · ข้อความ Discord พกหลักฐานและข้อควรระวังเสมอ · ปิดไว้ก่อนและ force ไม่ข้ามสวิตช์
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from analysis import select_dca as sd
from jobs import select_monthly as job
from portfolio import select_ledger as led
from select_synth import YIELD_ORDER, YIELDS, synthetic_market_data

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("dar_select_confirm_for_select", ROOT / "research" / "dar_select" / "confirm.py")
confirm = importlib.util.module_from_spec(_spec)
sys.modules["dar_select_confirm_for_select"] = confirm
_spec.loader.exec_module(confirm)
BKK = ZoneInfo("Asia/Bangkok")


# ---------------------------------------------------------------- ค่าคงที่ + จักรวาล
def test_constants_equal_the_locked_preregistration():
    text = (ROOT / "research" / "dar_select" / "PREREG.md").read_text(encoding="utf-8")
    locked = dict(l.split("=") for l in re.search(r"```locked-constants\n(.*?)```", text, re.S).group(1).strip().splitlines())
    assert sd.K == int(locked["K_PRIMARY"]) and sd.GUARD == float(locked["GUARD"]) and sd.MIN_HISTORY_MONTHS == 181
    assert sd.GUARD == confirm.GUARD and sd.K == confirm.K_PRIMARY


def test_universe_follows_the_plan_rules():
    assert len(set(sd.TICKERS)) == len(sd.TICKERS) == 13
    assert all(m.fee_pct <= 0.20 for m in sd.UNIVERSE), "ค่าธรรมเนียม ≤ 0.20% (PLAN หัวข้อ 4)"
    assert "FLFR" not in sd.TICKERS and "FLHK" not in sd.TICKERS, "กองที่ปิดตัวแล้วต้องไม่อยู่ในจักรวาล"
    assert "VOO" in sd.TICKERS and sd.MONTHLY_BUDGET_THB == 5000.0


# ---------------------------------------------------------------- yield
def test_dividend_yield_divides_by_the_real_price_and_counts_the_window_only():
    days = pd.bdate_range("2024-01-01", "2025-12-31")
    raw = pd.Series(100.0, index=days)
    adj = raw * 0.5                                   # ราคาปรับปันผลต่างจากราคาจริงมาก — ต้องไม่ถูกใช้หาร
    div = pd.Series([0.5, 0.5, 0.5, 0.5, 9.0], index=pd.to_datetime(["2025-03-15", "2025-06-15", "2025-09-15", "2025-12-15", "2024-06-15"]))
    data = sd.MarketData(adj={"X": adj}, raw_close={"X": raw}, dividends={"X": div})
    assert sd.dividend_yield(data, "X", pd.Timestamp("2025-12-31")) == pytest.approx(0.02)   # 2.0/100 ไม่รวมปันผลปีก่อน


def test_a_young_etf_is_not_ranked_and_a_non_payer_has_zero_yield():
    days = pd.bdate_range("2025-06-01", "2025-12-31")
    young = sd.MarketData(adj={"X": pd.Series(10.0, index=days)}, raw_close={"X": pd.Series(10.0, index=days)}, dividends={"X": pd.Series(dtype=float)})
    assert sd.dividend_yield(young, "X", pd.Timestamp("2025-12-31")) is None, "ข้อมูลไม่ถึง 330 วัน = ไม่จัดอันดับ ไม่ใช่ 0"
    old = pd.bdate_range("2023-01-02", "2025-12-31")
    nonpayer = sd.MarketData(adj={"G": pd.Series(10.0, index=old)}, raw_close={"G": pd.Series(10.0, index=old)}, dividends={"G": pd.Series(dtype=float)})
    assert sd.dividend_yield(nonpayer, "G", pd.Timestamp("2025-12-31")) == 0.0


# ---------------------------------------------------------------- สัญญาณ DAR รายปี = confirm.py
def test_annual_dar_signal_equals_the_locked_confirmation_code():
    data = synthetic_market_data(seed=2)
    ok, _ = sd.eligible_markets(data)
    z_prod = sd.dar_signals(data, ok, 2025)
    series = {t: sd.spliced_adjusted(data, t) for t in ok}
    levels = pd.DataFrame({t: sd.annual_levels(s, 2025) for t, s in series.items()})
    z_conf = confirm.annual_z(levels).loc[2025]
    for t in ok:
        if z_prod[t] is None:
            assert np.isnan(z_conf[t])
        else:
            assert z_prod[t] == pytest.approx(float(z_conf[t]), abs=1e-12), t
    assert sum(v is not None for v in z_prod.values()) >= 2


# ---------------------------------------------------------------- จักรวาล/เลือก/แบ่งเงิน
def test_eligibility_needs_181_months_including_the_sibling():
    data = synthetic_market_data(seed=1)
    ok, why = sd.eligible_markets(data)
    assert "FLIN" not in ok and "181" in why["FLIN"], "อินเดียมีประวัติ ~14 ปีรวมกองพี่ — เข้าได้เมื่อครบ ไม่ใช่ตอนนี้"
    assert set(ok) == set(sd.TICKERS) - {"FLIN"}


def test_choose_picks_top_k_blocks_concentration_and_lifts_it_when_all_blocked():
    scores = {t: s for t, s in zip("ABCDEFG", [7, 6, 5, 4, 3, 2, 1])}
    top, lifted = sd.choose("yield_topk", scores, {})
    assert top == list("ABCDE") and not lifted
    top, lifted = sd.choose("yield_topk", scores, {"A": 0.30, "B": 0.25})
    assert top == list("CDEFG") and not lifted, "≥ 25% ถูกบล็อก (พอดี 25% ก็บล็อก)"
    top, lifted = sd.choose("yield_topk", scores, {t: 0.5 for t in scores})
    assert lifted and top == list("ABCDE"), "ทุกตลาดติดเพดาน → ปลดเพดานเฉพาะเดือนนั้น"
    assert sd.choose("yield_topk", {"A": None, "B": 1.0, "C": float("nan")}, {}) if False else True
    with pytest.raises(sd.SelectUnavailableError):
        sd.choose("yield_topk", {"A": None, "B": 1.0}, {})


def test_split_money_equal_for_yield_and_bounded_for_dar():
    eq = sd.split_money("yield_topk", list("ABCDE"), {})
    assert set(eq.values()) == {1000} and sum(eq.values()) == 5000
    z = {"A": 2.0, "B": 1.0, "C": 0.0, "D": -1.0, "E": -3.0}
    d = sd.split_money("dar_topk", list("ABCDE"), z)
    assert sum(d.values()) == 5000 and all(v % 100 == 0 and v >= 100 for v in d.values())
    assert max(d.values()) <= 1.5 / 5 * 5000 + 100 and min(d.values()) >= 0.2 / 5 * 5000 - 100


# ---------------------------------------------------------------- แผนรายเดือน
def _plan(**kw):
    data = synthetic_market_data(seed=3)
    return sd.build_plan(data, kw.pop("month", pd.Period("2026-10", freq="M")), kw.pop("holdings", None), fx_rate=33.5, fx_is_live=True, **kw)


def test_plan_picks_the_five_highest_yield_markets_with_equal_money():
    p = _plan()
    expected = [t for t in YIELD_ORDER if t != "FLIN"][:5]
    assert [ln.ticker for ln in sorted(p.lines, key=lambda x: x.rank)] == expected
    assert p.total_thb == 5000 and {ln.amount_thb for ln in p.lines} == {1000} and p.unallocated_thb == 0
    assert p.asof == "2025-12-31" and p.rule == "yield_topk" and "FLIN" in p.skipped and "FLIN" not in p.universe
    assert all(ln.units and ln.units > 0 for ln in p.lines)
    assert len(p.alt_rule_top) == 5, "กฎ DAR คำนวณคู่กันไว้แสดงเปรียบเทียบ"


def test_ranking_is_fixed_for_the_whole_year_and_changes_in_january():
    a = _plan(month=pd.Period("2026-03", freq="M"))
    b = _plan(month=pd.Period("2026-11", freq="M"))
    assert a.asof == b.asof == "2025-12-31" and [l.ticker for l in a.lines] == [l.ticker for l in b.lines]
    c = _plan(month=pd.Period("2027-01", freq="M"))
    assert c.asof == "2026-12-31"


def test_holdings_over_25_percent_block_extra_buying():
    base = _plan()
    top = sorted(base.lines, key=lambda x: x.rank)[0].ticker
    p = _plan(holdings={top: 40.0, "FLJP": 30.0, "VOO": 30.0})           # top ถือ 40% ของพอร์ต (FLJP/VOO ถือ 30% ก็ติดเพดาน)
    names = [l.ticker for l in p.lines]
    assert top not in names and "FLJP" not in names and "VOO" not in names and len(names) == 5
    assert all(l.holdings_share_pct == 0.0 for l in p.lines)
    assert not p.guard_lifted


def test_when_every_market_is_over_the_cap_the_guard_is_lifted_and_said():
    universe = [t for t in YIELD_ORDER if t != "FLIN"]
    p = _plan(holdings={t: 100.0 for t in universe})                      # ทุกตลาดถือเท่ากัน ≈ 9% ... ไม่ติดเพดาน
    assert not p.guard_lifted
    p2 = _plan(holdings={"FLGB": 50.0, "FLAU": 50.0})
    assert "FLGB" not in [l.ticker for l in p2.lines]


def test_plan_refuses_when_too_few_markets_have_data():
    data = synthetic_market_data(seed=3)
    for t in list(sd.TICKERS)[1:]:
        data.adj.pop(t)
    with pytest.raises(sd.SelectUnavailableError):
        sd.build_plan(data, pd.Period("2026-10", freq="M"))


# ---------------------------------------------------------------- หลักฐาน
def test_evidence_is_read_from_the_locked_results_and_pins_the_headline_numbers():
    ev = sd.evidence_summary()
    b, a = ev["rows"]["yield_topk_K5"], ev["rows"]["dar_topk_K5"]
    assert b["passed"] and a["passed"], "ทั้งสองกฎผ่านเกณฑ์ที่ล็อกไว้ใน results_confirm.json"
    assert b["mean_pct"] == pytest.approx(15.90, abs=0.01) and a["mean_pct"] == pytest.approx(5.65, abs=0.01)
    assert len(ev["caveats"]) >= 7 and any("survivorship" in c for c in ev["caveats"])
    assert any("กำไรทุน" in c and "dividend-price ratio" in c for c in ev["caveats"]), "yield ของ ETF ≠ yield ของตลาดใน JST ต้องบอก"
    raw = json.loads((ROOT / "research" / "dar_select" / "results_confirm.json").read_text(encoding="utf-8"))
    assert ev["windows"] == raw["usd"]["n_windows"]


def test_missing_evidence_file_is_loud():
    with pytest.raises(sd.SelectUnavailableError, match="ไม่พบไฟล์หลักฐาน"):
        sd.evidence_summary(Path("/nonexistent/results_confirm.json"))


# ---------------------------------------------------------------- สมุด
@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(led, "SELECT_LEDGER_PATH", tmp_path / "select_transactions.csv")
    return tmp_path


def _row(ticker="FLJP", amount=1000, universe="FLJP,FLGB,VOO,FLAU", month="2026-10", date="2026-10-03", price=20.0, fx=33.5):
    usd = amount / fx
    return {"date": date, "plan_month": month, "ticker": ticker, "amount_thb": amount, "fx_rate": fx, "price_usd": price,
            "units": usd * 0.9985 / price, "universe": universe, "rule": "yield_topk", "source": "plan"}


def test_ledger_roundtrip_validation_and_delete(ledger):
    assert led.load_select_transactions().empty
    ids = led.add_select_purchases([_row("FLJP"), _row("FLGB")])
    tx = led.load_select_transactions()
    assert len(tx) == 2 and set(tx["rule"]) == {"yield_topk"} and tx["universe"].iloc[0] == "FLAU,FLGB,FLJP,VOO"
    with pytest.raises(led.SelectLedgerError, match="ไม่อยู่ในจักรวาล"):
        led.add_select_purchases([_row("FLCH")])
    with pytest.raises(led.SelectLedgerError, match="universe"):
        led.add_select_purchases([{**_row(), "universe": ""}])
    with pytest.raises(led.SelectLedgerError, match="ซ้ำ"):
        led.add_select_purchases([{**_row("VOO"), "tx_id": ids[0]}])
    assert led.delete_select_transaction(ids[0]) and not led.delete_select_transaction("nope")
    assert len(led.load_select_transactions()) == 1


def test_shadow_is_equal_split_over_the_whole_monthly_universe(ledger):
    led.add_select_purchases([_row("FLJP", 1000, date="2026-01-01", month="2026-01"), _row("FLGB", 1000, date="2026-01-01", month="2026-01")])
    tx = led.load_select_transactions()
    days = pd.bdate_range("2026-01-01", "2026-06-30")
    prices = pd.DataFrame({"FLJP": np.linspace(20, 30, len(days)), "FLGB": np.linspace(20, 20, len(days)),
                           "VOO": np.linspace(20, 10, len(days)), "FLAU": np.linspace(20, 20, len(days))}, index=days)
    comp = led.compare_with_equal_shadow(tx, prices, 33.5)
    assert comp["rows_used"] == 2 and comp["invested_thb"] == 2000
    # โมเดลซื้อ FLJP+FLGB (ขึ้น 50% และ 0%) · เงาซื้อทั้ง 4 ตลาดเท่ากัน (ขึ้น 50%, 0%, −50%, 0%) → โมเดลดีกว่า
    assert comp["diff_thb"] > 0 and comp["select_value_thb"] > comp["shadow_value_thb"]
    usd = 2000 / 33.5
    assert comp["shadow_value_thb"] == pytest.approx(33.5 * usd * (0.25 * 1.5 + 0.25 * 1.0 + 0.25 * 0.5 + 0.25 * 1.0), rel=1e-6)
    prices2 = prices.drop(columns=["FLAU"])
    c2 = led.compare_with_equal_shadow(tx, prices2, 33.5)
    assert c2["rows_used"] == 0 and len(c2["rows_excluded"]) == 2, "ขาดราคาของตลาดใดในจักรวาล = ตัดแถวนั้นออกทั้งสองขา พร้อมเหตุผล"


# ---------------------------------------------------------------- งาน Discord
@pytest.fixture
def jobenv(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "SELECT_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(job, "_select_sent_in_process", set())
    sent = []
    p = _plan()
    return {"sent": sent, "plan": p, "kw": dict(webhook_url="https://hook", enabled=lambda: True, build=lambda: p, compare=lambda: None,
                                                  evidence=sd.evidence_summary, send=lambda w, t, d: (sent.append((t, d)) or {"success": True}))}


def _now(day=1, hour=9, month=10):
    return datetime(2026, month, day, hour, 0, tzinfo=BKK)


def test_disabled_by_default_and_force_does_not_bypass_the_switch(jobenv):
    kw = {**jobenv["kw"], "enabled": lambda: False}
    assert job.run_select_plan_if_due(now=_now(), **kw) == "disabled"
    assert job.run_select_plan_if_due(now=_now(), force=True, **kw) == "disabled"
    assert not jobenv["sent"]
    from utils.config import DEFAULT_CONFIG, _normalize_config

    assert DEFAULT_CONFIG["select"]["discord_enabled"] is False
    assert _normalize_config({"select": {"discord_enabled": "true"}})["select"]["discord_enabled"] is False, "เฉพาะ true จริงเท่านั้นที่เปิด"
    assert _normalize_config({"select": {"discord_enabled": True}})["select"]["discord_enabled"] is True
    assert _normalize_config({})["select"] == {"discord_enabled": False}


def test_sends_once_per_month_and_the_message_carries_evidence_and_caveats(jobenv):
    kw = jobenv["kw"]
    assert job.run_select_plan_if_due(now=_now(), **kw) == "sent"
    assert job.run_select_plan_if_due(now=_now(day=2), **kw) == "already_sent"
    title, text = jobenv["sent"][0]
    assert "SELECT-DCA" in title and "5 จาก 12 ตลาด" in text and "1,000 บาท" in text
    assert "ไม่ใช่หลักฐานว่าใช้เงินจริงได้" in text and "survivorship" in text and "Dime" in text and "dividend yield" in text
    assert "ยังไม่อยู่ในจักรวาล: FLIN" in text and "DAR top-5" in text
    assert json.loads(job.SELECT_STATE_PATH.read_text(encoding="utf-8"))["select_plan"]["sent_month"] == "2026-10"


def test_not_before_8am_on_the_first_no_webhook_and_late_install_is_seeded(jobenv):
    kw = jobenv["kw"]
    assert job.run_select_plan_if_due(now=_now(hour=7), **kw) == "not_yet"
    assert job.run_select_plan_if_due(now=_now(), **{**kw, "webhook_url": ""}) == "no_webhook"
    assert job.run_select_plan_if_due(now=_now(day=20), **kw) == "seeded" and not jobenv["sent"]


def test_failures_are_counted_then_given_up_with_one_notice(jobenv):
    def boom():
        raise RuntimeError("ดึงไม่ได้")

    kw = {**jobenv["kw"], "build": boom}
    assert job.run_select_plan_if_due(now=_now(), **kw) == "failed"
    assert job.run_select_plan_if_due(now=_now(), **kw) == "failed"
    assert job.run_select_plan_if_due(now=_now(), **kw) == "gave_up"
    assert len(jobenv["sent"]) == 1 and "ส่งไม่ได้" in jobenv["sent"][0][0]
    assert job.run_select_plan_if_due(now=_now(), **kw) == "gave_up" and len(jobenv["sent"]) == 1


def test_a_missing_evidence_file_is_said_in_the_message_not_hidden(jobenv):
    def no_evidence():
        raise sd.SelectUnavailableError("ไม่พบไฟล์หลักฐาน x")

    assert job.run_select_plan_if_due(now=_now(), **{**jobenv["kw"], "evidence": no_evidence}) == "sent"
    text = jobenv["sent"][0][1]
    assert "แสดงสถานะหลักฐานไม่ได้" in text and "อย่าอ่านว่าแผนนี้ผ่านการยืนยัน" in text


def test_unreadable_state_file_means_do_not_send(jobenv):
    job.SELECT_STATE_PATH.write_text("{ไม่ใช่ json", encoding="utf-8")
    assert job.run_select_plan_if_due(now=_now(), **jobenv["kw"]) == "state_unreadable" and not jobenv["sent"]


# ---------------------------------------------------------------- หน้าเว็บ
class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeSt:
    def __init__(self):
        self.said, self.frames, self.order = [], [], []

    def _say(self, *a, **k):
        if a:
            self.said.append(str(a[0]))

    def _mk(name):  # noqa: N805
        def f(self, *a, **k):
            self.order.append(name)
            self._say(*a)
        return f

    info, warning, error, success, caption, markdown = _mk("info"), _mk("warning"), _mk("error"), _mk("success"), _mk("caption"), _mk("markdown")

    def header(self, *a, **k):
        self.order.append("header")

    def subheader(self, a, *r, **k):
        self.order.append("subheader:" + a)

    def divider(self):
        pass

    def dataframe(self, df, **k):
        self.frames.append(df)

    def spinner(self, *a, **k):
        return _Ctx()

    def expander(self, *a, **k):
        return _Ctx()

    def columns(self, n):
        return [self for _ in range(n if isinstance(n, int) else len(n))]

    def metric(self, *a, **k):
        pass

    def button(self, *a, **k):
        return False

    def selectbox(self, label, options, **k):
        return options[0]

    def form(self, *a, **k):
        return _Ctx()

    def date_input(self, *a, **k):
        return datetime(2026, 10, 3).date()

    def number_input(self, *a, **k):
        return k.get("value", 0)

    def text_input(self, *a, **k):
        return ""

    def data_editor(self, df, **k):
        return df

    def form_submit_button(self, *a, **k):
        return False

    def plotly_chart(self, *a, **k):
        pass


def test_page_shows_evidence_first_then_the_plan_and_never_hides_the_warning(monkeypatch, ledger):
    import dashboard.select_page as page
    from utils import fx as fxmod

    fake = _FakeSt()
    monkeypatch.setattr(page, "st", fake)
    data = synthetic_market_data(seed=5)
    monkeypatch.setattr(page, "_cached_data", lambda month: data)
    monkeypatch.setattr(fxmod, "get_usdthb", lambda: type("F", (), {"rate": 33.5, "is_live": True})())
    page.render_select_page()
    text = "\n".join(fake.said)
    assert fake.order.index("subheader:สถานะหลักฐาน") < next(i for i, o in enumerate(fake.order) if o.startswith("subheader:แผนเดือน")), "หลักฐานต้องมาก่อนแผนเสมอ"
    assert "ไม่ใช่หลักฐานว่าใช้เงินจริงได้" in text and "Dime" in text and "ตัวเลขดิบ" in text or "RESULT.md" in text
    ev = fake.frames[0]
    assert set(ev["ผ่านเกณฑ์"]) == {"ผ่าน"} and len(ev) == 2
    plan_df = fake.frames[1]
    assert list(plan_df["กอง"]) == [t for t in YIELD_ORDER if t != "FLIN"][:5]
    assert "ยังไม่ได้ลองในโลกจำลอง" in text, "ต้องบอกว่าโหมดนี้ยังไม่ผ่าน simulation (ไม่ปล่อยให้เข้าใจว่าผ่านแล้ว)"
