"""โมดูล AI Advisor: Groq + โครงสร้าง Vaultis AI สำหรับคำแนะนำ DCA ETF."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from groq import Groq

from alerts.notifier import send_discord_webhook
from analysis.ta_compat import ta
from data.fetcher import fetch_adjusted_close_data
from utils.config import get_tickers, load_config

ROOT_DIR = Path(__file__).resolve().parents[1]

VAULTIS_ADVISOR_SYSTEM_PROMPT = """
You are Vaultis AI, a long-term ETF investment advisor for Thai retail investors.
- Explain investment reasoning in clear, simple Thai (mixed with English tickers/terms)
- You NEVER calculate numbers yourself — all figures come from the financial model
- You ONLY interpret and explain the data provided

Response structure (always follow this order):
**📊 ภาพรวมสัญญาณวันนี้** — 2-3 ประโยค สรุปภาพรวม macro
**🎯 ETF แนะนำ (เรียงตาม Score)** — แต่ละ ETF: ticker, score, signal, เหตุผล 1-2 ประโยค
**💰 แผน DCA เดือนนี้ (งบ 5,000 บาท)** — จัดสรรตาม score tier (Strong Buy 60%, Buy 30%, Neutral 10%)
**⚠️ ความเสี่ยงที่ควรระวัง** — 1-2 ข้อ

