"""จุดเริ่มต้นสำหรับการตั้ง schedule แจ้งเตือนรายวัน/รายสัปดาห์/รายเดือน."""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict

import schedule
from dotenv import load_dotenv

from alerts.notifier import send_discord_webhook, send_technical_alert
from analysis.ai_advisor import get_monthly_advice
from analysis.returns import calculate_period_returns
from data.fetcher import DEFAULT_TICKERS, fetch_adjusted_close_data
from technical.indicators import calculate_rsi


DEFAULT_WEIGHTS: Dict[str, float] = {
    "VOO": 0.35,
    "SCHD": 0.20,
    "QQQM": 0.20,
    "XLV": 0.15,
    "GLDM": 0.10,
}

ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=ROOT_DIR / ".env", override=True)


def generate_weekly_report_and_notify(webhook_url: str) -> None:
    """สร้าง Weekly Summary (RSI + Return) และส่งแจ้งเตือนไป Discord."""
    try:
        prices = fetch_adjusted_close_data(DEFAULT_TICKERS, years=10)
        returns_df = calculate_period_returns(prices)
        one_week_return = ((prices.ffill().iloc[-1] / prices.ffill().iloc[-6]) - 1.0) * 100.0

        lines: list[str] = []
        abnormal_count = 0
        positive_count = 0
        for ticker in DEFAULT_TICKERS:
            ticker_df = prices[[ticker]].dropna().rename(columns={ticker: "Adj Close"})
            if ticker_df.empty:
                continue
            rsi_df = calculate_rsi(ticker_df, period=14).dropna(subset=["RSI"])
            latest_rsi = float(rsi_df["RSI"].iloc[-1])
            latest_1m = float(returns_df.loc["1M", ticker]) if ticker in returns_df.columns else 0.0
            latest_1w = float(one_week_return[ticker]) if ticker in one_week_return.index else 0.0

            if latest_rsi < 30 or latest_rsi > 70:
                abnormal_count += 1
            if latest_1w >= 0:
                positive_count += 1

            lines.append(f"{ticker}: RSI {latest_rsi:.1f} | 1W {latest_1w:+.2f}% | 1M {latest_1m:+.2f}%")

        description = "\n".join(lines) if lines else "ไม่พบข้อมูลสำหรับสรุปรายสัปดาห์"
        is_positive = positive_count >= max(1, len(lines) // 2)
        title = f"Vaultis Weekly Summary (RSI + Return) | RSI ผิดปกติ {abnormal_count} ตัว"

        result = send_discord_webhook(
            webhook_url=webhook_url,
            title=title,
            description=description,
            is_positive=is_positive,
        )
        if not result.get("success"):
            print(f"ส่ง Discord ไม่สำเร็จ: {result.get('error')}")
        else:
            print("ส่งรายงานรายสัปดาห์ไป Discord สำเร็จ")
    except Exception as exc:
        print(f"เกิดข้อผิดพลาดในการสร้างรายงานรายสัปดาห์: {exc}")


def generate_monthly_ai_advisor_and_notify() -> None:
    """ส่ง AI Advisor เดือนละครั้งตอนต้นเดือน."""
    try:
        result = get_monthly_advice(budget_thb=5000)
        discord_result = result.get("discord_result", {})
        if discord_result.get("success"):
            print("ส่ง AI Advisor รายเดือนไป Discord สำเร็จ")
        elif discord_result.get("skipped"):
            print("ข้ามการส่ง AI Advisor: ไม่ได้ตั้งค่า webhook")
        else:
            print(f"ส่ง AI Advisor ไม่สำเร็จ: {discord_result.get('error')}")
    except Exception as exc:
        print(f"เกิดข้อผิดพลาดใน AI Advisor รายเดือน: {exc}")


def generate_daily_technical_alerts(webhook_url: str) -> None:
    """เช็ค Technical Alert รายวันและส่งเฉพาะ RSI ผิดปกติ."""
    try:
        prices = fetch_adjusted_close_data(DEFAULT_TICKERS, years=2).ffill()
        for ticker in DEFAULT_TICKERS:
            if ticker not in prices.columns:
                continue

            ticker_series = prices[ticker].dropna()
            if len(ticker_series) < 15:
                continue

            ticker_df = ticker_series.to_frame(name="Adj Close")
            rsi_df = calculate_rsi(ticker_df, period=14).dropna(subset=["RSI"])
            if rsi_df.empty:
                continue

            latest_rsi = float(rsi_df["RSI"].iloc[-1])
            if 30 <= latest_rsi <= 70:
                continue

            latest_price = float(ticker_series.iloc[-1])
            previous_price = float(ticker_series.iloc[-2])
            ma200 = float(ticker_series.rolling(window=200, min_periods=200).mean().iloc[-1])
            if ma200 != ma200:
                continue

            result = send_technical_alert(
                webhook_url=webhook_url,
                symbol=ticker,
                rsi=latest_rsi,
                price=latest_price,
                ma200=ma200,
                previous_price=previous_price,
            )
            if result.get("success") and not result.get("skipped"):
                print(f"ส่ง Technical Alert สำเร็จ: {ticker} (RSI {latest_rsi:.1f})")
            elif not result.get("success"):
                print(f"ส่ง Technical Alert ไม่สำเร็จ ({ticker}): {result.get('error')}")
    except Exception as exc:
        print(f"เกิดข้อผิดพลาดใน daily technical alert: {exc}")


def run_monthly_ai_advisor_if_first_day() -> None:
    """รัน AI Advisor เฉพาะวันที่ 1 ของเดือน."""
    if datetime.now().day == 1:
        generate_monthly_ai_advisor_and_notify()


def run_scheduler() -> None:
    """ตั้งเวลาแจ้งเตือนตามรอบรายเดือน/รายสัปดาห์/รายวัน."""
    try:
        webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
        if not webhook_url:
            raise ValueError("กรุณาตั้งค่า environment variable: DISCORD_WEBHOOK_URL")

        # 1) วันที่ 1 ของทุกเดือน 08:00 -> AI Advisor (ผ่าน daily guard)
        schedule.every().day.at("08:00").do(run_monthly_ai_advisor_if_first_day)
        # 2) ทุกวันจันทร์ 08:00 -> Weekly Summary (RSI + Return)
        schedule.every().monday.at("08:00").do(generate_weekly_report_and_notify, webhook_url=webhook_url)
        # 3) ทุกวัน 09:00 -> Technical Alert เฉพาะ RSI ผิดปกติ
        schedule.every().day.at("09:00").do(generate_daily_technical_alerts, webhook_url=webhook_url)

        print(
            "Vaultis scheduler started: "
            "monthly AI Advisor (day 1 08:00), "
            "weekly summary (Mon 08:00), "
            "daily technical alert check (09:00, RSI abnormal only)"
        )

        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        print("หยุด scheduler แล้ว")
    except Exception as exc:
        print(f"เกิดข้อผิดพลาดใน scheduler: {exc}")


if __name__ == "__main__":
    run_scheduler()
