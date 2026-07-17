# -*- coding: utf-8 -*-
"""Streamlit dashboard สำหรับวิเคราะห์และติดตามพอร์ต ETF ระยะยาว."""

from __future__ import annotations

from datetime import datetime
import json
import math
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
from analysis.financial_model import (
    DIVIDEND_MAX,
    MOMENTUM_MAX,
    TILT_MAX,
    TILT_MIN,
    TIMING_MAX,
    TREND_MAX,
    _dividend_yield,
    build_etf_scores,
    calculate_allocation,
    run_full_analysis,
)
from analysis.ta_compat import ta
from analysis.returns import calculate_period_returns, monthly_seasonality
from analysis.macro import get_thai_inflation
from analysis.risk import calculate_risk_metrics, drawdown_episodes, underwater_series
from analysis.trend_channel import fit_trend_channel
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
from data.fetcher import PriceDataUnavailableError, fetch_adjusted_close_data
from db.sentiment_models import get_latest_sentiment_summaries
from portfolio.backtest import run_portfolio_backtest
from portfolio.dca import simulate_monthly_dca
from portfolio.targets import RISK_PROFILES, get_risk_profile, get_target_weights
from portfolio.costs import (
    US_DIVIDEND_WITHHOLDING,
    estimate_annual_dividend_tax_thb,
    estimate_monthly_costs_thb,
    fx_spread_pct,
    gross_up_net_dividend,
    net_dividend_yield,
)
from portfolio.drip import simulate_drip
from portfolio.tracker import (
    TX_DIVIDEND,
    add_dividend,
    add_transaction,
    estimate_dime_fee_thb,
    get_dividend_summary,
    get_dividends,
    get_portfolio_summary,
    get_today_fx_rate_thb,
    get_total_summary,
    get_transactions,
)
from technical import signal_rules
from technical.indicators import ma_cross_dates, weekly_dca_signal
from utils.config import add_ticker, get_tickers, load_config, remove_ticker, save_config
from utils.pdf_export import generate_monthly_report


@st.cache_data(ttl=3600, show_spinner=False)
def cached_prices(tickers: list[str], years: int = 10) -> pd.DataFrame:
    """ดึงราคา (cache 1 ชม.) — เดิม Streamlit rerun ทุกครั้งที่กดปุ่มแล้วยิง yfinance ใหม่
    ทำให้โดน rate limit บ่อยจนกลายเป็นสัญญาณปลอม (AUDIT.md H3/C1)
    """
    return fetch_adjusted_close_data(list(tickers), years=years)

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
    ("Main", ["Overview", "Scorecard", "Portfolio"]),
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
        if st.button("Scorecard", key="nav_scorecard", use_container_width=True):
            st.session_state["page"] = "Scorecard"
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
    # return_df: แถว = ช่วงเวลา (1M/3M/.../10Y), คอลัมน์ = ticker
    # บั๊กเดิม (AUDIT.md H4): หาคอลัมน์ "1Y (%)" ที่ไม่มีจริง → ไปหยิบคอลัมน์ ticker ตัวสุดท้าย
    # ทำให้การ์ด Best/Worst ETF โชว์ชื่อช่วงเวลาแทนชื่อ ETF
    if "1Y" in return_df.index:
        sortable = return_df.loc["1Y"].dropna()
    else:
        sortable = pd.Series(dtype=float)

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
    """ปุ่มสร้าง/ดาวน์โหลดรายงานรายเดือนเป็น PDF."""
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

    include_ai = st.checkbox(
        "ใส่บทวิเคราะห์ AI ในรายงาน (มีค่าใช้จ่าย ~0.10–0.30 บาท)",
        value=False,
        key=f"{section_key}_pdf_ai",
    )

    cache_key = f"{section_key}_pdf_bytes"
    file_key = f"{section_key}_pdf_filename"
    if st.button(prepare_label, key=f"{section_key}_prepare_pdf"):
        with st.spinner("กำลังสร้าง PDF..."):
            st.session_state[cache_key] = generate_monthly_report(
                month=month_text, budget_thb=float(budget_thb), include_ai=include_ai
            )
            st.session_state[file_key] = f"vaultis_monthly_report_{datetime.today():%Y_%m}.pdf"
        st.success("สร้าง PDF เรียบร้อย")

    if cache_key in st.session_state:
        st.download_button(
            label=download_label,
            data=st.session_state[cache_key],
            file_name=st.session_state.get(file_key, f"vaultis_monthly_report_{datetime.today():%Y_%m}.pdf"),
            mime="application/pdf",
            key=f"{section_key}_download_pdf",
        )


def _is_valid_etf_ticker(ticker: str) -> bool:
    """ตรวจว่า ticker มีอยู่จริงโดยลองดึงข้อมูล 1 วันจาก yfinance."""
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
    """หน้าตั้งค่า — บันทึกลง config.json."""
    st.header("Settings")
    st.caption("ตั้งค่า DCA, ETF ที่ติดตาม, การแจ้งเตือน และการแสดงผล")

    config = load_config()
    current_tickers = get_tickers()
    page_options = [
        "Overview",
        "Scorecard",
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
        "งบ DCA ต่อเดือน (บาท)",
        min_value=100.0,
        value=float(config["dca"]["monthly_budget_thb"]),
        step=100.0,
        format="%.0f",
    )
    dca_day = st.number_input(
        "วันที่ DCA ของเดือน",
        min_value=1,
        max_value=31,
        value=int(config["dca"]["day_of_month"]),
        step=1,
    )

    st.divider()
    st.subheader("2) ETF Management")
    st.caption("ETF ที่ระบบติดตามและนำมาคิดคะแนน")
    for ticker in current_tickers:
        col_ticker, col_remove = st.columns([4, 1])
        with col_ticker:
            st.text(ticker)
        with col_remove:
            if st.button("Remove", key=f"remove_{ticker}"):
                try:
                    remove_ticker(ticker)
                    st.success(f"ลบ ETF {ticker} แล้ว")
                    st.rerun()
                except Exception as exc:
                    st.error(f"ลบ ETF ไม่สำเร็จ: {exc}")

    new_ticker = st.text_input("เพิ่ม ETF ใหม่", value="", placeholder="เช่น VTI")
    if st.button("เพิ่ม ETF", type="secondary"):
        candidate = new_ticker.strip().upper()
        if not candidate:
            st.warning("กรุณากรอก Ticker ก่อน")
        elif candidate in current_tickers:
            st.info(f"{candidate} มีอยู่ในรายการแล้ว")
        elif not _is_valid_etf_ticker(candidate):
            st.error("ไม่พบ ETF นี้ใน yfinance — ตรวจสอบ Ticker อีกครั้ง")
        else:
            try:
                add_ticker(candidate)
                st.success(f"เพิ่ม ETF {candidate} แล้ว")
                st.rerun()
            except Exception as exc:
                st.error(f"เพิ่ม ETF ไม่สำเร็จ: {exc}")

    st.divider()
    st.subheader("3) สัดส่วนพอร์ตเป้าหมาย")
    st.caption(
        "ฐานของทั้งแผน DCA และการ rebalance — คะแนนรายเดือนจะปรับน้ำหนักรอบเป้าหมายนี้ "
        f"({TILT_MIN:.1f}–{TILT_MAX:.1f} เท่า) ไม่ตัดสินทรัพย์ใดออกจากพอร์ต"
    )
    profile_options = list(RISK_PROFILES.keys())
    profile_labels = {"conservative": "อนุรักษ์นิยม", "moderate": "สมดุล", "aggressive": "เชิงรุก"}
    current_profile = get_risk_profile()
    selected_profile = st.selectbox(
        "โปรไฟล์ความเสี่ยง",
        profile_options,
        index=profile_options.index(current_profile),
        format_func=lambda p: f"{profile_labels.get(p, p)} ({p})",
    )
    preset = RISK_PROFILES[selected_profile]
    effective_targets = get_target_weights(current_tickers)
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "ETF": t,
                    "เป้าหมายตาม preset": f"{preset.get(t, 0) * 100:.0f}%",
                    "เป้าหมายที่ใช้จริง": f"{effective_targets.get(t, 0) * 100:.1f}%",
                }
                for t in current_tickers
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )
    st.caption(
        'ถ้าต้องการกำหนดเอง ให้แก้ `portfolio.target_weights` ใน config.json '
        '(เช่น {"VOO": 0.4, "GLDM": 0.05}) — เว้นว่างไว้จะใช้ preset ด้านบน'
    )

    st.divider()
    st.subheader("4) Notification Settings")
    try:
        webhook_url = st.secrets["DISCORD_WEBHOOK_URL"]
    except Exception:
        webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "")

    if webhook_url.strip():
        st.success("Discord Webhook: ตั้งค่าแล้ว")
    else:
        st.error("Discord Webhook: ยังไม่ได้ตั้ง — ใส่ DISCORD_WEBHOOK_URL ในไฟล์ .env")

    weekly_summary_enabled = st.checkbox(
        "ส่ง Weekly Summary ทุกวันจันทร์",
        value=bool(config["notifications"]["weekly_summary"]),
    )
    dca_reminder_enabled = st.checkbox(
        "เตือน DCA ล่วงหน้า 1 วัน",
        value=bool(config["notifications"]["dca_reminder"]),
    )
    rsi_alert_enabled = st.checkbox(
        "เตือนเมื่อ RSI เข้าเขต Oversold/Overbought",
        value=bool(config["notifications"]["rsi_alert"]),
    )
    if st.button("ทดสอบส่ง Discord"):
        if not webhook_url.strip():
            st.error("ยังไม่ได้ตั้ง DISCORD_WEBHOOK_URL จึงส่งไม่ได้")
        else:
            test_result = test_alert(webhook_url=webhook_url)
            if test_result.get("success"):
                st.success("ส่งข้อความทดสอบไป Discord สำเร็จ")
            else:
                st.error(f"ส่งไม่สำเร็จ: {test_result.get('error', 'unknown error')}")

    st.divider()
    st.subheader("5) Display Settings")
    current_default_page = str(config["display"]["default_page"])
    default_page = st.selectbox(
        "หน้าเริ่มต้นเมื่อเปิดแอป",
        page_options,
        index=page_options.index(current_default_page) if current_default_page in page_options else 0,
    )
    currency = st.radio(
        "สกุลเงินหลักที่แสดงผล",
        options=["THB", "USD"],
        index=0 if str(config["display"]["currency"]).upper() == "THB" else 1,
        horizontal=True,
    )
    default_fx_rate = st.number_input(
        "อัตราแลกเปลี่ยนสำรอง (ใช้เมื่อดึงค่าสดไม่ได้)",
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
            "portfolio": {
                "risk_profile": selected_profile,
                # เก็บค่าที่ผู้ใช้ตั้งเองไว้ (ถ้ามี) — ไม่เขียนทับด้วย preset
                "target_weights": dict(config["portfolio"].get("target_weights") or {}),
            },
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
            st.success("บันทึก Settings ลง config.json แล้ว")
            st.info("ถ้ามี scheduler รันอยู่ ให้ restart เพื่อโหลดค่าใหม่")
        except Exception as exc:
            st.error(f"บันทึกไม่สำเร็จ: {exc}")


def _style_alert_rows(row: pd.Series) -> list[str]:
    state = str(row.get("Status", ""))
    distance = pd.to_numeric(pd.Series([row.get("Distance %", None)]), errors="coerce").iloc[0]
    if state == "Triggered":
        return ["background-color: rgba(220, 53, 69, 0.18)"] * len(row)
    if pd.notna(distance) and abs(float(distance)) <= 2.0:
        return ["background-color: rgba(46, 204, 113, 0.15)"] * len(row)
    return [""] * len(row)


def render_price_alerts_page() -> None:
    """หน้า Price Alerts: ระดับที่ระบบแนะนำ, ตั้งเอง, และรายการที่รออยู่."""
    st.header("Price Alerts")
    tickers = get_tickers()
    if not tickers:
        st.warning("ยังไม่มี ETF ในระบบ — เพิ่มได้ที่หน้า Settings")
        return

    all_alerts = list_alerts(include_triggered=True)
    history_alerts = [item for item in all_alerts if bool(item.get("triggered"))]
    active_alerts = get_active_alerts_with_distance(near_threshold_pct=2.0)
    latest_prices = get_current_prices(tickers)

    st.subheader("1) AI Suggest Alerts")
    if "ai_alert_suggestions" not in st.session_state:
        st.session_state["ai_alert_suggestions"] = []

    if st.button("คำนวณระดับ Price Alert ที่แนะนำ", type="primary", key="ai_suggest_alerts_btn"):
        with st.spinner("กำลังคำนวณจากแนวรับ/แนวต้าน และ MA..."):
            try:
                ai_result = ai_suggest_alerts()
                st.session_state["ai_alert_suggestions"] = ai_result.get("alerts", [])
                st.success("คำนวณระดับ Price Alert เรียบร้อย")
            except Exception as exc:
                st.error(f"คำนวณไม่สำเร็จ: {exc}")

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
                st.markdown(f"🟢 ระดับน่าสะสม: **${buy_alert:,.2f}** — {buy_reason}")
                st.markdown(f"🔴 ระดับควรระวัง: **${warning_alert:,.2f}** — {warning_reason}")

                c1, c2 = st.columns(2)
                with c1:
                    if st.button("ตั้ง Buy Alert", key=f"set_ai_buy_{ticker}"):
                        try:
                            add_or_update_alert(
                                ticker=ticker,
                                alert_type="below",
                                price=buy_alert,
                                note=f"AI Buy: {buy_reason}",
                            )
                            st.success(f"ตั้ง Buy Alert ให้ {ticker} แล้ว")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"ตั้ง Buy Alert ไม่สำเร็จ: {exc}")
                with c2:
                    if st.button("ตั้ง Warning Alert", key=f"set_ai_warn_{ticker}"):
                        try:
                            add_or_update_alert(
                                ticker=ticker,
                                alert_type="above",
                                price=warning_alert,
                                note=f"AI Warning: {warning_reason}",
                            )
                            st.success(f"ตั้ง Warning Alert ให้ {ticker} แล้ว")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"ตั้ง Warning Alert ไม่สำเร็จ: {exc}")
    else:
        st.info("กดปุ่มด้านบนเพื่อให้ระบบคำนวณระดับ Buy/Warning ของ ETF ทุกตัว")

    st.divider()
    st.subheader("2) Manual Alert")
    col_ticker, col_type, col_price = st.columns([2, 2, 2])
    with col_ticker:
        selected_ticker = st.selectbox("เลือก ETF", tickers, key="price_alert_ticker")
    with col_type:
        selected_type = st.selectbox(
            "เงื่อนไข",
            options=["below", "above"],
            format_func=lambda x: "Below (ราคาต่ำกว่า)" if x == "below" else "Above (ราคาสูงกว่า)",
            key="price_alert_type",
        )
    with col_price:
        target_price = st.number_input("ราคาเป้าหมาย (USD)", min_value=0.01, value=100.0, step=0.5, format="%.2f")
    note = st.text_input("หมายเหตุ", value="", placeholder="เช่น จังหวะ DCA")

    current_price = latest_prices.get(selected_ticker)
    if current_price is not None:
        st.caption(f"ราคาปัจจุบันของ {selected_ticker}: ${current_price:,.2f}")
    else:
        st.caption(f"ดึงราคาปัจจุบันของ {selected_ticker} ไม่ได้")

    if st.button("เพิ่ม Alert", type="primary"):
        try:
            created = add_alert(
                ticker=selected_ticker,
                alert_type=selected_type,
                price=float(target_price),
                note=note,
            )
            st.success(
                f"เพิ่ม Alert แล้ว: {created['ticker']} {created['alert_type']} ${float(created['price']):,.2f}"
            )
            st.rerun()
        except Exception as exc:
            st.error(f"เพิ่ม Alert ไม่สำเร็จ: {exc}")

    if st.button("ตรวจ Alert ตอนนี้"):
        result = check_alerts()
        triggered_count = len(result.get("triggered", []))
        if triggered_count > 0:
            st.success(f"มี Alert ถึงเงื่อนไข {triggered_count} รายการ (ส่ง Discord แล้ว)")
        else:
            st.info("ยังไม่มี Alert ที่ถึงเงื่อนไข")
        st.rerun()

    st.divider()
    st.subheader("3) Active Alerts")
    if not active_alerts:
        st.info("ยังไม่มี Alert ที่รออยู่")
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
                    "ราคาเป้าหมาย (USD)": target,
                    "ราคาปัจจุบัน (USD)": now_price,
                    "Distance %": distance,
                    "Status": "ใกล้ trigger" if bool(item.get("is_near_trigger")) else "รออยู่",
                    "หมายเหตุ": str(item.get("note", "")).strip() or "-",
                    "สร้างเมื่อ": str(item.get("created_at", "")),
                }
            )

        pending_df = pd.DataFrame(active_rows)
        show_cols = [
            "ETF",
            "เงื่อนไข",
            "ราคาเป้าหมาย (USD)",
            "ราคาปัจจุบัน (USD)",
            "Distance %",
            "Status",
            "หมายเหตุ",
            "สร้างเมื่อ",
        ]
        st.dataframe(
            pending_df[show_cols].style.format(
                {
                    "ราคาเป้าหมาย (USD)": "${:,.2f}",
                    "ราคาปัจจุบัน (USD)": "${:,.2f}",
                    "Distance %": "{:+.2f}%",
                },
                na_rep="N/A",
            ).apply(_style_alert_rows, axis=1),
            use_container_width=True,
        )

        delete_options = {f"{row['ETF']} | {row['เงื่อนไข']} | ${row['ราคาเป้าหมาย (USD)']:,.2f}": row["ID"] for _, row in pending_df.iterrows()}
        selected_delete_key = st.selectbox("เลือก Alert ที่จะลบ", options=list(delete_options.keys()), key="delete_price_alert")
        if st.button("ลบ Alert"):
            selected_alert_id = delete_options.get(selected_delete_key)
            if selected_alert_id and delete_alert(str(selected_alert_id)):
                st.success("ลบ Alert แล้ว")
                st.rerun()
            else:
                st.warning("ลบ Alert ไม่สำเร็จ")

    st.divider()
    st.subheader("4) Alert History")
    if not history_alerts:
        st.info("ยังไม่มี Alert ที่เคย trigger")
    else:
        history_rows: list[dict[str, object]] = []
        for item in history_alerts:
            alert_type = str(item.get("alert_type", "")).lower()
            history_rows.append(
                {
                    "ETF": str(item.get("ticker", "")).strip().upper(),
                    "เงื่อนไข": "ต่ำกว่า" if alert_type == "below" else "สูงกว่า",
                    "ราคาเป้าหมาย (USD)": float(item.get("price", 0.0)),
                    "ราคาตอน Trigger (USD)": item.get("triggered_price"),
                    "หมายเหตุ": str(item.get("note", "")).strip() or "-",
                    "Triggered At": str(item.get("triggered_at", "")),
                }
            )
        history_df = pd.DataFrame(history_rows).sort_values("Triggered At", ascending=False)
        st.dataframe(
            history_df.style.format(
                {
                    "ราคาเป้าหมาย (USD)": "${:,.2f}",
                    "ราคาตอน Trigger (USD)": "${:,.2f}",
                },
                na_rep="N/A",
            ),
            use_container_width=True,
        )


