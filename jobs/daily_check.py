import os
from datetime import datetime

import requests

BACKEND_URL = os.environ.get(
    "BACKEND_URL",
    "https://vaultis-backend.onrender.com",
)
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
TICKERS = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]


def fetch_prices_from_backend():
    print(f"Fetching from: {BACKEND_URL}/api/etf/prices")
    try:
        r = requests.get(
            f"{BACKEND_URL}/api/etf/prices",
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        print(f"Got data: {data}")
        return data.get("data", {})
    except Exception as e:
        print(f"Backend error: {e}")
        return {}


def fetch_changes_from_backend():
    try:
        r = requests.get(
            f"{BACKEND_URL}/api/etf/returns",
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("data", {})
    except Exception as e:
        print(f"Returns error: {e}")
        return {}


def run():
    print("Starting daily check...")
    print(f"Backend: {BACKEND_URL}")
    print(f"Webhook: {'OK' if DISCORD_WEBHOOK_URL else 'MISSING'}")

    prices = fetch_prices_from_backend()
    _ = fetch_changes_from_backend()

    if not prices:
        print("No prices from backend - trying yfinance fallback")
        import yfinance as yf

        for ticker in TICKERS:
            try:
                t = yf.Ticker(ticker)
                prices[ticker] = t.fast_info["last_price"]
            except Exception:
                prices[ticker] = 0.0

    today = datetime.now().strftime("%d/%m/%Y")
    lines = [f"Daily Price Check - {today}", "-" * 35]

    for ticker in TICKERS:
        price = float(prices.get(ticker, 0.0) or 0.0)
        print(f"{ticker}: {price}")
        lines.append(f"{ticker:<6} ${price:.2f}")

    lines.append("-" * 35)
    lines.append("Price Alerts: 0 items")

    message = "\n".join(lines)
    print(f"\nMessage:\n{message}")

    if DISCORD_WEBHOOK_URL:
        payload = {"content": f"```\n{message}\n```"}
        r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=30)
        print(f"Discord: {r.status_code}")
    else:
        print("No Discord webhook URL")


if __name__ == "__main__":
    run()