Rules:
- ใช้ "สัญญาณชี้ว่า…" ไม่ใช่ "แนะนำให้ซื้อ"
- ห้ามรับประกันผลตอบแทน
- ถ้า vix_warning = true ให้ขึ้นต้นด้วยคำเตือนผันผวนสูงก่อนเสมอ
- ตอบไม่เกิน 400 tokens
""".strip()


def _get_groq_client() -> Groq:
    """สร้าง Groq client; โหลดคีย์จากสภาพแวดล้อมหลัง load_dotenv."""
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key or api_key == "your_key_here":
        raise ValueError("กรุณาตั้งค่า GROQ_API_KEY ในไฟล์ .env")
    return Groq(api_key=api_key)


def _cell(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _build_user_message(
    etf_scores: list[dict[str, Any]],
    macro: dict[str, Any],
    portfolio: dict[str, Any] | None,
) -> str:
    """รวม etf_scores + macro (+ portfolio) เป็นข้อความตาราง plain text."""
    lines: list[str] = []
    lines.append("ข้อมูลจากโมเดลการเงิน (ใช้เฉพาะตัวเลขจากตารางนี้เท่านั้น)")
    lines.append("")
    lines.append("=== ETF scores ===")
    header = "ticker\tprice\tma50\tma200\trsi\ttotal_score\tsignal"
    lines.append(header)
    ranked = sorted(
        etf_scores,
        key=lambda row: float(row.get("total_score") or 0),
        reverse=True,
    )
    for row in ranked:
        lines.append(
            "\t".join(
                [
                    _cell(row.get("ticker")),
                    _cell(row.get("price")),
                    _cell(row.get("ma50")),
                    _cell(row.get("ma200")),
                    _cell(row.get("rsi")),
                    _cell(row.get("total_score")),
                    _cell(row.get("signal")),
                ]
            )
        )
    lines.append("")
    lines.append("=== Macro ===")
    macro_order = ["fed_rate", "vix", "dxy", "vix_warning", "monthly_dca_budget_thb"]
    seen_macro: set[str] = set()
    for key in macro_order:
        if key in macro:
            lines.append(f"{key}\t{_cell(macro.get(key))}")
            seen_macro.add(key)
    for key, val in sorted(macro.items()):
        if key not in seen_macro:
            lines.append(f"{key}\t{_cell(val)}")
    lines.append("")
    if portfolio:
        lines.append("=== Portfolio (user holdings snapshot) ===")
        lines.append(json.dumps(portfolio, ensure_ascii=False, indent=2))
    else:
        lines.append("=== Portfolio ===")
        lines.append("(none provided)")
    lines.append("")
    budget_hint = macro.get("monthly_dca_budget_thb")
    if budget_hint is not None:
        lines.append(
            f"งบ DCA รายเดือนที่ใช้จัดสรร: {budget_hint:,.0f} บาท "
            "(ปรับหัวข้อ **💰 แผน DCA** ให้ตรงกับจำนวนนี้)"
        )
    return "\n".join(lines)


def get_ai_advice(
    etf_scores: list[dict[str, Any]],
    macro: dict[str, Any],
    portfolio: dict[str, Any] | None = None,
) -> str:
    """ส่ง etf_scores + macro ให้ Groq ตาม system prompt Vaultis AI; คืนข้อความคำแนะนำ."""
    load_dotenv(dotenv_path=ROOT_DIR / ".env", override=True)
    client = _get_groq_client()
    user_content = _build_user_message(etf_scores, macro, portfolio)
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        temperature=0.1,
        max_tokens=500,
        messages=[
            {"role": "system", "content": VAULTIS_ADVISOR_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    )
    text = (response.choices[0].message.content or "").strip()
    if not text:
        raise RuntimeError("Groq ไม่ได้ส่งข้อความวิเคราะห์กลับมา")
    return text


def _compute_support_resistance(price_series: pd.Series, window: int = 60) -> tuple[float, float]:
    """คำนวณแนวรับ/แนวต้านแบบง่ายจากช่วงราคาย้อนหลัง."""
    cleaned = pd.to_numeric(price_series, errors="coerce").dropna()
    if cleaned.empty:
        raise ValueError("ไม่มีข้อมูลราคาสำหรับคำนวณแนวรับ/แนวต้าน")
    lookback = cleaned.tail(window)
    support = float(lookback.min())
    resistance = float(lookback.max())
    return round(support, 2), round(resistance, 2)


def _build_price_alerts_payload(price_df: pd.DataFrame, tickers: list[str]) -> dict[str, Any]:
    """เตรียมข้อมูลล่าสุดของ ETF สำหรับให้ AI แนะนำ price alerts."""
    if price_df.empty:
        raise ValueError("ไม่พบข้อมูลราคา ETF สำหรับสร้าง price alerts")

    prepared = price_df.reindex(columns=tickers).sort_index().ffill()
    snapshots: list[dict[str, Any]] = []
    for ticker in tickers:
        if ticker not in prepared.columns or prepared[ticker].dropna().empty:
            raise ValueError(f"ไม่พบข้อมูลราคาของ {ticker}")
        series = prepared[ticker]
        latest_price = float(series.dropna().iloc[-1])
        ma50 = float(ta.sma(series, length=50).iloc[-1])
        ma200 = float(ta.sma(series, length=200).iloc[-1])
        rsi14 = float(ta.rsi(series, length=14).iloc[-1])
        support, resistance = _compute_support_resistance(series, window=60)
        snapshots.append(
            {
                "ticker": ticker,
                "price": round(latest_price, 2),
                "rsi14": round(rsi14, 2),
                "ma50": round(ma50, 2),
                "ma200": round(ma200, 2),
                "support": support,
                "resistance": resistance,
            }
        )
    return {
        "as_of": str(prepared.index[-1].date()),
        "etfs": snapshots,
    }


def ai_suggest_alerts() -> dict[str, Any]:
    """ให้ AI วิเคราะห์และแนะนำ Buy/Warning price alert สำหรับ ETF หลัก."""
    load_dotenv(dotenv_path=ROOT_DIR / ".env", override=True)
    try:
        client = _get_groq_client()
        target_tickers = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]
        price_df = fetch_adjusted_close_data(target_tickers, years=10)
        payload = _build_price_alerts_payload(price_df, target_tickers)
        compact_data = json.dumps(payload, ensure_ascii=False, indent=2)
        prompt = f"""วิเคราะห์ ETF แต่ละตัวและแนะนำ Price Alert
ที่เหมาะสมสำหรับนักลงทุนระยะยาวที่ DCA รายเดือน

ข้อมูล: {compact_data}

สำหรับแต่ละ ETF ให้แนะนำ:
1. Buy Alert — ราคาที่น่าซื้อเพิ่ม (เช่น ใกล้ Support)
2. Warning Alert — ราคาที่ควรระวัง (เช่น Overbought)
3. เหตุผลสั้นๆ

