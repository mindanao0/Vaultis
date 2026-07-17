# -*- coding: utf-8 -*-
"""Backtest A/B harness — ด่านกั้นของ Phase 0 (Roadmap: "พิสูจน์ก่อนสร้าง").

เทียบ 3 แขนบนราคาชุดเดียวกัน:
- ``plain``    : DCA น้ำหนักเป้าหมายคงที่ (``get_target_weights``)
- ``tilt``     : DCA ที่เอียงน้ำหนักตามคะแนน ณ เวลานั้น (point-in-time, ห้าม look-ahead)
- ``voo_only`` : DCA ลง VOO ตัวเดียว (benchmark)

ต่างจาก ``portfolio.dca.simulate_dca`` ตรงที่น้ำหนักคำนวณใหม่ทุกเดือนผ่าน
``weights_fn(buy_date, history)`` โดย ``history`` คือราคารายวัน **ก่อน** วันซื้อเท่านั้น
เพื่อรองรับ tilt ที่แปรตามเวลา (สเปก Phase 0 ข้อ 1)

สองช่วงทดสอบตามบันทึกการตัดสินใจข้อ 3 (2026-07-16):
- ``proxy`` : VOO/SCHD/QQQ/XLV/GLD ตั้งแต่ 2011-10 (QQQ แทน QQQM, GLD แทน GLDM)
- ``real``  : VOO/SCHD/QQQM/XLV/GLDM ตั้งแต่ 2020-11 (ข้อมูลจริงล้วน)

ข้อจำกัดที่ตั้งใจ (บันทึกไว้เพื่อการตีความผล):
- **v1 ไม่คิดค่าธรรมเนียมทั้งสองแขน** — fee 0.15% คิดเป็นสัดส่วนของงบรายเดือน
  ที่เท่ากันทุกแขน จึงหักล้างกันเองในการเปรียบเทียบ A/B (ไม่กระทบผลแพ้ชนะ)
- **tilt ใช้คะแนนส่วนที่คำนวณจากราคาเท่านั้น** (``div_yield=None`` →
  ``score_from_prices`` ตัดคะแนนปันผลออกจาก max ให้เอง) เพราะ dividend yield
  ย้อนหลังแบบ point-in-time ต้องยิง network ซึ่ง harness ห้ามทำ
- Sharpe/vol/max drawdown คิดจาก **time-weighted monthly returns** ของพอร์ต
  (แยกผลของเงินเติมรายเดือนออกแล้ว) ด้วย risk-free = 0 — ใช้เทียบระหว่างแขน
  ในช่วงเดียวกัน ไม่ใช่เทียบข้ามช่วงเวลา
"""

from __future__ import annotations

import math
from typing import Any, Callable, Mapping

import pandas as pd

# import ชื่อ private ข้าม module โดยตั้งใจ — เพื่อ single-source ของ threshold
# (Roadmap invariant: ห้าม re-implement คะแนน/ตัวคูณที่อื่น)
from analysis.financial_model import _score_tilt, score_from_prices
from portfolio.targets import get_target_weights

WeightsFn = Callable[[pd.Timestamp, pd.DataFrame], Mapping[str, float]]

# ETF จริงที่เพิ่งเกิด → ใช้ proxy ที่ track ดัชนี/สินทรัพย์เดียวกันแต่ประวัติยาวกว่า
PROXY_MAP: dict[str, str] = {"QQQM": "QQQ", "GLDM": "GLD"}

WINDOWS: dict[str, dict[str, Any]] = {
    "proxy": {
        "start": "2011-10-01",  # จุดเกิด SCHD — เพดานย้อนหลังของพอร์ต 5 ตัว
        "label": "proxy (QQQ/GLD แทน QQQM/GLDM)",
        "proxy_map": PROXY_MAP,
    },
    "real": {
        "start": "2020-11-01",  # เดือนแรกหลัง QQQM (2020-10) ครบทุกตัวจริง
        "label": "ข้อมูลจริงล้วน",
        "proxy_map": {},
    },
}

