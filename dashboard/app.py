# -*- coding: utf-8 -*-
"""Streamlit dashboard   ETF  ."""

from __future__ import annotations

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
import streamlit.components.v1 as components
import yfinance as yf
from dotenv import load_dotenv
from plotly.subplots import make_subplots

#   path   root   import   Streamlit
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from analysis.correlation import calculate_correlation_matrix
from analysis.ai_advisor import ai_suggest_alerts, get_monthly_advice
from analysis.financial_model import run_full_analysis
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

load_dotenv()
BACKEND_URL = os.getenv(
    "BACKEND_URL",
    "https://vaultis-backend.onrender.com",
)

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
    ("Analysis", ["Backtest", "DCA Simulator", "Technical Signals", "Correlation", "DCF Analysis"]),
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
        /*   gap   radio items */
        [data-testid="stSidebar"] [role="radiogroup"] {{
            gap: 0px !important;
        }}
        /*   radio item */
        [data-testid="stSidebar"] label {{
            padding: 6px 12px !important;
            margin: 0px !important;
            border-radius: 6px !important;
            font-size: 14px !important;
            cursor: pointer !important;
        }}
        /*   radio circle */
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {{
            margin: 0 !important;
            padding: 0 !important;
        }}
        /*   default radio button dot */
        [data-testid="stSidebar"] input[type="radio"] {{
            display: none !important;
        }}
        /*   padding   sidebar */
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
        if st.button("DCF Analysis", key="nav_dcf_analysis", use_container_width=True):
            st.session_state["page"] = "DCF Analysis"

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
        arrow = " " if pct >= 0 else " "
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


def _ws_prices_url() -> str:
    """WebSocket URL for live prices (override with VAULTIS_WS_URL)."""
    default_ws_base = BACKEND_URL.replace("https://", "wss://").replace("http://", "ws://").rstrip("/")
    default_ws_url = f"{default_ws_base}/ws/prices"
    raw = os.getenv("VAULTIS_WS_URL", default_ws_url).strip()
    return raw or default_ws_url


def _render_realtime_price_ticker_bar() -> None:
    """Live ticker via backend WebSocket (iframe ? Streamlit strips <script> in st.markdown)."""
    ws_url = json.dumps(_ws_prices_url(), ensure_ascii=False)
    html = f"""
<div id="ticker-bar" style="
    background: #161B22;
    border: 1px solid #30363D;
    padding: 8px 16px;
    border-radius: 8px;
    display: flex;
    gap: 24px;
    font-size: 13px;
    margin-bottom: 16px;
    color: #E6EDF3;
    font-family: Inter, sans-serif;
">
    <span id="price-VOO">VOO ?</span>
    <span id="price-SCHD">SCHD ?</span>
    <span id="price-QQQM">QQQM ?</span>
    <span id="price-XLV">XLV ?</span>
    <span id="price-GLDM">GLDM ?</span>
</div>
<script>
(function () {{
    const wsUrl = {ws_url};
    const ws = new WebSocket(wsUrl);
    ws.onmessage = function (event) {{
        const raw = typeof event.data === "string"
            ? event.data
            : new TextDecoder("utf-8").decode(event.data);
        const data = JSON.parse(raw);
        console.log(data);
        if (data.type === "price_update") {{
            Object.entries(data.data).forEach(([ticker, info]) => {{
                const el = document.getElementById("price-" + ticker);
                if (el) {{
                    const color = info.change_pct >= 0 ? "#3FB950" : "#F85149";
                    const sign = info.change_pct >= 0 ? "+" : "";
                    el.innerHTML = ticker + " $" + info.price +
                        ' <span style="color:' + color + '">' +
                        sign + info.change_pct + "%</span>";
                }}
            }});
        }}
    }};
    ws.onerror = function () {{
        ["VOO","SCHD","QQQM","XLV","GLDM"].forEach(function (t) {{
            const el = document.getElementById("price-" + t);
            if (el) el.textContent = t + " (WS error)";
        }});
    }};
}})();
</script>
"""
    components.html(html, height=70)


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


def _render_pdf_export_panel(section_key: str, prepare_label: str, download_label: str) -> None:
    """Render monthly PDF export controls with Streamlit download button."""
    config = load_config()
    month_text = datetime.today().strftime("%B %Y")
    default_budget = float(config["dca"]["monthly_budget_thb"])
    budget_thb = st.number_input(
        "DCA Budget (THB)",
        min_value=500.0,
        value=default_budget,
        step=500.0,
        format="%.0f",
        key=f"{section_key}_pdf_budget",
    )

    cache_key = f"{section_key}_pdf_bytes"
    file_key = f"{section_key}_pdf_filename"
    if st.button(prepare_label, key=f"{section_key}_prepare_pdf"):
        with st.spinner("  PDF..."):
            st.session_state[cache_key] = generate_monthly_report(month=month_text, budget_thb=float(budget_thb))
            st.session_state[file_key] = f"vaultis_monthly_report_{datetime.today():%Y_%m}.pdf"
        st.success("PDF prepared successfully.")

    if cache_key in st.session_state:
        st.download_button(
            label=download_label,
            data=st.session_state[cache_key],
            file_name=st.session_state.get(file_key, f"vaultis_monthly_report_{datetime.today():%Y_%m}.pdf"),
            mime="application/pdf",
            key=f"{section_key}_download_pdf",
        )