def calculate_technical_signals(price_series: pd.Series) -> pd.DataFrame:
    """คำนวณ MA50, MA200 และ RSI จากราคาปิดแบบปรับแล้ว."""
    try:
        signals = pd.DataFrame(index=price_series.index)
        signals["Price"] = price_series
        signals["MA50"] = ta.sma(price_series, length=50)
        signals["MA200"] = ta.sma(price_series, length=200)
        signals["RSI14"] = ta.rsi(price_series, length=14)
        return signals
    except Exception as exc:
        raise RuntimeError(f"คำนวณ Technical Signals ไม่สำเร็จ: {exc}") from exc


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_ohlc_data(tickers: list[str], years: int = 10) -> dict[str, pd.DataFrame]:
    """ดึงข้อมูล OHLC ของ ETF สำหรับกราฟ Candlestick."""
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
    return {
        "oversold": "Oversold",
        "overbought": "Overbought",
        "neutral": "Neutral",
    }.get(signal_rules.rsi_zone(rsi_value), "N/A")


def _overall_signal(price: float, ma50: float, ma200: float, rsi_value: float) -> str:
    """ใช้นิยามสัญญาณกลางเดียวกับ backend/screener (AUDIT.md C2)."""
    central = signal_rules.dca_signal(price, ma50, ma200, rsi_value)
    return signal_rules.thai_description(central)


def _signal_bar_positions(display_ohlc: pd.DataFrame, dates: list) -> pd.DataFrame:
    """map วันสัญญาณ (นิยามรายวัน) → แท่งที่ครอบวันนั้นบนกราฟที่กำลังแสดง.

    มุมมองรายวัน = แท่งวันเดียวกัน; รายสัปดาห์ = แท่งของสัปดาห์ที่มีวันนั้น
    (index W-FRI คือวันสิ้นสัปดาห์ → ใช้แท่งแรกที่ index ≥ วันสัญญาณ)
    หลายสัญญาณตกแท่งเดียวกันถูกยุบเหลือเครื่องหมายเดียว
    """
    if not dates or display_ohlc.empty:
        return pd.DataFrame(columns=["x", "high", "low"])
    index = display_ohlc.index
    rows: list[dict[str, object]] = []
    for date in dates:
        pos = int(index.searchsorted(date, side="left"))
        if pos >= len(index):
            pos = len(index) - 1
        rows.append(
            {
                "x": index[pos],
                "high": float(display_ohlc["High"].iloc[pos]),
                "low": float(display_ohlc["Low"].iloc[pos]),
            }
        )
    return pd.DataFrame(rows).drop_duplicates(subset=["x"])


def _render_underwater_section(ticker: str, close_series: pd.Series) -> None:
    """กราฟ underwater (Roadmap A3): % ต่ำกว่า ATH ตามเวลา + สถิติการฟื้นรอบก่อน.

    เป้าหมายคือกัน panic-selling — ให้เห็นว่า drawdown ระดับนี้เคยเกิดและเคยฟื้นอย่างไร
    ตัวเลขเป็นสถิติเชิงบรรยายจากราคาจริง (analysis/risk.py) ไม่ใช่สัญญาณซื้อขาย
    """
    st.subheader(f"Underwater — {ticker} ต่ำกว่าจุดสูงสุดเดิม (ATH) กี่ %")
    if close_series.empty:
        st.error(f"{ticker}: ไม่มีข้อมูลราคา — ไม่แสดงกราฟ underwater")
        return

    underwater_pct = underwater_series(close_series) * 100.0

    uw_fig = go.Figure()
    uw_fig.add_trace(
        go.Scatter(
            x=underwater_pct.index,
            y=underwater_pct,
            mode="lines",
            line=dict(color=THEME["negative"], width=1.5),
            fill="tozeroy",
            fillcolor="rgba(248, 81, 73, 0.25)",
            name="ต่ำกว่า ATH (%)",
        )
    )
    uw_fig.update_layout(height=300, yaxis_title="% จาก ATH", showlegend=False)
    st.plotly_chart(_apply_plotly_dark_theme(uw_fig), use_container_width=True)

    current_uw = float(underwater_pct.iloc[-1])
    if current_uw > -0.1:
        st.caption(f"ตอนนี้ {ticker} อยู่ที่จุดสูงสุดเดิม (ATH)")
    else:
        st.caption(f"ตอนนี้ {ticker} ต่ำกว่า ATH {abs(current_uw):.1f}%")

    episodes = drawdown_episodes(close_series, min_depth=0.10)
    if not episodes:
        st.caption("ในช่วงข้อมูลที่โหลด ยังไม่เคยมี drawdown ลึกเกิน 10%")
        return

    st.markdown("**การฟื้นตัวรอบก่อน (เฉพาะรอบที่ลงลึกเกิน 10%)**")

    def _fmt_date(value: object) -> str:
        return pd.Timestamp(value).strftime("%b %Y") if value is not None else "-"

    # จงใจใช้ตาราง markdown แทน st.dataframe — การแปลง Arrow ของตารางนี้ในบริบทหน้าเต็ม
    # segfault กับ pyarrow 25 (จับได้จาก AppTest; pyarrow เพิ่งถูก pin ใน requirements.txt)
    lines = [
        "| พีคเมื่อ | ลึกสุด | จุดต่ำสุดเมื่อ | กลับมา ATH เมื่อ | เวลาฟื้นจากพีค (เดือน) |",
        "|---|---|---|---|---|",
    ]
    for e in episodes:
        recovery_text = (
            _fmt_date(e["recovery_date"])
            if e["recovery_date"] is not None
            else "ยังไม่ฟื้น (รอบปัจจุบัน)"
        )
        months_text = (
            f"{e['months_to_recover']:.1f}" if e["months_to_recover"] is not None else "-"
        )
        lines.append(
            f"| {_fmt_date(e['peak_date'])} | {e['depth_pct']:.1f}% | "
            f"{_fmt_date(e['trough_date'])} | {recovery_text} | {months_text} |"
        )
    st.markdown("\n".join(lines))

    recovered_months = [
        float(e["months_to_recover"]) for e in episodes if e["months_to_recover"] is not None
    ]
    if recovered_months:
        median_months = float(pd.Series(recovered_months).median())
        st.caption(
            f"รอบที่ลึกเกิน 10% ในช่วงข้อมูลนี้ฟื้นกลับ ATH แล้ว {len(recovered_months)} รอบ "
            f"ใช้เวลา median {median_months:.0f} เดือนจากพีคเดิม — "
            "สถิติในอดีตของช่วงข้อมูลที่โหลด ไม่ใช่การพยากรณ์"
        )


def _confluence_chip(daily_central: str, weekly_central: str) -> tuple[str, str]:
    """สรุปว่าสัญญาณ Daily กับ Weekly ตรงกันไหม (Roadmap B3) — ชั้นความมั่นใจ ไม่ใช่สัญญาณใหม่."""
    if signal_rules.NO_DATA in (daily_central, weekly_central):
        return "เทียบ Daily/Weekly ไม่ได้ — ข้อมูลไม่พอ", THEME["text_secondary"]
    up = {signal_rules.BULLISH, signal_rules.ACCUMULATE}
    down = {signal_rules.DOWNTREND, signal_rules.DOWNTREND_WATCH}
    if daily_central in up and weekly_central in up:
        return "Daily+Weekly ขาขึ้นตรงกัน — มั่นใจสูง", THEME["positive"]
    if daily_central in down and weekly_central in down:
        return "Daily+Weekly ขาลงตรงกัน — มั่นใจสูง", THEME["negative"]
    if daily_central == weekly_central:
        return "Daily+Weekly ตรงกัน", THEME["text_secondary"]
    return "Daily/Weekly ไม่ตรงกัน — ช่วงเปลี่ยนผ่าน สัญญาณอ่อน", THEME["text_secondary"]


