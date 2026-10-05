# -*- coding: utf-8 -*-
"""หน้า TRADE — ทำนายหุ้นรายตัว 30 ตัวทุกวัน + บัญชีกระดาษซื้อขายตามคำทำนาย (ทดลอง) แยกจากโหมด PREDICT (5 กอง) และแผน DCA ทุกแผน.

หน้านี้ **อ่านอย่างเดียว**: คำทำนายถูกบันทึกโดยงานรายวัน 06:35 (``jobs/trade_daily.py``) ไม่มีปุ่มบันทึก/แก้/ลบ · ตัวเลขมาจาก ``analysis/trade_lab.py`` ·
ป้าย "ซื้อ/ขาย/ถือ" คือสิ่งที่ **บัญชีกระดาษ** จะทำที่เปิดตลาดพรุ่งนี้ ไม่ใช่คำแนะนำลงทุน · สถานะหลักฐานอยู่บนสุดเสมอ (ยังไม่มีหลักฐาน)
"""
from __future__ import annotations

from typing import Callable

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analysis import trade_lab as tl
from jobs import trade_daily
from portfolio import trade_ledger
from simulation import trade_sim

_LABEL = {"momentum_12_1": "โมเมนตัม 12-1", "mean_reversion": "กลับเข้าหาค่าเฉลี่ย", "scorecard": "Scorecard", "prophet": "Prophet",
          "reversal_5d": "กลับตัว 5 วัน", "majority": "เสียงข้างมาก (ป้ายรวม)"}
_HLABEL = {"1d": "พรุ่งนี้", "1w": "1 สัปดาห์", "1m": "1 เดือน"}
_ACTION = {"BUY": "ซื้อ", "SELL": "ขาย", "HOLD": "ถือต่อ", "STAY_CASH": "รอ (เงินสด)"}


def _evidence(state: dict | None) -> None:
    st.subheader("สถานะหลักฐาน")
    lock = tl.lock_status()
    if not lock["ok"]:
        st.error(lock["reason"])
    n_acc = sum(1 for a in (state or {}).get("accounts", {}).values() if (a.get("verdict") or {}).get("status") == "ผ่าน")
    n_grp = (state or {}).get("n_pass", 0)
    st.error(f"**ยังไม่มีหลักฐานว่าซื้อขายตามคำทำนายทำกำไรได้** — บัญชีที่ผ่านเกณฑ์: {n_acc} จาก {tl.N_ACCOUNTS} · ชุดความแม่นที่ผ่าน: {n_grp} จาก {tl.N_TESTS} "
             "(ส่วนใหญ่จะเป็น \"ยังตอบไม่ได้\" จนกว่าจะครบ 252 วัน / 52 สัปดาห์)")
    with st.expander("ข้อควรระวังที่ต้องอ่านคู่กับทุกตัวเลขในหน้านี้", expanded=True):
        for c in tl.EVIDENCE_CAVEATS:
            st.markdown(f"- {c}")
        st.markdown("กติกาที่ล็อก: `research/trade_lab/PREREG.md` (+ `LOCK.sha256`)")


