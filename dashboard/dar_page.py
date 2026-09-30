# -*- coding: utf-8 -*-
"""หน้า DAR-DCA — สูตรแบ่งเงินทดลอง + พอร์ตแยกที่เริ่มจากศูนย์ (ไม่เกี่ยวกับแผน ERC หลัก).

หน้าเดียวจบ: แผนเดือนนี้ (ซื้อกองไหนกี่บาท ≈ กี่หน่วย) · บันทึกการซื้อ · พอร์ต DAR ·
ผลเทียบพอร์ตเงาแบ่งเท่ากัน · ข้อมูลภาษีปันผล · วิธีคิดของสูตร
ตัวเลขทุกตัวมาจาก ``analysis/dar_dca.py`` และ ``portfolio/dar_ledger.py`` — หน้านี้แค่แสดงและบันทึก
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analysis import dar_dca
from data.fetcher import PriceDataUnavailableError
from portfolio import dar_ledger
from utils.fx import FxRateUnavailable

BANGKOK_TZ = ZoneInfo("Asia/Bangkok")
REPORT_URL = "https://claude.ai/artifact/M9J5T1A4vVu4kWEG76BNbC"
DIVIDEND_WITHHOLDING = 0.15  # US–Thailand treaty rate on RIC dividends (ข้อมูลประกอบ ไม่เข้าสูตร)


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_plan(tickers: tuple[str, ...], budget: float, month: str) -> dar_dca.DarPlan:
    return dar_dca.build_plan(list(tickers), budget, pd.Period(month, freq="M"))


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_adj_prices(tickers: tuple[str, ...], years: int) -> pd.DataFrame:
    # ไม่ใช้ data.fetcher (yf.download) ใน process ของ dashboard — ดูเหตุผลที่ fetch_total_return_history
    return dar_dca.fetch_total_return_history(list(tickers), years=years)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _cached_yield(ticker: str) -> float | None:
    from analysis.financial_model import _dividend_yield  # noqa: PLC0415 - แหล่งเดียวของหน่วย yield

    try:
        return _dividend_yield(ticker)
    except Exception:  # noqa: BLE001 - ข้อมูลประกอบ: อ่านไม่ได้ = แสดง "ไม่ทราบ" ไม่ใช่ 0
        return None


def _fmt_pct(v: float | None, digits: int = 0) -> str:
    return "—" if v is None or not math.isfinite(v) else f"{v:+,.{digits}f}%"


def _settings_caption(tickers: list[str], budget: float) -> None:
    # อ่านอย่างเดียว — ค่าตายตัวใน analysis/dar_dca.py ไม่มีฟอร์ม และไม่อ่าน/เขียน config.json
    st.caption(
        f"กองในพอร์ต DAR: {' · '.join(tickers)} · งบ {budget:,.0f} บาท/เดือน — ตายตัวในโค้ด "
        "(`analysis/dar_dca.py`) ไม่อ่านจาก config.json และไม่ตามรายชื่อหรืองบของแผน ERC · "
        "สูตรไม่ได้เลือกกองเอง มันแบ่งเงินให้ทุกกองในรายชื่อนี้"
    )


def _plan_section(tickers: list[str], budget: float, month: pd.Period) -> dar_dca.DarPlan | None:
    st.subheader(f"แผนเดือน {month.strftime('%m/%Y')} — ซื้อกองไหนกี่บาท")
    try:
        with st.spinner("กำลังคำนวณแผน DAR (ดึงราคาย้อนหลัง 17 ปี)..."):
            plan = _cached_plan(tuple(tickers), float(budget), str(month))
    except (PriceDataUnavailableError, FxRateUnavailable, ValueError) as exc:
        st.error(f"คำนวณแผน DAR ไม่ได้: {exc}")
        st.caption("ไม่มีการเดาน้ำหนักแทน — ลองใหม่ภายหลัง (yfinance อาจจำกัดการเรียกชั่วคราว)")
        return None
    rows = []
    for ln in sorted(plan.lines, key=lambda x: -x.amount_thb):
        s = ln.signal
        rows.append(
            {
                "กอง": ln.ticker,
                "ซื้อ (บาท)": f"{ln.amount_thb:,}",
                "สัดส่วน": f"{ln.weight * 100:.1f}%",
                "≈ หน่วย": "—" if ln.units is None else f"{ln.units:,.4f}",
                "ราคาล่าสุด": "—" if ln.price_usd is None else f"${ln.price_usd:,.2f}",
                "5 ปีล่าสุด": _fmt_pct(s.ret_5y_pct),
                "10 ปีก่อนหน้า": _fmt_pct(s.ret_prior10y_pct),
                "DAR": "—" if s.dar is None else f"{s.dar:+.2f}",
                "สถานะ": s.label,
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    buy_line = " · ".join(f"{ln.ticker} {ln.amount_thb:,} บาท" for ln in sorted(plan.lines, key=lambda x: -x.amount_thb))
    st.success(f"ต้องซื้อเดือนนี้: {buy_line} (รวม {plan.total_thb:,} บาท)")
    notes = [
        f"ราคาถึงสิ้นเดือน {plan.data_through}",
        f"อัตราแลกเปลี่ยน {plan.fx_rate:.2f} บาท/ดอลลาร์" + ("" if plan.fx_is_live else " (ค่าสำรอง — หน่วยอาจคลาดเคลื่อน)"),
        "หน่วยหักค่าธรรมเนียม Dime 0.15% แล้ว (โดยประมาณ ราคาจริงตอนซื้อจะต่างเล็กน้อย)",
    ]
    if plan.siblings:
        notes.append("ยืดประวัติด้วยกองพี่: " + ", ".join(f"{k}←{v}" for k, v in plan.siblings.items()))
    if plan.unallocated_thb >= 1:
        notes.append(f"เศษจากการปัดหลักร้อยที่ยังไม่จัดสรร {plan.unallocated_thb:,.0f} บาท")
    for ln in plan.lines:
        if ln.signal.neutral_reason:
            notes.append(f"{ln.ticker} ได้ส่วนกลาง: {ln.signal.neutral_reason}")
    st.caption(" · ".join(notes))
    return plan


def _record_section(plan: dar_dca.DarPlan) -> None:
    st.subheader("บันทึกการซื้อเข้าพอร์ต DAR")
    st.caption("ตั้งต้นจากแผนเดือนนี้ แก้ให้ตรงกับที่ซื้อจริงได้ (ช่องหน่วยเว้นว่างหรือ 0 = คำนวณจากเงิน/ราคา/อัตราให้)")
    today = datetime.now(BANGKOK_TZ).date()
    base = pd.DataFrame(
        [
            {"กอง": ln.ticker, "บาท": float(ln.amount_thb), "ราคา ($)": ln.price_usd, "หน่วย": ln.units}
            for ln in plan.lines
        ]
    )
    with st.form("dar_record_form"):
        col1, col2 = st.columns(2)
        buy_date = col1.date_input("วันที่ซื้อ", value=today, key="dar_buy_date")
        fx = col2.number_input("อัตราแลกเปลี่ยน (บาท/ดอลลาร์)", value=float(plan.fx_rate), step=0.01, format="%.4f", key="dar_buy_fx")
        edited = st.data_editor(base, hide_index=True, use_container_width=True, key="dar_buy_editor", num_rows="fixed")
        note = st.text_input("หมายเหตุ (ไม่บังคับ)", key="dar_buy_note")
        if st.form_submit_button("บันทึกการซื้อทั้งชุด"):
            plan_amounts = {ln.ticker: ln.amount_thb for ln in plan.lines}
            rows = []
            for _, r in edited.iterrows():
                amount = r["บาท"]
                if amount is None or (isinstance(amount, float) and math.isnan(amount)) or float(amount) <= 0:
                    continue  # ไม่ได้ซื้อกองนี้ในรอบนี้
                units = r["หน่วย"]
                if units is None or (isinstance(units, float) and math.isnan(units)) or float(units) <= 0:
                    units = dar_dca.estimate_units(float(amount), float(fx), None if pd.isna(r["ราคา ($)"]) else float(r["ราคา ($)"]))
                rows.append(
                    {
                        "date": buy_date.isoformat(),
                        "plan_month": plan.plan_month,
                        "ticker": r["กอง"],
                        "amount_thb": amount,
                        "fx_rate": fx,
                        "price_usd": r["ราคา ($)"],
                        "units": units,
                        "source": "plan" if float(amount) == float(plan_amounts.get(r["กอง"], -1)) else "manual",
                        "note": note,
                    }
                )
            try:
                ids = dar_ledger.add_dar_purchases(rows)
            except dar_ledger.DarLedgerError as exc:
                st.error(f"ยังไม่ได้บันทึก: {exc}")
            else:
                st.success(f"บันทึกแล้ว {len(ids)} รายการ")
                st.rerun()


def _portfolio_section(apply_theme: Callable[[go.Figure], go.Figure]) -> None:
    st.subheader("พอร์ต DAR")
    try:
        tx = dar_ledger.load_dar_transactions()
    except dar_ledger.DarLedgerError as exc:
        st.error(str(exc))
        return
    if tx.empty:
        st.info("พอร์ตนี้ยังว่าง — เริ่มจากศูนย์ตามที่ตั้งใจ บันทึกการซื้อครั้งแรกจากฟอร์มด้านบน")
        return
    pos = dar_ledger.positions(tx)
    years = max(1, int((pd.Timestamp.now() - tx["date"].min()).days / 365) + 1)
    try:
        prices = _cached_adj_prices(tuple(sorted(set(tx["ticker"]))), years)
        from utils.fx import get_usdthb  # noqa: PLC0415

        fx_now = get_usdthb().rate
        comp = dar_ledger.compare_with_equal_shadow(tx, prices, fx_now)
    except (PriceDataUnavailableError, FxRateUnavailable, ValueError) as exc:
        st.error(f"ประเมินมูลค่า/เทียบผลไม่ได้รอบนี้: {exc}")
        comp, prices, fx_now = None, None, None

    c1, c2, c3 = st.columns(3)
    c1.metric("เงินที่ลงไปแล้ว", f"{tx['amount_thb'].sum():,.0f} บาท")
    if comp and comp["rows_used"]:
        c2.metric("มูลค่า (รวมปันผล)", f"{comp['dar_value_thb']:,.0f} บาท")
        diff = comp["diff_thb"]
        c3.metric("เทียบพอร์ตเงาแบ่งเท่ากัน", f"{diff:+,.0f} บาท", f"{comp['diff_pct_of_invested']:+.2f}% ของเงินที่ลง")
        path = comp["path"]
        if not path.empty:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=path.index, y=path["dar_usd"], name="พอร์ต DAR", line=dict(width=2)))
            fig.add_trace(go.Scatter(x=path.index, y=path["shadow_usd"], name="เงาแบ่งเท่ากัน", line=dict(width=2)))
            fig.add_trace(go.Scatter(x=path.index, y=path["invested_usd"], name="เงินที่ลง", line=dict(width=1, color="#7D8590")))
            fig.update_layout(height=320, yaxis_title="มูลค่า (ดอลลาร์ รวมปันผล)", hovermode="x unified")
            st.plotly_chart(apply_theme(fig), use_container_width=True)
        st.caption(
            "เงาแบ่งเท่ากันใช้เงินก้อนเดียวกันวันเดียวกัน แบ่งเท่า ๆ กันให้ทุกกองของเดือนนั้น วัดทั้งสองขาด้วยราคารวมปันผล "
            "ชุดเดียวกัน ส่วนต่างจึงมาจากการแบ่งเงินล้วน ๆ · ความได้เปรียบที่คาดไว้เล็กมาก (~0.1–0.2%/ปี) "
            "ต้องใช้เวลาหลายปีกว่าจะแยกจากดวงได้ อย่าตัดสินจากไม่กี่เดือน"
        )
        for ex in comp["rows_excluded"]:
            st.warning(f"ไม่ได้นับ {ex['tx_id']} ({ex['date']}): {ex['reason']}")

    rows = []
    last = prices.ffill().iloc[-1] if prices is not None and fx_now else None
    for t, r in pos.iterrows():
        value = None
        if last is not None and t in last.index and pd.notna(last[t]):
            value = float(r["units"]) * float(last[t]) * fx_now
        rows.append(
            {
                "กอง": t,
                "หน่วยสะสม": f"{r['units']:,.4f}",
                "เงินที่ลง (บาท)": f"{r['invested_thb']:,.0f}",
                # มูลค่าที่คำนวณไม่ได้ต้องเป็น "—" ไม่ใช่ 0 (กติกา fail-loud ของโปรเจกต์)
                "มูลค่าตอนนี้ (บาท)": "—" if value is None else f"{value:,.0f}",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.caption("มูลค่าต่อกองใช้หน่วยที่ซื้อได้จริง × ราคาปิดล่าสุด × อัตราแลกเปลี่ยนปัจจุบัน (ไม่รวมปันผลที่ได้รับเป็นเงินสด)")

    with st.expander(f"รายการซื้อทั้งหมด ({len(tx)} รายการ)"):
        view = tx.copy()
        view["date"] = view["date"].dt.strftime("%Y-%m-%d")
        st.dataframe(view, hide_index=True, use_container_width=True)
        options = {f"{r.date:%Y-%m-%d} · {r.ticker} · {r.amount_thb:,.0f} บาท · {r.tx_id}": r.tx_id for r in tx.itertuples()}
        pick = st.selectbox("เลือกรายการที่จะลบ", list(options), key="dar_delete_pick")
        if st.button("ลบรายการนี้", key="dar_delete_btn"):
            if dar_ledger.delete_dar_transaction(options[pick]):
                st.success("ลบแล้ว")
                st.rerun()
            else:
                st.error("ไม่พบรายการนี้ในสมุดแล้ว")


def _tax_section(tickers: list[str]) -> None:
    st.subheader("ภาษีปันผล 15% — ข้อมูลประกอบ (ไม่เข้าสูตร)")
    rows = []
    for t in tickers:
        y = _cached_yield(t)
        rows.append(
            {
                "กอง": t,
                # _dividend_yield คืน **สัดส่วน** (0.033 = 3.3%) — หน่วยถูกวัดไว้ที่ฟังก์ชันนั้นจุดเดียว
                "อัตราปันผล": "ไม่ทราบ" if y is None else f"{y * 100:.2f}%",
                "ถูกหักทิ้งต่อปี (ประมาณ)": "ไม่ทราบ" if y is None else f"{y * DIVIDEND_WITHHOLDING * 100:.2f}%",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.caption(
        "ปันผลจากกองสหรัฐถูกหัก ณ ที่จ่าย 15% ตามสนธิสัญญาไทย–สหรัฐ เป็นต้นทุนที่แน่นอน "
        "แต่ทดสอบย้อนหลังในข้อมูลวิจัยไม่ได้ จึงแสดงไว้ให้เห็น ไม่ได้ใช้ขยับเงินในสูตร"
    )


def _method_section() -> None:
    with st.expander("สูตรนี้คิดอย่างไร และหลักฐานที่มี"):
        st.markdown(
            f"""
