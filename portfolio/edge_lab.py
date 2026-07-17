# -*- coding: utf-8 -*-
"""Edge Lab — ออกแบบ edge candidate ใหม่แล้ววัดผ่าน harness Phase 0 (มติ 2026-07-18).

ที่มา: ด่าน A/B รอบแรก (2026-07-17) ตัดสินว่า score-tilt ≈ plain DCA → Phase 1/B2/B4
ถูก gate ไว้ ผู้ใช้เลือกทาง "ออกแบบ edge ใหม่แล้ววัดผ่าน harness" — ไฟล์นี้คือการวัดนั้น

ทุก candidate อยู่ใต้กติกาเดียวกับระบบ:
- per-ticker weight tilt **bounded 0.8–1.2** (แคบกว่า score-tilt เดิม ตามสเปก regime)
- point-in-time ล้วน (เห็นเฉพาะราคา "ก่อน" วันซื้อ ผ่าน harness เดิม)
- **ทุก ETF ยังถูกซื้อทุกเดือน** — ข้อมูลไม่พอ = tilt กลาง 1.0 ไม่ตัดทิ้ง ไม่เดา
- deterministic จากราคาเท่านั้น — ไม่มีเลขใหม่เข้าระบบจริงจนกว่าจะผ่านด่านทั้งสองช่วง

candidates:
- ``underwater``    : ลึกกว่า ATH ตัวเอง → เติมมากขึ้น (mean reversion ของดัชนีกว้าง)
- ``inverse_vol``   : ผันผวนต่ำกว่าเพื่อน → เติมมากขึ้น (risk-parity lean)
- ``rel_strength``  : โมเมนตัม 6 เดือนปรับความเสี่ยง (กลไกเดียวกับ B2)
- ``stretch``       : percentile ของ price/MA200 เทียบอดีตตัวเอง (B4 แบบไม่แตะ score)
- ``uw_x_ivol``     : underwater × inverse_vol (คูณกันแล้ว clamp กลับเข้า bound)

รัน: ``python -m portfolio.edge_lab`` (ใช้สอง window มาตรฐานของ ab_backtest)
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

import pandas as pd

from portfolio.ab_backtest import (
    WINDOWS,
    WeightsFn,
    _arm_metrics,
    fixed_weights_fn,
    score_tilt_weights_fn,
    simulate_dca_dynamic,
)
from portfolio.targets import get_target_weights

EDGE_TILT_MIN, EDGE_TILT_MAX = 0.8, 1.2

# ความยาวประวัติขั้นต่ำ (วันเทรด) ต่อ candidate — สั้นกว่านี้ = tilt กลาง 1.0
MIN_HISTORY_UNDERWATER = 252
MIN_HISTORY_VOL = 64
MIN_HISTORY_RS = 127
MIN_HISTORY_STRETCH = 452  # MA200 อุ่นเครื่อง 200 + แจกแจง ratio อีก ~1 ปี


def _clamp(value: float) -> float:
    return max(EDGE_TILT_MIN, min(EDGE_TILT_MAX, value))


def _closes(history: pd.DataFrame, ticker: str) -> pd.Series:
    if ticker not in history.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(history[ticker], errors="coerce").dropna()


def underwater_tilts(history: pd.DataFrame, tickers: list[str]) -> dict[str, float]:
    """ลึกกว่า ATH ตัวเองมาก → tilt สูงขึ้น: 1 + min(depth, 20%) → [1.0, 1.2]."""
    tilts: dict[str, float] = {}
    for ticker in tickers:
        closes = _closes(history, ticker)
        if len(closes) < MIN_HISTORY_UNDERWATER:
            tilts[ticker] = 1.0
            continue
        depth = 1.0 - float(closes.iloc[-1]) / float(closes.max())
        tilts[ticker] = _clamp(1.0 + min(max(depth, 0.0), 0.20))
    return tilts


def inverse_vol_tilts(history: pd.DataFrame, tickers: list[str]) -> dict[str, float]:
    """ผันผวน (63 วัน) ต่ำกว่า median ของพอร์ต → tilt สูงขึ้น: median_vol/vol → [0.8, 1.2]."""
    vols: dict[str, float] = {}
    for ticker in tickers:
        closes = _closes(history, ticker)
        if len(closes) < MIN_HISTORY_VOL:
            continue
        vol = float(closes.pct_change().dropna().tail(63).std())
        if vol > 0:
            vols[ticker] = vol
    if len(vols) < 2:
        return {t: 1.0 for t in tickers}
    median_vol = float(pd.Series(vols).median())
    return {
        t: _clamp(median_vol / vols[t]) if t in vols else 1.0
        for t in tickers
    }


def rel_strength_tilts(history: pd.DataFrame, tickers: list[str]) -> dict[str, float]:
    """โมเมนตัม 126 วันปรับความเสี่ยง (กลไก B2): z-score ข้ามตัว → 1 + 0.133z → [0.8, 1.2]."""
    scores: dict[str, float] = {}
    for ticker in tickers:
        closes = _closes(history, ticker)
        if len(closes) < MIN_HISTORY_RS:
            continue
        window = closes.tail(127)
        total_return = float(window.iloc[-1] / window.iloc[0] - 1.0)
        vol = float(window.pct_change().dropna().std())
        if vol > 0:
            scores[ticker] = total_return / vol
    if len(scores) < 3:
        return {t: 1.0 for t in tickers}
    series = pd.Series(scores)
    std = float(series.std(ddof=0))
    if std <= 0:
        return {t: 1.0 for t in tickers}
    z = (series - float(series.mean())) / std
    return {
        t: _clamp(1.0 + 0.1333 * float(z[t])) if t in z.index else 1.0
        for t in tickers
    }


def stretch_tilts(history: pd.DataFrame, tickers: list[str]) -> dict[str, float]:
    """percentile ของ price/MA200 เทียบแจกแจงอดีตตัวเอง: ถูก→1.2 แพง→0.8 (B4 แบบ tilt)."""
    tilts: dict[str, float] = {}
    for ticker in tickers:
        closes = _closes(history, ticker)
        if len(closes) < MIN_HISTORY_STRETCH:
            tilts[ticker] = 1.0
            continue
        ma200 = closes.rolling(200, min_periods=200).mean()
        ratio = (closes / ma200).dropna()
        trailing = ratio.tail(504)  # ~2 ปีของแจกแจง ratio
        if len(trailing) < 252:
            tilts[ticker] = 1.0
            continue
        percentile = float((trailing <= trailing.iloc[-1]).mean())
        tilts[ticker] = _clamp(1.2 - 0.4 * percentile)
    return tilts


def combo_uw_ivol_tilts(history: pd.DataFrame, tickers: list[str]) -> dict[str, float]:
    """underwater × inverse_vol แล้ว clamp กลับเข้า [0.8, 1.2]."""
    uw = underwater_tilts(history, tickers)
    iv = inverse_vol_tilts(history, tickers)
    return {t: _clamp(uw[t] * iv[t]) for t in tickers}


TiltFn = Callable[[pd.DataFrame, list[str]], dict[str, float]]

CANDIDATES: dict[str, TiltFn] = {
    "underwater": underwater_tilts,
    "inverse_vol": inverse_vol_tilts,
    "rel_strength": rel_strength_tilts,
    "stretch": stretch_tilts,
    "uw_x_ivol": combo_uw_ivol_tilts,
}


def edge_weights_fn(base_weights: Mapping[str, float], tilt_fn: TiltFn) -> WeightsFn:
    """แปลง tilt function เป็น weights_fn ของ harness (น้ำหนัก = เป้าหมาย × tilt)."""
    base = {str(t): float(w) for t, w in base_weights.items() if float(w) > 0}
    if not base:
        raise ValueError("base_weights ต้องมีน้ำหนักบวกอย่างน้อย 1 ตัว")
    tickers = list(base)

    def _fn(buy_date: pd.Timestamp, history: pd.DataFrame) -> dict[str, float]:
        tilts = tilt_fn(history, tickers)
        return {t: base[t] * float(tilts.get(t, 1.0)) for t in tickers}

    return _fn


def run_edge_lab(
    prices_by_window: Mapping[str, pd.DataFrame],
    monthly_amount: float = 10000.0,
    target_weights: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """รันทุก candidate เทียบ plain (และ score_tilt เดิมเป็น reference) ทุก window.

    ด่านผ่านต่อ candidate: ชนะ plain ทั้ง "มูลค่า" และ "Sharpe ≥" **ครบทั้งสอง window**
    (เกณฑ์เดียวกับด่าน Phase 0 เดิม)
    """
    base = dict(target_weights) if target_weights is not None else get_target_weights()
    base = {str(t).upper(): float(w) for t, w in base.items() if float(w) > 0}
    if not base:
        raise ValueError("ไม่มีน้ำหนักเป้าหมายให้ทดสอบ")

    results: dict[str, Any] = {"windows": {}, "gate": {}}
    for key, prices in prices_by_window.items():
        spec = WINDOWS.get(key)
        if spec is None:
            raise ValueError(f"ไม่รู้จัก window '{key}'")
        mapped = {spec["proxy_map"].get(t, t): w for t, w in base.items()}
        missing = sorted(t for t in mapped if t not in prices.columns)
        if missing:
            raise ValueError(f"window '{key}' ขาดคอลัมน์ราคา: {missing}")
        start: str = spec["start"]
        arm_prices = prices[list(mapped.keys())]

        arms: dict[str, dict[str, Any]] = {}
        arms["plain"] = _arm_metrics(
            simulate_dca_dynamic(arm_prices, monthly_amount, fixed_weights_fn(mapped), start=start)
        )
        arms["score_tilt_ref"] = _arm_metrics(
            simulate_dca_dynamic(arm_prices, monthly_amount, score_tilt_weights_fn(mapped), start=start)
        )
        for name, tilt_fn in CANDIDATES.items():
            arms[name] = _arm_metrics(
                simulate_dca_dynamic(
                    arm_prices, monthly_amount, edge_weights_fn(mapped, tilt_fn), start=start
                )
            )

        verdicts: dict[str, dict[str, bool]] = {}
        plain = arms["plain"]
        for name in CANDIDATES:
            candidate = arms[name]
            by_value = candidate["final_value"] > plain["final_value"]
            by_sharpe = (
                candidate["sharpe"] is not None
                and plain["sharpe"] is not None
                and candidate["sharpe"] >= plain["sharpe"]
            )
            verdicts[name] = {
                "by_value": by_value,
                "by_sharpe": by_sharpe,
                "overall": by_value and by_sharpe,
            }
        results["windows"][key] = {
            "label": spec["label"],
            "start": start,
            "arms": arms,
            "verdicts": verdicts,
        }

    for name in CANDIDATES:
        results["gate"][name] = all(
            results["windows"][key]["verdicts"][name]["overall"]
            for key in results["windows"]
        )
    return results


def _print_report(results: dict[str, Any]) -> None:
    for key, window in results["windows"].items():
        print(f"\n=== {window['label']} (เริ่มซื้อ {window['start']}) ===")
        plain = window["arms"]["plain"]
        print(
            f"  plain: จบ {plain['final_value']:,.0f} | CAGR {plain['cagr_pct']}% "
            f"| Sharpe {plain['sharpe']} | DD {plain['max_drawdown_pct']}%"
        )
        for name, metrics in window["arms"].items():
            if name == "plain":
                continue
            verdict = window["verdicts"].get(name)
            flag = ""
            if verdict is not None:
                flag = " ✓ชนะ" if verdict["overall"] else " ✗"
            diff_pct = (metrics["final_value"] / plain["final_value"] - 1.0) * 100.0
            print(
                f"  {name:>15}: จบ {metrics['final_value']:,.0f} ({diff_pct:+.2f}% vs plain) "
                f"| CAGR {metrics['cagr_pct']}% | Sharpe {metrics['sharpe']} "
                f"| DD {metrics['max_drawdown_pct']}%{flag}"
            )
    print("\n=== ด่านรวม (ต้องชนะทั้งมูลค่า+Sharpe ครบทั้งสองช่วง) ===")
    for name, passed in results["gate"].items():
        print(f"  {name:>15}: {'✅ ผ่านด่าน' if passed else '⛔ ไม่ผ่าน'}")


if __name__ == "__main__":
    from portfolio.ab_backtest import _fetch_default_windows

    _print_report(run_edge_lab(_fetch_default_windows()))
