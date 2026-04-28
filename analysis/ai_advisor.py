"""โมดูล AI Advisor สำหรับแนะนำ DCA ETF รายเดือนด้วย Groq API."""

import sys
import os

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
    """สร้าง Groq client แบบ lazy เพื่อไม่ให้ error ตั้งแต่ตอน import."""
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key or api_key == "your_key_here":
        raise ValueError("กรุณาตั้งค่า GROQ_API_KEY ในไฟล์ .env")
    return Groq(api_key=api_key)

def _format_ticker_snapshot(price_series: pd.Series) -> dict[str, Any]:
    """คำนวณ RSI/MA/ผลตอบแทน 1M, 3M ของแต่ละ ETF."""
    cleaned = price_series.ffill().dropna()
    if len(cleaned) < 200:
        raise ValueError("ข้อมูลราคาไม่เพียงพอสำหรับคำนวณ MA200")

    latest_price = float(cleaned.iloc[-1])
    ma50 = float(ta.sma(cleaned, length=50).iloc[-1])
    ma200 = float(ta.sma(cleaned, length=200).iloc[-1])
    rsi14 = float(ta.rsi(cleaned, length=14).iloc[-1])

    ret_1m = float((cleaned.iloc[-1] / cleaned.iloc[-22] - 1.0) * 100.0) if len(cleaned) > 21 else 0.0
    ret_3m = float((cleaned.iloc[-1] / cleaned.iloc[-64] - 1.0) * 100.0) if len(cleaned) > 63 else 0.0

    return {
        "price": round(latest_price, 2),
        "rsi14": round(rsi14, 2),
        "ma50_status": "Above" if latest_price >= ma50 else "Below",
        "ma200_status": "Above" if latest_price >= ma200 else "Below",
        "return_1m_pct": round(ret_1m, 2),
        "return_3m_pct": round(ret_3m, 2),
    }


def _build_advisor_payload(price_df: pd.DataFrame, tickers: list[str]) -> dict[str, Any]:
    """จัดรูปข้อมูลสรุปตลาดเพื่อส่งให้ Gemini วิเคราะห์."""
    if price_df.empty:
        raise ValueError("ไม่พบข้อมูลราคา ETF")

    price_df = price_df.reindex(columns=tickers).sort_index().ffill()

    ticker_snapshot: dict[str, dict[str, Any]] = {}
    for ticker in tickers:
        if ticker not in price_df.columns or price_df[ticker].dropna().empty:
            raise ValueError(f"ไม่พบข้อมูลราคาของ {ticker}")
        ticker_snapshot[ticker] = _format_ticker_snapshot(price_df[ticker])

    corr = calculate_correlation_matrix(price_df[tickers]).round(3)
    corr_matrix = corr.to_dict()
    as_of_date = str(price_df.index[-1].date())

    return {"as_of": as_of_date, "tickers": ticker_snapshot, "correlation_matrix": corr_matrix}


def _build_prompt(data: dict[str, Any], macro_data: dict[str, Any], budget_thb: float, tickers: list[str]) -> str:
    ticker_list_text = " ".join(tickers)
    compact_data = json.dumps(data, ensure_ascii=False, indent=2)

    def _macro_value(key: str, nested_key: str = "value") -> Any:
        value = macro_data.get(key, "N/A")
        if isinstance(value, dict):
            return value.get(nested_key, "N/A")
        return value

    fed_rate = _macro_value("fed_funds_rate")
    cpi = _macro_value("inflation_cpi")
    treasury = _macro_value("us10y_treasury_yield")
    dxy = _macro_value("dxy_dollar_index")
    vix = _macro_value("vix_fear_index")
    vix_level = "สูง" if isinstance(vix, (int, float)) and vix >= 25 else "ปกติ"
    budget_text = f"{budget_thb:,.0f}"
    return f"""คุณเป็น AI ที่ปรึกษาการลงทุน ETF ระยะยาวสำหรับคนไทย
นักลงทุนมีงบ DCA {budget_text} บาทต่อเดือน

ข้อมูล Macro Economics ปัจจุบัน:
- Fed Rate: {fed_rate}%
- Inflation CPI: {cpi}%
- 10Y Yield: {treasury}%
- DXY: {dxy}
- VIX: {vix} ({vix_level})

ข้อมูล ETF:
{compact_data}

วิเคราะห์โดยนำ Macro มาประกอบด้วย
เช่น ถ้า VIX สูง → ตลาดกลัว แนะนำระวัง
ถ้า DXY แข็ง → GLDM อาจอ่อนตัว

ตอบเป็นภาษาไทยเท่านั้น ห้าม JSON ห้าม code block
ตอบในรูปแบบนี้เท่านั้น:

🤖 Vaultis AI Advisor — [เดือน ปี]
งบ DCA: {budget_text} บาท
─────────────────────────────
📊 การวิเคราะห์:
[ETF] — RSI [ค่า] [วิเคราะห์สั้นๆ] [✅/⚠️/❌]
(ทำครบทุกตัว {ticker_list_text})

💰 แนะนำแบ่งเงิน {budget_text} บาท:
[ETF] [จำนวนบาท] ([เปอร์เซ็นต์]%)
(เฉพาะตัวที่แนะนำซื้อเท่านั้น)

⚠️ ความเสี่ยงเดือนนี้:
[อธิบาย 1-2 บรรทัด]

📅 แนะนำวันที่ควรซื้อ:
[แนะนำช่วงเวลาที่เหมาะสม]

สุดท้ายให้แสดง JSON allocations ในรูปแบบนี้เสมอ:
ALLOCATIONS_JSON:
[
  {{"ticker": "VOO", "percent": 30, "amount_thb": 1500}},
  {{"ticker": "SCHD", "percent": 30, "amount_thb": 1500}},
  {{"ticker": "QQQM", "percent": 20, "amount_thb": 1000}},
  {{"ticker": "GLDM", "percent": 20, "amount_thb": 1000}}
]"""


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
    """วิเคราะห์ข้อมูล ETF ปัจจุบันและขอคำแนะนำ DCA รายเดือนจาก Groq."""
    try:
        if budget_thb <= 0:
            raise ValueError("budget_thb ต้องมากกว่า 0")

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
            raise RuntimeError("Groq ไม่ได้ส่งข้อความวิเคราะห์กลับมา")

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
        raise RuntimeError(f"เกิดข้อผิดพลาดในการวิเคราะห์ AI Advisor: {exc}") from exc


if __name__ == "__main__":
    print("กำลังวิเคราะห์ ETF...")
    result = get_monthly_advice(budget_thb=5000)
    print(result)
