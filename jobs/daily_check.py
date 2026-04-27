"""Daily market price check job for CLI/GitHub Actions."""

from __future__ import annotations

import os
from datetime import datetime

import requests
import yfinance as yf


TICKERS = ("VOO", "SCHD", "QQQM", "XLV", "GLDM")


def _get_last_price(ticker: str) -> float:
    """Fetch latest last_price from yfinance fast_info."""
    info = yf.Ticker(ticker).fast_info
    price = info.get("last_price")
    if price is None:
        raise ValueError(f"ไม่พบราคาปัจจุบันของ {ticker}")
    return float(price)


def run_daily_check() -> dict[str, object]:
    """Send daily ETF prices to Discord webhook from environment."""
    webhook_url = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
    if not webhook_url:
        return {"success": False, "error": "DISCORD_WEBHOOK_URL is not set"}

    prices: dict[str, float] = {}
    for ticker in TICKERS:
        prices[ticker] = _get_last_price(ticker)

    date_text = datetime.now().strftime("%Y-%m-%d")
    lines = [f"📊 Daily Check — {date_text}"] + [f"{ticker} ${prices[ticker]:.2f}" for ticker in TICKERS]
    content = "\n".join(lines)

    response = requests.post(webhook_url, json={"content": content}, timeout=20)
    response.raise_for_status()
    return {"success": True, "message": content}
