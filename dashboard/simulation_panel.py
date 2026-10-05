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
    _render_calibration()


def _render_calibration() -> None:
    """โมเดลมี "หน้าตา" เหมือนประวัติจริงไหม — โมเดลหลวมกว่าอดีตต้องเห็นชัด (ความเสี่ยงที่แสดงอาจต่ำเกินจริง)."""
    cal = service.load_calibration()
    with st.expander("ความแม่นยำของโมเดล: เทียบกับประวัติจริงของกองชุดเดียวกัน", expanded=bool(cal and cal.get("flags_loose"))):
        if not cal:
            st.info("ยังไม่มีผลตรวจ — ตัวตั้งเวลา (06:00) จะตรวจให้หลังดึงข้อมูล")
            return
        if cal.get("flags_loose"):
            st.warning("โมเดลหลวมกว่าอดีตใน: " + ", ".join(cal["flags_loose"]) + " — ตัวเลขความเสี่ยงอาจต่ำเกินจริง")
        rows = []
        for c in cal["checks"]:
            rows.append({"ข้อ": c["name"], "ประวัติจริง": "—" if c.get("history") is None else c["history"],
                         "โมเดล": "—" if c.get("model") is None else c["model"],
                         "เกณฑ์": c.get("rule", ""), "ผล": c["status"],
                         "ประวัติอยู่เปอร์เซนไทล์ของโมเดล": "—" if c.get("percentile_of_history") is None else c["percentile_of_history"]})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption(cal.get("caveat", "") + " · เกณฑ์ประกาศไว้ก่อนดูผล (`simulation/validate.py`) · โหดกว่าอดีตเป็นเรื่องตั้งใจ (เหตุการณ์ใหญ่ซ้อนทับ) หลวมกว่าอดีตคือสิ่งที่ต้องระวัง")


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


def _show_universe_result(res: dict[str, Any]) -> None:
    u = res["universe"]
    st.success(f"ผลของพอร์ตจำลอง ({', '.join(u['tickers'])}) — ข้อมูลถึง {u['as_of']} · ช่วงสอบเทียบ {u['pool_range'][0][:7]} → {u['pool_range'][1][:7]} ({u['pool_months']} เดือน)")
    for note in res.get("notes", []):
        st.warning(note)
    tabs = st.tabs([f"{w} — {res['worlds'][w]['label']}" for w in res["worlds"]])
    for tab, w in zip(tabs, res["worlds"]):
        with tab:
            for label, df in _world_table(res["worlds"][w], res["reference"]).items():
                st.markdown(f"**{label}**")
                st.dataframe(df, use_container_width=True, hide_index=True)
    with st.expander("ข้อจำกัดของโมเดล (อ่านก่อนใช้ตัวเลข)", expanded=False):
        for lim in res.get("limitations", service.LIMITATIONS):
            st.markdown(f"- {lim}")


def render_simulation_page() -> None:
    """หน้า Simulation เต็ม: ผลของแผนปัจจุบัน + ลองพอร์ตที่เลือกกองและสัดส่วนเอง (รวมสินทรัพย์เพิ่ม)."""
    st.header("Simulation — โลกจำลองอนาคต 5–20 ปี")
    st.caption(
        "ทุกแผนที่ระบบคำนวณจะถูกลองรันที่นี่ด้วย · วิกฤต สงคราม เงินเฟ้อ ค่าเงินบาท ดอกเบี้ย น้ำมัน · "
        "**ใช้เทียบสูตรกัน ไม่ใช่พยากรณ์** · ที่มาของหลักฐาน: `research/dar_sim/REPORT.md`, `REPORT_NEW.md`"
    )
    render_simulation_panel(key="simpage")
    st.divider()
    st.subheader("ลองพอร์ตที่เลือกกองและสัดส่วนเอง (what-if)")
    from simulation.universe import asset_for, is_guessed
    from utils.config import load_config

    cfg = load_config()
    status = sim_data.data_status()
    available = list(status.get("funds_available", [])) if status.get("exists") else []
    if not available:
        st.info("ยังไม่มีข้อมูล simulation — รอตัวตั้งเวลา (06:00) หรือรัน `python main.py --job sim_refresh`")
        return
    st.caption(
        "เลือกกองได้เองจากที่ดึงข้อมูลไว้ (รวมสินทรัพย์เพิ่ม เช่น หุ้นนอกสหรัฐ พันธบัตร REIT) แล้วเทียบกับ ERC / blend / 1/N ของกองชุดเดียวกัน · "
        "**ยังไม่ได้ตรวจว่ากองเสริมซื้อได้บน Dime** — ตรวจเองก่อนซื้อจริง"
    )
    default = [str(t).strip().upper() for t in cfg["etf"]["tickers"] if str(t).strip().upper() in available]
    chosen = st.multiselect("กองที่จะใส่ในพอร์ตจำลอง", available, default=default, key="wi_funds")
    if len(chosen) < 2:
        st.info("เลือกอย่างน้อย 2 กอง")
        return
    kinds = pd.DataFrame([{"กอง": t, "ชนิดที่โมเดลสมมติ": asset_for(t).kind, "หมายเหตุ": asset_for(t).note or "",
                           "ชนิดเป็นการเดา": "ใช่" if is_guessed(t) else ""} for t in chosen])
    st.dataframe(kinds, use_container_width=True, hide_index=True)
    budget = st.number_input("งบต่อเดือน (บาท)", min_value=500.0, max_value=1_000_000.0,
                             value=float(cfg["dca"]["monthly_budget_thb"]), step=500.0, key="wi_budget")
    cols = st.columns(len(chosen))
    weights = {}
    for col, t in zip(cols, chosen):
        with col:
            weights[t] = st.number_input(f"{t} (%)", min_value=0.0, max_value=100.0, value=round(100.0 / len(chosen), 1), step=1.0, key=f"wi_{t}")
    if st.button("ลองพอร์ตนี้ใน simulation", key="wi_run"):
        if sum(weights.values()) <= 0:
            st.error("น้ำหนักรวมต้องมากกว่า 0")
            return
        with st.spinner("กำลังรัน simulation ..."):
            try:
                res = service.simulate_universe({t: w for t, w in weights.items() if w > 0}, budget_thb=float(budget), workers=2)
            except (sim_data.SimulationDataError, ValueError) as exc:
                st.error(f"รัน simulation ไม่ได้: {exc}")
                return
        _show_universe_result(res)
