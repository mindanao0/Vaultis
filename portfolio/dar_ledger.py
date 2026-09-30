# -*- coding: utf-8 -*-
"""สมุดบันทึกของ **พอร์ต DAR-DCA** — พอร์ตทดลองที่แยกจากพอร์ตหลักโดยตั้งใจ (เริ่มจากศูนย์).

นี่ไม่ใช่ "สมุดบัญชีชุดที่สอง" ของข้อมูลเดียวกัน (กฎ "หนึ่งที่เก็บต่อชนิดข้อมูล" ใน CLAUDE.md
ยังใช้ครบ): ``portfolio/data/transactions.csv`` คือพอร์ตหลักตามแผน ERC ส่วนไฟล์นี้คือ **อีกพอร์ตหนึ่ง**
ที่ผู้ใช้สั่งให้แยกออกมาเพื่อวัดผลของสูตร DAR โดยไม่ปนกัน ห้ามรวมสองไฟล์นี้เข้าด้วยกัน
และห้ามให้หน้า/งานของพอร์ตหลักอ่านไฟล์นี้

path อ่านจาก ``VAULTIS_DAR_LEDGER_PATH`` **ครั้งเดียวตอน import** (เทสต์ monkeypatch ชื่อ
``DAR_LEDGER_PATH`` — แบบเดียวกับ ``portfolio.tracker.TRANSACTIONS_FILE``; ถ้าเปลี่ยนเป็นอ่าน env
ทุกครั้ง ตาข่ายใน tests/conftest.py จะหลุดเงียบ ๆ)

การเทียบผล (:func:`compare_with_equal_shadow`): พอร์ตเงาแบบแบ่งเท่ากัน **ใช้เงินก้อนเดียวกัน
วันเดียวกัน** แบ่งเท่า ๆ กันให้ทุกกองของเดือนนั้น แล้ววัดทั้งสองขาด้วยราคาปรับปันผล (total return)
ชุดเดียวกัน ⇒ ส่วนต่างที่เหลือคือผลของ "การแบ่งเงิน" ล้วน ๆ ซึ่งเป็นสิ่งเดียวที่สูตร DAR ตัดสินใจ
"""

from __future__ import annotations

import math
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from utils.fx import MAX_RATE, MIN_RATE

REPO_ROOT = Path(__file__).resolve().parent.parent


def _ledger_path_from_env() -> Path:
    raw = os.environ.get("VAULTIS_DAR_LEDGER_PATH", "").strip()
    if raw:
        return Path(raw)
    return REPO_ROOT / "portfolio" / "data" / "dar_transactions.csv"


DAR_LEDGER_PATH: Path = _ledger_path_from_env()

COLUMNS = [
    "tx_id",
    "date",
    "plan_month",
    "ticker",
    "amount_thb",
    "fx_rate",
    "price_usd",
    "units",
    "source",
    "note",
    "recorded_at",
]
SOURCES = ("plan", "manual")


class DarLedgerError(ValueError):
    """ข้อมูลที่จะบันทึก/ที่อ่านได้ผิดรูป — ห้ามเดาแทนผู้ใช้ ห้ามข้ามเงียบ ๆ."""


def _empty() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "tx_id": pd.Series(dtype="object"),
            "date": pd.Series(dtype="datetime64[ns]"),
            "plan_month": pd.Series(dtype="object"),
            "ticker": pd.Series(dtype="object"),
            "amount_thb": pd.Series(dtype="float64"),
            "fx_rate": pd.Series(dtype="float64"),
            "price_usd": pd.Series(dtype="float64"),
            "units": pd.Series(dtype="float64"),
            "source": pd.Series(dtype="object"),
            "note": pd.Series(dtype="object"),
            "recorded_at": pd.Series(dtype="object"),
        }
    )