MONTHS_PER_YEAR = 12


def fixed_weights_fn(weights: Mapping[str, float]) -> WeightsFn:
    """weights_fn แบบน้ำหนักคงที่ทุกเดือน (แขน baseline / benchmark)."""
    frozen = {str(t): float(w) for t, w in weights.items() if float(w) > 0}
    if not frozen:
        raise ValueError("weights ต้องมีน้ำหนักบวกอย่างน้อย 1 ตัว")

    def _fn(buy_date: pd.Timestamp, history: pd.DataFrame) -> dict[str, float]:
        return dict(frozen)

    return _fn


def score_tilt_weights_fn(base_weights: Mapping[str, float]) -> WeightsFn:
    """weights_fn ที่เอียงน้ำหนักตามคะแนน ณ วันซื้อ — pipeline เดียวกับระบบจริง.

    ต่อ ticker: ``score_from_prices(history)`` → ``_score_tilt`` (0.6–1.4) →
    ``น้ำหนัก = เป้าหมาย × tilt`` (normalize ใน ``simulate_dca_dynamic``)
    — สูตรเดียวกับ ``calculate_allocation`` แต่ไม่ปัดหลักร้อย THB

    นโยบาย DCA (ห้ามละเมิด): ticker ที่ประวัติ ณ เดือนนั้นไม่พอ (<200 วันเทรด →
    ``score_from_prices`` raise) ได้ **tilt กลาง 1.0 และยังถูกซื้อตามเป้าหมาย**
    ไม่ตัดทิ้ง ไม่เดาคะแนน; เดือน/ตัวที่เป็นกลางถูกบันทึกใน ``_fn.neutral_log``
    """
    base = {str(t): float(w) for t, w in base_weights.items() if float(w) > 0}
    if not base:
        raise ValueError("base_weights ต้องมีน้ำหนักบวกอย่างน้อย 1 ตัว")

    def _fn(buy_date: pd.Timestamp, history: pd.DataFrame) -> dict[str, float]:
        weights: dict[str, float] = {}
        neutral: list[str] = []
        for ticker, target in base.items():
            tilt = 1.0
            closes = history[ticker] if ticker in history.columns else pd.Series(dtype=float)
            try:
                score = score_from_prices(ticker, closes, div_yield=None)
                tilt = _score_tilt(float(score["total_pct"]))
            except ValueError:
                neutral.append(ticker)  # ข้อมูลไม่พอ → กลาง (ยังซื้อตามเป้า)
            weights[ticker] = target * tilt
        if neutral:
            _fn.neutral_log.append((buy_date, tuple(neutral)))
        return weights

    _fn.neutral_log = []  # type: ignore[attr-defined]
    return _fn