def _today(state: dict | None) -> None:
    st.subheader("วันนี้ควรทำอะไร — ที่เปิดตลาดสหรัฐพรุ่งนี้ (ตามบัญชีกระดาษ)")
    if not state or not state.get("accounts"):
        st.info("ยังไม่มีสถานะ — รอตัวตั้งเวลา (06:35) หรือรัน `python main.py --job trade_daily`")
        return
    if state.get("ok") is False:
        st.error(f"รอบล่าสุดล้มเหลว: {state.get('error')} — แสดงผลจากรอบก่อนหน้า · ชุดของวันที่ล้มไม่ถูกเติมภายหลัง (กติกาที่ล็อก)")
    names = [n for n in tl.PREDICTORS if n in state["accounts"]]
    pick = st.selectbox("บัญชีตามตัวทำนาย", names, index=names.index("majority") if "majority" in names else 0, format_func=lambda n: _LABEL[n], key="trade_acct_pick")
    pend = state["accounts"][pick]["pending"]
    if not pend:
        st.info("บัญชีนี้ยังไม่มีสัญญาณ")
        return
    acts = pd.DataFrame(pend)
    todo = acts[acts["action"].isin(["BUY", "SELL"])]
    st.success(f"ข้อมูลถึงแท่ง {state.get('last_bar')} · ซื้อ {int((acts['action'] == 'BUY').sum())} ตัว · ขาย {int((acts['action'] == 'SELL').sum())} ตัว · "
               f"ถือต่อ {int((acts['action'] == 'HOLD').sum())} · รอ (เงินสด) {int((acts['action'] == 'STAY_CASH').sum())} — ซื้อขายเฉพาะเมื่อสัญญาณพลิก")
    if todo.empty:
        st.info("ไม่มีรายการซื้อ/ขายพรุ่งนี้ — ทุกช่องคงสถานะเดิม")
    else:
        view = todo.assign(action=todo["action"].map(_ACTION), name=todo["ticker"].map(tl.NAME))
        st.dataframe(view[["ticker", "name", "action", "signal"]].rename(columns={"ticker": "หุ้น", "name": "บริษัท", "action": "ทำที่เปิดตลาด", "signal": "สัญญาณวันนี้"}),
                     hide_index=True, use_container_width=True)
    with st.expander("ทุกช่องของบัญชีนี้"):
        v = acts.assign(action=acts["action"].map(_ACTION), name=acts["ticker"].map(tl.NAME))
        st.dataframe(v[["ticker", "name", "action", "signal"]], hide_index=True, use_container_width=True)
    st.caption("ราคาซื้อขายสมมติ = ราคาเปิดของแท่งถัดไป · ค่าธรรมเนียม Dime 0.15% ต่อรายการ · แต่ละบัญชี = 30 ช่อง ช่องละ 1,000 ดอลลาร์ (กระดาษ) long-only ไม่ชอร์ต · "
               "ป้ายนี้ไม่ใช่คำแนะนำลงทุน — ระบบยังไม่มีหลักฐานว่าทำกำไรได้")


def _tomorrow(state: dict | None) -> None:
    st.subheader("ทำนายพรุ่งนี้ (ทิศของแต่ละตัวทำนาย)")
    rows = (state or {}).get("tomorrow") or []
    if not rows:
        st.info("ยังไม่มีคำทำนาย")
        return
    df = pd.DataFrame(rows).set_index("ticker")
    cols = [n for n in tl.PREDICTORS if n in df.columns]
    show = df[cols].map(lambda v: "—" if pd.isna(v) else ("↑ ขึ้น" if v > 0 else "↓ ลง"))
    show.columns = [_LABEL[c] for c in cols]
    show.insert(0, "ราคาล่าสุด ($)", df["price_usd"].map(lambda p: f"{p:,.2f}"))
    st.dataframe(show, use_container_width=True)
    up = int((df["majority"] > 0).sum()) if "majority" in df.columns else 0
    st.caption(f"แท่งราคา {state.get('last_bar')} · เสียงข้างมากทายขึ้น {up} จาก {len(df)} ตัว · คำทำนายพรุ่งนี้ = ปิดพรุ่งนี้ เทียบปิดวันนี้ · "
               "ช่วง 1 สัปดาห์ (5 แท่ง) และ 1 เดือน (21 แท่ง) ถูกบันทึกไว้ในสมุดด้วย ให้คะแนนเมื่อครบกำหนด")


def _accounts(state: dict | None) -> None:
    st.subheader("บัญชีกระดาษ เทียบถือเฉย ๆ (หลังหักค่าธรรมเนียม)")
    if not state or not state.get("accounts"):
        st.info("ยังไม่มีบัญชี")
        return
    rows = []
    for n in tl.PREDICTORS:
        a = state["accounts"].get(n)
        if not a:
            continue
        v = a.get("verdict") or {}
        rows.append({"ตัวทำนาย": _LABEL[n], "สถานะ": v.get("status", "—"),
                     "ผลตอบแทน": "—" if v.get("return_pct") is None else f"{v['return_pct']:+.2f}%",
                     "แบ่งเท่ากัน 30 ช่อง": "—" if v.get("ew30_return_pct") is None else f"{v['ew30_return_pct']:+.2f}%",
                     "VOO": "—" if v.get("voo_return_pct") is None else f"{v['voo_return_pct']:+.2f}%",
                     "สัปดาห์": f"{v.get('weeks', 0)}/{tl.MIN_WEEKS}", "รายการซื้อขาย": a["n_trades"], "ค่าธรรมเนียมที่เสีย ($)": f"{a['fees_paid_usd']:,.2f}",
                     "ถืออยู่": len(a["positions"])})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    for n in tl.PREDICTORS:
        v = ((state["accounts"].get(n) or {}).get("verdict") or {})
        if v.get("status") == "ปฏิเสธ":
            st.warning(f"{_LABEL[n]}: {v['reasons'][0]}")
    st.caption("ผลตอบแทนวัดจากเงินตั้งต้น 30,000 ดอลลาร์ (กระดาษ) ถึงแท่งล่าสุด · ก่อนครบ 52 สัปดาห์ตอบได้อย่างเดียวว่า \"ยังตอบไม่ได้\" ตัวเลขข้างบนแสดงได้แต่ห้ามอ่านว่าชนะหรือแพ้ · "
               "ไม่มีภาษีปันผล/FX/slippage ผลจริงจะแย่กว่านี้")


