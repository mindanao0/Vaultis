# -*- coding: utf-8 -*-
"""Price alert storage, evaluation, and Discord notification."""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import pandas as pd
import yfinance as yf

from alerts.notifier import send_discord_webhook
from data.fallback import get_latest_prices_with_fallback
from data.fetcher import PriceDataUnavailableError
from utils.config import load_config

try:  # POSIX เท่านั้น — โปรเจกต์นี้รันบน Linux/Docker
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

ALERTS_PATH = Path(__file__).resolve().parent / "data" / "price_alerts.json"
ALLOWED_ALERT_TYPES = {"above", "below"}
DAILY_CHECK_TICKERS = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]


class AlertStoreUnavailable(RuntimeError):
    """อ่านคลัง alert ไม่ได้ (ไฟล์เสีย/สิทธิ์ไม่พอ/ผิดรูปแบบ).

    **ห้ามแปลงเป็นลิสต์ว่าง** — "อ่านไม่ได้" กับ "ไม่มี alert" คนละความหมาย
    เดิม ``_load_alerts()`` มี ``except Exception: return []`` แล้ว ``check_alerts()``
    เขียนลิสต์ว่างนั้นกลับลงไฟล์ ⇒ alert ของผู้ใช้หายถาวร (AUDIT_2026-08-06 ข้อ H2)
    """


def _lock_path() -> Path:
    return ALERTS_PATH.with_name(ALERTS_PATH.name + ".lock")


def _backup_path() -> Path:
    return ALERTS_PATH.with_name(ALERTS_PATH.name + ".bak")