def simulate_dca_dynamic(
    prices: pd.DataFrame,
    monthly_amount: float,
    weights_fn: WeightsFn,
    start: str | pd.Timestamp | None = None,
) -> dict[str, Any]:
    """จำลอง DCA รายเดือนแบบน้ำหนักแปรตามเวลา (ซื้อวันเทรดแรกของเดือน).

    ``prices`` ควรย้อนไกลกว่า ``start`` พอให้ indicator อุ่นเครื่อง (MA200 ต้องการ
    ~200 วันเทรด) — เดือนก่อน ``start`` ใช้เป็น history เท่านั้น ไม่ถูกซื้อ

    ทุกเดือน ``weights_fn(buy_date, history)`` เห็นเฉพาะราคา **ก่อน** วันซื้อ
    (ห้าม look-ahead) แล้วน้ำหนักที่คืนมาถูก normalize ก่อนแบ่งเงินซื้อ

    v1 ไม่คิดค่าธรรมเนียม — ดูเหตุผลใน docstring ระดับ module
    """
    if monthly_amount <= 0:
        raise ValueError("monthly_amount ต้องมากกว่า 0")
    frame = prices.sort_index().dropna(how="all")
    if frame.empty:
        raise ValueError("ไม่มีข้อมูลราคาให้จำลอง DCA")
    start_ts = pd.Timestamp(start) if start is not None else None

    shares: dict[str, float] = {str(t): 0.0 for t in frame.columns}
    total_invested = 0.0
    records: list[dict[str, Any]] = []
    twr_dates: list[pd.Timestamp] = []
    twr_values: list[float] = []
    prev_value_after: float | None = None

    periods = frame.index.to_period("M")
    for period in periods.unique():
        month_rows = frame[periods == period].dropna(how="any")
        if month_rows.empty:
            continue  # เดือนที่บาง ticker ยังไม่มีราคา — ข้ามทั้งเดือน (นิยามเดียวกับ simulate_dca)
        buy_date = month_rows.index[0]
        row = month_rows.iloc[0]
        if start_ts is not None and buy_date < start_ts:
            continue  # ช่วงอุ่นเครื่อง indicator — ไม่ซื้อ

        value_before = float(sum(shares[t] * float(row[t]) for t in frame.columns))
        if prev_value_after is not None and prev_value_after > 0:
            twr_dates.append(buy_date)
            twr_values.append(value_before / prev_value_after - 1.0)

        history = frame.loc[frame.index < buy_date]
        raw = dict(weights_fn(buy_date, history) or {})
        weights = {
            str(t): float(w)
            for t, w in raw.items()
            if str(t) in frame.columns and float(w) > 0
        }
        total_weight = sum(weights.values())
        if total_weight <= 0:
            # นโยบาย DCA: ทุกเดือนต้องซื้อ — น้ำหนักใช้ไม่ได้ = บั๊กของ weights_fn ให้ล้มดัง ๆ
            raise ValueError(f"weights_fn คืนน้ำหนักใช้ไม่ได้ที่ {buy_date.date()}: {raw}")

        for ticker, weight in weights.items():
            allocation = monthly_amount * weight / total_weight
            shares[ticker] += allocation / float(row[ticker])
        total_invested += monthly_amount

        value_after = float(sum(shares[t] * float(row[t]) for t in frame.columns))
        prev_value_after = value_after
        records.append(
            {
                "Date": buy_date,
                "Total Invested": total_invested,
                "Portfolio Value": value_after,
            }
        )

    if not records:
        raise ValueError("ไม่มีเดือนที่ซื้อได้เลยในช่วงที่ระบุ (ข้อมูลไม่พอหรือ start ช้าเกินไป)")

    history_df = pd.DataFrame(records).set_index("Date")
    final_value = float(history_df["Portfolio Value"].iloc[-1])
    pl_pct = (final_value - total_invested) / total_invested * 100.0 if total_invested else 0.0

    return {
        "total_invested": float(total_invested),
        "final_value": final_value,
        "pl_pct": float(pl_pct),
        "n_months": len(records),
        "shares": {t: float(s) for t, s in shares.items()},
        "history": history_df,
        # time-weighted monthly returns (ตัดผลเงินเติมออกแล้ว) — ฐานของ vol/Sharpe/DD
        "monthly_returns": pd.Series(twr_values, index=pd.Index(twr_dates, name="Date")),
        "start_used": history_df.index[0],
        "end_used": history_df.index[-1],
    }