def _render_seasonality_section(ticker: str, close_series: pd.Series) -> None:
    """Seasonality รายเดือน (Roadmap B5) — เชิงบรรยายเท่านั้น ห้ามเข้าเลขคะแนน/จัดสรร."""
    st.subheader(f"Seasonality — {ticker} แยกตามเดือนปฏิทินในอดีต")
    try:
        stats = monthly_seasonality(close_series)
    except ValueError as exc:
        st.error(f"{ticker}: {exc}")
        return

    month_labels = [
        "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
        "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค.",
    ]
    medians = stats["median_pct"]
    bar_colors = [
        THEME["positive"] if (pd.notna(v) and v >= 0) else THEME["negative"] for v in medians
    ]
    hover_text = []
    for month in range(1, 13):
        row = stats.loc[month]
        if pd.isna(row["median_pct"]):
            hover_text.append("ไม่มีข้อมูลเดือนนี้")
        else:
            hover_text.append(
                f"median {row['median_pct']:+.1f}% · บวก {row['positive_rate_pct']:.0f}% ของปี "
                f"· ตัวอย่าง {int(row['n_samples'])} ปี"
            )
    season_fig = go.Figure(
        go.Bar(x=month_labels, y=medians, marker_color=bar_colors, text=hover_text, hoverinfo="x+text")
    )
    season_fig.update_layout(height=300, yaxis_title="median ผลตอบแทนเดือน (%)", showlegend=False)
    st.plotly_chart(_apply_plotly_dark_theme(season_fig), use_container_width=True)

    sample_counts = stats["n_samples"].dropna()
    st.caption(
        f"ตัวอย่างต่อเดือนมีแค่ ~{int(sample_counts.min())}–{int(sample_counts.max())} ปี — "
        "น้อยมากเชิงสถิติ (noise สูง) ใช้เล่าเรื่องประกอบเท่านั้น "
        "ห้ามใช้เลื่อน/ข้ามการซื้อ — ตาราง DCA เดินตามปกติ และค่านี้ไม่เข้าเลขคะแนน/จัดสรร"
    )


def _add_buy_overlay(fig: go.Figure, ticker: str, display_ohlc: pd.DataFrame) -> None:
    """วางจุดซื้อจริง + เส้นต้นทุนเฉลี่ยจาก ledger ลงบนกราฟราคา (Roadmap A4).

    ledger เป็น buy-only (CSV local, gitignored) — ไม่มีธุรกรรมก็แจ้งเฉย ๆ ไม่พัง
    ต้นทุนเฉลี่ยคิดจากราคา USD ใน ledger ล้วน (ค่าธรรมเนียมบันทึกเป็นบาท จึงไม่รวม)
    """
    transactions = get_transactions(ticker)
    if transactions.empty:
        st.caption(f"ยังไม่มีธุรกรรมของ {ticker} ใน ledger — เพิ่มได้ที่หน้า Portfolio")
        return

    tx = transactions.copy()
    tx["date"] = pd.to_datetime(tx["date"], errors="coerce")
    tx["shares"] = pd.to_numeric(tx["shares"], errors="coerce")
    tx["price_usd"] = pd.to_numeric(tx["price_usd"], errors="coerce")
    tx = tx.dropna(subset=["date", "shares", "price_usd"])
    tx = tx[(tx["shares"] > 0) & (tx["price_usd"] > 0)].sort_values("date")
    if tx.empty:
        st.caption(f"ธุรกรรมของ {ticker} ใน ledger ไม่มีแถวที่วาดได้ (date/shares/price ไม่ครบ)")
        return

    cumulative_shares = tx["shares"].cumsum()
    average_cost = (tx["shares"] * tx["price_usd"]).cumsum() / cumulative_shares

    # เส้นขั้นบันไดลากถึงแท่งสุดท้ายของกราฟ ให้เห็นต้นทุนปัจจุบันเทียบราคาได้ทันที
    step_x = list(tx["date"]) + [display_ohlc.index[-1]]
    step_y = list(average_cost) + [float(average_cost.iloc[-1])]
    fig.add_trace(
        go.Scatter(
            x=step_x,
            y=step_y,
            mode="lines",
            line=dict(color=THEME["accent"], width=2, dash="dot"),
            line_shape="hv",
            name="ต้นทุนเฉลี่ยของฉัน",
            hoverinfo="x+y+name",
        ),
        row=1,
        col=1,
    )
    hover_text = [
        f"ซื้อ {shares:g} หุ้น @ ${price:,.2f}"
        for shares, price in zip(tx["shares"], tx["price_usd"])
    ]
    fig.add_trace(
        go.Scatter(
            x=tx["date"],
            y=tx["price_usd"],
            mode="markers",
            marker=dict(
                symbol="circle", size=9, color=THEME["accent"], line=dict(color="#FFFFFF", width=1)
            ),
            name="จุดซื้อของฉัน",
            text=hover_text,
            hoverinfo="text",
        ),
        row=1,
        col=1,
    )
    st.caption(
        f"จุดซื้อ {len(tx)} ครั้ง · ต้นทุนเฉลี่ยปัจจุบัน ${float(average_cost.iloc[-1]):,.2f}/หุ้น "
        "(จากราคา USD ใน ledger — ไม่รวมค่าธรรมเนียมที่บันทึกเป็นบาท)"
    )


def _render_trend_channel_section(ticker: str, close_series: pd.Series) -> None:
    """Trend channel (Roadmap A2): ราคาปัจจุบันอยู่ส่วนไหนของช่องเทรนด์หลายปีตัวเอง.

    สถิติพรรณนาเทียบเทรนด์ตัวเองในอดีต — ไม่ใช่การพยากรณ์ และไม่เข้าเลขคะแนน/จัดสรร
    ใช้พิจารณาเฉพาะ "เงินเติมพิเศษ" ส่วนแผน DCA หลักซื้อตามตารางเสมอ
    """
    st.subheader(f"Trend Channel — {ticker} เทียบเทรนด์หลายปีของตัวเอง")
    try:
        channel = fit_trend_channel(close_series)
    except ValueError as exc:
        # ข้อมูลไม่พอ/ใช้ไม่ได้ = บอกตรง ๆ ไม่วาดแถบจากข้อมูลบาง ๆ (AUDIT.md C1)
        st.error(f"{ticker}: {exc}")
        return

    trend = channel["trend"]
    sigma = float(channel["sigma_log"])
    current_sigma = float(channel["current_sigma"])
    factor_1s = math.exp(sigma)
    factor_2s = math.exp(2.0 * sigma)

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=trend.index, y=trend * factor_2s, mode="lines",
            line=dict(width=0), name="+2σ", showlegend=False, hoverinfo="skip",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=trend.index, y=trend / factor_2s, mode="lines",
            line=dict(width=0), fill="tonexty", fillcolor="rgba(56, 139, 253, 0.08)",
            name="-2σ", showlegend=False, hoverinfo="skip",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=trend.index, y=trend * factor_1s, mode="lines",
            line=dict(color=THEME["text_secondary"], width=1, dash="dot"), name="+1σ",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=trend.index, y=trend / factor_1s, mode="lines",
            line=dict(color=THEME["text_secondary"], width=1, dash="dot"), name="-1σ",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=trend.index, y=trend, mode="lines",
            line=dict(color=THEME["accent"], width=2, dash="dash"), name="เทรนด์ (log-linear)",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=close_series.index, y=close_series, mode="lines",
            line=dict(color=THEME["text_primary"], width=1.5), name="ราคา",
        )
    )
    fig.update_layout(
        height=340,
        yaxis_title="Price (USD)",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02},
    )
    st.plotly_chart(_apply_plotly_dark_theme(fig), use_container_width=True)

    if current_sigma >= 1.5:
        zone_text = "โซนบนของช่อง — แพงเชิงสถิติเทียบเทรนด์ตัวเอง เงินเติมพิเศษได้ความคุ้มต่ำกว่าค่าเฉลี่ย"
        zone_color = THEME["negative"]
    elif current_sigma <= -1.5:
        zone_text = "โซนล่างของช่อง — ถูกเชิงสถิติเทียบเทรนด์ตัวเอง จังหวะของเงินเติมพิเศษ"
        zone_color = THEME["positive"]
    else:
        zone_text = "อยู่กลางช่องเทรนด์"
        zone_color = THEME["text_secondary"]
    st.markdown(_chip_html(f"{current_sigma:+.1f}σ · {zone_text}", zone_color), unsafe_allow_html=True)

    metric_col1, metric_col2 = st.columns(2)
    metric_col1.metric("ตำแหน่งปัจจุบันในช่อง", f"{current_sigma:+.1f}σ")
    metric_col2.metric("อัตราโตตามเทรนด์", f"{channel['annual_growth_pct']:.1f}%/ปี")
    st.caption(
        "แถบ = เทรนด์ log-linear ±1σ/±2σ จากช่วงข้อมูลที่โหลด — สถิติอดีต ไม่ใช่การพยากรณ์ "
        "และไม่เข้าเลขคะแนน/จัดสรรของระบบ · แผน DCA หลักซื้อตามตารางเสมอ"
    )


def render_technical_signals_page(prices: pd.DataFrame) -> None:
    """หน้า Technical Signals: กราฟ Candlestick + RSI + การ์ดสรุปสัญญาณ."""
    st.header("Technical Signals")
    technical_tickers = get_tickers()
    if not technical_tickers:
        st.warning("ยังไม่มี ETF ในระบบ — เพิ่มได้ที่หน้า Settings")
        return

    selected_ticker = st.selectbox("เลือก ETF", technical_tickers, index=0)
    timeframe = st.radio(
        "กรอบเวลา",
        ["รายสัปดาห์", "รายวัน"],
        horizontal=True,
        key="technical_timeframe",
        help="สัญญาณทุกตัว (MA/RSI/จุดสะสม) คำนวณจากแท่งรายวันตามนิยามกลางเสมอ — กรอบเวลาเปลี่ยนแค่มุมมอง",
    )
    show_buys = st.checkbox(
        "แสดงจุดซื้อของฉัน + เส้นต้นทุนเฉลี่ย (จาก ledger)",
        value=True,
        key="technical_show_buys",
    )

    with st.spinner("กำลังโหลดข้อมูลราคา..."):
        ohlc_map = fetch_ohlc_data(technical_tickers, years=10)
    selected_ohlc = ohlc_map.get(selected_ticker)
    if selected_ohlc is None or selected_ohlc.empty:
        # fetch_ohlc_data คืน {} เมื่อดึงไม่สำเร็จ — หยุดชัด ๆ ห้ามวาดกราฟจากข้อมูลว่าง (AUDIT.md C1)
        st.error(f"ดึงข้อมูล OHLC ของ {selected_ticker} ไม่สำเร็จ — ไม่แสดงกราฟจากข้อมูลว่าง ลองกด Refresh Data")
        return

    selected_signals = calculate_technical_signals(prices[selected_ticker]).dropna(subset=["MA50", "MA200", "RSI14"])
    if selected_signals.empty:
        st.error(f"{selected_ticker}: ข้อมูลไม่พอคำนวณ MA200/RSI — ไม่แสดงสัญญาณ")
        return

    # สัญญาณกลางรายวัน (นิยามเดียวกับทุก subsystem — AUDIT.md C2) แล้วค่อยวาดลงแท่งที่เลือก
    sig = selected_signals.copy()
    sig["central"] = [
        signal_rules.dca_signal(price, ma50, ma200, rsi)
        for price, ma50, ma200, rsi in zip(sig["Price"], sig["MA50"], sig["MA200"], sig["RSI14"])
    ]
    accumulate_days = list(sig.index[sig["central"] == signal_rules.ACCUMULATE])
    crosses = ma_cross_dates(sig["MA50"], sig["MA200"])

    if timeframe == "รายสัปดาห์":
        display_ohlc = (
            selected_ohlc.resample("W-FRI")
            .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"})
            .dropna(how="any")
        )
    else:
        display_ohlc = selected_ohlc

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        row_heights=[0.7, 0.3],
        subplot_titles=(f"{selected_ticker} Candlestick + เหตุผลบนกราฟ", "RSI (14) รายวัน"),
    )

    fig.add_trace(
        go.Candlestick(
            x=display_ohlc.index,
            open=display_ohlc["Open"],
            high=display_ohlc["High"],
            low=display_ohlc["Low"],
            close=display_ohlc["Close"],
            name="Candlestick",
        ),
        row=1,
        col=1,
    )

    # พื้นหลังเขียว = ช่วงที่ราคายืนเหนือ MA200 (เห็นขาขึ้นแวบเดียวโดยไม่ต้องไล่เส้น)
    uptrend = sig["Price"] >= sig["MA200"]
    runs = (uptrend != uptrend.shift(1)).cumsum()
    for _, segment in sig[uptrend].groupby(runs[uptrend]):
        fig.add_vrect(
            x0=segment.index[0],
            x1=segment.index[-1],
            fillcolor="rgba(63, 185, 80, 0.08)",
            line_width=0,
            row=1,
            col=1,
        )

    # เส้น MA คงนิยามรายวันเสมอ (MA50/MA200 วัน — ห้ามคำนวณใหม่บนแท่ง week)
    fig.add_trace(
        go.Scatter(
            x=sig.index,
            y=sig["MA50"],
            mode="lines",
            line=dict(color="orange", width=2),
            name="MA50 (วัน)",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=sig.index,
            y=sig["MA200"],
            mode="lines",
            line=dict(color="red", width=2),
            name="MA200 (วัน)",
        ),
        row=1,
        col=1,
    )

    # เครื่องหมายบนแท่งจริง: ▲ วันสะสม, ★ golden, ✕ death (ไม่มีลูกศรขาย — โมเดลนี้คือ DCA)
    acc_pos = _signal_bar_positions(display_ohlc, accumulate_days)
    if not acc_pos.empty:
        fig.add_trace(
            go.Scatter(
                x=acc_pos["x"],
                y=acc_pos["low"] * 0.97,
                mode="markers",
                marker=dict(symbol="triangle-up", size=9, color=THEME["positive"]),
                name="จุดสะสม (ACCUMULATE)",
                hoverinfo="x+name",
            ),
            row=1,
            col=1,
        )
    golden_pos = _signal_bar_positions(display_ohlc, crosses["golden"])
    if not golden_pos.empty:
        fig.add_trace(
            go.Scatter(
                x=golden_pos["x"],
                y=golden_pos["high"] * 1.03,
                mode="text",
                text=["★"] * len(golden_pos),
                textfont=dict(size=18, color="#E3B341"),
                name="Golden Cross ★",
                hoverinfo="x+name",
            ),
            row=1,
            col=1,
        )
    death_pos = _signal_bar_positions(display_ohlc, crosses["death"])
    if not death_pos.empty:
        fig.add_trace(
            go.Scatter(
                x=death_pos["x"],
                y=death_pos["high"] * 1.03,
                mode="text",
                text=["✕"] * len(death_pos),
                textfont=dict(size=16, color=THEME["negative"]),
                name="Death Cross ✕",
                hoverinfo="x+name",
            ),
            row=1,
            col=1,
        )

    fig.add_trace(
        go.Scatter(
            x=sig.index,
            y=sig["RSI14"],
            mode="lines",
            line=dict(color="deepskyblue", width=2),
            name="RSI",
        ),
        row=2,
        col=1,
    )
    fig.add_hline(y=70, line=dict(color="red", dash="dash"), row=2, col=1)
    fig.add_hline(y=30, line=dict(color="green", dash="dash"), row=2, col=1)
    fig.add_hrect(y0=70, y1=100, fillcolor="rgba(255, 0, 0, 0.12)", line_width=0, row=2, col=1)
    fig.add_hrect(y0=0, y1=30, fillcolor="rgba(0, 128, 0, 0.12)", line_width=0, row=2, col=1)

    if show_buys:
        _add_buy_overlay(fig, selected_ticker, display_ohlc)

    fig.update_layout(height=850, xaxis_rangeslider_visible=False, legend_title_text="Indicators")
    fig.update_yaxes(title_text="Price (USD)", row=1, col=1)
    fig.update_yaxes(title_text="RSI", range=[0, 100], row=2, col=1)
    st.plotly_chart(_apply_plotly_dark_theme(fig), use_container_width=True)
    st.caption(
        "พื้นหลังเขียว = ราคายืนเหนือ MA200 (ขาขึ้น) · ▲ = วัน ACCUMULATE (ย่อในขาขึ้น — จังหวะสะสมตามแผน DCA) · "
        "★/✕ = golden/death cross MA50-MA200 (ข้อมูลแนวโน้ม ไม่ใช่คำสั่งซื้อขาย) · "
        "สัญญาณทั้งหมดคำนวณจากแท่งรายวันตามนิยามกลาง `technical/signal_rules`"
    )

    _render_underwater_section(selected_ticker, prices[selected_ticker].dropna())

    _render_trend_channel_section(selected_ticker, prices[selected_ticker].dropna())

    _render_seasonality_section(selected_ticker, prices[selected_ticker].dropna())

    st.subheader("Signal Summary Cards")
    columns = st.columns(len(technical_tickers))
    for idx, ticker in enumerate(technical_tickers):
        ticker_prices = prices[ticker].dropna()
        ticker_signals = calculate_technical_signals(ticker_prices).dropna(subset=["MA50", "MA200", "RSI14"])
        if ticker_signals.empty:
            with columns[idx]:
                st.warning(f"{ticker}: ข้อมูลไม่พอคำนวณสัญญาณ")
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

        weekly = weekly_dca_signal(ticker_prices)
        daily_central = signal_rules.dca_signal(current_price, ma50, ma200, rsi_value)
        confluence_text, confluence_color = _confluence_chip(daily_central, weekly["signal"])

        with columns[idx]:
            with st.container(border=True):
                st.markdown(f"**{ticker}**")
                st.markdown(f"Price: **${current_price:,.2f}**")
                st.markdown(f"MA50 / MA200: **{ma50_state} / {ma200_state}**")
                st.markdown(f"RSI: **{rsi_value:.2f} ({rsi_state})**")
                st.markdown(f"สัญญาณ: **{signal}**")
                st.markdown(f"Weekly: **{signal_rules.thai_description(weekly['signal'])}**")
                st.markdown(_chip_html(confluence_text, confluence_color), unsafe_allow_html=True)


