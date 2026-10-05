# -*- coding: utf-8 -*-
"""กล่อง/หน้า simulation ของ dashboard — แสดงผลที่ scheduler รันไว้ + ปุ่มรันใหม่ + ข้อจำกัดของโมเดลเสมอ.

ตัวเลขทุกตัวมาจาก ``simulation/service.py`` (ไม่มี LLM) — ไฟล์นี้แค่แสดง · import ตอนใช้ใน ``app.py`` เพื่อให้ส่วนนี้พัง
แล้วหน้าหลักไม่ล่ม (รูปแบบเดียวกับ ``dar_page.py``)
"""
from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from simulation import data as sim_data
from simulation import service

_HORIZON_LABEL = {"60": "5 ปี", "120": "10 ปี", "240": "20 ปี"}


def _fmt_ci(v: dict[str, Any]) -> str:
    lo, hi = v["d_irr_ci95"]
    noise = " (แยกจากสัญญาณรบกวนไม่ได้)" if abs(v["d_irr_mean"]) < service.NOISE_FLOOR_PP and lo < 0 < hi else ""
    return f"{v['d_irr_mean']:+.2f} pp [{lo:+.2f}, {hi:+.2f}]{noise}"


def _world_table(world_block: dict[str, Any], reference: str) -> dict[str, pd.DataFrame]:
    """ตารางต่อขอบฟ้า: แถว = กลยุทธ์."""
    out: dict[str, pd.DataFrame] = {}
    for h, label in _HORIZON_LABEL.items():
        block = world_block["horizons"].get(h)
        if not block:
            continue
        rows = []
        for name, s in block["strategies"].items():
            vs = block["vs_reference"].get(name)
            rows.append({
                "กลยุทธ์": name,
                "ผลตอบแทน/ปี (แย่สุด 5% · มัธยฐาน · ดีสุด 5%)": f"{s['irr_p5']:.1f}% · {s['irr_p50']:.1f}% · {s['irr_p95']:.1f}%",
                "ขาดทุนสูงสุด (มัธยฐาน · แย่สุด 5%)": f"{s['dd_med']:.0f}% · {s['dd_p95']:.0f}%",
                "ขาดทุนเป็นบาท": f"{s['p_loss_nominal'] * 100:.0f}%",
                "ขาดทุนหลังเงินเฟ้อ": f"{s['p_loss_real'] * 100:.0f}%",
                f"เทียบ {reference} (ผลตอบแทน)": "— (ตัวเทียบ)" if name == reference else (_fmt_ci(vs) if vs else "—"),
            })
        out[label] = pd.DataFrame(rows)
    return out


def _render_result(result: dict[str, Any]) -> None:
    plan = result["plan"]
    data = result["data"]
    age = service.last_plan_age_days(result)
    st.caption(
        f"แผนที่จำลอง: **{plan['plan_strategy']}** · วิธีใน Settings = `{plan['method']}` · DCA {plan['budget_thb']:,.0f} บาท/เดือน ไม่หยุด · "
        f"กอง {', '.join(plan['tickers'])} · {result['paths_per_world']:,} เส้นทางต่อโลก · ข้อมูลถึง {data['as_of']} · "
        f"รันเมื่อ {result['created_at'][:16].replace('T', ' ')}" + (f" ({age:.0f} วันก่อน)" if age is not None and age >= 1 else "")
    )
    if age is not None and age > 14:
        st.warning(f"ผลนี้เก่า {age:.0f} วัน — กด \"รันใหม่\" หรือรอตัวตั้งเวลา (06:00)")
    for note in result.get("notes", []):
        st.warning(note)
    worlds = result["worlds"]
    tabs = st.tabs([f"{w} — {worlds[w]['label']}" for w in worlds])
    for tab, w in zip(tabs, worlds):
        with tab:
            for label, df in _world_table(worlds[w], result["reference"]).items():
                st.markdown(f"**{label}**")
                st.dataframe(df, use_container_width=True, hide_index=True)
    with st.expander("ข้อจำกัดของโมเดล (อ่านก่อนใช้ตัวเลข)", expanded=False):
        for lim in result.get("limitations", service.LIMITATIONS):
            st.markdown(f"- {lim}")
        st.markdown(
            "- ผลต่างระหว่างสูตรที่เล็กกว่า ~0.15 pp/ปี แยกจากสัญญาณรบกวนไม่ได้ที่จำนวนเส้นทางระดับนี้ "
            "(ชุดวิจัยใช้ 300,000 เส้นทาง: `research/dar_sim/REPORT_NEW.md`)"
        )