def _is_valid_etf_ticker(ticker: str) -> bool:
    """Validate ticker by fetching 1-day data from yfinance."""
    cleaned_ticker = str(ticker).strip().upper()
    if not cleaned_ticker:
        return False
    try:
        test_df = yf.download(
            cleaned_ticker,
            period="1d",
            interval="1d",
            auto_adjust=False,
            progress=False,
        )
        if test_df.empty:
            return False
        close_series = pd.to_numeric(test_df.get("Close"), errors="coerce").dropna()
        return not close_series.empty
    except Exception:
        return False


def render_settings_page() -> None:
    """  config.json."""
    st.header("Settings")
    st.caption(" , ETF,    ")

    config = load_config()
    current_tickers = get_tickers()
    page_options = [
        "Overview",
        "Portfolio",
        "Backtest",
        "DCA Simulator",
        "Technical Signals",
        "DCF Analysis",
        "AI Advisor",
        "Macro",
        "Price Alerts",
        "Settings",
    ]

    st.subheader("1) DCA Settings")
    dca_budget = st.number_input(
        "  DCA   (THB)",
        min_value=100.0,
        value=float(config["dca"]["monthly_budget_thb"]),
        step=100.0,
        format="%.0f",
    )
    dca_day = st.number_input(
        "  DCA  ",
        min_value=1,
        max_value=31,
        value=int(config["dca"]["day_of_month"]),
        step=1,
    )

    st.divider()
    st.subheader("2) ETF Management")
    st.caption("ETF  ")
    for ticker in current_tickers:
        col_ticker, col_remove = st.columns([4, 1])
        with col_ticker:
            st.text(ticker)
        with col_remove:
            if st.button("Remove", key=f"remove_{ticker}"):
                try:
                    remove_ticker(ticker)
                    st.success(f"  ETF {ticker}  ")
                    st.rerun()
                except Exception as exc:
                    st.error(f"  ETF  : {exc}")

    new_ticker = st.text_input("  ETF  ", value="", placeholder="  VTI")
    if st.button("  ETF", type="secondary"):
        candidate = new_ticker.strip().upper()
        if not candidate:
            st.warning("  Ticker  ")
        elif candidate in current_tickers:
            st.info(f"{candidate}  ")
        elif not _is_valid_etf_ticker(candidate):
            st.error("  ETF     Ticker")
        else:
            try:
                add_ticker(candidate)
                st.success(f"  ETF {candidate}  ")
                st.rerun()
            except Exception as exc:
                st.error(f"  ETF  : {exc}")

    st.divider()
    st.subheader("3) Notification Settings")
    try:
        webhook_url = st.secrets["DISCORD_WEBHOOK_URL"]
    except Exception:
        webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "")

    if webhook_url.strip():
        st.success("Discord Webhook:  ")
    else:
        st.error("Discord Webhook:   DISCORD_WEBHOOK_URL   .env")

    weekly_summary_enabled = st.checkbox(
        "Weekly Summary  ",
        value=bool(config["notifications"]["weekly_summary"]),
    )
    dca_reminder_enabled = st.checkbox(
        "DCA Reminder   1  ",
        value=bool(config["notifications"]["dca_reminder"]),
    )
    rsi_alert_enabled = st.checkbox(
        "RSI Alert   Oversold/Overbought",
        value=bool(config["notifications"]["rsi_alert"]),
    )
    if st.button("  Discord"):
        if not webhook_url.strip():
            st.error("  DISCORD_WEBHOOK_URL   Discord  ")
        else:
            test_result = test_alert(webhook_url=webhook_url)
            if test_result.get("success"):
                st.success("  Discord  ")
            else:
                st.error(f" : {test_result.get('error', 'unknown error')}")

    st.divider()
    st.subheader("4) Display Settings")
    current_default_page = str(config["display"]["default_page"])
    default_page = st.selectbox(
        "Default Page  ",
        page_options,
        index=page_options.index(current_default_page) if current_default_page in page_options else 0,
    )
    currency = st.radio(
        " ",
        options=["THB", "USD"],
        index=0 if str(config["display"]["currency"]).upper() == "THB" else 1,
        horizontal=True,
    )
    default_fx_rate = st.number_input(
        "  Default ( )",
        min_value=1.0,
        value=float(config["display"]["default_fx_rate"]),
        step=0.1,
        format="%.4f",
    )

    if st.button("  Settings", type="primary"):
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
            st.success("  Settings   config.json  ")
            st.info("  scheduler     restart  ")
        except Exception as exc:
            st.error(f" : {exc}")


def _style_alert_rows(row: pd.Series) -> list[str]:
    state = str(row.get("Status", ""))
    distance = pd.to_numeric(pd.Series([row.get("Distance %", None)]), errors="coerce").iloc[0]
    if state == "Triggered":
        return ["background-color: rgba(220, 53, 69, 0.18)"] * len(row)
    if pd.notna(distance) and abs(float(distance)) <= 2.0:
        return ["background-color: rgba(46, 204, 113, 0.15)"] * len(row)
    return [""] * len(row)


