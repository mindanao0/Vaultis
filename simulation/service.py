# -*- coding: utf-8 -*-
"""บริการ simulation: รันแผนที่ระบบคำนวณแล้วสรุปเป็นช่วงผลลัพธ์ + ข้อจำกัด — ที่เดียวที่ dashboard / API / Discord เรียก.

หลักการ (ตามหลักฐานใน research/dar_sim): ผลคือ **ช่วง** ไม่ใช่ตัวเลขเดียว · แยกตาม "โลก" ของส่วนต่างระหว่างกอง ไม่เฉลี่ยรวม ·
ข้อจำกัดของโมเดลต้องไปพร้อมตัวเลขเสมอ · ไม่มี LLM (ตัวเลขทุกตัวคำนวณในโค้ด)
"""
from __future__ import annotations

import json
import logging
import math
import os
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from simulation import data as sim_data
from simulation import engine

logger = logging.getLogger(__name__)

LAST_PLAN_FILE = "last_plan.json"
#: โลกที่รันเป็นค่าเริ่มต้น: สุ่มเดิน (ไม่มีรูปแบบ) · ต่อเนื่อง (ร้ายต่อสูตรสวนทางราคา) · ประวัติจริง (ไม่พึ่งข้อสมมติเหตุการณ์)
DEFAULT_WORLDS = ("rw", "mom", "boot")
DEFAULT_HORIZONS = (60, 120, 240)
DEFAULT_PATHS = 3000          # ต่อโลก (ชุดวิจัยใช้ 300,000) — พอให้เห็นขนาดผลต่างใหญ่ ๆ ไม่พอแยกผลต่าง ~0.1 pp
MAX_PATHS = 60000
CHUNK = 3000
#: ผลต่างเล็กกว่านี้ (pp ของ IRR/ปี) ที่ simulation จำนวนเส้นทางระดับนี้แยกจากสัญญาณรบกวนไม่ได้
NOISE_FLOOR_PP = 0.15

LIMITATIONS = (
    "ผลตอบแทนคาดหวังข้างหน้าและเหตุการณ์ใหญ่ส่วนใหญ่ (สงคราม ฟองสบู่ เงินเฟ้อยาว ฯลฯ) เป็น **ข้อสมมติ** ไม่ใช่ข้อมูลที่วัด",
    "ใช้ **เทียบสูตรกัน** ไม่ใช่พยากรณ์ผลตอบแทนจริง และสร้างหลักฐานว่าสูตรไหนชนะไม่ได้ (ผลคือสิ่งที่สมมติใส่เข้าไป)",
    "แผนในโมเดลเป็นสัดส่วนฐานล้วน ไม่รวมการเอียงตามคะแนนรายเดือน 0.6–1.4× ของแผนจริง",
    "ไม่หักภาษีหัก ณ ที่จ่ายปันผล 15% (กองที่ปันผลสูงจึงดูดีกว่าความจริงเล็กน้อย)",
    "ข้อมูลกองเริ่มราว 2004 ไม่มีทศวรรษ 1970 — เหตุการณ์แบบนั้นมาจากข้อสมมติเท่านั้น",
    "ช่วงความเชื่อมั่นคือความคลาดเคลื่อนของการสุ่ม ไม่ใช่ความไม่แน่นอนของโมเดล ซึ่งใหญ่กว่ามาก",
)


# ---------------------------------------------------------------- รันเอนจิน
def _task(args):
    panel, cfg = args
    r = engine.run_chunk(panel, cfg)
    return {h: {a: {k: np.asarray(x[k], dtype=np.float32) for k in ("V", "V_real", "contrib_real", "irr", "maxdd")}
                for a, x in r["H"][h]["arms"].items()} for h in cfg.horizons}


