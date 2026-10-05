# -*- coding: utf-8 -*-
"""หน้า PREDICT — ทำนายตลาด (ทดลอง · พอร์ตกระดาษ) แยกจากแผน DCA ทุกแผน.

หน้านี้ **อ่านอย่างเดียว**: คำทำนายถูกบันทึกโดยงานรายวัน 06:30 (``jobs/predict_daily.py``) ไม่มีปุ่มบันทึก/แก้/ลบ (กติกาที่ล็อก) ·
ตัวเลขมาจาก ``analysis/predict_lab.py`` (+ ผลจำลองเกณฑ์ตัดสินจาก ``simulation/predictor_sim.py``) — import ตอนใช้เหมือนหน้า DAR/SELECT/STOCK
สถานะหลักฐานอยู่บนสุดเสมอ: **ไม่มีหลักฐานว่าตัวทำนายใดทายได้ดีกว่า "ขึ้นเสมอ"** จนกว่าจะครบ cohort ตามที่ล็อกไว้
"""
from __future__ import annotations

from typing import Callable

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analysis import predict_lab as pl
from jobs import predict_daily
from portfolio import predict_ledger
from simulation import predictor_sim

_LABEL = {"momentum_12_1": "โมเมนตัม 12-1", "mean_reversion": "กลับเข้าหาค่าเฉลี่ย", "scorecard": "คะแนน Scorecard", "prophet": "Prophet"}
_HLABEL = {"1m": "1 เดือน", "6m": "6 เดือน", "1y": "1 ปี"}


def _evidence(state: dict | None) -> None:
    st.subheader("สถานะหลักฐาน")
    lock = pl.lock_status()
    if not lock["ok"]:
        st.error(lock["reason"])
    n_pass = (state or {}).get("n_pass", 0)
    st.error(f"**ยังไม่มีหลักฐานว่าตัวทำนายใดทายได้ดีกว่า \"ขึ้นเสมอ\"** — ชุดที่ผ่านเกณฑ์: {n_pass} จาก {pl.N_TESTS} (ส่วนใหญ่จะเป็น \"ยังตอบไม่ได้\" ไปอีกหลายปี)")
    with st.expander("ข้อควรระวังที่ต้องอ่านคู่กับทุกตัวเลขในหน้านี้", expanded=True):
        for c in pl.EVIDENCE_CAVEATS:
            st.markdown(f"- {c}")
        st.markdown("กติกาที่ล็อก: `research/predict_lab/PREREG.md` (+ `LOCK.sha256`)")


