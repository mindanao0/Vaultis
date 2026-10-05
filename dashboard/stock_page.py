# -*- coding: utf-8 -*-
"""หน้า STOCK-DCA — เลือกหุ้นรายตัว (ทดลอง · พอร์ตกระดาษ forward test) แยกจากพอร์ตหลัก DAR และ SELECT ทั้งหมด.

ตัวเลขทุกตัวมาจาก ``analysis/stock_pick.py`` และ ``portfolio/stock_ledger.py`` — หน้านี้แค่แสดงและกดบันทึก (import ตอนใช้เหมือนหน้า DAR/SELECT)
สถานะหลักฐานต้องอยู่บนสุดเสมอ: **ไม่มีหลักฐาน** จนกว่าจะครบ 36 cohort ตามที่ล็อกไว้
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analysis import stock_pick
from data.fetcher import PriceDataUnavailableError
from portfolio import stock_ledger
from utils.fx import FxRateUnavailable

BANGKOK_TZ = ZoneInfo("Asia/Bangkok")


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_prices(month: str) -> tuple[pd.DataFrame, dict[str, str]]:
    # ดึงทีละตัว (ไม่ใช้ yf.download) แคช 1 ชม. ต่อเดือน · ล้มเหลวไม่ถูกแคช (st.cache_data ไม่เก็บผลที่โยน)
    return stock_pick.fetch_prices()


def _fmt(v: float | None, fmt: str = "{:.2f}") -> str:
    return "—" if v is None or not math.isfinite(v) else fmt.format(v)


def _evidence_section(status: str, n: int) -> None:
    st.subheader("สถานะหลักฐาน")
    lock = stock_pick.lock_status()
    if not lock["ok"]:
        st.error(f"{lock['reason']}")
    st.error(f"**ยังไม่มีหลักฐานว่าสูตรนี้ได้ผล** — สถานะตอนนี้: {status} · cohort ที่บันทึกแล้ว {n}/{stock_pick.MIN_COHORTS}")
    with st.expander("ข้อควรระวังที่ต้องอ่านคู่กับทุกตัวเลขในหน้านี้", expanded=True):
        for cav in stock_pick.EVIDENCE_CAVEATS:
            st.markdown(f"- {cav}")
        st.markdown("สูตรและเกณฑ์ที่ล็อก: `research/stock_pick/PREREG.md` (+ `LOCK.sha256`)")


def _plan_section(month: pd.Period) -> stock_pick.StockPlan | None:
    st.subheader(f"แผนเดือน {month.strftime('%m/%Y')} — เลือกหุ้น 5 ตัวที่ผันผวนต่ำสุด")
    try:
        with st.spinner("กำลังดึงราคาหุ้น 30 ตัว + VOO ทีละตัว ใช้เวลาครู่หนึ่ง..."):
            from utils.fx import get_usdthb  # noqa: PLC0415

            prices, failed = _cached_prices(str(month))
            fx = get_usdthb()
            plan = stock_pick.build_plan(prices, month, fx_rate=float(fx.rate), fx_is_live=bool(fx.is_live), failed=failed)
    except (PriceDataUnavailableError, FxRateUnavailable, stock_pick.StockPickUnavailableError, ValueError) as exc:
        st.error(f"คำนวณแผน STOCK ไม่ได้: {exc}")
        st.caption("ไม่มีการเดาแทน — ลองใหม่ภายหลัง (yfinance อาจจำกัดการเรียกชั่วคราว)")
        return None
    rows = [{"หุ้น": ln.ticker, "บริษัท": ln.name, "อันดับ": ln.rank, "ผันผวน 3 ปี (ต่อปี)": _fmt(ln.vol_pct, "{:.1f}%"), "ซื้อ (บาท)": f"{ln.amount_thb:,}",
             "≈ หน่วย": _fmt(ln.units, "{:,.4f}"), "ราคาล่าสุด": _fmt(ln.price_usd, "${:,.2f}")} for ln in plan.lines]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.success("แผนกระดาษเดือนนี้: " + " · ".join(f"{ln.ticker} {ln.amount_thb:,} บาท" for ln in plan.lines) + f" (รวม {plan.total_thb:,} บาท)")
    st.caption(f"ราคาถึง {plan.data_through} · อัตราแลกเปลี่ยน {plan.fx_rate:.2f} บาท/ดอลลาร์" + ("" if plan.fx_is_live else " (ค่าสำรอง)")
               + " · หน่วยหักค่าธรรมเนียม Dime 0.15% โดยประมาณ · " + stock_pick.PRICE_COLUMN_NOTE)
    for n in plan.notes:
        st.info(n)
    if plan.skipped:
        st.warning("ถูกตัดออกจากเดือนนี้ (ไม่เดาแทน): " + "; ".join(f"{k} — {v}" for k, v in plan.skipped.items()))
    with st.expander("อันดับความผันผวนของทั้งจักรวาลที่ผ่านเกณฑ์"):
        st.dataframe(pd.DataFrame([{"หุ้น": t, "ผันผวน (ต่อปี)": f"{v:.1f}%"} for t, v in plan.ranking]), hide_index=True, use_container_width=True)
    st.warning("พอร์ตกระดาษ — ไม่ได้สั่งซื้อจริง · ถ้าจะซื้อจริงเอง ตรวจก่อนว่า Dime ขายหุ้นเหล่านี้ (รายชื่อมาจากหุ้นใหญ่สหรัฐ **ไม่ใช่การยืนยันกับโบรก**) และอย่าอ่านแผนนี้ว่าผ่านการพิสูจน์")
    return plan


def _record_section(plan: stock_pick.StockPlan) -> None:
    st.subheader("บันทึกแผนเดือนนี้เข้าพอร์ตกระดาษ")
    st.caption("บันทึกได้เดือนละครั้ง เฉพาะเดือนปัจจุบัน ตามแผนเป๊ะ — แก้ไม่ได้ ลบทีหลังไม่มีปุ่มให้ (กติกาที่ล็อกกันการเลือกเดือนที่ดูดี) · หลักฐานคือการบันทึกทุกเดือนโดยไม่เว้น")
    if st.button("บันทึกแผนเดือนนี้", key="stock_record_btn"):
        try:
            ids = stock_ledger.record_plan(plan)
        except stock_ledger.StockLedgerError as exc:
            st.error(f"ยังไม่ได้บันทึก: {exc}")
        else:
            st.success(f"บันทึกแล้ว {len(ids)} รายการ")
            st.rerun()


def _portfolio_section(prices: pd.DataFrame | None, apply_theme: Callable[[go.Figure], go.Figure]) -> tuple[str, int]:
    st.subheader("ผล forward test เทียบ 3 แขน")
    try:
        tx = stock_ledger.load_stock_transactions()
    except stock_ledger.StockLedgerError as exc:
        st.error(str(exc))
        return "อ่านสมุดไม่ได้", 0
    if tx.empty:
        st.info("ยังไม่มี cohort — บันทึกแผนเดือนนี้เพื่อเริ่มนับ")
        return "ยังไม่มีข้อมูล", 0
    if prices is None:
        st.error("ไม่มีราคาล่าสุดรอบนี้ จึงประเมินผลไม่ได้")
        return "ประเมินไม่ได้", int(tx["plan_month"].nunique())
    try:
        from utils.fx import get_usdthb  # noqa: PLC0415

        res = stock_ledger.compare(tx, prices, float(get_usdthb().rate))
    except (FxRateUnavailable, ValueError) as exc:
        st.error(f"ประเมินผลไม่ได้รอบนี้: {exc}")
        return "ประเมินไม่ได้", int(tx["plan_month"].nunique())
    status = res["status"]
    box = {"ผ่าน": st.success, "ไม่ผ่าน": st.error}.get(status, st.warning)
    box(f"**สถานะ: {status}**" + "".join(f"\n\n- {r}" for r in res["reasons"]))
    if res["picks_value_thb"] is not None:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("เงินกระดาษที่ลง", f"{res['invested_thb']:,.0f} บาท")
        c2.metric("มูลค่าแขนเลือก", f"{res['picks_value_thb']:,.0f} บาท")
        c3.metric("เทียบแบ่งเท่ากันทั้งจักรวาล", f"{res['diff_vs_shadow_pct']:+.2f}% ของเงินที่ลง")
        c4.metric("เทียบ VOO", f"{res['diff_vs_voo_pct']:+.2f}% ของเงินที่ลง")
        coh = res["cohorts"]
        fig = go.Figure()
        fig.add_trace(go.Bar(x=coh["plan_month"], y=coh["vs_shadow_pct"], name="เทียบเงาแบ่งเท่ากัน"))
        fig.add_trace(go.Bar(x=coh["plan_month"], y=coh["vs_voo_pct"], name="เทียบ VOO"))
        fig.update_layout(height=300, yaxis_title="ส่วนต่าง (% ของเงินที่ลงใน cohort)", barmode="group")
        st.plotly_chart(apply_theme(fig), use_container_width=True)
        st.caption("ทุกแขนใช้เงินก้อนเดียวกันวันเดียวกัน วัดด้วยราคาปรับปันผลชุดเดียวกัน · cohort ซ้อนทับกัน ไม่อิสระต่อกัน อย่าอ่านเป็นนัยสำคัญทางสถิติ")
    with st.expander(f"รายการที่บันทึกทั้งหมด ({len(tx)} แถว)"):
        view = tx.copy()
        view["date"] = view["date"].dt.strftime("%Y-%m-%d")
        st.dataframe(view, hide_index=True, use_container_width=True)
    return status, int(res["n_cohorts"])


def _method_section() -> None:
    with st.expander("โหมดนี้คิดอย่างไร ทำไมไม่มี backtest"):
        st.markdown(
            f"""