def run_world(panel: dict, world: str, arms: tuple, fixed: dict | None, *, paths: int, horizons: tuple, seed: int,
              budget_thb: float, drift: str = "mid", event_mult: float = 1.0, workers: int = 1) -> dict[int, dict[str, dict]]:
    """รันหนึ่งโลก → ``{ขอบฟ้า: {กลยุทธ์: {V, irr, maxdd, ...}}}`` ต่อเส้นทางรวมทุกก้อน."""
    if not 100 <= paths <= MAX_PATHS:
        raise ValueError(f"จำนวนเส้นทางต้องอยู่ระหว่าง 100 ถึง {MAX_PATHS:,} (ได้ {paths})")
    n_chunks = max(1, -(-paths // CHUNK))
    sizes = [min(CHUNK, paths - i * CHUNK) for i in range(n_chunks)]
    cfgs = [engine.Config(world=world, drift=drift, event_mult=event_mult, P=s, seed=seed + 1000 * i, arms=arms, fixed=fixed,
                          horizons=horizons, budget_thb=budget_thb, fx_revert=(0.0 if world == "boot" else engine.FX_REVERT))
            for i, s in enumerate(sizes)]
    if workers > 1 and n_chunks > 1:
        with ProcessPoolExecutor(max_workers=min(workers, n_chunks)) as pool:
            parts = list(pool.map(_task, [(panel, c) for c in cfgs]))
    else:
        parts = [_task((panel, c)) for c in cfgs]
    out: dict[int, dict[str, dict]] = {}
    for h in horizons:
        out[h] = {a: {k: np.concatenate([p[h][a][k] for p in parts]) for k in parts[0][h][a]} for a in parts[0][h]}
    return out


def _pct(x, q) -> float:
    return float(np.percentile(x, q))


def summarize_world(res: dict[int, dict[str, dict]], budget_thb: float, reference: str) -> dict[str, Any]:
    """สถิติต่อกลยุทธ์ + ผลต่างจับคู่เทียบ ``reference`` (เส้นทางเดียวกัน)."""
    out: dict[str, Any] = {}
    for h, arms in res.items():
        rows = {}
        for a, x in arms.items():
            irr = x["irr"].astype(np.float64) * 100
            dd = x["maxdd"].astype(np.float64) * 100
            rows[a] = {
                "irr_p5": _pct(irr, 5), "irr_p50": _pct(irr, 50), "irr_p95": _pct(irr, 95),
                "dd_med": _pct(dd, 50), "dd_p95": _pct(dd, 95),
                "p_loss_nominal": float((x["V"] < budget_thb * h).mean()),
                "p_loss_real": float((x["V_real"] < x["contrib_real"]).mean()),
                "real_mult_med": float(np.median(x["V_real"] / x["contrib_real"])),
                "value_med_thb": float(np.median(x["V"])), "value_p5_thb": _pct(x["V"], 5),
                "contributed_thb": float(budget_thb * h),
            }
        paired = {}
        if reference in arms:
            ref = arms[reference]
            for a, x in arms.items():
                if a == reference:
                    continue
                d_irr = (x["irr"].astype(np.float64) - ref["irr"].astype(np.float64)) * 100
                d_dd = (x["maxdd"].astype(np.float64) - ref["maxdd"].astype(np.float64)) * 100
                se = d_irr.std(ddof=1) / math.sqrt(len(d_irr))
                paired[a] = {"d_irr_mean": float(d_irr.mean()), "d_irr_ci95": [float(d_irr.mean() - 1.96 * se), float(d_irr.mean() + 1.96 * se)],
                             "d_dd_median_of_diff": float(np.median(d_dd)), "p_beats_ref": float((d_irr > 0).mean())}
        out[str(h)] = {"strategies": rows, "vs_reference": paired}
    return out


def simulate_strategies(panel: dict, strategies: dict[str, Any], *, reference: str, budget_thb: float, worlds=DEFAULT_WORLDS,
                        horizons=DEFAULT_HORIZONS, paths: int | None = None, seed: int = 20261005, drift: str = "mid",
                        workers: int = 1) -> dict[str, Any]:
    """รันหลายกลยุทธ์บนเส้นทางเดียวกันในหลายโลก.

    ``strategies``: ``{ชื่อ: "DAR"|"EQ"|"ERC"|"BLEND"}`` (สูตรสด คำนวณใหม่ทุกเดือนในโลกจำลอง)
    หรือ ``{ชื่อ: {ticker: น้ำหนัก}}`` (น้ำหนักคงที่ เช่นแผน preset/กำหนดเอง)
    """
    paths = DEFAULT_PATHS if paths is None else paths
    funds = list(panel["funds"])
    dynamic = tuple(v for v in strategies.values() if isinstance(v, str))
    unknown = [v for v in dynamic if v not in ("DAR", "EQ", "ERC", "BLEND")]
    if unknown:
        raise ValueError(f"ไม่รู้จักกลยุทธ์ {unknown}")
    if "DAR" in dynamic and len(panel["live_me_logs"]) < engine.HISTORY_MONTHS:
        raise ValueError("ประวัติราคาไม่ถึง 181 เดือน — จำลองสูตร DAR ไม่ได้")
    fixed = {}
    for name, spec in strategies.items():
        if isinstance(spec, dict):
            outside = sorted(set(spec) - set(funds))
            if outside:
                raise ValueError(f"น้ำหนักของ {name} มีกองที่ไม่อยู่ในข้อมูล simulation: {', '.join(outside)} (เพิ่มกองใน Settings ก่อน)")
            w = np.array([float(spec.get(f, 0.0)) for f in funds])
            if (w < 0).any() or not np.isfinite(w).all() or w.sum() <= 0:
                raise ValueError(f"น้ำหนักของ {name} ใช้ไม่ได้")
            fixed[name] = w / w.sum()
    # ชื่อของสูตรสดในเอนจินคือชื่อสูตรเอง — จับคู่กลับเป็นชื่อที่ผู้เรียกตั้ง
    name_of = {v: k for k, v in strategies.items() if isinstance(v, str)}
    result: dict[str, Any] = {"worlds": {}, "reference": reference}
    for i, world in enumerate(worlds):
        res = run_world(panel, world, tuple(dict.fromkeys(dynamic)), fixed or None, paths=paths, horizons=tuple(horizons),
                        seed=seed + 7919 * i, budget_thb=budget_thb, drift=drift, workers=workers)
        renamed = {h: {name_of.get(a, a): x for a, x in arms.items()} for h, arms in res.items()}
        result["worlds"][world] = {"label": engine.WORLD_TH[world], "horizons": summarize_world(renamed, budget_thb, reference)}
    return result


# ---------------------------------------------------------------- แผนปัจจุบันของระบบ
def _now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=7))).isoformat(timespec="seconds")


