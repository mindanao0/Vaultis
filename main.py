# -*- coding: utf-8 -*-
"""จุดเริ่มต้นสำหรับการตั้ง schedule แจ้งเตือนรายวัน/รายสัปดาห์/รายเดือน."""

from __future__ import annotations

import argparse
import time
from calendar import monthrange
from datetime import datetime, timedelta
from typing import Dict
from zoneinfo import ZoneInfo

import schedule

# เวลาทั้งหมดอ้างอิงเวลาไทย — เดิมใช้เวลาท้องถิ่นของเครื่อง ทำให้เมื่อรันบนเซิร์ฟเวอร์ UTC
# งานที่ตั้งไว้ 08:00 จะยิงตอน 15:00 เวลาไทย (AUDIT.md M7)
BANGKOK_TZ = ZoneInfo("Asia/Bangkok")


def _now_bangkok() -> datetime:
    return datetime.now(BANGKOK_TZ)

from alerts.notifier import send_dca_reminder, send_discord_webhook, send_technical_alert
from alerts.price_alert import check_alerts
from analysis.ai_advisor import get_monthly_advice
from analysis.returns import calculate_period_returns
from data.fetcher import DEFAULT_TICKERS, fetch_adjusted_close_data
from jobs.daily_check import run
from portfolio.tracker import get_today_fx_rate_thb
from technical.indicators import calculate_rsi
from utils.config import load_config


DEFAULT_WEIGHTS: Dict[str, float] = {
    "VOO": 0.35,
    "SCHD": 0.20,
    "QQQM": 0.20,
    "XLV": 0.15,
    "GLDM": 0.10,
}

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
    """ส่ง AI Advisor เดือนละครั้งตอนต้นเดือน (เรียก get_monthly_advice ครั้งเดียว)."""
    try:
        config = load_config()
        budget_thb = float(config["dca"]["monthly_budget_thb"])
        result = get_monthly_advice(budget_thb=budget_thb)
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
    """รัน AI Advisor เฉพาะวันที่ 1 ของเดือน (เวลาไทย)."""
    if _now_bangkok().day == 1:
        generate_monthly_ai_advisor_and_notify()
    else:
        print("Not day 1 (Asia/Bangkok) - skipping AI Advisor")


def _extract_ai_allocation_summary(advice_text: str) -> str:
    """ดึงเฉพาะส่วนสรุปการแบ่งเงินจาก AI เพื่อใช้ในข้อความเตือน DCA."""
    cleaned = (advice_text or "").strip()
    if not cleaned:
        return "- ยังไม่มีคำแนะนำ AI สำหรับเดือนนี้"

    start_candidates = (
        "💰 แนะนำแบ่งเงิน",
        "💰 แผน DCA",
        "**💰 แผน DCA",
    )
    end_candidates = (
        "⚠️ ความเสี่ยงเดือนนี้",
        "**⚠️ ความเสี่ยงที่ควรระวัง",
        "⚠️ ความเสี่ยงที่ควรระวัง",
    )
    for start_key in start_candidates:
        if start_key not in cleaned:
            continue
        start_idx = cleaned.index(start_key)
        section = cleaned[start_idx:]
        for end_key in end_candidates:
            if end_key in section:
                section = section.split(end_key, maxsplit=1)[0].strip()
                break
        return section[:900]
    return cleaned[:900]


def _effective_dca_day(dca_day: int, year: int, month: int) -> int:
    """วัน DCA จริงของเดือนนั้น — ถ้าเดือนไม่มีวันที่ตั้งไว้ ให้ใช้วันสุดท้ายของเดือน.

    (AUDIT.md M7: ตั้ง DCA วันที่ 31 → เดือน ก.พ./เม.ย./มิ.ย./ก.ย./พ.ย. ไม่เคยเตือนเลย)
    """
    last_day = monthrange(year, month)[1]
    return min(int(dca_day), last_day)