@contextmanager
def _store_lock() -> Iterator[None]:
    """กันคนละโปรเซสเขียนทับกัน (backend / dashboard / scheduler แชร์ bind mount เดียวกัน).

    ล็อกอยู่บนไฟล์ ``.lock`` แยกต่างหาก ไม่ใช่ตัวไฟล์ข้อมูล เพราะ ``os.replace``
    เปลี่ยน inode ของไฟล์ข้อมูลทุกครั้งที่บันทึก — ล็อกบน inode เดิมจะไม่กันอะไรเลย

    ห้ามซ้อนกันในโปรเซสเดียว: ``flock`` ผูกกับ open file description
    เปิดซ้ำแล้วขอ ``LOCK_EX`` อีกครั้งจะบล็อกตัวเอง — ฟังก์ชันที่ถือล็อกอยู่แล้วจึงต้อง
    เรียก ``_load_alerts``/``_save_alerts`` ตรง ๆ ห้ามเรียก ``add_alert`` ฯลฯ ซ้อนข้างใน
    """
    ALERTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if fcntl is None:  # pragma: no cover - Windows
        yield
        return
    with open(_lock_path(), "a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except OSError as exc:  # ระบบไฟล์ที่ไม่รองรับ flock — เดินต่อแต่ต้องเห็นในล็อก
            logger.warning("ล็อกคลัง alert ไม่ได้ (%s) — เขียนต่อโดยไม่มีล็อก", exc)
            yield
            return
        try:
            yield
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:  # pragma: no cover
                pass


def _load_alerts() -> list[dict[str, Any]]:
    """อ่านคลัง alert.

    - ไม่มีไฟล์ = ยังไม่เคยตั้ง alert → ลิสต์ว่างจริง (และ **ไม่สร้างไฟล์ให้เอง**
      ไม่งั้น "เครื่องนี้มองไม่เห็นคลัง" จะกลายเป็น "คุณไม่มี alert" — ข้อ D1.3)
    - อ่าน/แปลงไม่ได้ → ``AlertStoreUnavailable`` (ห้ามคืนลิสต์ว่าง)
    - มีแถวที่ไม่ใช่ระเบียน alert → ``AlertStoreUnavailable`` เช่นกัน **ห้ามกรองทิ้งเงียบ ๆ**
      เพราะการบันทึกครั้งถัดไปจะเขียนลิสต์ที่กรองแล้วทับไฟล์ = ลบแถวนั้นถาวร
      ("ตัดข้อมูลทิ้งเงียบ" ผิดพอกับ "กุตัวเลข")
    """
    if not ALERTS_PATH.exists():
        return []
    try:
        raw = ALERTS_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise AlertStoreUnavailable(f"เปิดไฟล์คลัง alert ไม่ได้ ({ALERTS_PATH}): {exc}") from exc
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise AlertStoreUnavailable(
            f"ไฟล์คลัง alert เสียหาย อ่าน JSON ไม่ออก ({ALERTS_PATH}): {exc}"
        ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("alerts"), list):
        raise AlertStoreUnavailable(f"ไฟล์คลัง alert ผิดรูปแบบ ต้องเป็น {{'alerts': [...]}} ({ALERTS_PATH})")
    rows = payload["alerts"]
    broken = [index for index, item in enumerate(rows) if not isinstance(item, dict)]
    if broken:
        raise AlertStoreUnavailable(
            f"ไฟล์คลัง alert มีแถวที่ไม่ใช่ระเบียน alert ที่ตำแหน่ง {broken} ({ALERTS_PATH}) — "
            "ระบบไม่ตัดทิ้งให้เอง เพราะการบันทึกครั้งถัดไปจะลบแถวเหล่านั้นถาวร"
        )
    return list(rows)


def _save_alerts(alerts: list[dict[str, Any]]) -> None:
    """บันทึกแบบ atomic: เขียน ``.tmp`` → fsync → สำรอง ``.bak`` → ``os.replace``.

    เดิมใช้ ``write_text()`` ตรง ๆ — ถูกขัดจังหวะกลางคันเมื่อไหร่ไฟล์เสียทันที
    ซึ่งเป็นกลไกที่ทำให้เกิดอาการ H2 ตั้งแต่แรก
    """
    ALERTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"alerts": alerts}, ensure_ascii=False, indent=2) + "\n"
    tmp_path = ALERTS_PATH.with_name(f"{ALERTS_PATH.name}.tmp.{os.getpid()}")
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if ALERTS_PATH.exists():
            shutil.copy2(ALERTS_PATH, _backup_path())
        os.replace(tmp_path, ALERTS_PATH)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover
            pass


def get_store_status() -> dict[str, Any]:
    """สถานะคลัง alert สำหรับข้อความสรุป — แยก 3 กรณีที่เดิมยุบเป็น "0 รายการ" เหมือนกันหมด.

    ``missing`` = สภาพแวดล้อมนี้ไม่มีไฟล์คลังเลย (เช่น GitHub Actions ที่ไฟล์ถูก gitignore)
    ``error``   = มีไฟล์แต่อ่านไม่ได้ · ``ok`` = อ่านได้ พร้อมจำนวนจริง
    """
    path_text = str(ALERTS_PATH)
    if not ALERTS_PATH.exists():
        return {"status": "missing", "path": path_text, "pending": None, "triggered": None, "error": None}
    try:
        alerts = _load_alerts()
    except AlertStoreUnavailable as exc:
        return {"status": "error", "path": path_text, "pending": None, "triggered": None, "error": str(exc)}
    pending = [item for item in alerts if not bool(item.get("triggered"))]
    return {
        "status": "ok",
        "path": path_text,
        "pending": len(pending),
        "triggered": len(alerts) - len(pending),
        "error": None,
    }


