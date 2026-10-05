# -*- coding: utf-8 -*-
"""รัน simulation ชุดใหญ่ + สรุปผล.

    python run.py <scratch_dir> grid    # 4 โลก × ชุดผลตอบแทน × เหตุการณ์ → results/grid.json
    python run.py <scratch_dir> sweep   # กวาด kappa หาจุดคุ้มทุนของ DAR → results/sweep.json

เส้นทางจำลองทุกเส้นเป็นอิสระ ⇒ แบ่งเป็นก้อนแล้วกระจายข้าม CPU (numpy ต่อก้อนใช้คอร์เดียว)
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
PATHS = int(sys.argv[3]) if len(sys.argv) > 3 else 60000


def _task(args):
    cfg = args
    r = sim.run_chunk(PANEL, cfg)
    out = {"H": {}}
    for h, d in r["H"].items():
        out["H"][h] = {
            "arms": {a: {k: np.asarray(v, dtype=np.float32) for k, v in x.items()} for a, x in d["arms"].items()},
            "macro": {k: np.asarray(v, dtype=np.float32 if v.dtype != bool else bool) for k, v in d["macro"].items()},
        }
    out["dar_w"] = r["dar_mean_weights"]
    out["erc_sweeps_max"] = r["erc_sweeps_max"]
    out["event_names"] = r["event_names"]
    return out


def run_config(pool, world, drift, event_mult, paths, seed0, kappa=None, arms=sim.ARMS):
    cfgs = [sim.Config(world=world, drift=drift, event_mult=event_mult, P=CHUNK, T=240,
                       seed=seed0 + i, arms=arms, kappa=kappa) for i in range(paths // CHUNK)]
    parts = pool.map(_task, cfgs)
    merged = {"H": {}, "dar_w": np.mean([p["dar_w"] for p in parts], axis=0) if "DAR" in arms else None,
              "erc_sweeps_max": max(p["erc_sweeps_max"] or 0 for p in parts), "event_names": parts[0]["event_names"]}
    for h in parts[0]["H"]:
        merged["H"][h] = {
            "arms": {a: {k: np.concatenate([p["H"][h]["arms"][a][k] for p in parts]) for k in parts[0]["H"][h]["arms"][a]}
                     for a in arms},
            "macro": {k: np.concatenate([p["H"][h]["macro"][k] for p in parts]) for k in parts[0]["H"][h]["macro"]},
        }
    return merged


# ------------------------------------------------------------------ สรุป
def pct(x, q):
    return float(np.percentile(x, q))


def summarize(res) -> dict:
    out = {}
    for h, d in res["H"].items():
        A = d["arms"]
        rows = {}
        for a, x in A.items():
            contrib = 5000.0 * h
            rows[a] = {
                "V_med_k": float(np.median(x["V"]) / 1e3),
                "irr_med": float(np.median(x["irr"]) * 100), "irr_p5": pct(x["irr"], 5) * 100, "irr_p95": pct(x["irr"], 95) * 100,
                "p_nominal_loss": float((x["V"] < contrib).mean()),
                "p_real_loss": float((x["V_real"] < x["contrib_real"]).mean()),
                "real_mult_med": float(np.median(x["V_real"] / x["contrib_real"])),
                "maxdd_med": float(np.median(x["maxdd"]) * 100), "maxdd_p95": pct(x["maxdd"], 95) * 100,
                "under_share": float(np.mean(x["under"]) * 100),
            }
        paired = {}
        for a, b in (("DAR", "EQ"), ("DAR", "ERC"), ("ERC", "EQ"), ("DAR", "PRESET"), ("EQ", "VOO")):
            if a in A and b in A:
                dlt = (A[a]["irr"] - A[b]["irr"]).astype(np.float64) * 100
                lw = np.log(A[a]["V"].astype(np.float64) / A[b]["V"].astype(np.float64))
                se = dlt.std(ddof=1) / np.sqrt(len(dlt))
                k5 = np.sort(dlt)[: max(1, int(0.05 * len(dlt)))]
                paired[f"{a}-{b}"] = {
                    "d_irr_mean": float(dlt.mean()), "ci95": [float(dlt.mean() - 1.96 * se), float(dlt.mean() + 1.96 * se)],
                    "d_irr_med": float(np.median(dlt)), "p_a_wins": float((dlt > 0).mean()), "cvar5": float(k5.mean()),
                    "wealth_ratio_med": float(np.exp(np.median(lw))), "wealth_ratio_p5": float(np.exp(pct(lw, 5))),
                    "wealth_ratio_p95": float(np.exp(pct(lw, 95))),
                }
        out[h] = {"arms": rows, "paired": paired}
    return out


def conditional(res, h=240) -> dict:
    """ผลต่างระหว่างกลยุทธ์แยกตามสภาพเศรษฐกิจของเส้นทาง (ที่ขอบฟ้า h)."""
    A, M = res["H"][h]["arms"], res["H"][h]["macro"]
    irr = {a: A[a]["irr"].astype(np.float64) * 100 for a in A}
    names = res["event_names"]
    masks = {}
    q = np.percentile(M["th_infl_cagr"], [33.3, 66.7])
    masks["เงินเฟ้อไทยต่ำ (ล่าง 1/3)"] = M["th_infl_cagr"] <= q[0]
    masks["เงินเฟ้อไทยกลาง"] = (M["th_infl_cagr"] > q[0]) & (M["th_infl_cagr"] <= q[1])
    masks["เงินเฟ้อไทยสูง (บน 1/3)"] = M["th_infl_cagr"] > q[1]
    masks["บาทอ่อนลง >20%"] = M["fx_change"] > 0.20
    masks["บาทนิ่ง (±20%)"] = np.abs(M["fx_change"]) <= 0.20
    masks["บาทแข็งขึ้น >20%"] = M["fx_change"] < -0.20
    qc = np.percentile(M["crisis_share"], [33.3, 66.7])
    masks["เวลาในภาวะวิกฤตน้อย"] = M["crisis_share"] <= qc[0]
    masks["เวลาในภาวะวิกฤตมาก"] = M["crisis_share"] > qc[1]
    for i, n in enumerate(names):
        masks[f"เจอเหตุการณ์ {n}"] = M["events"][:, i]
    masks["ไม่เจอเหตุการณ์ใหญ่เลย"] = ~M["events"].any(axis=1)
    out = {}
    for label, m in masks.items():
        if m.sum() < 200:
            continue
        out[label] = {
            "share": float(m.mean()),
            **{f"irr_{a}": float(np.median(irr[a][m])) for a in irr},
            "DAR-EQ": float((irr["DAR"][m] - irr["EQ"][m]).mean()),
            "ERC-EQ": float((irr["ERC"][m] - irr["EQ"][m]).mean()),
            "DAR-ERC": float((irr["DAR"][m] - irr["ERC"][m]).mean()),
            "p_real_loss_EQ": float((A["EQ"]["V_real"][m] < A["EQ"]["contrib_real"][m]).mean()),
        }
    return out


def macro_summary(res, h) -> dict:
    M = res["H"][h]["macro"]
    f = lambda k, s=1.0: [round(pct(M[k], q) * s, 2) for q in (5, 50, 95)]
    return {"th_infl_pct": f("th_infl_cagr", 100), "fx_change_pct": f("fx_change", 100), "y10_end": f("y10_end"),
            "oil_max": f("oil_max"), "equity_cagr_pct": f("equity_cagr", 100), "crisis_share_pct": f("crisis_share", 100)}


if __name__ == "__main__":
    t0 = time.time()
    ctx = mp.get_context("fork")
    with ctx.Pool(6) as pool:
        if stage == "grid":
            result = {}
            seed = 1000
            for world in ("rw", "rev", "mom", "prem"):
                for drift, em in (("mid", 1.0), ("low", 1.0), ("hist", 1.0), ("mid", 0.0), ("mid", 2.0)):
                    key = f"{world}|{drift}|ev{em:g}"
                    res = run_config(pool, world, drift, em, PATHS, seed)
                    seed += 100
                    result[key] = {"summary": summarize(res), "macro": {h: macro_summary(res, h) for h in res["H"]},
                                   "dar_mean_weights": [round(x, 4) for x in res["dar_w"]], "erc_sweeps_max": res["erc_sweeps_max"]}
                    if key in ("rw|mid|ev1", "rev|mid|ev1", "mom|mid|ev1", "prem|mid|ev1"):
                        result[key]["conditional"] = {h: conditional(res, h) for h in (120, 240)}
                    s = result[key]["summary"][240]["paired"]["DAR-EQ"]
                    print(f"[{time.time()-t0:6.0f}s] {key:18s} 20y DAR−EQ {s['d_irr_mean']:+.3f} pp  CI {s['ci95'][0]:+.3f}..{s['ci95'][1]:+.3f}", flush=True)
            json.dump(result, open(OUT / "grid.json", "w"), ensure_ascii=False, indent=1)
        elif stage == "sweep":
            result = {}
            for kap in (-0.6, -0.3, -0.15, 0.0, 0.15, 0.3, 0.6, 1.0):
                res = run_config(pool, "rw", "mid", 1.0, PATHS // 2, 5000, kappa=kap, arms=("DAR", "EQ", "ERC"))
                result[str(kap)] = {h: summarize(res)[h]["paired"] for h in res["H"]}
                s = result[str(kap)][240]["DAR-EQ"]
                print(f"[{time.time()-t0:6.0f}s] kappa {kap:+.2f}  20y DAR−EQ {s['d_irr_mean']:+.3f} pp  CI {s['ci95'][0]:+.3f}..{s['ci95'][1]:+.3f}", flush=True)
            json.dump(result, open(OUT / "sweep.json", "w"), ensure_ascii=False, indent=1)
    print("เสร็จใน", round(time.time() - t0), "วินาที")