def render_price_alerts_page() -> None:
    """  Price Alerts: AI  ,  ,   active alerts."""
    st.header("Price Alerts")
    tickers = get_tickers()
    if not tickers:
        st.warning("  ETF     Settings")
        return

    all_alerts = list_alerts(include_triggered=True)
    history_alerts = [item for item in all_alerts if bool(item.get("triggered"))]
    active_alerts = get_active_alerts_with_distance(near_threshold_pct=2.0)
    latest_prices = get_current_prices(tickers)

    st.subheader("1) AI Suggest Alerts")
    if "ai_alert_suggestions" not in st.session_state:
        st.session_state["ai_alert_suggestions"] = []

    if st.button("    AI   Price Alerts", type="primary", key="ai_suggest_alerts_btn"):
        with st.spinner("  ETF   AI..."):
            try:
                ai_result = ai_suggest_alerts()
                st.session_state["ai_alert_suggestions"] = ai_result.get("alerts", [])
                st.success("AI   Price Alerts  ")
            except Exception as exc:
                st.error(f"AI  : {exc}")

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
                    st.markdown(f" : **${float(current_price):,.2f}**")
                else:
                    st.markdown(" : **N/A**")
                st.markdown(f"  Buy Alert: **${buy_alert:,.2f}**   {buy_reason}")
                st.markdown(f"  Warning Alert: **${warning_alert:,.2f}**   {warning_reason}")

                c1, c2 = st.columns(2)
                with c1:
                    if st.button("  Alert   (Buy)", key=f"set_ai_buy_{ticker}"):
                        try:
                            add_or_update_alert(
                                ticker=ticker,
                                alert_type="below",
                                price=buy_alert,
                                note=f"AI Buy: {buy_reason}",
                            )
                            st.success(f"  Buy Alert   {ticker}  ")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"  Buy Alert  : {exc}")
                with c2:
                    if st.button("  Alert   (Warning)", key=f"set_ai_warn_{ticker}"):
                        try:
                            add_or_update_alert(
                                ticker=ticker,
                                alert_type="above",
                                price=warning_alert,
                                note=f"AI Warning: {warning_reason}",
                            )
                            st.success(f"  Warning Alert   {ticker}  ")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"  Warning Alert  : {exc}")
    else:
        st.info("  AI   Buy/Warning alerts   ETF  ")

    st.divider()
    st.subheader("2) Manual Alert")
    col_ticker, col_type, col_price = st.columns([2, 2, 2])
    with col_ticker:
        selected_ticker = st.selectbox("  ETF", tickers, key="price_alert_ticker")
    with col_type:
        selected_type = st.selectbox(
            " ",
            options=["below", "above"],
            format_func=lambda x: "Below ( )" if x == "below" else "Above ( )",
            key="price_alert_type",
        )
    with col_price:
        target_price = st.number_input("  (USD)", min_value=0.01, value=100.0, step=0.5, format="%.2f")
    note = st.text_input("Note", value="", placeholder="e.g. DCA plan")

    current_price = latest_prices.get(selected_ticker)
    if current_price is not None:
        st.caption(f"  {selected_ticker}: ${current_price:,.2f}")
    else:
        st.caption(f"  {selected_ticker}  ")

    if st.button("  Alert", type="primary"):
        try:
            created = add_alert(
                ticker=selected_ticker,
                alert_type=selected_type,
                price=float(target_price),
                note=note,
            )
            st.success(
                f"  Alert  : {created['ticker']} {created['alert_type']} ${float(created['price']):,.2f}"
            )
            st.rerun()
        except Exception as exc:
            st.error(f"  Alert  : {exc}")

    if st.button("  Alert  "):
        result = check_alerts()
        triggered_count = len(result.get("triggered", []))
        if triggered_count > 0:
            st.success(f"  Alert trigger   {triggered_count}   (  Discord  )")
        else:
            st.info("  Alert  ")
        st.rerun()

    st.divider()
    st.subheader("3) Active Alerts")
    if not active_alerts:
        st.info("  Active Alerts")
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
                    " ": " " if alert_type == "below" else " ",
                    "  (USD)": target,
                    "  (USD)": now_price,
                    "Distance %": distance,
                    "Status": "  Near Trigger" if bool(item.get("is_near_trigger")) else "Pending",
                    " ": str(item.get("note", "")).strip() or "-",
                    " ": str(item.get("created_at", "")),
                }
            )

        pending_df = pd.DataFrame(active_rows)
        show_cols = [
            "ETF",
            " ",
            "  (USD)",
            "  (USD)",
            "Distance %",
            "Status",
            " ",
            " ",
        ]
        st.dataframe(
            pending_df[show_cols].style.format(
                {
                    "  (USD)": "${:,.2f}",
                    "  (USD)": "${:,.2f}",
                    "Distance %": "{:+.2f}%",
                },
                na_rep="N/A",
            ).apply(_style_alert_rows, axis=1),
            use_container_width=True,
        )

        delete_options = {f"{row['ETF']} | {row[' ']} | ${row['  (USD)']:,.2f}": row["ID"] for _, row in pending_df.iterrows()}
        selected_delete_key = st.selectbox("  Alert  ", options=list(delete_options.keys()), key="delete_price_alert")
        if st.button("  Alert"):
            selected_alert_id = delete_options.get(selected_delete_key)
            if selected_alert_id and delete_alert(str(selected_alert_id)):
                st.success("  Alert  ")
                st.rerun()
            else:
                st.warning("  Alert  ")

    st.divider()
    st.subheader("4) Alert History")
    if not history_alerts:
        st.info("  Alert   trigger")
    else:
        history_rows: list[dict[str, object]] = []
        for item in history_alerts:
            alert_type = str(item.get("alert_type", "")).lower()
            history_rows.append(
                {
                    "ETF": str(item.get("ticker", "")).strip().upper(),
                    " ": " " if alert_type == "below" else " ",
                    "  (USD)": float(item.get("price", 0.0)),
                    "  Trigger (USD)": item.get("triggered_price"),
                    " ": str(item.get("note", "")).strip() or "-",
                    "Triggered At": str(item.get("triggered_at", "")),
                }
            )
        history_df = pd.DataFrame(history_rows).sort_values("Triggered At", ascending=False)
        st.dataframe(
            history_df.style.format(
                {
                    "  (USD)": "${:,.2f}",
                    "  Trigger (USD)": "${:,.2f}",
                },
                na_rep="N/A",
            ),
            use_container_width=True,
        )