def get_current_prices(tickers: list[str]) -> dict[str, float]:
    """Fetch latest close prices for given tickers.

    ใช้ ``data/fallback`` (yfinance → Stooq → Alpha Vantage) เพื่อให้ cron/dashboard/
    rebalance ยังได้ราคาล่าสุดตอน yfinance ล่ม — คง contract เดิมทุกอย่าง:
    ticker ที่ดึงไม่ได้หายไปจากผล และคืน ``{}`` เมื่อดึงไม่ได้ทั้งหมด
    (fallback ใช้กับราคาล่าสุดเท่านั้น ห้ามใช้กับ series ประวัติที่คำนวณ score)
    """
    try:
        return get_latest_prices_with_fallback(tickers)
    except PriceDataUnavailableError as exc:
        logger.warning("get_current_prices: %s", exc)
        return {}


def get_price_snapshots(tickers: list[str]) -> dict[str, dict[str, float | None]]:
    """Fetch latest and previous close prices for given tickers.

    ``previous_close`` เป็น ``None`` เมื่อมีแท่งปิดแท่งเดียว — เดิมยัดราคาตัวเองลงไป
    ทำให้ข้อความสรุปพิมพ์ ``🟡 (+0.00%)`` = "ราคาไม่เปลี่ยน" จากข้อมูลที่ไม่มีจริง
    (AUDIT_2026-08-06 ข้อ D1.2)
    """
    normalized = sorted({str(t).strip().upper() for t in tickers if str(t).strip()})
    if not normalized:
        return {}
    try:
        raw = yf.download(
            tickers=normalized,
            period="5d",
            interval="1d",
            auto_adjust=False,
            progress=False,
            group_by="ticker",
        )
        snapshots: dict[str, dict[str, float | None]] = {}
        for ticker in normalized:
            if raw.empty:
                continue
            if isinstance(raw.columns, pd.MultiIndex):
                if ticker not in raw.columns.get_level_values(0):
                    continue
                close_series = pd.to_numeric(raw[ticker]["Close"], errors="coerce").dropna()
            else:
                close_series = pd.to_numeric(raw.get("Close"), errors="coerce").dropna()
            if close_series.empty:
                continue

            latest_price = float(close_series.iloc[-1])
            previous_close = float(close_series.iloc[-2]) if len(close_series) >= 2 else None
            snapshots[ticker] = {
                "latest_price": latest_price,
                "previous_close": previous_close,
            }
        return snapshots
    except Exception as exc:
        # ticker ที่หายไปจากผลถูกรายงานต่อเป็น unchecked/⚠️ ที่ปลายทาง ไม่ใช่ "ไม่มีอะไรเกิดขึ้น"
        logger.warning("get_price_snapshots(%s) ล้มเหลว: %s", normalized, exc)
        return {}


def _validate_alert_input(ticker: str, alert_type: str, price: float) -> tuple[str, str, float]:
    normalized_ticker = str(ticker).strip().upper()
    normalized_type = str(alert_type).strip().lower()
    target_price = float(price)
    if not normalized_ticker:
        raise ValueError("ticker ห้ามว่าง")
    if normalized_type not in ALLOWED_ALERT_TYPES:
        raise ValueError("alert_type ต้องเป็น 'above' หรือ 'below'")
    # NaN เทียบกับอะไรก็ได้ False — ``price <= 0`` ดักไม่ได้ ส่วน inf ผ่านทั้ง pydantic (gt=0)
    # และด่านนี้ แล้วถูกเขียนลงไฟล์เป็น token `NaN`/`Infinity` ที่ไม่ใช่ JSON มาตรฐาน
    # ⇒ หลังจากนั้น GET /api/alerts ตอบ 500 ทุกครั้ง (starlette ใช้ allow_nan=False)
    if not math.isfinite(target_price):
        raise ValueError("price ต้องเป็นตัวเลขจำกัด (ไม่ใช่ NaN/Infinity)")
    if target_price <= 0:
        raise ValueError("price ต้องมากกว่า 0")
    return normalized_ticker, normalized_type, target_price


def _new_alert_record(ticker: str, alert_type: str, price: float, note: str) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "ticker": ticker,
        "alert_type": alert_type,
        "price": price,
        "note": str(note).strip(),
        "triggered": False,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "triggered_at": None,
        "triggered_price": None,
    }


