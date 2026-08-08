# -*- coding: utf-8 -*-
"""จุดเริ่มต้นสำหรับการตั้ง schedule แจ้งเตือนรายวัน/รายสัปดาห์/รายเดือน."""

from __future__ import annotations

import argparse
import functools
import logging
import math
import time
from calendar import monthrange
from datetime import datetime, timedelta
from typing import Any, Callable, Dict
from zoneinfo import ZoneInfo

import pandas as pd
import schedule

# เวลาทั้งหมดอ้างอิงเวลาไทย — เดิมใช้เวลาท้องถิ่นของเครื่อง ทำให้เมื่อรันบนเซิร์ฟเวอร์ UTC
# งานที่ตั้งไว้ 08:00 จะยิงตอน 15:00 เวลาไทย (AUDIT.md M7)
BANGKOK_TZ = ZoneInfo("Asia/Bangkok")


def _now_bangkok() -> datetime:
    return datetime.now(BANGKOK_TZ)

from alerts.line_notifier import send_line_message
from alerts.notifier import send_dca_reminder, send_discord_webhook, send_technical_alert
from alerts.price_alert import ALERTS_PATH, check_alerts, check_result_contract_error
from analysis.ai_advisor import get_monthly_advice
from analysis.returns import calculate_period_returns, real_bars
from data.fetcher import DEFAULT_TICKERS, fetch_adjusted_close_data
from jobs.daily_check import run
from portfolio.tracker import get_today_fx_rate_thb
from technical.indicators import calculate_rsi
from utils.config import load_config

# ตั้งชื่อ logger เองแทน ``__name__`` เพราะไฟล์นี้ถูกรันเป็นสคริปต์ (``python main.py``)
# ⇒ ``__name__ == "__main__"`` ซึ่งอ่านแล้วไม่รู้เลยว่าเป็นทางเข้าไหนในสองทางเข้าของระบบ
logger = logging.getLogger("vaultis.scheduler")


def get_default_weights() -> Dict[str, float]:
    """สัดส่วนเป้าหมายจากแหล่งเดียว (portfolio/targets.py) — เดิม hardcode คนละชุดกับ rebalance."""
    from portfolio.targets import get_target_weights

    return get_target_weights()

def _real_bars(prices: pd.DataFrame, ticker: str) -> pd.Series:
    """แท่งราคา**จริง**ของ ticker หนึ่งตัว (ไม่มีช่องที่ถูกเติมขึ้นมา).

    ``data/fetcher.fetch_adjusted_close_data`` ใช้ ``dropna(how="all")`` ซึ่งตัดเฉพาะ
    แถวที่ NaN ทุกคอลัมน์ — คอลัมน์เดียวที่ NaN ท้าย ๆ จึงรอดมาถึงที่นี่เสมอ

    นิยาม "แท่งจริง" มาจาก ``analysis.returns.real_bars`` ที่เดียว (G7) — ที่นี่เหลือแค่
    ส่วนที่ต่างจริง ๆ คือ "ไม่มีคอลัมน์นี้ในเฟรมเลย"
    """
    if ticker not in prices.columns:
        return pd.Series(dtype=float)
    return real_bars(prices[ticker])


def _stale_reason(bars: pd.Series, frame_last) -> str | None:
    """คืนเหตุผลเมื่อ ticker นี้ "ดึงข้อมูลไม่ได้" — ``None`` เมื่อข้อมูลสดพอใช้งาน.

    "ไม่มีแท่งของวันล่าสุด" ≠ "ราคาไม่เปลี่ยน" — ห้ามยุบเป็นค่าเดียวกัน
    (AUDIT_2026-08-06 ข้อ M-CI-2/M-CI-3)
    """
    if bars.empty:
        return "ดึงข้อมูลไม่ได้ (ไม่มีแท่งราคาเลย)"
    last = bars.index[-1]
    if frame_last is not None and last < frame_last:
        try:
            last_text = pd.Timestamp(last).strftime("%d/%m/%Y")
        except Exception:  # index ที่ไม่ใช่เวลา — ยังต้องเตือน แค่ไม่มีวันที่ให้อ้าง
            last_text = str(last)
        return f"ดึงข้อมูลไม่ได้ (แท่งราคาล่าสุด {last_text})"
    return None


