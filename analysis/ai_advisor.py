"""โมดูล AI Advisor สำหรับคำแนะนำ DCA ETF.

หลักการ (AUDIT.md C3): **ตัวเลขทุกตัวคำนวณในโค้ด** — คะแนนจาก financial_model,
แผนจัดสรรงบจาก calculate_allocation, ระดับราคา alert จากกฎ technical ที่ตรวจสอบได้
LLM (Claude Sonnet 5 ผ่าน analysis/llm.py — ผู้ให้บริการเดียว ไม่มี fallback) มีหน้าที่
"อธิบายผลลัพธ์" เท่านั้น ห้ามคิดเลขหรือแต่งตัวเลขใหม่

ETF ที่ข้อมูลไม่พร้อมจะถูกส่งเข้า prompt ในสถานะ NO DATA พร้อมคำสั่งห้ามตีความ
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv

from alerts.notifier import send_discord_webhook
from analysis.llm import LLMDisabledError, chat_text
from analysis.ta_compat import ta
from data.fetcher import fetch_adjusted_close_data
from db.sentiment_models import get_latest_sentiment_summaries
from utils.config import get_tickers, load_config

ROOT_DIR = Path(__file__).resolve().parents[1]

# โหลด .env **ครั้งเดียวตอน import** — env ของโปรเซสคือแหล่งความจริงตอนรัน
# ไฟล์เป็นแค่ค่าเริ่มต้นตอนบูต (เดิมเรียกซ้ำในทุกฟังก์ชัน ทำให้ unset ตัวแปรไม่มีผล)
load_dotenv(dotenv_path=ROOT_DIR / ".env", override=False)

# ระดับ sentiment ที่ถือว่า "ลบรุนแรง" พอจะเตือนผู้ใช้ — เป็นบริบทเสริมเท่านั้น
# (ห้ามใช้ปรับคะแนน/allocation ที่ financial_model คำนวณแล้ว — invariant ของระบบ)
SENTIMENT_WARNING_SCORE = -0.4
SENTIMENT_WARNING_MIN_ARTICLES = 3

VAULTIS_ADVISOR_SYSTEM_PROMPT = """
You are Vaultis AI, a long-term ETF investment advisor for Thai retail investors.
- อธิบายเป็นภาษาไทยที่อ่านง่าย (ticker และศัพท์เทคนิคเป็นภาษาอังกฤษได้)
- ตัวเลขทั้งหมด (คะแนน จำนวนเงิน เปอร์เซ็นต์ ราคา) ถูกคำนวณมาแล้วจากโมเดลการเงิน
  คุณมีหน้าที่อธิบายเหตุผลเท่านั้น — ห้ามคำนวณใหม่ ห้ามเปลี่ยนตัวเลข ห้ามสร้างตัวเลขที่ไม่มีในข้อมูล
- ETF ที่ระบุว่า "NO DATA" คือดึงข้อมูลไม่สำเร็จ ให้แจ้งตรง ๆ ว่าข้อมูลไม่พร้อม
  ห้ามตีความเป็นสัญญาณซื้อหรือขายเด็ดขาด

โครงสร้างคำตอบ (ตามลำดับนี้เสมอ):
**📊 ภาพรวมสัญญาณวันนี้** — 2-3 ประโยคจากข้อมูล macro ที่ให้
**🎯 ETF แนะนำ (เรียงตาม Score)** — แต่ละ ETF: อธิบายว่าคะแนน/สัญญาณที่คำนวณมาสะท้อนอะไร
**💰 แผน DCA เดือนนี้** — อธิบายแผนจัดสรรใน "แผนจัดสรรที่คำนวณแล้ว" (ยกตัวเลขตามนั้นเป๊ะ ๆ)
  อธิบายด้วยว่าทำไมบางตัวได้มากกว่า/น้อยกว่าสัดส่วนเป้าหมาย (ดูคอลัมน์ตัวคูณ)
  และย้ำว่าทุกตัวยังได้ซื้อทุกเดือนเพื่อรักษาการกระจายความเสี่ยง