def add_alert(ticker: str, alert_type: str, price: float, note: str = "") -> dict[str, Any]:
    """Add a new price alert.

    โหลดคลังไม่สำเร็จ = ``AlertStoreUnavailable`` และ **ไม่เขียนอะไรกลับ**
    (เดิมจะเขียนคลังใหม่ทับของเดิมที่อ่านไม่ออก)
    """
    normalized_ticker, normalized_type, target_price = _validate_alert_input(ticker, alert_type, price)
    record = _new_alert_record(normalized_ticker, normalized_type, target_price, note)
    with _store_lock():
        alerts = _load_alerts()
        alerts.append(record)
        _save_alerts(alerts)
    return record


def add_or_update_alert(ticker: str, alert_type: str, price: float, note: str = "") -> dict[str, Any]:
    """Add new alert or update existing pending alert with same ticker+type."""
    normalized_ticker, normalized_type, target_price = _validate_alert_input(ticker, alert_type, price)

    # ทำงานทั้งก้อนใต้ล็อกเดียว — ห้ามเรียก add_alert() ซ้อนข้างใน (flock ซ้อนในโปรเซสเดียว = บล็อกตัวเอง)
    with _store_lock():
        alerts = _load_alerts()
        for item in alerts:
            if bool(item.get("triggered")):
                continue
            if str(item.get("ticker", "")).strip().upper() != normalized_ticker:
                continue
            if str(item.get("alert_type", "")).strip().lower() != normalized_type:
                continue
            item["price"] = target_price
            item["note"] = str(note).strip()
            item["updated_at"] = datetime.now().isoformat(timespec="seconds")
            _save_alerts(alerts)
            return item

        record = _new_alert_record(normalized_ticker, normalized_type, target_price, note)
        alerts.append(record)
        _save_alerts(alerts)
        return record


def list_alerts(include_triggered: bool = True) -> list[dict[str, Any]]:
    """List alerts from storage."""
    alerts = _load_alerts()
    if include_triggered:
        return alerts
    return [item for item in alerts if not bool(item.get("triggered"))]


