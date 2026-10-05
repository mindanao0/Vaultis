# -*- coding: utf-8 -*-
"""สมุดคำทำนายของโหมด **PREDICT** (ทำนายตลาด) — แยกจากสมุดทุกเล่มของแผน DCA/DAR/SELECT/STOCK โดยตั้งใจ.

path อ่านจาก ``VAULTIS_PREDICT_LEDGER_PATH`` **ครั้งเดียวตอน import** (เทสต์ monkeypatch ``PREDICT_LEDGER_PATH`` — ตาข่ายใน tests/conftest.py ผูกกับชื่อนี้)
กติกาที่ล็อกใน ``research/predict_lab/PREREG.md`` ข้อ 4 และบังคับที่นี่: บันทึกหนึ่งชุดต่อวันที่ของแท่งราคา · ห้ามย้อนบันทึก (แท่งต้องไม่เก่ากว่า 5 วันจากวันนี้
และไม่เก่ากว่าชุดล่าสุดที่บันทึกแล้ว) · ไม่มีฟังก์ชันแก้/ลบ — คำทำนายที่บันทึกแล้วแก้ไม่ได้ (ไม่งั้นเลือกวันที่ทายถูกได้)
"""
from __future__ import annotations

import math
import os
import uuid
from pathlib import Path
from typing import Sequence

import pandas as pd

from analysis import predict_lab

REPO_ROOT = Path(__file__).resolve().parent.parent
MAX_BACKFILL_DAYS = 5


def _path_from_env() -> Path:
    raw = os.environ.get("VAULTIS_PREDICT_LEDGER_PATH", "").strip()
    return Path(raw) if raw else REPO_ROOT / "portfolio" / "data" / "predict_log.csv"


PREDICT_LEDGER_PATH: Path = _path_from_env()
COLUMNS = ["pred_id", "date", "plan_month", "ticker", "predictor", "horizon", "direction", "score", "price_usd", "recorded_at"]


class PredictLedgerError(ValueError):
    """ข้อมูลที่จะบันทึก/ที่อ่านได้ผิดรูป หรือผิดกติกาที่ล็อก — ห้ามเดาแทน ห้ามข้ามเงียบ ๆ."""


def _empty() -> pd.DataFrame:
    num = ("direction", "score", "price_usd")
    return pd.DataFrame({c: pd.Series(dtype="datetime64[ns]" if c == "date" else "float64" if c in num else "object") for c in COLUMNS})


def load_log() -> pd.DataFrame:
    path = PREDICT_LEDGER_PATH
    if not path.exists():
        return _empty()
    try:
        df = pd.read_csv(path, dtype={"pred_id": str, "plan_month": str, "ticker": str, "predictor": str, "horizon": str})
    except (pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise PredictLedgerError(f"อ่านสมุดคำทำนายไม่ได้ ({path}): {exc}") from exc
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise PredictLedgerError(f"สมุดคำทำนายขาดคอลัมน์ {missing} ({path})")
    if df.empty:
        return _empty()
    df = df[COLUMNS].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in ("direction", "score", "price_usd"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if df[["date", "direction", "score", "price_usd"]].isna().any().any():
        raise PredictLedgerError(f"สมุดคำทำนายมีแถวที่ตัวเลข/วันที่อ่านไม่ได้ ({path}) — ไม่เดาแทน")
    return df.sort_values(["date", "ticker", "predictor", "horizon"]).reset_index(drop=True)


def _write(df: pd.DataFrame) -> None:
    path = PREDICT_LEDGER_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out[COLUMNS].to_csv(tmp, index=False)
    os.replace(tmp, path)


def record_predictions(
    preds: Sequence[predict_lab.Prediction],
    bar_date: str | pd.Timestamp,
    *,
    today: pd.Timestamp | None = None,
) -> int | None:
    """บันทึกชุดของวันที่ ``bar_date`` ทั้งชุด · คืนจำนวนแถว · ชุดของวันนี้มีอยู่แล้ว = ``None`` (ไม่เขียนซ้ำ — งานรันหลายครั้งต่อวันได้)

    ผิดกติกา (ย้อนบันทึก / คำทำนายใช้ไม่ได้) = ``PredictLedgerError`` ไม่เขียนไฟล์
    """
    bar = pd.Timestamp(bar_date).normalize()
    now = pd.Timestamp(today) if today is not None else pd.Timestamp.now(tz="Asia/Bangkok").tz_localize(None)
    if (now.normalize() - bar).days > MAX_BACKFILL_DAYS:
        raise PredictLedgerError(f"แท่งราคาล่าสุด {bar:%Y-%m-%d} เก่ากว่า {MAX_BACKFILL_DAYS} วันจากวันนี้ — ย้อนบันทึกไม่ได้ (กติกาที่ล็อก)")
    if not preds:
        raise PredictLedgerError("ไม่มีคำทำนายให้บันทึก")
    existing = load_log()
    if not existing.empty:
        if (existing["date"] == bar).any():
            return None
        if bar < existing["date"].max():
            raise PredictLedgerError(f"ชุด {bar:%Y-%m-%d} เก่ากว่าชุดที่บันทึกล่าสุด ({existing['date'].max():%Y-%m-%d}) — ย้อนบันทึกไม่ได้")
    stamp = now.isoformat(timespec="seconds")
    rows = []
    for p in preds:
        if p.direction not in (-1, 1) or not all(math.isfinite(float(x)) for x in (p.score, p.price_usd)) or p.price_usd <= 0:
            raise PredictLedgerError(f"{p.ticker}/{p.predictor}/{p.horizon}: คำทำนายใช้ไม่ได้ — ไม่บันทึก")
        rows.append({"pred_id": uuid.uuid4().hex[:12], "date": bar.strftime("%Y-%m-%d"), "plan_month": bar.strftime("%Y-%m"),
                     "ticker": p.ticker, "predictor": p.predictor, "horizon": p.horizon, "direction": int(p.direction),
                     "score": float(p.score), "price_usd": float(p.price_usd), "recorded_at": stamp})
    new = pd.DataFrame(rows, columns=COLUMNS)
    _write(new if existing.empty else pd.concat([existing, new], ignore_index=True))
    return len(rows)