def check_and_send_dca_reminder(webhook_url: str) -> None:
    """ทุกวัน 08:00 เช็คว่าพรุ่งนี้เป็นวัน DCA หรือไม่ และส่งเตือนล่วงหน้า."""
    try:
        config = load_config()
        dca_day = int(config["dca"]["day_of_month"])
        dca_budget_thb = float(config["dca"]["monthly_budget_thb"])
        tomorrow = _now_bangkok() + timedelta(days=1)
        if tomorrow.day != _effective_dca_day(dca_day, tomorrow.year, tomorrow.month):
            return

        fx_rate = float(get_today_fx_rate_thb())
        ai_advice = "- ยังไม่มีคำแนะนำ AI สำหรับเดือนนี้"
        try:
            advice_result = get_monthly_advice(budget_thb=dca_budget_thb, send_discord=False)
            ai_advice = _extract_ai_allocation_summary(advice_result.get("advice_text", ""))
        except Exception as advice_exc:
            ai_advice = f"- ดึงคำแนะนำ AI ไม่สำเร็จ ({advice_exc})"

        result = send_dca_reminder(
            webhook_url=webhook_url,
            dca_date_text=tomorrow.strftime("%d/%m/%Y"),
            dca_budget_thb=dca_budget_thb,
            fx_rate_thb=fx_rate,
            ai_advice=ai_advice,
        )
        if result.get("success"):
            print(f"ส่ง DCA reminder สำเร็จ สำหรับวันที่ {tomorrow.strftime('%d/%m/%Y')}")
        else:
            print(f"ส่ง DCA reminder ไม่สำเร็จ: {result.get('error')}")
    except Exception as exc:
        print(f"เกิดข้อผิดพลาดใน DCA reminder: {exc}")


def run_scheduler() -> None:
    """ตั้งเวลาแจ้งเตือนตามรอบรายเดือน/รายสัปดาห์/รายวัน."""
    try:
        config = load_config()
        notifications = config["notifications"]
        dca_day = int(config["dca"]["day_of_month"])
        webhook_url = str(notifications.get("discord_webhook_url", "")).strip()
        if not webhook_url:
            raise ValueError("กรุณาตั้งค่า Discord Webhook URL ใน Settings")

        # 1) วันที่ 1 ของทุกเดือน 08:00 -> AI Advisor (ผ่าน daily guard)
        schedule.every().day.at("08:00").do(run_monthly_ai_advisor_if_first_day)
        # 2) ทุกวัน 08:00 -> เช็คว่าพรุ่งนี้เป็นวัน DCA แล้วเตือนล่วงหน้า
        if notifications.get("dca_reminder", True):
            schedule.every().day.at("08:00").do(check_and_send_dca_reminder, webhook_url=webhook_url)
        # 3) ทุกวันจันทร์ 08:00 -> Weekly Summary (RSI + Return)
        if notifications.get("weekly_summary", True):
            schedule.every().monday.at("08:00").do(generate_weekly_report_and_notify, webhook_url=webhook_url)
        # 4) ทุกวัน 09:00 -> Technical Alert เฉพาะ RSI ผิดปกติ
        if notifications.get("rsi_alert", True):
            schedule.every().day.at("09:00").do(generate_daily_technical_alerts, webhook_url=webhook_url)
        # 5) ทุกวัน 09:00 และ 21:00 -> Price Alert
        schedule.every().day.at("09:00").do(check_alerts)
        schedule.every().day.at("21:00").do(check_alerts)

        print(
            "Vaultis scheduler started: "
            "monthly AI Advisor (day 1 08:00), "
            f"DCA reminder check (daily 08:00, DCA day {dca_day}) = {notifications.get('dca_reminder', True)}, "
            f"weekly summary (Mon 08:00) = {notifications.get('weekly_summary', True)}, "
            f"daily technical alert check (09:00, RSI abnormal only) = {notifications.get('rsi_alert', True)}, "
            "price alert check (daily 09:00, 21:00) = True"
        )

        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        print("หยุด scheduler แล้ว")
    except Exception as exc:
        print(f"เกิดข้อผิดพลาดใน scheduler: {exc}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=str, default="all")
    args = parser.parse_args()

    if args.job == "weekly_summary":
        config = load_config()
        webhook_url = str(config["notifications"].get("discord_webhook_url", "")).strip()
        if not webhook_url:
            raise ValueError("กรุณาตั้งค่า Discord Webhook URL ใน Settings")
        generate_weekly_report_and_notify(webhook_url=webhook_url)
    elif args.job == "monthly_advice":
        if _now_bangkok().day == 1:
            config = load_config()
            get_monthly_advice(budget_thb=float(config["dca"]["monthly_budget_thb"]))
        else:
            print("Not day 1 (Asia/Bangkok) - skipping")
    elif args.job == "price_alert":
        # เดิม job นี้เรียก daily_check (สรุปราคา) ไม่ใช่ตัวเช็ค alert จริง — AUDIT.md C6
        result = check_alerts()
        print(
            f"ตรวจ alert {result.get('checked', 0)} รายการ, "
            f"trigger {len(result.get('triggered', []))} รายการ"
        )
    elif args.job == "daily_check":
        run()
    elif args.job == "all":
        # รัน scheduler ปกติ (ใช้เมื่อรันบนเครื่องตัวเอง)
        run_scheduler()
    else:
        raise ValueError(f"Unknown job: {args.job}")
