# -*- coding: utf-8 -*-
"""รัน PREREG_NEW: สูตรใหม่ (CF_ERC, CF_EQ, BLEND) เทียบ ERC ด้วยเส้นทางจำนวนมาก + headroom.

    python run_new.py <scratch_dir> main      # 5 โลก × 300,000 เส้นทาง        → results/new_main.json
    python run_new.py <scratch_dir> sens      # 4 โลก × 4 ความไว × 100,000     → results/new_sens.json
    python run_new.py <scratch_dir> headroom  # น้ำหนักคงที่ที่ดีที่สุดแค่ไหน    → results/new_headroom.json
    python run_new.py <scratch_dir> verdict   # ตัดสินตามเกณฑ์ที่ล็อก            → results/new_verdict.json

ค่าเกณฑ์ด้านล่างต้องตรงกับ PREREG_NEW.md ทุกตัว (ห้ามแก้หลังเห็นผล)
"""
from __future__ import annotations

import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import sim  # noqa: E402

scratch = Path(sys.argv[1])
stage = sys.argv[2]
PANEL = pd.read_pickle(scratch / "panel.pkl")
OUT = Path(__file__).parent / "results"
OUT.mkdir(exist_ok=True)
CHUNK = 3000
NPROC = 7

# ---------------------------------------------------------------- เกณฑ์ที่ล็อก (PREREG_NEW.md)
CANDS = ("CF_ERC", "CF_EQ", "BLEND")
BASE = ("DAR", "EQ", "ERC", "PRESET", "VOO")
Z = 2.394                 # ช่วงความเชื่อมั่น 98.3% สองข้าง (0.05 / 3 สูตร)
A1_IRR_PP = 0.05          # IRR มัธยฐานต่ำกว่า ERC ได้ไม่เกิน
A1_DD_MED_PT = 1.0        # MaxDD มัธยฐานลึกกว่า ERC ได้ไม่เกิน
A1_DD_P95_PT = 2.0        # MaxDD แย่สุด 5% ลึกกว่า ERC ได้ไม่เกิน
A2_IRR_PP = 0.10          # ΔIRR ที่ถือว่ามีประโยชน์ (และขอบล่างของช่วงต้อง > 0)
A2_DD_PT = 2.0            # MaxDD มัธยฐานดีกว่า ERC (และช่วงจับคู่ต้องไม่คร่อมศูนย์)
A2_WORLDS = 3             # ต้องมีประโยชน์ในอย่างน้อยกี่โลก (จาก 5) ที่ 10 และ 20 ปีพร้อมกัน
HZ = (120, 240)


def _task(cfg):
    r = sim.run_chunk(PANEL, cfg)
    out = {}
    for h in cfg.horizons:
        out[h] = {a: {k: np.asarray(x[k], dtype=np.float32) for k in ("V", "irr", "maxdd")} for a, x in r["H"][h]["arms"].items()}
    return out


