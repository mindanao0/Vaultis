# -*- coding: utf-8 -*-
"""หน้า SELECT-DCA — โหมดทดลอง "โมเดลเลือกกองเอง" + พอร์ตแยกที่เริ่มจากศูนย์ (ไม่เกี่ยวกับพอร์ตหลัก blend/ERC และพอร์ต DAR).

หน้าเดียวจบ: **สถานะหลักฐาน (ต้องอยู่บนสุดเสมอ)** · แผนเดือนนี้ (โมเดลเลือก 5 จาก 12 ตลาดเอง) · บันทึกการซื้อ ·
พอร์ต SELECT เทียบพอร์ตเงาแบ่งเท่ากันทั้งจักรวาล · วิธีคิด · ข้อควรระวัง
ตัวเลขทุกตัวมาจาก ``analysis/select_dca.py`` และ ``portfolio/select_ledger.py`` — หน้านี้แค่แสดงและบันทึก (import ตอนใช้เหมือนหน้า DAR)
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analysis import select_dca
from data.fetcher import PriceDataUnavailableError
from portfolio import select_ledger
from utils.fx import FxRateUnavailable

BANGKOK_TZ = ZoneInfo("Asia/Bangkok")


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_data(month: str) -> select_dca.MarketData:
    # ข้อมูลดิบของจักรวาลทั้งหมด (ทีละกอง ไม่ใช้ yf.download) — แคช 1 ชม. ต่อเดือน · ล้มเหลวไม่ถูกแคช (st.cache_data ไม่เก็บผลที่โยน)
    return select_dca.fetch_market_data()


def _evidence_section() -> None:
    st.subheader("สถานะหลักฐาน")
    try:
        ev = select_dca.evidence_summary()
    except select_dca.SelectUnavailableError as exc:
        st.error(f"แสดงสถานะหลักฐานไม่ได้: {exc} — อย่าอ่านว่ากติกานี้ผ่านการยืนยัน")
        return
    st.warning(
        "**ผ่านเกณฑ์ที่ล็อกไว้ก่อนรัน แต่ไม่ใช่หลักฐานว่าใช้เงินจริงได้** — ข้อมูลยืนยัน: " + ev["data"] + f" · {ev['windows']} หน้าต่าง DCA 20 ปี (ซ้อนทับกันมาก) · "
        "เกณฑ์ (ต้องครบ): " + ev["thresholds"]
    )
    rows = []
    for r in ev["rows"].values():
        lo, hi = r["annual_excess_ci95_pct"]
        rows.append({
            "กฎ": r["label"], "เงินปลายทางเฉลี่ย": f"{r['mean_pct']:+.2f}%", "ชนะ": f"{r['win_pct']:.0f}%", "10% ที่แย่สุด": f"{r['p10_pct']:+.2f}%",
            "แย่สุด": f"{r['worst_pct']:+.1f}%", "ส่วนเกิน/ปี [CI95]": f"{r['annual_excess_pct']:+.2f}% [{lo:+.2f}, {hi:+.2f}]",
            "ครึ่งแรก / ครึ่งหลัง": f"{r['first_half_mean_pct']:+.1f} / {r['second_half_mean_pct']:+.1f}",
            "เทียบ DAR เอียงทุกตลาด": f"{r['vs_dar_all_mean_pct']:+.2f}%", "น้ำหนักตลาดเดียวสูงสุดตอนจบ": f"{r['max_single_market_weight_pct']:.0f}%",
            "ผ่านเกณฑ์": "ผ่าน" if r["passed"] else "ไม่ผ่าน"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    with st.expander("ข้อควรระวังที่ต้องอ่านคู่กับตัวเลขข้างบน", expanded=True):
        for cav in ev["caveats"]:
            st.markdown(f"- {cav}")
        st.markdown("รายละเอียดและตัวเลขดิบ: `research/dar_select/RESULT.md`, `PREREG.md`, `results_confirm.json`")


def _fmt(v: float | None, fmt: str = "{:.2f}") -> str:
    return "—" if v is None or not math.isfinite(v) else fmt.format(v)


def _plan_section(month: pd.Period) -> select_dca.SelectPlan | None:
    st.subheader(f"แผนเดือน {month.strftime('%m/%Y')} — โมเดลเลือกให้เอง")
    try:
        with st.spinner("กำลังคำนวณแผน SELECT (ดึงราคา/ปันผลของจักรวาล 13 กอง ใช้เวลาครู่หนึ่ง)..."):
            from utils.fx import get_usdthb  # noqa: PLC0415

            data = _cached_data(str(month))
            tx = select_ledger.load_select_transactions()
            fx = get_usdthb()
            plan = select_dca.build_plan(data, month, select_dca.holdings_value_usd(tx, data), fx_rate=float(fx.rate), fx_is_live=bool(fx.is_live))
    except (PriceDataUnavailableError, FxRateUnavailable, select_dca.SelectUnavailableError, select_ledger.SelectLedgerError, ValueError) as exc:
        st.error(f"คำนวณแผน SELECT ไม่ได้: {exc}")
        st.caption("ไม่มีการเดาแทน — ลองใหม่ภายหลัง (yfinance อาจจำกัดการเรียกชั่วคราว)")
        return None
    rows = [{"กอง": ln.ticker, "ตลาด": ln.name_th, "อันดับ": ln.rank, "ปันผล 12 เดือน": _fmt(ln.yield_pct, "{:.2f}%"), "ซื้อ (บาท)": f"{ln.amount_thb:,}",
             "≈ หน่วย": _fmt(ln.units, "{:,.4f}"), "ราคาล่าสุด": _fmt(ln.price_usd, "${:,.2f}"), "สัดส่วนที่ถืออยู่": f"{ln.holdings_share_pct:.1f}%"}
            for ln in sorted(plan.lines, key=lambda x: (x.rank, x.ticker))]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.success("ต้องซื้อเดือนนี้: " + " · ".join(f"{ln.ticker} {ln.amount_thb:,} บาท" for ln in sorted(plan.lines, key=lambda x: (x.rank, x.ticker))) + f" (รวม {plan.total_thb:,} บาท)")
    notes = [f"จัดอันดับ ณ {plan.asof} ด้วย dividend yield 12 เดือน (คงเดิมทั้งปี)", f"ราคาถึง {plan.data_through}",
             f"อัตราแลกเปลี่ยน {plan.fx_rate:.2f} บาท/ดอลลาร์" + ("" if plan.fx_is_live else " (ค่าสำรอง — หน่วยอาจคลาดเคลื่อน)"),
             "หน่วยหักค่าธรรมเนียม Dime 0.15% แล้ว (โดยประมาณ)", "ตลาดที่มูลค่า ≥ 25% ของพอร์ตไม่ซื้อเพิ่ม"]
    st.caption(" · ".join(notes))
    for n in plan.notes:
        st.info(n)
    if plan.skipped:
        st.caption("ยังไม่อยู่ในจักรวาล: " + ", ".join(f"{k} ({v})" for k, v in plan.skipped.items()))
    if plan.alt_rule_top:
        st.caption("เทียบเฉย ๆ — กฎ DAR top-5 (ผ่านเกณฑ์เหมือนกันแต่ไม่ได้ใช้ซื้อ) จะเลือก: " + ", ".join(plan.alt_rule_top))
    st.warning("ตรวจก่อนซื้อว่า Dime ขายกองเหล่านี้จริง — รายชื่อมาจากกฎของจักรวาล (ค่าธรรมเนียม ≤ 0.20% · ประวัติ ≥ 181 เดือน) **ไม่ใช่การยืนยันกับโบรก**")
    return plan


def _record_section(plan: select_dca.SelectPlan) -> None:
    st.subheader("บันทึกการซื้อเข้าพอร์ต SELECT")
    st.caption("ตั้งต้นจากแผนเดือนนี้ แก้ให้ตรงกับที่ซื้อจริงได้ (ช่องหน่วยเว้นว่างหรือ 0 = คำนวณจากเงิน/ราคา/อัตราให้) · ไม่ได้ซื้อกองไหนก็ลบแถวนั้นออกหรือใส่ 0")
    today = datetime.now(BANGKOK_TZ).date()
    base = pd.DataFrame([{"กอง": ln.ticker, "บาท": float(ln.amount_thb), "ราคา ($)": ln.price_usd, "หน่วย": ln.units} for ln in plan.lines])
    with st.form("select_record_form"):
        col1, col2 = st.columns(2)
        buy_date = col1.date_input("วันที่ซื้อ", value=today, key="select_buy_date")
        fx = col2.number_input("อัตราแลกเปลี่ยน (บาท/ดอลลาร์)", value=float(plan.fx_rate), step=0.01, format="%.4f", key="select_buy_fx")
        edited = st.data_editor(base, hide_index=True, use_container_width=True, key="select_buy_editor", num_rows="fixed")
        note = st.text_input("หมายเหตุ (ไม่บังคับ)", key="select_buy_note")
        if st.form_submit_button("บันทึกการซื้อทั้งชุด"):
            plan_amounts = {ln.ticker: ln.amount_thb for ln in plan.lines}
            rows: list[dict[str, Any]] = []
            for _, r in edited.iterrows():
                amount = r["บาท"]
                if amount is None or (isinstance(amount, float) and math.isnan(amount)) or float(amount) <= 0:
                    continue
                units = r["หน่วย"]
                if units is None or (isinstance(units, float) and math.isnan(units)) or float(units) <= 0:
                    px = None if pd.isna(r["ราคา ($)"]) else float(r["ราคา ($)"])
                    units = None if px is None else float(amount) / float(fx) * (1.0 - select_dca.DIME_FEE_RATE) / px
                rows.append({"date": buy_date.isoformat(), "plan_month": plan.plan_month, "ticker": r["กอง"], "amount_thb": amount, "fx_rate": fx,
                             "price_usd": r["ราคา ($)"], "units": units, "universe": ",".join(plan.universe), "rule": plan.rule,
                             "source": "plan" if float(amount) == float(plan_amounts.get(r["กอง"], -1)) else "manual", "note": note})
            try:
                ids = select_ledger.add_select_purchases(rows)
            except select_ledger.SelectLedgerError as exc:
                st.error(f"ยังไม่ได้บันทึก: {exc}")
            else:
                st.success(f"บันทึกแล้ว {len(ids)} รายการ")
                st.rerun()


def _portfolio_section(apply_theme: Callable[[go.Figure], go.Figure]) -> None:
    st.subheader("พอร์ต SELECT")
    try:
        tx = select_ledger.load_select_transactions()
    except select_ledger.SelectLedgerError as exc:
        st.error(str(exc))
        return
    if tx.empty:
        st.info("พอร์ตนี้ยังว่าง — เริ่มจากศูนย์ตามที่ตั้งใจ บันทึกการซื้อครั้งแรกจากฟอร์มด้านบน")
        return
    pos = select_ledger.positions(tx)
    comp, prices, fx_now = None, None, None
    try:
        from utils.fx import get_usdthb  # noqa: PLC0415

        need = sorted({t for u in tx["universe"] for t in str(u).split(",") if t} | set(tx["ticker"]))
        prices = pd.DataFrame({t: select_dca._ticker_history(t, True) for t in need})  # noqa: SLF001 - ดึงทีละกอง ราคาปรับปันผล
        fx_now = float(get_usdthb().rate)
        comp = select_ledger.compare_with_equal_shadow(tx, prices, fx_now)
    except (PriceDataUnavailableError, FxRateUnavailable, ValueError) as exc:
        st.error(f"ประเมินมูลค่า/เทียบผลไม่ได้รอบนี้: {exc}")
    c1, c2, c3 = st.columns(3)
    c1.metric("เงินที่ลงไปแล้ว", f"{tx['amount_thb'].sum():,.0f} บาท")
    if comp and comp["rows_used"]:
        c2.metric("มูลค่า (รวมปันผล)", f"{comp['select_value_thb']:,.0f} บาท")
        c3.metric("เทียบเงาแบ่งเท่ากันทั้งจักรวาล", f"{comp['diff_thb']:+,.0f} บาท", f"{comp['diff_pct_of_invested']:+.2f}% ของเงินที่ลง")
        path = comp["path"]
        if not path.empty:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=path.index, y=path["select_usd"], name="พอร์ต SELECT", line=dict(width=2)))
            fig.add_trace(go.Scatter(x=path.index, y=path["shadow_usd"], name="เงาแบ่งเท่ากัน", line=dict(width=2)))
            fig.add_trace(go.Scatter(x=path.index, y=path["invested_usd"], name="เงินที่ลง", line=dict(width=1, color="#7D8590")))
            fig.update_layout(height=320, yaxis_title="มูลค่า (ดอลลาร์ รวมปันผล)", hovermode="x unified")
            st.plotly_chart(apply_theme(fig), use_container_width=True)
        st.caption("เงาแบ่งเท่ากันใช้เงินก้อนเดียวกันวันเดียวกัน แบ่งเท่า ๆ กันให้ **ทุกตลาดในจักรวาลของเดือนนั้น** วัดทั้งสองขาด้วยราคารวมปันผลชุดเดียวกัน "
                   "ส่วนต่างจึงมาจากการเลือกล้วน ๆ · ต้องใช้เวลาหลายปีกว่าจะแยกจากดวงได้ อย่าตัดสินจากไม่กี่เดือน")
        for ex in comp["rows_excluded"]:
            st.warning(f"ไม่ได้นับ {ex['tx_id']} ({ex['date']}): {ex['reason']}")
    last = prices.ffill().iloc[-1] if prices is not None and fx_now else None
    rows = []
    total_value = 0.0
    values: dict[str, float | None] = {}
    for t, r in pos.iterrows():
        v = None if (last is None or t not in last.index or pd.isna(last[t])) else float(r["units"]) * float(last[t]) * fx_now
        values[t] = v
        total_value += v or 0.0
    for t, r in pos.iterrows():
        v = values[t]
        share = None if (v is None or total_value <= 0) else v / total_value * 100.0
        rows.append({"กอง": t, "หน่วยสะสม": f"{r['units']:,.4f}", "เงินที่ลง (บาท)": f"{r['invested_thb']:,.0f}",
                     "มูลค่าตอนนี้ (บาท)": "—" if v is None else f"{v:,.0f}", "สัดส่วนของพอร์ต": "—" if share is None else f"{share:.1f}%",
                     "ติดเพดาน 25%": "ใช่ (ไม่ซื้อเพิ่ม)" if (share is not None and share >= select_dca.GUARD * 100) else ""})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.caption("เพดาน 25% กัน \"การซื้อเพิ่ม\" เท่านั้น ไม่ได้ขายตลาดที่ราคาขึ้นจนเกิน — ในการทดสอบตอนจบมีตลาดเดียวสูงสุด 46–49% ของพอร์ต")
    with st.expander(f"รายการซื้อทั้งหมด ({len(tx)} รายการ)"):
        view = tx.copy()
        view["date"] = view["date"].dt.strftime("%Y-%m-%d")
        st.dataframe(view, hide_index=True, use_container_width=True)
        options = {f"{r.date:%Y-%m-%d} · {r.ticker} · {r.amount_thb:,.0f} บาท · {r.tx_id}": r.tx_id for r in tx.itertuples()}
        pick = st.selectbox("เลือกรายการที่จะลบ", list(options), key="select_delete_pick")
        if st.button("ลบรายการนี้", key="select_delete_btn"):
            if select_ledger.delete_select_transaction(options[pick]):
                st.success("ลบแล้ว")
                st.rerun()
            else:
                st.error("ไม่พบรายการนี้ในสมุดแล้ว")


def _method_section() -> None:
    with st.expander("โหมดนี้คิดอย่างไร และอะไรที่ยังไม่ได้ทดสอบ"):
        st.markdown(
            f"""
