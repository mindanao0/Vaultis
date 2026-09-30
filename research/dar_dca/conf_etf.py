"""CONFIRMATION (descriptive) on the user's ETF universe through the project's own harness.

Windows (same as portfolio/ab_backtest.WINDOWS):
  proxy: VOO SCHD QQQ XLV GLD from 2011-10     real: VOO SCHD QQQM XLV GLDM from 2020-11
Signal history (month-end, total return) is extended with same-index siblings before inception:
  VOO<-SPY, QQQM<-QQQ, GLDM<-GLD (level-rescaled at the join). SCHD has no sibling -> neutral until
  it has 181 month-ends of its own.
Arms: DAR (locked) | equal 1/N | old preset 35/25/20/10/10 | old preset + score tilt |
      ERC + score tilt (current default plan, point-in-time) | VOO only.
Stats: project _arm_metrics + paired_diff_stats of each arm vs equal (and DAR vs current plan).
"""
from __future__ import annotations

import json
import os
import warnings

import numpy as np
import pandas as pd

import dar_formula as F
from analysis.financial_model import _score_tilt, score_from_prices
from analysis.risk import paired_diff_stats
from portfolio.ab_backtest import _arm_metrics, fixed_weights_fn, score_tilt_weights_fn, simulate_dca_dynamic

warnings.filterwarnings("ignore")

SIBLING = {"VOO": "SPY", "QQQM": "QQQ", "GLDM": "GLD"}
PRESET = {"VOO": 0.35, "SCHD": 0.25, "QQQM": 0.20, "XLV": 0.10, "GLDM": 0.10}


