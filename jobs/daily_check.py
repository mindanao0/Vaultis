import os
from datetime import datetime

import requests

BACKEND_URL = os.environ.get(
    "BACKEND_URL",
    "https://vaultis-backend.onrender.com",
)
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
TICKERS = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]

SEP_LINE = "─" * 31


def fetch_daily_snapshot_from_backend() -> dict:
    url = f"{BACKEND_URL}/api/etf/daily-snapshot"
    print(f"Fetching from: {url}")
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        print(f"Got snapshot: {data}")
        return data.get("data", {}) or {}
    except Exception as e:
        print(f"Daily snapshot error: {e}")
        return {}


def fetch_prices_from_backend() -> dict:
    print(f"Fetching from: {BACKEND_URL}/api/etf/prices")
    try:
        r = requests.get(
            f"{BACKEND_URL}/api/etf/prices",
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("data", {}) or {}
    except Exception as e:
        print(f"Backend error: {e}")
        return {}


def _change_pct_yfinance(ticker: str, price_today: float) -> float:
    import yfinance as yf

    try:
        prev = yf.Ticker(ticker).fast_info["previous_close"]
        prev_f = float(prev)
        if prev_f > 0 and price_today > 0:
            return (price_today - prev_f) / prev_f * 100.0
    except Exception as e:
        print(f"{ticker} yfinance change error: {e}")
    return 0.0


def _price_yfinance(ticker: str) -> float:
    import yfinance as yf

    try:
        return float(yf.Ticker(ticker).fast_info["last_price"])
    except Exception:
        return 0.0


def _snapshot_asof_date(snapshot: dict) -> str | None:
    for t in TICKERS:
        s = snapshot.get(t)
        if isinstance(s, dict) and s.get("date"):
            return str(s["date"])
    return None


def _row_for_ticker(
    ticker: str,
    snapshot: dict,
    prices: dict,
) -> tuple[float, float]:
    """Returns (price, change_pct)."""
    snap = snapshot.get(ticker)
    if isinstance(snap, dict) and "price" in snap and "change_pct" in snap:
        try:
            return float(snap["price"]), float(snap["change_pct"])
        except (TypeError, ValueError):
            pass

    price = float(prices.get(ticker, 0.0) or 0.0)
    if price <= 0:
        price = _price_yfinance(ticker)
    chg = _change_pct_yfinance(ticker, price)
    return price, chg


def build_discord_message(snapshot: dict, prices: dict) -> str:
    asof = _snapshot_asof_date(snapshot) or datetime.now().strftime("%d/%m/%Y")
    lines = [
        f"Daily Price Check — {asof}",
        SEP_LINE,
    ]

    for ticker in TICKERS:
        price, change_pct = _row_for_ticker(ticker, snapshot, prices)
        sign = "+" if change_pct >= 0 else ""
        emoji = "🟢" if change_pct >= 0 else "🔴"
        lines.append(
            f"{ticker:<6} ${price:<8.2f} ({sign}{change_pct:.2f}%) {emoji}  ({asof})"
        )

    lines.extend(
        [
            SEP_LINE,
            "⚠️ Price Alerts: 0 items",
        ]
    )
    return "\n".join(lines)


def run():
    print("Starting daily check...")
    print(f"Backend: {BACKEND_URL}")
    print(f"Webhook: {'OK' if DISCORD_WEBHOOK_URL else 'MISSING'}")

    snapshot = fetch_daily_snapshot_from_backend()
    prices = fetch_prices_from_backend()

    if not prices and not snapshot:
        print("No data from backend - trying yfinance fallback for prices")
        import yfinance as yf

        for ticker in TICKERS:
            try:
                prices[ticker] = yf.Ticker(ticker).fast_info["last_price"]
            except Exception:
                prices[ticker] = 0.0

    message = build_discord_message(snapshot, prices)
    print(f"\nMessage:\n{message}")

    if DISCORD_WEBHOOK_URL:
        payload = {"content": f"```\n{message}\n```"}
        r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=30)
        print(f"Discord: {r.status_code}")
    else:
        print("No Discord webhook URL")


if __name__ == "__main__":
    run()