def load_dar_transactions() -> pd.DataFrame:
    """อ่านสมุด DAR — ไม่มีไฟล์ = พอร์ตว่าง (เริ่มจากศูนย์) · ไฟล์เสีย = ``DarLedgerError``."""
    path = DAR_LEDGER_PATH
    if not path.exists():
        return _empty()
    try:
        df = pd.read_csv(path, dtype={"tx_id": str, "plan_month": str, "ticker": str, "source": str, "note": str})
    except (pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise DarLedgerError(f"อ่านสมุด DAR ไม่ได้ ({path}): {exc}") from exc
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise DarLedgerError(f"สมุด DAR ขาดคอลัมน์ {missing} ({path}) — ไฟล์ถูกแก้มือหรือเป็นไฟล์อื่น")
    if df.empty:
        return _empty()
    df = df[COLUMNS].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in ("amount_thb", "fx_rate", "price_usd", "units"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["note"] = df["note"].fillna("")
    return df.sort_values(["date", "ticker"]).reset_index(drop=True)


def _finite_positive(value: Any, label: str, row: int) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError) as exc:
        raise DarLedgerError(f"แถว {row}: {label} ต้องเป็นตัวเลข (ได้ {value!r})") from exc
    if not math.isfinite(v) or v <= 0:
        raise DarLedgerError(f"แถว {row}: {label} ต้องเป็นจำนวนบวก (ได้ {value!r})")
    return v


def _validate(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    clean: list[dict[str, Any]] = []
    now = datetime.now().isoformat(timespec="seconds")
    for i, row in enumerate(rows, start=1):
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker:
            raise DarLedgerError(f"แถว {i}: ไม่มีชื่อกอง")
        raw_date = row.get("date")
        date = pd.to_datetime(raw_date, errors="coerce")
        if pd.isna(date):
            raise DarLedgerError(f"แถว {i}: วันที่อ่านไม่ออก ({raw_date!r})")
        fx = _finite_positive(row.get("fx_rate"), "อัตราแลกเปลี่ยน", i)
        if not (MIN_RATE <= fx <= MAX_RATE):
            raise DarLedgerError(f"แถว {i}: อัตราแลกเปลี่ยน {fx} อยู่นอกช่วง {MIN_RATE}–{MAX_RATE} บาท/ดอลลาร์")
        source = str(row.get("source") or "manual").strip()
        if source not in SOURCES:
            raise DarLedgerError(f"แถว {i}: source ต้องเป็น {SOURCES} (ได้ {source!r})")
        plan_month = str(row.get("plan_month") or date.strftime("%Y-%m")).strip()
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", plan_month):
            raise DarLedgerError(f"แถว {i}: plan_month ต้องเป็น YYYY-MM (ได้ {plan_month!r})")
        clean.append(
            {
                "tx_id": str(row.get("tx_id") or uuid.uuid4().hex[:12]),
                "date": date.strftime("%Y-%m-%d"),
                "plan_month": plan_month,
                "ticker": ticker,
                "amount_thb": _finite_positive(row.get("amount_thb"), "จำนวนเงิน (บาท)", i),
                "fx_rate": fx,
                "price_usd": _finite_positive(row.get("price_usd"), "ราคา (ดอลลาร์)", i),
                "units": _finite_positive(row.get("units"), "จำนวนหน่วย", i),
                "source": source,
                "note": str(row.get("note") or ""),
                "recorded_at": now,
            }
        )
    if not clean:
        raise DarLedgerError("ไม่มีรายการให้บันทึก")
    return clean


def _write(df: pd.DataFrame) -> None:
    path = DAR_LEDGER_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    out = df.copy()
    if "date" in out.columns and not out.empty:
        out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out[COLUMNS].to_csv(tmp, index=False)
    os.replace(tmp, path)


def add_dar_purchases(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    """บันทึกการซื้อ (ทั้งชุดหรือไม่บันทึกเลย) — คืน ``tx_id`` ของแถวใหม่."""
    clean = _validate(list(rows))
    existing = load_dar_transactions()
    dup = set(existing["tx_id"].astype(str)) & {r["tx_id"] for r in clean}
    if dup:
        raise DarLedgerError(f"tx_id ซ้ำกับที่มีอยู่แล้ว: {sorted(dup)}")
    new = pd.DataFrame(clean, columns=COLUMNS)
    combined = new if existing.empty else pd.concat([existing, new], ignore_index=True)
    _write(combined)
    return [r["tx_id"] for r in clean]


def delete_dar_transaction(tx_id: str) -> bool:
    """ลบหนึ่งรายการ — ไม่พบ ``tx_id`` = ``False`` (ไม่เขียนไฟล์)."""
    df = load_dar_transactions()
    mask = df["tx_id"].astype(str) == str(tx_id)
    if not mask.any():
        return False
    _write(df.loc[~mask])
    return True


# ----------------------------------------------------------------------------- analytics
def positions(tx: pd.DataFrame) -> pd.DataFrame:
    """หน่วยสะสมและเงินที่ลงไปต่อกอง (จากหน่วยที่ซื้อได้จริงตามที่บันทึก)."""
    if tx.empty:
        return pd.DataFrame(columns=["ticker", "units", "invested_thb"]).set_index("ticker")
    g = tx.groupby("ticker").agg(units=("units", "sum"), invested_thb=("amount_thb", "sum"))
    return g.sort_index()


def _price_on(adj: pd.Series, date: pd.Timestamp) -> float | None:
    s = adj.dropna()
    s = s.loc[:date]
    if s.empty:
        return None
    v = float(s.iloc[-1])
    return v if math.isfinite(v) and v > 0 else None


def compare_with_equal_shadow(tx: pd.DataFrame, adj_prices: pd.DataFrame, fx_now: float) -> dict[str, Any]:
    """เทียบพอร์ต DAR กับพอร์ตเงาแบ่งเท่ากันที่ใช้ **เงินเข้าชุดเดียวกัน** (วันเดียวกัน จำนวนเท่ากัน).

    ทั้งสองขาวัดด้วยราคาปรับปันผล (``adj_prices``) ชุดเดียวกัน ⇒ ปันผล/ภาษี/ค่าธรรมเนียมที่เป็น
    สัดส่วนเท่ากันหักล้างกันเอง เหลือแต่ผลของการแบ่งเงิน
    แต่ละแถว (วัน d, เงิน a) ของพอร์ต DAR ⇒ เงาซื้อ a/N ให้ทุกกองของเดือนนั้น (N = กองในแผนเดือนนั้น)
    ณ วัน d เดียวกัน · แถวที่หาราคาไม่ได้ถูกตัดออก **ทั้งสองขา** พร้อมเหตุผล (ไม่ใช่ข้ามข้างเดียว)
    """
    result: dict[str, Any] = {
        "rows_used": 0,
        "rows_excluded": [],
        "invested_thb": 0.0,
        "dar_value_thb": None,
        "shadow_value_thb": None,
        "diff_thb": None,
        "diff_pct_of_invested": None,
        "path": pd.DataFrame(columns=["dar_usd", "shadow_usd", "invested_usd"]),
    }
    if tx.empty:
        return result
    if not math.isfinite(float(fx_now)) or fx_now <= 0:
        raise ValueError(f"อัตราแลกเปลี่ยนปัจจุบันใช้ไม่ได้: {fx_now!r}")
    universe = tx.groupby("plan_month")["ticker"].apply(lambda s: sorted(set(s))).to_dict()
    dar_units: dict[str, float] = {}
    sh_units: dict[str, float] = {}
    events: list[tuple[pd.Timestamp, dict[str, float], dict[str, float], float]] = []
    invested_thb = 0.0
    for _, row in tx.sort_values("date").iterrows():
        d = pd.Timestamp(row["date"])
        names = universe.get(row["plan_month"], [row["ticker"]])
        prices = {t: (_price_on(adj_prices[t], d) if t in adj_prices.columns else None) for t in set(names) | {row["ticker"]}}
        missing = sorted(t for t, p in prices.items() if p is None)
        if missing:
            result["rows_excluded"].append(
                {"tx_id": row["tx_id"], "date": d.strftime("%Y-%m-%d"), "reason": f"ไม่มีราคาของ {missing} ณ วันนั้น"}
            )
            continue
        usd = float(row["amount_thb"]) / float(row["fx_rate"])
        du = {row["ticker"]: usd / prices[row["ticker"]]}
        su = {t: usd / len(names) / prices[t] for t in names}
        for t, u in du.items():
            dar_units[t] = dar_units.get(t, 0.0) + u
        for t, u in su.items():
            sh_units[t] = sh_units.get(t, 0.0) + u
        invested_thb += float(row["amount_thb"])
        events.append((d, dict(du), dict(su), usd))
        result["rows_used"] += 1
    if not events:
        return result
    last = {t: _price_on(adj_prices[t], adj_prices.index.max()) for t in set(dar_units) | set(sh_units)}
    if any(v is None for v in last.values()):
        raise ValueError(f"ไม่มีราคาล่าสุดของ {[t for t, v in last.items() if v is None]} — ประเมินมูลค่าไม่ได้")
    dar_usd = sum(u * last[t] for t, u in dar_units.items())
    sh_usd = sum(u * last[t] for t, u in sh_units.items())
    result.update(
        {
            "invested_thb": invested_thb,
            "dar_value_thb": dar_usd * fx_now,
            "shadow_value_thb": sh_usd * fx_now,
            "diff_thb": (dar_usd - sh_usd) * fx_now,
            "diff_pct_of_invested": (dar_usd - sh_usd) * fx_now / invested_thb * 100.0 if invested_thb else None,
        }
    )
    # เส้นทางรายวัน (ดอลลาร์ — ทั้งสองขาเจออัตราแลกเปลี่ยนเดียวกัน จึงเทียบกันตรง ๆ ได้)
    start = events[0][0]
    frame = adj_prices.loc[adj_prices.index >= start].ffill()
    cum_d = {t: pd.Series(0.0, index=frame.index) for t in frame.columns}
    cum_s = {t: pd.Series(0.0, index=frame.index) for t in frame.columns}
    inv = pd.Series(0.0, index=frame.index)
    for d, du, su, usd in events:
        after = frame.index >= d
        for t, u in du.items():
            cum_d[t] = cum_d[t] + np.where(after, u, 0.0)
        for t, u in su.items():
            cum_s[t] = cum_s[t] + np.where(after, u, 0.0)
        inv = inv + np.where(after, usd, 0.0)
    dar_path = sum(cum_d[t] * frame[t] for t in frame.columns if t in dar_units)
    sh_path = sum(cum_s[t] * frame[t] for t in frame.columns if t in sh_units)
    result["path"] = pd.DataFrame({"dar_usd": dar_path, "shadow_usd": sh_path, "invested_usd": inv})
    return result