def _build_weight_sliders(
    tickers: list[str], default_weights: dict[str, float], key_prefix: str
) -> dict[str, float]:
    """สร้าง slider ปรับน้ำหนัก แล้ว normalize ให้รวมเป็น 1."""
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
        raise ValueError("ผลรวมน้ำหนักต้องมากกว่า 0")

    return {k: v / total_weight for k, v in raw_weights.items()}


def render_backtest_page(prices: pd.DataFrame, default_weights: dict[str, float], tickers: list[str]) -> None:
    """หน้า Backtest: เทียบผลพอร์ตย้อนหลังกับ benchmark."""
    st.header("Backtest")
    benchmark_ticker = "VOO" if "VOO" in tickers else tickers[0]
    st.caption(f"เทียบพอร์ตตามน้ำหนักที่กำหนดกับ {benchmark_ticker}")
    st.info(
        "โมเดลนี้ปรับสัดส่วนกลับ (rebalance) **ทุกวันทำการ** และไม่คิดค่าธรรมเนียม/ภาษี — "
        "ผลจริงจากการ DCA เดือนละครั้งจะต่างจากนี้ | เริ่มนับจากวันแรกที่ทุก ETF ในพอร์ตมีข้อมูล"
    )

    initial_capital = st.number_input(
        "เงินลงทุนเริ่มต้น (USD)",
        min_value=100.0,
        value=10000.0,
        step=100.0,
        format="%.2f",
    )
    st.markdown("**น้ำหนักแต่ละ ETF**")
    normalized_weights = _build_weight_sliders(tickers, default_weights, "backtest_weight")

    if st.button("Run Backtest", type="primary"):
        try:
            backtest_df = run_portfolio_backtest(prices, normalized_weights, initial_capital=initial_capital)
        except RuntimeError as exc:
            st.error(str(exc))
            return

        start_date = backtest_df.index[0]
        st.caption(f"ช่วงที่ทดสอบจริง: {start_date:%d/%m/%Y} – {backtest_df.index[-1]:%d/%m/%Y}")

        # benchmark ต้องเริ่มวันเดียวกับพอร์ต ไม่งั้นเทียบผลตอบแทนกันไม่ได้
        benchmark_prices = prices[benchmark_ticker].ffill().dropna()
        benchmark_prices = benchmark_prices.loc[benchmark_prices.index >= start_date]
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
        st.info("กดปุ่ม Run Backtest เพื่อดูผล")


def render_dca_simulator_page(prices: pd.DataFrame, default_weights: dict[str, float], tickers: list[str]) -> None:
    """หน้า DCA Simulator: จำลองการทยอยลงทุนรายเดือน."""
    st.header("DCA Simulator")
    st.caption("จำลองการซื้อทุกเดือนด้วยงบเท่ากัน (ไม่รวมค่าธรรมเนียม)")

    monthly_investment = st.number_input(
        "งบ DCA ต่อเดือน (USD)",
        min_value=50.0,
        value=1000.0,
        step=50.0,
        format="%.2f",
    )
    st.markdown("**น้ำหนักแต่ละ ETF**")
    normalized_weights = _build_weight_sliders(tickers, default_weights, "dca_weight")

    dca_df = simulate_monthly_dca(prices, normalized_weights, monthly_investment=monthly_investment)

    dca_fig = px.line(
        dca_df,
        x=dca_df.index,
        y=["Total Invested", "Portfolio Value"],
        title="เงินลงทุนสะสม vs มูลค่าพอร์ต",
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
    """Flatten financial_model.run_full_analysis() for tables and charts.

    NO DATA และ ticker ที่ทำ DCF ไม่ได้ (เช่น GLDM) แสดงเป็น N/A ไม่ใช่ 0 (AUDIT.md C1/C4)
    """
    if not full_analysis or not isinstance(full_analysis.get("analysis"), dict):
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for ticker, payload in full_analysis["analysis"].items():
        if not isinstance(payload, dict):
            continue
        if not payload.get("data_ok", True):
            rows.append(
                {
                    "Ticker": str(ticker).upper(),
                    "Score %": None,
                    "Trend": None,
                    "Timing": None,
                    "Momentum": None,
                    "Dividend": None,
                    "RSI": None,
                    "Signal": "NO DATA",
                    "Current (USD)": None,
                    "DCF intrinsic (USD)": None,
                    "Margin of Safety %": None,
                    "DCF signal": "N/A",
                    "Data": f"NO DATA ({payload.get('error', '')})",
                }
            )
            continue
        dcf = payload.get("dcf") if isinstance(payload.get("dcf"), dict) else {}
        dcf_ok = bool(payload.get("dcf_available", True))
        rows.append(
            {
                "Ticker": str(ticker).upper(),
                "Score %": float(payload.get("total_pct") or 0),
                "Trend": int(payload.get("trend_score", 0) or 0),
                "Timing": int(payload.get("timing_score", 0) or 0),
                "Momentum": int(payload.get("momentum_score", 0) or 0),
                "Dividend": int(payload.get("dividend_score", 0) or 0),
                "RSI": float(payload.get("rsi", 0) or 0),
                "Signal": str(payload.get("signal", "")),
                "Current (USD)": float(dcf.get("current_price") or 0) if dcf_ok else None,
                "DCF intrinsic (USD)": float(dcf.get("intrinsic_value") or 0) if dcf_ok else None,
                "Margin of Safety %": float(dcf.get("margin_of_safety") or 0) if dcf_ok else None,
                "DCF signal": str(dcf.get("signal", "")) if dcf_ok else "N/A (ไม่มีกำไร → ทำ DCF ไม่ได้)",
                "Data": "OK",
            }
        )
    order = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]
    rows.sort(key=lambda r: order.index(str(r["Ticker"])) if str(r["Ticker"]) in order else 99)
    return pd.DataFrame(rows)


# หมายเหตุ: _extract_allocation_df (regex แกะตัวเลขจากข้อความ AI) ถูกถอดออกแล้ว —
# แผนจัดสรรมาจาก get_monthly_advice()["allocation"] ที่คำนวณในโค้ดโดยตรง (AUDIT.md C3)


@st.cache_data(ttl=3600)
def cached_full_analysis(budget_thb: float) -> dict:
    return run_full_analysis(budget_thb=float(budget_thb))


def render_dcf_analysis_page() -> None:
    """หน้า DCF Analysis: เจาะราย ETF + heatmap คะแนนรวม."""
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
            st.session_state["dcf_full_analysis"] = cached_full_analysis(float(budget_thb))

    if st.button("Run Full Analysis", type="primary", key="dcf_run_full"):
        with st.spinner("Running all ETF analysis..."):
            st.session_state["dcf_full_analysis"] = cached_full_analysis(float(budget_thb))
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

    if not bool(selected_raw.get("data_ok", True)):
        st.error(f"{selected_ticker}: ดึงข้อมูลไม่สำเร็จ — {selected_raw.get('error', '')}")
        return

    dcf_ok = bool(selected_raw.get("dcf_available", True))
    st.info(
        "DCF ของ ETF เป็นข้อมูล**ประกอบ** ไม่ถูกนับเป็นคะแนนซื้อ/ขาย "
        "(เป็น earnings-yield proxy จาก P/E ไม่ใช่ DCF จากงบกระแสเงินสดของกิจการ)"
    )
    if not dcf_ok:
        st.warning(
            f"{selected_ticker} ไม่มีกำไร/ค่า P/E (เช่น กองทองคำ) — ทำ DCF ไม่ได้เลย "
            "ระบบจึงไม่แสดงมูลค่าที่แท้จริง แทนการเดาตัวเลขให้ดูน่าเชื่อ"
        )

    cards = st.columns(4)

    def _money(value: object) -> str:
        return f"${float(value):,.2f}" if value is not None and pd.notna(value) else "N/A"

    cards[0].metric("Score (0-100)", f"{float(selected_row['Score %']):.1f}")
    cards[1].metric("DCF Intrinsic Value", _money(selected_row["DCF intrinsic (USD)"]))
    mos_val = selected_row["Margin of Safety %"]
    cards[2].metric(
        "Margin of Safety %",
        f"{float(mos_val):.2f}%" if mos_val is not None and pd.notna(mos_val) else "N/A",
    )
    cards[3].metric("Signal", str(selected_row["Signal"]))
    st.caption(f"สัญญาณเทคนิค: {selected_raw.get('technical_signal_th', '-')}")

    st.subheader("Score Breakdown (คะแนนเดียวกับที่ AI Advisor ใช้)")
    breakdown_map: dict[str, float] = {
        "Trend (max 40)": float(selected_row["Trend"]),
        "Timing (max 30)": float(selected_row["Timing"]),
        "Momentum (max 20)": float(selected_row["Momentum"]),
        "Dividend (max 10)": float(selected_row["Dividend"]),
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

    if dcf_ok:
        st.subheader("DCF Assumptions")
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("WACC %", f"{float(selected_dcf.get('wacc', 0)):.2f}%")
        a2.metric("Growth Rate %", f"{float(selected_dcf.get('growth_rate', 0)):.2f}%")
        a3.metric("Terminal Growth %", f"{float(selected_dcf.get('terminal_growth', 0)):.2f}%")
        a4.metric("Beta", f"{float(selected_dcf.get('beta', 0)):.2f}")
        st.caption(
            "หมายเหตุ: DCF ของ ETF เป็น proxy จาก earnings yield (1/PE) + ปันผล ไม่ใช่ DCF "
            "จากงบกระแสเงินสดของกิจการ — ใช้เป็นตัวชี้วัดคร่าว ๆ เท่านั้น"
        )

    st.subheader("Heatmap Score (All ETFs)")
    heat_cols = ["Score %", "Trend", "Timing", "Momentum", "Dividend"]
    heatmap_df = score_df.set_index("Ticker")[heat_cols].astype(float)
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
        zmax=100,
    )
    heatmap_fig.update_layout(coloraxis_colorbar_title="Score")
    st.plotly_chart(_apply_plotly_dark_theme(heatmap_fig), use_container_width=True)
    st.caption("ช่องว่าง = ข้อมูลไม่พร้อม (NO DATA)")


