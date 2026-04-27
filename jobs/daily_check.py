import os
import requests
import yfinance as yf
from datetime import datetime

TICKERS = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]

def run():
    print("เริ่ม daily check...")
    
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    print(f"Webhook: {'✅' if webhook_url else '❌ ไม่มี'}")
    
    # ดึงราคา
    prices = {}
    for ticker in TICKERS:
        try:
            t = yf.Ticker(ticker)
            price = t.fast_info["last_price"]
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
        payload = {"content": f"```\n{message}\n```"}
        r = requests.post(webhook_url, json=payload)
        print(f"Discord status: {r.status_code}")
    else:
        print("❌ ไม่มี DISCORD_WEBHOOK_URL")

if __name__ == "__main__":
    run()