ตอบในรูปแบบ JSON เท่านั้น:
{{
  "alerts": [
    {{
      "ticker": "VOO",
      "buy_alert": 620.00,
      "warning_alert": 680.00,
      "buy_reason": "ใกล้ MA200 จังหวะสะสม",
      "warning_reason": "RSI สูงกว่า 75 ระวังปรับฐาน"
    }}
  ]
}}"""

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = (response.choices[0].message.content or "").strip()
        if not raw_text:
            raise RuntimeError("Groq ไม่ได้ส่งคำแนะนำ alerts กลับมา")

        parsed: dict[str, Any] | None = None
        try:
            parsed_candidate = json.loads(raw_text)
            if isinstance(parsed_candidate, dict):
                parsed = parsed_candidate
        except json.JSONDecodeError:
            start = raw_text.find("{")
            end = raw_text.rfind("}")
            if start >= 0 and end > start:
                parsed_candidate = json.loads(raw_text[start : end + 1])
                if isinstance(parsed_candidate, dict):
                    parsed = parsed_candidate
        if not parsed:
            raise RuntimeError("ไม่สามารถแปลงผลลัพธ์จาก Groq เป็น JSON ได้")

        raw_alerts = parsed.get("alerts", [])
        if not isinstance(raw_alerts, list):
            raise RuntimeError("รูปแบบ JSON จาก Groq ไม่ถูกต้อง (missing alerts list)")

        alerts: list[dict[str, Any]] = []
        for item in raw_alerts:
            if not isinstance(item, dict):
                continue
            ticker = str(item.get("ticker", "")).strip().upper()
            if ticker not in target_tickers:
                continue
            try:
                buy_alert = float(item.get("buy_alert"))
                warning_alert = float(item.get("warning_alert"))
            except (TypeError, ValueError):
                continue
            alerts.append(
                {
                    "ticker": ticker,
                    "current_price": next(
                        (entry["price"] for entry in payload["etfs"] if entry["ticker"] == ticker),
                        None,
                    ),
                    "buy_alert": round(buy_alert, 2),
                    "warning_alert": round(warning_alert, 2),
                    "buy_reason": str(item.get("buy_reason", "")).strip(),
                    "warning_reason": str(item.get("warning_reason", "")).strip(),
                }
            )

        alerts = sorted(alerts, key=lambda row: target_tickers.index(row["ticker"]))
        if not alerts:
            raise RuntimeError("Groq ไม่ได้ส่ง alerts ที่ใช้งานได้กลับมา")

        return {
            "as_of": payload["as_of"],
            "source_data": payload,
            "alerts": alerts,
        }
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการแนะนำ Price Alerts: {exc}") from exc


def get_monthly_advice(budget_thb: float = 5000, send_discord: bool = True) -> dict[str, Any]:
    """ดึงคะแนน ETF + macro snapshot แล้วขอคำแนะนำ DCA รายเดือนจาก Groq."""
    from analysis.financial_model import build_etf_scores
    from analysis.macro import get_macro_snapshot
    from portfolio.tracker import get_portfolio_summary

    load_dotenv(dotenv_path=ROOT_DIR / ".env", override=True)
    try:
        if budget_thb <= 0:
            raise ValueError("budget_thb ต้องมากกว่า 0")

        advisor_tickers = get_tickers()
        etf_scores = build_etf_scores(list(advisor_tickers))
        macro = dict(get_macro_snapshot())
        macro["monthly_dca_budget_thb"] = float(budget_thb)

        holdings_df = get_portfolio_summary()
        portfolio: dict[str, Any] | None = None
        if not holdings_df.empty:
            portfolio = {"holdings": holdings_df.to_dict(orient="records")}

        advice_text = get_ai_advice(etf_scores, macro, portfolio)

        print("\n========== AI Advisor (Monthly DCA) ==========")
        print(advice_text)
        print("=============================================\n")

        webhook_url = str(load_config()["notifications"]["discord_webhook_url"]).strip()
        discord_result: dict[str, Any] = {"success": False, "skipped": True}
        if webhook_url and send_discord:
            discord_result = send_discord_webhook(
                webhook_url=webhook_url,
                title="Vaultis AI Advisor (Monthly DCA)",
                description=advice_text[:3900],
                is_positive=True,
                embed_color=0x00B300,
            )

        return {
            "budget_thb": budget_thb,
            "etf_scores": etf_scores,
            "macro": macro,
            "advice_text": advice_text,
            "discord_result": discord_result,
        }
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการวิเคราะห์ AI Advisor: {exc}") from exc