def show_result(result: dict) -> None:
    """Render AI Advisor output — ตัวเลขทุกตัวมาจากโมเดลโดยตรง ไม่ parse จากข้อความ AI.

    (AUDIT.md C3/H5: เดิมหน้านี้คาดหวัง key ที่ get_monthly_advice ไม่เคยคืน
    ทำให้ตารางคะแนนไม่แสดง และตาราง allocation ถูก regex จากข้อความ AI)
    """
    etf_scores = result.get("etf_scores") or []
    score_rows: list[dict[str, object]] = []
    for row in etf_scores:
        if not isinstance(row, dict):
            continue
        data_ok = bool(row.get("data_ok", True))
        score_rows.append(
            {
                "Ticker": str(row.get("ticker", "")).upper(),
                "Price (USD)": row.get("price"),
                "MA50": row.get("ma50"),
                "MA200": row.get("ma200"),
                "RSI": row.get("rsi"),
                "Score %": row.get("total_pct"),
                "Signal": str(row.get("signal", "")),
                "Data": "OK" if data_ok else f"NO DATA ({row.get('error', '')})",
            }
        )
    if score_rows:
        st.subheader("คะแนนจากโมเดล (Score % = คะแนนที่คำนวณในโค้ด 0-100)")
        score_df = pd.DataFrame(score_rows).sort_values("Score %", ascending=False, na_position="last")
        st.dataframe(
            score_df.style.format(
                {
                    "Price (USD)": "${:,.2f}",
                    "MA50": "${:,.2f}",
                    "MA200": "${:,.2f}",
                    "RSI": "{:.1f}",
                    "Score %": "{:.1f}",
                },
                na_rep="N/A",
            ),
            use_container_width=True,
        )

    no_data = result.get("no_data_tickers") or []
    if no_data:
        st.error(f"ดึงข้อมูลไม่ได้: {', '.join(no_data)} — ตัวเหล่านี้ไม่ถูกนำมาคิดคะแนน/จัดสรร")

    allocation = result.get("allocation") or {}
    if allocation:
        st.markdown("### การจัดสรร DCA (คำนวณโดยโมเดล ไม่ใช่ AI)")
        st.caption(
            "ฐาน = สัดส่วนเป้าหมายของพอร์ต → ปรับน้ำหนักด้วยคะแนน "
            f"({TILT_MIN:.1f}–{TILT_MAX:.1f} เท่า) | ตัวคูณ > 1 = ซื้อมากกว่าเป้า, < 1 = ซื้อน้อยกว่าเป้า "
            "แต่ทุกตัวยังได้ซื้อทุกเดือนเพื่อรักษาการกระจายความเสี่ยง"
        )
        allocation_rows = [
            {
                "Ticker": ticker,
                "Amount (THB)": item.get("amount_thb", 0),
                "จัดสรรจริง": item.get("percent", 0),
                "เป้าหมาย": item.get("target_percent", 0),
                "ตัวคูณ": item.get("tilt"),
                "Score %": item.get("score"),
                "Signal": item.get("group", ""),
            }
            for ticker, item in allocation.items()
        ]
        allocation_df = pd.DataFrame(allocation_rows)
        st.dataframe(
            allocation_df.style.format(
                {
                    "Amount (THB)": "{:,.0f}",
                    "จัดสรรจริง": "{:.0f}%",
                    "เป้าหมาย": "{:.0f}%",
                    "ตัวคูณ": "{:.2f}×",
                    "Score %": "{:.1f}",
                },
                na_rep="N/A",
            ),
            use_container_width=True,
        )
        unallocated = float(result.get("unallocated_thb") or 0)
        if unallocated > 0:
            st.caption(f"ยังไม่จัดสรร {unallocated:,.0f} บาท (เศษจากการปัดหลักร้อย/ไม่มีสัญญาณรองรับ)")
        pie = px.pie(
            allocation_df,
            names="Ticker",
            values="Amount (THB)",
            title="DCA allocation (THB)",
            hole=0.35,
        )
        st.plotly_chart(_apply_plotly_dark_theme(pie), use_container_width=True)
    else:
        st.warning("เดือนนี้ไม่มี ETF ที่คะแนนถึงเกณฑ์จัดสรร — โมเดลแนะนำถือเงินสดรอ")

    advice_text = str(result.get("advice_text") or result.get("advice") or "")
    if result.get("ai_used"):
        st.markdown("### คำอธิบายจาก AI (Claude Haiku 4.5)")
        st.markdown(advice_text)
    else:
        st.info(advice_text)

    discord_result = result.get("discord_result", {})
    if discord_result.get("success"):
        st.info("ส่งสรุปเข้า Discord แล้ว")
    elif not discord_result.get("skipped"):
        st.warning(f"ส่ง Discord ไม่สำเร็จ: {discord_result.get('error', 'unknown error')}")


def _render_sentiment_context_box() -> None:
    """ข่าว/sentiment เป็นบริบทข้าง ๆ (Roadmap ข้อ 8) — ห้ามเข้าเลขคะแนน/จัดสรร (invariant)."""
    summaries = get_latest_sentiment_summaries(get_tickers())
    if summaries is None:
        st.caption(
            "บริบทข่าว/sentiment: ไม่มีข้อมูล (ไม่ได้ตั้ง DATABASE_URL หรือเชื่อมต่อไม่ได้) — ไม่กระทบคะแนนใด ๆ"
        )
        return
    if not summaries:
        st.caption("บริบทข่าว/sentiment: ยังไม่มีข้อมูลในฐาน — รอ scheduled job รอบถัดไป")
        return
    with st.expander("บริบทข่าว/Sentiment ล่าสุด (ไม่เข้าเลขคะแนน)"):
        for item in summaries:
            label = str(item.get("overall_sentiment") or "unknown").lower()
            dot = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}.get(label, "⚪")
            score = item.get("score")
            score_text = f"score {float(score):+.2f}" if score is not None else "score -"
            st.markdown(
                f"{dot} **{item['symbol']}** — {label} · {score_text} · "
                f"{item.get('total_articles') or 0} ข่าว · {item.get('created_at') or '-'}"
            )
        st.caption(
            "sentiment เป็นบริบทประกอบการอ่านสถานการณ์เท่านั้น — "
            "ไม่มีผลต่อคะแนน/การจัดสรร (invariant ของระบบ)"
        )


def render_ai_advisor_page() -> None:
    """หน้า AI Advisor: คะแนนและแผน DCA คำนวณในระบบ — AI อธิบายเหตุผล."""
    st.header("AI Advisor")
    st.caption("คะแนนและแผน DCA คำนวณในระบบทั้งหมด — AI ใช้เพื่ออธิบายเหตุผลเท่านั้น")
    _render_sentiment_context_box()
    config = load_config()

    if "ai_result" not in st.session_state:
        st.session_state["ai_result"] = None
    if "ai_running" not in st.session_state:
        st.session_state["ai_running"] = False

    budget_thb = st.number_input(
        "งบ DCA เดือนนี้ (บาท)",
        min_value=500.0,
        value=float(config["dca"]["monthly_budget_thb"]),
        step=500.0,
        format="%.0f",
    )

    col_free, col_ai = st.columns(2)
    with col_free:
        run_free = st.button("คำนวณคะแนน + แผน DCA (ฟรี)", type="primary", use_container_width=True)
    with col_ai:
        run_ai = st.button("ให้ AI อธิบายด้วย (มีค่าใช้จ่าย)", use_container_width=True)

    st.caption(
        "ปุ่มซ้าย: คำนวณทุกอย่างในระบบ ไม่เรียก AI ไม่มีค่าใช้จ่าย | "
        "ปุ่มขวา: เรียก Claude Haiku 4.5 มาอธิบายเพิ่ม (ประมาณ 0.10–0.30 บาทต่อครั้ง)"
    )

    if (run_free or run_ai) and not st.session_state["ai_running"]:
        st.session_state["ai_running"] = True
        try:
            with st.spinner("กำลังวิเคราะห์..."):
                st.session_state["ai_result"] = get_monthly_advice(
                    float(budget_thb),
                    send_discord=False,
                    user_initiated=run_ai,  # เรียก AI เฉพาะเมื่อกดปุ่มขวา
                )
        except Exception as exc:
            st.error(f"วิเคราะห์ไม่สำเร็จ: {exc}")
        finally:
            st.session_state["ai_running"] = False

    if st.session_state["ai_result"]:
        show_result(st.session_state["ai_result"])
    else:
        st.info("กดปุ่มด้านบนเพื่อเริ่มวิเคราะห์")


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_macro_data() -> pd.DataFrame:
    """ดึงตัวชี้วัด macro ย้อนหลัง 1 ปี.

    AUDIT.md H7 (แก้แล้ว):
    - CPI เดิมใช้ ``CPIAUCSL`` (FRED series ID) เป็น Yahoo ticker → **404 ตลอด**
      คอลัมน์ว่างเสมอ ตอนนี้ดึงจาก FRED และแปลงเป็นอัตราเงินเฟ้อ YoY (%)
    - "Fed Rate" เดิมใช้ ``^IRX`` (T-bill 13 สัปดาห์) ติดป้ายผิด → ดึง FEDFUNDS จริงจาก FRED
    """
    yahoo_tickers = {
        "10Y Treasury Yield": "^TNX",
        "DXY Dollar Index": "DX-Y.NYB",
        "VIX Fear Index": "^VIX",
    }
    try:
        downloaded = yf.download(
            tickers=list(yahoo_tickers.values()),
            period="1y",
            interval="1d",
            auto_adjust=False,
            progress=False,
            group_by="ticker",
        )
    except Exception:
        st.warning("ดึงข้อมูล macro จาก Yahoo ไม่สำเร็จ")
        return pd.DataFrame()

    if downloaded.empty:
        return pd.DataFrame()

    close_df = pd.DataFrame(index=downloaded.index)
    for label, ticker in yahoo_tickers.items():
        series = pd.Series(dtype="float64")
        if isinstance(downloaded.columns, pd.MultiIndex):
            if ticker in downloaded.columns.get_level_values(0) and "Close" in downloaded[ticker]:
                series = downloaded[ticker]["Close"]
        elif "Close" in downloaded.columns:
            series = downloaded["Close"]
        close_df[label] = pd.to_numeric(series, errors="coerce")

    close_df = close_df.sort_index().ffill()

    # ^TNX รายงานเป็น 10 เท่าของ % (เช่น 42.5 = 4.25%)
    if "10Y Treasury Yield" in close_df.columns and close_df["10Y Treasury Yield"].dropna().median() > 20:
        close_df["10Y Treasury Yield"] = close_df["10Y Treasury Yield"] / 10

    # Fed Funds Rate + เงินเฟ้อ YoY จาก FRED (รายเดือน → reindex เป็นรายวัน)
    fred_data = _fetch_fred_macro()
    for label, series in fred_data.items():
        if series.empty:
            close_df[label] = pd.NA
            continue
        aligned = series.reindex(
            series.index.union(close_df.index)
        ).ffill().reindex(close_df.index)
        close_df[label] = pd.to_numeric(aligned, errors="coerce")

    return close_df


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _fetch_fred_macro() -> dict[str, pd.Series]:
    """Fed Funds Rate (%) และอัตราเงินเฟ้อ CPI YoY (%) จาก FRED."""
    from analysis.macro import _cpi_yoy_percent

    api_key = os.getenv("FRED_API_KEY", "").strip()
    if not api_key or api_key == "your_key_here":
        return {"Fed Rate": pd.Series(dtype=float), "CPI Inflation (YoY %)": pd.Series(dtype=float)}
    try:
        from fredapi import Fred

        fred = Fred(api_key=api_key)
        fed = pd.to_numeric(fred.get_series("FEDFUNDS"), errors="coerce").dropna().sort_index()
        cpi_index = pd.to_numeric(fred.get_series("CPIAUCSL"), errors="coerce").dropna().sort_index()
        return {"Fed Rate": fed, "CPI Inflation (YoY %)": _cpi_yoy_percent(cpi_index)}
    except Exception:
        return {"Fed Rate": pd.Series(dtype=float), "CPI Inflation (YoY %)": pd.Series(dtype=float)}


def _vix_regime_text(vix_value: float) -> str:
    if vix_value < 20:
        return "สงบ"
    if vix_value <= 30:
        return "ระวัง"
    return "ผันผวนสูง"