def get_active_alerts_with_distance(near_threshold_pct: float = 2.0) -> list[dict[str, Any]]:
    """Return pending alerts with current price distance and near-trigger flag."""
    pending = list_alerts(include_triggered=False)
    if not pending:
        return []
    tickers = sorted({str(item.get("ticker", "")).strip().upper() for item in pending if item.get("ticker")})
    current_prices = get_current_prices(tickers)
    rows: list[dict[str, Any]] = []
    for alert in pending:
        ticker = str(alert.get("ticker", "")).strip().upper()
        alert_type = str(alert.get("alert_type", "")).strip().lower()
        try:
            target = float(alert.get("price"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            # แถวเสียต้องไม่ทำให้ทั้งรายการพัง (ปลายทางคือ GET /api/alerts และหน้าแดชบอร์ด)
            target = float("nan")
        now_price = current_prices.get(ticker)
        distance_pct: float | None = None
        if now_price is not None and math.isfinite(target) and target > 0:
            if alert_type == "below":
                distance_pct = ((now_price - target) / target) * 100.0
            elif alert_type == "above":
                distance_pct = ((target - now_price) / target) * 100.0
        is_near = bool(distance_pct is not None and 0 <= distance_pct <= near_threshold_pct)
        rows.append(
            {
                **alert,
                "current_price": now_price,
                "distance_pct": distance_pct,
                "is_near_trigger": is_near,
            }
        )
    return rows


def delete_alert(alert_id: str) -> bool:
    """Delete alert by id. โหลดคลังไม่สำเร็จ → raise (ห้ามลบจากลิสต์ว่างที่กุขึ้นมา)."""
    target = str(alert_id).strip()
    if not target:
        return False
    with _store_lock():
        alerts = _load_alerts()
        kept = [item for item in alerts if str(item.get("id")) != target]
        if len(kept) == len(alerts):
            return False
        _save_alerts(kept)
    return True


def _build_price_alert_message(alert: dict[str, Any], current_price: float) -> str:
    condition_text = "สูงกว่า" if str(alert.get("alert_type")) == "above" else "ต่ำกว่า"
    note = str(alert.get("note", "")).strip() or "-"
    timestamp = datetime.now().strftime("%d/%m/%Y %H:%M")
    return (
        "🎯 Price Alert Triggered!\n"
        "─────────────────────────\n"
        f"📌 {alert.get('ticker', '-')} ราคา{condition_text} ${float(alert.get('price', 0.0)):,.2f}\n"
        f"💵 ราคาปัจจุบัน: ${current_price:,.2f}\n"
        f"📝 หมายเหตุ: {note}\n"
        f"⏰ เวลา: {timestamp}"
    )


def _build_daily_status_message(
    tracked_tickers: list[str],
    snapshots: dict[str, dict[str, float | None]],
    triggered_items: list[dict[str, Any]],
    unchecked: list[dict[str, Any]] | None = None,
) -> str:
    date_text = datetime.now().strftime("%d/%m/%Y")
    lines = [
        f"📊 Daily Price Check — {date_text}",
        "─────────────────────────────",
    ]
    for ticker in tracked_tickers:
        snapshot = snapshots.get(ticker)
        if not snapshot:
            # เดิมโชว์ 🟡 (0.00%) ทำให้ดูเหมือน "ราคาไม่เปลี่ยน" ทั้งที่ดึงข้อมูลไม่ได้ (AUDIT.md C1)
            lines.append(f"{ticker:<4}  ⚠️ ดึงราคาไม่ได้")
            continue

        latest_price = float(snapshot["latest_price"])
        previous_close = snapshot.get("previous_close")
        if previous_close is None or float(previous_close) <= 0:
            # มีแท่งปิดแท่งเดียว = ไม่รู้ราคาก่อนหน้า — ห้ามพิมพ์ +0.00% (AUDIT_2026-08-06 ข้อ D1.2)
            lines.append(f"{ticker:<4}  ${latest_price:,.2f}  ⚠️ ดึง %เปลี่ยนแปลงไม่ได้")
            continue

        previous_close = float(previous_close)
        change_pct = ((latest_price - previous_close) / previous_close) * 100.0
        if latest_price > previous_close:
            status = "🟢"
        elif latest_price < previous_close:
            status = "🔴"
        else:
            status = "🟡"
        lines.append(f"{ticker:<4}  ${latest_price:,.2f}  {status} ({change_pct:+.2f}%)")

    lines.append("─────────────────────────────")
    lines.append(f"⚠️ Price Alerts: trigger {len(triggered_items)} รายการ")
    if unchecked:
        # เหตุผลไม่ได้มีชนิดเดียว (ดึงราคาไม่ได้ / แถวเสีย / เพิ่งเพิ่มระหว่างรอบ)
        # เดิมพิมพ์ "ไม่มีราคา" ให้ทุกกรณี = บอกสาเหตุผิดชนิด
        detail = ", ".join(
            f"{row.get('ticker', '-')} ({row.get('reason', 'ไม่ทราบสาเหตุ')})" for row in unchecked
        )
        lines.append(f"⚠️ ตรวจไม่ได้ {len(unchecked)} รายการ — {detail}")
    return "\n".join(lines)


def _store_failure_result(webhook_url: str, exc: AlertStoreUnavailable) -> dict[str, Any]:
    """โหลดคลังไม่สำเร็จ = หยุดทั้งรอบ ไม่เขียนอะไรกลับ และต้องบอกผู้ใช้ให้รู้."""
    logger.error("check_alerts: อ่านคลัง alert ไม่ได้ — ข้ามการตรวจรอบนี้: %s", exc)
    message = (
        "🚨 อ่านคลัง Price Alert ไม่ได้ — ข้ามการตรวจรอบนี้ทั้งหมด\n"
        "─────────────────────────────\n"
        f"ไฟล์: {ALERTS_PATH}\n"
        f"สาเหตุ: {exc}\n"
        "ระบบ **ไม่ได้** เขียนทับไฟล์ของคุณ — สำเนาก่อนหน้าอยู่ที่ "
        f"{_backup_path().name} (ถ้ามี)\n"
        "⚠️ นี่ไม่ได้แปลว่าไม่มี alert ค้าง แต่แปลว่าตรวจไม่ได้"
    )
    discord_result: dict[str, Any] = {"success": False, "skipped": True, "error": "missing webhook_url"}
    if webhook_url:
        discord_result = send_discord_webhook(
            webhook_url=webhook_url,
            title="Price Alert Store Error",
            description=message,
            is_positive=False,
            embed_color=0xE74C3C,
        )
    return {
        "success": False,
        "store_error": True,
        "error": str(exc),
        "checked": 0,
        "triggered": [],
        "unchecked": [],
        "daily_summary": message,
        "daily_discord_result": discord_result,
    }


def check_alerts() -> dict[str, Any]:
    """Check alerts and always send a daily Discord status summary.

    ลำดับสำคัญ 2 อย่าง:

    1. อ่านคลังไม่ได้ → หยุดทันที **ห้ามเขียนกลับ** (เดิมเขียนลิสต์ว่างทับ = alert หายถาวร)
    2. ยิงราคา (ช้า ~1 วิ) **นอก** ล็อก แล้วค่อยอ่านคลังใหม่ใต้ล็อกก่อนตัดสิน/บันทึก —
       ไม่งั้น alert ที่ผู้ใช้เพิ่มจากแดชบอร์ดระหว่างนั้นถูกเขียนทับหาย (lost update)
    """
    config = load_config()
    tracked_tickers = DAILY_CHECK_TICKERS.copy()
    webhook_url = str(config["notifications"]["discord_webhook_url"]).strip()

    try:
        alerts_preview = _load_alerts()
    except AlertStoreUnavailable as exc:
        return _store_failure_result(webhook_url, exc)

    pending_tickers = {
        str(item.get("ticker", "")).strip().upper()
        for item in alerts_preview
        if item.get("ticker") and not bool(item.get("triggered"))
    }
    requested_tickers = sorted(pending_tickers | set(tracked_tickers))
    snapshots = get_price_snapshots(requested_tickers)
    latest_prices = {ticker: snapshot["latest_price"] for ticker, snapshot in snapshots.items()}

    triggered_items: list[dict[str, Any]] = []
    triggered_records: list[tuple[dict[str, Any], float]] = []
    unchecked: list[dict[str, Any]] = []
    checked = 0

    with _store_lock():
        try:
            alerts = _load_alerts()
        except AlertStoreUnavailable as exc:
            return _store_failure_result(webhook_url, exc)

        pending = [item for item in alerts if not bool(item.get("triggered"))]
        for alert in pending:
            ticker = str(alert.get("ticker", "")).strip().upper()
            if not ticker:
                unchecked.append({"id": alert.get("id"), "ticker": "-", "reason": "alert ไม่มี ticker"})
                continue
            current_price = latest_prices.get(ticker)
            if current_price is None:
                # "ดึงราคาไม่ได้" ≠ "ตรวจแล้วยังไม่ถึงเงื่อนไข" — ต้องรายงานออกไป (ข้อ D1.1)
                # และ "ไม่เคยขอราคาให้" (alert ที่เพิ่งถูกเพิ่มระหว่างรอบ) ก็ไม่ใช่ "ขอแล้วดึงไม่ได้"
                reason = (
                    "ดึงราคาไม่ได้"
                    if ticker in requested_tickers
                    else "เพิ่มระหว่างรอบตรวจ — ยังไม่ได้ขอราคา จะตรวจในรอบถัดไป"
                )
                unchecked.append({"id": alert.get("id"), "ticker": ticker, "reason": reason})
                continue

            # ราคาเป้าหมาย/ชนิดเงื่อนไขที่ใช้ไม่ได้ ≠ "ตรวจแล้วยังไม่ถึงเงื่อนไข"
            # เดิม ``float(alert.get("price", 0.0))`` เปลี่ยนแถวที่ไม่มีคีย์ราคาให้เป็นเป้า 0.0
            # ⇒ alert ชนิด above trigger ทันทีที่ราคาเป็นบวก (แจ้งเตือนที่ไม่มีมูล + ปิดตัวเองถาวร)
            # และ ``float("ห้าร้อย")`` โยน ValueError ออกไปทั้งรอบ (alert อื่นไม่ถูกตรวจเลย)
            alert_type = str(alert.get("alert_type", "")).strip().lower()
            try:
                target_price = float(alert.get("price"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                target_price = float("nan")
            if (
                alert_type not in ALLOWED_ALERT_TYPES
                or not math.isfinite(target_price)
                or target_price <= 0
            ):
                unchecked.append(
                    {
                        "id": alert.get("id"),
                        "ticker": ticker,
                        "reason": "แถว alert ใช้ไม่ได้ (ราคาเป้าหมายหรือชนิดเงื่อนไขไม่ถูกต้อง) — ต้องแก้ในคลังเอง",
                    }
                )
                continue

            checked += 1
            is_triggered = (alert_type == "above" and current_price >= target_price) or (
                alert_type == "below" and current_price <= target_price
            )
            if not is_triggered:
                continue

            alert["triggered"] = True
            alert["triggered_at"] = datetime.now().isoformat(timespec="seconds")
            alert["triggered_price"] = current_price
            triggered_items.append(
                {
                    "id": alert.get("id"),
                    "ticker": ticker,
                    "alert_type": alert_type,
                    "target_price": target_price,
                    "current_price": current_price,
                }
            )
            triggered_records.append((alert, current_price))

        # เขียนเฉพาะตอนที่มีอะไรเปลี่ยนจริง — การเขียนทุกครั้งคือความเสี่ยงไฟล์เสียโดยไม่ได้อะไร
        if triggered_items:
            _save_alerts(alerts)

    # ยิง Discord หลังปล่อยล็อกและหลังบันทึกแล้ว — ไม่ถือล็อกคร่อม network I/O
    if webhook_url:
        for alert, current_price in triggered_records:
            alert_type = str(alert.get("alert_type", "")).lower()
            send_discord_webhook(
                webhook_url=webhook_url,
                title="Price Alert",
                description=_build_price_alert_message(alert, current_price),
                is_positive=(alert_type == "above"),
                embed_color=(0x2ECC71 if alert_type == "above" else 0xE74C3C),
            )

    summary_tickers = tracked_tickers + sorted(pending_tickers - set(tracked_tickers))
    daily_summary = _build_daily_status_message(
        tracked_tickers=summary_tickers,
        snapshots=snapshots,
        triggered_items=triggered_items,
        unchecked=unchecked,
    )
    daily_result: dict[str, Any] = {"success": False, "skipped": True, "error": "missing webhook_url"}
    if webhook_url:
        daily_result = send_discord_webhook(
            webhook_url=webhook_url,
            title="Daily Price Check",
            description=daily_summary,
            is_positive=(len(triggered_items) == 0),
            embed_color=(0x3498DB if len(triggered_items) == 0 else 0xE67E22),
        )

    return {
        "success": True,
        "store_error": False,
        "checked": checked,
        "triggered": triggered_items,
        "unchecked": unchecked,
        "daily_summary": daily_summary,
        "daily_discord_result": daily_result,
    }

