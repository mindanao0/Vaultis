from __future__ import annotations

<<<<<<< HEAD
import requests
import streamlit as st

API_BASE_URL = "http://localhost:8000"
=======
from datetime import datetime
import json
import os
import re
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf
from dotenv import load_dotenv
from plotly.subplots import make_subplots

# เพิ่ม path ของ root โปรเจกต์เพื่อให้ import โมดูลข้ามโฟลเดอร์ได้เมื่อรันผ่าน Streamlit
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from analysis.correlation import calculate_correlation_matrix
from analysis.ai_advisor import ai_suggest_alerts, get_monthly_advice
from analysis.ta_compat import ta
from analysis.returns import calculate_period_returns
from analysis.risk import calculate_risk_metrics
from alerts.notifier import test_alert
from alerts.price_alert import (
    add_alert,
    add_or_update_alert,
    check_alerts,
    delete_alert,
    get_active_alerts_with_distance,
    get_current_prices,
    list_alerts,
)
from data.fetcher import fetch_adjusted_close_data
from portfolio.backtest import run_portfolio_backtest
from portfolio.dca import simulate_monthly_dca
from portfolio.tracker import (
    add_transaction,
    estimate_dime_fee_thb,
    get_portfolio_summary,
    get_today_fx_rate_thb,
    get_total_summary,
    get_transactions,
)
from utils.config import add_ticker, get_tickers, load_config, remove_ticker, save_config
from utils.pdf_export import generate_monthly_report
>>>>>>> 2e136b0841b9b6f56b13f65995d33f9eea5fd827

load_dotenv()

THEME = {
    "main_bg": "#0F1117",
    "sidebar_bg": "#161B22",
    "card_bg": "#1C2128",
    "border": "#30363D",
    "text_primary": "#E6EDF3",
    "text_secondary": "#7D8590",
    "accent": "#388BFD",
    "positive": "#3FB950",
    "negative": "#F85149",
    "grid": "#21262D",
}

NAV_GROUPS = [
    ("Main", ["Overview", "Portfolio"]),
    ("Analysis", ["Backtest", "DCA Simulator", "Technical Signals", "Correlation"]),
    ("AI & Alerts", ["AI Advisor", "Macro", "Price Alerts"]),
    ("System", ["Settings"]),
]

NAV_ITEMS = [item for _, group_items in NAV_GROUPS for item in group_items]