def calculate_technical_signals(price_series: pd.Series) -> pd.DataFrame:
    """  MA50, MA200   RSI  ."""
    try:
        signals = pd.DataFrame(index=price_series.index)
        signals["Price"] = price_series
        signals["MA50"] = ta.sma(price_series, length=50)
        signals["MA200"] = ta.sma(price_series, length=200)
        signals["RSI14"] = ta.rsi(price_series, length=14)
        return signals
    except Exception as exc:
        raise RuntimeError(f"  Technical Signals: {exc}") from exc


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_ohlc_data(tickers: list[str], years: int = 10) -> dict[str, pd.DataFrame]:
    """  OHLC   ETF   Candlestick."""
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
        st.warning("Unable to fetch market data. Please try again.")
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
    """  Technical Signals   Candlestick + RSI + Signal Cards."""
    st.header("Technical Signals")
    technical_tickers = get_tickers()
    if not technical_tickers:
        st.warning("  ETF     Settings")
        return

    selected_ticker = st.selectbox("  ETF", technical_tickers, index=0)

    with st.spinner(" ..."):
        ohlc_map = fetch_ohlc_data(technical_tickers, years=10)
    selected_ohlc = ohlc_map.get(selected_ticker)
    if selected_ohlc is None or selected_ohlc.empty:
        st.warning(f"  OHLC   {selected_ticker}")
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
                st.warning(f"{ticker}:  ")
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
    """  slider   normalize   1."""
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
        raise ValueError("  0")

    return {k: v / total_weight for k, v in raw_weights.items()}


def render_backtest_page(prices: pd.DataFrame, default_weights: dict[str, float], tickers: list[str]) -> None:
    """  Backtest  ."""
    st.header("Backtest")
    benchmark_ticker = "VOO" if "VOO" in tickers else tickers[0]
    st.caption(f"  +   ETF   {benchmark_ticker}")

    initial_capital = st.number_input(
        "  (USD)",
        min_value=100.0,
        value=10000.0,
        step=100.0,
        format="%.2f",
    )
    st.markdown("**  ETF**")
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
        st.info("  Run Backtest  ")


def render_dca_simulator_page(prices: pd.DataFrame, default_weights: dict[str, float], tickers: list[str]) -> None:
    """  DCA Simulator  ."""
    st.header("DCA Simulator")
    st.caption("  DCA  ")

    monthly_investment = st.number_input(
        "  DCA   (USD)",
        min_value=50.0,
        value=1000.0,
        step=50.0,
        format="%.2f",
    )
    st.markdown("**  ETF**")
    normalized_weights = _build_weight_sliders(tickers, default_weights, "dca_weight")

    dca_df = simulate_monthly_dca(prices, normalized_weights, monthly_investment=monthly_investment)

    dca_fig = px.line(
        dca_df,
        x=dca_df.index,
        y=["Total Invested", "Portfolio Value"],
        title="  vs  ",
    )
    st.plotly_chart(_apply_plotly_dark_theme(dca_fig), use_container_width=True)

    total_invested = float(dca_df["Total Invested"].iloc[-1])
    current_value = float(dca_df["Portfolio Value"].iloc[-1])
    profit = current_value - total_invested

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Invested", f"${total_invested:,.2f}")
    col2.metric("Current Value", f"${current_value:,.2f}")
    col3.metric("Profit", f"${profit:,.2f}", delta=f"{(profit / total_invested) * 100:.2f}%")