def render_macro_page() -> None:
    """หน้า Macro: ภาวะตลาดโดยรวม."""
    st.header("Macro")
    st.caption("Fed Rate + เงินเฟ้อ (FRED) | พันธบัตร/DXY/VIX (Yahoo)")

    with st.spinner("กำลังโหลดข้อมูล macro..."):
        macro_df = fetch_macro_data()
    if macro_df.empty:
        st.error("ดึงข้อมูล Macro ไม่สำเร็จ")
        return

    required_cols = [
        "Fed Rate",
        "CPI Inflation (YoY %)",
        "10Y Treasury Yield",
        "DXY Dollar Index",
        "VIX Fear Index",
    ]
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
            elif col_name in {"Fed Rate", "CPI Inflation (YoY %)", "10Y Treasury Yield"}:
                st.metric(col_name, f"{latest:.2f}%", delta_fmt)
            else:
                st.metric(col_name, f"{latest:.2f}", delta_fmt)

    st.markdown("เกณฑ์ VIX: < 20 (สงบ) | 20-30 (ระวัง) | > 30 (ผันผวนสูง)")

    vix_series = macro_df["VIX Fear Index"].dropna()
    if vix_series.empty:
        st.warning("ไม่มีข้อมูล VIX ย้อนหลัง 1 ปี")
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
        cpi = latest_values["CPI Inflation (YoY %)"]
        ten_y = latest_values["10Y Treasury Yield"]
        dxy = latest_values["DXY Dollar Index"]
        vix = latest_values["VIX Fear Index"]
        vix_regime = _vix_regime_text(vix)
        real_rate = fed - cpi  # ดอกเบี้ยที่แท้จริง (real rate)

        st.subheader("สรุปภาวะ Macro")
        st.markdown(
            "\n".join(
                [
                    f"- Fed Rate อยู่ที่ **{fed:.2f}%** เทียบกับเงินเฟ้อ **{cpi:.2f}%** "
                    f"→ ดอกเบี้ยที่แท้จริง **{real_rate:+.2f}%** "
                    f"({'นโยบายตึงตัว' if real_rate > 1 else 'นโยบายผ่อนคลาย' if real_rate < 0 else 'ค่อนข้างเป็นกลาง'})",
                    f"- พันธบัตร 10 ปีให้ผลตอบแทน **{ten_y:.2f}%**",
                    f"- ดัชนีดอลลาร์ (DXY) อยู่ที่ **{dxy:.2f}**",
                    f"- VIX อยู่ที่ **{vix:.2f}** → ตลาด{vix_regime}",
                ]
            )
        )
    else:
        st.subheader("สรุปภาวะ Macro")
        missing = [c for c in required_cols if c not in latest_values]
        st.info(f"ข้อมูลไม่ครบ ไม่สามารถสรุปได้ (ขาด: {', '.join(missing)})")


@st.cache_data(ttl=3600, show_spinner=False)
def cached_dividend_yields(tickers: tuple[str, ...]) -> dict[str, float | None]:
    """gross dividend yield ต่อ ticker (ตัวดึงเดียวกับระบบคะแนน); ดึงไม่ได้ = None ไม่เดา."""
    return {ticker: _dividend_yield(ticker) for ticker in tickers}


def _render_overlap_section() -> None:
    """ความทับซ้อน/การกระจายจริง (Roadmap ข้อ 10) — correlation คำนวณจริง + โครงสร้างเชิงบรรยาย."""
    st.divider()
    st.subheader("การกระจายจริง & ความทับซ้อน")
    try:
        overlap_prices = cached_prices(get_tickers(), years=10)
        corr = calculate_correlation_matrix(overlap_prices)
    except Exception as exc:
        st.error(f"คำนวณ correlation ไม่ได้: {exc} — ไม่แสดงตัวเลขแทน")
        return

    tickers_in_corr = list(corr.columns)
    pairs: list[tuple[str, str, float]] = []
    for i, first in enumerate(tickers_in_corr):
        for second in tickers_in_corr[i + 1 :]:
            value = corr.loc[first, second]
            if pd.notna(value):
                pairs.append((first, second, float(value)))

    high_pairs = sorted([p for p in pairs if p[2] >= 0.85], key=lambda p: -p[2])
    if high_pairs:
        pair_text = ", ".join(f"{a}–{b} ({c:.2f})" for a, b, c in high_pairs)
        st.warning(
            f"คู่ที่เคลื่อนไหวแทบเป็นตัวเดียวกัน (correlation ≥ 0.85): {pair_text} — "
            "การถือทั้งคู่กระจายความเสี่ยงได้น้อยกว่าที่จำนวนตัวบอก"
        )
    diversifiers = sorted([p for p in pairs if p[2] <= 0.30], key=lambda p: p[2])
    if diversifiers:
        st.caption(
            "ตัวที่กระจายความเสี่ยงจริง (correlation ≤ 0.30): "
            + ", ".join(f"{a}–{b} ({c:.2f})" for a, b, c in diversifiers)
        )

    corr_fig = px.imshow(
        corr,
        text_auto=".2f",
        zmin=-1,
        zmax=1,
        color_continuous_scale=[
            [0.0, THEME["accent"]],
            [0.5, THEME["main_bg"]],
            [1.0, THEME["negative"]],
        ],
        aspect="auto",
        title="Correlation ผลตอบแทนรายวัน (10 ปี)",
    )
    st.plotly_chart(_apply_plotly_dark_theme(corr_fig), use_container_width=True)
    st.caption(
        "โครงสร้างที่ควรรู้: VOO (S&P 500) กับ QQQM (Nasdaq-100) มีหุ้นเทคใหญ่เป็นท็อปโฮลดิ้งร่วมกัน "
        "· SCHD เน้นปันผล ทับซ้อน VOO บางส่วน · XLV = healthcare ล้วน (ซึ่งอยู่ใน VOO ด้วย) "
        "· GLDM = ทองคำ ไม่ใช่หุ้น — ตัวเลข correlation ข้างบนคือพฤติกรรมจริงย้อนหลัง ไม่ใช่การพยากรณ์"
    )