def _inject_premium_theme() -> None:
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

        .stApp {{
            background-color: {THEME["main_bg"]};
            color: {THEME["text_primary"]};
            font-family: 'Inter', sans-serif;
        }}
        [data-testid="stSidebar"] {{
            background-color: {THEME["sidebar_bg"]};
            border-right: 1px solid {THEME["border"]};
            min-width: 220px;
            max-width: 220px;
        }}
        /* ลด gap ระหว่าง radio items */
        [data-testid="stSidebar"] [role="radiogroup"] {{
            gap: 0px !important;
        }}
        /* แต่ละ radio item */
        [data-testid="stSidebar"] label {{
            padding: 6px 12px !important;
            margin: 0px !important;
            border-radius: 6px !important;
            font-size: 14px !important;
            cursor: pointer !important;
        }}
        /* ซ่อน radio circle */
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {{
            margin: 0 !important;
            padding: 0 !important;
        }}
        /* ซ่อน default radio button dot */
        [data-testid="stSidebar"] input[type="radio"] {{
            display: none !important;
        }}
        /* ลด padding ทั่วไปใน sidebar */
        [data-testid="stSidebar"] .block-container {{
            padding-top: 1rem !important;
            padding-bottom: 1rem !important;
        }}
        [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {{
            gap: 0rem !important;
        }}
        h1, h2, h3 {{
            font-family: 'Inter', sans-serif !important;
            color: {THEME["text_primary"]} !important;
            font-weight: 600 !important;
            letter-spacing: 0;
        }}
        [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3, [data-testid="stSidebar"] p {{
            color: {THEME["text_primary"]};
        }}
        .logo {{
            font-size: 16px !important;
            font-weight: 600 !important;
            color: #E6EDF3 !important;
            padding: 8px 0 16px 0 !important;
            margin: 0 !important;
        }}
        .nav-group {{
            font-size: 10px !important;
            color: #7D8590 !important;
            letter-spacing: 0.08em !important;
            text-transform: uppercase !important;
            padding: 12px 0 2px 0 !important;
            margin: 0 !important;
        }}
        [data-testid="stSidebar"] .sidebar-footer {{
            border-top: 1px solid {THEME["border"]};
            margin-top: 12px;
            padding: 12px 16px;
            color: {THEME["text_secondary"]};
            font-size: 11px;
            line-height: 1.4;
        }}
        [data-testid="stSidebar"] .stButton button {{
            background: transparent !important;
            border: none !important;
            color: #7D8590 !important;
            text-align: left !important;
            padding: 6px 8px !important;
            font-size: 14px !important;
            border-radius: 6px !important;
            width: 100% !important;
            margin: 1px 0 !important;
        }}
        [data-testid="stSidebar"] .stButton button:hover {{
            background: #1C2128 !important;
            color: #E6EDF3 !important;
        }}
        div[data-testid="stButton"] > button {{
            background: {THEME["card_bg"]};
            border: 1px solid {THEME["border"]};
            color: {THEME["text_primary"]};
            border-radius: 8px;
            transition: all 0.2s ease;
        }}
        div[data-testid="stButton"] > button:hover {{
            border-color: {THEME["accent"]};
            color: {THEME["accent"]};
        }}
        .metric-card {{
            background: {THEME["card_bg"]};
            border: 1px solid {THEME["border"]};
            border-radius: 12px;
            padding: 1rem 1.1rem;
            min-height: 122px;
            opacity: 0;
            transform: translateY(14px);
            animation: metricFadeIn 0.75s ease-out forwards;
        }}
        .metric-title {{
            color: {THEME["text_secondary"]};
            font-size: 0.85rem;
            margin-bottom: 0.35rem;
        }}
        .metric-value {{
            color: {THEME["text_primary"]};
            font-size: 1.65rem;
            font-weight: 700;
            line-height: 1.1;
        }}
        .metric-change-positive {{ color: {THEME["positive"]}; font-weight: 600; font-size: 0.95rem; }}
        .metric-change-negative {{ color: {THEME["negative"]}; font-weight: 600; font-size: 0.95rem; }}
        .metric-change-neutral {{ color: {THEME["text_secondary"]}; font-weight: 600; font-size: 0.95rem; }}
        @keyframes metricFadeIn {{
            from {{
                opacity: 0;
                transform: translateY(14px) scale(0.985);
            }}
            to {{
                opacity: 1;
                transform: translateY(0) scale(1);
            }}
        }}
        .ticker-wrap {{
            overflow: hidden;
            border: 1px solid {THEME["border"]};
            border-radius: 10px;
            background: {THEME["sidebar_bg"]};
            margin-bottom: 1rem;
            padding: 0.55rem 0;
        }}
        .ticker-track {{
            white-space: nowrap;
            display: inline-block;
            animation: vaultis-ticker 20s linear infinite;
            color: {THEME["text_primary"]};
            font-size: 0.92rem;
        }}
        @keyframes vaultis-ticker {{
            from {{ transform: translateX(100%); }}
            to {{ transform: translateX(-100%); }}
        }}
        [data-testid="stDataFrame"] div[role="columnheader"] {{
            color: {THEME["accent"]} !important;
            background-color: {THEME["sidebar_bg"]} !important;
        }}
        [data-testid="stDataFrame"] div[role="gridcell"] {{
            background-color: {THEME["card_bg"]};
            color: {THEME["text_primary"]};
        }}
        [data-testid="stDataFrame"] [aria-rowindex="2"] div[role="gridcell"],
        [data-testid="stDataFrame"] [aria-rowindex="4"] div[role="gridcell"],
        [data-testid="stDataFrame"] [aria-rowindex="6"] div[role="gridcell"] {{
            background-color: {THEME["main_bg"]};
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _apply_plotly_dark_theme(fig: go.Figure) -> go.Figure:
    fig.update_layout(
        plot_bgcolor=THEME["main_bg"],
        paper_bgcolor=THEME["main_bg"],
        font=dict(color=THEME["text_primary"], family="Inter"),
        title_font=dict(color=THEME["text_primary"], family="Inter"),
        legend=dict(bgcolor="rgba(0,0,0,0)"),
    )
    fig.update_xaxes(gridcolor=THEME["grid"], zerolinecolor=THEME["grid"])
    fig.update_yaxes(gridcolor=THEME["grid"], zerolinecolor=THEME["grid"])
    return fig


def _render_custom_sidebar(default_page: str) -> str:
    if "page" not in st.session_state:
        st.session_state["page"] = default_page if default_page in NAV_ITEMS else "Overview"

    with st.sidebar:
        st.markdown('<p class="logo">VAULTIS</p>', unsafe_allow_html=True)

        st.markdown('<p class="nav-group">MAIN</p>', unsafe_allow_html=True)
        if st.button("Overview", key="nav_overview", use_container_width=True):
            st.session_state["page"] = "Overview"
        if st.button("Portfolio", key="nav_portfolio", use_container_width=True):
            st.session_state["page"] = "Portfolio"

        st.markdown('<p class="nav-group">ANALYSIS</p>', unsafe_allow_html=True)
        if st.button("Backtest", key="nav_backtest", use_container_width=True):
            st.session_state["page"] = "Backtest"
        if st.button("DCA Simulator", key="nav_dca_simulator", use_container_width=True):
            st.session_state["page"] = "DCA Simulator"
        if st.button("Technical Signals", key="nav_technical_signals", use_container_width=True):
            st.session_state["page"] = "Technical Signals"
        if st.button("Correlation", key="nav_correlation", use_container_width=True):
            st.session_state["page"] = "Correlation"

        st.markdown('<p class="nav-group">AI & ALERTS</p>', unsafe_allow_html=True)
        if st.button("AI Advisor", key="nav_ai_advisor", use_container_width=True):
            st.session_state["page"] = "AI Advisor"
        if st.button("Macro", key="nav_macro", use_container_width=True):
            st.session_state["page"] = "Macro"
        if st.button("Price Alerts", key="nav_price_alerts", use_container_width=True):
            st.session_state["page"] = "Price Alerts"

        st.markdown('<p class="nav-group">SYSTEM</p>', unsafe_allow_html=True)
        if st.button("Settings", key="nav_settings", use_container_width=True):
            st.session_state["page"] = "Settings"

        st.markdown(
            '<div class="sidebar-footer">Vaultis v1.0</div>',
            unsafe_allow_html=True,
        )

    return str(st.session_state.get("page", "Overview"))


def _render_market_ticker_bar(tickers: list[str], prices: pd.DataFrame) -> None:
    snippets: list[str] = []
    for ticker in tickers[:5]:
        series = prices[ticker].dropna() if ticker in prices.columns else pd.Series(dtype=float)
        if len(series) < 2:
            snippets.append(f"{ticker} N/A")
            continue
        last_px = float(series.iloc[-1])
        prev_px = float(series.iloc[-2])
        pct = ((last_px - prev_px) / prev_px) * 100 if prev_px else 0.0
        arrow = "▲" if pct >= 0 else "▼"
        color = THEME["positive"] if pct >= 0 else THEME["negative"]
        snippets.append(
            f'{ticker} ${last_px:,.2f} <span style="color:{color};font-weight:600;">{arrow} {pct:+.2f}%</span>'
        )
    timestamp = datetime.now().strftime("%H:%M:%S")
    content = f"{timestamp} &nbsp;&nbsp;&nbsp; " + " &nbsp;&nbsp;&nbsp; | &nbsp;&nbsp;&nbsp; ".join(snippets)
    st.markdown(
        f"""
        <div class="ticker-wrap">
            <div class="ticker-track">{content}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_overview_metrics(prices: pd.DataFrame, tickers: list[str]) -> None:
    return_df = calculate_period_returns(prices)
    yearly_col = "1Y (%)" if "1Y (%)" in return_df.columns else return_df.columns[-1]
    sortable = return_df[yearly_col].dropna()

    total_return = 0.0
    if len(prices.index) > 1:
        base_idx = prices.ffill().dropna().index[0]
        latest = prices.ffill().iloc[-1]
        base = prices.ffill().loc[base_idx]
        basket = (latest / base).mean()
        total_return = (float(basket) - 1.0) * 100

    best_etf = sortable.idxmax() if not sortable.empty else "-"
    best_val = float(sortable.max()) if not sortable.empty else 0.0
    worst_etf = sortable.idxmin() if not sortable.empty else "-"
    worst_val = float(sortable.min()) if not sortable.empty else 0.0

    vix_value = None
    try:
        macro_df = fetch_macro_data()
        if "VIX Fear Index" in macro_df.columns and not macro_df["VIX Fear Index"].dropna().empty:
            vix_value = float(macro_df["VIX Fear Index"].dropna().iloc[-1])
    except Exception:
        vix_value = None

    total_return_class = "metric-change-positive" if total_return >= 0 else "metric-change-negative"
    cards = st.columns(4)
    with cards[0]:
        st.markdown(
            f"""
            <div class="metric-card">
              <div class="metric-title">Total Return (Basket)</div>
              <div class="metric-value">{total_return:+.2f}%</div>
              <div class="{total_return_class}">10Y blended performance</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with cards[1]:
        st.markdown(
            f"""
            <div class="metric-card">
              <div class="metric-title">Best ETF (1Y)</div>
              <div class="metric-value">{best_etf}</div>
              <div class="metric-change-positive">{best_val:+.2f}%</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with cards[2]:
        st.markdown(
            f"""
            <div class="metric-card">
              <div class="metric-title">Worst ETF (1Y)</div>
              <div class="metric-value">{worst_etf}</div>
              <div class="metric-change-negative">{worst_val:+.2f}%</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with cards[3]:
        vix_text = f"{vix_value:.2f}" if vix_value is not None else "N/A"
        vix_class = "metric-change-neutral"
        if vix_value is not None:
            vix_class = "metric-change-negative" if vix_value >= 30 else "metric-change-positive"
        st.markdown(
            f"""
            <div class="metric-card">
              <div class="metric-title">VIX</div>
              <div class="metric-value">{vix_text}</div>
              <div class="{vix_class}">Market volatility index</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def _api_get(path: str):
    response = requests.get(f"{API_BASE_URL}{path}", timeout=20)
    response.raise_for_status()
    return response.json()


def _api_post(path: str, payload: dict):
    response = requests.post(f"{API_BASE_URL}{path}", json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def _api_delete(path: str):
    response = requests.delete(f"{API_BASE_URL}{path}", timeout=20)
    response.raise_for_status()
    return response.json()


def render_etf_page():
    st.header("ETF Data")
    cols = st.columns(5)
    endpoints = [
        ("/api/etf/prices", "Prices"),
        ("/api/etf/returns", "Returns"),
        ("/api/etf/risk", "Risk"),
        ("/api/etf/correlation", "Correlation"),
        ("/api/etf/technical", "Technical"),
    ]
    for idx, (path, title) in enumerate(endpoints):
        with cols[idx]:
            if st.button(title, use_container_width=True):
                st.session_state["etf_data"] = _api_get(path)
                st.session_state["etf_title"] = title
    if st.session_state.get("etf_data"):
        st.subheader(st.session_state.get("etf_title", "Result"))
        st.json(st.session_state["etf_data"])


<<<<<<< HEAD
def render_portfolio_page():
    st.header("Portfolio")
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("Summary", use_container_width=True):
            st.session_state["portfolio_data"] = _api_get("/api/portfolio")
    with c2:
        if st.button("Holdings", use_container_width=True):
            st.session_state["portfolio_data"] = _api_get("/api/portfolio/holdings")
    with c3:
        if st.button("History", use_container_width=True):
            st.session_state["portfolio_data"] = _api_get("/api/portfolio/history")

    with st.expander("Add transaction"):
        date = st.text_input("date", value="2026-01-01")
        ticker = st.text_input("ticker", value="VOO")
        shares = st.number_input("shares", min_value=0.0001, value=1.0)
        price_usd = st.number_input("price_usd", min_value=0.0001, value=100.0)
        amount_thb = st.number_input("amount_thb", min_value=0.01, value=3500.0)
        fx_rate = st.number_input("fx_rate", min_value=0.0001, value=35.0)
        fee = st.number_input("fee", min_value=0.0, value=0.0)
        note = st.text_input("note", value="")
        if st.button("POST /api/portfolio/add"):
            st.session_state["portfolio_data"] = _api_post(
                "/api/portfolio/add",
                {
                    "date": date,
                    "ticker": ticker,
                    "shares": shares,
                    "price_usd": price_usd,
                    "amount_thb": amount_thb,
                    "fx_rate": fx_rate,
                    "fee": fee,
                    "note": note,
                },
=======
    st.divider()
    st.subheader("2) ETF Management")
    st.caption("ETF ปัจจุบันทั้งหมด")
    for ticker in current_tickers:
        col_ticker, col_remove = st.columns([4, 1])
        with col_ticker:
            st.text(ticker)
        with col_remove:
            if st.button("Remove", key=f"remove_{ticker}"):
                try:
                    remove_ticker(ticker)
                    st.success(f"ลบ ETF {ticker} สำเร็จ")
                    st.rerun()
                except Exception as exc:
                    st.error(f"ลบ ETF ไม่สำเร็จ: {exc}")

    new_ticker = st.text_input("ค้นหา ETF ใหม่", value="", placeholder="เช่น VTI")
    if st.button("เพิ่ม ETF", type="secondary"):
        candidate = new_ticker.strip().upper()
        if not candidate:
            st.warning("กรุณากรอก Ticker ก่อน")
        elif candidate in current_tickers:
            st.info(f"{candidate} มีอยู่แล้วในรายการ")
        elif not _is_valid_etf_ticker(candidate):
            st.error("ไม่พบ ETF นี้ กรุณาตรวจสอบ Ticker")
        else:
            try:
                add_ticker(candidate)
                st.success(f"เพิ่ม ETF {candidate} สำเร็จ")
                st.rerun()
            except Exception as exc:
                st.error(f"เพิ่ม ETF ไม่สำเร็จ: {exc}")

    st.divider()
    st.subheader("3) Notification Settings")
    try:
        webhook_url = st.secrets["DISCORD_WEBHOOK_URL"]
    except Exception:
        webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "")

    if webhook_url.strip():
        st.success("Discord Webhook: เชื่อมต่อแล้ว")
    else:
        st.error("Discord Webhook: ไม่พบ DISCORD_WEBHOOK_URL ใน .env")

    weekly_summary_enabled = st.checkbox(
        "Weekly Summary ทุกวันจันทร์",
        value=bool(config["notifications"]["weekly_summary"]),
    )
    dca_reminder_enabled = st.checkbox(
        "DCA Reminder ก่อน 1 วัน",
        value=bool(config["notifications"]["dca_reminder"]),
    )
    rsi_alert_enabled = st.checkbox(
        "RSI Alert เมื่อ Oversold/Overbought",
        value=bool(config["notifications"]["rsi_alert"]),
    )
    if st.button("ทดสอบส่ง Discord"):
        if not webhook_url.strip():
            st.error("ไม่พบ DISCORD_WEBHOOK_URL จึงไม่สามารถทดสอบส่ง Discord ได้")
        else:
            test_result = test_alert(webhook_url=webhook_url)
            if test_result.get("success"):
                st.success("ส่งข้อความทดสอบไป Discord สำเร็จ")
            else:
                st.error(f"ส่งข้อความทดสอบไม่สำเร็จ: {test_result.get('error', 'unknown error')}")

    st.divider()
    st.subheader("4) Display Settings")
    current_default_page = str(config["display"]["default_page"])
    default_page = st.selectbox(
        "Default Page เมื่อเปิดแอป",
        page_options,
        index=page_options.index(current_default_page) if current_default_page in page_options else 0,
    )
    currency = st.radio(
        "สกุลเงินหลัก",
        options=["THB", "USD"],
        index=0 if str(config["display"]["currency"]).upper() == "THB" else 1,
        horizontal=True,
    )
    default_fx_rate = st.number_input(
        "อัตราแลกเปลี่ยน Default (ถ้าดึงไม่ได้)",
        min_value=1.0,
        value=float(config["display"]["default_fx_rate"]),
        step=0.1,
        format="%.4f",
    )

    if st.button("บันทึก Settings", type="primary"):
        updated_config = {
            **config,
            "dca": {
                "monthly_budget_thb": float(dca_budget),
                "day_of_month": int(dca_day),
            },
            "etf": {"tickers": get_tickers()},
            "notifications": {
                "discord_webhook_url": str(config["notifications"].get("discord_webhook_url", "")),
                "weekly_summary": bool(weekly_summary_enabled),
                "dca_reminder": bool(dca_reminder_enabled),
                "rsi_alert": bool(rsi_alert_enabled),
            },
            "display": {
                "default_page": default_page,
                "currency": currency,
                "default_fx_rate": float(default_fx_rate),
            },
        }
        try:
            save_config(updated_config)
            st.success("บันทึก Settings ลง config.json เรียบร้อยแล้ว")
            st.info("หากมี scheduler รันอยู่ ให้ restart เพื่อโหลดค่าใหม่")
        except Exception as exc:
            st.error(f"บันทึกค่าล้มเหลว: {exc}")


def _style_alert_rows(row: pd.Series) -> list[str]:
    state = str(row.get("Status", ""))
    distance = pd.to_numeric(pd.Series([row.get("Distance %", None)]), errors="coerce").iloc[0]
    if state == "Triggered":
        return ["background-color: rgba(220, 53, 69, 0.18)"] * len(row)
    if pd.notna(distance) and abs(float(distance)) <= 2.0:
        return ["background-color: rgba(46, 204, 113, 0.15)"] * len(row)
    return [""] * len(row)


def render_price_alerts_page() -> None:
    """หน้า Price Alerts: AI แนะนำ, ตั้งเอง, และติดตาม active alerts."""
    st.header("Price Alerts")
    tickers = get_tickers()
    if not tickers:
        st.warning("ยังไม่มี ETF ในระบบ กรุณาเพิ่มใน Settings")
        return

    all_alerts = list_alerts(include_triggered=True)
    history_alerts = [item for item in all_alerts if bool(item.get("triggered"))]
    active_alerts = get_active_alerts_with_distance(near_threshold_pct=2.0)
    latest_prices = get_current_prices(tickers)

    st.subheader("1) AI Suggest Alerts")
    if "ai_alert_suggestions" not in st.session_state:
        st.session_state["ai_alert_suggestions"] = []

    if st.button("🤖 ให้ AI แนะนำ Price Alerts", type="primary", key="ai_suggest_alerts_btn"):
        with st.spinner("กำลังวิเคราะห์ราคา ETF ด้วย AI..."):
            try:
                ai_result = ai_suggest_alerts()
                st.session_state["ai_alert_suggestions"] = ai_result.get("alerts", [])
                st.success("AI แนะนำ Price Alerts เรียบร้อยแล้ว")
            except Exception as exc:
                st.error(f"AI วิเคราะห์ไม่สำเร็จ: {exc}")

    suggested_alerts = st.session_state.get("ai_alert_suggestions", [])
    if suggested_alerts:
        for alert in suggested_alerts:
            ticker = str(alert.get("ticker", "")).upper()
            current_price = alert.get("current_price")
            if current_price is None:
                current_price = latest_prices.get(ticker)
            buy_alert = float(alert.get("buy_alert", 0.0))
            warning_alert = float(alert.get("warning_alert", 0.0))
            buy_reason = str(alert.get("buy_reason", "")).strip() or "-"
            warning_reason = str(alert.get("warning_reason", "")).strip() or "-"

            with st.container(border=True):
                st.markdown(f"### {ticker}")
                if current_price is not None:
                    st.markdown(f"ราคาปัจจุบัน: **${float(current_price):,.2f}**")
                else:
                    st.markdown("ราคาปัจจุบัน: **N/A**")
                st.markdown(f"🟢 Buy Alert: **${buy_alert:,.2f}** — {buy_reason}")
                st.markdown(f"🔴 Warning Alert: **${warning_alert:,.2f}** — {warning_reason}")

                c1, c2 = st.columns(2)
                with c1:
                    if st.button("ตั้ง Alert นี้ (Buy)", key=f"set_ai_buy_{ticker}"):
                        try:
                            add_or_update_alert(
                                ticker=ticker,
                                alert_type="below",
                                price=buy_alert,
                                note=f"AI Buy: {buy_reason}",
                            )
                            st.success(f"ตั้ง Buy Alert ของ {ticker} เรียบร้อย")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"ตั้ง Buy Alert ไม่สำเร็จ: {exc}")
                with c2:
                    if st.button("ตั้ง Alert นี้ (Warning)", key=f"set_ai_warn_{ticker}"):
                        try:
                            add_or_update_alert(
                                ticker=ticker,
                                alert_type="above",
                                price=warning_alert,
                                note=f"AI Warning: {warning_reason}",
                            )
                            st.success(f"ตั้ง Warning Alert ของ {ticker} เรียบร้อย")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"ตั้ง Warning Alert ไม่สำเร็จ: {exc}")
    else:
        st.info("กดปุ่มให้ AI วิเคราะห์เพื่อแนะนำ Buy/Warning alerts สำหรับ ETF หลัก")

    st.divider()
    st.subheader("2) Manual Alert")
    col_ticker, col_type, col_price = st.columns([2, 2, 2])
    with col_ticker:
        selected_ticker = st.selectbox("เลือก ETF", tickers, key="price_alert_ticker")
    with col_type:
        selected_type = st.selectbox(
            "เงื่อนไข",
            options=["below", "above"],
            format_func=lambda x: "Below (ต่ำกว่า)" if x == "below" else "Above (สูงกว่า)",
            key="price_alert_type",
        )
    with col_price:
        target_price = st.number_input("ราคาเป้าหมาย (USD)", min_value=0.01, value=100.0, step=0.5, format="%.2f")
    note = st.text_input("หมายเหตุ", value="", placeholder="เช่น จังหวะ DCA เพิ่ม")

    current_price = latest_prices.get(selected_ticker)
    if current_price is not None:
        st.caption(f"ราคาปัจจุบันของ {selected_ticker}: ${current_price:,.2f}")
    else:
        st.caption(f"ไม่พบราคาปัจจุบันของ {selected_ticker} ในขณะนี้")

    if st.button("ตั้ง Alert", type="primary"):
        try:
            created = add_alert(
                ticker=selected_ticker,
                alert_type=selected_type,
                price=float(target_price),
                note=note,
            )
            st.success(
                f"ตั้ง Alert สำเร็จ: {created['ticker']} {created['alert_type']} ${float(created['price']):,.2f}"
            )
            st.rerun()
        except Exception as exc:
            st.error(f"ตั้ง Alert ไม่สำเร็จ: {exc}")

    if st.button("เช็ค Alert ตอนนี้"):
        result = check_alerts()
        triggered_count = len(result.get("triggered", []))
        if triggered_count > 0:
            st.success(f"พบ Alert trigger แล้ว {triggered_count} รายการ (มีการส่ง Discord แล้ว)")
        else:
            st.info("ยังไม่มี Alert ที่เข้าเงื่อนไข")
        st.rerun()

    st.divider()
    st.subheader("3) Active Alerts")
    if not active_alerts:
        st.info("ยังไม่มี Active Alerts")
    else:
        active_rows: list[dict[str, object]] = []
        for item in active_alerts:
            ticker = str(item.get("ticker", "")).strip().upper()
            alert_type = str(item.get("alert_type", "")).lower()
            target = float(item.get("price", 0.0))
            now_price = item.get("current_price")
            distance = item.get("distance_pct")
            active_rows.append(
                {
                    "ID": item.get("id"),
                    "ETF": ticker,
                    "เงื่อนไข": "ต่ำกว่า" if alert_type == "below" else "สูงกว่า",
                    "ราคาเป้า (USD)": target,
                    "ราคาปัจจุบัน (USD)": now_price,
                    "Distance %": distance,
                    "Status": "🔴 Near Trigger" if bool(item.get("is_near_trigger")) else "Pending",
                    "หมายเหตุ": str(item.get("note", "")).strip() or "-",
                    "สร้างเมื่อ": str(item.get("created_at", "")),
                }
            )

        pending_df = pd.DataFrame(active_rows)
        show_cols = [
            "ETF",
            "เงื่อนไข",
            "ราคาเป้า (USD)",
            "ราคาปัจจุบัน (USD)",
            "Distance %",
            "Status",
            "หมายเหตุ",
            "สร้างเมื่อ",
        ]
        st.dataframe(
            pending_df[show_cols].style.format(
                {
                    "ราคาเป้า (USD)": "${:,.2f}",
                    "ราคาปัจจุบัน (USD)": "${:,.2f}",
                    "Distance %": "{:+.2f}%",
                },
                na_rep="N/A",
            ).apply(_style_alert_rows, axis=1),
            use_container_width=True,
        )

        delete_options = {f"{row['ETF']} | {row['เงื่อนไข']} | ${row['ราคาเป้า (USD)']:,.2f}": row["ID"] for _, row in pending_df.iterrows()}
        selected_delete_key = st.selectbox("เลือก Alert ที่ต้องการลบ", options=list(delete_options.keys()), key="delete_price_alert")
        if st.button("ลบ Alert"):
            selected_alert_id = delete_options.get(selected_delete_key)
            if selected_alert_id and delete_alert(str(selected_alert_id)):
                st.success("ลบ Alert เรียบร้อยแล้ว")
                st.rerun()
            else:
                st.warning("ไม่พบ Alert ที่เลือก")

    st.divider()
    st.subheader("4) Alert History")
    if not history_alerts:
        st.info("ยังไม่มีประวัติ Alert ที่ trigger")
    else:
        history_rows: list[dict[str, object]] = []
        for item in history_alerts:
            alert_type = str(item.get("alert_type", "")).lower()
            history_rows.append(
                {
                    "ETF": str(item.get("ticker", "")).strip().upper(),
                    "เงื่อนไข": "ต่ำกว่า" if alert_type == "below" else "สูงกว่า",
                    "ราคาเป้า (USD)": float(item.get("price", 0.0)),
                    "ราคาที่ Trigger (USD)": item.get("triggered_price"),
                    "หมายเหตุ": str(item.get("note", "")).strip() or "-",
                    "Triggered At": str(item.get("triggered_at", "")),
                }
            )
        history_df = pd.DataFrame(history_rows).sort_values("Triggered At", ascending=False)
        st.dataframe(
            history_df.style.format(
                {
                    "ราคาเป้า (USD)": "${:,.2f}",
                    "ราคาที่ Trigger (USD)": "${:,.2f}",
                },
                na_rep="N/A",
            ),
            use_container_width=True,
        )


def calculate_technical_signals(price_series: pd.Series) -> pd.DataFrame:
    """คำนวณสัญญาณเทคนิค MA50, MA200 และ RSI จากราคาปิดแบบปรับแล้ว."""
    try:
        signals = pd.DataFrame(index=price_series.index)
        signals["Price"] = price_series
        signals["MA50"] = ta.sma(price_series, length=50)
        signals["MA200"] = ta.sma(price_series, length=200)
        signals["RSI14"] = ta.rsi(price_series, length=14)
        return signals
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการคำนวณ Technical Signals: {exc}") from exc


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_ohlc_data(tickers: list[str], years: int = 10) -> dict[str, pd.DataFrame]:
    """ดึงข้อมูล OHLC ของ ETF หลายตัวสำหรับวาดกราฟ Candlestick."""
    try:
        end_date = pd.Timestamp.today()
        start_date = end_date - pd.DateOffset(years=years)
        raw_data = yf.download(
            tickers=tickers,
            start=start_date.strftime("%Y-%m-%d"),
            end=end_date.strftime("%Y-%m-%d"),
            interval="1d",
            auto_adjust=False,
            progress=False,
            group_by="ticker",
        )
    except Exception:
        st.warning("ไม่สามารถดึงข้อมูลได้ กรุณารอสักครู่")
        return {}

    if raw_data.empty:
        return {}

    ohlc_map: dict[str, pd.DataFrame] = {}
    if isinstance(raw_data.columns, pd.MultiIndex):
        for ticker in tickers:
            if ticker not in raw_data.columns.get_level_values(0):
                continue
            ticker_df = raw_data[ticker][["Open", "High", "Low", "Close"]].dropna(how="all").sort_index()
            if not ticker_df.empty:
                ohlc_map[ticker] = ticker_df
        return ohlc_map

    ticker = tickers[0]
    required_cols = ["Open", "High", "Low", "Close"]
    if all(col in raw_data.columns for col in required_cols):
        ohlc_map[ticker] = raw_data[required_cols].dropna(how="all").sort_index()
    return ohlc_map


def _rsi_status(rsi_value: float) -> str:
    if rsi_value > 70:
        return "Overbought"
    if rsi_value < 30:
        return "Oversold"
    return "Neutral"


def _overall_signal(price: float, ma50: float, ma200: float, rsi_value: float) -> str:
    if rsi_value > 70:
        return "Overbought"
    if price >= ma50 and price >= ma200 and rsi_value <= 70:
        return "Buy Zone"
    return "Neutral"


def render_technical_signals_page(prices: pd.DataFrame) -> None:
    """หน้า Technical Signals แบบกราฟ Candlestick + RSI + Signal Cards."""
    st.header("Technical Signals")
    technical_tickers = get_tickers()
    if not technical_tickers:
        st.warning("ยังไม่มี ETF ในระบบ กรุณาเพิ่มใน Settings")
        return

    selected_ticker = st.selectbox("เลือก ETF", technical_tickers, index=0)

    with st.spinner("กำลังโหลดข้อมูล..."):
        ohlc_map = fetch_ohlc_data(technical_tickers, years=10)
    selected_ohlc = ohlc_map.get(selected_ticker)
    if selected_ohlc is None or selected_ohlc.empty:
        st.warning(f"ไม่พบข้อมูล OHLC สำหรับ {selected_ticker}")
        return

    selected_signals = calculate_technical_signals(prices[selected_ticker]).dropna(subset=["MA50", "MA200", "RSI14"])
    aligned_signals = selected_signals.reindex(selected_ohlc.index)
    aligned_signals[["MA50", "MA200", "RSI14"]] = aligned_signals[["MA50", "MA200", "RSI14"]].ffill()

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        row_heights=[0.7, 0.3],
        subplot_titles=(f"{selected_ticker} Candlestick + MA50/MA200", "RSI (14)"),
    )

    fig.add_trace(
        go.Candlestick(
            x=selected_ohlc.index,
            open=selected_ohlc["Open"],
            high=selected_ohlc["High"],
            low=selected_ohlc["Low"],
            close=selected_ohlc["Close"],
            name="Candlestick",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=aligned_signals.index,
            y=aligned_signals["MA50"],
            mode="lines",
            line=dict(color="orange", width=2),
            name="MA50",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=aligned_signals.index,
            y=aligned_signals["MA200"],
            mode="lines",
            line=dict(color="red", width=2),
            name="MA200",
        ),
        row=1,
        col=1,
    )

    fig.add_trace(
        go.Scatter(
            x=aligned_signals.index,
            y=aligned_signals["RSI14"],
            mode="lines",
            line=dict(color="deepskyblue", width=2),
            name="RSI",
        ),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=aligned_signals.index,
            y=[70] * len(aligned_signals.index),
            mode="lines",
            line=dict(color="red", dash="dash"),
            name="Overbought (70)",
        ),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=aligned_signals.index,
            y=[30] * len(aligned_signals.index),
            mode="lines",
            line=dict(color="green", dash="dash"),
            name="Oversold (30)",
        ),
        row=2,
        col=1,
    )
    fig.add_hrect(y0=70, y1=100, fillcolor="rgba(255, 0, 0, 0.12)", line_width=0, row=2, col=1)
    fig.add_hrect(y0=0, y1=30, fillcolor="rgba(0, 128, 0, 0.12)", line_width=0, row=2, col=1)

    fig.update_layout(height=850, xaxis_rangeslider_visible=False, legend_title_text="Indicators")
    fig.update_yaxes(title_text="Price (USD)", row=1, col=1)
    fig.update_yaxes(title_text="RSI", range=[0, 100], row=2, col=1)
    st.plotly_chart(_apply_plotly_dark_theme(fig), use_container_width=True)

    st.subheader("Signal Summary Cards")
    columns = st.columns(len(technical_tickers))
    for idx, ticker in enumerate(technical_tickers):
        ticker_prices = prices[ticker].dropna()
        ticker_signals = calculate_technical_signals(ticker_prices).dropna(subset=["MA50", "MA200", "RSI14"])
        if ticker_signals.empty:
            with columns[idx]:
                st.warning(f"{ticker}: ไม่พอข้อมูล")
            continue

        latest = ticker_signals.iloc[-1]
        current_price = float(latest["Price"])
        ma50 = float(latest["MA50"])
        ma200 = float(latest["MA200"])
        rsi_value = float(latest["RSI14"])

        ma50_state = "Above" if current_price >= ma50 else "Below"
        ma200_state = "Above" if current_price >= ma200 else "Below"
        rsi_state = _rsi_status(rsi_value)
        signal = _overall_signal(current_price, ma50, ma200, rsi_value)

        with columns[idx]:
            with st.container(border=True):
                st.markdown(f"**{ticker}**")
                st.markdown(f"Price: **${current_price:,.2f}**")
                st.markdown(f"MA50 / MA200: **{ma50_state} / {ma200_state}**")
                st.markdown(f"RSI: **{rsi_value:.2f} ({rsi_state})**")
                st.markdown(f"Overall Signal: **{signal}**")


def _build_weight_sliders(
    tickers: list[str], default_weights: dict[str, float], key_prefix: str
) -> dict[str, float]:
    """สร้าง slider ปรับสัดส่วนและ normalize ให้ผลรวมเท่ากับ 1."""
    raw_weights: dict[str, float] = {}
    for ticker in tickers:
        raw_weights[ticker] = st.slider(
            label=f"{ticker}",
            min_value=0.0,
            max_value=1.0,
            value=float(default_weights[ticker]),
            step=0.01,
            key=f"{key_prefix}_{ticker}",
        )

    total_weight = sum(raw_weights.values())
    if total_weight <= 0:
        raise ValueError("น้ำหนักพอร์ตต้องมากกว่า 0")

    return {k: v / total_weight for k, v in raw_weights.items()}


def render_backtest_page(prices: pd.DataFrame, default_weights: dict[str, float], tickers: list[str]) -> None:
    """หน้า Backtest แบบโต้ตอบ."""
    st.header("Backtest")
    benchmark_ticker = "VOO" if "VOO" in tickers else tickers[0]
    st.caption(f"กำหนดเงินลงทุนเริ่มต้น + สัดส่วน ETF แล้วรันเพื่อดูผลเทียบ {benchmark_ticker}")

    initial_capital = st.number_input(
        "เงินลงทุนเริ่มต้น (USD)",
        min_value=100.0,
        value=10000.0,
        step=100.0,
        format="%.2f",
    )
    st.markdown("**สัดส่วน ETF**")
    normalized_weights = _build_weight_sliders(tickers, default_weights, "backtest_weight")

    if st.button("Run Backtest", type="primary"):
        backtest_df = run_portfolio_backtest(prices, normalized_weights, initial_capital=initial_capital)

        benchmark_prices = prices[benchmark_ticker].ffill().dropna()
        benchmark = (benchmark_prices / benchmark_prices.iloc[0]) * initial_capital
        comparison_df = backtest_df[["Portfolio Value"]].join(
            benchmark.rename(f"Benchmark ({benchmark_ticker})"), how="inner"
        )

        comparison_fig = px.line(
            comparison_df,
            x=comparison_df.index,
            y=["Portfolio Value", f"Benchmark ({benchmark_ticker})"],
            title=f"Portfolio vs Benchmark ({benchmark_ticker})",
        )
        st.plotly_chart(_apply_plotly_dark_theme(comparison_fig), use_container_width=True)

        final_portfolio = float(comparison_df["Portfolio Value"].iloc[-1])
        final_benchmark = float(comparison_df[f"Benchmark ({benchmark_ticker})"].iloc[-1])
        col1, col2 = st.columns(2)
        col1.metric("Final Portfolio Value", f"${final_portfolio:,.2f}")
        col2.metric(f"Final Benchmark ({benchmark_ticker})", f"${final_benchmark:,.2f}")
    else:
        st.info("ปรับค่าแล้วกด Run Backtest เพื่อดูผล")


def render_dca_simulator_page(prices: pd.DataFrame, default_weights: dict[str, float], tickers: list[str]) -> None:
    """หน้า DCA Simulator แบบโต้ตอบ."""
    st.header("DCA Simulator")
    st.caption("จำลองลงทุนแบบ DCA รายเดือนพร้อมสรุปผลพอร์ต")

    monthly_investment = st.number_input(
        "จำนวนเงิน DCA ต่อเดือน (USD)",
        min_value=50.0,
        value=1000.0,
        step=50.0,
        format="%.2f",
    )
    st.markdown("**สัดส่วน ETF**")
    normalized_weights = _build_weight_sliders(tickers, default_weights, "dca_weight")

    dca_df = simulate_monthly_dca(prices, normalized_weights, monthly_investment=monthly_investment)

    dca_fig = px.line(
        dca_df,
        x=dca_df.index,
        y=["Total Invested", "Portfolio Value"],
        title="เงินสะสม vs มูลค่าพอร์ต",
    )
    st.plotly_chart(_apply_plotly_dark_theme(dca_fig), use_container_width=True)

    total_invested = float(dca_df["Total Invested"].iloc[-1])
    current_value = float(dca_df["Portfolio Value"].iloc[-1])
    profit = current_value - total_invested

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Invested", f"${total_invested:,.2f}")
    col2.metric("Current Value", f"${current_value:,.2f}")
    col3.metric("Profit", f"${profit:,.2f}", delta=f"{(profit / total_invested) * 100:.2f}%")


def _extract_allocation_df(advice_text: str | None) -> pd.DataFrame:
    """แปลง ALLOCATIONS_JSON จากข้อความ AI ให้เป็น DataFrame."""
    if not advice_text:
        return pd.DataFrame()

    marker = "ALLOCATIONS_JSON:"
    marker_idx = advice_text.find(marker)
    allocations: list[dict] = []
    if marker_idx >= 0:
        json_tail = advice_text[marker_idx + len(marker):].strip()
        start_idx = json_tail.find("[")
        end_idx = json_tail.rfind("]")
        if start_idx >= 0 and end_idx > start_idx:
            json_text = json_tail[start_idx : end_idx + 1]
            try:
                parsed = json.loads(json_text)
                if isinstance(parsed, list):
                    allocations = parsed
            except (json.JSONDecodeError, TypeError, ValueError):
                allocations = []

    if not allocations:
        pattern = r"(?im)\b([A-Z]{2,10})\b\s+([\d,]+(?:\.\d+)?)\s*บาท\s*\(([\d.]+)\s*%\)"
        regex_rows = re.findall(pattern, advice_text)
        for ticker, amount_text, percent_text in regex_rows:
            try:
                amount_value = float(amount_text.replace(",", ""))
                percent_value = float(percent_text)
            except ValueError:
                continue
            allocations.append(
                {
                    "ticker": ticker.strip().upper(),
                    "percent": percent_value,
                    "amount_thb": amount_value,
                }
            )

    rows: list[dict] = []
    for item in allocations:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker", "")).strip().upper()
        percent = item.get("percent")
        amount = item.get("amount_thb")
        reason = str(item.get("reason", "")).strip()

        try:
            percent_value = float(percent)
            amount_value = float(amount)
        except (TypeError, ValueError):
            continue

        if ticker and percent_value > 0:
            rows.append(
                {
                    "Ticker": ticker,
                    "Percent": percent_value,
                    "Amount (THB)": amount_value,
                    "Reason": reason,
                }
            )

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def render_ai_advisor_page() -> None:
    """หน้า AI Advisor: ขอคำแนะนำ DCA รายเดือนจาก Claude."""
    st.header("AI Advisor")
    st.caption("วิเคราะห์ ETF ปัจจุบันด้วย Claude และแนะนำแผน DCA รายเดือน")
    config = load_config()

    budget_thb = st.number_input(
        "งบ DCA รายเดือน (บาท)",
        min_value=500.0,
        value=float(config["dca"]["monthly_budget_thb"]),
        step=500.0,
        format="%.0f",
    )

    if st.button("วิเคราะห์เดือนนี้", type="primary"):
        with st.spinner("กำลังดึงข้อมูล ETF และวิเคราะห์ด้วย Claude..."):
            result = get_monthly_advice(budget_thb=float(budget_thb))

        st.success("วิเคราะห์เสร็จแล้ว")
        st.markdown("### คำแนะนำจาก AI")
        st.markdown(result["advice_text"])

        discord_result = result.get("discord_result", {})
        if discord_result.get("success"):
            st.info("ส่งผลวิเคราะห์ไป Discord แล้ว")
        elif not discord_result.get("skipped"):
            st.warning(f"ส่ง Discord ไม่สำเร็จ: {discord_result.get('error', 'unknown error')}")

        allocation_df = _extract_allocation_df(result.get("advice_text"))
        if not allocation_df.empty:
            st.markdown("### สัดส่วนที่แนะนำ")
            st.dataframe(
                allocation_df.style.format(
                    {
                        "Percent": "{:.2f}%",
                        "Amount (THB)": "{:,.0f}",
                    }
                )
            )
            pie = px.pie(
                allocation_df,
                names="Ticker",
                values="Percent",
                title="Recommended DCA Allocation",
                hole=0.35,
            )
            st.plotly_chart(_apply_plotly_dark_theme(pie), use_container_width=True)
        else:
            st.warning("ไม่พบ JSON allocations ที่ parse ได้จากคำตอบ AI จึงยังไม่สามารถวาด Pie Chart")
    else:
        st.info("ระบุงบประมาณ แล้วกดปุ่ม 'วิเคราะห์เดือนนี้'")


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_macro_data() -> pd.DataFrame:
    """ดึงข้อมูล Macro indicators สำหรับ 1 ปีย้อนหลัง."""
    macro_tickers = {
        "Fed Rate": "^IRX",
        "CPI Inflation": "CPIAUCSL",
        "10Y Treasury Yield": "^TNX",
        "DXY Dollar Index": "DX-Y.NYB",
        "VIX Fear Index": "^VIX",
    }
    try:
        downloaded = yf.download(
            tickers=list(macro_tickers.values()),
            period="1y",
            interval="1d",
            auto_adjust=False,
            progress=False,
            group_by="ticker",
        )
    except Exception:
        st.warning("ไม่สามารถดึงข้อมูลได้ กรุณารอสักครู่")
        return pd.DataFrame()

    if downloaded.empty:
        return pd.DataFrame()

    close_df = pd.DataFrame(index=downloaded.index)
    for label, ticker in macro_tickers.items():
        series = pd.Series(dtype="float64")
        if isinstance(downloaded.columns, pd.MultiIndex):
            if ticker in downloaded.columns.get_level_values(0) and "Close" in downloaded[ticker]:
                series = downloaded[ticker]["Close"]
        elif "Close" in downloaded.columns:
            # กรณี yfinance ส่งโครงสร้างคอลัมน์แบบเดี่ยว
            series = downloaded["Close"]
        close_df[label] = pd.to_numeric(series, errors="coerce")

    close_df = close_df.sort_index().ffill()

    # ปรับหน่วยผลตอบแทนพันธบัตรให้เป็น % หากค่ามาในรูปแบบ x10
    if "10Y Treasury Yield" in close_df.columns and close_df["10Y Treasury Yield"].dropna().median() > 20:
        close_df["10Y Treasury Yield"] = close_df["10Y Treasury Yield"] / 10

    return close_df


def _vix_regime_text(vix_value: float) -> str:
    if vix_value < 20:
        return "สงบ"
    if vix_value <= 30:
        return "ระวัง"
    return "กลัว"


def render_macro_page() -> None:
    """หน้า Macro: แสดงภาพรวมเศรษฐกิจมหภาคและระดับความเสี่ยงตลาด."""
    st.header("Macro")
    st.caption("ติดตามตัวชี้วัดเศรษฐกิจหลักและดัชนีความกลัวของตลาด")

    with st.spinner("กำลังโหลดข้อมูล..."):
        macro_df = fetch_macro_data()
    if macro_df.empty:
        st.error("ไม่สามารถดึงข้อมูล Macro ได้ในขณะนี้")
        return

    required_cols = ["Fed Rate", "CPI Inflation", "10Y Treasury Yield", "DXY Dollar Index", "VIX Fear Index"]
    available_cols = [col for col in required_cols if col in macro_df.columns]
    if len(available_cols) < len(required_cols):
        st.warning("บางตัวชี้วัดอาจไม่พร้อมใช้งานจากแหล่งข้อมูล ณ ตอนนี้")

    latest_values: dict[str, float] = {}
    previous_values: dict[str, float] = {}
    for col in available_cols:
        valid_series = macro_df[col].dropna()
        if len(valid_series) < 2:
            continue
        latest_values[col] = float(valid_series.iloc[-1])
        previous_values[col] = float(valid_series.iloc[-2])

    card_cols = st.columns(5)
    for idx, col_name in enumerate(required_cols):
        with card_cols[idx]:
            latest = latest_values.get(col_name)
            previous = previous_values.get(col_name)
            if latest is None or previous is None:
                st.metric(col_name, "N/A", "N/A")
                continue

            delta = latest - previous
            delta_fmt = f"{delta:+.2f}"

            if col_name == "VIX Fear Index":
                regime = _vix_regime_text(latest)
                st.metric(col_name, f"{latest:.2f} {regime}", delta_fmt)
            elif col_name in {"Fed Rate", "CPI Inflation", "10Y Treasury Yield"}:
                st.metric(col_name, f"{latest:.2f}%", delta_fmt)
            else:
                st.metric(col_name, f"{latest:.2f}", delta_fmt)

    st.markdown("เกณฑ์ VIX: < 20 (สงบ) | 20-30 (ระวัง) | > 30 (กลัว)")

    vix_series = macro_df["VIX Fear Index"].dropna()
    if vix_series.empty:
        st.warning("ไม่พบข้อมูล VIX สำหรับแสดงกราฟ 1 ปี")
    else:
        st.subheader("VIX ย้อนหลัง 1 ปี")
        vix_fig = px.line(
            x=vix_series.index,
            y=vix_series.values,
            labels={"x": "Date", "y": "VIX"},
            title="VIX Fear Index - 1Y",
        )
        vix_fig.add_hline(y=20, line_dash="dash", line_color=THEME["positive"], annotation_text="Calm")
        vix_fig.add_hline(y=30, line_dash="dash", line_color=THEME["accent"], annotation_text="Caution")
        st.plotly_chart(_apply_plotly_dark_theme(vix_fig), use_container_width=True)

    if all(metric in latest_values for metric in required_cols):
        fed = latest_values["Fed Rate"]
        cpi = latest_values["CPI Inflation"]
        ten_y = latest_values["10Y Treasury Yield"]
        dxy = latest_values["DXY Dollar Index"]
        vix = latest_values["VIX Fear Index"]
        vix_regime = _vix_regime_text(vix)
        policy_gap = cpi - fed

        st.subheader("สรุป Macro Environment")
        st.markdown(
            "\n".join(
                [
                    f"- Fed Rate ล่าสุดอยู่ที่ **{fed:.2f}%** ขณะที่ CPI อยู่ที่ **{cpi:.2f}%** (ช่องว่างเงินเฟ้อ-ดอกเบี้ย **{policy_gap:+.2f}%**).",
                    f"- Bond Yield 10 ปีที่ **{ten_y:.2f}%** สะท้อนต้นทุนเงินระยะยาวของตลาดในปัจจุบัน.",
                    f"- DXY ที่ **{dxy:.2f}** บ่งชี้ทิศทางค่าเงินดอลลาร์ และส่งผลต่อสินทรัพย์เสี่ยงทั่วโลก.",
                    f"- VIX อยู่ที่ **{vix:.2f}** ในโหมด **{vix_regime}** ควรบริหารความเสี่ยงพอร์ตให้เหมาะกับความผันผวน.",
                ]
            )
        )
    else:
        st.subheader("สรุป Macro Environment")
        st.info("ข้อมูลยังไม่ครบทุกตัวชี้วัด จึงยังสรุปภาพรวมเชิงข้อความไม่ได้")


def render_portfolio_page() -> None:
    """หน้า Portfolio: บันทึกธุรกรรมและสรุปพอร์ตปัจจุบัน."""
    st.header("Portfolio")
    st.caption("บันทึกการซื้อ ETF และติดตามผลกำไร/ขาดทุนแบบปัจจุบัน")
    _render_pdf_export_panel(
        section_key="portfolio",
        prepare_label="Export Portfolio Report",
        download_label="ดาวน์โหลด PDF พอร์ตปัจจุบัน",
    )
    st.divider()
    config = load_config()
    primary_currency = str(config["display"]["currency"]).upper()
    default_fx_rate = float(config["display"]["default_fx_rate"])

    st.subheader("เพิ่มรายการซื้อ")
    with st.spinner("กำลังโหลดข้อมูล..."):
        today_fx_rate = get_today_fx_rate_thb()
    if not today_fx_rate or today_fx_rate <= 0:
        today_fx_rate = default_fx_rate
    with st.form("portfolio_buy_form", clear_on_submit=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            buy_date = st.date_input("วันที่")
            ticker = st.text_input("ETF (Ticker)", value="VOO").strip().upper()
        with col2:
            shares = st.number_input("จำนวน Shares", min_value=0.0001, value=1.0, step=0.1, format="%.4f")
            price_usd = st.number_input("ราคา USD", min_value=0.0001, value=100.0, step=0.1, format="%.4f")
        with col3:
            amount_thb = st.number_input("จำนวนเงิน THB", min_value=0.01, value=1000.0, step=10.0, format="%.2f")
            fx_rate_thb = st.number_input(
                "FX Rate (THB/USD)",
                min_value=0.0001,
                value=float(today_fx_rate),
                step=0.01,
                format="%.4f",
            )
            note = st.text_input("หมายเหตุ", value="")

        trade_number, estimated_fee_thb = estimate_dime_fee_thb(
            trade_date=buy_date,
            shares=float(shares),
            price_usd=float(price_usd),
            fx_rate_thb=float(fx_rate_thb),
        )
        st.caption(f"เทรดที่ {trade_number} ของเดือนนี้")
        st.caption(f"ค่าธรรมเนียมโดยประมาณ: {estimated_fee_thb:,.2f} บาท")

        submitted = st.form_submit_button("บันทึกการซื้อ", type="primary")
        if submitted:
            try:
                add_transaction(
                    date=buy_date.strftime("%Y-%m-%d"),
                    ticker=ticker,
                    shares=float(shares),
                    price_usd=float(price_usd),
                    fx_rate_thb=float(fx_rate_thb),
                    amount_thb=float(amount_thb),
                    note=note,
                )
                st.success("บันทึกรายการซื้อเรียบร้อยแล้ว")
                st.rerun()
            except Exception as exc:
                st.error(f"บันทึกไม่สำเร็จ: {exc}")

    st.divider()
    st.subheader("สรุปพอร์ตปัจจุบัน")
    with st.spinner("กำลังโหลดข้อมูล..."):
        holdings_df = get_portfolio_summary()
        total_summary = get_total_summary()

    m1, m2, m3, m4, m5 = st.columns(5)
    if primary_currency == "USD":
        invested = total_summary["total_invested_thb"] / today_fx_rate
        current = total_summary["current_value_thb"] / today_fx_rate
        pnl_value = total_summary["total_pnl_thb"] / today_fx_rate
        m1.metric("เงินลงทุนทั้งหมด (USD)", f"{invested:,.2f}")
        m2.metric("มูลค่าปัจจุบัน (USD)", f"{current:,.2f}")
        m3.metric(
            "กำไร/ขาดทุน (USD)",
            f"{pnl_value:,.2f}",
            delta=f"{total_summary['total_return_pct']:.2f}%",
        )
    else:
        m1.metric("เงินลงทุนทั้งหมด (THB)", f"{total_summary['total_invested_thb']:,.2f}")
        m2.metric("มูลค่าปัจจุบัน (THB)", f"{total_summary['current_value_thb']:,.2f}")
        m3.metric(
            "กำไร/ขาดทุน (THB)",
            f"{total_summary['total_pnl_thb']:,.2f}",
            delta=f"{total_summary['total_return_pct']:.2f}%",
        )
    m4.metric("FX Rate วันนี้", f"{today_fx_rate:.2f} THB/USD")
    m5.metric("ค่าธรรมเนียมรวมทั้งหมด (THB)", f"{total_summary['total_fee_thb']:,.2f}")

    if holdings_df.empty:
        st.info("ยังไม่มีรายการซื้อในพอร์ต")
    else:
        display_holdings = holdings_df[
            [
                "Ticker",
                "Shares",
                "FX Rate (Buy)",
                "Avg Cost (USD)",
                "Current Price (USD)",
                "P&L (USD)",
                "P&L (THB)",
                "Return (%)",
                "Fee (THB)",
            ]
        ].copy()
        st.dataframe(
            display_holdings.style.format(
                {
                    "Shares": "{:,.4f}",
                    "FX Rate (Buy)": "{:,.4f}",
                    "Avg Cost (USD)": "${:,.2f}",
                    "Current Price (USD)": "${:,.2f}",
                    "P&L (USD)": "${:,.2f}",
                    "P&L (THB)": "{:,.2f}",
                    "Return (%)": "{:,.2f}%",
                    "Fee (THB)": "{:,.2f}",
                }
            ),
            use_container_width=True,
        )

        pie_fig = px.pie(
            holdings_df,
            names="Ticker",
            values="Current Value (THB)",
            title="สัดส่วนพอร์ตปัจจุบัน (ตามมูลค่า THB)",
            hole=0.35,
        )
        st.plotly_chart(_apply_plotly_dark_theme(pie_fig), use_container_width=True)

    st.divider()
    st.subheader("ประวัติการซื้อขาย")
    with st.spinner("กำลังโหลดข้อมูล..."):
        all_transactions = get_transactions()
    if all_transactions.empty:
        st.info("ยังไม่มีประวัติการซื้อขาย")
        return

    ticker_options = ["ทั้งหมด"] + sorted(all_transactions["ticker"].dropna().astype(str).str.upper().unique().tolist())
    selected_ticker = st.selectbox("กรองตาม ETF", ticker_options, index=0)
    filtered_transactions = all_transactions.copy()
    if selected_ticker != "ทั้งหมด":
        filtered_transactions = get_transactions(selected_ticker)

    filtered_transactions = filtered_transactions.rename(
        columns={
            "date": "Date",
            "ticker": "Ticker",
            "shares": "Shares",
            "price_usd": "Price (USD)",
            "fx_rate_thb": "FX Rate (THB/USD)",
            "amount_thb": "Amount (THB)",
            "fee_thb": "ค่าธรรมเนียม (THB)",
            "note": "Note",
        }
    )
    st.dataframe(
        filtered_transactions.style.format(
            {
                "Shares": "{:,.4f}",
                "Price (USD)": "${:,.4f}",
                "FX Rate (THB/USD)": "{:,.4f}",
                "Amount (THB)": "{:,.2f}",
                "ค่าธรรมเนียม (THB)": "{:,.2f}",
            }
        ),
        use_container_width=True,
    )


def render_dashboard() -> None:
    """แสดงผล dashboard หลักของ Vaultis."""
    try:
        st.set_page_config(page_title="Vaultis ETF Analyzer", layout="wide")
        _inject_premium_theme()
        st.title("Vaultis Premium Financial Dashboard")
        tickers = get_tickers()
        st.caption(f"Dark & Luxury Finance view | ETF Universe: {', '.join(tickers)}")

        if st.button("Refresh ข้อมูล"):
            st.cache_data.clear()
            st.success("ล้างแคชเรียบร้อย กำลังโหลดข้อมูลใหม่...")
            st.rerun()

        with st.spinner("กำลังโหลดข้อมูล..."):
            prices = fetch_adjusted_close_data(tickers, years=10)
        if prices.empty:
            st.error("ไม่พบข้อมูล ETF")
            return

        base_weights = {"VOO": 0.35, "SCHD": 0.20, "QQQM": 0.20, "XLV": 0.15, "GLDM": 0.10}
        default_weights = {ticker: base_weights.get(ticker, 1.0) for ticker in tickers}
        total = sum(default_weights.values())
        default_weights = {ticker: value / total for ticker, value in default_weights.items()}
        config = load_config()

        default_page = str(config["display"]["default_page"])
        _render_custom_sidebar(default_page)
        page = st.session_state.get("page", "Overview")

        if page == "Overview":
            pass
        elif page == "Portfolio":
            render_portfolio_page()
            return
        elif page == "Backtest":
            render_backtest_page(prices, default_weights, tickers)
            return
        elif page == "DCA Simulator":
            render_dca_simulator_page(prices, default_weights, tickers)
            return
        elif page == "Technical Signals":
            render_technical_signals_page(prices)
            return
        elif page == "Correlation":
            pass
        elif page == "AI Advisor":
            render_ai_advisor_page()
            return
        elif page == "Macro":
            render_macro_page()
            return
        elif page == "Price Alerts":
            render_price_alerts_page()
            return
        elif page == "Settings":
            render_settings_page()
            return

        _render_pdf_export_panel(
            section_key="overview",
            prepare_label="Export รายงานเดือนนี้",
            download_label="ดาวน์โหลด PDF รายงานเดือนนี้",
        )
        st.divider()
        _render_market_ticker_bar(tickers, prices)
        _render_overview_metrics(prices, tickers)
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        st.subheader("Price Trend (Normalized = 100)")
        normalized_prices = prices.ffill().apply(
            lambda series: (series / series.dropna().iloc[0]) * 100 if not series.dropna().empty else series
        )
        price_trend_fig = px.line(normalized_prices, x=normalized_prices.index, y=normalized_prices.columns)
        st.plotly_chart(_apply_plotly_dark_theme(price_trend_fig), use_container_width=True)

        col1, col2 = st.columns(2)

        with col1:
            st.subheader("Return Analysis")
            with st.spinner("กำลังโหลดข้อมูล..."):
                returns_df = calculate_period_returns(prices)
            st.dataframe(returns_df.style.format("{:.2f}%", na_rep="N/A"))
            st.caption("*QQQM เริ่ม Trading ปี 2020")

        with col2:
            st.subheader("Risk Metrics")
            with st.spinner("กำลังโหลดข้อมูล..."):
                risk_df = calculate_risk_metrics(prices)
            st.dataframe(risk_df.style.format("{:.4f}"))

        st.subheader("Correlation Heatmap")
        with st.spinner("กำลังโหลดข้อมูล..."):
            corr_df = calculate_correlation_matrix(prices)
        if corr_df.empty:
            st.warning("ไม่สามารถดึงข้อมูลได้ กรุณารอสักครู่")
            return
        available_tickers = [ticker for ticker in tickers if ticker in corr_df.index and ticker in corr_df.columns]
        if len(available_tickers) < 2:
            st.warning("ข้อมูล correlation ยังไม่พอสำหรับ ETF ที่เลือก")
            return
        corr_for_display = corr_df.loc[available_tickers, available_tickers]
        heatmap = px.imshow(
            corr_for_display,
            color_continuous_scale=[
                [0.0, THEME["negative"]],
                [0.5, THEME["text_primary"]],
                [1.0, THEME["positive"]],
            ],
            zmin=-1,
            zmax=1,
            origin="lower",
            text_auto=".2f",
        )
        heatmap.update_layout(coloraxis_colorbar_title="Correlation")
        st.plotly_chart(_apply_plotly_dark_theme(heatmap), use_container_width=True)

        corr_pairs = corr_for_display.where(
            pd.DataFrame(
                [[col_idx < row_idx for col_idx in range(len(corr_for_display.columns))]
                 for row_idx in range(len(corr_for_display.index))],
                index=corr_for_display.index,
                columns=corr_for_display.columns,
>>>>>>> 2e136b0841b9b6f56b13f65995d33f9eea5fd827
            )

    delete_id = st.number_input("Delete transaction id", min_value=1, value=1, step=1)
    if st.button("DELETE /api/portfolio/{id}"):
        st.session_state["portfolio_data"] = _api_delete(f"/api/portfolio/{int(delete_id)}")

    if st.session_state.get("portfolio_data"):
        st.json(st.session_state["portfolio_data"])


def render_analysis_page():
    st.header("Analysis")
    if st.button("GET /api/macro"):
        st.session_state["analysis_data"] = _api_get("/api/macro")

    with st.expander("POST /api/backtest"):
        initial_capital = st.number_input("initial_capital", min_value=100.0, value=10000.0)
        if st.button("Run backtest"):
            st.session_state["analysis_data"] = _api_post(
                "/api/backtest",
                {
                    "initial_capital": initial_capital,
                    "weights": {"VOO": 0.35, "SCHD": 0.20, "QQQM": 0.20, "XLV": 0.15, "GLDM": 0.10},
                },
            )

    with st.expander("POST /api/dca/simulate"):
        monthly = st.number_input("monthly_investment", min_value=50.0, value=1000.0)
        if st.button("Run DCA simulation"):
            st.session_state["analysis_data"] = _api_post(
                "/api/dca/simulate",
                {
                    "monthly_investment": monthly,
                    "weights": {"VOO": 0.35, "SCHD": 0.20, "QQQM": 0.20, "XLV": 0.15, "GLDM": 0.10},
                },
            )

    if st.session_state.get("analysis_data"):
        st.json(st.session_state["analysis_data"])


def render_alerts_page():
    st.header("Alerts")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("GET /api/alerts"):
            st.session_state["alerts_data"] = _api_get("/api/alerts")
    with c2:
        if st.button("POST /api/alerts/check"):
            st.session_state["alerts_data"] = _api_post("/api/alerts/check", {})

    with st.expander("Create alert"):
        ticker = st.text_input("alert ticker", value="VOO")
        alert_type = st.selectbox("alert_type", ["above", "below"])
        target_price = st.number_input("target_price", min_value=0.01, value=500.0)
        if st.button("POST /api/alerts"):
            st.session_state["alerts_data"] = _api_post(
                "/api/alerts",
                {"ticker": ticker, "alert_type": alert_type, "target_price": target_price},
            )

    alert_id = st.number_input("Delete alert id", min_value=1, value=1, step=1)
    if st.button("DELETE /api/alerts/{id}"):
        st.session_state["alerts_data"] = _api_delete(f"/api/alerts/{int(alert_id)}")

    if st.session_state.get("alerts_data"):
        st.json(st.session_state["alerts_data"])


def render_ai_page():
    st.header("AI Advisor")
    if st.button("GET /api/ai/history"):
        st.session_state["ai_data"] = _api_get("/api/ai/history")

    budget_thb = st.number_input("budget_thb", min_value=500.0, value=5000.0)
    if st.button("POST /api/ai/advice"):
        st.session_state["ai_data"] = _api_post("/api/ai/advice", {"budget_thb": budget_thb})

    if st.session_state.get("ai_data"):
        st.json(st.session_state["ai_data"])


def main():
    st.set_page_config(page_title="Vaultis Dashboard", layout="wide")
    st.title("Vaultis Dashboard (FastAPI Client)")

    try:
        health = _api_get("/health")
        st.success(f"Backend status: {health.get('status', 'unknown')}")
    except Exception as exc:
        st.error(f"Backend connection failed: {exc}")
        st.stop()

    page = st.sidebar.radio("Page", ["ETF", "Portfolio", "Analysis", "Alerts", "AI"])
    if page == "ETF":
        render_etf_page()
    elif page == "Portfolio":
        render_portfolio_page()
    elif page == "Analysis":
        render_analysis_page()
    elif page == "Alerts":
        render_alerts_page()
    else:
        render_ai_page()


if __name__ == "__main__":
    main()
