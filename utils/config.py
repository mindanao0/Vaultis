# -*- coding: utf-8 -*-
"""Utilities for loading and saving application configuration.

Secrets policy (AUDIT.md H1): Discord webhook URL ห้ามเก็บใน config.json
(ไฟล์นี้ถูก track ใน git) — ให้ตั้งผ่าน env `DISCORD_WEBHOOK_URL` (.env / GitHub
Secrets / Render env) ซึ่ง `load_config()` จะ overlay ให้อัตโนมัติ
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"
load_dotenv(dotenv_path=CONFIG_PATH.parent / ".env", override=False)
DEFAULT_TICKERS = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]

DEFAULT_CONFIG: dict[str, Any] = {
    "dca": {
        "monthly_budget_thb": 5000.0,
        "day_of_month": 1,
    },
    "etf": {
        "tickers": DEFAULT_TICKERS,
    },
    "notifications": {
        "discord_webhook_url": "",
        "weekly_summary": True,
        "dca_reminder": True,
        "rsi_alert": True,
    },
    "display": {
        "default_page": "Overview",
        "currency": "THB",
        "default_fx_rate": 33.5,
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _normalize_config(raw_config: dict[str, Any]) -> dict[str, Any]:
    merged = _deep_merge(DEFAULT_CONFIG, raw_config)

    dca_day = int(merged["dca"].get("day_of_month", 1))
    merged["dca"]["day_of_month"] = max(1, min(31, dca_day))
    merged["dca"]["monthly_budget_thb"] = float(merged["dca"].get("monthly_budget_thb", 5000.0))

    raw_tickers = merged["etf"].get("tickers", [])
    normalized_tickers = [str(ticker).strip().upper() for ticker in raw_tickers if str(ticker).strip()]
    merged["etf"]["tickers"] = list(dict.fromkeys(normalized_tickers)) or deepcopy(DEFAULT_CONFIG["etf"]["tickers"])

    # env มาก่อนค่าในไฟล์เสมอ — webhook เป็น secret ไม่ควรอยู่ใน config.json
    env_webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    file_webhook = str(merged["notifications"].get("discord_webhook_url", "")).strip()
    merged["notifications"]["discord_webhook_url"] = env_webhook or file_webhook
    merged["notifications"]["weekly_summary"] = bool(merged["notifications"].get("weekly_summary", True))
    merged["notifications"]["dca_reminder"] = bool(merged["notifications"].get("dca_reminder", True))
    merged["notifications"]["rsi_alert"] = bool(merged["notifications"].get("rsi_alert", True))

    default_page = str(merged["display"].get("default_page", "Overview")).strip() or "Overview"
    merged["display"]["default_page"] = default_page
    currency = str(merged["display"].get("currency", "THB")).upper()
    merged["display"]["currency"] = currency if currency in {"THB", "USD"} else "THB"
    merged["display"]["default_fx_rate"] = float(merged["display"].get("default_fx_rate", 33.5))

    return merged


def load_config() -> dict[str, Any]:
    """Load configuration from config.json and fill missing defaults."""
    if not CONFIG_PATH.exists():
        return deepcopy(DEFAULT_CONFIG)

    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return deepcopy(DEFAULT_CONFIG)
        return _normalize_config(payload)
    except Exception:
        return deepcopy(DEFAULT_CONFIG)


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    """Save configuration into config.json and return normalized config.

    webhook ไม่ถูกเขียนลงไฟล์เด็ดขาด (ไฟล์นี้อยู่ใน git — เคยหลุดมาแล้ว, AUDIT.md H1)
    ค่า runtime ยังใช้ได้ปกติผ่าน env overlay ใน load_config()
    """
    normalized = _normalize_config(config if isinstance(config, dict) else {})
    on_disk = deepcopy(normalized)
    on_disk["notifications"]["discord_webhook_url"] = ""
    CONFIG_PATH.write_text(json.dumps(on_disk, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return normalized


def get_tickers() -> list[str]:
    """Return ETF tickers from config.json."""
    return list(load_config()["etf"]["tickers"])


def add_ticker(ticker: str) -> list[str]:
    """Add a ticker to config.json and return updated list."""
    normalized_ticker = str(ticker).strip().upper()
    if not normalized_ticker:
        raise ValueError("ticker ห้ามว่าง")

    config = load_config()
    tickers = list(config["etf"]["tickers"])
    if normalized_ticker not in tickers:
        tickers.append(normalized_ticker)
    config["etf"]["tickers"] = tickers
    saved = save_config(config)
    return list(saved["etf"]["tickers"])


def remove_ticker(ticker: str) -> list[str]:
    """Remove a ticker from config.json and return updated list."""
    normalized_ticker = str(ticker).strip().upper()
    config = load_config()
    tickers = [item for item in config["etf"]["tickers"] if item != normalized_ticker]
    if not tickers:
        raise ValueError("ต้องมี ETF อย่างน้อย 1 ตัว")
    config["etf"]["tickers"] = tickers
    saved = save_config(config)
    return list(saved["etf"]["tickers"])
