# -*- coding: utf-8 -*-
"""โมดูลแจ้งเตือนผ่าน Discord Webhook."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import requests
from dotenv import load_dotenv
from utils.config import load_config

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(dotenv_path=ROOT_DIR / ".env", override=True)


def send_discord_webhook(
    webhook_url: str,
    title: str,
    description: str,
    is_positive: bool = True,
    embed_color: int | None = None,
) -> Dict[str, Any]:
    """ส่งข้อความแบบ Embed ไปยัง Discord Webhook."""
    try:
        if not webhook_url:
            raise ValueError("webhook_url ห้ามว่าง")

        color = embed_color if embed_color is not None else (0x2ECC71 if is_positive else 0xE74C3C)
        emoji = "🟢" if is_positive else "🔴"

        payload = {
            "embeds": [
                {
                    "title": f"{emoji} {title}",
                    "description": description,
                    "color": color,
                }
            ]
        }

        response = requests.post(webhook_url, json=payload, timeout=10)
        response.raise_for_status()
        return {"success": True, "status_code": response.status_code}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def build_weekly_summary_message(
    portfolio_value: float,
    invested_capital: float,
    rebalance_triggered: bool,
) -> tuple[str, str, bool]:
    """สร้างหัวข้อและเนื้อหาสรุปรายสัปดาห์สำหรับ Discord."""
    try:
        pnl = portfolio_value - invested_capital
        pnl_pct = (pnl / invested_capital * 100.0) if invested_capital > 0 else 0.0
        is_positive = pnl >= 0

        rebalance_text = "ต้อง Rebalance" if rebalance_triggered else "ยังไม่ต้อง Rebalance"
        title = "Vaultis Weekly ETF Summary"
        description = (
            f"มูลค่าพอร์ตปัจจุบัน: ${portfolio_value:,.2f}\n"
            f"เงินลงทุนสะสม: ${invested_capital:,.2f}\n"
            f"ผลตอบแทนรวม: {pnl_pct:.2f}% (${pnl:,.2f})\n"
            f"สถานะพอร์ต: {rebalance_text}"
        )
        return title, description, is_positive
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการสร้างข้อความรายสัปดาห์: {exc}") from exc


def send_technical_alert(
    webhook_url: str,
    symbol: str,
    rsi: float,
    price: float,
    ma200: float,
    previous_price: float,
) -> Dict[str, Any]:
    """ส่งแจ้งเตือน Technical Signal ผ่าน Discord เมื่อเข้าเงื่อนไข RSI/MA200."""
    try:
        if not webhook_url:
            raise ValueError("webhook_url ห้ามว่าง")

        rsi_line = f"RSI: {rsi:.1f} ⚪ Neutral Zone"
        ma_line = f"MA200: ราคาอยู่ต่ำกว่า MA200 ❌"
        signal_text = "Signal: ยังไม่มีสัญญาณเด่น"
        color = 0x3498DB

        # กำหนดสัญญาณทางเทคนิคตามลำดับความสำคัญที่ต้องการแจ้งเตือน
        if rsi < 30:
            rsi_line = f"RSI: {rsi:.1f} 🟢 Oversold Zone"
            signal_text = "Signal: อาจเป็นจังหวะเพิ่มสถานะ"
            color = 0x2ECC71
        elif rsi > 70:
            rsi_line = f"RSI: {rsi:.1f} 🔴 Overbought Zone"
            signal_text = "Signal: ระวังการปรับฐาน"
            color = 0xE67E22
        elif previous_price <= ma200 < price:
            signal_text = "Signal: Golden Signal - ราคาตัด MA200 ขึ้น"
            color = 0xF1C40F
        elif previous_price >= ma200 > price:
            signal_text = "Signal: Death Signal - ราคาหลุด MA200 ลง"
            color = 0xE74C3C
        else:
            return {"success": True, "skipped": True, "reason": "ไม่มีสัญญาณเทคนิคที่ต้องแจ้งเตือน"}

        if price >= ma200:
            ma_line = "MA200: ราคาอยู่เหนือ MA200 ✅"

        payload = {
            "embeds": [
                {
                    "title": f"📊 Technical Alert — {symbol}",
                    "description": f"{rsi_line}\n{ma_line}\n{signal_text}",
                    "color": color,
                }
            ]
        }

        response = requests.post(webhook_url, json=payload, timeout=10)
        response.raise_for_status()
        return {"success": True, "status_code": response.status_code}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def send_dca_reminder(
    webhook_url: str = "",
    dca_date_text: str = "วันที่ 1 ของเดือนหน้า",
    dca_budget_thb: float = 5000,
    fx_rate_thb: float = 33.5,
    ai_advice: str = "กำลังวิเคราะห์...",
) -> Dict[str, Any]:
    """ส่งแจ้งเตือน DCA ล่วงหน้าสำหรับวันพรุ่งนี้ผ่าน Discord."""
    try:
        webhook_url = (webhook_url or "").strip() or str(load_config()["notifications"]["discord_webhook_url"]).strip()
        if not webhook_url:
            raise ValueError("webhook_url ห้ามว่าง")

        budget_text = f"{dca_budget_thb:,.0f}"
        advice_text = (ai_advice or "").strip() or "- รอตรวจสอบข้อมูลตลาดเพิ่มเติม"
        description = (
            f"📅 DCA Reminder — พรุ่งนี้วันที่ {dca_date_text}\n"
            "───────────────────────────\n"
            f"💰 งบ DCA เดือนนี้: {budget_text} บาท\n"
            f"💱 FX Rate วันนี้: {fx_rate_thb:.2f} THB/USD\n\n"
            "🤖 AI แนะนำแบ่งเงินเดือนนี้:\n"
            f"{advice_text}\n\n"
            "⚠️ อย่าลืมเปิด Dime พรุ่งนี้!"
        )

        payload = {
            "embeds": [
                {
                    "title": "Vaultis DCA Reminder",
                    "description": description,
                    "color": 0x0099FF,
                }
            ]
        }

        response = requests.post(webhook_url, json=payload, timeout=10)
        response.raise_for_status()
        return {"success": True, "status_code": response.status_code}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def test_alert(webhook_url: str = "") -> Dict[str, Any]:
    """ส่งข้อความทดสอบการเชื่อมต่อ Discord Webhook."""
    payload = {
        "embeds": [
            {
                "title": "🚀 Vaultis Alert Test",
                "color": 0x00FF00,
                "fields": [
                    {"name": "Status", "value": "✅ Connected Successfully", "inline": False},
                    {
                        "name": "Time",
                        "value": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "inline": False,
                    },
                    {"name": "Message", "value": "Vaultis Discord Alert ทำงานแล้ว!", "inline": False},
                ],
            }
        ]
    }

    try:
        selected_webhook = (webhook_url or "").strip() or str(load_config()["notifications"]["discord_webhook_url"]).strip()
        if not selected_webhook:
            raise ValueError("กรุณาตั้งค่า Discord Webhook URL ในหน้า Settings ก่อนทดสอบ")

        response = requests.post(selected_webhook, json=payload, timeout=10)
        response.raise_for_status()
        return {"success": True, "status_code": response.status_code}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


if __name__ == "__main__":
    print(test_alert())