def _full_analysis_score_dcf_df(full_analysis: dict | None) -> pd.DataFrame:
    """Flatten financial_model.run_full_analysis() for tables and charts."""
    if not full_analysis or not isinstance(full_analysis.get("analysis"), dict):
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for ticker, payload in full_analysis["analysis"].items():
        if not isinstance(payload, dict):
            continue
        dcf = payload.get("dcf") if isinstance(payload.get("dcf"), dict) else {}
        rows.append(
            {
                "Ticker": str(ticker).upper(),
                "Total": int(payload.get("total_score", 0) or 0),
                "Technical": int(payload.get("technical_score", 0) or 0),
                "MA": int(payload.get("ma_score", 0) or 0),
                "DCF pts": int(payload.get("dcf_score", 0) or 0),
                "Momentum": int(payload.get("momentum_score", 0) or 0),
                "RSI": float(payload.get("rsi", 0) or 0),
                "Signal": str(payload.get("signal", "")),
                "Current (USD)": float(dcf.get("current_price", 0) or 0),
                "DCF intrinsic (USD)": float(dcf.get("intrinsic_value", 0) or 0),
                "Margin of Safety %": float(dcf.get("margin_of_safety", 0) or 0),
                "DCF signal": str(dcf.get("signal", "")),
            }
        )
    order = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]
    rows.sort(key=lambda r: order.index(str(r["Ticker"])) if str(r["Ticker"]) in order else 99)
    return pd.DataFrame(rows)


def _allocation_from_full_analysis(full_analysis: dict | None) -> pd.DataFrame:
    if not full_analysis or not isinstance(full_analysis.get("allocation"), dict):
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for ticker, payload in full_analysis["allocation"].items():
        if not isinstance(payload, dict):
            continue
        rows.append(
            {
                "Ticker": str(ticker).upper(),
                "Percent": float(payload.get("percent", 0) or 0),
                "Amount (THB)": float(payload.get("amount_thb", 0) or 0),
                "Score": int(payload.get("score", 0) or 0),
            }
        )
    order = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]
    rows.sort(key=lambda r: order.index(str(r["Ticker"])) if str(r["Ticker"]) in order else 99)
    return pd.DataFrame(rows)


def _extract_allocation_df(advice_text: str | None) -> pd.DataFrame:
    """  ALLOCATIONS_JSON   AI   DataFrame."""
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
        pattern = r"(?im)\b([A-Z]{2,10})\b\s+([\d,]+(?:\.\d+)?)\s* \s*\(([\d.]+)\s*%\)"
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


def render_dcf_analysis_page() -> None:
    """DCF analysis page with ETF drill-down and full heatmap."""
    st.header("DCF Analysis")
    st.caption("Model-driven DCF details, score breakdown, and full ETF heatmap")

    config = load_config()
    budget_thb = st.number_input(
        "Monthly Budget (THB)",
        min_value=500.0,
        value=float(config["dca"]["monthly_budget_thb"]),
        step=500.0,
        format="%.0f",
        key="dcf_analysis_budget",
    )

    if "dcf_full_analysis" not in st.session_state:
        with st.spinner("Running full analysis..."):
            st.session_state["dcf_full_analysis"] = run_full_analysis(budget_thb=float(budget_thb))

    if st.button("Run Full Analysis", type="primary", key="dcf_run_full"):
        with st.spinner("Running all ETF analysis..."):
            st.session_state["dcf_full_analysis"] = run_full_analysis(budget_thb=float(budget_thb))
        st.success("Full analysis completed.")

    full_analysis = st.session_state.get("dcf_full_analysis")
    score_df = _full_analysis_score_dcf_df(full_analysis if isinstance(full_analysis, dict) else None)
    if score_df.empty:
        st.warning("No DCF analysis data available.")
        return

    selected_ticker = st.selectbox("Select ETF", options=score_df["Ticker"].tolist(), key="dcf_selected_ticker")
    selected_row = score_df.loc[score_df["Ticker"] == selected_ticker].iloc[0]
    selected_raw = full_analysis["analysis"].get(selected_ticker, {})
    selected_dcf = selected_raw.get("dcf", {}) if isinstance(selected_raw, dict) else {}

    cards = st.columns(4)
    cards[0].metric("Current Price", f"${float(selected_row['Current (USD)']):,.2f}")
    cards[1].metric("DCF Intrinsic Value", f"${float(selected_row['DCF intrinsic (USD)']):,.2f}")
    cards[2].metric("Margin of Safety %", f"{float(selected_row['Margin of Safety %']):.2f}%")
    cards[3].metric("Signal", str(selected_row["Signal"]))

    st.subheader("Score Breakdown")
    breakdown_map = {
        "Technical Score": float(selected_row["Technical"]),
        "MA Score": float(selected_row["MA"]),
        "DCF Score": float(selected_row["DCF pts"]),
        "Momentum Score": float(selected_row["Momentum"]),
        "Dividend Score": float(selected_raw.get("dividend_score", 0) if isinstance(selected_raw, dict) else 0),
    }
    breakdown_df = pd.DataFrame(
        {"Metric": list(breakdown_map.keys()), "Score": list(breakdown_map.values())}
    ).sort_values("Score", ascending=True)
    bar_fig = px.bar(
        breakdown_df,
        x="Score",
        y="Metric",
        orientation="h",
        color="Score",
        color_continuous_scale=[THEME["negative"], THEME["accent"], THEME["positive"]],
        title=f"{selected_ticker} Score Breakdown",
    )
    bar_fig.update_layout(coloraxis_showscale=False)
    st.plotly_chart(_apply_plotly_dark_theme(bar_fig), use_container_width=True)

    st.subheader("DCF Cash Flow Table (10 Years)")
    cash_flows = selected_dcf.get("cash_flows", []) if isinstance(selected_dcf, dict) else []
    if cash_flows:
        cash_flow_df = pd.DataFrame(cash_flows).rename(
            columns={"year": "Year", "cash_flow": "Cash Flow", "present_value": "Present Value"}
        )
        st.dataframe(
            cash_flow_df[["Year", "Cash Flow", "Present Value"]].style.format(
                {"Cash Flow": "${:,.2f}", "Present Value": "${:,.2f}"}
            ),
            use_container_width=True,
        )
    else:
        st.info("No cash flow data available for this ETF.")

    st.subheader("DCF Assumptions")
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("WACC %", f"{float(selected_dcf.get('wacc', 0)):.2f}%")
    a2.metric("Growth Rate %", f"{float(selected_dcf.get('growth_rate', 0)):.2f}%")
    a3.metric("Terminal Growth %", f"{float(selected_dcf.get('terminal_growth', 0)):.2f}%")
    a4.metric("Beta", f"{float(selected_dcf.get('beta', 0)):.2f}")

    st.subheader("Heatmap Score (All ETFs)")
    heatmap_df = score_df.set_index("Ticker")[["Total", "Technical", "MA", "DCF pts", "Momentum"]]
    heatmap_fig = px.imshow(
        heatmap_df,
        color_continuous_scale=[
            [0.0, THEME["negative"]],
            [0.5, THEME["text_primary"]],
            [1.0, THEME["positive"]],
        ],
        text_auto=".0f",
        aspect="auto",
        zmin=0,
        zmax=max(100.0, float(heatmap_df.to_numpy().max())),
    )
    heatmap_fig.update_layout(coloraxis_colorbar_title="Score")
    st.plotly_chart(_apply_plotly_dark_theme(heatmap_fig), use_container_width=True)


