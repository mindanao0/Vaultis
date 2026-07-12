# -*- coding: utf-8 -*-
"""Price alert storage, evaluation, and Discord notification."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

from alerts.notifier import send_discord_webhook
from utils.config import load_config

ALERTS_PATH = Path(__file__).resolve().parent / "data" / "price_alerts.json"
ALLOWED_ALERT_TYPES = {"above", "below"}
DAILY_CHECK_TICKERS = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]


def _ensure_storage() -> None:
    ALERTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not ALERTS_PATH.exists():
        ALERTS_PATH.write_text(json.dumps({"alerts": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_alerts() -> list[dict[str, Any]]:
    _ensure_storage()
    try:
        payload = json.loads(ALERTS_PATH.read_text(encoding="utf-8"))
        alerts = payload.get("alerts", []) if isinstance(payload, dict) else []
        return alerts if isinstance(alerts, list) else []
    except Exception:
        return []


def _save_alerts(alerts: list[dict[str, Any]]) -> None:
    _ensure_storage()
    payload = {"alerts": alerts}
    ALERTS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _extract_latest_price(raw_data: pd.DataFrame, ticker: str) -> float | None:
    try:
        if raw_data.empty:
            return None

        if isinstance(raw_data.columns, pd.MultiIndex):
            if ticker not in raw_data.columns.get_level_values(0):
                return None
            close_series = pd.to_numeric(raw_data[ticker]["Close"], errors="coerce").dropna()
        else:
            close_series = pd.to_numeric(raw_data.get("Close"), errors="coerce").dropna()
        if close_series.empty:
            return None
        return float(close_series.iloc[-1])
    except Exception:
        return None


def get_current_prices(tickers: list[str]) -> dict[str, float]:
    """Fetch latest close prices for given tickers."""
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
        prices: dict[str, float] = {}
        for ticker in normalized:
            price = _extract_latest_price(raw, ticker)
            if price is not None:
                prices[ticker] = price
        return prices
    except Exception:
        return {}


def get_price_snapshots(tickers: list[str]) -> dict[str, dict[str, float]]:
    """Fetch latest and previous close prices for given tickers."""
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
        snapshots: dict[str, dict[str, float]] = {}
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
            previous_close = float(close_series.iloc[-2]) if len(close_series) >= 2 else latest_price
            snapshots[ticker] = {
                "latest_price": latest_price,
                "previous_close": previous_close,
            }
        return snapshots
    except Exception:
        return {}


def add_alert(ticker: str, alert_type: str, price: float, note: str = "") -> dict[str, Any]:
    """Add a new price alert."""
    normalized_ticker = str(ticker).strip().upper()
    normalized_type = str(alert_type).strip().lower()
    target_price = float(price)
    if not normalized_ticker:
        raise ValueError("ticker ห้ามว่าง")
    if normalized_type not in ALLOWED_ALERT_TYPES:
        raise ValueError("alert_type ต้องเป็น 'above' หรือ 'below'")
    if target_price <= 0:
        raise ValueError("price ต้องมากกว่า 0")

    alerts = _load_alerts()
    record = {
        "id": str(uuid.uuid4()),
        "ticker": normalized_ticker,
        "alert_type": normalized_type,
        "price": target_price,
        "note": str(note).strip(),
        "triggered": False,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "triggered_at": None,
        "triggered_price": None,
    }
    alerts.append(record)
    _save_alerts(alerts)
    return record


def add_or_update_alert(ticker: str, alert_type: str, price: float, note: str = "") -> dict[str, Any]:
    """Add new alert or update existing pending alert with same ticker+type."""
    normalized_ticker = str(ticker).strip().upper()
    normalized_type = str(alert_type).strip().lower()
    target_price = float(price)
    if not normalized_ticker:
        raise ValueError("ticker ห้ามว่าง")
    if normalized_type not in ALLOWED_ALERT_TYPES:
        raise ValueError("alert_type ต้องเป็น 'above' หรือ 'below'")
    if target_price <= 0:
        raise ValueError("price ต้องมากกว่า 0")

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

    return add_alert(ticker=normalized_ticker, alert_type=normalized_type, price=target_price, note=note)


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
        target = float(alert.get("price", 0.0))
        now_price = current_prices.get(ticker)
        distance_pct: float | None = None
        if now_price is not None and target > 0:
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
    """Delete alert by id."""
    target = str(alert_id).strip()
    if not target:
        return False
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
    snapshots: dict[str, dict[str, float]],
    triggered_items: list[dict[str, Any]],
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
        previous_close = float(snapshot["previous_close"])
        change_pct = 0.0
        if previous_close != 0:
            change_pct = ((latest_price - previous_close) / previous_close) * 100.0
        if latest_price > previous_close:
            status = "🟢"
        elif latest_price < previous_close:
            status = "🔴"
        else:
            status = "🟡"
        lines.append(f"{ticker:<4}  ${latest_price:,.2f}  {status} ({change_pct:+.2f}%)")

    lines.append("─────────────────────────────")
    lines.append(f"⚠️ Price Alerts: {len(triggered_items)} รายการ")
    return "\n".join(lines)


def check_alerts() -> dict[str, Any]:
    """Check alerts and always send a daily Discord status summary."""
    config = load_config()
    tracked_tickers = DAILY_CHECK_TICKERS.copy()
    webhook_url = str(config["notifications"]["discord_webhook_url"]).strip()
    alerts = _load_alerts()
    pending = [item for item in alerts if not bool(item.get("triggered"))]

    tickers = sorted(
        {str(item.get("ticker", "")).strip().upper() for item in pending if item.get("ticker")} | set(tracked_tickers)
    )
    snapshots = get_price_snapshots(tickers)
    latest_prices = {ticker: snapshot["latest_price"] for ticker, snapshot in snapshots.items()}

    triggered_items: list[dict[str, Any]] = []
    for alert in pending:
        ticker = str(alert.get("ticker", "")).strip().upper()
        current_price = latest_prices.get(ticker)
        if current_price is None:
            continue

        target_price = float(alert.get("price", 0.0))
        alert_type = str(alert.get("alert_type", "")).lower()
        is_triggered = (alert_type == "above" and current_price >= target_price) or (
            alert_type == "below" and current_price <= target_price
        )
        if not is_triggered:
            continue

        alert["triggered"] = True
        alert["triggered_at"] = datetime.now().isoformat(timespec="seconds")
        alert["triggered_price"] = current_price

        triggered_result = {
            "id": alert.get("id"),
            "ticker": ticker,
            "alert_type": alert_type,
            "target_price": target_price,
            "current_price": current_price,
        }
        triggered_items.append(triggered_result)

        if webhook_url:
            msg = _build_price_alert_message(alert, current_price)
            send_discord_webhook(
                webhook_url=webhook_url,
                title="Price Alert",
                description=msg,
                is_positive=(alert_type == "above"),
                embed_color=(0x2ECC71 if alert_type == "above" else 0xE74C3C),
            )

    daily_summary = _build_daily_status_message(
        tracked_tickers=tracked_tickers,
        snapshots=snapshots,
        triggered_items=triggered_items,
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

    _save_alerts(alerts)
    return {
        "success": True,
        "checked": len(pending),
        "triggered": triggered_items,
        "daily_summary": daily_summary,
        "daily_discord_result": daily_result,
    }

