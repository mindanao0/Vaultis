import os
import requests
import yfinance as yf
from datetime import datetime

TICKERS = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]


def get_price_and_change(ticker):
    t = yf.Ticker(ticker)
    fast_info = t.fast_info or {}

    current_price = fast_info.get("last_price")
    prev_close = fast_info.get("previous_close")

    if current_price is None or prev_close in (None, 0):
        hist = yf.download(ticker, period="2d", progress=False, auto_adjust=False)
        if hist.empty or "Close" not in hist.columns:
            raise ValueError("Unable to fetch close prices")

        closes = hist["Close"].dropna()
        if closes.empty:
            raise ValueError("No valid close prices")

        if current_price is None:
            current_price = float(closes.iloc[-1])

        if prev_close in (None, 0):
            if len(closes) >= 2:
                prev_close = float(closes.iloc[-2])
            else:
                prev_close = float(closes.iloc[-1])

    if not prev_close:
        pct_change = 0.0
    else:
        pct_change = ((float(current_price) - float(prev_close)) / float(prev_close)) * 100

    return float(current_price), float(pct_change)


def run():
    print("เริ่ม daily check...")

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    print(f"Webhook: {'✅' if webhook_url else '❌ ไม่มี'}")

    # ดึงราคา + % เปลี่ยนแปลง
    daily_data = {}
    for ticker in TICKERS:
        try:
            price, pct_change = get_price_and_change(ticker)
            daily_data[ticker] = {"price": price, "pct_change": pct_change}
            print(f"{ticker}: ${price:.2f} ({pct_change:+.2f}%)")
        except Exception as e:
            print(f"{ticker}: Error - {e}")
            daily_data[ticker] = {"price": 0.0, "pct_change": 0.0}

    # ส่ง Discord
    today = datetime.now().strftime("%d/%m/%Y")
    lines = [f"📊 Daily Price Check — {today}", "─" * 32]
    for ticker in TICKERS:
        price = daily_data[ticker]["price"]
        pct_change = daily_data[ticker]["pct_change"]
        color_icon = "🟢" if pct_change >= 0 else "🔴"
        pct_text = f"({pct_change:+.2f}%)"
        lines.append(f"{ticker:<5} ${price:>7.2f}  {pct_text} {color_icon}")

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