def _accuracy(state: dict | None) -> None:
    st.subheader("ความแม่นเทียบ \"ขึ้นเสมอ\" (เฉพาะวันที่ไม่ซ้อนทับ)")
    if not state:
        return
    for k, why in (state.get("skipped") or {}).items():
        st.warning(f"ไม่ได้ทำนาย {k}: {why}")
    rows = []
    for g in state["groups"].values():
        rows.append({"ตัวทำนาย": _LABEL[g["predictor"]], "ช่วง": _HLABEL[g["horizon"]], "สถานะ": g["status"],
                     "วันที่ไม่ซ้อนทับ": f"{g['n_independent']}/{tl.N_MIN[g['horizon']]}", "รอครบกำหนด": g["pending"],
                     "ความแม่นเฉลี่ย": "—" if g["mean_accuracy"] is None else f"{g['mean_accuracy']:.1f}%",
                     "เส้นฐาน (ขึ้นเสมอ)": "—" if g["mean_baseline"] is None else f"{g['mean_baseline']:.1f}%",
                     "ส่วนเกิน (จุด)": "—" if g["mean_excess_acc_pp"] is None else f"{g['mean_excess_acc_pp']:+.2f}"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    desc = pd.DataFrame(state.get("descriptive") or [])
    if not desc.empty:
        with st.expander("พรรณนา: ความแม่นรวมทุกวันที่ครบกำหนดแล้ว (ซ้อนทับกัน — ไม่ใช้ตัดสิน)"):
            desc = desc.assign(predictor=desc["predictor"].map(_LABEL), horizon=desc["horizon"].map(_HLABEL))
            st.dataframe(desc.round(2), hide_index=True, use_container_width=True)
    st.caption(f"อัปเดตล่าสุด {state.get('updated_at')} · แท่งราคาล่าสุด {state.get('last_bar')} · บันทึกแล้ว {state.get('days_recorded', 0)} วัน")


def _sim() -> None:
    st.subheader("simulation: เกณฑ์ของเราจับฝีมือได้ไหม และค่าธรรมเนียมกินเท่าไร")
    sim = trade_sim.load()
    if sim is None:
        st.info("ยังไม่มีผลจำลอง — รอตัวตั้งเวลา (06:35) หรือรัน `python main.py --job trade_daily` · ไม่มีผลไม่ได้แปลว่าเกณฑ์ผ่านการจำลองแล้ว")
        return
    age = trade_sim.age_days(sim)
    if age is not None and age > 2 * trade_sim.MAX_AGE_DAYS:
        st.warning(f"ผลจำลองเก่า {age:.0f} วัน")
    rows = []
    for r in sim["results"].values():
        rows.append({"โลก": "ไม่มีใครทายได้ (rw)" if r["world"] == "rw" else f"สมมติเอดจ์โมเมนตัม +{sim['mom_edge_pct_month']}%/เดือน (mom)",
                     "ตัวทำนาย": _LABEL[r["predictor"]], "ผ่านเกณฑ์ (252 วัน, พรุ่งนี้)": f"{r['pass_rate_pct']:.2f}%",
                     "ส่วนเกินเฉลี่ย": f"{r['mean_excess_acc_pp']:+.2f} จุด", "ส่วนเกินขั้นต่ำที่จับได้ 80%": f"{r['mde80_excess_acc_pp']:.1f} จุด",
                     "รายการซื้อขาย/ช่อง/ปี": f"{r['trades_per_slot_year']:.1f}", "ค่าธรรมเนียม/ปี": f"{r['fee_drag_pct_year']:.2f}%"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.warning("**อ่านคู่กัน:** ในโลกที่ไม่มีใครทายได้ ผู้ไร้ฝีมือได้ส่วนเกินติดลบ (ตลาดขึ้นเกินครึ่ง จึงแพ้ \"ขึ้นเสมอ\") เกณฑ์จับได้เฉพาะเอดจ์ที่ใหญ่กว่า \"ส่วนเกินขั้นต่ำ\" ในตาราง "
               "**\"ไม่ผ่าน\" ≠ \"ไม่มีฝีมือ\"** · และ **ค่าธรรมเนียมคือกำแพงที่แยกต่างหาก**: ตัวทำนายที่พลิกสัญญาณบ่อย (เช่น กลับตัว 5 วัน) เสียค่าธรรมเนียมหลายเปอร์เซ็นต์ต่อปีก่อนจะมีกำไร · "
               f"อัตราผ่านลวงในโลกไร้ฝีมือ {sim['null_pass_rate_pct']:.2f}% ({'ในเกณฑ์' if sim['null_pass_rate_ok'] else 'สูงเกินที่ล็อกไว้ — ต้องทำ pre-registration ฉบับใหม่'})")
    for lim in sim["limitations"]:
        st.caption("• " + lim)
    st.caption(f"จำลอง {sim['paths']:,} เส้นทาง/โลก · ราคาถึง {sim['data']['last_bar']} · สร้างเมื่อ {sim['created_at']}")


def _method() -> None:
    with st.expander("โหมดนี้คิดอย่างไร ทำไมไม่มี backtest"):
        st.markdown(
            f"""
**หุ้น {len(tl.TICKERS)} ตัว** (ตายตัวในโค้ด): {', '.join(tl.TICKERS)} · **ตัวทำนาย 6 ตัว** (ไม่มี LLM): โมเมนตัม 12-1 · กลับเข้าหาค่าเฉลี่ย · Scorecard · Prophet · กลับตัว 5 วัน · เสียงข้างมากของห้าตัวแรก (ป้ายรวม)
**ทุกวัน 06:35** ระบบบันทึกคำทำนาย 3 ช่วง (พรุ่งนี้ / 5 แท่ง / 21 แท่ง) ให้คะแนนชุดที่ครบกำหนด เล่นซ้ำบัญชีกระดาษจากสมุดที่แก้ไม่ได้ และจำลองเกณฑ์ — ไม่มีคนกดเลือก ไม่มีปุ่มแก้/ลบ ไม่ย้อนบันทึก
**บัญชีกระดาษ (ต่อตัวทำนาย):** 30 ช่อง ช่องละ $1,000 · สัญญาณขึ้น = ถือ ลง = เงินสด · ซื้อขายเฉพาะเมื่อสัญญาณพลิก ที่ราคาเปิดวันถัดไป · ค่าธรรมเนียม 0.15%/รายการ · เทียบแบ่งเท่ากัน 30 ช่องและ VOO

**ทำไมไม่ backtest:** ข้อมูลฟรีไม่มีหุ้นที่ล้ม/ถูกซื้อ และ 30 ตัวนี้เป็นผู้รอด ⇒ ผลย้อนหลังดูดีเกินจริง · หลักฐานที่ซื่อสัตย์มีทางเดียวคือคำทำนายที่บันทึกไว้ก่อนผลเกิด
**เกณฑ์ที่ล็อก:** ความแม่น: ครบ {tl.N_MIN['1d']}/{tl.N_MIN['1w']}/{tl.N_MIN['1m']} วันที่ไม่ซ้อนทับ และชนะเส้นฐาน ≥ {tl.MIN_EXCESS_ACC_PP:.0f} จุดที่ p < {tl.ALPHA:.4f} · บัญชี: ครบ {tl.MIN_WEEKS} สัปดาห์ และชนะทั้งแบ่งเท่ากัน 30 ช่องกับ VOO หลังหักค่าธรรมเนียม ที่ p < {tl.ALPHA_ACCOUNT:.4f} ·
ผู้พัฒนาทำนายล่วงหน้าว่า ~85% ไม่มีบัญชีใดผ่าน · **ผลของโหมดนี้ไม่ใช้ร่วมกับโหมด PREDICT และห้ามไหลกลับไปปรับสูตร DCA**
"""
        )


def render_trade_page(apply_theme: Callable[[go.Figure], go.Figure] = lambda f: f) -> None:
    st.header("TRADE · ทำนายหุ้นรายตัวรายวัน + บัญชีกระดาษ (ทดลอง)")
    st.info("แยกจากโหมด PREDICT (5 กอง) และแผน DCA ทุกแผน · บันทึกคำทำนายอัตโนมัติทุกวัน · **ยังไม่มีหลักฐาน** ไม่ใช่คำแนะนำลงทุน")
    state = trade_daily.load_state()
    _evidence(state)
    st.divider()
    _today(state)
    st.divider()
    _tomorrow(state)
    st.divider()
    _accounts(state)
    st.divider()
    _accuracy(state)
    st.divider()
    _sim()
    _method()