**ทุกเดือน** โมเดลเลือกเองว่าจะซื้อตลาดไหน: จัดอันดับ {len(select_dca.UNIVERSE)} ตลาดรายประเทศ (ETF ค่าธรรมเนียม ≤ 0.20%) ด้วย **dividend yield 12 เดือนล่าสุด ณ สิ้นปีก่อนหน้า**
แล้วซื้อ {select_dca.K} ตลาดที่สูงสุด แบ่งเท่ากัน · อันดับคงเดิมทั้งปี · ตลาดที่มูลค่าเกิน 25% ของพอร์ตไม่ซื้อเพิ่ม (ถ้าติดเพดานหมด ปลดเพดานเดือนนั้น)
งบ {select_dca.MONTHLY_BUDGET_THB:,.0f} บาท/เดือน ไม่ขาย · **ทุกค่า (รายชื่อตลาด K เพดาน งบ) ตายตัวในโค้ด** ไม่อ่านจาก config.json

ที่มา: `research/dar_select/` — แผนวิจัย → ลงทะเบียนล่วงหน้า (SHA-256) → รันยืนยันครั้งเดียวบนตลาดรายประเทศ 1887–1974 → ผ่านเกณฑ์ที่ล็อกไว้ทั้งสองกฎ
**สิ่งที่ยังไม่ได้ทดสอบ:** ETF รายประเทศปี 2026 · ค่าธรรมเนียมกอง/Dime · ภาษีปันผล 15% · การซื้อรายเดือนแทนรายปี · การต่อประวัติด้วยกองพี่ ·
ความเสี่ยงที่ตลาดหนึ่งกินพอร์ต (ไม่ขาย) · ยังไม่ได้ลองในโลกจำลอง 5–20 ปีของ simulation (engine ยังไม่รองรับกฎคัดกอง)

**ข้อความ Discord รายเดือนของโหมดนี้ปิดไว้ก่อน** — เปิดที่หน้า Settings หลังตรวจว่า Dime ขายกองในจักรวาลจริง
"""
        )


def render_select_page(apply_theme: Callable[[go.Figure], go.Figure] = lambda f: f) -> None:
    st.header("SELECT-DCA · โมเดลเลือกกองเอง (ทดลอง)")
    st.info("พอร์ตทดลองที่แยกจากพอร์ตหลัก (blend) และพอร์ต DAR ทั้งหมด เริ่มจากศูนย์ · โมเดลเลือก 5 จาก 12 ตลาดรายประเทศเอง · **ผ่านเกณฑ์ที่ล็อกไว้ แต่ไม่ใช่หลักฐานว่าใช้เงินจริงได้**")
    _evidence_section()
    st.divider()
    month = select_dca.plan_month_of(datetime.now(BANGKOK_TZ))
    plan = _plan_section(month)
    if plan is not None:
        _record_section(plan)
    st.divider()
    _portfolio_section(apply_theme)
    _method_section()
