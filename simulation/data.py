# -*- coding: utf-8 -*-
"""ข้อมูลของ simulation: ดึงสดอัตโนมัติ → เก็บพร้อม manifest + SHA-256 → สร้างแผงสอบเทียบ (แผนรายเดือนอ่านจากนี่).

กติกาโปรเจกต์ที่ใช้ที่นี่ (อย่าผ่อน):
* **ล้มดัง ไม่เดา** — ดึงกอง/ชุดข้อมูลไม่ได้ = :class:`SimulationDataError` ไม่เติมค่าแทน (ข้อมูลเก่ายังอยู่ แต่ถูกบอกว่าเก่า)
* ดึงราคา **ทีละกอง** ผ่าน ``analysis.dar_dca.fetch_total_return_history`` ห้าม ``yf.download`` (ดูเหตุผลที่ฟังก์ชันนั้น)
* ไฟล์ข้อมูลเก็บที่ ``simulation/data/`` (bind mount ใน Docker, gitignore) — ``VAULTIS_SIM_DATA_DIR`` อ่านครั้งเดียวตอน import
* ช่วงข้อมูลสอบเทียบ "วัด" จากข้อมูลจริงทั้งหมด ยกเว้นที่ระบุว่า ASSUMED ใน ``engine.py``
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from analysis.dar_dca import fetch_total_return_history, load_month_end_history
from analysis.proxy_history import proxy_tickers_for
from simulation.universe import CALIBRATION_REQUIRED, asset_for, extra_tickers, is_guessed, universe_for

logger = logging.getLogger(__name__)

#: โฟลเดอร์ข้อมูล — **อ่านครั้งเดียวตอน import** (เทสต์ monkeypatch ชื่อนี้) · Docker bind mount ที่ /app/simulation/data
DATA_DIR = Path(os.getenv("VAULTIS_SIM_DATA_DIR", str(Path(__file__).resolve().parent / "data")))

RAW_FILE = "raw.pkl"
MANIFEST_FILE = "manifest.json"
FX_TICKER = "THB=X"
FRED_SERIES = ("CPIAUCSL", "FEDFUNDS", "DGS10", "DCOILWTICO", "DEXTHUS", "VIXCLS", "USREC", "FPCPITOTLZGTHA")
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
HISTORY_YEARS = 40
#: เก่ากว่านี้ = ต้องดึงใหม่ (ตัวตั้งเวลาเช็กทุกวัน)
STALE_AFTER_DAYS = 7
#: ข้อมูลน้อยกว่านี้ (เดือนของผลตอบแทนที่ทุกกองมีร่วมกัน) สอบเทียบไม่ได้
MIN_POOL_MONTHS = 60
_FETCH_ATTEMPTS = 3


class SimulationDataError(RuntimeError):
    """ข้อมูล simulation ดึง/อ่าน/สร้างไม่ได้ — ข้อความเป็นภาษาไทยพอให้ผู้ใช้เข้าใจสาเหตุ."""


@dataclass
class RawInputs:
    daily: pd.DataFrame                      # ราคา total return รายวัน (กอง + กองพี่ + THB=X)
    fred: dict[str, pd.Series]               # ชุดข้อมูลมหภาค
    tickers: list[str]                       # กองที่ผู้ใช้ถือ/ติดตามตอนดึง
    fetched_at: str = ""
    sha256: str = ""
    notes: list[str] = field(default_factory=list)
    #: ปันผลจริงต่อกอง (วันที่ → จำนวนต่อหน่วยเป็น USD) — ไว้วัด yield 12 เดือนสำหรับภาษีหัก ณ ที่จ่าย; ว่าง = กองนั้นไม่จ่ายปันผล
    #: (ข้อมูลที่ดึงไว้ก่อนมีฟิลด์นี้ = ไม่มีคีย์เลย → ใช้ค่าตามชนิดกองและ **บอก**)
    dividends: dict[str, pd.Series] = field(default_factory=dict)


#: yield ต่อปีโดยประมาณตามชนิดกอง — ใช้เฉพาะเมื่อไม่มีข้อมูลปันผลจริงของกองนั้น (ASSUMED ไม่ใช่ข้อมูลที่วัด)
DEFAULT_YIELD_BY_KIND = {
    "us_equity": 0.013, "us_dividend": 0.035, "us_growth": 0.006, "us_health": 0.015, "gold": 0.0,
    "intl_equity": 0.030, "em_equity": 0.028, "reit": 0.038, "bond": 0.035, "other_equity": 0.013,
}


# ---------------------------------------------------------------- ดึงสด
def _fetch_fred(series_id: str) -> pd.Series:
    import requests

    last: Exception | None = None
    for attempt in range(_FETCH_ATTEMPTS):
        try:
            r = requests.get(FRED_URL.format(series_id), timeout=40)
            r.raise_for_status()
            text = r.text
            if not text.startswith("observation_date"):
                raise ValueError("เนื้อที่ได้ไม่ใช่ CSV ของ FRED (อาจเป็นหน้า HTML/ถูกบล็อก)")
            df = pd.read_csv(io.StringIO(text))
            s = pd.to_numeric(df.iloc[:, 1], errors="coerce")
            s.index = pd.to_datetime(df.iloc[:, 0])
            s = s.dropna()
            if s.empty:
                raise ValueError("ไม่มีข้อมูล")
            return s
        except Exception as exc:  # noqa: BLE001 — นับทุกความล้มเหลว แล้วล้มดังเมื่อครบรอบ
            last = exc
            if attempt < _FETCH_ATTEMPTS - 1:
                time.sleep(2.0)
    raise SimulationDataError(f"ดึง {series_id} จาก FRED ไม่สำเร็จหลังลอง {_FETCH_ATTEMPTS} ครั้ง: {last}")


def fetch_dividends(tickers: list[str]) -> dict[str, pd.Series]:
    """ปันผลจริงทีละกอง (``yf.Ticker(t).dividends``) — ว่าง = ไม่จ่ายปันผล (ถูกต้อง เช่นกองทอง) · ดึงล้ม = ล้มดัง."""
    import yfinance as yf

    out: dict[str, pd.Series] = {}
    for t in dict.fromkeys(tickers):
        last: Exception | None = None
        for attempt in range(_FETCH_ATTEMPTS):
            try:
                s = pd.to_numeric(yf.Ticker(t).dividends, errors="coerce").dropna()
                idx = pd.DatetimeIndex(s.index)
                if idx.tz is not None:
                    idx = idx.tz_localize(None)
                out[t] = pd.Series(s.to_numpy(dtype=float), index=idx.normalize())
                break
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt < _FETCH_ATTEMPTS - 1:
                    time.sleep(2.0)
        else:
            raise SimulationDataError(f"ดึงปันผลของ {t} ไม่สำเร็จหลังลอง {_FETCH_ATTEMPTS} ครั้ง: {last}")
    return out


def required_tickers(tickers: list[str]) -> list[str]:
    """กองที่ต้องดึงทั้งหมด: กองที่ถือ + กองพี่สำหรับสอบเทียบ + กองพี่ของสูตร DAR + กองวัดเหตุการณ์ปี 2000 + ค่าเงิน."""
    assets = universe_for(tickers)
    funds = [a.ticker for a in assets]
    need = list(funds)
    need += [a.calib_proxy for a in assets if a.calib_proxy]
    need += proxy_tickers_for(funds)
    need += list(CALIBRATION_REQUIRED)
    need.append(FX_TICKER)
    return list(dict.fromkeys(need))


def fetch_raw(tickers: list[str], include_extras: bool = True) -> RawInputs:
    """ดึงข้อมูลสดทั้งหมด (ประมาณ 1–3 นาที).

    * **กองหลัก** (ที่ผู้ใช้ติดตาม + กองพี่ + FRED + ค่าเงิน): ล้มตัวใดตัวหนึ่ง = ล้มทั้งชุด ไม่ส่งชุดครึ่งเดียว
    * **กองเสริม** (``universe.KNOWN_EXTRA_ASSETS`` ไว้ลองใน simulation): ดึงไม่ได้ = ข้ามกองนั้นแล้ว **บอกใน notes**
      (กองเสริมตัวเดียวที่ Yahoo ล่มไม่ควรทำให้ simulation ของแผนหลักไม่มีข้อมูลเลย)
    """
    from data.fetcher import PriceDataUnavailableError

    need = required_tickers(tickers)
    try:
        daily = fetch_total_return_history(need, years=HISTORY_YEARS)
    except PriceDataUnavailableError as exc:
        raise SimulationDataError(f"ดึงราคาสำหรับ simulation ไม่สำเร็จ: {exc}") from exc
    missing = [t for t in need if t not in daily.columns or daily[t].dropna().empty]
    if missing:
        raise SimulationDataError(f"ไม่มีราคาของ {', '.join(missing)}")
    fred = {sid: _fetch_fred(sid) for sid in FRED_SERIES}
    funds = [a.ticker for a in universe_for(tickers)]
    dividends = fetch_dividends(funds)
    notes: list[str] = []
    guessed = [t for t in tickers if is_guessed(t)]
    if guessed:
        notes.append("ไม่รู้จักชนิดของ " + ", ".join(guessed) + " — ใช้ค่าสมมติของหุ้นสหรัฐ (ขนาดเหตุการณ์/ผลตอบแทนคาดหวังเป็นการเดา)")
    if include_extras:
        added: dict[str, pd.Series] = {}
        for a in (asset_for(t) for t in extra_tickers()):
            if a.ticker in daily.columns:
                continue
            try:
                got = fetch_total_return_history(list(dict.fromkeys([a.ticker] + ([a.calib_proxy] if a.calib_proxy else []))), years=HISTORY_YEARS)
                for c in got.columns:
                    if c not in daily.columns:
                        added[c] = got[c]
                dividends.update(fetch_dividends([a.ticker]))
            except (PriceDataUnavailableError, SimulationDataError) as exc:
                notes.append(f"ดึงกองเสริม {a.ticker} ไม่ได้ ({exc}) — ข้ามกองนี้ (ไม่กระทบแผนหลัก)")
        if added:
            daily = daily.join(pd.DataFrame(added), how="outer").sort_index()
    return RawInputs(
        daily=daily, fred=fred, tickers=[a.ticker for a in universe_for(tickers)],
        fetched_at=datetime.now(timezone(timedelta(hours=7))).isoformat(timespec="seconds"), notes=notes, dividends=dividends,
    )


# ---------------------------------------------------------------- เก็บ/อ่าน (พร้อมตรวจ hash)
def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def save_raw(raw: RawInputs, directory: Path | None = None) -> dict[str, Any]:
    """เขียน raw.pkl + manifest.json แบบ atomic (เขียนไฟล์ชั่วคราวแล้ว replace) — อ่านระหว่างเขียนจะไม่เจอไฟล์ครึ่งเดียว."""
    d = Path(directory or DATA_DIR)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (RAW_FILE + ".tmp")
    pd.to_pickle({"daily": raw.daily, "fred": raw.fred, "tickers": raw.tickers, "fetched_at": raw.fetched_at, "notes": raw.notes,
                  "dividends": raw.dividends}, tmp)
    sha = _sha(tmp)
    os.replace(tmp, d / RAW_FILE)
    series = {
        str(c): {"first": raw.daily[c].first_valid_index().date().isoformat(),
                 "last": raw.daily[c].last_valid_index().date().isoformat(), "n": int(raw.daily[c].notna().sum())}
        for c in raw.daily.columns
    }
    manifest = {
        "fetched_at": raw.fetched_at, "sha256": sha, "tickers": raw.tickers, "notes": raw.notes, "series": series,
        "fred": {k: {"last": v.index[-1].date().isoformat(), "n": int(len(v))} for k, v in raw.fred.items()},
        "dividends": {k: int(len(v)) for k, v in raw.dividends.items()},
        # กองที่ใช้เป็นกองของ simulation ได้ (มีราคาของตัวเอง + ปันผลที่ดึงแล้ว) — ไม่รวมกองพี่ที่ดึงมาแค่ยืดประวัติ
        "funds_available": sorted(t for t in raw.dividends if t in raw.daily.columns),
    }
    tmpm = d / (MANIFEST_FILE + ".tmp")
    tmpm.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmpm, d / MANIFEST_FILE)
    return manifest


def load_raw(directory: Path | None = None) -> RawInputs:
    """อ่านข้อมูลดิบ — hash ไม่ตรง manifest/ไฟล์หาย = :class:`SimulationDataError` (ไม่ใช้ข้อมูลที่ไม่รู้ที่มา)."""
    d = Path(directory or DATA_DIR)
    mf, rp = d / MANIFEST_FILE, d / RAW_FILE
    if not mf.exists() or not rp.exists():
        raise SimulationDataError("ยังไม่มีข้อมูล simulation — ต้องดึงก่อน (`python main.py --job sim_refresh` หรือรอตัวตั้งเวลา)")
    manifest = json.loads(mf.read_text(encoding="utf-8"))
    if _sha(rp) != manifest.get("sha256"):
        raise SimulationDataError("ไฟล์ข้อมูล simulation ไม่ตรงกับ manifest (hash ต่าง) — ดึงใหม่")
    blob = pd.read_pickle(rp)
    return RawInputs(daily=blob["daily"], fred=blob["fred"], tickers=list(blob["tickers"]),
                     fetched_at=blob.get("fetched_at", ""), sha256=manifest["sha256"], notes=list(blob.get("notes", [])),
                     dividends=dict(blob.get("dividends", {})))


def data_status(directory: Path | None = None, now: datetime | None = None) -> dict[str, Any]:
    """สถานะข้อมูลสำหรับแสดงบนหน้าจอ/Discord: มีไหม · ดึงเมื่อไร · เก่าไหม · แท่งราคาล่าสุด."""
    d = Path(directory or DATA_DIR)
    mf = d / MANIFEST_FILE
    if not mf.exists() or not (d / RAW_FILE).exists():
        return {"exists": False, "stale": True, "reason": "ยังไม่เคยดึงข้อมูล simulation"}
    try:
        m = json.loads(mf.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(m["fetched_at"])
    except Exception as exc:  # noqa: BLE001
        return {"exists": False, "stale": True, "reason": f"อ่าน manifest ไม่ได้: {exc}"}
    now = now or datetime.now(timezone(timedelta(hours=7)))
    age = (now - fetched).total_seconds() / 86400.0
    last_bar = max((v["last"] for k, v in m["series"].items() if k != FX_TICKER), default="")
    stale = age > STALE_AFTER_DAYS
    return {"exists": True, "stale": stale, "age_days": round(age, 1), "fetched_at": m["fetched_at"],
            "last_bar": last_bar, "tickers": m["tickers"], "series_tickers": sorted(m["series"]),
            "funds_available": m.get("funds_available") or list(m["tickers"]), "sha256": m["sha256"],
            "notes": m.get("notes", []),
            "reason": f"ข้อมูลเก่า {age:.0f} วัน (เกิน {STALE_AFTER_DAYS})" if stale else ""}


# ---------------------------------------------------------------- สร้างแผงสอบเทียบ
REGIMES = ["calm", "stress", "crisis", "highinfl"]


def _last_complete_month_end(last_bar: pd.Timestamp) -> pd.Timestamp:
    """สิ้นเดือนล่าสุดที่ "ปิดครบแล้ว" ณ วันที่ของแท่งราคาล่าสุด (เดือนที่ยังเหลือวันทำการ = ยังไม่ปิด)."""
    me = last_bar + pd.offsets.MonthEnd(0)
    remaining = pd.bdate_range(last_bar + pd.Timedelta(days=1), me)
    return me if len(remaining) == 0 else (me - pd.offsets.MonthEnd(1))


def _splice(daily: pd.DataFrame, own: str, sib: str) -> pd.Series:
    """ต่อประวัติ ``own`` ด้วย ``sib`` โดยปรับระดับที่วันเชื่อม (กติกาเดียวกับ ``proxy_history``)."""
    a, b = daily[own].dropna(), daily[sib].dropna()
    join = a.index[0]
    before = b.loc[:join]
    if before.empty or b.index[0] >= join:
        return a
    scale = float(a.iloc[0]) / float(before.iloc[-1])
    return pd.concat([b[b.index < join] * scale, a])


def build_panel(raw: RawInputs, tickers: list[str] | None = None) -> dict[str, Any]:
    """แผงสอบเทียบสำหรับ ``engine.run_chunk`` — ผลตอบแทนรายเดือนจริงของกองที่ระบุ + มหภาค + regime + ความแปรปรวนรายวัน."""
    assets = universe_for(tickers or raw.tickers)
    funds = [a.ticker for a in assets]
    kinds = [a.kind for a in assets]
    daily, fred = raw.daily, raw.fred
    for f in funds:
        if f not in daily.columns or daily[f].dropna().empty:
            raise SimulationDataError(f"ไม่มีราคาของ {f} ในข้อมูลที่ดึงไว้ — ดึงใหม่ (กองนี้อาจเพิ่งเพิ่ม)")
    last_bar = pd.Timestamp(daily[funds].dropna(how="all").index.max())
    as_of = _last_complete_month_end(last_bar)
    plan_month = (as_of + pd.Timedelta(days=1)).to_period("M")

    def me(s: pd.Series) -> pd.Series:
        s = s.dropna().resample("ME").last().dropna()
        return s[s.index <= as_of]

    # ---- ประวัติจริงตามที่สูตรสด (DAR) เห็น — ใช้ฟังก์ชันของโปรเจกต์เอง ไม่ประกอบใหม่
    live_me, used, _ = load_month_end_history(funds, plan_month, fetch=lambda t, years: daily[list(t)])
    # ---- แผงรายเดือนสำหรับสอบเทียบ (ยืดด้วยกองพี่)
    spliced = {}
    for a in assets:
        s = daily[a.ticker].dropna()
        spliced[a.ticker] = _splice(daily, a.ticker, a.calib_proxy) if a.calib_proxy and a.calib_proxy in daily.columns else s
    lev = pd.DataFrame({k: me(v) for k, v in spliced.items()})
    lr = np.log(lev).diff()

    fx_daily_yf = daily[FX_TICKER].dropna()
    fx_me = me(fred["DEXTHUS"])
    fx_yf = me(fx_daily_yf)
    fx0 = float(fx_yf.iloc[-1])

    def monthly_end(idx: pd.Series) -> pd.Series:
        s = idx.copy(); s.index = s.index + pd.offsets.MonthEnd(0); return s

    cpi = monthly_end(fred["CPIAUCSL"]); ffr = monthly_end(fred["FEDFUNDS"]); rec = monthly_end(fred["USREC"])
    y10 = me(fred["DGS10"]); wti = me(fred["DCOILWTICO"]); vix = fred["VIXCLS"].resample("ME").mean()
    infl_m = np.log(cpi).diff()
    infl_yoy = np.exp(np.log(cpi).diff(12)) - 1.0

    # ---- ป้าย regime 1990–ปัจจุบัน (กติกาตายตัว อ่านง่าย — ไม่ได้ฟิต)
    idx = pd.date_range("1990-01-31", as_of, freq="ME")
    lab = pd.Series("calm", index=idx, dtype=object)
    v = vix.reindex(idx); r = rec.reindex(idx).fillna(0); yoy = infl_yoy.reindex(idx)
    crisis = (r == 1) | (v >= 30)
    highinfl = (~crisis) & (yoy >= 0.04)
    stress = (~crisis) & (~highinfl) & (v >= 20)
    lab[stress] = "stress"; lab[highinfl] = "highinfl"; lab[crisis] = "crisis"
    lab = lab.where(v.notna(), other="calm")
    code = lab.map({n: i for i, n in enumerate(REGIMES)}).to_numpy()
    K = len(REGIMES)
    trans = np.ones((K, K)) * 0.5
    for a_, b_ in zip(code[:-1], code[1:]):
        trans[a_, b_] += 1.0
    trans = trans / trans.sum(axis=1, keepdims=True)

    # ---- ความแปรปรวนร่วมรายวันจริงของแต่ละเดือน (เป็นบาท) — ให้ ERC ใน sim ประมาณด้วยสูตรเดียวกับของจริง
    daily_px = pd.DataFrame(spliced).dropna()
    fx_daily = fx_daily_yf.reindex(daily_px.index).ffill(limit=3)
    thb_d = np.log(daily_px.mul(fx_daily, axis=0)).diff().dropna()
    iu = np.triu_indices(len(funds))
    S_rows, S_days, S_idx = [], [], []
    for me_ts, g in thb_d.groupby(pd.Grouper(freq="ME")):
        if me_ts > as_of or len(g) == 0:
            continue
        a_ = g.to_numpy()
        S_rows.append((a_.T @ a_)[iu]); S_days.append(len(g)); S_idx.append(me_ts)
    S_all = pd.DataFrame(S_rows, index=S_idx)
    S_days = pd.Series(S_days, index=S_idx)
    # เดือนแรกที่มีผลตอบแทนรายวันเป็นบาทครบอย่างน้อย 15 วัน (ค่าเงินรายวันเริ่ม 2003-12) — ช่วงสอบเทียบต้องไม่เริ่มก่อนนั้น
    s_ok = S_days[S_days >= 15]
    if s_ok.empty:
        raise SimulationDataError("ไม่มีเดือนที่มีผลตอบแทนรายวันเป็นบาทครบทุกกอง — ประมาณความเสี่ยงของ ERC ไม่ได้")
    s_start = s_ok.index[0]

    cols = funds + ["fx", "us_infl", "d_ffr", "d_10y", "oil"]
    panel = pd.DataFrame({**{f: lr[f] for f in funds}, "fx": np.log(fx_me).diff(), "us_infl": infl_m,
                          "d_ffr": ffr.diff(), "d_10y": y10.diff(), "oil": np.log(wti.where(wti > 0)).diff()})[cols]
    first_ret = max(max(lev[f].first_valid_index() for f in funds) + pd.offsets.MonthEnd(1), s_start)
    panel = panel.loc[first_ret:].dropna()
    if len(panel) < MIN_POOL_MONTHS:
        raise SimulationDataError(
            f"ข้อมูลที่ทุกกองมีร่วมกันมีแค่ {len(panel)} เดือน (ต้องการอย่างน้อย {MIN_POOL_MONTHS}) — "
            "กองใหม่เกินไปและไม่มีกองพี่ให้ยืดประวัติ จำลองให้เชื่อถือไม่ได้")
    pool_regime = lab.reindex(panel.index).map({n: i for i, n in enumerate(REGIMES)}).to_numpy()
    pools = [panel.to_numpy()[pool_regime == k] for k in range(K)]
    for k, p in enumerate(pools):
        if len(p) == 0:
            raise SimulationDataError(f"ไม่มีเดือนจริงของ regime {REGIMES[k]} ในช่วงข้อมูลสอบเทียบ — จำลองไม่ได้")

    pool_S = [S_all.reindex(panel.index[pool_regime == k]).to_numpy() for k in range(K)]
    pool_days = [S_days.reindex(panel.index[pool_regime == k]).to_numpy().astype(float) for k in range(K)]
    live_idx = pd.date_range(end=as_of, periods=60, freq="ME")
    live_S = S_all.reindex(live_idx).to_numpy()
    live_days = S_days.reindex(live_idx).to_numpy().astype(float)
    if np.isnan(live_S).any() or any(np.isnan(p).any() for p in pool_S):
        raise SimulationDataError("ผลตอบแทนรายวันของบางกองไม่ครบในช่วง 5 ปีล่าสุด/ช่วงสอบเทียบ — ประมาณความเสี่ยงของ ERC ไม่ได้")

    # ---- เงินเฟ้อไทย ≈ a + b × เงินเฟ้อสหรัฐ (รายปี) — ความมั่นใจต่ำ รายงานตามจริง
    th = fred["FPCPITOTLZGTHA"] / 100.0
    th.index = th.index.year
    us_ann = cpi.groupby(cpi.index.year).mean().pct_change().dropna()
    both = pd.concat([th.rename("th"), us_ann.rename("us")], axis=1).dropna().loc[1981:]
    b_, a0 = np.polyfit(both["us"], both["th"], 1)
    resid = both["th"] - (a0 + b_ * both["us"])
    th_fit = {"a": float(a0), "b": float(b_), "resid_sd_annual": float(resid.std(ddof=2)),
              "r2": float(1 - resid.var() / both["th"].var()), "n": int(len(both))}

    # ---- เหตุการณ์ที่วัดได้จากข้อมูลจริง
    def tr(sym: str, a_: str, b2: str) -> float:
        s = daily[sym].dropna()
        return float(math.log(s.loc[:b2].iloc[-1] / s.loc[:a_].iloc[-1]))

    lfx = np.log(fx_me.loc["1981":])
    ev = {
        "dotcom": {"VOO": tr("SPY", "2000-03-24", "2002-10-09"), "QQQM": tr("QQQ", "2000-03-24", "2002-10-09"),
                   "XLV": tr("XLV", "2000-03-24", "2002-10-09"), "months": 31},
        "fx_up_6m": float(lfx.diff(6).max()), "fx_down_24m": float(lfx.diff(24).min()),
        "fx_up_6m_end": str(lfx.diff(6).idxmax().date()), "fx_down_24m_end": str(lfx.diff(24).idxmin().date()),
    }
    first_valid = {c: int(live_me[c].notna().to_numpy().argmax()) for c in live_me.columns}
    yields, yield_source = {}, {}
    for a in assets:
        if a.ticker in raw.dividends:
            div = raw.dividends[a.ticker]
            if len(div) == 0:       # ซีรีส์ว่าง = กองนี้ไม่จ่ายปันผลจริง (เช่นกองทอง) → yield 0 ที่ "วัดแล้ว" ไม่ใช่ค่าเดา
                yields[a.ticker], yield_source[a.ticker] = 0.0, "measured"
                continue
            div = div.set_axis(pd.DatetimeIndex(div.index))
            window = div[(div.index > last_bar - pd.Timedelta(days=365)) & (div.index <= last_bar)]
            price = float(daily[a.ticker].dropna().iloc[-1])  # ราคาปรับปันผลล่าสุด = ราคาจริงล่าสุด (ปรับย้อนหลังเท่านั้น)
            yields[a.ticker] = float(window.sum() / price)
            yield_source[a.ticker] = "measured"
        else:
            yields[a.ticker] = DEFAULT_YIELD_BY_KIND[a.kind]
            yield_source[a.ticker] = "kind_default"
    pr = panel[funds]
    stats = {
        "pool_range": [str(panel.index[0].date()), str(panel.index[-1].date())], "pool_months": int(len(panel)),
        "fund_cagr_pct": {f: round(float(np.expm1(pr[f].mean() * 12) * 100), 2) for f in funds},
        "fund_vol_pct": {f: round(float(pr[f].std() * math.sqrt(12) * 100), 2) for f in funds},
        "corr": pr.corr().round(2).to_dict(),
        "regime_counts": {n: int((code == i).sum()) for i, n in enumerate(REGIMES)},
        "mean_duration_months": {REGIMES[i]: round(float(1 / (1 - trans[i, i])), 1) for i in range(K)},
        "start_regime": REGIMES[int(code[-1])], "fx_start": fx0,
        "us_infl_yoy_latest_pct": round(float(infl_yoy.dropna().iloc[-1] * 100), 2),
        "ffr_latest": float(ffr.iloc[-1]), "y10_latest": float(y10.iloc[-1]), "wti_latest": float(wti.iloc[-1]),
        "vix_latest": float(vix.dropna().iloc[-1]), "proxies_live": used,
    }
    return {
        "funds": funds, "kinds": kinds, "cols": cols, "pools": pools, "trans": trans, "start_regime": int(code[-1]),
        "live_me_logs": np.log(live_me[funds]).to_numpy(), "first_valid": first_valid, "fx0": fx0,
        "th_fit": th_fit, "events_measured": ev, "yields": yields,
        "start_levels": {"ffr": float(ffr.iloc[-1]), "y10": float(y10.iloc[-1]), "oil": float(wti.iloc[-1])},
        "chron": {"X": panel.to_numpy(), "S": S_all.reindex(panel.index).to_numpy(),
                  "days": S_days.reindex(panel.index).to_numpy().astype(float), "reg": pool_regime},
        "pool_S": pool_S, "pool_days": pool_days, "live_S": live_S, "live_days": live_days,
        "stats": stats,
        "meta": {"as_of": as_of.date().isoformat(), "plan_month": str(plan_month), "last_bar": last_bar.date().isoformat(),
                 "funds": funds, "kinds": kinds, "guessed_kinds": [f for f in funds if is_guessed(f)],
                 "raw_sha256": raw.sha256, "fetched_at": raw.fetched_at, "n_pool_months": int(len(panel)),
                 "yield_source": yield_source,
                 "live_months": int(len(live_me)), "dar_ready": bool(len(live_me) >= 181)},
    }


def load_panel(tickers: list[str] | None = None, directory: Path | None = None) -> dict[str, Any]:
    """อ่านข้อมูลดิบที่ดึงไว้แล้วสร้างแผง (ไม่ยิงเน็ต) — ผู้เรียกต้องดู ``data_status`` เรื่องความเก่าเอง."""
    return build_panel(load_raw(directory), tickers)
