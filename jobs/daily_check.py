import os
import random
import time
import requests
import yfinance as yf
import pandas as pd
from datetime import datetime, timezone

TICKERS = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]

session = requests.Session()
session.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
)


def _fetch_price_from_chart_api(ticker):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    response.raise_for_status()
    data = response.json()
    meta = data["chart"]["result"][0]["meta"]
    timestamps = data["chart"]["result"][0].get("timestamp", [])
    price = float(meta["regularMarketPrice"])
    prev = float(meta["previousClose"])
    change_pct = (price - prev) / prev * 100 if prev else 0.0
    if timestamps:
        last_dt = datetime.fromtimestamp(timestamps[-1], tz=timezone.utc)
        date_str = last_dt.strftime("%d/%m/%Y")
    else:
        date_str = datetime.now(timezone.utc).strftime("%d/%m/%Y")
    return price, change_pct, date_str


def fetch_price_with_retry(ticker, max_retries=3):
    for attempt in range(max_retries):
        try:
            # Randomized jitter helps avoid concurrent request bursts.
            time.sleep(random.uniform(1, 3))
            t = yf.Ticker(ticker, session=session)
            fast_info = t.fast_info
            price = fast_info.get("last_price")
            prev = fast_info.get("previous_close")
            if price and prev and price > 0:
                change_pct = (price - prev) / prev * 100
                hist = t.history(period="5d", interval="1d", auto_adjust=False)
                if not hist.empty:
                    latest_idx = pd.to_datetime(hist.index[-1]).to_pydatetime()
                    date_str = latest_idx.strftime("%d/%m/%Y")
                else:
                    date_str = datetime.now(timezone.utc).strftime("%d/%m/%Y")
                return float(price), float(change_pct), date_str
            raise ValueError("Invalid fast_info data")
        except Exception as e:
            print(f"{ticker} attempt {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                time.sleep(3)

    try:
        return _fetch_price_from_chart_api(ticker)
    except Exception as e:
        print(f"{ticker} fallback failed: {e}")
        return 0.0, 0.0, "N/A"


def is_market_open():
    now = datetime.now(timezone.utc)
    if now.weekday() > 4:
        return False
    market_open = now.replace(hour=14, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=21, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close


def run():
    print("เริ่ม daily check...")

    # GitHub Actions ส่งมาเป็น env var โดยตรง
    # ไม่ต้องใช้ dotenv
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")

    # dotenv เฉพาะตอนรันในเครื่องเท่านั้น
    if not webhook_url:
        from dotenv import load_dotenv
        from pathlib import Path

        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
        webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")

    print(f"Webhook: {'✅' if webhook_url else '❌ ไม่มี'}")

    # ดึงราคา + % เปลี่ยนแปลง
    daily_data = {}
    for idx, ticker in enumerate(TICKERS):
        try:
            price, pct_change, price_date = fetch_price_with_retry(ticker)
            daily_data[ticker] = {
                "price": price,
                "pct_change": pct_change,
                "price_date": price_date,
            }
            print(f"{ticker}: ${price:.2f} ({pct_change:+.2f}%) [{price_date}]")
        except Exception as e:
            print(f"{ticker}: Error - {e}")
            daily_data[ticker] = {"price": 0.0, "pct_change": 0.0, "price_date": "N/A"}
        if idx < len(TICKERS) - 1:
            time.sleep(2)

    # ส่ง Discord
    now_utc = datetime.now(timezone.utc)
    today = now_utc.strftime("%d/%m/%Y")
    lines = [f"📊 Daily Price Check — {today}", "─" * 32]
    for ticker in TICKERS:
        price = daily_data[ticker]["price"]
        pct_change = daily_data[ticker]["pct_change"]
        price_date = daily_data[ticker]["price_date"]
        color_icon = "🟢" if pct_change >= 0 else "🔴"
        pct_text = f"({pct_change:+.2f}%)"
        if price in (None, 0):
            lines.append(f"{ticker:<5} $0.00  (+0.00%) 🟢  ({price_date})")
        else:
            lines.append(f"{ticker:<5} ${price:>7.2f}  {pct_text} {color_icon}  ({price_date})")

    lines.extend(["─" * 32, "⚠️ Price Alerts: 0 รายการ"])
    message = "\n".join(lines)
    print(f"\nส่งข้อความ:\n{message}")

    if webhook_url:
        if not webhook_url.startswith(("http://", "https://")):
            print("❌ DISCORD_WEBHOOK_URL ไม่ถูกต้อง (ต้องขึ้นต้นด้วย http/https)")
            return
        payload = {"content": f"```\n{message}\n```"}
        try:
            r = requests.post(webhook_url, json=payload, timeout=15)
            print(f"Discord status: {r.status_code}")
        except requests.RequestException as e:
            # Avoid failing CI job when webhook/network is temporarily unavailable.
            print(f"Discord send failed: {e}")
    else:
        print("❌ ไม่มี DISCORD_WEBHOOK_URL")

if __name__ == "__main__":
    run()