def render_portfolio_page() -> None:
    """หน้า Portfolio: บันทึกธุรกรรมและสรุปพอร์ต."""
    st.header("Portfolio")
    st.caption("บันทึกการซื้อ ETF และดูกำไร/ขาดทุนปัจจุบัน")
    _render_pdf_export_panel(
        section_key="portfolio",
        prepare_label="Export Portfolio Report",
        download_label="ดาวน์โหลด PDF",
    )
    st.divider()
    config = load_config()
    primary_currency = str(config["display"]["currency"]).upper()
    default_fx_rate = float(config["display"]["default_fx_rate"])

    st.subheader("Add Transaction")
    with st.spinner("กำลังดึงอัตราแลกเปลี่ยน..."):
        today_fx_rate = get_today_fx_rate_thb()
    if not today_fx_rate or today_fx_rate <= 0:
        today_fx_rate = default_fx_rate
    with st.form("portfolio_buy_form", clear_on_submit=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            buy_date = st.date_input("Date")
            ticker = st.text_input("ETF (Ticker)", value="VOO").strip().upper()
        with col2:
            shares = st.number_input("จำนวนหุ้น (Shares)", min_value=0.0001, value=1.0, step=0.1, format="%.4f")
            price_usd = st.number_input("ราคาต่อหุ้น (USD)", min_value=0.0001, value=100.0, step=0.1, format="%.4f")
        with col3:
            amount_thb = st.number_input("จำนวนเงินที่จ่าย (บาท)", min_value=0.01, value=1000.0, step=10.0, format="%.2f")
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
        st.caption(f"เป็นการเทรดลำดับที่ {trade_number} ของเดือนนี้")
        st.caption(f"ค่าธรรมเนียมโดยประมาณ: {estimated_fee_thb:,.2f} บาท")

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
                st.success("บันทึกธุรกรรมแล้ว")
                st.rerun()
            except Exception as exc:
                st.error(f"บันทึกไม่สำเร็จ: {exc}")

    with st.expander("บันทึกปันผลรับ (Dividend)"):
        st.caption(
            f"กรอกยอด**สุทธิ**ที่เข้าบัญชีจริง (โบรกหักภาษี ณ ที่จ่าย {US_DIVIDEND_WITHHOLDING:.0%} แล้ว) "
            "— ระบบเก็บตามที่รับจริง ไม่กระทบต้นทุน/จำนวนหุ้น"
        )
        with st.form("portfolio_dividend_form", clear_on_submit=True):
            div_col1, div_col2, div_col3 = st.columns(3)
            with div_col1:
                dividend_date = st.date_input("วันที่รับ", key="dividend_date")
                dividend_ticker = st.selectbox("ETF", get_tickers(), key="dividend_ticker")
            with div_col2:
                dividend_usd = st.number_input(
                    "ปันผลสุทธิ (USD)", min_value=0.01, value=10.0, step=0.01, format="%.2f"
                )
            with div_col3:
                dividend_fx = st.number_input(
                    "FX Rate (THB/USD)",
                    min_value=0.0001,
                    value=float(today_fx_rate),
                    step=0.01,
                    format="%.4f",
                    key="dividend_fx",
                )
                dividend_note = st.text_input("หมายเหตุ", value="", key="dividend_note")
            dividend_submitted = st.form_submit_button("บันทึกปันผล", type="primary")
            if dividend_submitted:
                try:
                    add_dividend(
                        date=dividend_date.strftime("%Y-%m-%d"),
                        ticker=dividend_ticker,
                        amount_usd=float(dividend_usd),
                        fx_rate_thb=float(dividend_fx),
                        note=dividend_note,
                    )
                    st.success("บันทึกปันผลแล้ว")
                    st.rerun()
                except Exception as exc:
                    st.error(f"บันทึกไม่สำเร็จ: {exc}")

    st.divider()
    st.subheader("Portfolio Summary")
    with st.spinner(" ..."):
        holdings_df = get_portfolio_summary()
        total_summary = get_total_summary()

    # ราคาที่ดึงไม่ได้ = แจ้งชัด ไม่ใช่แสดงเป็นขาดทุน -100% (AUDIT.md C1)
    missing_prices = list(total_summary.get("missing_prices") or [])
    if missing_prices:
        st.error(
            f"ดึงราคาปัจจุบันไม่ได้: {', '.join(missing_prices)} — "
            "มูลค่าและกำไร/ขาดทุนด้านล่างคิดเฉพาะ ETF ที่มีราคาเท่านั้น"
        )

    m1, m2, m3, m4, m5 = st.columns(5)
    if primary_currency == "USD":
        invested = float(total_summary["total_invested_thb"]) / today_fx_rate
        current = float(total_summary["current_value_thb"]) / today_fx_rate
        pnl_value = float(total_summary["total_pnl_thb"]) / today_fx_rate
        m1.metric("เงินลงทุนรวม (USD)", f"{invested:,.2f}")
        m2.metric("มูลค่าปัจจุบัน (USD)", f"{current:,.2f}")
        m3.metric(
            "กำไร/ขาดทุน (USD)",
            f"{pnl_value:,.2f}",
            delta=f"{float(total_summary['total_return_pct']):.2f}%",
        )
    else:
        m1.metric("เงินลงทุนรวม (THB)", f"{float(total_summary['total_invested_thb']):,.2f}")
        m2.metric("มูลค่าปัจจุบัน (THB)", f"{float(total_summary['current_value_thb']):,.2f}")
        m3.metric(
            "กำไร/ขาดทุน (THB)",
            f"{float(total_summary['total_pnl_thb']):,.2f}",
            delta=f"{float(total_summary['total_return_pct']):.2f}%",
        )
    m4.metric("FX Rate วันนี้", f"{today_fx_rate:.2f} THB/USD")
    m5.metric("ค่าธรรมเนียมรวม (THB)", f"{float(total_summary['total_fee_thb']):,.2f}")

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
                "Price OK",
            ]
        ].copy()
        display_holdings["Price OK"] = display_holdings["Price OK"].map({True: "OK", False: "ดึงราคาไม่ได้"})
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
                },
                na_rep="N/A",
            ),
            use_container_width=True,
        )

        priced = holdings_df[holdings_df["Price OK"]]
        if not priced.empty:
            pie_fig = px.pie(
                priced,
                names="Ticker",
                values="Current Value (THB)",
                title="สัดส่วนพอร์ต (มูลค่าปัจจุบัน THB)",
                hole=0.35,
            )
            st.plotly_chart(_apply_plotly_dark_theme(pie_fig), use_container_width=True)

    _render_overlap_section()

    st.divider()
    st.subheader("ต้นทุนจริง & ภาษี (โดยประมาณ)")
    dca_budget_thb = float(config["dca"]["monthly_budget_thb"])
    month_costs = estimate_monthly_costs_thb(dca_budget_thb)
    cost_col1, cost_col2, cost_col3 = st.columns(3)
    cost_col1.metric("ค่าคอมสะสมที่จ่ายแล้ว", f"{float(total_summary['total_fee_thb']):,.2f} ฿")
    cost_col2.metric(
        f"ต้นทุนต่อรอบ DCA ({dca_budget_thb:,.0f} ฿)",
        f"~{month_costs['total_thb']:,.0f} ฿ ({month_costs['total_pct']:.2f}%)",
    )

    tax_rows: list[dict[str, object]] = []
    if not holdings_df.empty:
        with st.spinner("กำลังดึง dividend yield..."):
            yields = cached_dividend_yields(tuple(holdings_df["Ticker"].astype(str)))
        for _, holding in holdings_df[holdings_df["Price OK"]].iterrows():
            gross_yield = yields.get(str(holding["Ticker"]))
            if gross_yield is None or gross_yield <= 0:
                continue
            tax_rows.append(
                {
                    "Ticker": holding["Ticker"],
                    "Gross yield": gross_yield * 100.0,
                    "Net yield (หลังภาษี 15%)": net_dividend_yield(gross_yield) * 100.0,
                    "ภาษีถูกหัก/ปี (฿)": estimate_annual_dividend_tax_thb(
                        float(holding["Current Value (THB)"]), gross_yield
                    ),
                }
            )
    estimated_annual_tax = sum(float(r["ภาษีถูกหัก/ปี (฿)"]) for r in tax_rows)
    cost_col3.metric("ภาษีปันผลถูกหัก/ปี (ประมาณ)", f"~{estimated_annual_tax:,.0f} ฿")
    if tax_rows:
        st.dataframe(
            pd.DataFrame(tax_rows).style.format(
                {
                    "Gross yield": "{:.2f}%",
                    "Net yield (หลังภาษี 15%)": "{:.2f}%",
                    "ภาษีถูกหัก/ปี (฿)": "{:,.0f}",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )
    st.caption(
        f"ค่าคอม 0.15%/รายการ (ตามบัญชีจริง) · FX spread ~{fx_spread_pct():.2f}% เป็น**ประมาณการ** "
        "(ปรับได้ที่ config `costs.fx_spread_pct`) · "
        f"ปันผล US ถูกหักภาษี ณ ที่จ่าย {US_DIVIDEND_WITHHOLDING:.0%} ตามสนธิสัญญาภาษี US-ไทย "
        "(กระทบ SCHD มากสุด) · P&L ด้านบนเป็นกำไรจากราคาล้วน ไม่รวมปันผลรับ"
    )
    st.caption(
        "หมายเหตุภาษีไทย: การนำเงินกลับประเทศมีประเด็นภาษีเงินได้ (ปอ.161/2566 มีผลตั้งแต่ปีภาษี 2567) "
        "— ขึ้นกับสถานการณ์รายบุคคล ระบบไม่นำมาคิดในตัวเลข แจ้งไว้เพื่อความครบถ้วน"
    )

    thai_inflation = get_thai_inflation()
    if thai_inflation is not None:
        st.caption(
            f"เงินเฟ้อไทยล่าสุด ~{thai_inflation['inflation_pct']:.1f}%/ปี "
            f"(ปี {thai_inflation['year']}, {thai_inflation['source']}) — เป้าขั้นต่ำที่พอร์ต"
            "ต้องชนะต่อปีเพื่อรักษาอำนาจซื้อเงินบาท · ผลตอบแทนรวมด้านบนเป็นตัวเลขสะสม"
            "ตั้งแต่เริ่มพอร์ต จึงเทียบตรง ๆ กับเงินเฟ้อรายปีไม่ได้ (real return เต็มรูป"
            "จะมากับ XIRR ในหัวข้อ benchmark)"
        )
    else:
        st.caption("ไม่ทราบเงินเฟ้อไทยขณะนี้ (ดึงข้อมูลไม่สำเร็จ) — ไม่แสดงตัวเลขประมาณแทน")

    dividend_summary = get_dividend_summary()
    if int(dividend_summary["count"]) > 0:
        st.subheader("ปันผลรับจริง (สุทธิหลังภาษี)")
        _, withheld_tax_thb = gross_up_net_dividend(float(dividend_summary["total_thb"]))
        income_col1, income_col2, income_col3 = st.columns(3)
        income_col1.metric("รับสุทธิสะสม", f"{float(dividend_summary['total_thb']):,.2f} ฿")
        income_col2.metric("ปีนี้", f"{float(dividend_summary['this_year_thb']):,.2f} ฿")
        income_col3.metric("ภาษีที่ถูกหักไปแล้ว (ประมาณ)", f"~{withheld_tax_thb:,.2f} ฿")

        with st.expander("จำลอง DRIP — ถ้านำปันผลทุกงวดซื้อหุ้นเพิ่มทันที ณ วันรับ"):
            try:
                drip_prices = cached_prices(get_tickers(), years=10)
            except PriceDataUnavailableError as exc:
                drip_prices = pd.DataFrame()
                st.error(f"ดึงราคาไม่สำเร็จ — จำลอง DRIP ไม่ได้: {exc}")
            for drip_ticker in sorted(dividend_summary["by_ticker_thb"]):
                if drip_ticker not in drip_prices.columns:
                    st.warning(f"{drip_ticker}: ไม่มีข้อมูลราคา — ข้ามการจำลอง")
                    continue
                try:
                    drip_result = simulate_drip(
                        get_dividends(drip_ticker), drip_prices[drip_ticker].dropna()
                    )
                except ValueError as exc:
                    st.warning(f"{drip_ticker}: {exc}")
                    continue
                skipped_note = (
                    f" · ข้าม {drip_result['skipped']} งวด (ไม่มีราคา ณ วันรับ)"
                    if drip_result["skipped"]
                    else ""
                )
                st.markdown(
                    f"**{drip_ticker}** — ปันผลสุทธิ {drip_result['cash_usd']:,.2f} USD: "
                    f"ถ้า DRIP จะได้เพิ่ม {drip_result['extra_shares']:.4f} หุ้น "
                    f"มูลค่าปัจจุบัน {drip_result['drip_value_usd']:,.2f} USD "
                    f"({drip_result['advantage_usd']:+,.2f} USD เทียบถือเงินสด){skipped_note}"
                )
            st.caption("จำลองจากราคาจริง ณ วันรับปันผล — สถิติเปรียบเทียบ ไม่ใช่คำแนะนำ")

    st.divider()
    st.subheader("Transaction History")
    with st.spinner(" ..."):
        all_transactions = get_transactions()
    if all_transactions.empty:
        st.info("No transactions found.")
        return

    ticker_options = ["ทั้งหมด"] + sorted(all_transactions["ticker"].dropna().astype(str).str.upper().unique().tolist())
    selected_ticker = st.selectbox("กรอง ETF", ticker_options, index=0)
    filtered_transactions = all_transactions.copy()
    if selected_ticker != "ทั้งหมด":
        filtered_transactions = get_transactions(selected_ticker)

    filtered_transactions["tx_type"] = filtered_transactions["tx_type"].map(
        {TX_DIVIDEND: "ปันผล"}
    ).fillna("ซื้อ")
    filtered_transactions = filtered_transactions.rename(
        columns={
            "date": "Date",
            "ticker": "Ticker",
            "tx_type": "ประเภท",
            "shares": "Shares",
            "price_usd": "Price (USD)",
            "fx_rate_thb": "FX Rate (THB/USD)",
            "amount_thb": "Amount (THB)",
            "fee_thb": "ค่าธรรมเนียม (บาท)",
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
                "ค่าธรรมเนียม (บาท)": "{:,.2f}",
            }
        ),
        use_container_width=True,
    )


# --- Scorecard (Roadmap B1): รวมคำตอบ "เดือนนี้ซื้ออะไร เท่าไร เพราะอะไร" ไว้หน้าเดียว ---
# ตัวเลขทุกตัวมาจาก build_etf_scores / calculate_allocation — หน้านี้ห้ามคำนวณเกณฑ์เอง
# (คะแนนของ ETF ตัวเดียวกันต้องเท่ากันทุกหน้าจอ — AUDIT.md C2)

THAI_MONTHS = [
    "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
]

# สี 4 องค์ประกอบคะแนน (โทน GitHub dark เดียวกับ THEME)
_SCORE_PARTS = [
    ("Trend", "trend_score", TREND_MAX, THEME["accent"]),
    ("Timing", "timing_score", TIMING_MAX, "#A371F7"),
    ("Momentum", "momentum_score", MOMENTUM_MAX, "#D29922"),
    ("Dividend", "dividend_score", DIVIDEND_MAX, THEME["text_secondary"]),
]


@st.cache_data(ttl=3600, show_spinner=False)
def cached_etf_scores(tickers: tuple[str, ...]) -> list[dict]:
    """คะแนนกลางต่อ ETF (cache 1 ชม.) — ชุดเดียวกับ AI Advisor/หน้า DCF/cron."""
    return build_etf_scores(list(tickers))


def _chip_html(text: str, color: str) -> str:
    return (
        f'<span style="display:inline-block;padding:2px 10px;margin:2px 6px 2px 0;'
        f'border:1px solid {color};border-radius:12px;font-size:12px;color:{color};">{text}</span>'
    )


def _central_signal_color(central: str) -> str:
    label = signal_rules.to_technical_label(central)
    if label == "bullish":
        return THEME["positive"]
    if label == "bearish":
        return THEME["negative"]
    return THEME["text_secondary"]


def _score_reason_chips(row: dict) -> str:
    """chips เหตุผลจากตัวเลขที่โมเดลคืนมา — แสดงข้อเท็จจริง ไม่ตั้งเกณฑ์ใหม่ (เกณฑ์อยู่ใน signal_rules)."""
    chips: list[str] = []
    central = str(row.get("technical_signal", ""))
    chips.append(_chip_html(str(row.get("technical_signal_th", central)), _central_signal_color(central)))

    price, ma50, ma200, rsi = row.get("price"), row.get("ma50"), row.get("ma200"), row.get("rsi")
    if price is not None and ma200 is not None:
        if float(price) >= float(ma200):
            chips.append(_chip_html("เหนือ MA200 ✓", THEME["positive"]))
        else:
            chips.append(_chip_html("ใต้ MA200 ✗", THEME["negative"]))
    if price is not None and ma50 is not None:
        if float(price) >= float(ma50):
            chips.append(_chip_html("เหนือ MA50 ✓", THEME["positive"]))
        else:
            chips.append(_chip_html("ใต้ MA50", THEME["text_secondary"]))
    if rsi is not None:
        zone = signal_rules.rsi_zone(rsi)
        zone_th = {"oversold": "ย่อลึก", "neutral": "โซนกลาง", "overbought": "ร้อนแรง"}.get(zone, zone)
        zone_color = {"oversold": THEME["accent"], "overbought": THEME["negative"]}.get(
            zone, THEME["text_secondary"]
        )
        chips.append(_chip_html(f"RSI {float(rsi):.0f} · {zone_th}", zone_color))
    for key, label in (("return_1m_pct", "1 เดือน"), ("return_3m_pct", "3 เดือน")):
        value = row.get(key)
        if value is None:
            continue
        v = float(value)
        v_color = THEME["positive"] if v > 0 else THEME["negative"] if v < 0 else THEME["text_secondary"]
        chips.append(_chip_html(f"{label} {v:+.1f}%", v_color))
    if not row.get("dividend_available", False):
        chips.append(_chip_html("ปันผล: ไม่มีข้อมูล (ตัดจากคะแนนเต็ม)", THEME["text_secondary"]))
    elif int(row.get("dividend_score") or 0) > 0:
        chips.append(_chip_html(f"ปันผลหนุน +{int(row['dividend_score'])}", THEME["positive"]))
    else:
        chips.append(_chip_html("ปันผลต่ำ/ไม่มี", THEME["text_secondary"]))
    return "".join(chips)


def _render_verdict_cards(allocation: dict, budget_thb: float) -> None:
    """การ์ดเงินที่จะซื้อต่อ ETF เดือนนี้ — จำนวนเงิน/ตัวคูณมาจาก calculate_allocation ตรง ๆ."""
    cards = st.columns(len(allocation))
    for col, (ticker, item) in zip(cards, allocation.items()):
        amount = float(item.get("amount_thb") or 0)
        tilt = float(item.get("tilt") or 1.0)
        target_pct = float(item.get("target_percent") or 0)
        actual_pct = float(item.get("percent") or 0)
        with col:
            st.markdown(
                f'<div style="border:1px solid {THEME["border"]};border-radius:8px;'
                f'padding:12px 8px;background:{THEME["card_bg"]};text-align:center;">'
                f'<div style="font-size:14px;color:{THEME["text_secondary"]};">{ticker}</div>'
                f'<div style="font-size:22px;font-weight:600;color:{THEME["text_primary"]};">'
                f'{amount:,.0f} ฿</div>'
                f'<div style="font-size:12px;color:{THEME["text_secondary"]};">'
                f'เป้า {target_pct:.0f}% → จริง {actual_pct:.0f}% (×{tilt:.2f})</div>'
                f"</div>",
                unsafe_allow_html=True,
            )
    unallocated = float(budget_thb) - sum(float(i.get("amount_thb") or 0) for i in allocation.values())
    if unallocated > 0:
        st.caption(f"เศษจากการปัดหลักร้อย {unallocated:,.0f} บาท — สมทบเดือนถัดไป")


def _render_score_audit_trail(row: dict, alloc_item: dict | None) -> None:
    """audit trail "ทำไมได้เท่านี้" (Roadmap ข้อ 9) — โชว์เลขที่โมเดลคืนมาทุกชั้น ไม่คำนวณใหม่."""
    dividend_max_text = str(DIVIDEND_MAX) if row.get("dividend_available") else "ตัดออก (ไม่มีข้อมูลปันผล)"
    st.markdown(
        f"**ชั้น 1 — คะแนนดิบ** (จาก `score_from_prices`)  \n"
        f"Trend {row.get('trend_score')}/{TREND_MAX} · Timing {row.get('timing_score')}/{TIMING_MAX} · "
        f"Momentum {row.get('momentum_score')}/{MOMENTUM_MAX} · Dividend {row.get('dividend_score')}/{dividend_max_text}  \n"
        f"รวม {row.get('total_score')}/{row.get('max_score')} = **{float(row.get('total_pct') or 0):.1f}%**"
    )
    if alloc_item:
        st.markdown(
            f"**ชั้น 2 — ตัวคูณจากคะแนน** (bounded {TILT_MIN:.1f}–{TILT_MAX:.1f})  \n"
            f"tilt = {TILT_MIN:.1f} + ({TILT_MAX:.1f}−{TILT_MIN:.1f}) × {float(row.get('total_pct') or 0):.1f}/100 "
            f"= **×{float(alloc_item.get('tilt') or 1):.2f}**  \n"
            f"**ชั้น 3 — เงินจริง**  \n"
            f"เป้า {float(alloc_item.get('target_percent') or 0):.1f}% × tilt → normalize รวมกับตัวอื่น "
            f"= จริง {float(alloc_item.get('percent') or 0):.1f}% = **{float(alloc_item.get('amount_thb') or 0):,.0f} ฿** "
            "(ปัดหลักร้อย เศษแจกแบบ largest-remainder)"
        )
    else:
        st.markdown("ไม่อยู่ในแผนจัดสรรเดือนนี้ (ไม่มีน้ำหนักเป้าหมาย หรือข้อมูลไม่พร้อม)")


def _render_drift_advisory() -> None:
    """เทียบพอร์ตจริงกับเป้าหมาย (Roadmap ข้อ 7) — advisory เท่านั้น ไม่แก้เลขจัดสรร.

    ใช้เกณฑ์ drift 5% เดียวกับ rebalance_service เพื่อไม่สร้างนิยามใหม่
    """
    try:
        holdings = get_portfolio_summary()
    except Exception as exc:
        st.caption(f"อ่านพอร์ตจริงไม่ได้ ({exc}) — ข้ามคำแนะนำ drift")
        return
    if holdings.empty:
        return  # ยังไม่มีพอร์ตจริง — ไม่มีอะไรให้เทียบ
    priced = holdings[holdings["Price OK"]]
    total_value = float(priced["Current Value (THB)"].sum()) if not priced.empty else 0.0
    if total_value <= 0:
        return

    targets = get_target_weights([str(t) for t in priced["Ticker"]])
    drifts: list[tuple[str, float]] = []
    for _, holding in priced.iterrows():
        ticker = str(holding["Ticker"])
        actual_pct = float(holding["Current Value (THB)"]) / total_value * 100.0
        target_pct = float(targets.get(ticker, 0.0)) * 100.0
        drifts.append((ticker, actual_pct - target_pct))

    DRIFT_THRESHOLD_PCT = 5.0
    off_target = [(t, d) for t, d in drifts if abs(d) >= DRIFT_THRESHOLD_PCT]
    if not off_target:
        st.caption(f"พอร์ตจริงใกล้เป้าหมายทุกตัว (drift < {DRIFT_THRESHOLD_PCT:.0f}%) — DCA ตามแผนได้เลย")
        return
    drift_text = ", ".join(f"{t} {d:+.1f}% จากเป้า" for t, d in sorted(off_target, key=lambda x: -abs(x[1])))
    st.info(
        f"พอร์ตจริงตอนนี้เอียง: {drift_text} — การซื้อตามแผนเดือนนี้ช่วยดึงตัวที่ต่ำกว่าเป้ากลับ"
        "โดยไม่ต้องขาย (ข้อมูลจาก ledger ในเครื่อง · คำอธิบายเท่านั้น ไม่เปลี่ยนตัวเลขจัดสรร)"
    )


def render_scorecard_page() -> None:
    """หน้า Scorecard (Roadmap B1): เรียง 5 ETF + การ์ด "คำตัดสินเดือนนี้"."""
    st.header("Scorecard")
    now = datetime.now()
    st.caption(
        f"คำตัดสินเดือน{THAI_MONTHS[now.month - 1]} {now.year + 543}: ซื้ออะไร เท่าไร เพราะอะไร "
        "— คะแนนคำนวณในโค้ด (ชุดเดียวกับ AI Advisor/DCF) ไม่ใช่ AI"
    )

    config = load_config()
    tickers = get_tickers()
    if not tickers:
        st.warning("ยังไม่มี ETF ในระบบ — เพิ่มได้ที่หน้า Settings")
        return

    budget_col, refresh_col = st.columns([2.6, 1])
    with budget_col:
        budget_thb = st.number_input(
            "งบ DCA เดือนนี้ (บาท)",
            min_value=500.0,
            value=float(config["dca"]["monthly_budget_thb"]),
            step=500.0,
            format="%.0f",
            key="scorecard_budget",
        )
    with refresh_col:
        st.write("")
        st.write("")
        if st.button("คำนวณคะแนนใหม่", key="scorecard_refresh"):
            cached_etf_scores.clear()

    with st.spinner("กำลังคำนวณคะแนน ETF..."):
        scores = cached_etf_scores(tuple(tickers))

    ok_rows = [r for r in scores if r.get("data_ok", True) and r.get("total_pct") is not None]
    no_data_rows = [r for r in scores if r not in ok_rows]

    if not ok_rows:
        # ข้อมูลพังทั้งหมด = บอกตรง ๆ ห้ามแสดงเลขปลอม (AUDIT.md C1)
        st.error("ดึงข้อมูลราคาไม่สำเร็จทุกตัว — ไม่มีคะแนนให้แสดง")
        for row in no_data_rows:
            st.caption(f"• {row.get('ticker')}: {row.get('error', 'ไม่ทราบสาเหตุ')}")
        return

    scores_by_ticker = {str(r["ticker"]): r for r in scores if r.get("ticker")}
    allocation = calculate_allocation(scores_by_ticker, float(budget_thb))

    # --- การ์ดคำตัดสินเดือนนี้ ---
    st.subheader("คำตัดสินเดือนนี้")
    st.caption(
        f"ซื้อทุกตัวที่มีข้อมูลตามแผน DCA — คะแนนแค่เอียงน้ำหนักจากเป้า ({TILT_MIN:.1f}–{TILT_MAX:.1f} เท่า) "
        "ไม่มีการเลือกตัวเดียวหรือตัดตัวไหนออก"
    )
    if no_data_rows:
        missing = ", ".join(str(r.get("ticker")) for r in no_data_rows)
        st.warning(f"ไม่มีข้อมูล: {missing} — ไม่ถูกนำมาคิดคะแนน/จัดสรร งบส่วนนั้นกระจายให้ตัวที่เหลือ")

    if allocation:
        _render_verdict_cards(allocation, float(budget_thb))

        table_col, donut_col = st.columns([1.25, 1])
        with table_col:
            alloc_df = pd.DataFrame(
                [
                    {
                        "Ticker": ticker,
                        "Amount (THB)": item.get("amount_thb", 0),
                        "จัดสรรจริง": item.get("percent", 0),
                        "เป้าหมาย": item.get("target_percent", 0),
                        "ตัวคูณ": item.get("tilt"),
                        "Score %": item.get("score"),
                        "Signal": item.get("group", ""),
                    }
                    for ticker, item in allocation.items()
                ]
            )
            st.dataframe(
                alloc_df.style.format(
                    {
                        "Amount (THB)": "{:,.0f}",
                        "จัดสรรจริง": "{:.0f}%",
                        "เป้าหมาย": "{:.0f}%",
                        "ตัวคูณ": "{:.2f}×",
                        "Score %": "{:.1f}",
                    },
                    na_rep="N/A",
                ),
                use_container_width=True,
                hide_index=True,
            )
        with donut_col:
            donut_df = pd.DataFrame(
                [
                    {"label": f"{t} ×{float(i.get('tilt') or 1):.2f}", "amount": i.get("amount_thb", 0)}
                    for t, i in allocation.items()
                ]
            )
            donut = px.pie(
                donut_df,
                names="label",
                values="amount",
                title="น้ำหนักเดือนนี้ (บาท)",
                hole=0.45,
            )
            st.plotly_chart(_apply_plotly_dark_theme(donut), use_container_width=True)
            st.caption("× = ตัวคูณจากคะแนน — 1.00 คือตามเป้าพอดี, สูง/ต่ำกว่าคือซื้อมาก/น้อยกว่าเป้า")
    else:
        st.warning("จัดสรรไม่ได้ — ไม่มี ETF ที่ข้อมูลพร้อม หรืองบเป็นศูนย์")

    _render_drift_advisory()

    # --- stacked bar: คะแนนรวมแยก 4 องค์ประกอบ ---
    st.subheader("คะแนน 0-100 แยกองค์ประกอบ")
    ranked = sorted(ok_rows, key=lambda r: float(r.get("total_pct") or 0), reverse=True)
    bar_fig = go.Figure()
    for label, key, part_max, color in _SCORE_PARTS:
        bar_fig.add_trace(
            go.Bar(
                y=[str(r["ticker"]) for r in ranked],
                x=[int(r.get(key) or 0) for r in ranked],
                name=f"{label} (เต็ม {part_max})",
                orientation="h",
                marker_color=color,
            )
        )
    for r in ranked:
        bar_fig.add_annotation(
            y=str(r["ticker"]),
            x=int(r.get("total_score") or 0),
            text=f" {float(r.get('total_pct') or 0):.0f}% ({r.get('total_score')}/{r.get('max_score')})",
            showarrow=False,
            xanchor="left",
            font={"size": 12, "color": THEME["text_primary"]},
        )
    bar_fig.update_layout(
        barmode="stack",
        xaxis={"range": [0, 112], "title": "คะแนนดิบ"},
        yaxis={
            "categoryorder": "array",
            # เรียงคะแนนมากสุดไว้บนสุด (แกน y ของ plotly ไล่จากล่างขึ้นบน)
            "categoryarray": [str(r["ticker"]) for r in reversed(ranked)],
        },
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02},
        height=320,
    )
    st.plotly_chart(_apply_plotly_dark_theme(bar_fig), use_container_width=True)
    if any(not r.get("dividend_available", False) for r in ranked):
        st.caption(
            "ตัวที่ไม่มีข้อมูลปันผล คะแนนเต็มคือ 90 (ตัดหมวด Dividend ออก) — % คิดจากคะแนนเต็มจริงของตัวนั้น"
        )

    # --- เจาะราย ETF พร้อม chips เหตุผล ---
    st.subheader("เจาะราย ETF (เรียงตามคะแนน)")
    for row in ranked:
        with st.container(border=True):
            c1, c2, c3 = st.columns([0.9, 1.0, 3.3])
            c1.markdown(f"**{row['ticker']}**  \n${float(row.get('price') or 0):,.2f}")
            c2.markdown(
                f"**{float(row.get('total_pct') or 0):.1f}** / 100  \n{row.get('signal', '')}"
            )
            c3.markdown(_score_reason_chips(row), unsafe_allow_html=True)
            with st.expander("ทำไมได้เท่านี้ (audit trail ทุกชั้น)"):
                _render_score_audit_trail(row, allocation.get(str(row.get("ticker"))))
    for row in no_data_rows:
        with st.container(border=True):
            st.markdown(
                f"**{row.get('ticker')}** — ไม่มีข้อมูล ({row.get('error', 'ไม่ทราบสาเหตุ')}) "
                "· แสดงเป็น NO DATA ไม่ใช่คะแนน 0"
            )

    st.caption(
        "ตัวเลขทุกตัวมาจาก `build_etf_scores` / `calculate_allocation` (โมเดลกลางตัวเดียวกับ "
        "AI Advisor และหน้า DCF) — รายละเอียด DCF/heatmap ดูได้ที่หน้า DCF Analysis"
    )


