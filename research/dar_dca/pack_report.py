"""Pack confirmation results into one compact JSON for the report page."""
import json

R = "/scratch/results"
out = {"pools": {}}
for k in ["P1_mixed", "S1_ind17", "S2_ind49", "S3_style", "S4_mixed_gold"]:
    r = json.load(open(f"{R}/conf_{k}.json"))
    pool = {"starts": [s[:7] for s in r["rolling_starts"]]}
    for a in ("DAR", "AMP_only", "MOM_12_1"):
        ro, co = r["rolling_20y"][a], r["continuous"][a]
        pool[a] = {
            "roll_mean": round(ro["mean"], 2), "roll_median": round(ro["median"], 2), "roll_win": round(ro["win"], 1),
            "roll_p10": round(ro["p10"], 2), "roll_p90": round(ro["p90"], 2),
            "cont_ann": round(co["ann_excess_pct"], 3), "cont_lo": round(co["ci95_low"], 3), "cont_hi": round(co["ci95_high"], 3),
            "sub1": round(co["sub_1975_2000_ann"], 3), "sub2": round(co["sub_2001_2026_ann"], 3),
            "term_mean": round(co["terminal_mean"], 2), "term_win": round(co["terminal_win"], 1),
        }
        if a == "DAR":
            pool[a]["by_start"] = [round(v, 2) for v in ro["by_start_mean"]]
            path = co["cum_excess_path"]
            pool[a]["cum_path_q"] = [round(path[i], 3) for i in range(0, len(path), 3)]  # quarterly
    pool["xcheck"] = r["xcheck_max_abs_diff"]
    out["pools"][k] = pool
e = json.load(open(f"{R}/conf_etf.json"))
out["etf"] = {}
for w, r in e.items():
    out["etf"][w] = {
        "start": r["start"], "traded": r["traded"],
        "metrics": {a: {k: m.get(k) for k in ("final_value", "total_invested", "cagr_pct", "vol_pct", "max_drawdown_pct")} for a, m in r["metrics"].items()},
        "paired": {k: ({kk: p.get(kk) for kk in ("diff_annual_pct", "ci95_low_pct", "ci95_high_pct", "mde_annual_pct", "distinguishable_from_zero")} if "error" not in p else p) for k, p in r["paired"].items()},
        "erc_fallback_months": len(r.get("erc_fallback_months", [])),
    }
out["current_plan"] = json.load(open(f"{R}/current_plan.json"))
json.dump(out, open(f"{R}/report_data.json", "w"))
print(json.dumps({k: {a: out['pools'][k][a]['roll_mean'] for a in ('DAR', 'AMP_only', 'MOM_12_1')} for k in out['pools']}, indent=1))
print(len(json.dumps(out)), "bytes")
