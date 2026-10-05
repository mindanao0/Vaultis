# -*- coding: utf-8 -*-
"""จุดเริ่มต้นสำหรับการตั้ง schedule แจ้งเตือนรายวัน/รายสัปดาห์/รายเดือน."""

from __future__ import annotations

import argparse
import functools
import json
import logging
import math
import os
import time
from calendar import monthrange
from datetime import datetime, timedelta
from pathlib import Path
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
from alerts.price_alert import (
    ALERTS_PATH,
    check_alerts,
    check_result_contract_error,
    send_daily_status,
    send_unchecked_notice,
)
from analysis.ai_advisor import (
    MONTHLY_AI_ENV,
    format_allocation_line,
    get_monthly_advice,
    monthly_ai_enabled,
)
from analysis.returns import calculate_period_returns, real_bars
from data.fetcher import DEFAULT_TICKERS, fetch_adjusted_close_data
from jobs.daily_check import run
from jobs.dar_monthly import run_dar_plan_if_due
from jobs.simulation_refresh import run_simulation_refresh
from portfolio.tracker import get_today_fx_rate_thb
from technical.indicators import calculate_rsi
from technical.signal_rules import rsi_zone
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


def generate_monthly_ai_advisor_and_notify() -> bool:
    """ส่งแผน DCA รายเดือนตอนต้นเดือน.

    ดีฟอลต์ **ไม่เรียก AI** (ไม่มีค่าใช้จ่าย) แต่ยังส่งคะแนนและแผนจัดสรรจากโมเดล
    เข้า Discord ตามปกติ — ตั้ง ``VAULTIS_MONTHLY_AI=1`` ถ้าต้องการให้ Claude อธิบายแผน
    ด้วย (จ่ายเงิน ~1 ครั้ง/เดือน) ตัวเลขในแผนยังมาจากโค้ดเหมือนเดิมทุกตัว

    คืน ``True`` เมื่อ Discord รับข้อความแล้วเท่านั้น — ตัวส่งย้อนหลังใช้ค่านี้ตัดสินว่า
    "เดือนนี้ส่งแล้ว" (ข้ามเพราะไม่มี webhook ≠ ส่งแล้ว)
    """
    try:
        config = load_config()
        budget_thb = float(config["dca"]["monthly_budget_thb"])
        result = get_monthly_advice(budget_thb=budget_thb, user_initiated=monthly_ai_enabled())
        if not result.get("ai_used"):
            print(f"(ไม่ได้ใช้คำอธิบาย AI รอบนี้ — ส่งเฉพาะตัวเลขจากโมเดล; เปิดด้วย {MONTHLY_AI_ENV}=1)")
        discord_result = result.get("discord_result", {})
        if discord_result.get("success"):
            print("ส่งแผน DCA รายเดือนไป Discord สำเร็จ")
            return True
        if discord_result.get("skipped"):
            print("ข้ามการส่ง: ไม่ได้ตั้งค่า webhook")
        else:
            print(f"ส่งไม่สำเร็จ: {discord_result.get('error')}")
    except Exception as exc:
        print(f"เกิดข้อผิดพลาดใน Advisor รายเดือน: {exc}")
    return False


