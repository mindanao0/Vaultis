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
from analysis.correlation import calculate_correlation_matrix
from analysis.macro import get_macro_data
from analysis.ta_compat import ta
from data.fetcher import fetch_adjusted_close_data
from utils.config import get_tickers, load_config

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(dotenv_path=ROOT_DIR / ".env", override=True)


def _get_groq_client() -> Groq:
    """ Groq client  lazy  error  import."""
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key or api_key == "your_key_here":
        raise ValueError(" GROQ_API_KEY  .env")
    return Groq(api_key=api_key)

def _format_ticker_snapshot(price_series: pd.Series) -> dict[str, Any]:
    """ RSI/MA/ 1M, 3M  ETF."""
    cleaned = price_series.ffill().dropna()
    if len(cleaned) < 200:
        raise ValueError(" MA200")

    latest_price = float(cleaned.iloc[-1])
    ma50 = float(ta.sma(cleaned, length=50).iloc[-1])
    ma200 = float(ta.sma(cleaned, length=200).iloc[-1])
    rsi14 = float(ta.rsi(cleaned, length=14).iloc[-1])

    ret_1m = float((cleaned.iloc[-1] / cleaned.iloc[-22] - 1.0) * 100.0) if len(cleaned) > 21 else 0.0
    ret_3m = float((cleaned.iloc[-1] / cleaned.iloc[-64] - 1.0) * 100.0) if len(cleaned) > 63 else 0.0

    return {
        "price": round(latest_price, 2),
        "rsi14": round(rsi14, 2),
        "ma50": round(ma50, 2),
        "ma200": round(ma200, 2),
        "ma50_status": "Above" if latest_price >= ma50 else "Below",
        "ma200_status": "Above" if latest_price >= ma200 else "Below",
        "price_vs_ma50_position": "above" if latest_price >= ma50 else "below",
        "price_vs_ma200_position": "above" if latest_price >= ma200 else "below",
        "return_1m_pct": round(ret_1m, 2),
        "return_3m_pct": round(ret_3m, 2),
    }


def _build_advisor_payload(price_df: pd.DataFrame, tickers: list[str]) -> dict[str, Any]:
    """ Gemini ."""
    if price_df.empty:
        raise ValueError(" ETF")

    price_df = price_df.reindex(columns=tickers).sort_index().ffill()

    ticker_snapshot: dict[str, dict[str, Any]] = {}
    for ticker in tickers:
        if ticker not in price_df.columns or price_df[ticker].dropna().empty:
            raise ValueError(f" {ticker}")
        ticker_snapshot[ticker] = _format_ticker_snapshot(price_df[ticker])

    corr = calculate_correlation_matrix(price_df[tickers]).round(3)
    corr_matrix = corr.to_dict()
    as_of_date = str(price_df.index[-1].date())

    return {"as_of": as_of_date, "tickers": ticker_snapshot, "correlation_matrix": corr_matrix}


def _build_prompt(data: dict[str, Any], macro_data: dict[str, Any], budget_thb: float, tickers: list[str]) -> str:
    def _macro_value(key: str, nested_key: str = "value") -> Any:
        value = macro_data.get(key, "N/A")
        if isinstance(value, dict):
            return value.get(nested_key, "N/A")
        return value

    fed_rate = _macro_value("fed_funds_rate")
    dxy = _macro_value("dxy_dollar_index")
    vix = _macro_value("vix_fear_index")
    budget_text = f"{budget_thb:,.0f}"
    now = datetime.now()
    month_name = now.strftime("%B")
    year = now.year
    prompt_data = {
        "etf_data": data,
        "macro_data": {
            "vix_current": vix,
            "dxy_current": dxy,
            "fed_rate_current": fed_rate,
        },
    }
    prompt_data_text = json.dumps(prompt_data, ensure_ascii=False, indent=2)
    return f"""You are a professional ETF investment advisor for long-term investors.
Analyze the following ETF data and provide consistent, data-driven advice.

Current Data:
{prompt_data_text}

ANALYSIS FRAMEWORK (use this exact framework every time):

1. RSI Analysis:
   - RSI < 30: Oversold -> Strong Buy signal
   - RSI 30-45: Low -> Buy signal
   - RSI 45-55: Neutral -> Hold
   - RSI 55-70: High -> Caution
   - RSI > 70: Overbought -> Avoid / Wait

2. MA Signal:
   - Price above MA50 AND MA200: Strong uptrend
   - Price above MA50, below MA200: Weak trend
   - Price below MA50 AND MA200: Downtrend

3. Combined Signal Score (0-10):
   RSI score + MA score + Momentum score
   8-10: Strong Buy
   6-7: Buy
   4-5: Neutral
   2-3: Caution
   0-1: Avoid

For each ETF provide:
- Exact RSI value and zone
- MA50/MA200 status
- Signal Score (0-10)
- Recommendation with clear reason

Budget: {budget_text} THB/month
DCA Day: 1st of each month

Respond in Thai language with this EXACT format:

🤖 Vaultis AI Advisor — {month_name} {year}
งบ DCA: {budget_text} บาท
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 การวิเคราะห์:

[ETF] — RSI [value] ([zone]) | MA: [status] | Score: [x]/10
→ [recommendation in Thai]

(repeat for all 5 ETFs)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 แนะนำแบ่งเงิน {budget_text} บาท:
(list only ETFs with Score >= 5)
[ETF] [amount] บาท ([percent]%)

⚠️ ความเสี่ยงเดือนนี้:
[2-3 bullet points based on current macro data]

📅 แนะนำวันที่ควรซื้อ:
[specific date recommendation based on market conditions]

IMPORTANT RULES:
- Always use the scoring framework above
- Never skip any ETF in the analysis
- Budget allocation must sum to exactly {budget_text} THB
- Be consistent - same data should give same recommendation
- Include specific numbers (RSI, MA values) always"""


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

        advisor_tickers = get_tickers()
        price_df = fetch_adjusted_close_data(advisor_tickers, years=10)
        payload = _build_advisor_payload(price_df=price_df, tickers=advisor_tickers)
        macro_data = get_macro_data()
        prompt = _build_prompt(payload, macro_data=macro_data, budget_thb=budget_thb, tickers=advisor_tickers)

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
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
            "market_data": payload,
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