def render_ai_advisor_page() -> None:
    """  AI Advisor:   DCA   Claude."""
    st.header("AI Advisor")
    st.caption("คะแนนและ DCF คำนวณในระบบ — Groq ใช้เพื่ออธิบายเหตุผลเท่านั้น")
    config = load_config()

    budget_thb = st.number_input(
        "  DCA   ( )",
        min_value=500.0,
        value=float(config["dca"]["monthly_budget_thb"]),
        step=500.0,
        format="%.0f",
    )

    if st.button("Analyze This Month", type="primary"):
        with st.spinner("กำลังรัน Financial Model + Groq (อาจใช้เวลาสักครู่)..."):
            result = get_monthly_advice(budget_thb=float(budget_thb))

        st.success("Analysis completed.")
        full = result.get("full_analysis")
        if not isinstance(full, dict):
            full = {
                "analysis": result.get("analysis", {}),
                "allocation": result.get("allocation", {}),
            }
        score_dcf_df = _full_analysis_score_dcf_df(full if isinstance(full, dict) else None)
        if not score_dcf_df.empty:
            st.subheader("Score breakdown (0–100)")
            st.dataframe(
                score_dcf_df[
                    [
                        "Ticker",
                        "Total",
                        "Technical",
                        "MA",
                        "DCF pts",
                        "Momentum",
                        "RSI",
                        "Signal",
                    ]
                ].style.format({"RSI": "{:.2f}"}),
                use_container_width=True,
            )
            st.subheader("DCF: intrinsic value vs current price (USD)")
            iv_fig = go.Figure(
                data=[
                    go.Bar(
                        name="Current price",
                        x=score_dcf_df["Ticker"],
                        y=score_dcf_df["Current (USD)"],
                        marker_color=THEME["text_secondary"],
                    ),
                    go.Bar(
                        name="DCF intrinsic",
                        x=score_dcf_df["Ticker"],
                        y=score_dcf_df["DCF intrinsic (USD)"],
                        marker_color=THEME["accent"],
                    ),
                ]
            )
            iv_fig.update_layout(barmode="group", legend_title_text="")
            iv_fig.update_yaxes(title_text="USD")
            st.plotly_chart(_apply_plotly_dark_theme(iv_fig), use_container_width=True)

            st.subheader("Margin of safety (%)")
            mos_fig = go.Figure(
                data=[
                    go.Bar(
                        x=score_dcf_df["Ticker"],
                        y=score_dcf_df["Margin of Safety %"],
                        marker_color=THEME["positive"],
                    )
                ]
            )
            mos_fig.update_yaxes(title_text="%")
            st.plotly_chart(_apply_plotly_dark_theme(mos_fig), use_container_width=True)

        st.markdown("### คำอธิบายจาก AI")
        advice_text = str(result.get("advice_text") or result.get("advice") or "")
        st.markdown(advice_text)

        discord_result = result.get("discord_result", {})
        if discord_result.get("success"):
            st.info("  Discord  ")
        elif not discord_result.get("skipped"):
            st.warning(f"  Discord  : {discord_result.get('error', 'unknown error')}")

        allocation_df = _allocation_from_full_analysis(full if isinstance(full, dict) else None)
        if allocation_df.empty:
            allocation_df = _extract_allocation_df(advice_text)
        if not allocation_df.empty:
            st.markdown("### การจัดสรร DCA (จากโมเดล)")
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
                values="Amount (THB)",
                title="DCA allocation (THB)",
                hole=0.35,
            )
            st.plotly_chart(_apply_plotly_dark_theme(pie), use_container_width=True)
        else:
            st.warning("ไม่พบข้อมูล allocation — ลองรันใหม่หรือตรวจสอบการเชื่อมต่อ")
    else:
        st.info("    ' '")


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_macro_data() -> pd.DataFrame:
    """  Macro indicators   1  ."""
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
        st.warning("Some required macro metrics are missing.")
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
            #   yfinance  
            series = downloaded["Close"]
        close_df[label] = pd.to_numeric(series, errors="coerce")

    close_df = close_df.sort_index().ffill()

    #   %   x10
    if "10Y Treasury Yield" in close_df.columns and close_df["10Y Treasury Yield"].dropna().median() > 20:
        close_df["10Y Treasury Yield"] = close_df["10Y Treasury Yield"] / 10

    return close_df