def _sim_section() -> None:
    st.subheader("simulation: เกณฑ์ตัดสินของเราแยกโชคออกจากฝีมือได้ดีแค่ไหน")
    sim = predictor_sim.load()
    if sim is None:
        st.info("ยังไม่มีผลจำลอง — รอตัวตั้งเวลา (06:30) หรือรัน `python main.py --job predict_daily` · ไม่มีผลไม่ได้แปลว่าเกณฑ์ผ่านการจำลองแล้ว")
        return
    age = predictor_sim.age_days(sim)
    if age is not None and age > 2 * predictor_sim.MAX_AGE_DAYS:
        st.warning(f"ผลจำลองเก่า {age:.0f} วัน")
    rows = []
    for k, r in sim["results"].items():
        rows.append({"โลก": "ไม่มีใครทายได้ (rw)" if r["world"] == "rw" else f"สมมติเอดจ์โมเมนตัม +{sim['mom_edge_pct_month']}%/เดือน (mom)",
                     "ตัวทำนาย": _LABEL[r["predictor"]], "ผ่านเกณฑ์ (36 cohort, 1 เดือน)": f"{r['pass_rate_pct']:.2f}%",
                     "ส่วนเกินความแม่นเฉลี่ย": f"{r['mean_excess_acc_pp']:+.1f} จุด", "ช่วง 5–95%": f"{r['p05_excess_acc_pp']:+.1f} … {r['p95_excess_acc_pp']:+.1f}",
                     "ส่วนเกินขั้นต่ำที่จับได้ 80%": f"{r['mde80_excess_acc_pp']:.1f} จุด" if "mde80_excess_acc_pp" in r else "—"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.warning("**อ่านคู่กัน:** ในโลกที่ไม่มีใครทายได้ ผลเฉลี่ยติดลบ (ทายสุ่มแพ้ \"ขึ้นเสมอ\" เพราะตลาดขึ้นเกินครึ่ง) และความแปรปรวนของ 36 cohort สูงมาก ⇒ เกณฑ์จะจับได้ก็ต่อเมื่อฝีมือใหญ่มาก "
               "**\"ไม่ผ่าน\" จึงไม่ได้แปลว่า \"ไม่มีฝีมือ\"** — แปลว่าหลักฐานยังไม่พอจะบอกว่ามี · อัตราผ่านลวงในโลกไร้ฝีมือ: "
               f"{sim['null_pass_rate_pct']:.2f}% ({'ในเกณฑ์' if sim['null_pass_rate_ok'] else 'สูงเกินที่ล็อกไว้ — ต้องทำ pre-registration ฉบับใหม่'})")
    for lim in sim["limitations"]:
        st.caption("• " + lim)
    st.caption(f"จำลอง {sim['paths']:,} เส้นทาง/โลก · ราคาถึง {sim['data']['last_bar']} · สร้างเมื่อ {sim['created_at']}")


def _today_section() -> None:
    st.subheader("คำทำนายชุดล่าสุด")
    try:
        log = predict_ledger.load_log()
    except predict_ledger.PredictLedgerError as exc:
        st.error(str(exc))
        return
    if log.empty:
        st.info("ยังไม่มีคำทำนาย — งานรายวัน 06:30 จะบันทึกชุดแรก (หรือรัน `python main.py --job predict_daily`)")
        return
    last = log[log["date"] == log["date"].max()]
    piv = last.pivot_table(index="ticker", columns=["predictor", "horizon"], values="direction")
    piv.columns = [f"{_LABEL[p]} · {_HLABEL[h]}" for p, h in piv.columns]
    st.dataframe(piv.map(lambda v: "↑ ขึ้น" if v > 0 else "↓ ลง"), use_container_width=True)
    st.caption(f"ชุดของวันที่ {log['date'].max():%Y-%m-%d} · สะสม {log['date'].nunique()} วัน {len(log):,} คำทำนาย · "
               "บันทึกอัตโนมัติ ไม่มีปุ่มแก้/ลบ · ราคาปรับปันผล")


def _scores_section(state: dict | None, apply_theme: Callable[[go.Figure], go.Figure]) -> None:
    st.subheader("ผลเทียบกับของจริง (เฉพาะวันแรกของแต่ละเดือนที่ใช้ตัดสิน)")
    if not state:
        st.info("ยังไม่มีสถานะ — รอตัวตั้งเวลา (06:30) หรือรัน `python main.py --job predict_daily`")
        return
    if state.get("ok") is False:
        st.error(f"รอบล่าสุดล้มเหลว: {state.get('error')} — แสดงผลจากรอบก่อนหน้า · ชุดของวันที่ล้มไม่ถูกเติมภายหลัง (กติกาที่ล็อก)")
    for k, why in (state.get("skipped") or {}).items():
        st.warning(f"ไม่ได้ทำนาย {k}: {why}")
    rows = []
    for g in state["groups"].values():
        rows.append({"ตัวทำนาย": _LABEL[g["predictor"]], "ช่วง": _HLABEL[g["horizon"]], "สถานะ": g["status"],
                     "cohort ที่ไม่ซ้อนทับ": f"{g['n_independent']}/{pl.N_MIN[g['horizon']]}", "รอครบกำหนด": g["pending"],
                     "ความแม่นเฉลี่ย": "—" if g["mean_accuracy"] is None else f"{g['mean_accuracy']:.1f}%",
                     "เส้นฐาน (ขึ้นเสมอ)": "—" if g["mean_baseline"] is None else f"{g['mean_baseline']:.1f}%",
                     "ส่วนเกิน (จุด)": "—" if g["mean_excess_acc_pp"] is None else f"{g['mean_excess_acc_pp']:+.1f}",
                     "ส่วนเกินตะกร้า (%)": "—" if g["mean_excess_basket_pct"] is None else f"{g['mean_excess_basket_pct']:+.2f}"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.caption("ช่วง 6 เดือนต้องใช้เวลา ≈ 6 ปี และ 1 ปี ≈ 12 ปีกว่าจะตัดสินได้ · ก่อนครบ ตอบได้อย่างเดียวว่า \"ยังตอบไม่ได้\" ตัวเลขข้างบนแสดงได้แต่ห้ามอ่านว่าชนะหรือแพ้")
    desc = pd.DataFrame(state.get("descriptive") or [])
    if not desc.empty:
        with st.expander("พรรณนา: ความแม่นรวมทุกวันที่ครบกำหนดแล้ว (ซ้อนทับกัน — ไม่ใช้ตัดสิน)"):
            desc = desc.assign(predictor=desc["predictor"].map(_LABEL), horizon=desc["horizon"].map(_HLABEL))
            st.dataframe(desc.round(1), hide_index=True, use_container_width=True)
    st.caption(f"อัปเดตล่าสุด {state.get('updated_at')} · แท่งราคาล่าสุด {state.get('last_bar')} · บันทึกแล้ว {state.get('days_recorded', 0)} วัน")


def _method_section() -> None:
    with st.expander("โหมดนี้คิดอย่างไร ทำไมไม่มี backtest"):
        st.markdown(
            f"""
**คำถามเดียว:** ตัวทำนายไหนทายทิศทาง (ขึ้น/ลง) ของ {len(pl.TICKERS)} กอง ({', '.join(pl.TICKERS)}) ได้ **ดีกว่าทายว่า \"ขึ้นเสมอ\"** — ใน 1 เดือน / 6 เดือน / 1 ปีข้างหน้า
**ตัวทำนาย 4 ตัว (คำนวณในโค้ด ไม่มี LLM):** โมเมนตัม 12-1 · กลับเข้าหาค่าเฉลี่ย (ราคาเทียบค่าเฉลี่ย 3 ปี) · คะแนน Scorecard เดิม · Prophet
**ทุกวัน 06:30** ระบบบันทึกคำทำนายหนึ่งชุด ให้คะแนนชุดที่ครบกำหนดจากราคาจริง และจำลองเกณฑ์ตัดสิน — ไม่มีคนกดเลือก ไม่มีปุ่มแก้/ลบ ไม่ย้อนบันทึก
**ตะกร้ากระดาษ:** ซื้อเท่า ๆ กันเฉพาะกองที่ทายว่าขึ้น (ไม่มี = เงินสด 0%) เทียบแบ่งเท่ากันทั้ง 5 กอง

**ทำไมไม่ backtest:** ตัวทำนายจากราคาพอดีกับอดีตได้เสมอ และ 5 กองนี้รอดมาถึงวันนี้ ⇒ ผลย้อนหลังดูดีเกินจริง · หลักฐานที่ซื่อสัตย์มีทางเดียวคือคำทำนายที่บันทึกไว้ก่อนผลเกิด
**เกณฑ์ที่ล็อก:** ครบ cohort ที่ไม่ซ้อนทับ ({pl.N_MIN['1m']}/{pl.N_MIN['6m']}/{pl.N_MIN['1y']} สำหรับ 1 เดือน/6 เดือน/1 ปี) และชนะเส้นฐาน ≥ {pl.MIN_EXCESS_ACC_PP:.0f} จุดที่ p < {pl.ALPHA:.4f} และตะกร้าชนะ ·
ผู้พัฒนาทำนายล่วงหน้าว่า ~90% ไม่มีชุดไหนผ่าน · **ผลของโหมดนี้ห้ามไหลกลับไปปรับสูตร DCA**
"""
        )


def render_predict_page(apply_theme: Callable[[go.Figure], go.Figure] = lambda f: f) -> None:
    st.header("PREDICT · ทำนายตลาด (ทดลอง · พอร์ตกระดาษ)")
    st.info("แยกจากแผน DCA ทุกแผน · บันทึกคำทำนายอัตโนมัติทุกวัน · **ยังไม่มีหลักฐาน** ไม่ใช่คำแนะนำลงทุน")
    state = predict_daily.load_state()
    _evidence(state)
    st.divider()
    _today_section()
    st.divider()
    _scores_section(state, apply_theme)
    st.divider()
    _sim_section()
    _method_section()
