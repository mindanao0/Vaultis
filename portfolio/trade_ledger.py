# -*- coding: utf-8 -*-
"""สมุดคำทำนายของโหมด **TRADE** (หุ้นรายตัว 30 ตัว รายวัน) — แยกจากสมุดโหมด PREDICT (5 กอง) และทุกพอร์ตอื่นโดยตั้งใจ.

path อ่านจาก ``VAULTIS_TRADE_LEDGER_PATH`` **ครั้งเดียวตอน import** (เทสต์ monkeypatch ``TRADE_LEDGER_PATH`` — ตาข่ายใน tests/conftest.py ผูกกับชื่อนี้)
กติกาที่ล็อกใน ``research/trade_lab/PREREG.md`` ข้อ 4: หนึ่งชุดต่อวันที่ของแท่งราคา · ห้ามย้อนบันทึก · ไม่มีฟังก์ชันแก้/ลบ ·
**บัญชีกระดาษถูกเล่นซ้ำจากสมุดนี้ + ราคาเสมอ** (ไม่มีไฟล์สถานะบัญชีที่แก้มือได้) — สมุดนี้จึงคือหลักฐานทั้งก้อน หายแล้วกู้ไม่ได้ สำรองไว้
ขนาดโตราว 130,000 แถว/ปี จึงบันทึกแบบต่อท้าย (append) ไม่เขียนทับทั้งไฟล์
"""
from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Sequence

import pandas as pd

from analysis import trade_lab

REPO_ROOT = Path(__file__).resolve().parent.parent
MAX_BACKFILL_DAYS = 5


def _path_from_env() -> Path:
    raw = os.environ.get("VAULTIS_TRADE_LEDGER_PATH", "").strip()
    return Path(raw) if raw else REPO_ROOT / "portfolio" / "data" / "trade_log.csv"


TRADE_LEDGER_PATH: Path = _path_from_env()
COLUMNS = ["date", "ticker", "predictor", "horizon", "direction", "score", "price_usd", "recorded_at"]


class TradeLedgerError(ValueError):
    """ข้อมูลที่จะบันทึก/ที่อ่านได้ผิดรูป หรือผิดกติกาที่ล็อก — ห้ามเดาแทน ห้ามข้ามเงียบ ๆ."""


def _empty() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="datetime64[ns]" if c == "date" else "float64" if c in ("direction", "score", "price_usd") else "object") for c in COLUMNS})


def load_log() -> pd.DataFrame:
    path = TRADE_LEDGER_PATH
    if not path.exists():
        return _empty()
    try:
        df = pd.read_csv(path, dtype={"ticker": str, "predictor": str, "horizon": str})
    except (pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise TradeLedgerError(f"อ่านสมุดคำทำนาย TRADE ไม่ได้ ({path}): {exc}") from exc
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise TradeLedgerError(f"สมุดคำทำนาย TRADE ขาดคอลัมน์ {missing} ({path})")
    if df.empty:
        return _empty()
    df = df[COLUMNS].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in ("direction", "score", "price_usd"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if df[["date", "direction", "score", "price_usd"]].isna().any().any():
        raise TradeLedgerError(f"สมุดคำทำนาย TRADE มีแถวที่ตัวเลข/วันที่อ่านไม่ได้ ({path}) — ไม่เดาแทน")
    return df.reset_index(drop=True)


def _last_date() -> pd.Timestamp | None:
    path = TRADE_LEDGER_PATH
    if not path.exists():
        return None
    try:
        d = pd.to_datetime(pd.read_csv(path, usecols=["date"])["date"], errors="coerce")
    except (ValueError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise TradeLedgerError(f"อ่านสมุดคำทำนาย TRADE ไม่ได้ ({path}): {exc}") from exc
    if d.isna().any():
        raise TradeLedgerError(f"สมุดคำทำนาย TRADE มีวันที่อ่านไม่ได้ ({path})")
    return None if d.empty else pd.Timestamp(d.max())


def _dates() -> set[pd.Timestamp]:
    path = TRADE_LEDGER_PATH
    if not path.exists():
        return set()
    return set(pd.to_datetime(pd.read_csv(path, usecols=["date"])["date"]).unique())


def record_predictions(preds: Sequence[trade_lab.Prediction], bar_date: str | pd.Timestamp, *, today: pd.Timestamp | None = None) -> int | None:
    """บันทึกชุดของ ``bar_date`` ทั้งชุด · คืนจำนวนแถว · ชุดของวันนั้นมีอยู่แล้ว = ``None`` · ผิดกติกา = ``TradeLedgerError`` ไม่เขียนไฟล์."""
    bar = pd.Timestamp(bar_date).normalize()
    now = pd.Timestamp(today) if today is not None else pd.Timestamp.now(tz="Asia/Bangkok").tz_localize(None)
    if (now.normalize() - bar).days > MAX_BACKFILL_DAYS:
        raise TradeLedgerError(f"แท่งราคาล่าสุด {bar:%Y-%m-%d} เก่ากว่า {MAX_BACKFILL_DAYS} วันจากวันนี้ — ย้อนบันทึกไม่ได้ (กติกาที่ล็อก)")
    if not preds:
        raise TradeLedgerError("ไม่มีคำทำนายให้บันทึก")
    if bar in _dates():
        return None
    last = _last_date()
    if last is not None and bar < last:
        raise TradeLedgerError(f"ชุด {bar:%Y-%m-%d} เก่ากว่าชุดที่บันทึกล่าสุด ({last:%Y-%m-%d}) — ย้อนบันทึกไม่ได้")
    stamp = now.isoformat(timespec="seconds")
    rows = []
    for p in preds:
        if p.direction not in (-1, 1) or not all(math.isfinite(float(x)) for x in (p.score, p.price_usd)) or p.price_usd <= 0:
            raise TradeLedgerError(f"{p.ticker}/{p.predictor}/{p.horizon}: คำทำนายใช้ไม่ได้ — ไม่บันทึก")
        rows.append({"date": bar.strftime("%Y-%m-%d"), "ticker": p.ticker, "predictor": p.predictor, "horizon": p.horizon, "direction": int(p.direction),
                     "score": round(float(p.score), 4), "price_usd": round(float(p.price_usd), 6), "recorded_at": stamp})
    path = TRADE_LEDGER_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    new = path.exists() and path.stat().st_size > 0
    with open(path, "a", encoding="utf-8", newline="") as fh:
        pd.DataFrame(rows, columns=COLUMNS).to_csv(fh, index=False, header=not new)
        fh.flush()
        os.fsync(fh.fileno())
    return len(rows)