def _arm_metrics(sim: dict[str, Any]) -> dict[str, Any]:
    """สรุป metrics ต่อแขนจากผล ``simulate_dca_dynamic`` (risk-free = 0)."""
    returns = sim["monthly_returns"]
    metrics: dict[str, Any] = {
        "n_months": sim["n_months"],
        "total_invested": round(sim["total_invested"], 2),
        "final_value": round(sim["final_value"], 2),
        "pl_pct": round(sim["pl_pct"], 2),
        "cagr_pct": None,
        "vol_pct": None,
        "sharpe": None,
        "max_drawdown_pct": None,
    }
    if len(returns) == 0:
        return metrics  # เดือนเดียว — ไม่มีอนุกรมผลตอบแทนให้สรุป (ไม่เดาเลขแทน)

    growth_index = (1.0 + returns).cumprod()
    total_growth = float(growth_index.iloc[-1])
    if total_growth > 0:
        cagr = total_growth ** (MONTHS_PER_YEAR / len(returns)) - 1.0
        metrics["cagr_pct"] = round(cagr * 100.0, 2)

    std = float(returns.std(ddof=1)) if len(returns) > 1 else float("nan")
    if not math.isnan(std):
        metrics["vol_pct"] = round(std * math.sqrt(MONTHS_PER_YEAR) * 100.0, 2)
        if std > 0:
            sharpe = float(returns.mean()) / std * math.sqrt(MONTHS_PER_YEAR)
            metrics["sharpe"] = round(sharpe, 2)

    curve = pd.concat([pd.Series([1.0]), growth_index.reset_index(drop=True)])
    drawdown = curve / curve.cummax() - 1.0
    metrics["max_drawdown_pct"] = round(float(drawdown.min()) * 100.0, 2)
    return metrics


def _fmt(value: Any, suffix: str = "") -> str:
    return "-" if value is None else f"{value:,.2f}{suffix}"


def _summary_th(window_label: str, start: str, arms: dict[str, Any], verdict: dict[str, bool]) -> str:
    plain, tilt = arms["plain"], arms["tilt"]
    voo = arms.get("voo_only")
    parts = [
        f"ช่วง {window_label} (เริ่มซื้อ {start}, {plain['n_months']} เดือน, "
        f"เงินลงทุนรวม {plain['total_invested']:,.0f}):",
        f"plain จบที่ {plain['final_value']:,.0f} "
        f"(CAGR {_fmt(plain['cagr_pct'], '%')}, Sharpe {_fmt(plain['sharpe'])}, "
        f"DD {_fmt(plain['max_drawdown_pct'], '%')})",
        f"| tilt จบที่ {tilt['final_value']:,.0f} "
        f"(CAGR {_fmt(tilt['cagr_pct'], '%')}, Sharpe {_fmt(tilt['sharpe'])}, "
        f"DD {_fmt(tilt['max_drawdown_pct'], '%')}, "
        f"เดือนที่มีตัวเป็นกลางเพราะข้อมูลไม่พอ {tilt.get('months_neutral', 0)})",
    ]
    if voo:
        parts.append(
            f"| VOO อย่างเดียวจบที่ {voo['final_value']:,.0f} "
            f"(CAGR {_fmt(voo['cagr_pct'], '%')}, Sharpe {_fmt(voo['sharpe'])})"
        )
    win_value = "ชนะ" if verdict["by_value"] else "ไม่ชนะ"
    win_sharpe = "ชนะ" if verdict["by_sharpe"] else "ไม่ชนะ"
    overall = "ผ่านด่าน" if verdict["overall"] else "ไม่ผ่านด่าน — ทบทวน edge ก่อนลงมือ"
    parts.append(f"→ tilt เทียบ plain: มูลค่า {win_value}, Sharpe {win_sharpe} ⇒ {overall}")
    return " ".join(parts)


