import os
import random
import time
import requests
import yfinance as yf
from datetime import datetime, timezone

TICKERS = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]


def fetch_price_with_retry(ticker, max_retries=3):
    for attempt in range(max_retries):
        try:
            # Randomized jitter helps avoid concurrent request bursts.
            time.sleep(random.uniform(1, 3))
            hist = yf.download(
                ticker,
                period="2d",
                progress=False,
                auto_adjust=True,
            )
            if hist.empty or "Close" not in hist.columns:
                raise ValueError("No data")

            closes = hist["Close"].dropna()
            if closes.empty:
                raise ValueError("No valid close prices")

            price = float(closes.iloc[-1])
            prev = float(closes.iloc[-2]) if len(closes) >= 2 else float(closes.iloc[-1])
            change_pct = (price - prev) / prev * 100 if prev else 0.0
            return price, prev, change_pct
        except Exception as e:
            print(f"{ticker} attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1))

    return 0.0, 0.0, 0.0


def is_market_open():
    now = datetime.now(timezone.utc)
    if now.weekday() > 4:
        return False
    market_open = now.replace(hour=14, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=21, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close


def run():
    print("เริ่ม daily check...")

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    print(f"Webhook: {'✅' if webhook_url else '❌ ไม่มี'}")

    market_open_now = is_market_open()
    # ดึงราคา + % เปลี่ยนแปลง
    daily_data = {}
    for idx, ticker in enumerate(TICKERS):
        try:
            price, prev_close, pct_change = fetch_price_with_retry(ticker)
            daily_data[ticker] = {
                "price": price,
                "prev_close": prev_close,
                "pct_change": pct_change,
            }
            print(f"{ticker}: ${price:.2f} ({pct_change:+.2f}%)")
        except Exception as e:
            print(f"{ticker}: Error - {e}")
            daily_data[ticker] = {"price": 0.0, "prev_close": 0.0, "pct_change": 0.0}
        if idx < len(TICKERS) - 1:
            time.sleep(2)

    # ส่ง Discord
    now_utc = datetime.now(timezone.utc)
    today = now_utc.strftime("%d/%m/%Y")
    as_of_text = now_utc.strftime("%d/%m/%Y %H:%M UTC")
    lines = [f"📊 Daily Price Check — {today}", "─" * 32]
    for ticker in TICKERS:
        price = daily_data[ticker]["price"]
        prev_close = daily_data[ticker]["prev_close"]
        pct_change = daily_data[ticker]["pct_change"]
        color_icon = "🟢" if pct_change >= 0 else "🔴"
        pct_text = f"({pct_change:+.2f}%)"
        if (not market_open_now) or price in (None, 0):
            latest_price = prev_close if prev_close not in (None, 0) else 0.0
            lines.append(
                f"{ticker:<5} ⏰ ตลาดปิดอยู่ — ราคาล่าสุด: ${latest_price:.2f} (as of {as_of_text})"
            )
        else:
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