**⚠️ ความเสี่ยงที่ควรระวัง** — 1-2 ข้อ (รวม sentiment warning ถ้ามี)

Rules:
- ใช้ "สัญญาณชี้ว่า…" ไม่ใช่ "แนะนำให้ซื้อ"
- ห้ามรับประกันผลตอบแทน
- ถ้า vix_warning = true ให้ขึ้นต้นด้วยคำเตือนความผันผวนสูงก่อนเสมอ
- ถ้ามี "Sentiment warnings" ระบุ ETF ตัวใด ให้เพิ่มเป็นคำเตือนบริบทใน "ความเสี่ยงที่ควรระวัง"
  เท่านั้น (เช่น "ข่าวรอบล่าสุดของ X เป็นลบเด่นชัด") — ห้ามใช้ปรับคะแนน สัดส่วน หรือคำแนะนำซื้อ/ขาย
  ที่คำนวณมาแล้วเด็ดขาด เพราะ sentiment เป็นบริบทประกอบเท่านั้น
""".strip()


def _cell(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _sentiment_warnings(tickers: list[str]) -> list[dict[str, Any]]:
    """คืนรายชื่อ ETF ที่ sentiment ล่าสุด "ลบรุนแรง" — บริบทเสริมเท่านั้น (ไม่เข้าเลขคะแนน/allocation).

    คืน ``[]`` เงียบ ๆ เมื่อไม่ได้ตั้ง DATABASE_URL หรือดึงไม่ได้ — เหมือนนโยบาย sentiment job เดิม
    (ไม่ใช่ข้อผิดพลาดที่ต้อง fail advisor)
    """
    summaries = get_latest_sentiment_summaries(tickers)
    if not summaries:
        return []
    warnings: list[dict[str, Any]] = []
    for item in summaries:
        score = item.get("score")
        articles = item.get("total_articles") or 0
        if score is None or articles < SENTIMENT_WARNING_MIN_ARTICLES:
            continue
        if float(score) <= SENTIMENT_WARNING_SCORE:
            warnings.append(
                {
                    "ticker": item.get("symbol"),
                    "score": float(score),
                    "total_articles": int(articles),
                }
            )
    return warnings


def _build_user_message(
    etf_scores: list[dict[str, Any]],
    macro: dict[str, Any],
    portfolio: dict[str, Any] | None,
    allocation: dict[str, dict[str, Any]] | None = None,
    unallocated_thb: float | None = None,
    sentiment_warnings: list[dict[str, Any]] | None = None,
) -> str:
    """รวม etf_scores + allocation + macro (+ portfolio) เป็นข้อความ plain text ให้ LLM อธิบาย."""
    lines: list[str] = []
    lines.append("ข้อมูลจากโมเดลการเงิน (อธิบายจากตัวเลขเหล่านี้เท่านั้น ห้ามคำนวณใหม่)")
    lines.append("")
    lines.append("=== ETF scores (total_pct = คะแนน 0-100 ที่คำนวณแล้ว) ===")
    header = "ticker\tprice\tma50\tma200\trsi\ttotal_pct\tsignal"
    lines.append(header)

    ok_rows = [r for r in etf_scores if r.get("data_ok", True) and r.get("total_pct") is not None]
    no_data_rows = [r for r in etf_scores if r not in ok_rows]
    ranked = sorted(ok_rows, key=lambda row: float(row.get("total_pct") or 0), reverse=True)
    for row in ranked:
        lines.append(
            "\t".join(
                [
                    _cell(row.get("ticker")),
                    _cell(row.get("price")),
                    _cell(row.get("ma50")),
                    _cell(row.get("ma200")),
                    _cell(row.get("rsi")),
                    _cell(row.get("total_pct")),
                    _cell(row.get("signal")),
                ]
            )
        )
    if no_data_rows:
        lines.append("")
        lines.append("=== ETF ที่ข้อมูลไม่พร้อม (NO DATA — ห้ามตีความเป็นสัญญาณ) ===")
        for row in no_data_rows:
            lines.append(f"{_cell(row.get('ticker'))}\t{_cell(row.get('error') or 'ดึงข้อมูลไม่สำเร็จ')}")

    lines.append("")
    lines.append("=== แผนจัดสรรที่คำนวณแล้ว (คำนวณโดยโมเดล — ใช้ตัวเลขนี้เป๊ะ ๆ) ===")
    lines.append(
        "วิธีคิด: ฐาน = สัดส่วนเป้าหมายของพอร์ต แล้วปรับน้ำหนักด้วยคะแนน (0.6–1.4 เท่า) "
        "→ ซื้อทุกตัวทุกเดือนเพื่อรักษาการกระจายความเสี่ยง แต่เอียงเข้าหาตัวที่สัญญาณดีกว่า"
    )
    if allocation:
        lines.append("ticker\tเงิน(บาท)\tจัดสรรจริง\tเป้าหมาย\tตัวคูณ\tsignal")
        for ticker, item in allocation.items():
            tilt = item.get("tilt")
            lines.append(
                f"{ticker}\t{item.get('amount_thb', 0):,.0f}\t{item.get('percent', 0)}%\t"
                f"{item.get('target_percent', 0)}%\t{tilt if tilt is not None else 'N/A'}×\t{item.get('group', '')}"
            )
        if unallocated_thb and unallocated_thb > 0:
            lines.append(f"(ยังไม่จัดสรร: {unallocated_thb:,.0f} บาท — เศษจากการปัดหลักร้อย)")
    else:
        lines.append("(ไม่มี ETF ที่มีข้อมูลพร้อมจัดสรร — อธิบายให้ผู้ใช้ทราบว่าดึงข้อมูลไม่ได้)")

    lines.append("")
    lines.append("=== Sentiment warnings (บริบทเสริม — ห้ามใช้ปรับคะแนน/allocation) ===")
    if sentiment_warnings:
        for item in sentiment_warnings:
            lines.append(
                f"{item.get('ticker')}\tscore {item.get('score'):+.2f}\t"
                f"{item.get('total_articles')} ข่าว\t(ลบรุนแรง)"
            )
    else:
        lines.append("(ไม่มี ETF ที่ sentiment ลบรุนแรง หรือไม่มีข้อมูล sentiment)")

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
    return "\n".join(lines)


def get_ai_advice(
    etf_scores: list[dict[str, Any]],
    macro: dict[str, Any],
    portfolio: dict[str, Any] | None = None,
    allocation: dict[str, dict[str, Any]] | None = None,
    unallocated_thb: float | None = None,
    user_initiated: bool = False,
    sentiment_warnings: list[dict[str, Any]] | None = None,
) -> str:
    """ให้ LLM อธิบายคะแนน/แผนจัดสรรที่คำนวณแล้ว; คืนข้อความคำอธิบาย.

    ``user_initiated=True`` เฉพาะเมื่อผู้ใช้กดปุ่มเอง — ไม่งั้นจะ raise LLMDisabledError
    """
    user_content = _build_user_message(
        etf_scores, macro, portfolio, allocation, unallocated_thb, sentiment_warnings
    )
    text = chat_text(
        VAULTIS_ADVISOR_SYSTEM_PROMPT,
        user_content,
        # วัดจริง 2026-08-02: คำตอบไทยเต็มรูปแบบใช้ ~2,180 โทเคน ที่ 1,500 จึงชนเพดานทุกครั้ง
        # แล้วเข้า retry 2× — ซึ่งเสียเงิน "สองรอบ" เพราะรอบแรกที่ถูกตัดก็ถูกเก็บเงินไปแล้ว
        # (0.91 + 1.25 = 2.16 บาท/ครั้ง) ตั้งให้พอตั้งแต่รอบแรกถูกกว่าจ่ายค่า retry
        max_tokens=2500,
        user_initiated=user_initiated,
    )
    if not text:
        raise RuntimeError("LLM ไม่ได้ส่งข้อความวิเคราะห์กลับมา")
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
    """เตรียมตัวชี้วัดล่าสุดของ ETF สำหรับคำนวณระดับ alert."""
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
        if any(v != v for v in (latest_price, ma50, ma200, rsi14)):  # NaN check
            raise ValueError(f"ข้อมูลตัวชี้วัดของ {ticker} ไม่ครบ (NO DATA)")
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


def _suggest_alert_levels(snapshot: dict[str, Any]) -> dict[str, Any]:
    """คำนวณระดับ Buy/Warning alert ด้วยกฎ deterministic — ไม่ใช้ AI เดาราคา (AUDIT.md C3/M8).

    กฎ: Buy alert = ระดับรองรับที่ใกล้ที่สุดซึ่งต่ำกว่าราคาปัจจุบันชัดเจน
    (แนวรับ 60 วัน / MA200 / MA50), Warning alert = แนวต้าน 60 วัน หรือ +5%
    การันตี buy_alert < ราคาปัจจุบัน < warning_alert เสมอโดยโครงสร้าง
    """
    price = float(snapshot["price"])
    rsi = float(snapshot["rsi14"])

    buy_candidates = [
        ("แนวรับ 60 วัน", float(snapshot["support"])),
        ("MA200", float(snapshot["ma200"])),
        ("MA50", float(snapshot["ma50"])),
    ]
    below = [(name, lvl) for name, lvl in buy_candidates if lvl < price * 0.995]
    if below:
        buy_name, buy_level = max(below, key=lambda item: item[1])
    else:
        buy_name, buy_level = ("-5% จากราคาปัจจุบัน", price * 0.95)

    resistance = float(snapshot["resistance"])
    if resistance > price * 1.005:
        warn_name, warn_level = ("แนวต้าน 60 วัน", resistance)
    else:
        warn_name, warn_level = ("+5% จากราคาปัจจุบัน", price * 1.05)

    warn_reason = f"แตะ{warn_name} — ระวังไล่ราคา"
    if rsi > 70:
        warn_reason += f" (RSI {rsi:.0f} overbought อยู่แล้ว)"

    return {
        "ticker": snapshot["ticker"],
        "current_price": price,
        "buy_alert": round(buy_level, 2),
        "warning_alert": round(warn_level, 2),
        "buy_reason": f"ย่อลงใกล้{buy_name} (${buy_level:,.2f}) จังหวะสะสมตามแผน DCA",
        "warning_reason": warn_reason,
    }


def ai_suggest_alerts() -> dict[str, Any]:
    """แนะนำ Buy/Warning price alert สำหรับ ETF หลักด้วยกฎ technical ที่ตรวจสอบได้.

    หมายเหตุ: ตั้งแต่ Phase 1 (AUDIT.md C3) ฟังก์ชันนี้ไม่เรียก LLM แล้ว —
    ระดับราคาและเหตุผลมาจากกฎ deterministic ทั้งหมด ผลลัพธ์เหมือนเดิมทุก field
    """
    target_tickers = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]
    price_df = fetch_adjusted_close_data(target_tickers, years=10)
    payload = _build_price_alerts_payload(price_df, target_tickers)

    alerts = [_suggest_alert_levels(snapshot) for snapshot in payload["etfs"]]
    return {
        "as_of": payload["as_of"],
        "source_data": payload,
        "alerts": alerts,
    }


def _allocation_summary_lines(
    allocation: dict[str, dict[str, Any]],
    budget_thb: float,
    unallocated_thb: float,
    no_data_tickers: list[str],
    sentiment_warnings: list[dict[str, Any]] | None = None,
) -> list[str]:
    """สร้างข้อความสรุปแผนจัดสรรจากตัวเลขที่คำนวณแล้ว (ใช้ใน Discord — ไม่พึ่งข้อความ AI)."""
    lines = [f"📋 แผนจัดสรรจากโมเดล (งบ {budget_thb:,.0f} บาท):"]
    if allocation:
        for ticker, item in allocation.items():
            tilt = item.get("tilt")
            tilt_txt = f" [{tilt:.2f}× ของเป้า {item.get('target_percent', 0)}%]" if tilt else ""
            lines.append(
                f"• {ticker}: {item.get('amount_thb', 0):,.0f} บาท ({item.get('percent', 0)}%){tilt_txt}"
            )
        if unallocated_thb > 0:
            lines.append(f"• ยังไม่จัดสรร: {unallocated_thb:,.0f} บาท")
    else:
        lines.append("• ไม่มี ETF ที่มีข้อมูลพร้อมจัดสรร (ดึงข้อมูลไม่ได้)")
    if no_data_tickers:
        lines.append(f"⚠️ ข้อมูลไม่พร้อม (ไม่ถูกนำมาคิด): {', '.join(no_data_tickers)}")
    if sentiment_warnings:
        tickers_txt = ", ".join(f"{w['ticker']} ({w['score']:+.2f})" for w in sentiment_warnings)
        lines.append(f"🗞️ Sentiment ลบรุนแรง (บริบทเท่านั้น ไม่กระทบสัดส่วนข้างบน): {tickers_txt}")
    return lines


def get_monthly_advice(
    budget_thb: float = 5000,
    send_discord: bool = True,
    user_initiated: bool = False,
) -> dict[str, Any]:
    """คำนวณคะแนน + แผนจัดสรรในโค้ด (ฟรี) แล้วให้ LLM อธิบาย (มีค่าใช้จ่าย).

    ตัวเลขใน Discord มาจากโมเดลโดยตรง (ไม่ตัดจากข้อความ AI) — AUDIT.md C3

    ถ้า ``user_initiated=False`` และไม่ได้ตั้ง ``VAULTIS_LLM_AUTO=1``:
    **ยังคำนวณและส่งตัวเลขทุกอย่างตามปกติ** เพียงแต่ ``advice_text`` จะเป็นข้อความ
    แจ้งว่า AI ปิดอยู่ — ไม่มีค่าใช้จ่ายเกิดขึ้น
    """
    from analysis.financial_model import build_etf_scores, calculate_allocation
    from analysis.macro import get_macro_snapshot
    from portfolio.tracker import get_portfolio_summary

    try:
        if budget_thb <= 0:
            raise ValueError("budget_thb ต้องมากกว่า 0")

        advisor_tickers = get_tickers()
        etf_scores = build_etf_scores(list(advisor_tickers))
        macro = dict(get_macro_snapshot())
        macro["monthly_dca_budget_thb"] = float(budget_thb)

        # --- คำนวณแผนจัดสรรในโค้ด (ไม่ใช่หน้าที่ของ AI) ---
        scores_by_ticker = {row["ticker"]: row for row in etf_scores if row.get("ticker")}
        allocation = calculate_allocation(scores_by_ticker, float(budget_thb))
        allocated_total = sum(item.get("amount_thb", 0) for item in allocation.values())
        unallocated_thb = max(0.0, float(budget_thb) - float(allocated_total))
        no_data_tickers = [r["ticker"] for r in etf_scores if not r.get("data_ok", True)]
        sentiment_warnings = _sentiment_warnings(list(advisor_tickers))

        # --- สแนปช็อตพอร์ต: ส่งเฉพาะแถวที่ราคาปัจจุบันดึงได้จริง + ระบุตัวที่ขาด ---
        holdings_df = get_portfolio_summary()
        portfolio: dict[str, Any] | None = None
        if not holdings_df.empty:
            if "Price OK" in holdings_df.columns:
                ok_df = holdings_df[holdings_df["Price OK"]]
                missing = holdings_df.loc[~holdings_df["Price OK"], "Ticker"].tolist()
            else:
                ok_df = holdings_df
                missing = []
            portfolio = {"holdings": ok_df.to_dict(orient="records")}
            if missing:
                portfolio["price_unavailable"] = missing

        # ตัวเลขทั้งหมดข้างบนคำนวณเสร็จแล้วโดยไม่มีค่าใช้จ่าย
        # ส่วนคำอธิบายจาก AI เป็นส่วนที่เสียเงิน → เรียกเฉพาะเมื่อผู้ใช้กดปุ่มเอง
        ai_used = False
        try:
            advice_text = get_ai_advice(
                etf_scores,
                macro,
                portfolio,
                allocation=allocation,
                unallocated_thb=unallocated_thb,
                user_initiated=user_initiated,
                sentiment_warnings=sentiment_warnings,
            )
            ai_used = True
        except LLMDisabledError as exc:
            advice_text = str(exc)
        except RuntimeError as exc:
            # LLM ล้มเหลวจริง (คีย์หาย / provider ล่ม / ตอบว่างเปล่า) — ต่างจาก
            # LLMDisabledError ที่เป็นการปิดไว้ตั้งใจ  ห้ามให้ทั้งงานพัง เพราะตัวเลข
            # ทุกตัว (คะแนน/แผนจัดสรร) คำนวณเสร็จแล้วใน Python ไม่ได้พึ่ง LLM
            # แต่ต้องบอกให้ชัดว่าคำอธิบายหายไปเพราะอะไร ห้ามเงียบ (C1)
            advice_text = (
                f"⚠️ เรียก AI ไม่สำเร็จ: {exc}\n"
                "ตัวเลขและสัญญาณทั้งหมดด้านบนคำนวณจากโมเดลในระบบตามปกติ (ไม่ได้พึ่ง AI)"
            )

        print("\n========== Vaultis Advisor (Monthly DCA) ==========")
        print(advice_text)
        print("==================================================\n")

        webhook_url = str(load_config()["notifications"]["discord_webhook_url"]).strip()
        discord_result: dict[str, Any] = {"success": False, "skipped": True}
        if webhook_url and send_discord:
            summary_lines = _allocation_summary_lines(
                allocation, float(budget_thb), unallocated_thb, no_data_tickers, sentiment_warnings
            )
            description = "\n".join(summary_lines) + "\n\n" + advice_text
            discord_result = send_discord_webhook(
                webhook_url=webhook_url,
                title="Vaultis AI Advisor (Monthly DCA)",
                description=description[:3900],
                is_positive=True,
                embed_color=0x00B300,
            )

        return {
            "budget_thb": budget_thb,
            "etf_scores": etf_scores,
            "allocation": allocation,
            "unallocated_thb": unallocated_thb,
            "no_data_tickers": no_data_tickers,
            "sentiment_warnings": sentiment_warnings,
            "macro": macro,
            "advice_text": advice_text,
            # False = "ไม่ได้ใช้คำอธิบายจาก AI ในรอบนี้" ไม่ใช่ "ไม่มีค่าใช้จ่าย":
            # สาขา LLMDisabledError ไม่มีค่าใช้จ่ายจริง แต่สาขา RuntimeError คือ
            # เรียกไปแล้วล้มกลางทาง (เช่น ตอบมาแล้วถูกตัด) ซึ่งผู้ให้บริการคิดเงิน
            # ไปแล้ว — AUDIT_2026-08-06 D3.13 (คอมเมนต์เดิมอ้างว่าไม่มีค่าใช้จ่ายเสมอ)
            "ai_used": ai_used,
            "discord_result": discord_result,
        }
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการวิเคราะห์ Advisor: {exc}") from exc