def run_ab_backtest(
    prices_by_window: Mapping[str, pd.DataFrame],
    monthly_amount: float = 10000.0,
    target_weights: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """รัน A/B ทุก window ที่ส่งมา (คีย์ต้องอยู่ใน ``WINDOWS``) แล้วคืนผลรวม.

    ``target_weights`` ไม่ระบุ → อ่านจาก ``get_target_weights()`` (config.json);
    คีย์เป็น ticker จริง (QQQM/GLDM) เสมอ — ช่วง proxy ถูก map ด้วย ``PROXY_MAP`` ให้เอง
    """
    base = dict(target_weights) if target_weights is not None else get_target_weights()
    base = {str(t).upper(): float(w) for t, w in base.items() if float(w) > 0}
    if not base:
        raise ValueError("ไม่มีน้ำหนักเป้าหมายให้ทดสอบ")

    results: dict[str, Any] = {}
    for key, prices in prices_by_window.items():
        spec = WINDOWS.get(key)
        if spec is None:
            raise ValueError(f"ไม่รู้จัก window '{key}' (ที่มี: {sorted(WINDOWS)})")
        proxy_map: dict[str, str] = spec["proxy_map"]
        mapped = {proxy_map.get(t, t): w for t, w in base.items()}
        missing = sorted(t for t in mapped if t not in prices.columns)
        if missing:
            raise ValueError(f"window '{key}' ขาดคอลัมน์ราคา: {missing}")
        start: str = spec["start"]
        arm_prices = prices[list(mapped.keys())]

        plain_sim = simulate_dca_dynamic(arm_prices, monthly_amount, fixed_weights_fn(mapped), start=start)
        tilt_fn = score_tilt_weights_fn(mapped)
        tilt_sim = simulate_dca_dynamic(arm_prices, monthly_amount, tilt_fn, start=start)

        arms: dict[str, Any] = {"plain": _arm_metrics(plain_sim), "tilt": _arm_metrics(tilt_sim)}
        neutral_log = list(getattr(tilt_fn, "neutral_log", []))
        arms["tilt"]["months_neutral"] = len(neutral_log)
        neutral_counts: dict[str, int] = {}
        for _, tickers in neutral_log:
            for t in tickers:
                neutral_counts[t] = neutral_counts.get(t, 0) + 1
        arms["tilt"]["neutral_by_ticker"] = neutral_counts

        if "VOO" in prices.columns:
            voo_sim = simulate_dca_dynamic(
                prices[["VOO"]], monthly_amount, fixed_weights_fn({"VOO": 1.0}), start=start
            )
            arms["voo_only"] = _arm_metrics(voo_sim)

        by_value = arms["tilt"]["final_value"] > arms["plain"]["final_value"]
        sharpe_t, sharpe_p = arms["tilt"]["sharpe"], arms["plain"]["sharpe"]
        by_sharpe = sharpe_t is not None and sharpe_p is not None and sharpe_t >= sharpe_p
        verdict = {"by_value": by_value, "by_sharpe": by_sharpe, "overall": by_value and by_sharpe}

        results[key] = {
            "window": key,
            "window_label": spec["label"],
            "start": start,
            "monthly_amount": float(monthly_amount),
            "weights_base": mapped,
            "arms": arms,
            "tilt_beats_plain": verdict,
            "summary_th": _summary_th(spec["label"], start, arms, verdict),
        }
    return results


def _fetch_default_windows() -> dict[str, pd.DataFrame]:
    """ดึงราคาจริงสำหรับสองช่วงมาตรฐาน (fail-loud ตาม data/fetcher)."""
    from data.fetcher import fetch_adjusted_close_data

    tickers = sorted({"VOO", "SCHD", "QQQ", "QQQM", "XLV", "GLD", "GLDM"})
    prices = fetch_adjusted_close_data(tickers=tickers, years=16)
    return {
        "proxy": prices[["VOO", "SCHD", "QQQ", "XLV", "GLD"]],
        "real": prices[["VOO", "SCHD", "QQQM", "XLV", "GLDM"]],
    }


if __name__ == "__main__":
    all_results = run_ab_backtest(_fetch_default_windows())
    for window_key in ("proxy", "real"):
        result = all_results.get(window_key)
        if not result:
            continue
        print(f"\n=== {result['window_label']} ===")
        print(result["summary_th"])
        for arm_name, metrics in result["arms"].items():
            if isinstance(metrics, dict):
                print(f"  [{arm_name}] {metrics}")