def _vix_regime_text(vix_value: float) -> str:
    if vix_value < 20:
        return " "
    if vix_value <= 30:
        return " "
    return " "


def render_macro_page() -> None:
    """  Macro:  ."""
    st.header("Macro")
    st.caption("VIX regime guide")

    with st.spinner(" ..."):
        macro_df = fetch_macro_data()
    if macro_df.empty:
        st.error("  Macro  ")
        return

    required_cols = ["Fed Rate", "CPI Inflation", "10Y Treasury Yield", "DXY Dollar Index", "VIX Fear Index"]
    available_cols = [col for col in required_cols if col in macro_df.columns]
    if len(available_cols) < len(required_cols):
        st.warning("VIX data is unavailable for the selected period.")

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

    st.markdown("  VIX: < 20 ( ) | 20-30 ( ) | > 30 ( )")

    vix_series = macro_df["VIX Fear Index"].dropna()
    if vix_series.empty:
        st.warning("  VIX   1  ")
    else:
        st.subheader("VIX   1  ")
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

        st.subheader("  Macro Environment")
        st.markdown(
            "\n".join(
                [
                    f"- Fed Rate   **{fed:.2f}%**   CPI   **{cpi:.2f}%** ( -  **{policy_gap:+.2f}%**).",
                    f"- Bond Yield 10   **{ten_y:.2f}%**  .",
                    f"- DXY   **{dxy:.2f}**    .",
                    f"- VIX   **{vix:.2f}**   **{vix_regime}**  .",
                ]
            )
        )
    else:
        st.subheader("  Macro Environment")
        st.info("Not enough macro data for a full summary.")