def generate_daily_technical_alerts(webhook_url: str) -> None:
    """เช็ค Technical Alert รายวันและส่งเฉพาะ RSI ผิดปกติ.

    **ไม่มี ``ffill()``** — เดิมเติมช่องว่างก่อน แล้ว ``iloc[-1]``/``iloc[-2]`` หยิบ
    ราคาเดิมซ้ำสองครั้ง ⇒ ราคาเก่า 3 สัปดาห์ถูกส่งเป็นราคาวันนี้ และ ``previous_price``
    เท่ากับ ``price`` เป๊ะ (ตัวตัดสินทิศทางใน ``alerts/notifier`` ตาบอดทันที)
    โดยไม่มีสัญลักษณ์เตือน — และปลายทางของงานนี้คือ **สัญญาณ** ไม่ใช่แค่ตัวเลขรายงาน
    (AUDIT_2026-08-06 ข้อ M-CI-3)

    ticker ที่ตรวจไม่ได้จะถูกรวบไปแจ้งเป็นข้อความเดียว — "ตรวจไม่ได้" ต้องออกไปให้
    ผู้ใช้เห็น ห้ามตัดทิ้งเงียบ

    **เกณฑ์ RSI ไม่ได้อยู่ในไฟล์นี้** ทั้งโซนกลางที่ใช้ตัดสินว่า "ไม่ต้องแจ้งเตือน" และ
    ป้าย/สีของการ์ดที่ ``alerts/notifier.py`` ประกอบ ล้วนมาจาก
    ``technical/signal_rules.py`` ที่เดียว (AUDIT_ROUND2_2026-08-07)

    ค่าที่ ``send_technical_alert()`` คืนกลับมีสามความหมาย ห้ามยุบรวมกัน:
    ส่งสำเร็จ · ตรวจแล้วไม่มีสัญญาณ (``skipped`` + ``data_ok=True`` เงียบได้) ·
    **ตรวจไม่ได้** (``data_ok=False`` + ``success=False`` — ข้อมูลไม่พร้อมจนตัดสินไม่ได้
    ซึ่งเป็นคนละเรื่องกับ "ยิง Discord ไม่ออก" และต้องอ่านออกจาก log ว่าเป็นคนละเรื่อง)
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
            # โซน RSI มาจาก ``technical/signal_rules.py`` ที่เดียว — เดิมบรรทัดนี้พิมพ์
            # เลข 30/70 ซ้ำเอง ทั้งที่มันคือ RSI_OVERSOLD/RSI_OVERBOUGHT ของนิยามกลาง
            # ⇒ วันที่ใครแก้ค่ากลาง งานนี้จะเงียบ ๆ ใช้เส้นเก่าต่อไป แล้ว "โซนกลาง" ของ
            # การแจ้งเตือนจะไม่ตรงกับของหน้าจอ/สกรีนเนอร์/AI โดยไม่มีอะไรร้อง
            # (รอยเดียวกับที่ ``alerts/notifier.py`` เพิ่งถูกถอดออกทั้งไฟล์
            #  — AUDIT_ROUND2_2026-08-07)
            #
            # ขอบเขตเท่าเดิมทุกประการ: ``rsi_zone()`` คืน "neutral" เมื่อ
            # RSI_OVERSOLD <= rsi <= RSI_OVERBOUGHT ⇒ RSI 30.0 และ 70.0 พอดี
            # ยังนับเป็น "ตรวจแล้วปกติ" เหมือนเดิม
            if rsi_zone(latest_rsi) == "neutral":
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
            elif result.get("data_ok") is False:
                # ปลายทางบอกว่า "ข้อมูลไม่พร้อมจนตัดสินสัญญาณไม่ได้" ไม่ใช่ "ส่งไม่ออก"
                # ถ้าพิมพ์รวมกับกรณีเน็ต/webhook ล่ม ผู้ใช้จะอ่านไม่ออกว่าต้องไปแก้อะไร
                # และที่แย่กว่าคืออ่านเป็น "ระบบมีปัญหาชั่วคราว" ทั้งที่แปลว่ายังไม่รู้
                # ว่า ticker นี้มีสัญญาณหรือไม่
                print(
                    f"[technical alert] ตรวจไม่ได้ ({ticker}): "
                    f"{result.get('reason') or result.get('error') or 'ไม่ระบุสาเหตุ'} "
                    "— ไม่ใช่ 'ส่งไม่สำเร็จ' และไม่ได้แปลว่า RSI ปกติ"
                )
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


# --- แผน DCA ต้นเดือน: ส่งย้อนหลังได้ถ้าเครื่องปิดอยู่ตอน 08:00 วันที่ 1 ---
# เดิมเช็ค "วันนี้วันที่ 1 ไหม" วันละครั้งตอน 08:00 ⇒ คอมปิดอยู่ตอนนั้น = ไม่ส่งทั้งเดือน
# โดยไม่มีอะไรฟ้อง  ตอนนี้จำเดือนที่ส่งแล้วไว้ในไฟล์ แล้วเช็คตอนเริ่มโปรเซส + ทุกชั่วโมง
#
# อ่าน env **ครั้งเดียวตอน import** (เทสต์ monkeypatch ชื่อนี้ — แบบเดียวกับ
# VAULTIS_LEDGER_PATH/VAULTIS_ALERTS_PATH) · Docker ชี้ไป /data ซึ่ง bind mount จาก
# host ⇒ rebuild image แล้วสถานะไม่หาย ไม่งั้นทุก rebuild = ส่งแผนซ้ำ
SCHEDULER_STATE_PATH = Path(
    os.getenv("VAULTIS_SCHEDULER_STATE_PATH")
    or Path(__file__).resolve().parent / ".scheduler_state.json"
)
MONTHLY_PLAN_HOUR = 8
# กันจ่ายค่า AI ซ้ำไม่รู้จบเมื่อ Discord ล่ม: แต่ละครั้งที่ล้มอาจเรียก AI ไปแล้ว
MONTHLY_PLAN_MAX_FAILED_ATTEMPTS = 3
# สำรองในหน่วยความจำ: ส่งสำเร็จแต่เขียนไฟล์สถานะไม่ได้ ⇒ อย่าส่งซ้ำทุกชั่วโมง
_monthly_plan_sent_in_process: set[str] = set()


class SchedulerStateUnreadable(RuntimeError):
    """ไฟล์สถานะมีอยู่แต่อ่านไม่ออก — ไม่รู้ว่าส่งแล้วหรือยัง ห้ามเดา."""


def _load_scheduler_state() -> dict[str, Any] | None:
    """คืน ``None`` เมื่อยังไม่มีไฟล์ (ติดตั้งครั้งแรก) — ต่างจากไฟล์เสียที่ต้อง raise."""
    try:
        raw = SCHEDULER_STATE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SchedulerStateUnreadable(f"{SCHEDULER_STATE_PATH}: JSON เสีย ({exc})") from exc
    if not isinstance(data, dict):
        raise SchedulerStateUnreadable(f"{SCHEDULER_STATE_PATH}: ไม่ใช่ JSON object")
    return data


def _save_scheduler_state(state: dict[str, Any]) -> None:
    SCHEDULER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SCHEDULER_STATE_PATH.with_name(SCHEDULER_STATE_PATH.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, SCHEDULER_STATE_PATH)


def run_monthly_plan_if_due() -> str:
    """ส่งแผน DCA ของเดือนนี้ถ้าถึงเวลาแล้วและยังไม่เคยส่ง — เรียกซ้ำได้ปลอดภัย.

    ถึงเวลา = ตั้งแต่ 08:00 วันที่ 1 เป็นต้นไปจนสิ้นเดือน (เปิดคอมวันที่ 3 ก็ยังได้แผน)
    คืนสถานะเป็นสตริงเพื่อ log/เทสต์:
    ``not_yet`` · ``already_sent`` · ``seeded`` · ``sent`` · ``failed`` · ``gave_up`` ·
    ``state_unreadable``
    """
    now = _now_bangkok()
    month = now.strftime("%Y-%m")
    if now.day == 1 and now.hour < MONTHLY_PLAN_HOUR:
        return "not_yet"
    if month in _monthly_plan_sent_in_process:
        return "already_sent"

    try:
        state = _load_scheduler_state()
    except SchedulerStateUnreadable as exc:
        # ไม่รู้ว่าส่งไปแล้วหรือยัง: เดาว่า "ยัง" = อาจส่งซ้ำ+จ่าย AI ทุกชั่วโมง → ไม่ส่งแล้วฟ้องดัง ๆ
        logger.error("ข้ามแผน DCA รายเดือน — อ่านไฟล์สถานะไม่ได้: %s (ลบ/แก้ไฟล์นี้แล้วจะกลับมาทำงาน)", exc)
        return "state_unreadable"

    if state is None or not state.get("monthly_plan"):
        # ติดตั้งครั้งแรกกลางเดือน: อย่ายิงแผนของเดือนที่ผ่านไปครึ่งทางแล้วทันทีที่ deploy
        # เริ่มนับจากเดือนหน้า  (ติดตั้งครั้งแรกในวันที่ 1 เองก็ถือว่าเริ่มเดือนหน้าเช่นกัน)
        # "ยังไม่มีคีย์ monthly_plan" ก็คือติดตั้งครั้งแรก — ไฟล์นี้เก็บสถานะของสรุปราคา
        # รายวันด้วย ถ้างานนั้นเขียนไฟล์ก่อน การเช็คแค่ "ไม่มีไฟล์" จะส่งแผนเดือนนี้ทันที
        state = dict(state or {})
        state["monthly_plan"] = {"sent_month": month, "seeded_at": now.isoformat(timespec="seconds")}
        try:
            _save_scheduler_state(state)
        except OSError as exc:
            logger.error("เขียนไฟล์สถานะ scheduler ไม่ได้: %s", exc)
        _monthly_plan_sent_in_process.add(month)
        logger.info("เริ่มจำสถานะแผน DCA รายเดือนที่ %s — แผนแรกจะส่งเดือนถัดไป", SCHEDULER_STATE_PATH)
        return "seeded"

    plan = dict(state.get("monthly_plan") or {})
    if plan.get("sent_month") == month:
        _monthly_plan_sent_in_process.add(month)
        return "already_sent"

    failed = int(plan.get("failed_attempts") or 0) if plan.get("failed_month") == month else 0
    if failed >= MONTHLY_PLAN_MAX_FAILED_ATTEMPTS:
        return "gave_up"

    if now.day > 1 or now.hour > MONTHLY_PLAN_HOUR:
        logger.info("ส่งแผน DCA ของเดือน %s ย้อนหลัง (เครื่องไม่ได้เปิดตอน 08:00 วันที่ 1)", month)

    if generate_monthly_ai_advisor_and_notify():
        _monthly_plan_sent_in_process.add(month)
        plan = {"sent_month": month, "sent_at": now.isoformat(timespec="seconds")}
        status = "sent"
    else:
        plan = {**plan, "failed_month": month, "failed_attempts": failed + 1}
        status = "failed"
        if failed + 1 >= MONTHLY_PLAN_MAX_FAILED_ATTEMPTS:
            logger.error(
                "ส่งแผน DCA เดือน %s ไม่สำเร็จ %d ครั้ง — หยุดลองจนถึงเดือนหน้า "
                "(แก้ต้นเหตุแล้วลบ failed_attempts ใน %s แล้ว restart scheduler เพื่อลองใหม่)",
                month, failed + 1, SCHEDULER_STATE_PATH,
            )

    state["monthly_plan"] = plan
    try:
        _save_scheduler_state(state)
    except OSError as exc:
        logger.error("เขียนไฟล์สถานะ scheduler ไม่ได้: %s", exc)
    return status


def _format_allocation_plan(advice_result: dict) -> str:
    """สร้างข้อความแผนจัดสรรจาก **ตัวเลขของโมเดล** (ไม่ใช่จากข้อความ AI).

    เดิมใช้ regex แกะตัวเลขออกจากคำตอบของ AI ซึ่งเปราะและเสียเงินโดยไม่จำเป็น
    """
    allocation = advice_result.get("allocation") or {}
    if not allocation:
        return "- ไม่มี ETF ที่มีข้อมูลพร้อมจัดสรร (ดึงข้อมูลไม่ได้)"

    lines = [f"- {format_allocation_line(ticker, item)}" for ticker, item in allocation.items()]

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

        if tomorrow.day == 1:
            # วัน DCA = วันที่ 1 ⇒ พรุ่งนี้ 08:00 run_monthly_plan_if_due ส่งแผนเดียวกันอยู่แล้ว
            # จากราคาปิดที่ใหม่กว่า — แนบที่นี่ด้วย = ได้แผนสองใบติดกันที่ตัวเลขอาจต่างกันนิดหน่อย
            # แล้วไม่รู้จะเชื่อใบไหน (2026-09-30)
            plan = (
                f"- ส่งพรุ่งนี้ {MONTHLY_PLAN_HOUR:02d}:00 ในข้อความแผน DCA ต้นเดือน "
                "(คำนวณจากราคาปิดล่าสุด — ใช้ใบนั้นตอนกดซื้อ)"
            )
        else:
            # แผนจัดสรรมาจากโมเดลโดยตรง — ไม่เรียก AI (ไม่มีค่าใช้จ่าย) และไม่แกะตัวเลข
            # จากข้อความ AI อีกต่อไป (รอยเดิมของ AUDIT.md C3)
            try:
                # explain=False: ใช้แค่ allocation — ห้ามจ่ายค่า AI ให้ข้อความที่ถูกทิ้ง
                advice_result = get_monthly_advice(
                    budget_thb=dca_budget_thb, send_discord=False, explain=False
                )
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
    if delivery.get("skipped") and delivery.get("reason"):
        # ตั้งใจไม่ส่ง (ราคาปิดเดิม / รอบ 21:00) — ไม่ใช่ความล้มเหลว แต่ต้องอ่านออกจาก log ว่าทำไม
        return f"(ไม่ได้ส่งสรุปเข้า Discord รอบนี้: {delivery['reason']})"
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


# สรุปราคา "Daily Price Check" ของแต่ละรอบตรวจ price alert:
#   always    = check_alerts() ส่งเองทุกรอบ (``--job price_alert`` ที่ผู้ใช้สั่งเอง)
#   new_close = ส่งเมื่อมีแท่งราคาปิดใหม่ หรือมีเรื่องต้องเตือน (scheduler 09:00)
#   off       = ไม่ส่งสรุป ส่งเฉพาะ alert ที่ trigger/ตรวจไม่ได้ (scheduler 21:00)
DAILY_SUMMARY_ALWAYS = "always"
DAILY_SUMMARY_NEW_CLOSE = "new_close"
DAILY_SUMMARY_OFF = "off"
_DAILY_SUMMARY_MODES = (DAILY_SUMMARY_ALWAYS, DAILY_SUMMARY_NEW_CLOSE, DAILY_SUMMARY_OFF)
# คีย์ในไฟล์สถานะ scheduler (ไฟล์เดียวกับแผน DCA ต้นเดือน): แท่งราคาปิดล่าสุดที่สรุปไปแล้ว
PRICE_SUMMARY_STATE_KEY = "price_summary"


def _daily_summary_skip_reason(result: dict[str, Any], last_sent_bar: str | None) -> str | None:
    """เหตุผลที่ **ไม่ต้อง** ส่งสรุปรอบนี้ — ``None`` = ต้องส่ง.

    ข้ามได้กรณีเดียว: ราคาปิดเป็นแท่งเดิมที่สรุปไปแล้ว **และ** ไม่มีอะไรต้องเตือน
    ดึงราคาไม่ได้ / alert ตรวจไม่ได้ / alert trigger ต้องออกไปเสมอ ห้ามถูกตัดเพราะ "ซ้ำ"
    """
    bar = result.get("latest_bar_date")
    if not bar:
        return None  # ไม่รู้วันที่ของราคา = ดึงไม่ได้ ต้องให้เห็น
    if result.get("triggered") or result.get("unchecked") or result.get("unpriced_tickers"):
        return None
    if bar != last_sent_bar:
        return None
    return f"ราคาปิดวันที่ {pd.Timestamp(bar):%d/%m/%Y} สรุปไปแล้ว ยังไม่มีแท่งราคาใหม่"


def _deliver_daily_summary(result: dict[str, Any], webhook_url: str) -> None:
    """รอบ 09:00: ส่งสรุปเมื่อมีราคาปิดใหม่ — วันอาทิตย์/วันจันทร์ 09:00 และวันหยุดตลาด
    สหรัฐฯ ยังเป็นราคาปิดแท่งเดิม เดิมส่งซ้ำตัวเลขเดิมทุกตัว (2026-09-30)

    จำแท่งที่ส่งแล้วในไฟล์สถานะ ไม่ใช่ในหน่วยความจำ: คอนเทนเนอร์เริ่มใหม่ทุกครั้งที่เปิดเครื่อง
    อ่านไฟล์ไม่ได้ = ส่ง (สรุปซ้ำหนึ่งใบเสียน้อยกว่าสรุปที่หายไป) และห้ามเขียนทับไฟล์นั้น
    เพราะในไฟล์เดียวกันมีสถานะของแผน DCA ที่เขียนทับแล้วอาจส่งแผน+จ่ายค่า AI ซ้ำ
    """
    state: dict[str, Any] | None
    try:
        state = _load_scheduler_state()
        writable = True
    except SchedulerStateUnreadable as exc:
        logger.error("อ่านไฟล์สถานะ scheduler ไม่ได้ — ส่งสรุปราคาไปก่อนโดยไม่เช็คซ้ำ: %s", exc)
        state, writable = None, False

    last_sent = ((state or {}).get(PRICE_SUMMARY_STATE_KEY) or {}).get("last_bar_date")
    reason = _daily_summary_skip_reason(result, last_sent)
    if reason:
        result["daily_discord_result"] = {"success": False, "skipped": True, "reason": reason}
        return
    if not webhook_url:
        result["daily_discord_result"] = {"success": False, "skipped": True, "error": "missing webhook_url"}
        return

    delivery = send_daily_status(webhook_url, result["daily_summary"], len(result["triggered"]))
    result["daily_discord_result"] = delivery
    bar = result.get("latest_bar_date")
    if delivery.get("success") and bar and writable:
        new_state = dict(state or {})
        new_state[PRICE_SUMMARY_STATE_KEY] = {
            "last_bar_date": bar,
            "sent_at": _now_bangkok().isoformat(timespec="seconds"),
        }
        try:
            _save_scheduler_state(new_state)
        except OSError as exc:
            logger.error("เขียนไฟล์สถานะ scheduler ไม่ได้ (สรุปราคาพรุ่งนี้อาจซ้ำ): %s", exc)


def _deliver_unchecked_notice(result: dict[str, Any], webhook_url: str) -> None:
    """รอบ 21:00: ไม่ส่งสรุปราคา — alert ที่ trigger ถูก ``check_alerts()`` ส่งไปแล้ว
    ที่เหลือคือ alert ที่ **ตรวจไม่ได้** ซึ่งเดิมเดินทางไปถึง Discord ผ่านสรุปราคาเท่านั้น
    """
    unchecked = result.get("unchecked") or []
    if not unchecked:
        result["daily_discord_result"] = {
            "success": False,
            "skipped": True,
            "reason": "รอบนี้ส่งเฉพาะ alert ที่ถึงเงื่อนไขหรือตรวจไม่ได้",
        }
        return
    if not webhook_url:
        result["daily_discord_result"] = {"success": False, "skipped": True, "error": "missing webhook_url"}
        return
    result["daily_discord_result"] = send_unchecked_notice(webhook_url, unchecked)


def run_price_alert_job(daily_summary: str = DAILY_SUMMARY_ALWAYS) -> dict[str, Any]:
    """ตรวจ price alert หนึ่งรอบ แล้ว **รายงานผลออก stdout ครบทั้ง 3 สถานะ**.

    ใช้ทั้งจาก scheduler (09:00 ``new_close`` / 21:00 ``off``) และจาก ``--job price_alert``
    (``always``) — ดูความหมายของโหมดที่ ``DAILY_SUMMARY_*`` ข้างบน
    เมื่อไม่ได้ตั้ง webhook ไม่มีอะไรถูกส่งเลย stdout ของ scheduler จึงเป็นช่องทางเดียว
    ที่ผู้ใช้จะรู้ว่า "รอบนี้ตรวจไม่ได้"
    """
    if daily_summary not in _DAILY_SUMMARY_MODES:
        raise ValueError(f"daily_summary ต้องเป็นหนึ่งใน {_DAILY_SUMMARY_MODES} (ได้ {daily_summary!r})")
    result = check_alerts(send_daily_summary=daily_summary == DAILY_SUMMARY_ALWAYS)
    usable = check_result_contract_error(result) is None and not result["store_error"]
    # คลังเสีย: check_alerts() ส่งข้อความ "อ่านคลังไม่ได้" ของมันเองแล้ว
    if usable and daily_summary != DAILY_SUMMARY_ALWAYS:
        webhook_url = str(load_config()["notifications"].get("discord_webhook_url", "")).strip()
        if daily_summary == DAILY_SUMMARY_NEW_CLOSE:
            _deliver_daily_summary(result, webhook_url)
        else:
            _deliver_unchecked_notice(result, webhook_url)
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
            # 1) แผน DCA ต้นเดือน: 08:00 ตรงเวลา + ทุกชั่วโมง + ทันทีตอนเริ่ม (ส่งย้อนหลัง
            #    เมื่อเครื่องปิดอยู่ตอน 08:00 วันที่ 1) — ตัวฟังก์ชันกันส่งซ้ำเองด้วยไฟล์สถานะ
            schedule.every().day.at("08:00").do(_safe(run_monthly_plan_if_due))
            schedule.every().hour.do(_safe(run_monthly_plan_if_due))
            _safe(run_monthly_plan_if_due)()
            # 2) ทุกวัน 08:00 -> เช็คว่าพรุ่งนี้เป็นวัน DCA แล้วเตือนล่วงหน้า
            if notifications.get("dca_reminder", True):
                schedule.every().day.at("08:00").do(_safe(check_and_send_dca_reminder), webhook_url=webhook_url)
            # 3) ทุกวันจันทร์ 08:00 -> Weekly Summary (RSI + Return)
            if notifications.get("weekly_summary", True):
                schedule.every().monday.at("08:00").do(_safe(generate_weekly_report_and_notify), webhook_url=webhook_url)
            # 4) ทุกวัน 09:00 -> Technical Alert เฉพาะ RSI ผิดปกติ
            if notifications.get("rsi_alert", True):
                schedule.every().day.at("09:00").do(_safe(generate_daily_technical_alerts), webhook_url=webhook_url)
            # 4b) แผน DAR-DCA (พอร์ตทดลองแยก) — จังหวะเดียวกับแผนหลัก แต่ไฟล์สถานะ/ตัวกันซ้ำ
            #     ของตัวเอง (jobs/dar_monthly.py) ไม่แตะสถานะของแผน ERC และไม่ใช้ LLM
            schedule.every().day.at("08:00").do(_safe(run_dar_plan_if_due), webhook_url=webhook_url)
            schedule.every().hour.do(_safe(run_dar_plan_if_due), webhook_url=webhook_url)
            _safe(run_dar_plan_if_due)(webhook_url=webhook_url)
        # 5) ทุกวัน 09:00 และ 21:00 -> Price Alert (ไม่ต้องใช้ webhook)
        #    ผ่าน run_price_alert_job ไม่ใช่ check_alerts ดิบ ๆ — ผลลัพธ์ต้องถูก
        #    "อ่าน" ออกมาเป็น 3 สถานะ ไม่งั้น unchecked/store_error หายไปกับค่าคืนที่ทิ้ง
        #    สรุปราคา "Daily Price Check" ส่ง **วันละใบเดียว** ตอน 09:00 (หลังตลาดสหรัฐฯ ปิด)
        #    และเฉพาะเมื่อมีราคาปิดใหม่ — 21:00 ตลาดเพิ่งเปิด ตรวจ alert อย่างเดียว
        #    เดิมส่งสรุปทั้งสองรอบทุกวัน รวมกับ CI อีกใบ = 3 ใบต่อวันทำการ (2026-09-30)
        schedule.every().day.at("09:00").do(
            _safe(run_price_alert_job), daily_summary=DAILY_SUMMARY_NEW_CLOSE
        )
        schedule.every().day.at("21:00").do(_safe(run_price_alert_job), daily_summary=DAILY_SUMMARY_OFF)
        # 6) simulation (งานหลักของระบบ): ดึงข้อมูลสดเมื่อเก่าเกิน 7 วัน + รันแผนปัจจุบันในโลกจำลอง ทุกวัน 06:00
        #    และตอนเริ่มโปรเซส (ไม่ต้องใช้ webhook ไม่มี LLM) — รันหลังงานอื่นตอนเริ่ม เพราะครั้งแรกใช้เวลา 1–3 นาที
        schedule.every().day.at("06:00").do(_safe(run_simulation_refresh))
        _safe(run_simulation_refresh)()

        print(
            "Vaultis scheduler started: "
            f"discord = {bool(webhook_url)}, "
            f"monthly DCA plan (day 1 08:00, catch-up hourly) = {bool(webhook_url)}, "
            f"DCA reminder check (daily 08:00, DCA day {dca_day}) = "
            f"{bool(webhook_url) and notifications.get('dca_reminder', True)}, "
            f"weekly summary (Mon 08:00) = "
            f"{bool(webhook_url) and notifications.get('weekly_summary', True)}, "
            f"daily technical alert check (09:00, RSI abnormal only) = "
            f"{bool(webhook_url) and notifications.get('rsi_alert', True)}, "
            "price alert check (daily 09:00 + price summary when there is a new close, "
            "21:00 alerts only) = True, "
            "simulation refresh (daily 06:00 + startup) = True"
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
            # เรียกตรง ไม่ผ่าน wrapper ที่กลืน exception — CI ต้องแดงเมื่องานพัง
            get_monthly_advice(
                budget_thb=float(config["dca"]["monthly_budget_thb"]),
                user_initiated=monthly_ai_enabled(),
            )
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
    elif args.job == "dar_plan":
        # ส่งแผน DAR ของเดือนนี้ทันที (ข้ามการเช็คเวลา) แล้วจำว่าส่งแล้ว — scheduler จะไม่ส่งซ้ำ
        status = run_dar_plan_if_due(force=True)
        print(f"DAR-DCA plan: {status}")
        if status not in {"sent"}:
            raise SystemExit(1)
    elif args.job == "sim_refresh":
        # ดึงข้อมูลสดของ simulation + รันแผนปัจจุบันใหม่ทั้งหมดทันที (ข้ามการเช็กว่าถึงเวลา)
        outcome = run_simulation_refresh(force=True)
        print(f"simulation: {outcome}")
        if not outcome["ok"]:
            raise SystemExit(1)
    elif args.job == "all":
        # รัน scheduler ปกติ (ใช้เมื่อรันบนเครื่องตัวเอง)
        run_scheduler()
    else:
        raise ValueError(f"Unknown job: {args.job}")
