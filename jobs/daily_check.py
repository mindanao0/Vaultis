import os
import requests
import yfinance as yf
from datetime import datetime

TICKERS = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]

def run():
    print("เริ่ม daily check...")
    
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    print(f"Webhook: {'✅' if webhook_url else '❌ ไม่มี'}")
    
    # ดึงราคา
    prices = {}
    for ticker in TICKERS:
        try:
            t = yf.Ticker(ticker)
            fast_info = t.fast_info or {}
            price = fast_info.get("last_price")
            if price is None:
                hist = t.history(period="1d")
                if not hist.empty and "Close" in hist.columns:
                    price = float(hist["Close"].iloc[-1])
                else:
                    raise ValueError("last_price not available")
            prices[ticker] = price
            print(f"{ticker}: ${price:.2f}")
        except Exception as e:
            print(f"{ticker}: Error - {e}")
            prices[ticker] = 0
    
    # ส่ง Discord
    today = datetime.now().strftime("%d/%m/%Y")
    lines = [f"📊 Daily Price Check — {today}", "─" * 30]
    for ticker, price in prices.items():
        lines.append(f"{ticker:<6} ${price:.2f}")
    
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
