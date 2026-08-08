# -*- coding: utf-8 -*-
"""โมดูลแจ้งเตือนผ่าน Discord Webhook.

**ข้อความในไฟล์นี้คือสิ่งที่ผู้ใช้อ่านจริงบนมือถือ** ไม่ใช่ log ภายใน จึงต้อง (1) มีเนื้อความ
ภาษาไทยครบ และ (2) ตัดสินสัญญาณด้วยนิยามกลางของระบบเท่านั้น

AUDIT_ROUND2_2026-08-07 (P2) — ก่อนหน้านี้ผิดทั้งสองข้อ:

1. คอมมิต ``fa5b139`` ("Fix encoding - change Thai text to English") ลบสตริงไทยและอีโมจิ
   ออกจากไฟล์นี้ทั้งไฟล์โดยไม่ได้ใส่ภาษาอังกฤษกลับเข้าไป เหลือแต่โครง f-string เปล่า
   ⇒ การ์ดที่ส่งเข้า Discord มีเนื้อความว่าง เช่น ``"MA200:  MA200 \\nSignal: "``
   (โดนทั้ง technical alert, สรุปรายสัปดาห์, DCA reminder และข้อความทดสอบ)
2. ไฟล์นี้ **ตัดสินสัญญาณเอง** ด้วย ``if rsi < 30 → เขียว`` โดยไม่แตะ
   ``technical/signal_rules.py`` เลย ⇒ RSI 22 ที่ราคาต่ำกว่า MA200 (oversold ใน
   **ขาลง**) ได้การ์ดสีเขียวชวนซื้อ เหมือนกันเป๊ะกับ RSI 22 ที่ราคาเหนือ MA200
   ทั้งสองเคสให้ payload เท่ากันทุกไบต์ (color=3066993 = เขียว) — นี่คือความล้มเหลว
   ที่ ``signal_rules.py`` ถูกเขียนขึ้นมาเพื่อกันโดยตรง: "oversold ในขาขึ้น = สะสม,
   oversold ในขาลง = เฝ้าดู ไม่ใช่เชียร์ซื้อมีดที่กำลังตก"

ตอนนี้ทุกป้ายและทุกสีมาจาก :mod:`technical.signal_rules` (``dca_signal`` / ``rsi_zone`` /
``overall_signal`` / ``thai_description``) — ห้ามเขียนเกณฑ์ RSI หรือเกณฑ์แนวโน้มซ้ำในไฟล์นี้
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import requests
from dotenv import load_dotenv
from technical.signal_rules import (
    ACCUMULATE,
    BULLISH,
    DOWNTREND,
    DOWNTREND_WATCH,
    NEUTRAL,
    NO_DATA,
    OVERBOUGHT_CAUTION,
    RSI_OVERBOUGHT,
    RSI_OVERSOLD,
    dca_signal,
    overall_signal,
    rsi_zone,
    thai_description,
)
from utils.config import load_config

ROOT_DIR = Path(__file__).resolve().parents[1]
# override=False: env จริงมาก่อนไฟล์เสมอ — นโยบายเดียวกับ utils/config.py
# (เดิมเป็น True ทำให้ .env ทับค่าที่ Docker/GitHub Actions ตั้งไว้แบบเงียบ ๆ)
load_dotenv(dotenv_path=ROOT_DIR / ".env", override=False)

SEP_LINE = "─" * 27

#: สีของ embed ต่อ **สัญญาณกลาง** หนึ่งค่า — สีคือสิ่งแรกที่ผู้ใช้เห็นก่อนอ่านตัวหนังสือ
#: จึงต้องเดินตามสัญญาณเสมอ ห้ามผูกกับ "RSI ต่ำ = เขียว" อีก (AUDIT_ROUND2_2026-08-07)
SIGNAL_COLORS: Dict[str, int] = {
    ACCUMULATE: 0x2ECC71,          # เขียว — ย่อตัวในขาขึ้น = จังหวะสะสมตามแผน DCA
    BULLISH: 0x27AE60,             # เขียวเข้ม — ขาขึ้นปกติ
    NEUTRAL: 0x3498DB,             # น้ำเงิน — กลาง ๆ ไม่มีสัญญาณชัด
    OVERBOUGHT_CAUTION: 0xE67E22,  # ส้ม — ร้อนแรง ระวังไล่ราคา
    DOWNTREND_WATCH: 0xE67E22,     # ส้ม — oversold ในขาลง: เฝ้าดู ไม่ใช่เชียร์ซื้อ
    DOWNTREND: 0xE74C3C,           # แดง — ขาลง
    NO_DATA: 0x95A5A6,             # เทา — ไม่รู้ ห้ามระบายเป็นเขียว/แดง
}

#: สีที่ผู้ใช้อ่านว่า "เขียว = ซื้อได้" — สัญญาณฝั่งขาลงต้องไม่มีสีในเซ็ตนี้เด็ดขาด
#: (``tests/test_notifier_signal.py`` ตรึงไว้)
GREEN_SIGNAL_COLORS = frozenset({SIGNAL_COLORS[ACCUMULATE], SIGNAL_COLORS[BULLISH]})

#: อีโมจินำหน้าบรรทัด Signal — เดินตามสัญญาณกลางชุดเดียวกับสี
SIGNAL_EMOJI: Dict[str, str] = {
    ACCUMULATE: "🟢",
    BULLISH: "🟢",
    NEUTRAL: "⚪",
    OVERBOUGHT_CAUTION: "🟠",
    DOWNTREND_WATCH: "🟠",
    DOWNTREND: "🔴",
    NO_DATA: "⚠️",
}

#: คำแนะนำเชิงปฏิบัติของป้ายสรุปจาก ``overall_signal()`` — เป็นแค่การ**แปลภาษา**
#: ของป้ายที่ signal_rules ตัดสินมาแล้ว ไม่ใช่เกณฑ์ชุดที่สอง
ACTION_TEXT_TH: Dict[str, str] = {
    "strong_buy": "ทยอยสะสมได้ (สัญญาณแรง)",
    "buy": "ทยอยสะสมตามแผน DCA",
    "hold": "ยังไม่ใช่จังหวะไล่ซื้อ — ถือ/รอยืนยัน",
    "sell": "ลดน้ำหนัก (ขาลงยืนยันด้วย death cross)",
    "no_data": "ข้อมูลไม่พร้อม — ห้ามตีความเป็นสัญญาณ",
}


def _finite(value: Any) -> bool:
    """เป็นตัวเลขจริงที่ใช้คำนวณได้หรือไม่ (``None``/NaN/inf/แปลงไม่ได้ = ไม่ใช่)."""
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _fmt_usd(value: Any) -> str:
    """ราคาที่อ่านไม่ได้ต้องเป็น ``$?`` ไม่ใช่ ``$0.00`` — ห้ามกุตัวเลขบนเส้นทางเงินจริง.

    ตรรกะเดียวกับ ``main._fmt_price`` แต่ **นำเข้าจากที่นั่นไม่ได้**: ``main.py`` import
    โมดูลนี้อยู่ (จะกลายเป็น circular import) — นี่เป็นตัวจัดรูปแบบการแสดงผล ไม่ใช่เกณฑ์
    ตัดสินใจ ส่วนเกณฑ์ทั้งหมดยังมาจาก ``technical/signal_rules.py`` ที่เดียว
    """
    if not _finite(value):
        return "$?"
    return f"${float(value):,.2f}"


def send_discord_webhook(
    webhook_url: str,
    title: str,
    description: str,
    is_positive: bool = True,
    embed_color: int | None = None,
) -> Dict[str, Any]:
    """ส่งข้อความแบบ Embed ไปยัง Discord Webhook.

    ``description`` ว่าง = ผู้ใช้ได้การ์ดเปล่า (และ Discord ตอบ 400 อยู่ดี) จึงถือเป็น
    ความผิดพลาดที่ต้องดังออกมา ไม่ใช่ปล่อยผ่านเงียบ ๆ — รอยเดิมของ ``fa5b139``
    คือการ์ดเนื้อความว่างที่ไม่มีใครจับได้เลย (AUDIT_ROUND2_2026-08-07)
    """
    try:
        if not webhook_url:
            raise ValueError("webhook_url ห้ามว่าง")
        if not str(description or "").strip():
            raise ValueError("description ห้ามว่าง — การ์ดเปล่าไม่บอกอะไรผู้ใช้เลย")

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
    """สร้างหัวข้อและเนื้อหาสรุปรายสัปดาห์สำหรับ Discord.

    ``invested_capital <= 0`` (หรือคำนวณไม่ได้) → ผลตอบแทนเป็น ``n/a`` **ไม่ใช่ 0.00%**
    เพราะ "ยังไม่มีเงินลงทุนสะสม" คนละเรื่องกับ "ลงทุนแล้วเสมอตัว"
    (AUDIT_ROUND2_2026-08-07 — กฎเดียวกับ ``main._fmt_price``)
    """
    try:
        if not _finite(portfolio_value) or not _finite(invested_capital):
            raise ValueError("มูลค่าพอร์ต/เงินลงทุนสะสมไม่ใช่ตัวเลขที่ใช้ได้ (None/NaN)")

        pnl = float(portfolio_value) - float(invested_capital)
        has_base = float(invested_capital) > 0
        pnl_pct = (pnl / float(invested_capital) * 100.0) if has_base else None
        is_positive = pnl >= 0

        pnl_text = (
            f"{pnl_pct:.2f}% (${pnl:,.2f})" if pnl_pct is not None
            else "n/a (ยังไม่มีเงินลงทุนสะสม)"
        )
        rebalance_text = "⚠️ ถึงเกณฑ์ต้อง Rebalance" if rebalance_triggered else "✅ ยังไม่ต้อง Rebalance"
        title = "Vaultis Weekly ETF Summary"
        description = (
            f"มูลค่าพอร์ตปัจจุบัน: {_fmt_usd(portfolio_value)}\n"
            f"เงินลงทุนสะสม: {_fmt_usd(invested_capital)}\n"
            f"ผลตอบแทนรวม: {pnl_text}\n"
            f"สถานะพอร์ต: {rebalance_text}"
        )
        return title, description, is_positive
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการสร้างข้อความรายสัปดาห์: {exc}") from exc


def _rsi_zone_line(rsi: Any) -> str:
    """บรรทัด RSI — โซนมาจาก ``rsi_zone()`` ไม่ใช่ ``if rsi < 30`` ในไฟล์นี้.

    อีโมจิของบรรทัดนี้บอก **โซน** เท่านั้น (ไม่ใช่คำตัดสินซื้อ/ขาย) คำตัดสินอยู่บรรทัด
    ``Signal:`` ซึ่งดูแนวโน้มประกอบด้วย — เดิม oversold ติด 🟢 มาแต่ต้นบรรทัด ทำให้
    ขาลงอ่านเป็นข่าวดีตั้งแต่บรรทัดแรก
    """
    zone = rsi_zone(rsi)
    if zone == NO_DATA:
        return "RSI: ⚠️ คำนวณไม่ได้ (ไม่ใช่ 'RSI ปกติ')"
    value = f"RSI: {float(rsi):.1f}"
    if zone == "oversold":
        return f"{value} 🔵 Oversold Zone (ต่ำกว่า {RSI_OVERSOLD:.0f})"
    if zone == "overbought":
        return f"{value} 🔴 Overbought Zone (สูงกว่า {RSI_OVERBOUGHT:.0f})"
    return f"{value} ⚪ Neutral Zone ({RSI_OVERSOLD:.0f}–{RSI_OVERBOUGHT:.0f})"


def build_technical_alert_payload(
    symbol: str,
    rsi: Any,
    price: Any,
    ma200: Any,
    previous_price: Any,
    ma50: Any = None,
) -> Dict[str, Any]:
    """ประกอบ payload ของ Technical Alert จาก **นิยามสัญญาณกลาง** ที่เดียว.

    คืน ``{"send", "signal", "label", "color", "reason", "payload"}`` — แยกจากการยิง
    เครือข่ายเพื่อให้เทสต์ตรวจข้อความ/สีที่ผู้ใช้เห็นได้ตรง ๆ

    สามสถานะที่ห้ามยุบรวมกัน (CLAUDE.md):

    - ``signal == NO_DATA``  = **ตรวจไม่ได้** (RSI/ราคา/MA200 ขาดหรือเป็น NaN) → ไม่ส่ง
      และผู้เรียกต้องรายงานว่าเป็นความล้มเหลว ห้ามนับเป็น "ปกติ"
    - ``send is False`` ทั้งที่มีข้อมูลครบ = **ตรวจแล้วไม่มีสัญญาณต้องเตือน**
    - ``send is True``   = มีสัญญาณจริง สี/ป้ายมาจาก ``dca_signal`` + ``overall_signal``
    """
    central = dca_signal(price, ma50, ma200, rsi)
    if central == NO_DATA:
        return {
            "send": False,
            "signal": NO_DATA,
            "label": "no_data",
            "color": SIGNAL_COLORS[NO_DATA],
            "reason": (
                f"{symbol}: ข้อมูลไม่พร้อม (RSI/ราคา/MA200 ขาดหรือคำนวณไม่ได้) — "
                "ไม่ส่งสัญญาณจากข้อมูลที่ไม่ครบ และนี่ไม่ได้แปลว่า RSI ปกติ"
            ),
            "payload": None,
        }

    price_f, ma200_f = float(price), float(ma200)
    uptrend = price_f >= ma200_f

    # การตัด MA200 เป็นข้อมูลที่ผู้เรียกมี (ราคาก่อนหน้า) ไม่ใช่เกณฑ์ RSI/แนวโน้มชุดใหม่
    # — ค่าที่ใช้ไม่ได้ = "ตรวจการตัดไม่ได้" ห้ามแปลว่า "ไม่มีการตัด" เงียบ ๆ
    prev_ok = _finite(previous_price)
    prev_f = float(previous_price) if prev_ok else None
    golden_cross = bool(prev_ok and prev_f <= ma200_f < price_f)
    death_cross = bool(prev_ok and prev_f >= ma200_f > price_f)

    zone = rsi_zone(rsi)
    if zone == "neutral" and not golden_cross and not death_cross:
        # ``golden_cross``/``death_cross`` เป็น False ได้จากสองสาเหตุที่ห้ามยุบรวมกัน:
        # "ตรวจแล้วไม่มีการตัด" กับ "ตรวจการตัดไม่ได้เพราะราคาก่อนหน้าใช้ไม่ได้"
        # เดิมทั้งสองคืนเหตุผลเดียวกันที่ยืนยันว่า "ไม่มีการตัด MA200" — เป็นคำกล่าวเท็จ
        # ในเคสหลัง และ ``data_ok=True`` ทำให้ผู้เรียกนับรอบนั้นเป็น "ตรวจแล้วปกติ เงียบได้"
        # ทั้งที่การตัดอาจเกิดขึ้นจริงและไม่มีใครตรวจ (AUDIT_ROUND2_2026-08-07)
        return {
            "send": False,
            "signal": central,
            "label": overall_signal(central, golden_cross, death_cross, rsi),
            "color": SIGNAL_COLORS.get(central),
            "cross_checked": prev_ok,
            "data_ok": prev_ok,
            "reason": (
                f"{symbol}: ไม่มีสัญญาณเทคนิคที่ต้องแจ้งเตือน "
                f"(RSI อยู่ในโซนกลาง และไม่มีการตัด MA200)"
                if prev_ok
                else (
                    f"{symbol}: RSI อยู่ในโซนกลาง แต่**ตรวจการตัด MA200 ไม่ได้** "
                    "(ไม่มีราคาก่อนหน้าที่ใช้ได้) — ไม่ใช่ 'ไม่มีการตัด'"
                )
            ),
            "payload": None,
        }

    label = overall_signal(central, golden_cross=golden_cross, death_cross=death_cross, rsi=rsi)
    color = SIGNAL_COLORS[central]

    lines = [_rsi_zone_line(rsi)]
    if uptrend:
        lines.append(f"MA200: ราคาอยู่เหนือ MA200 ✅ ({_fmt_usd(price)} / MA200 {_fmt_usd(ma200)})")
    else:
        lines.append(f"MA200: ราคาอยู่ต่ำกว่า MA200 ❌ ({_fmt_usd(price)} / MA200 {_fmt_usd(ma200)})")

    if golden_cross:
        lines.append("🟡 Golden Signal — ราคาตัดขึ้นเหนือ MA200")
    elif death_cross:
        lines.append("🔻 Death Signal — ราคาหลุด MA200 ลง")
    elif not prev_ok:
        lines.append("⚠️ ไม่มีราคาก่อนหน้าที่ใช้ได้ — ตรวจการตัด MA200 ไม่ได้ (ไม่ใช่ 'ไม่มีการตัด')")

    action = ACTION_TEXT_TH.get(label, label)
    lines.append(f"Signal: {SIGNAL_EMOJI.get(central, '')} {action} [{label}]")
    lines.append(f"บริบท: {thai_description(central)}")

    payload = {
        "embeds": [
            {
                "title": f"📊 Technical Alert — {symbol}",
                "description": "\n".join(lines),
                "color": color,
            }
        ]
    }
    return {
        "send": True,
        "signal": central,
        "label": label,
        "color": color,
        "reason": None,
        "payload": payload,
    }


def send_technical_alert(
    webhook_url: str,
    symbol: str,
    rsi: float,
    price: float,
    ma200: float,
    previous_price: float,
    ma50: float | None = None,
) -> Dict[str, Any]:
    """ส่งแจ้งเตือน Technical Signal ผ่าน Discord เมื่อเข้าเงื่อนไข RSI/MA200.

    ป้ายและสีมาจาก ``technical/signal_rules.py`` ทั้งหมด (ดู
    :func:`build_technical_alert_payload`) — ห้ามใส่เกณฑ์ RSI กลับเข้ามาในฟังก์ชันนี้
    """
    try:
        if not webhook_url:
            raise ValueError("webhook_url ห้ามว่าง")

        built = build_technical_alert_payload(
            symbol=symbol,
            rsi=rsi,
            price=price,
            ma200=ma200,
            previous_price=previous_price,
            ma50=ma50,
        )
        if not built["send"]:
            if built["signal"] == NO_DATA:
                # "ตรวจไม่ได้" ต้องดังเท่ากับ "ส่งไม่สำเร็จ" — ผู้เรียก (main.py) พิมพ์
                # ข้อความนี้ลง log ถ้าคืน success=True จะกลืนเป็น "ตรวจแล้วปกติ"
                return {
                    "success": False,
                    "skipped": True,
                    "data_ok": False,
                    "reason": built["reason"],
                    "error": built["reason"],
                }
            # ไม่ได้ส่ง และข้อมูลก็ไม่ครบพอจะสรุปว่า "ปกติ" (ตรวจการตัด MA200 ไม่ได้)
            # → ไม่ใช่ความล้มเหลวของการส่ง (success=True) แต่ต้องไม่ถูกนับเป็น
            # "ตรวจแล้วปกติ" เช่นกัน ผู้เรียกอ่าน ``data_ok`` เพื่อแยกสองอย่างนี้
            return {
                "success": True,
                "skipped": True,
                "data_ok": bool(built.get("data_ok", True)),
                "reason": built["reason"],
            }

        response = requests.post(webhook_url, json=built["payload"], timeout=10)
        response.raise_for_status()
        return {
            "success": True,
            "status_code": response.status_code,
            "signal": built["signal"],
            "label": built["label"],
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def build_dca_reminder_message(
    dca_date_text: str = "วันที่ 1 ของเดือนหน้า",
    dca_budget_thb: float = 5000,
    fx_rate_thb: float = 33.5,
    ai_advice: str = "",
) -> str:
    """เนื้อความเตือน DCA ล่วงหน้า — งบ/อัตราแลกเปลี่ยนที่อ่านไม่ได้ต้องติดป้าย ไม่ใช่พิมพ์ 0."""
    budget_text = f"{float(dca_budget_thb):,.0f} บาท" if _finite(dca_budget_thb) else "⚠️ อ่านงบ DCA ไม่ได้"
    fx_text = (
        f"{float(fx_rate_thb):.2f} THB/USD" if _finite(fx_rate_thb)
        else "⚠️ ดึงอัตราแลกเปลี่ยนไม่ได้"
    )
    advice_text = (ai_advice or "").strip() or "- ยังไม่มีแผนจัดสรร (คำนวณไม่สำเร็จ/ยังไม่ได้คำนวณ)"
    return (
        f"📅 DCA Reminder — พรุ่งนี้ {dca_date_text}\n"
        f"{SEP_LINE}\n"
        f"💰 งบ DCA เดือนนี้: {budget_text}\n"
        f"💱 FX Rate วันนี้: {fx_text}\n\n"
        "📊 แผนแบ่งเงินเดือนนี้ (คำนวณจากโมเดล):\n"
        f"{advice_text}\n\n"
        "⚠️ อย่าลืมเปิด Dime พรุ่งนี้!"
    )


def send_dca_reminder(
    webhook_url: str = "",
    dca_date_text: str = "วันที่ 1 ของเดือนหน้า",
    dca_budget_thb: float = 5000,
    fx_rate_thb: float = 33.5,
    ai_advice: str = "",
) -> Dict[str, Any]:
    """ส่งแจ้งเตือน DCA ล่วงหน้าสำหรับวันพรุ่งนี้ผ่าน Discord."""
    try:
        webhook_url = (webhook_url or "").strip() or str(load_config()["notifications"]["discord_webhook_url"]).strip()
        if not webhook_url:
            raise ValueError("webhook_url ห้ามว่าง")

        payload = {
            "embeds": [
                {
                    "title": "📅 Vaultis DCA Reminder",
                    "description": build_dca_reminder_message(
                        dca_date_text=dca_date_text,
                        dca_budget_thb=dca_budget_thb,
                        fx_rate_thb=fx_rate_thb,
                        ai_advice=ai_advice,
                    ),
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
                    {"name": "Status", "value": "✅ เชื่อมต่อสำเร็จ", "inline": False},
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
