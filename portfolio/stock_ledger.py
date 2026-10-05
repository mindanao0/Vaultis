# -*- coding: utf-8 -*-
"""สมุดของ **พอร์ตกระดาษ STOCK-DCA** (เลือกหุ้นรายตัว) — forward test ที่แยกจากพอร์ตหลัก DAR และ SELECT โดยตั้งใจ.

path อ่านจาก ``VAULTIS_STOCK_LEDGER_PATH`` **ครั้งเดียวตอน import** (เทสต์ monkeypatch ``STOCK_LEDGER_PATH`` — ตาข่ายใน tests/conftest.py ผูกกับชื่อนี้)
กติกาที่ล็อกใน ``research/stock_pick/PREREG.md`` และบังคับที่นี่:
  * บันทึกได้เดือนละครั้ง และ **เฉพาะเดือนปัจจุบัน** (ห้ามย้อนบันทึก — ไม่งั้นก็เลือกเดือนที่ดูดีได้)
  * ทุกแถวพกจักรวาลของเดือนนั้น (ขาเงาแบ่งเท่ากันทั้งจักรวาล)
  * ``compare()`` เทียบ 3 แขนด้วยเงินก้อนเดียวกันวันเดียวกัน และตอบ "ยังตอบไม่ได้" จนกว่าจะครบ ``MIN_COHORTS`` เดือน
  * หุ้นที่ราคาหายภายหลัง = ประเมินไม่ได้ ⇒ ปฏิเสธตัดสิน (ไม่ใช่ศูนย์ ไม่ใช่ข้ามไป)
"""
from __future__ import annotations

import math
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from analysis import stock_pick
from utils.fx import MAX_RATE, MIN_RATE

REPO_ROOT = Path(__file__).resolve().parent.parent


def _ledger_path_from_env() -> Path:
    raw = os.environ.get("VAULTIS_STOCK_LEDGER_PATH", "").strip()
    return Path(raw) if raw else REPO_ROOT / "portfolio" / "data" / "stock_transactions.csv"


STOCK_LEDGER_PATH: Path = _ledger_path_from_env()

COLUMNS = ["tx_id", "date", "plan_month", "ticker", "amount_thb", "fx_rate", "price_usd", "units", "universe", "rule", "recorded_at"]


class StockLedgerError(ValueError):
    """ข้อมูลที่จะบันทึก/ที่อ่านได้ผิดรูป หรือผิดกติกาที่ล็อก — ห้ามเดาแทน ห้ามข้ามเงียบ ๆ."""


def _empty() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="datetime64[ns]" if c == "date" else "float64" if c in ("amount_thb", "fx_rate", "price_usd", "units") else "object") for c in COLUMNS})