def simulate_current_plan(*, paths: int | None = None, workers: int = 1, panel: dict | None = None,
                          config: dict | None = None, target_status: Any = None, seed: int = 20261005) -> dict[str, Any]:
    """รันแผนที่ระบบใช้อยู่ตอนนี้ (วิธีสัดส่วนฐานจาก config) เทียบ ERC / blend / 1/N แล้วคืนผลพร้อมข้อจำกัดและที่มา.

    ดึงราคาไม่ได้/ไม่มีข้อมูล simulation → ``SimulationDataError`` (ห้ามคืนผลที่เดา)
    """
    from portfolio.targets import get_target_weights_with_status
    from utils.config import load_config

    paths = DEFAULT_PATHS if paths is None else paths
    cfg = config or load_config()
    tickers = [str(t).strip().upper() for t in cfg["etf"]["tickers"]]
    budget = float(cfg["dca"]["monthly_budget_thb"])
    if panel is None:
        panel = sim_data.load_panel(tickers)
    status = target_status or get_target_weights_with_status(tickers)
    method = status.method
    notes: list[str] = []
    if panel["meta"].get("guessed_kinds"):
        notes.append(f"ไม่รู้จักชนิดของ {', '.join(panel['meta']['guessed_kinds'])} — ใช้ค่าสมมติของหุ้นสหรัฐ")
    strategies: dict[str, Any] = {"ERC": "ERC", "BLEND": "BLEND", "1/N": "EQ"}
    if method in ("blend", "erc"):
        plan_name = "BLEND" if method == "blend" else "ERC"
    else:
        plan_name = "แผนปัจจุบัน"
        strategies[plan_name] = {t: float(status.weights[t]) for t in panel["funds"] if t in status.weights}
        if method == "erc_sector_cap":
            notes.append("แผนนี้ใช้เพดานเซกเตอร์ — จำลองด้วยน้ำหนักของวันนี้คงที่ (ไม่คำนวณใหม่ทุกเดือนในโลกจำลอง)")
    reference = "ERC"
    res = simulate_strategies(panel, strategies, reference=reference, budget_thb=budget, paths=paths, workers=workers, seed=seed)
    out = {
        "created_at": _now_iso(),
        "plan": {"method": method, "plan_strategy": plan_name, "tickers": panel["funds"], "budget_thb": budget,
                 "weights_today": {t: float(w) for t, w in status.weights.items()}},
        "paths_per_world": paths, "seed": seed, "reference": reference, "worlds": res["worlds"],
        "data": {"as_of": panel["meta"]["as_of"], "last_bar": panel["meta"]["last_bar"], "fetched_at": panel["meta"]["fetched_at"],
                 "pool_months": panel["meta"]["n_pool_months"], "raw_sha256": panel["meta"]["raw_sha256"]},
        "notes": notes, "limitations": list(LIMITATIONS), "noise_floor_pp": NOISE_FLOOR_PP,
    }
    return out


