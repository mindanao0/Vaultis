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


def list_alerts(include_triggered: bool = True) -> list[dict[str, Any]]:
    """List alerts from storage."""
    alerts = _load_alerts()
    if include_triggered:
        return alerts
    return [item for item in alerts if not bool(item.get("triggered"))]


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


def check_alerts() -> dict[str, Any]:
    """Check all pending alerts and send Discord notification on trigger."""
    alerts = _load_alerts()
    pending = [item for item in alerts if not bool(item.get("triggered"))]
    if not pending:
        return {"success": True, "checked": 0, "triggered": []}

    tickers = sorted({str(item.get("ticker", "")).strip().upper() for item in pending if item.get("ticker")})
    latest_prices = get_current_prices(tickers)
    webhook_url = str(load_config()["notifications"]["discord_webhook_url"]).strip()

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

    _save_alerts(alerts)
    return {"success": True, "checked": len(pending), "triggered": triggered_items}