**ทุกเดือน** เทียบผลงาน 5 ปีล่าสุดของแต่ละกองกับจังหวะการเดินของกองนั้นเองใน 10 ปีก่อนหน้า
กองที่ตามหลังผิดปกติได้เงินเพิ่ม กองที่นำผิดปกติได้น้อยลง

```
AMP = ln(ค่าเฉลี่ยราคา 54–66 เดือนก่อน) − ln(ราคาล่าสุด)
OLD = ln(ราคา 60 เดือนก่อน) − ln(ราคา 180 เดือนก่อน)
DAR = AMP + 0.5 × OLD
z   = (DAR − ค่าเฉลี่ย) / max(ส่วนเบี่ยงเบน, 0.15)
w   = max(1 + z, 0) / N  → ขั้นต่ำ 0.2/N · เพดาน 1.5/N
```

- เงิน DCA ที่ห้ามขายถูกถือหลายปี สัญญาณระยะสั้น (RSI, MA, momentum) หมดฤทธิ์ภายในไม่กี่เดือน
  ข้อมูลสหรัฐ 1926–2026: เอียงตาม momentum ทำให้เงินปลายทาง 20 ปีแพ้การแบ่งเท่ากันใน 67–77% ของกรณี
- ยืนยันรอบ 2 บนข้อมูลที่ไม่เคยเปิดดู (ดัชนี 20 ประเทศ, สินค้าโภคภัณฑ์ 9 ชนิด 1990–2025):
  DCA 20 ปีได้มากกว่าแบ่งเท่ากัน +1.24% (ชนะ 75%) · ส่วนเกิน +0.11%/ปี CI95 [−0.10, +0.32]
