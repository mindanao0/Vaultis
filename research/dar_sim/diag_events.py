# -*- coding: utf-8 -*-
"""วินิจฉัย (สำรวจ — ไม่อยู่ในสเปกที่ล็อก): แต่ละสูตรได้/เสียเท่าไรเมื่อเปิดเหตุการณ์ใหญ่ทีละแบบ (โลก rw, drift mid).

    python diag_events.py <scratch_dir>  → results/new_diag_events.json
"""
from __future__ import annotations

import json
import multiprocessing as mp
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import sim  # noqa: E402

scratch = Path(sys.argv[1])
PANEL = pd.read_pickle(scratch / "panel.pkl")
ARMS = ("DAR", "EQ", "ERC", "CF_ERC", "CF_EQ", "BLEND")


def _task(cfg):
    r = sim.run_chunk(PANEL, cfg)
    return {h: {a: {k: np.asarray(x[k], dtype=np.float32) for k in ("irr", "maxdd")} for a, x in r["H"][h]["arms"].items()} for h in cfg.horizons}


if __name__ == "__main__":
    names = [e["name"] for e in sim.build_events(PANEL["events_measured"])]
    out = {}
    with mp.get_context("fork").Pool(7) as pool:
        for i, nm in enumerate([None] + names):
            only = () if nm is None else (nm,)
            cfgs = [sim.Config(world="rw", drift="mid", P=3000, T=240, seed=500_000 + 100 * i + j, arms=ARMS,
                               horizons=(120, 240), events_only=only) for j in range(8)]
            parts = pool.map(_task, cfgs)
            key = "ไม่มีเหตุการณ์" if nm is None else nm
            out[key] = {}
            for h in (120, 240):
                cat = {a: {k: np.concatenate([p[h][a][k] for p in parts]).astype(np.float64) for k in ("irr", "maxdd")} for a in ARMS}
                out[key][str(h)] = {}
                for a in ARMS:
                    for ref in ("EQ", "ERC"):
                        if a == ref:
                            continue
                        d = (cat[a]["irr"] - cat[ref]["irr"]) * 100
                        out[key][str(h)][f"{a}-{ref}"] = {"d_irr": float(d.mean()), "se": float(d.std(ddof=1) / np.sqrt(len(d))),
                                                          "d_dd_med": float((np.median(cat[a]["maxdd"]) - np.median(cat[ref]["maxdd"])) * 100)}
            print("เสร็จ", key, flush=True)
    json.dump(out, open(Path(__file__).parent / "results" / "new_diag_events.json", "w"), ensure_ascii=False, indent=1)
