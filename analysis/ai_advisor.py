# -*- coding: utf-8 -*-
""" AI Advisor  DCA ETF  Groq API."""

import sys
import os
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from pathlib import Path
from typing import Any

from groq import Groq
import pandas as pd
from dotenv import load_dotenv

from alerts.notifier import send_discord_webhook
from analysis.financial_model import run_full_analysis
from analysis.macro import get_macro_data
from analysis.ta_compat import ta
from data.fetcher import fetch_adjusted_close_data
from utils.config import load_config

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(dotenv_path=ROOT_DIR / ".env", override=True)


def _get_groq_client() -> Groq:
    """ Groq client  lazy  error  import."""
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key or api_key == "your_key_here":
        raise ValueError(" GROQ_API_KEY  .env")
    return Groq(api_key=api_key)

def _build_explanation_prompt(full_analysis: dict[str, Any], macro_data: dict[str, Any], budget_thb: float) -> str:
    """Prompt: model only narrates; all numbers come from full_analysis JSON."""
    payload_text = json.dumps(full_analysis, ensure_ascii=False, indent=2)
    macro_text = json.dumps(macro_data, ensure_ascii=False, indent=2)
    budget_text = f"{budget_thb:,.0f}"
    now = datetime.now()
    month_name = now.strftime("%B")
    year = now.year
    return f"""You are a professional ETF investment advisor for long-term investors.

The JSON below is the COMPLETE quantitative output from Vaultis (scores, DCF, allocation). Treat it as ground truth.

RULES:
- Explain in Thai only. Do NOT recalculate, change, or contradict any numbers from the JSON.
- Do not invent new RSI, MA, intrinsic value, margin of safety, or allocation amounts.
- Reference the provided figures when you explain *why* each ETF scored as it did.

FULL_ANALYSIS_JSON:
{payload_text}

MACRO_CONTEXT_JSON:
{macro_text}

DCA budget: {budget_text} THB/month (day 1 of each month).

Respond in Thai with this structure (narrative prose is fine under each heading; use the exact headings):

🤖 Vaultis AI Advisor — {month_name} {year}
งบ DCA: {budget_text} บาท
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 สรุปความหมายของคะแนน (อ้างอิง JSON เท่านั้น)
(ครอบคลุมทุก ETF ใน analysis)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 แนวคิดการจัดสรร {budget_text} บาท
(อธิบายเหตุผลของสัดส่วนใน allocation — ห้ามคำนวณใหม่)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ ความเสี่ยงเดือนนี้
(2-3 ข้อ จาก macro context)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📅 แนะนำจังหวะ DCA
(เช่น ซื้อวันที่ 1 ของเดือน และข้อควรระวังสั้นๆ)"""


def _compute_support_resistance(price_series: pd.Series, window: int = 60) -> tuple[float, float]:
    """/."""
    cleaned = pd.to_numeric(price_series, errors="coerce").dropna()
    if cleaned.empty:
        raise ValueError("/")
    lookback = cleaned.tail(window)
    support = float(lookback.min())
    resistance = float(lookback.max())
    return round(support, 2), round(resistance, 2)


def _build_price_alerts_payload(price_df: pd.DataFrame, tickers: list[str]) -> dict[str, Any]:
    """ ETF  AI  price alerts."""
    if price_df.empty:
        raise ValueError(" ETF  price alerts")

    prepared = price_df.reindex(columns=tickers).sort_index().ffill()
    snapshots: list[dict[str, Any]] = []
    for ticker in tickers:
        if ticker not in prepared.columns or prepared[ticker].dropna().empty:
            raise ValueError(f" {ticker}")
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
    """ AI  Buy/Warning price alert  ETF ."""
    try:
        client = _get_groq_client()
        target_tickers = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]
        price_df = fetch_adjusted_close_data(target_tickers, years=10)
        payload = _build_price_alerts_payload(price_df, target_tickers)
        compact_data = json.dumps(payload, ensure_ascii=False, indent=2)
        prompt = f""" ETF  Price Alert
 DCA 

: {compact_data}

 ETF :
1. Buy Alert   (  Support)
2. Warning Alert   ( Overbought)
3. 

 JSON :
{{
  "alerts": [
    {{
      "ticker": "VOO",
      "buy_alert": 620.00,
      "warning_alert": 680.00,
      "buy_reason": " MA200 ",
      "warning_reason": "RSI  75 "
    }}
  ]
}}"""

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=2000,
        )
        raw_text = (response.choices[0].message.content or "").strip()
        if not raw_text:
            raise RuntimeError("Groq  alerts ")

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
            raise RuntimeError(" Groq  JSON ")

        raw_alerts = parsed.get("alerts", [])
        if not isinstance(raw_alerts, list):
            raise RuntimeError(" JSON  Groq  (missing alerts list)")

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
            raise RuntimeError("Groq  alerts ")

        return {
            "as_of": payload["as_of"],
            "source_data": payload,
            "alerts": alerts,
        }
    except Exception as exc:
        raise RuntimeError(f" Price Alerts: {exc}") from exc


def get_monthly_advice(budget_thb: float = 5000, send_discord: bool = True) -> dict[str, Any]:
    """ ETF  DCA  Groq."""
    try:
        if budget_thb <= 0:
            raise ValueError("budget_thb  0")

        client = _get_groq_client()

        full_analysis = run_full_analysis(budget_thb=budget_thb)
        macro_data = get_macro_data()
        prompt = _build_explanation_prompt(full_analysis, macro_data=macro_data, budget_thb=budget_thb)

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=2000,
        )

        result = response.choices[0].message.content
        advice_text = (result or "").strip()
        if not advice_text:
            raise RuntimeError("Groq ")

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
            "full_analysis": full_analysis,
            "macro_data": macro_data,
            "advice_text": advice_text,
            "discord_result": discord_result,
        }
    except Exception as exc:
        raise RuntimeError(f" AI Advisor: {exc}") from exc


if __name__ == "__main__":
    print(" ETF...")
    result = get_monthly_advice(budget_thb=5000)
    print(result)