def run_config(pool, world, drift, em, paths, seed0, arms, horizons=HZ, fixed=None, round_units=True):
    cfgs = [sim.Config(world=world, drift=drift, event_mult=em, P=CHUNK, T=240, seed=seed0 + i, arms=arms,
                       horizons=horizons, fixed=fixed, fx_revert=(0.0 if world == "boot" else sim.FX_REVERT),
                       round_units=round_units) for i in range(paths // CHUNK)]
    parts = pool.map(_task, cfgs, chunksize=1)
    merged = {}
    for h in horizons:
        merged[h] = {a: {k: np.concatenate([p[h][a][k] for p in parts]) for k in parts[0][h][a]} for a in parts[0][h]}
    return merged


def paired(a, b):
    """a − b (IRR: pp, MaxDD: จุด%) พร้อมช่วงความเชื่อมั่น 98.3% ของค่าเฉลี่ยจับคู่."""
    d_irr = (a["irr"].astype(np.float64) - b["irr"].astype(np.float64)) * 100
    d_dd = (a["maxdd"].astype(np.float64) - b["maxdd"].astype(np.float64)) * 100
    n = len(d_irr)
    out = {}
    for name, d in (("d_irr", d_irr), ("d_dd", d_dd)):
        se = d.std(ddof=1) / np.sqrt(n)
        out[name] = {"mean": float(d.mean()), "lo": float(d.mean() - Z * se), "hi": float(d.mean() + Z * se)}
    return out


def summarize(res):
    out = {}
    for h, arms in res.items():
        rows = {}
        for a, x in arms.items():
            irr, dd = x["irr"].astype(np.float64) * 100, x["maxdd"].astype(np.float64) * 100
            rows[a] = {"irr_med": float(np.median(irr)), "irr_p5": float(np.percentile(irr, 5)),
                       "dd_med": float(np.median(dd)), "dd_p95": float(np.percentile(dd, 95)),
                       "p_nominal_loss": float((x["V"] < 5000.0 * h).mean())}
        vs_erc = {c: paired(arms[c], arms["ERC"]) for c in arms if c != "ERC"}
        out[str(h)] = {"arms": rows, "vs_erc": vs_erc}
    return out


# ---------------------------------------------------------------- ตัดสิน
def a1_violations(summary_by_cfg: dict, cand: str) -> list[str]:
    bad = []
    for cfg_key, s in summary_by_cfg.items():
        for h in map(str, HZ):
            r = s[h]["arms"]
            di = r[cand]["irr_med"] - r["ERC"]["irr_med"]
            dm = r[cand]["dd_med"] - r["ERC"]["dd_med"]
            dp = r[cand]["dd_p95"] - r["ERC"]["dd_p95"]
            if di < -A1_IRR_PP:
                bad.append(f"{cfg_key} {int(h)//12}y: IRR {di:+.2f}pp < -{A1_IRR_PP}")
            if dm > A1_DD_MED_PT:
                bad.append(f"{cfg_key} {int(h)//12}y: MaxDD มัธยฐาน {dm:+.1f} > +{A1_DD_MED_PT}")
            if dp > A1_DD_P95_PT:
                bad.append(f"{cfg_key} {int(h)//12}y: MaxDD p95 {dp:+.1f} > +{A1_DD_P95_PT}")
    return bad


def a2_worlds(main: dict, cand: str) -> dict:
    ok = {}
    for world, s in main.items():
        per_h = []
        for h in map(str, HZ):
            v = s[h]["vs_erc"][cand]
            r = s[h]["arms"]
            ben_irr = v["d_irr"]["mean"] >= A2_IRR_PP and v["d_irr"]["lo"] > 0
            ben_dd = (r["ERC"]["dd_med"] - r[cand]["dd_med"]) >= A2_DD_PT and v["d_dd"]["hi"] < 0
            per_h.append(bool(ben_irr or ben_dd))
        ok[world] = all(per_h)
    return ok


def verdict(main, sens):
    out = {}
    for c in CANDS:
        v_main = a1_violations(main, c)
        v_sens = a1_violations(sens, c)
        w2 = a2_worlds(main, c)
        a1 = not v_main
        a2 = sum(w2.values()) >= A2_WORLDS
        a3 = not v_sens
        out[c] = {"A1": a1, "A2": a2, "A3": a3, "PASS": a1 and a2 and a3, "A2_worlds": w2,
                  "A1_violations": v_main, "A3_violations": v_sens}
    return out


if __name__ == "__main__":
    t0 = time.time()
    ctx = mp.get_context("fork")
    ARMS = BASE + CANDS
    with ctx.Pool(NPROC) as pool:
        if stage == "main":
            res = {}
            for i, world in enumerate(("rw", "rev", "mom", "prem", "boot")):
                r = run_config(pool, world, "mid", 1.0, 300_000, 20_000 + 1000 * i, ARMS)
                res[world] = summarize(r)
                s = res[world]["240"]
                print(f"[{time.time()-t0:6.0f}s] {world:5s} 20y IRR med " + " ".join(f"{a}={s['arms'][a]['irr_med']:.2f}" for a in ARMS), flush=True)
            json.dump(res, open(OUT / "new_main.json", "w"), ensure_ascii=False, indent=1)
        elif stage == "sens":
            res = {}
            seed = 60_000
            for world in ("rw", "rev", "mom", "prem"):
                for label, drift, em in (("low", "low", 1.0), ("hist", "hist", 1.0), ("ev0", "mid", 0.0), ("ev2", "mid", 2.0)):
                    r = run_config(pool, world, drift, em, 100_000, seed, ARMS)
                    seed += 100
                    res[f"{world}|{label}"] = summarize(r)
                    print(f"[{time.time()-t0:6.0f}s] {world}|{label}", flush=True)
            json.dump(res, open(OUT / "new_sens.json", "w"), ensure_ascii=False, indent=1)
        elif stage == "headroom":
            rng = np.random.default_rng(77)
            ws = np.vstack([np.eye(5), rng.dirichlet(np.ones(5), size=400)])
            fixed = {f"F{i:03d}": ws[i] for i in range(len(ws))}
            out = {}
            for label, world, drift in (("rw|mid", "rw", "mid"), ("rw|low", "rw", "low"), ("rw|hist", "rw", "hist"), ("boot", "boot", "mid")):
                tr = run_config(pool, world, drift, 1.0, 12_000, 90_000, ("EQ", "ERC", "DAR"), (240,), fixed, round_units=False)[240]
                te = run_config(pool, world, drift, 1.0, 24_000, 95_000, ("EQ", "ERC", "DAR"), (240,), fixed, round_units=False)[240]
                med = lambda d, k, f=np.median: float(f(d[k].astype(np.float64)) * 100)
                stat = lambda d: {a: {"irr": med(d[a], "irr"), "dd_med": med(d[a], "maxdd"),
                                      "dd_p95": float(np.percentile(d[a]["maxdd"].astype(np.float64), 95) * 100)} for a in d}
                s_tr, s_te = stat(tr), stat(te)
                names = list(fixed)
                erc_tr = s_tr["ERC"]
                # เลือกบน "ฝึก": (ก) IRR สูงสุด โดย MaxDD มัธยฐานไม่เกินของ ERC  (ข) MaxDD p95 ต่ำสุด โดย IRR ไม่ต่ำกว่า ERC
                cand_a = [n for n in names if s_tr[n]["dd_med"] <= erc_tr["dd_med"]]
                best_a = max(cand_a, key=lambda n: s_tr[n]["irr"]) if cand_a else None
                cand_b = [n for n in names if s_tr[n]["irr"] >= erc_tr["irr"]]
                best_b = min(cand_b, key=lambda n: s_tr[n]["dd_p95"]) if cand_b else None
                best_irr = max(names, key=lambda n: s_tr[n]["irr"])
                out[label] = {
                    "test": {"ERC": s_te["ERC"], "EQ": s_te["EQ"], "DAR": s_te["DAR"]},
                    "pick_max_irr_at_erc_risk": None if best_a is None else {"w": [round(float(x), 3) for x in fixed[best_a]], "train": s_tr[best_a], "test": s_te[best_a]},
                    "pick_min_tail_at_erc_return": None if best_b is None else {"w": [round(float(x), 3) for x in fixed[best_b]], "train": s_tr[best_b], "test": s_te[best_b]},
                    "pick_max_irr_any_risk": {"w": [round(float(x), 3) for x in fixed[best_irr]], "train": s_tr[best_irr], "test": s_te[best_irr]},
                    "n_weights_better_than_erc_on_both_test": int(sum(1 for n in names if s_te[n]["irr"] > s_te["ERC"]["irr"] and s_te[n]["dd_p95"] < s_te["ERC"]["dd_p95"])),
                    "n_weights": len(names),
                }
                print(f"[{time.time()-t0:6.0f}s] headroom {label}", flush=True)
            json.dump(out, open(OUT / "new_headroom.json", "w"), ensure_ascii=False, indent=1)
        elif stage == "verdict":
            main = json.load(open(OUT / "new_main.json"))
            sens = json.load(open(OUT / "new_sens.json"))
            v = verdict(main, sens)
            json.dump(v, open(OUT / "new_verdict.json", "w"), ensure_ascii=False, indent=1)
            for c, x in v.items():
                print(c, "A1", x["A1"], "A2", x["A2"], x["A2_worlds"], "A3", x["A3"], "=> PASS" if x["PASS"] else "=> ไม่ผ่าน")
    print("เสร็จใน", round(time.time() - t0), "วินาที")