# ---------------------------------------------------------------- เก็บ/อ่านผลล่าสุด
def save_last_plan(result: dict[str, Any], directory: Path | None = None) -> Path:
    d = Path(directory or sim_data.DATA_DIR)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (LAST_PLAN_FILE + ".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, d / LAST_PLAN_FILE)
    return d / LAST_PLAN_FILE


def load_last_plan(directory: Path | None = None) -> dict[str, Any] | None:
    p = Path(directory or sim_data.DATA_DIR) / LAST_PLAN_FILE
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.error("อ่านผล simulation ล่าสุดไม่ได้: %s", exc)
        return None


def last_plan_age_days(result: dict[str, Any] | None, now: datetime | None = None) -> float | None:
    if not result or not result.get("created_at"):
        return None
    now = now or datetime.now(timezone(timedelta(hours=7)))
    return (now - datetime.fromisoformat(result["created_at"])).total_seconds() / 86400.0


# ---------------------------------------------------------------- ข้อความสรุป (Discord / dashboard)
def summary_lines(result: dict[str, Any] | None, *, stale_after_days: float = 14.0) -> list[str]:
    """สรุปสั้น ๆ ของผล simulation เป็นข้อความไทย — ไม่มีผล/เก่าเกินไป = **บอก** ไม่ใช่เงียบ (ไม่มีข้อความ ≠ ปลอดภัย)."""
    if not result:
        return ["🧪 Simulation: ยังไม่มีผลของแผนนี้ (ยังไม่ได้ดึงข้อมูลหรือยังไม่เคยรัน) — ไม่ได้แปลว่าแผนผ่านการจำลองแล้ว"]
    age = last_plan_age_days(result)
    plan = result["plan"]["plan_strategy"]
    lines: list[str] = []
    if age is not None and age > stale_after_days:
        lines.append(f"🧪 Simulation: ผลล่าสุดเก่า {age:.0f} วัน (ข้อมูลถึง {result['data']['as_of']}) — ใช้ประกอบอย่างระมัดระวัง")
    try:
        rw = result["worlds"]["rw"]["horizons"]
        h20, h10 = rw["240"]["strategies"], rw["120"]["strategies"]
        s20, s10 = h20[plan], h10[plan]
        lines.append(
            f"🧪 Simulation แผน ({plan}) · DCA {result['plan']['budget_thb']:,.0f} บาท/เดือน ไม่หยุด · โลกสุ่มเดิน {result['paths_per_world']:,} เส้นทาง")
        lines.append(
            f"   10 ปี: ผลตอบแทน/ปี มัธยฐาน {s10['irr_p50']:.1f}% (แย่สุด 5% {s10['irr_p5']:.1f}%) · ขาดทุนเป็นบาท {s10['p_loss_nominal']*100:.0f}% "
            f"· หลังเงินเฟ้อ {s10['p_loss_real']*100:.0f}% · ขาดทุนสูงสุดมัธยฐาน {s10['dd_med']:.0f}%")
        lines.append(
            f"   20 ปี: ผลตอบแทน/ปี มัธยฐาน {s20['irr_p50']:.1f}% (แย่สุด 5% {s20['irr_p5']:.1f}%) · ขาดทุนเป็นบาท {s20['p_loss_nominal']*100:.0f}% "
            f"· หลังเงินเฟ้อ {s20['p_loss_real']*100:.0f}% · ขาดทุนสูงสุดมัธยฐาน {s20['dd_med']:.0f}%")
        if plan != "ERC":
            v = rw["240"]["vs_reference"].get(plan)
            if v:
                lo, hi = v["d_irr_ci95"]
                tag = "แยกจากสัญญาณรบกวนไม่ได้" if abs(v["d_irr_mean"]) < NOISE_FLOOR_PP and lo < 0 < hi else ""
                lines.append(f"   เทียบ ERC ล้วน (20 ปี): ผลตอบแทน {v['d_irr_mean']:+.2f} pp/ปี · MaxDD {v['d_dd_median_of_diff']:+.1f} จุด {tag}".rstrip())
        if "boot" in result["worlds"]:
            b = result["worlds"]["boot"]["horizons"]["240"]["strategies"][plan]
            lines.append(f"   โลกประวัติจริงสุ่มบล็อก (ไม่พึ่งข้อสมมติเหตุการณ์): มัธยฐาน {b['irr_p50']:.1f}%/ปี ขาดทุนสูงสุดมัธยฐาน {b['dd_med']:.0f}% — อดีตดีผิดปกติ ถือเป็นขอบบน")
    except (KeyError, TypeError) as exc:
        return [f"🧪 Simulation: อ่านผลไม่ได้ ({exc}) — รันใหม่"]
    lines.append(f"   ข้อมูลถึง {result['data']['as_of']} · ใช้เทียบสูตรกัน ไม่ใช่พยากรณ์ · ข้อจำกัด: ผลตอบแทนคาดหวัง/เหตุการณ์เป็นข้อสมมติ, ฐานล้วนไม่รวมการเอียงตามคะแนน")
    return lines