def render_portfolio_page() -> None:
    """  Portfolio:  ."""
    st.header("Portfolio")
    st.caption("  ETF  / ")
    _render_pdf_export_panel(
        section_key="portfolio",
        prepare_label="Export Portfolio Report",
        download_label="  PDF  ",
    )
    st.divider()
    config = load_config()
    primary_currency = str(config["display"]["currency"]).upper()
    default_fx_rate = float(config["display"]["default_fx_rate"])

    st.subheader("Add Transaction")
    with st.spinner(" ..."):
        today_fx_rate = get_today_fx_rate_thb()
    if not today_fx_rate or today_fx_rate <= 0:
        today_fx_rate = default_fx_rate
    with st.form("portfolio_buy_form", clear_on_submit=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            buy_date = st.date_input("Date")
            ticker = st.text_input("ETF (Ticker)", value="VOO").strip().upper()
        with col2:
            shares = st.number_input("  Shares", min_value=0.0001, value=1.0, step=0.1, format="%.4f")
            price_usd = st.number_input("  USD", min_value=0.0001, value=100.0, step=0.1, format="%.4f")
        with col3:
            amount_thb = st.number_input("  THB", min_value=0.01, value=1000.0, step=10.0, format="%.2f")
            fx_rate_thb = st.number_input(
                "FX Rate (THB/USD)",
                min_value=0.0001,
                value=float(today_fx_rate),
                step=0.01,
                format="%.4f",
            )
            note = st.text_input("Note", value="")

        trade_number, estimated_fee_thb = estimate_dime_fee_thb(
            trade_date=buy_date,
            shares=float(shares),
            price_usd=float(price_usd),
            fx_rate_thb=float(fx_rate_thb),
        )
        st.caption(f"  {trade_number}  ")
        st.caption(f" : {estimated_fee_thb:,.2f}  ")

        submitted = st.form_submit_button("Save Transaction", type="primary")
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
                st.success("Transaction saved.")
                st.rerun()
            except Exception as exc:
                st.error(f" : {exc}")

    st.divider()
    st.subheader("Portfolio Summary")
    with st.spinner(" ..."):
        holdings_df = get_portfolio_summary()
        total_summary = get_total_summary()

    m1, m2, m3, m4, m5 = st.columns(5)
    if primary_currency == "USD":
        invested = total_summary["total_invested_thb"] / today_fx_rate
        current = total_summary["current_value_thb"] / today_fx_rate
        pnl_value = total_summary["total_pnl_thb"] / today_fx_rate
        m1.metric("  (USD)", f"{invested:,.2f}")
        m2.metric("  (USD)", f"{current:,.2f}")
        m3.metric(
            " /  (USD)",
            f"{pnl_value:,.2f}",
            delta=f"{total_summary['total_return_pct']:.2f}%",
        )
    else:
        m1.metric("  (THB)", f"{total_summary['total_invested_thb']:,.2f}")
        m2.metric("  (THB)", f"{total_summary['current_value_thb']:,.2f}")
        m3.metric(
            " /  (THB)",
            f"{total_summary['total_pnl_thb']:,.2f}",
            delta=f"{total_summary['total_return_pct']:.2f}%",
        )
    m4.metric("FX Rate  ", f"{today_fx_rate:.2f} THB/USD")
    m5.metric("  (THB)", f"{total_summary['total_fee_thb']:,.2f}")

    if holdings_df.empty:
        st.info("No portfolio data found.")
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
            title="  (  THB)",
            hole=0.35,
        )
        st.plotly_chart(_apply_plotly_dark_theme(pie_fig), use_container_width=True)

    st.divider()
    st.subheader("Transaction History")
    with st.spinner(" ..."):
        all_transactions = get_transactions()
    if all_transactions.empty:
        st.info("No transactions found.")
        return

    ticker_options = [" "] + sorted(all_transactions["ticker"].dropna().astype(str).str.upper().unique().tolist())
    selected_ticker = st.selectbox("  ETF", ticker_options, index=0)
    filtered_transactions = all_transactions.copy()
    if selected_ticker != " ":
        filtered_transactions = get_transactions(selected_ticker)

    filtered_transactions = filtered_transactions.rename(
        columns={
            "date": "Date",
            "ticker": "Ticker",
            "shares": "Shares",
            "price_usd": "Price (USD)",
            "fx_rate_thb": "FX Rate (THB/USD)",
            "amount_thb": "Amount (THB)",
            "fee_thb": "  (THB)",
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
                "  (THB)": "{:,.2f}",
            }
        ),
        use_container_width=True,
    )


def render_dashboard() -> None:
    """  dashboard   Vaultis."""
    try:
        st.set_page_config(page_title="Vaultis ETF Analyzer", layout="wide")
        _inject_premium_theme()
        st.title("Vaultis Premium Financial Dashboard")
        tickers = get_tickers()
        st.caption(f"Dark & Luxury Finance view | ETF Universe: {', '.join(tickers)}")

        if st.button("Refresh Data"):
            st.cache_data.clear()
            st.success("   ...")
            st.rerun()

        with st.spinner(" ..."):
            prices = fetch_adjusted_close_data(tickers, years=10)
        if prices.empty:
            st.error("  ETF")
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
        elif page == "DCF Analysis":
            render_dcf_analysis_page()
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
            prepare_label="Export Monthly Report",
            download_label="  PDF  ",
        )
        st.divider()
        _render_realtime_price_ticker_bar()
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
            with st.spinner(" ..."):
                returns_df = calculate_period_returns(prices)
            st.dataframe(returns_df.style.format("{:.2f}%", na_rep="N/A"))
            st.caption("*QQQM   Trading   2020")

        with col2:
            st.subheader("Risk Metrics")
            with st.spinner(" ..."):
                risk_df = calculate_risk_metrics(prices)
            st.dataframe(risk_df.style.format("{:.4f}"))

        st.subheader("Correlation Heatmap")
        with st.spinner(" ..."):
            corr_df = calculate_correlation_matrix(prices)
        if corr_df.empty:
            st.warning("Correlation data is unavailable.")
            return
        available_tickers = [ticker for ticker in tickers if ticker in corr_df.index and ticker in corr_df.columns]
        if len(available_tickers) < 2:
            st.warning("  correlation   ETF  ")
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
            )
        ).stack()
        max_pair = corr_pairs.idxmax()
        min_pair = corr_pairs.idxmin()
        max_value = float(corr_pairs.loc[max_pair])
        min_value = float(corr_pairs.loc[min_pair])

        st.markdown("**Insight   Correlation Heatmap**")
        st.markdown(
            f"-   correlation  : **{max_pair[0]} - {max_pair[1]} ({max_value:.2f})**    "
        )
        st.markdown(
            f"-   correlation  : **{min_pair[0]} - {min_pair[1]} ({min_value:.2f})**    "
        )
        st.markdown("-   correlation  ")

        st.info("  Backtest   DCA Simulator   Sidebar  ")
    except Exception as exc:
        st.error(f"  dashboard: {exc}")


if __name__ == "__main__":
    render_dashboard()