def generate_weekly_report_and_notify(webhook_url: str) -> None:
    """สร้าง Weekly Summary (RSI + Return) และส่งแจ้งเตือนไป Discord.

    **ไม่มี ``ffill()`` บนเส้นทางรายงาน** — เดิม ``prices.ffill().iloc[-1] /
    prices.ffill().iloc[-6]`` เท่ากับ 1.0 เป๊ะเมื่อแท่งท้ายของ ticker นั้นหายไป
    จึงพิมพ์ ``1W +0.00%`` ที่หน้าตาเป็นแถวปกติทุกไบต์ แล้วยังถูกนับเข้า
    ``positive_count`` ซึ่งกำหนดสีของ embed ทั้งใบ (AUDIT_2026-08-06 ข้อ M-CI-2)
    """
    try:
        prices = fetch_adjusted_close_data(DEFAULT_TICKERS, years=10)
        returns_df = calculate_period_returns(prices)
        frame_last = prices.index[-1] if not prices.empty else None

        lines: list[str] = []
        abnormal_count = 0
        positive_count = 0
        scored_count = 0
        for ticker in DEFAULT_TICKERS:
            bars = _real_bars(prices, ticker)
            reason = _stale_reason(bars, frame_last)
            if reason:
                lines.append(f"{ticker}: ⚠️ {reason}")
                continue
            if len(bars) < 6:
                lines.append(f"{ticker}: ⚠️ ข้อมูลไม่พอคำนวณผลตอบแทน 1 สัปดาห์")
                continue

            ticker_df = bars.to_frame(name="Adj Close")
            rsi_df = calculate_rsi(ticker_df, period=14).dropna(subset=["RSI"])
            if rsi_df.empty:
                lines.append(f"{ticker}: ⚠️ คำนวณ RSI ไม่ได้")
                continue
            latest_rsi = float(rsi_df["RSI"].iloc[-1])

            # 1W คิดจากแท่งจริงของ ticker เอง — ไม่ยืมตำแหน่งแถวของทั้งเฟรม
            latest_1w = (float(bars.iloc[-1]) / float(bars.iloc[-6]) - 1.0) * 100.0

            raw_1m = returns_df.loc["1M", ticker] if ticker in returns_df.columns else None
            has_1m = raw_1m is not None and pd.notna(raw_1m)
            text_1m = f"{float(raw_1m):+.2f}%" if has_1m else "n/a"

            if latest_rsi < 30 or latest_rsi > 70:
                abnormal_count += 1
            scored_count += 1
            if latest_1w >= 0:
                positive_count += 1

            lines.append(f"{ticker}: RSI {latest_rsi:.1f} | 1W {latest_1w:+.2f}% | 1M {text_1m}")

        description = "\n".join(lines) if lines else "ไม่พบข้อมูลสำหรับสรุปรายสัปดาห์"
        # นับเฉพาะตัวที่มีตัวเลขจริง — ticker ที่ดึงข้อมูลไม่ได้ต้องไม่ถ่วงสีไปทางไหนทั้งนั้น
        is_positive = positive_count >= max(1, scored_count // 2)
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

        # ช่องทางเสริม LINE (Roadmap ข้อ 16) — ไม่ได้ตั้งค่า = ข้ามเงียบ ๆ งานหลักไม่พัง
        line_result = send_line_message(f"{title}\n{description}")
        if line_result.get("success"):
            print("ส่งรายงานรายสัปดาห์เข้า LINE สำเร็จ")
        elif not line_result.get("skipped"):
            print(f"ส่ง LINE ไม่สำเร็จ: {line_result.get('error')}")
    except Exception as exc:
        print(f"เกิดข้อผิดพลาดในการสร้างรายงานรายสัปดาห์: {exc}")


def generate_monthly_ai_advisor_and_notify() -> None:
    """ส่งแผน DCA รายเดือนตอนต้นเดือน.

    งานอัตโนมัติ → ``user_initiated=False`` → **ไม่เรียก AI** (ไม่มีค่าใช้จ่าย)
    แต่ยังส่งคะแนนและแผนจัดสรรจากโมเดลเข้า Discord ตามปกติ
    """
    try:
        config = load_config()
        budget_thb = float(config["dca"]["monthly_budget_thb"])
        result = get_monthly_advice(budget_thb=budget_thb)
        if not result.get("ai_used"):
            print("(ไม่ได้เรียก AI — ส่งเฉพาะตัวเลขจากโมเดล ไม่มีค่าใช้จ่าย)")
        discord_result = result.get("discord_result", {})
        if discord_result.get("success"):
            print("ส่งแผน DCA รายเดือนไป Discord สำเร็จ")
        elif discord_result.get("skipped"):
            print("ข้ามการส่ง: ไม่ได้ตั้งค่า webhook")
        else:
            print(f"ส่งไม่สำเร็จ: {discord_result.get('error')}")
    except Exception as exc:
        print(f"เกิดข้อผิดพลาดใน Advisor รายเดือน: {exc}")


def generate_daily_technical_alerts(webhook_url: str) -> None:
    """เช็ค Technical Alert รายวันและส่งเฉพาะ RSI ผิดปกติ.

    **ไม่มี ``ffill()``** — เดิมเติมช่องว่างก่อน แล้ว ``iloc[-1]``/``iloc[-2]`` หยิบ
    ราคาเดิมซ้ำสองครั้ง ⇒ ราคาเก่า 3 สัปดาห์ถูกส่งเป็นราคาวันนี้ และ ``previous_price``
    เท่ากับ ``price`` เป๊ะ (ตัวตัดสินทิศทางใน ``alerts/notifier`` ตาบอดทันที)
    โดยไม่มีสัญลักษณ์เตือน — และปลายทางของงานนี้คือ **สัญญาณ** ไม่ใช่แค่ตัวเลขรายงาน
    (AUDIT_2026-08-06 ข้อ M-CI-3)

    ticker ที่ตรวจไม่ได้จะถูกรวบไปแจ้งเป็นข้อความเดียว — "ตรวจไม่ได้" ต้องออกไปให้
    ผู้ใช้เห็น ห้ามตัดทิ้งเงียบ
    """
    try:
        prices = fetch_adjusted_close_data(DEFAULT_TICKERS, years=2)
        frame_last = prices.index[-1] if not prices.empty else None
        cannot_check: list[str] = []

        for ticker in DEFAULT_TICKERS:
            ticker_series = _real_bars(prices, ticker)
            reason = _stale_reason(ticker_series, frame_last)
            if reason:
                cannot_check.append(f"{ticker}: {reason}")
                continue
            if len(ticker_series) < 15:
                cannot_check.append(f"{ticker}: ข้อมูลน้อยกว่า 15 แท่ง คำนวณ RSI ไม่ได้")
                continue

            ticker_df = ticker_series.to_frame(name="Adj Close")
            rsi_df = calculate_rsi(ticker_df, period=14).dropna(subset=["RSI"])
            if rsi_df.empty:
                cannot_check.append(f"{ticker}: คำนวณ RSI ไม่ได้")
                continue

            latest_rsi = float(rsi_df["RSI"].iloc[-1])
            if 30 <= latest_rsi <= 70:
                continue  # ตรวจแล้วปกติ — คนละเรื่องกับ "ตรวจไม่ได้"

            latest_price = float(ticker_series.iloc[-1])
            previous_price = float(ticker_series.iloc[-2])
            ma200 = float(ticker_series.rolling(window=200, min_periods=200).mean().iloc[-1])
            if ma200 != ma200:
                cannot_check.append(
                    f"{ticker}: RSI {latest_rsi:.1f} ผิดปกติ แต่ข้อมูลไม่ถึง 200 แท่ง "
                    "คำนวณ MA200 ไม่ได้ จึงยังไม่ส่งสัญญาณ"
                )
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

        if cannot_check:
            detail = "\n".join(f"• {row}" for row in cannot_check)
            print(f"[technical alert] ตรวจไม่ได้:\n{detail}")
            if webhook_url:
                send_discord_webhook(
                    webhook_url=webhook_url,
                    title="⚠️ Technical Alert — ตรวจไม่ได้บางตัว",
                    description=(
                        f"{detail}\n\n"
                        "⚠️ นี่ไม่ได้แปลว่า RSI ปกติ แต่แปลว่ายังตรวจไม่ได้"
                    ),
                    is_positive=False,
                    embed_color=0xE67E22,
                )
    except Exception as exc:
        print(f"เกิดข้อผิดพลาดใน daily technical alert: {exc}")


def run_monthly_ai_advisor_if_first_day() -> None:
    """รัน AI Advisor เฉพาะวันที่ 1 ของเดือน (เวลาไทย)."""
    if _now_bangkok().day == 1:
        generate_monthly_ai_advisor_and_notify()
    else:
        print("Not day 1 (Asia/Bangkok) - skipping AI Advisor")


def _format_allocation_plan(advice_result: dict) -> str:
    """สร้างข้อความแผนจัดสรรจาก **ตัวเลขของโมเดล** (ไม่ใช่จากข้อความ AI).

    เดิมใช้ regex แกะตัวเลขออกจากคำตอบของ AI ซึ่งเปราะและเสียเงินโดยไม่จำเป็น
    """
    allocation = advice_result.get("allocation") or {}
    if not allocation:
        return "- ไม่มี ETF ที่มีข้อมูลพร้อมจัดสรร (ดึงข้อมูลไม่ได้)"

    lines: list[str] = []
    for ticker, item in allocation.items():
        tilt = item.get("tilt")
        tilt_txt = f" [{tilt:.2f}× ของเป้า {item.get('target_percent', 0)}%]" if tilt else ""
        lines.append(f"- {ticker}: {item.get('amount_thb', 0):,.0f} บาท{tilt_txt}")

    unallocated = float(advice_result.get("unallocated_thb") or 0)
    if unallocated > 0:
        lines.append(f"- ยังไม่จัดสรร: {unallocated:,.0f} บาท")

    no_data = advice_result.get("no_data_tickers") or []
    if no_data:
        lines.append(f"⚠️ ดึงข้อมูลไม่ได้: {', '.join(map(str, no_data))}")

    return "\n".join(lines)[:900]


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

        # แผนจัดสรรมาจากโมเดลโดยตรง — ไม่เรียก AI (ไม่มีค่าใช้จ่าย) และไม่แกะตัวเลข
        # จากข้อความ AI อีกต่อไป (รอยเดิมของ AUDIT.md C3)
        try:
            advice_result = get_monthly_advice(budget_thb=dca_budget_thb, send_discord=False)
            plan = _format_allocation_plan(advice_result)
        except Exception as exc:
            plan = f"- คำนวณแผนจัดสรรไม่สำเร็จ ({exc})"

        result = send_dca_reminder(
            webhook_url=webhook_url,
            dca_date_text=tomorrow.strftime("%d/%m/%Y"),
            dca_budget_thb=dca_budget_thb,
            fx_rate_thb=fx_rate,
            ai_advice=plan,
        )
        if result.get("success"):
            print(f"ส่ง DCA reminder สำเร็จ สำหรับวันที่ {tomorrow.strftime('%d/%m/%Y')}")
        else:
            print(f"ส่ง DCA reminder ไม่สำเร็จ: {result.get('error')}")
    except Exception as exc:
        print(f"เกิดข้อผิดพลาดใน DCA reminder: {exc}")


# ``--job price_alert`` ออกด้วยรหัสนี้เมื่อ "ตรวจไม่ได้ทั้งรอบ" — cron/systemd/CI
# ต้องแยก "รันแล้วไม่มีอะไรถึงเงื่อนไข" (0) ออกจาก "รันแล้วตาบอด" ให้ได้
PRICE_ALERT_STORE_ERROR_EXIT_CODE = 2

_REPORT_SEP = "─" * 31

# ตัวตรวจสัญญาย้ายไปอยู่ข้าง **ผู้ผลิต** แล้ว (``alerts/price_alert.check_result_contract_error``)
# — เดิมประกาศไว้ที่นี่ที่เดียว ผู้เรียกรายอื่น (``backend/services/alert_service.py``,
# หน้าแดชบอร์ด) จึงเติมค่าดีฟอลต์กันเอง แล้ว "ผลลัพธ์ผิดสัญญา" กลายเป็น
# "ตรวจแล้วไม่มีอะไร" บนหน้าจอผู้ใช้ (AUDIT_ROUND2_2026-08-07)


def _fmt_price(value: Any) -> str:
    """ราคาที่อ่านไม่ได้ต้องเป็น ``?`` ไม่ใช่ ``0.00`` (ห้ามกุตัวเลข).

    บรรทัดนี้อยู่บนเส้นทางเงินจริง: รายการ triggered ที่พิมพ์ลง stdout ของ scheduler
    และส่งเข้า Discord  ``$0.00`` คือ "ราคาเป้าหมาย 0 ดอลลาร์" ซึ่งเป็นตัวเลขที่ระบบ
    แต่งขึ้นเองจากข้อมูลที่หายไป — ผู้ใช้แยกไม่ออกจากราคาจริง

    NaN/inf นับเป็น "อ่านไม่ได้" ด้วย: ไม่ใช่ราคา และ ``f"{nan:,.2f}"`` พิมพ์ ``$nan``
    ซึ่งอ่านเหมือนระบบพัง มากกว่าจะบอกว่า "ไม่รู้ราคา"
    (ตรึงไว้ด้วย tests/test_fmt_price.py — AUDIT_ROUND2_2026-08-07)
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "$?"
    if not math.isfinite(number):
        return "$?"
    return f"${number:,.2f}"


def _no_pending_lines(result: dict[str, Any]) -> list[str]:
    """บรรทัดสรุปกรณี "ไม่มีอะไรถูกตรวจเลยในรอบนี้" — แยกตามสถานะ **คลัง** ไม่ใช่ตัวเลข 0.

    ``checked=0, triggered=[], unchecked=[]`` มาจากคนละเรื่องกันได้ 3 แบบ และเดิม
    ทั้งสามพิมพ์ประโยคเดียวกันว่า "(อ่านคลัง alert ได้ปกติ)":

    - ``missing`` = สภาพแวดล้อมนี้ไม่มีไฟล์คลังเลย (GitHub Actions มองไม่เห็นไฟล์นี้
      เพราะถูก gitignore) ⇒ "รอบนี้ไม่ได้ตรวจอะไรเลย" ไม่ใช่ "ไม่มี alert ถึงเงื่อนไข"
    - ``ok``      = มีคลัง อ่านได้ และไม่มี alert ค้างจริง ๆ (สถานะเดียวที่ยืนยันได้)
    - ไม่มีคีย์   = ผู้เรียกประกอบผลลัพธ์เอง (stub เก่า) ⇒ **ไม่ทราบ** ห้ามยืนยันแทน

    (AUDIT_ROUND2_2026-08-07 — check_alerts() ยุบ "เครื่องนี้ไม่มีคลัง" เข้ากับ "อ่านได้ 0 รายการ")
    """
    store_status = result.get("store_status")
    status = store_status.get("status") if isinstance(store_status, dict) else None
    store_path = (store_status or {}).get("path") if isinstance(store_status, dict) else None

    if status == "missing":
        return [
            f"⚠️ [price alert] เครื่องนี้ไม่มีไฟล์คลัง alert ({store_path or ALERTS_PATH}) — "
            "รอบนี้ไม่ได้ตรวจอะไรเลย",
            "⚠️ นี่ไม่ได้แปลว่า 'ไม่มี alert ถึงเงื่อนไข' แต่แปลว่าสภาพแวดล้อมนี้ไม่มีคลังให้ตรวจ",
            "👉 ไฟล์คลังถูก gitignore ไว้ (ตั้งใจ) — การตรวจ alert รายตัวทำงานเฉพาะจาก "
            "scheduler ในเครื่อง/Docker ที่ mount ไฟล์จริงเข้ามา",
        ]
    if status == "error":
        # อ่านคลังไม่ได้ตอนสรุปสถานะ ทั้งที่รอบตรวจผ่าน = ไฟล์เพิ่งเสียระหว่างรอบ
        return [
            "🚨 [price alert] อ่านคลัง alert ไม่ได้ตอนสรุปสถานะ "
            f"({(store_status or {}).get('error') or 'ไม่ระบุสาเหตุ'})",
            "⚠️ ตัวเลขของรอบนี้จึงยืนยันไม่ได้ว่าครบ",
        ]
    if status == "ok":
        return ["[price alert] ไม่มี alert ค้างให้ตรวจ (อ่านคลัง alert ได้ปกติ)"]
    return ["[price alert] ไม่มี alert ค้างให้ตรวจ (ผลลัพธ์ไม่ได้แนบสถานะคลัง — ไม่ทราบว่ามีคลังให้ตรวจหรือไม่)"]


def _discord_delivery_note(result: dict[str, Any]) -> str | None:
    """คำเตือนเมื่อสรุปประจำรอบ **ไม่ได้** ไปถึง Discord — log นี้จึงเป็นช่องทางเดียว."""
    delivery = result.get("daily_discord_result")
    if not isinstance(delivery, dict) or delivery.get("success"):
        return None
    if delivery.get("skipped"):
        return "⚠️ ไม่ได้ส่งเข้า Discord (ไม่ได้ตั้ง webhook) — ข้อความนี้เห็นได้เฉพาะใน log"
    return f"⚠️ ส่งสรุปเข้า Discord ไม่สำเร็จ ({delivery.get('error')}) — เห็นได้เฉพาะใน log"


def format_price_alert_report(result: Any) -> str:
    """สรุปผล ``check_alerts()`` เป็นข้อความที่แยก **3 สถานะ** ออกจากกัน.

    ``ถึงเงื่อนไข`` / ``ตรวจแล้วไม่ถึง`` / ``ตรวจไม่ได้`` — เดิมงานนี้อ่านแค่
    ``checked`` กับ ``triggered`` ทำให้ทั้ง "ไม่มี alert ค้าง", "ดึงราคาไม่ได้ทุกตัว"
    และ "อ่านไฟล์คลังไม่ได้เลย" พิมพ์บรรทัดเดียวกันเป๊ะว่า
    ``ตรวจ alert 0 รายการ, trigger 0 รายการ`` ⇒ ผู้ใช้สรุปว่า "ไม่มีอะไรถึงเงื่อนไข"
    ทั้งที่สองกรณีหลังคือ "ยังไม่รู้" (กฎ: "ดึงไม่สำเร็จ" ≠ "ไม่มีข้อมูล")

    ``checked`` จาก ``check_alerts()`` **นับรวมตัวที่ trigger แล้ว** ดังนั้น
    "ตรวจแล้วไม่ถึงเงื่อนไข" = ``checked - len(triggered)``

    สถานะที่ 4 อยู่ใน ``_no_pending_lines()``: "เครื่องนี้ไม่มีคลัง alert เลย"
    ซึ่งเดิมถูกยุบเข้ากับ "อ่านคลังได้ ไม่มี alert ค้าง"
    """
    contract_error = check_result_contract_error(result)
    if contract_error is not None:
        return "\n".join(
            [
                f"🚨 [price alert] ผลลัพธ์จาก check_alerts() ผิดสัญญา — {contract_error}",
                "⚠️ สรุปสถานะไม่ได้ = **ยังไม่รู้** ว่ามี alert ถึงเงื่อนไขหรือไม่ "
                "(ไม่ใช่ 'ไม่มี')",
            ]
        )

    if result["store_error"]:
        lines = [
            "🚨🚨 [price alert] ตรวจไม่ได้ทั้งรอบ — อ่านคลัง alert ไม่สำเร็จ",
            _REPORT_SEP,
            f"ไฟล์: {ALERTS_PATH}",
            f"สาเหตุ: {result.get('error') or 'ไม่ระบุ'}",
            "ระบบไม่ได้เขียนทับไฟล์ของคุณ แต่รอบนี้ไม่ได้ตรวจ alert สักรายการ",
            "⚠️ นี่ไม่ได้แปลว่า 'ไม่มี alert ถึงเงื่อนไข' แต่แปลว่า 'ตรวจไม่ได้'",
            "👉 ต้องซ่อมไฟล์คลัง alert ก่อน ไม่งั้นทุกรอบถัดไปก็ตาบอดเหมือนเดิม",
        ]
        note = _discord_delivery_note(result)
        if note:
            lines.append(note)
        return "\n".join(lines)

    triggered = list(result["triggered"] or [])
    unchecked = list(result["unchecked"] or [])
    checked = int(result["checked"])
    not_triggered = checked - len(triggered)

    if not checked and not triggered and not unchecked:
        lines = _no_pending_lines(result)
        note = _discord_delivery_note(result)
        if note:
            lines.append(note)
        return "\n".join(lines)

    lines = [
        f"[price alert] ถึงเงื่อนไข {len(triggered)} รายการ | "
        f"ตรวจแล้วไม่ถึง {max(not_triggered, 0)} รายการ | "
        f"ตรวจไม่ได้ {len(unchecked)} รายการ"
    ]
    if not_triggered < 0:
        # checked ต้อง ≥ จำนวนที่ trigger เสมอ — ถ้าไม่ใช่แปลว่านับผิดที่ต้นทาง
        lines.append(
            f"🚨 ตัวเลขไม่สอดคล้อง: checked={checked} แต่ trigger {len(triggered)} รายการ"
        )

    if triggered:
        lines.append("🔔 ถึงเงื่อนไข:")
        for item in triggered:
            lines.append(
                f"   • {item.get('ticker') or '-'} {item.get('alert_type') or '?'} "
                f"{_fmt_price(item.get('target_price'))} "
                f"(ราคาล่าสุด {_fmt_price(item.get('current_price'))})"
            )

    if unchecked:
        lines.append("⚠️ ตรวจไม่ได้ (คนละเรื่องกับ 'ตรวจแล้วไม่ถึงเงื่อนไข'):")
        for item in unchecked:
            lines.append(
                f"   • {item.get('ticker') or '-'}: {item.get('reason') or 'ไม่ระบุสาเหตุ'}"
            )
        lines.append("⚠️ alert เหล่านี้อาจถึงเงื่อนไขไปแล้วก็ได้ — รอบนี้ระบบมองไม่เห็น")

    note = _discord_delivery_note(result)
    if note:
        lines.append(note)
    return "\n".join(lines)


def run_price_alert_job() -> dict[str, Any]:
    """ตรวจ price alert หนึ่งรอบ แล้ว **รายงานผลออก stdout ครบทั้ง 3 สถานะ**.

    ใช้ทั้งจาก scheduler (09:00 / 21:00) และจาก ``--job price_alert``
    ตัว ``check_alerts()`` ยิง Discord เองอยู่แล้ว ที่นี่จึง **ไม่ส่งซ้ำ** — แต่เมื่อ
    ไม่ได้ตั้ง webhook มันไม่ส่งอะไรเลย stdout ของ scheduler จึงเป็นช่องทางเดียว
    ที่ผู้ใช้จะรู้ว่า "รอบนี้ตรวจไม่ได้"
    """
    result = check_alerts()
    print(format_price_alert_report(result))
    return result


def _safe(job: Callable) -> Callable:
    """ห่อ job ให้ข้อผิดพลาดจบที่ตัวมันเอง.

    ``schedule.run_pending()`` ปล่อย exception ของ job ออกมาตรง ๆ — ก่อนแก้
    ``try/except`` ครอบ ``while True`` ทั้งก้อนอยู่ **นอก** ลูป ⇒ job เดียวพัง
    = ไม่มีใครเรียก ``run_pending`` อีกเลย งานที่เหลือรอไปตลอดกาล
    (``check_alerts`` เป็น job เดียวที่ไม่มี try/except ของตัวเอง — และเป็น job
    เดียวที่ยังทำงานเมื่อไม่ได้ตั้ง webhook) AUDIT_2026-08-06 ข้อ M-CI-4

    ``functools.wraps`` จำเป็น: ชื่อของงานถูกใช้ทั้งใน log และในเทสต์ที่ตรวจว่า
    งานไหนถูกลงทะเบียนบ้าง
    """

    @functools.wraps(job)
    def _wrapped(*args, **kwargs):
        try:
            return job(*args, **kwargs)
        except Exception as exc:
            print(f"[scheduler] งาน {getattr(job, '__name__', job)!s} ล้มเหลว: {exc}")
            return None

    return _wrapped


def run_scheduler() -> None:
    """ตั้งเวลาแจ้งเตือนตามรอบรายเดือน/รายสัปดาห์/รายวัน."""
    try:
        config = load_config()
        notifications = config["notifications"]
        dca_day = int(config["dca"]["day_of_month"])
        webhook_url = str(notifications.get("discord_webhook_url", "")).strip()

        # ไม่มี webhook ≠ เหตุให้ทั้ง scheduler ตาย — เดิม raise ตรงนี้แล้วถูก except ด้านล่าง
        # จับ ทำให้ process จบทันที พอรันใน container ที่ตั้ง restart: unless-stopped
        # มันจะเกิด-ตาย-เกิดใหม่เป็นวงไม่รู้จบ (เจอตอนเตรียม Docker 2026-07-28)
        # งานที่ไม่ต้องพึ่ง Discord (ตรวจ price alert) ยังมีประโยชน์และต้องเดินต่อได้
        if not webhook_url:
            print(
                "[scheduler] ไม่ได้ตั้ง DISCORD_WEBHOOK_URL — ข้ามงานที่ต้องส่ง Discord "
                "(AI advisor รายเดือน, เตือน DCA, weekly summary, technical alert) "
                "แต่ยังตรวจ price alert ตามเวลาปกติ"
            )

        # ทุก job ห่อด้วย _safe() — งานหนึ่งพังต้องไม่ลากงานอื่นและตัว scheduler ไปด้วย
        if webhook_url:
            # 1) วันที่ 1 ของทุกเดือน 08:00 -> AI Advisor (ผ่าน daily guard)
            schedule.every().day.at("08:00").do(_safe(run_monthly_ai_advisor_if_first_day))
            # 2) ทุกวัน 08:00 -> เช็คว่าพรุ่งนี้เป็นวัน DCA แล้วเตือนล่วงหน้า
            if notifications.get("dca_reminder", True):
                schedule.every().day.at("08:00").do(_safe(check_and_send_dca_reminder), webhook_url=webhook_url)
            # 3) ทุกวันจันทร์ 08:00 -> Weekly Summary (RSI + Return)
            if notifications.get("weekly_summary", True):
                schedule.every().monday.at("08:00").do(_safe(generate_weekly_report_and_notify), webhook_url=webhook_url)
            # 4) ทุกวัน 09:00 -> Technical Alert เฉพาะ RSI ผิดปกติ
            if notifications.get("rsi_alert", True):
                schedule.every().day.at("09:00").do(_safe(generate_daily_technical_alerts), webhook_url=webhook_url)
        # 5) ทุกวัน 09:00 และ 21:00 -> Price Alert (ไม่ต้องใช้ webhook)
        #    ผ่าน run_price_alert_job ไม่ใช่ check_alerts ดิบ ๆ — ผลลัพธ์ต้องถูก
        #    "อ่าน" ออกมาเป็น 3 สถานะ ไม่งั้น unchecked/store_error หายไปกับค่าคืนที่ทิ้ง
        schedule.every().day.at("09:00").do(_safe(run_price_alert_job))
        schedule.every().day.at("21:00").do(_safe(run_price_alert_job))

        print(
            "Vaultis scheduler started: "
            f"discord = {bool(webhook_url)}, "
            f"monthly AI Advisor (day 1 08:00) = {bool(webhook_url)}, "
            f"DCA reminder check (daily 08:00, DCA day {dca_day}) = "
            f"{bool(webhook_url) and notifications.get('dca_reminder', True)}, "
            f"weekly summary (Mon 08:00) = "
            f"{bool(webhook_url) and notifications.get('weekly_summary', True)}, "
            f"daily technical alert check (09:00, RSI abnormal only) = "
            f"{bool(webhook_url) and notifications.get('rsi_alert', True)}, "
            "price alert check (daily 09:00, 21:00) = True"
        )

        while True:
            # try/except ต้องอยู่ **ในลูป** — ถ้าอยู่นอก ความล้มเหลวครั้งเดียวจบเกม
            try:
                schedule.run_pending()
            except Exception as exc:
                print(f"[scheduler] run_pending ล้มเหลว (เดินต่อ): {exc}")
            time.sleep(30)
    except KeyboardInterrupt:
        print("หยุด scheduler แล้ว")
    except Exception as exc:
        print(f"เกิดข้อผิดพลาดใน scheduler: {exc}")


def _configure_logging_for_scheduler() -> None:
    """ตั้งค่า logging ของโปรเซส scheduler — **ยืมนิยามเดียวกับ backend ห้ามเขียนใหม่**.

    ระบบนี้มี "ทางเข้า" สองทาง: ``uvicorn backend.main:app`` กับ ``python main.py``
    (service ``vaultis-scheduler`` ใน docker-compose) รอบก่อนแก้ให้เฉพาะทางแรก
    ทางนี้จึงยังรันด้วย root logger เปล่า ๆ ที่มีแต่ ``lastResort`` ระดับ WARNING
    ⇒ ทุกบรรทัด ``logger.info`` ในคอนเทนเนอร์นี้หายเงียบ รวมถึงสองบรรทัดที่สำคัญ:

    - ``analysis/llm.py`` log จำนวนโทเคน + ค่าใช้จ่ายโดยประมาณเป็น INFO ซึ่งเป็น
      **หลักฐานชิ้นเดียว** ว่ารอบที่ตั้ง ``VAULTIS_LLM_AUTO=1`` ใช้เงินไปเท่าไร
    - ``analysis/sentiment_analyzer.py`` log ``"ข้าม sentiment — LLM ปิดอยู่"``
      ซึ่งเป็นตัวแยก "งานรันแล้วข้ามตัวเอง" ออกจาก "งานไม่ได้รัน"

    (AUDIT_ROUND2_2026-08-07 — ข้อเดียวกับของ backend แต่หลุดไปหนึ่งทางเข้า)

    **import หนัก จึงทำแบบ lazy ในฟังก์ชันนี้ ไม่ใช่ที่หัวไฟล์**: ``backend.main``
    ลาก FastAPI + router ทุกตัว (~3 วินาที) และสร้างตาราง SQLite ตอน import
    ไฟล์นี้ถูก ``import`` โดยเทสต์หลายไฟล์ในฐานะไลบรารี — ต้นทุนนั้นจึงต้องตกอยู่กับ
    **การรันจริงเท่านั้น** (เรียกจากบล็อก ``__main__``)  ส่วน ``AsyncIOScheduler``
    ของ backend ถูก "สร้าง" ตอน import แต่ ``start()`` อยู่ใน lifespan ของ FastAPI
    การ import จากที่นี่จึงไม่ได้จุด scheduler ตัวที่สองขึ้นมา

    import ล้มเหลว = **เตือนดัง ๆ แล้วเดินต่อ** ไม่ใช่ล้มทั้งโปรเซส: ปรัชญาเดียวกับ
    ``run_scheduler()`` (ไม่มี webhook ก็ยังต้องตรวจ price alert ต่อ) การตั้ง log ไม่ได้
    ไม่ใช่เหตุให้เลิกตรวจ alert — แต่ต้องไม่เงียบ เพราะคนอ่าน log ต้องรู้ว่าทำไม
    บรรทัด INFO ถึงไม่มา  (สคริปต์นี้รายงานงานของตัวเองด้วย ``print`` อยู่แล้ว
    ข้อความของ scheduler เองจึงไม่หายไปด้วย)
    """
    try:
        from backend.main import configure_logging
    except Exception as exc:  # โมดูล backend พังทั้งตัวเท่านั้นถึงจะมาถึงบรรทัดนี้
        print(
            "[scheduler] ⚠️ ตั้งค่า logging ไม่สำเร็จ (import backend.main ไม่ได้: "
            f"{exc}) — บรรทัดระดับ INFO รวมถึงค่าใช้จ่าย LLM จะไม่ออกใน log รอบนี้ "
            "งานตามเวลายังทำงานต่อตามปกติ"
        )
        return
    configure_logging()


if __name__ == "__main__":
    # ต้องตั้งก่อน dispatch ทุกงาน — ไม่ใช่ในแต่ละสาขา ไม่งั้นงานที่เพิ่มทีหลัง
    # จะเงียบอีกรอบโดยไม่มีใครสังเกต (ตรึงไว้ด้วย tests/test_logging_config.py)
    _configure_logging_for_scheduler()

    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=str, default="all")
    args = parser.parse_args()

    # บรรทัดแรกของทุกโปรเซส: มีเวลากำกับ ⇒ แยก "รอบนี้ไม่มีอะไรเข้าเงื่อนไข" ออกจาก
    # "โปรเซสไม่ได้เริ่มเลย" ได้จาก log อย่างเดียว (ปัญหาเดียวกับ screener ฝั่ง backend)
    logger.info("ทางเข้า scheduler เริ่มทำงาน: job=%s", args.job)

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
        # และเดิมพิมพ์แค่ checked/triggered ⇒ "ตรวจไม่ได้" กับ "ตรวจแล้วไม่ถึง" หน้าตาเท่ากัน
        price_alert_result = run_price_alert_job()
        # "อ่านคลังไม่ได้" ต้องดังถึงระดับ exit code — cron ที่เห็น exit 0
        # จะเข้าใจว่ารอบนี้ตรวจสำเร็จและไม่มีอะไรถึงเงื่อนไข
        if check_result_contract_error(price_alert_result) is not None or price_alert_result["store_error"]:
            raise SystemExit(PRICE_ALERT_STORE_ERROR_EXIT_CODE)
    elif args.job == "daily_check":
        run()
    elif args.job == "all":
        # รัน scheduler ปกติ (ใช้เมื่อรันบนเครื่องตัวเอง)
        run_scheduler()
    else:
        raise ValueError(f"Unknown job: {args.job}")