**สูตร (ปัจจัยเดียว):** ทุกเดือนเลือก {stock_pick.K} ตัวที่ความผันผวนรายปี 3 ปีต่ำสุดจากจักรวาลตายตัว {len(stock_pick.UNIVERSE)} ตัว แบ่งเท่ากัน {stock_pick.MONTHLY_BUDGET_THB:,.0f} บาท/เดือน ไม่ขาย ·
ทุกค่าตายตัวในโค้ด ไม่อ่านจาก config.json

**ทำไมไม่ backtest:** ข้อมูลฟรีไม่มีหุ้นที่ล้ม/ถูกซื้อไปแล้ว (วัด 2026-10-05: SIVB FRC TWTR ATVI XLNX ฯลฯ ว่างเปล่า) ผลย้อนหลังจึงเห็นแต่ผู้รอดและดูดีเกินจริง
ถ้าอยากให้ backtest ได้ ต้องซื้อข้อมูลที่รวมหุ้นตายแล้ว (เช่น Norgate / Sharadar) · ตอนนี้หลักฐานเดียวที่ซื่อสัตย์คือบันทึกแผนไปข้างหน้า

**เกณฑ์ที่ล็อก:** ก่อนครบ {stock_pick.MIN_COHORTS} เดือนตอบได้อย่างเดียวว่า "ยังตอบไม่ได้" · ครบแล้วต้องชนะทั้งเงาแบ่งเท่ากันและ VOO และ cohort อายุ ≥ 12 เดือนชนะเงา ≥ {stock_pick.MIN_WIN_RATE_PCT:.0f}% ·
ผู้พัฒนาทำนายล่วงหน้าว่า ~70% ไม่ชนะ VOO