- **ความได้เปรียบเล็กและยังแยกจากศูนย์ทางสถิติไม่ได้** · บน ETF 5 กองช่วง 2011–2026 ตามหลังแบ่งเท่ากัน −0.21%/ปี (แยกจากดวงไม่ได้)
- ค่าคงที่ทุกตัวถูกล็อกก่อนทดสอบ ห้ามจูนจากผลของพอร์ตนี้

รายงานพร้อมกราฟ: {REPORT_URL} · สคริปต์วิจัยและ hash ของการล็อก: `research/dar_dca/`
"""
        )


def render_dar_page(apply_theme: Callable[[go.Figure], go.Figure] = lambda f: f) -> None:
    st.header("DAR-DCA · พอร์ตทดลอง")
    st.info(
        "สูตรแบ่งเงิน DCA ใหม่ที่ผ่านการยืนยันรอบ 2 (ข้อมูลที่ไม่เคยเปิดดู) · พอร์ตแยกเริ่มจากศูนย์ "
        "ไม่เกี่ยวกับแผน ERC หลัก · ความได้เปรียบที่คาดไว้เล็ก (~0.1–0.2%/ปี) และยังแยกจากดวงไม่ได้"
    )
    tickers, budget = list(dar_dca.TICKERS), dar_dca.MONTHLY_BUDGET_THB
    _settings_caption(tickers, budget)
    month = dar_dca.plan_month_of(datetime.now(BANGKOK_TZ))
    plan = _plan_section(tickers, budget, month)
    if plan is not None:
        _record_section(plan)
    st.divider()
    _portfolio_section(apply_theme)
    st.divider()
    _tax_section(tickers)
    _method_section()