def load_stock_transactions() -> pd.DataFrame:
    path = STOCK_LEDGER_PATH
    if not path.exists():
        return _empty()
    try:
        df = pd.read_csv(path, dtype={"tx_id": str, "plan_month": str, "ticker": str, "universe": str, "rule": str})
    except (pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise StockLedgerError(f"อ่านสมุด STOCK ไม่ได้ ({path}): {exc}") from exc
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise StockLedgerError(f"สมุด STOCK ขาดคอลัมน์ {missing} ({path})")
    if df.empty:
        return _empty()
    df = df[COLUMNS].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in ("amount_thb", "fx_rate", "price_usd", "units"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if df[["date", "amount_thb", "fx_rate", "price_usd", "units"]].isna().any().any():
        raise StockLedgerError(f"สมุด STOCK มีแถวที่ตัวเลข/วันที่อ่านไม่ได้ ({path}) — ไม่เดาแทน")
    return df.sort_values(["date", "ticker"]).reset_index(drop=True)


def _write(df: pd.DataFrame) -> None:
    path = STOCK_LEDGER_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    out = df.copy()
    if not out.empty:
        out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out[COLUMNS].to_csv(tmp, index=False)
    os.replace(tmp, path)


def record_plan(plan: stock_pick.StockPlan, *, today: datetime | pd.Timestamp | None = None) -> list[str]:
    """บันทึกแผนเดือนนี้ทั้งชุด (ตามแผนเป๊ะ ไม่แก้ไขได้) — คืน tx_id · ผิดกติกา = ``StockLedgerError`` ไม่เขียนไฟล์."""
    now = pd.Timestamp(today) if today is not None else pd.Timestamp.now(tz="Asia/Bangkok").tz_localize(None)
    month = now.strftime("%Y-%m")
    if plan.plan_month != month:
        raise StockLedgerError(f"บันทึกได้เฉพาะแผนของเดือนปัจจุบัน ({month}) — แผน {plan.plan_month} ย้อนบันทึกไม่ได้ (กติกาที่ล็อก)")
    if not (MIN_RATE <= plan.fx_rate <= MAX_RATE):
        raise StockLedgerError(f"อัตราแลกเปลี่ยน {plan.fx_rate} อยู่นอกช่วง {MIN_RATE}–{MAX_RATE}")
    existing = load_stock_transactions()
    if not existing.empty and (existing["plan_month"] == month).any():
        raise StockLedgerError(f"เดือน {month} บันทึกไปแล้ว — เดือนละครั้งเท่านั้น (ลบแล้วบันทึกใหม่ = เลือกเดือนที่ดูดี ซึ่งกติกาห้าม)")
    stamp = now.isoformat(timespec="seconds")
    rows = []
    for ln in plan.lines:
        if ln.price_usd is None or ln.units is None or not all(math.isfinite(float(x)) and float(x) > 0 for x in (ln.price_usd, ln.units, ln.amount_thb)):
            raise StockLedgerError(f"{ln.ticker}: ราคา/หน่วย/เงินใช้ไม่ได้ — ไม่บันทึก")
        rows.append({
            "tx_id": uuid.uuid4().hex[:12], "date": now.strftime("%Y-%m-%d"), "plan_month": month, "ticker": ln.ticker,
            "amount_thb": float(ln.amount_thb), "fx_rate": float(plan.fx_rate), "price_usd": float(ln.price_usd), "units": float(ln.units),
            "universe": ",".join(plan.universe), "rule": plan.rule, "recorded_at": stamp,
        })
    new = pd.DataFrame(rows, columns=COLUMNS)
    _write(new if existing.empty else pd.concat([existing, new], ignore_index=True))
    return [r["tx_id"] for r in rows]


def _price_on(adj: pd.Series, date: pd.Timestamp) -> float | None:
    s = adj.dropna().loc[:date]
    if s.empty:
        return None
    v = float(s.iloc[-1])
    return v if math.isfinite(v) and v > 0 else None


def compare(tx: pd.DataFrame, adj_prices: pd.DataFrame, fx_now: float, *, today: pd.Timestamp | None = None) -> dict[str, Any]:
    """เทียบ 3 แขน (เลือก / แบ่งเท่ากันทั้งจักรวาล / VOO) ต่อ cohort เดือน และตัดสินตามเกณฑ์ที่ล็อก.

    ราคาทุกขาอ่านจากชุด ``adj_prices`` ปัจจุบันชุดเดียว ณ วันที่บันทึก (ราคาปรับปันผลถูกเขียนใหม่ทุกครั้งที่มีปันผล จึงใช้ราคาที่เคยเก็บเทียบไม่ได้)
    ตัวไหนในแขนใดแขนหนึ่งไม่มีราคาล่าสุดหรือราคาเก่าเกิน ``MAX_STALE_DAYS`` ⇒ ``status = "ปฏิเสธ"`` และไม่คืนตัวเลขรวมใด ๆ
    """
    out: dict[str, Any] = {"status": "ยังไม่มีข้อมูล", "reasons": [], "cohorts": pd.DataFrame(), "n_cohorts": 0, "invested_thb": 0.0,
                           "picks_value_thb": None, "shadow_value_thb": None, "voo_value_thb": None,
                           "diff_vs_shadow_pct": None, "diff_vs_voo_pct": None, "win_rate_vs_shadow_pct": None, "n_mature": 0}
    if tx.empty:
        return out
    if not math.isfinite(float(fx_now)) or fx_now <= 0:
        raise ValueError(f"อัตราแลกเปลี่ยนปัจจุบันใช้ไม่ได้: {fx_now!r}")
    if stock_pick.BENCHMARK not in adj_prices.columns:
        raise ValueError(f"ไม่มีราคา {stock_pick.BENCHMARK} (แขนเทียบ)")
    last_day = pd.Timestamp(adj_prices[stock_pick.BENCHMARK].dropna().index.max())
    now = pd.Timestamp(today) if today is not None else last_day
    need = sorted({t for u in tx["universe"] for t in str(u).split(",") if t} | set(tx["ticker"]) | {stock_pick.BENCHMARK})
    stale = []
    for t in need:
        col = adj_prices[t].dropna() if t in adj_prices.columns else pd.Series(dtype=float)
        if col.empty or (last_day - pd.Timestamp(col.index.max())).days > stock_pick.MAX_STALE_DAYS:
            stale.append(t)
    if stale:
        out["status"] = "ปฏิเสธ"
        out["reasons"] = [f"ประเมินราคาไม่ได้: {stale} — ราคาหายหรือเก่าเกินไป (อาจเลิกกิจการ/ถูกซื้อ/ย้ายตัวย่อ) ประเมินไม่ได้ ≠ ศูนย์ ≠ ไม่เสียหาย ห้ามตัดสินจนกว่าจะรู้เหตุการณ์จริง"]
        out["n_cohorts"] = int(tx["plan_month"].nunique())
        out["invested_thb"] = float(tx["amount_thb"].sum())
        return out
    rows = []
    for month, g in tx.groupby("plan_month"):
        d = pd.Timestamp(g["date"].iloc[0])
        usd = float(g["amount_thb"].sum()) / float(g["fx_rate"].iloc[0])
        names = sorted(t for t in str(g["universe"].iloc[0]).split(",") if t)
        picked = {r.ticker: float(r.amount_thb) / float(r.fx_rate) for r in g.itertuples()}
        entry = {t: _price_on(adj_prices[t], d) for t in set(names) | set(picked) | {stock_pick.BENCHMARK}}
        gone = sorted(t for t, p in entry.items() if p is None)
        if gone:
            out["status"] = "ปฏิเสธ"
            out["reasons"] = [f"cohort {month}: ไม่มีราคา ณ วันบันทึกของ {gone}"]
            return out
        last = {t: _price_on(adj_prices[t], last_day) for t in entry}
        picks_v = sum(a / entry[t] * last[t] for t, a in picked.items())
        shadow_v = sum(usd / len(names) / entry[t] * last[t] for t in names)
        voo_v = usd / entry[stock_pick.BENCHMARK] * last[stock_pick.BENCHMARK]
        rows.append({"plan_month": month, "age_days": int((now - d).days), "invested_usd": usd, "picks_usd": picks_v, "shadow_usd": shadow_v, "voo_usd": voo_v,
                     "vs_shadow_pct": (picks_v - shadow_v) / usd * 100.0, "vs_voo_pct": (picks_v - voo_v) / usd * 100.0})
    coh = pd.DataFrame(rows).sort_values("plan_month").reset_index(drop=True)
    inv = float(coh["invested_usd"].sum())
    mature = coh[coh["age_days"] >= stock_pick.MATURE_COHORT_DAYS]
    out.update({
        "cohorts": coh, "n_cohorts": len(coh), "n_mature": len(mature), "invested_thb": float(tx["amount_thb"].sum()),
        "picks_value_thb": float(coh["picks_usd"].sum()) * fx_now, "shadow_value_thb": float(coh["shadow_usd"].sum()) * fx_now,
        "voo_value_thb": float(coh["voo_usd"].sum()) * fx_now,
        "diff_vs_shadow_pct": float((coh["picks_usd"].sum() - coh["shadow_usd"].sum()) / inv * 100.0),
        "diff_vs_voo_pct": float((coh["picks_usd"].sum() - coh["voo_usd"].sum()) / inv * 100.0),
        "win_rate_vs_shadow_pct": None if mature.empty else float((mature["vs_shadow_pct"] > 0).mean() * 100.0),
    })
    if len(coh) < stock_pick.MIN_COHORTS:
        out["status"] = "ยังตอบไม่ได้"
        out["reasons"] = [f"มี {len(coh)} cohort จาก {stock_pick.MIN_COHORTS} ที่เกณฑ์ล็อกไว้ — ตัวเลขข้างบนแสดงได้ แต่ห้ามอ่านว่าชนะหรือแพ้"]
        return out
    checks = {
        "ชนะแบ่งเท่ากันทั้งจักรวาล (ส่วนต่างรวม > 0)": out["diff_vs_shadow_pct"] > 0,
        "ชนะ VOO (ส่วนต่างรวม > 0)": out["diff_vs_voo_pct"] > 0,
        f"cohort อายุ ≥ 12 เดือนชนะเงา ≥ {stock_pick.MIN_WIN_RATE_PCT:.0f}%": out["win_rate_vs_shadow_pct"] is not None and out["win_rate_vs_shadow_pct"] >= stock_pick.MIN_WIN_RATE_PCT,
    }
    out["status"] = "ผ่าน" if all(checks.values()) else "ไม่ผ่าน"
    out["reasons"] = [f"{'✓' if ok else '✗'} {k}" for k, ok in checks.items()]
    return out