**ยังไม่มี:** Discord รายเดือน (ไม่ได้ทำ — เปิดทีหลังได้) · โลกจำลอง 5–20 ปี (engine ไม่มีนโยบายคัดหุ้น) · ภาษีปันผล/FX spread
"""
        )


def render_stock_page(apply_theme: Callable[[go.Figure], go.Figure] = lambda f: f) -> None:
    st.header("STOCK-DCA · เลือกหุ้นรายตัว (ทดลอง · พอร์ตกระดาษ)")
    st.info("พอร์ตกระดาษแยกจากพอร์ตหลัก DAR และ SELECT ทั้งหมด · **forward test ที่ยังไม่มีหลักฐาน** ไม่ใช่คำแนะนำลงทุน")
    evidence_slot = st.container()  # ต้องอยู่บนสุดเสมอ แต่รู้สถานะหลังประเมินพอร์ตเท่านั้น
    st.divider()
    month = stock_pick.plan_month_of(datetime.now(BANGKOK_TZ))
    plan = _plan_section(month)
    if plan is not None:
        _record_section(plan)
    st.divider()
    prices = None
    try:
        prices = _cached_prices(str(month))[0]
    except (PriceDataUnavailableError, stock_pick.StockPickUnavailableError):
        prices = None
    status, n = _portfolio_section(prices, apply_theme)
    with evidence_slot:
        _evidence_section(status, n)
    _method_section()