def render_dashboard() -> None:
    """เรนเดอร์ dashboard หลักของ Vaultis."""
    try:
        st.set_page_config(page_title="Vaultis ETF Analyzer", layout="wide")
        _inject_premium_theme()
        st.title("Vaultis Premium Financial Dashboard")
        tickers = get_tickers()
        st.caption(f"Dark & Luxury Finance view | ETF Universe: {', '.join(tickers)}")

        if st.button("Refresh Data"):
            st.cache_data.clear()
            st.success("ล้างแคชแล้ว กำลังโหลดข้อมูลใหม่...")
            st.rerun()

        try:
            with st.spinner("กำลังโหลดข้อมูลราคา..."):
                prices = cached_prices(tickers, years=10)
        except PriceDataUnavailableError as exc:
            # ข้อมูลราคาพัง = หยุดทันที ห้ามแสดงหน้าวิเคราะห์จากข้อมูลว่าง (AUDIT.md C1)
            st.error(f"ดึงข้อมูลราคา ETF ไม่สำเร็จ: {exc}")
            st.info("ลองกด Refresh Data อีกครั้งในอีกสักครู่ (yfinance อาจจำกัดการเรียกชั่วคราว)")
            return

        # สัดส่วนเป้าหมายจากแหล่งเดียว (portfolio/targets.py) — ตรงกับที่ DCA/rebalance ใช้
        default_weights = get_target_weights(tickers)
        config = load_config()

        default_page = str(config["display"]["default_page"])
        _render_custom_sidebar(default_page)
        page = st.session_state.get("page", "Overview")

        if page == "Overview":
            pass
        elif page == "Scorecard":
            render_scorecard_page()
            return
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
            download_label="ดาวน์โหลด PDF",
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
            st.caption("*QQQM เริ่มซื้อขายปี 2020 — ช่วงก่อนหน้าจึงไม่มีข้อมูล")

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
            st.warning("ต้องมี ETF อย่างน้อย 2 ตัวจึงจะคำนวณ correlation ได้")
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

        st.markdown("**อ่านค่าจาก Correlation Heatmap**")
        st.markdown(
            f"-   correlation  : **{max_pair[0]} - {max_pair[1]} ({max_value:.2f})**    "
        )
        st.markdown(
            f"-   correlation  : **{min_pair[0]} - {min_pair[1]} ({min_value:.2f})**    "
        )
        st.markdown("- correlation ต่ำ = กระจายความเสี่ยงได้ดีกว่า")

        st.info("ดู Backtest และ DCA Simulator ได้จากเมนูด้านซ้าย")
    except Exception as exc:
        st.error(f"เกิดข้อผิดพลาดใน dashboard: {exc}")


if __name__ == "__main__":
    render_dashboard()