def month_end_history(adj: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    me = adj.resample("ME").last()
    out = {}
    for t in tickers:
        own = me[t].dropna()
        sib = SIBLING.get(t)
        if sib and sib in me.columns:
            s = me[sib].dropna()
            first = own.index[0]
            if first in s.index and s.index[0] < first:
                scale = own.loc[first] / s.loc[first]
                ext = (s.loc[: first] * scale).iloc[:-1]
                own = pd.concat([ext, own])
        out[t] = own
    return pd.DataFrame(out)


def daily_spliced(adj: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    out = {}
    for t in tickers:
        own = adj[t].dropna()
        sib = SIBLING.get(t)
        if sib and sib in adj.columns:
            s = adj[sib].dropna()
            first = own.index[0]
            if first in s.index and s.index[0] < first:
                ext = (s.loc[:first] * (own.loc[first] / s.loc[first])).iloc[:-1]
                own = pd.concat([ext, own])
        out[t] = own
    return pd.DataFrame(out)


def dar_weights_fn(hist_me: pd.DataFrame, tickers: list[str]):
    log = []

    def fn(buy_date: pd.Timestamp, history: pd.DataFrame):
        cutoff = pd.Timestamp(buy_date).to_period("M").start_time
        h = hist_me.loc[hist_me.index < cutoff, tickers]
        w = F.dar_weights(h)
        sig = F.dar_signal(h)
        log.append({"date": str(pd.Timestamp(buy_date).date()), **{t: float(w[t]) for t in tickers},
                    "neutral": [t for t in tickers if np.isnan(sig[t])]})
        return w.to_dict()

    fn.log = log
    return fn


def erc_tilt_weights_fn(tickers: list[str], spliced: pd.DataFrame):
    """Current default plan: ERC base (on sibling-spliced daily history, like the live 5y fetch would
    see) x score tilt. Months where ERC cannot be computed (a fund with < 1y of common history and no
    sibling, e.g. SCHD in 2011-12) fall back to equal base — counted in fn.fallback, descriptive arm only."""
    from portfolio.risk_weights import erc_from_prices

    fallback = []

    def fn(buy_date, history):
        h = spliced.loc[spliced.index < pd.Timestamp(buy_date), tickers].tail(252 * 5 + 5)
        try:
            base = erc_from_prices(h, tickers)["weights"]
        except ValueError:
            fallback.append(str(pd.Timestamp(buy_date).date()))
            base = {t: 1.0 / len(tickers) for t in tickers}
        out = {}
        for t in tickers:
            tilt = 1.0
            try:
                tilt = _score_tilt(float(score_from_prices(t, history[t], div_yield=None)["total_pct"]))
            except ValueError:
                pass
            out[t] = base[t] * tilt
        return out

    fn.fallback = fallback
    return fn


def run_window(name: str, traded: list[str], start: str, adj: pd.DataFrame, hist_me: pd.DataFrame) -> dict:
    prices = adj[traded].dropna(how="all")
    preset = {t: PRESET.get({"QQQ": "QQQM", "GLD": "GLDM"}.get(t, t), 0.0) for t in traded}
    dfn = dar_weights_fn(hist_me, traded)
    arms = {
        "DAR": dfn,
        "equal": fixed_weights_fn({t: 1.0 for t in traded}),
        "old_preset": fixed_weights_fn(preset),
        "old_preset_tilt": score_tilt_weights_fn(preset),
        "erc_tilt_current": erc_tilt_weights_fn(traded, daily_spliced(adj, traded)),
        "voo_only": fixed_weights_fn({"VOO": 1.0}),
    }
    sims, metrics = {}, {}
    for a, fn in arms.items():
        try:
            sims[a] = simulate_dca_dynamic(prices, 1000.0, fn, start=start)
            metrics[a] = _arm_metrics(sims[a])
        except Exception as exc:  # noqa: BLE001 - record per-arm failure, never hide it
            metrics[a] = {"error": repr(exc)}
    paired = {}
    for a in sims:
        if a == "equal":
            continue
        try:
            paired[f"{a}_vs_equal"] = paired_diff_stats(sims[a]["monthly_returns"], sims["equal"]["monthly_returns"], label_a=a, label_b="equal")
        except ValueError as exc:
            paired[f"{a}_vs_equal"] = {"error": str(exc)}
    if "erc_tilt_current" in sims:
        paired["DAR_vs_current"] = paired_diff_stats(sims["DAR"]["monthly_returns"], sims["erc_tilt_current"]["monthly_returns"], label_a="DAR", label_b="current")
    return {"window": name, "traded": traded, "start": start, "metrics": metrics, "paired": paired,
            "erc_fallback_months": arms["erc_tilt_current"].fallback,
            "dar_weights_log": dfn.log,
            "value_paths": {a: [[str(d.date()), float(v)] for d, v in s["history"]["Portfolio Value"].items()] for a, s in sims.items()},
            "invested_path": [[str(d.date()), float(v)] for d, v in sims["equal"]["history"]["Total Invested"].items()]}


def main() -> None:
    etf = pd.read_pickle("/scratch/cache/etf_daily.pkl")
    adj = etf["adj"]
    out = {}
    tick_all = ["VOO", "SCHD", "QQQ", "QQQM", "XLV", "GLD", "GLDM"]
    hist_me = month_end_history(adj, tick_all)
    out["proxy"] = run_window("proxy", ["VOO", "SCHD", "QQQ", "XLV", "GLD"], "2011-10-01", adj, hist_me)
    out["real"] = run_window("real", ["VOO", "SCHD", "QQQM", "XLV", "GLDM"], "2020-11-01", adj, hist_me)
    os.makedirs("/scratch/results", exist_ok=True)
    with open("/scratch/results/conf_etf.json", "w") as fh:
        json.dump(out, fh, default=str)
    for w, r in out.items():
        print(f"\n== {w} window from {r['start']} ==")
        for a, m in r["metrics"].items():
            print(f"  {a:18s} {m}")
        for k, p in r["paired"].items():
            if "error" in p:
                print(f"  {k}: error {p['error']}")
            else:
                print(f"  {k}: {p['diff_annual_pct']:+.2f}%/y CI [{p['ci95_low_pct']:+.2f},{p['ci95_high_pct']:+.2f}] distinguishable={p['distinguishable_from_zero']} MDE {p['mde_annual_pct']:.2f}")


if __name__ == "__main__":
    main()