def render_simulation_panel(*, preview: dict[str, Any] | None = None, key: str = "sim") -> None:
    """แสดงผล simulation ของแผนปัจจุบัน.

    ``preview`` = วิธีที่เลือกอยู่แต่ยังไม่บันทึก (``{"label": ..., "spec": "ERC"|"BLEND"|{ticker: w}}``) →
    มีปุ่มลองรันเทียบก่อนบันทึก (ผลไม่ถูกเก็บเป็นผลล่าสุด)
    """
    status = sim_data.data_status()
    if not status.get("exists"):
        st.info(f"ยังไม่มีข้อมูล simulation — {status.get('reason', '')} · ตัวตั้งเวลาจะดึงให้ตอน 06:00 หรือกดปุ่มด้านล่าง")
    elif status.get("stale"):
        st.warning(f"ข้อมูล simulation เก่า: {status.get('reason')} (ดึงเมื่อ {status.get('fetched_at', '')[:16].replace('T', ' ')})")
    else:
        st.caption(f"ข้อมูลสด ดึงเมื่อ {status['fetched_at'][:16].replace('T', ' ')} ({status['age_days']:.1f} วันก่อน) · แท่งราคาล่าสุด {status['last_bar']}")
    for note in status.get("notes", []):
        st.warning(note)

    last = service.load_last_plan()
    if last is None:
        st.info("ยังไม่มีผลของแผนปัจจุบัน — ไม่ได้แปลว่าแผนผ่านการจำลองแล้ว")
    else:
        try:
            _render_result(last)
        except (KeyError, TypeError) as exc:
            st.error(f"อ่านผล simulation ไม่ได้ ({exc}) — กด \"รันใหม่\"")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("รัน simulation ของแผนปัจจุบันใหม่ (~15–30 วินาที)", key=f"{key}_run"):
            _run_now()
    with col2:
        if preview and st.button(f"ลองวิธีที่เลือก ({preview['label']}) ก่อนบันทึก", key=f"{key}_preview"):
            _run_preview(preview)


def _run_now() -> None:
    from data.fetcher import PriceDataUnavailableError
    from portfolio.targets import TargetWeightsError

    with st.spinner("กำลังรัน simulation ..."):
        try:
            result = service.simulate_current_plan(workers=2)
            result["inputs"] = None  # รันจากหน้าจอ: ให้ scheduler ตัดสินเองว่าต้องรันใหม่ตอน 06:00
            service.save_last_plan(result)
        except (sim_data.SimulationDataError, PriceDataUnavailableError, TargetWeightsError, ValueError) as exc:
            st.error(f"รัน simulation ไม่ได้: {exc}")
            return
    st.rerun()


def _run_preview(preview: dict[str, Any]) -> None:
    from utils.config import load_config

    with st.spinner("กำลังรัน simulation ของวิธีที่เลือก ..."):
        try:
            cfg = load_config()
            tickers = [str(t).strip().upper() for t in cfg["etf"]["tickers"]]
            panel = sim_data.load_panel(tickers)
            strategies: dict[str, Any] = {"ERC": "ERC", "BLEND": "BLEND", "1/N": "EQ"}
            strategies[preview["label"]] = preview["spec"]
            res = service.simulate_strategies(panel, strategies, reference="ERC", budget_thb=float(cfg["dca"]["monthly_budget_thb"]),
                                              worlds=("rw", "boot"), paths=service.DEFAULT_PATHS, workers=2)
        except (sim_data.SimulationDataError, ValueError) as exc:
            st.error(f"รัน simulation ไม่ได้: {exc}")
            return
    st.success(f"ผลของ \"{preview['label']}\" (ยังไม่ได้บันทึกวิธีนี้) — ข้อมูลถึง {panel['meta']['as_of']}")
    tabs = st.tabs([f"{w} — {res['worlds'][w]['label']}" for w in res["worlds"]])
    for tab, w in zip(tabs, res["worlds"]):
        with tab:
            for label, df in _world_table(res["worlds"][w], "ERC").items():
                st.markdown(f"**{label}**")
                st.dataframe(df, use_container_width=True, hide_index=True)


def render_simulation_page() -> None:
    """หน้า Simulation เต็ม: ผลของแผนปัจจุบัน + ลองสัดส่วนที่กำหนดเอง (what-if)."""
    st.header("Simulation — โลกจำลองอนาคต 5–20 ปี")
    st.caption(
        "ทุกแผนที่ระบบคำนวณจะถูกลองรันที่นี่ด้วย · วิกฤต สงคราม เงินเฟ้อ ค่าเงินบาท ดอกเบี้ย น้ำมัน · "
        "**ใช้เทียบสูตรกัน ไม่ใช่พยากรณ์** · ที่มาของหลักฐาน: `research/dar_sim/REPORT.md`, `REPORT_NEW.md`"
    )
    render_simulation_panel(key="simpage")
    st.divider()
    st.subheader("ลองสัดส่วนที่กำหนดเอง (what-if)")
    from utils.config import load_config

    cfg = load_config()
    tickers = [str(t).strip().upper() for t in cfg["etf"]["tickers"]]
    st.caption("ใส่น้ำหนักของแต่ละกอง (รวมเท่าไรก็ได้ ระบบปรับให้รวม 100%) แล้วเทียบกับ ERC / blend / 1/N")
    cols = st.columns(len(tickers))
    weights = {}
    for col, t in zip(cols, tickers):
        with col:
            weights[t] = st.number_input(t, min_value=0.0, max_value=100.0, value=round(100.0 / len(tickers), 1), step=1.0, key=f"wi_{t}")
    if st.button("ลองสัดส่วนนี้ใน simulation", key="wi_run"):
        if sum(weights.values()) <= 0:
            st.error("น้ำหนักรวมต้องมากกว่า 0")
        else:
            _run_preview({"label": "สัดส่วนที่กำหนด", "spec": {t: w for t, w in weights.items() if w > 0}})
