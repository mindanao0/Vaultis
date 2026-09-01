# -*- coding: utf-8 -*-
"""Streamlit dashboard สำหรับวิเคราะห์และติดตามพอร์ต ETF ระยะยาว."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import faulthandler
import json
import math
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

# dashboard เคยตายทั้ง process ด้วย SIGSEGV ระหว่างสลับหน้า (AUDIT.md M20) — เกิด 2 ใน 5 รอบ
# ตอนตรวจ 2026-07-28 แล้วหลังจากนั้นเรียกซ้ำ 96 ครั้งก็ไม่เกิดอีก จึงยังหาต้นตอไม่ได้
# (pyarrow 25.0.0 ซึ่งเป็นผู้ต้องสงสัยในคอมเมนต์ requirements.txt ผ่าน stress test 1,500 รอบ)
# เปิด faulthandler ไว้เพื่อให้ครั้งหน้ามี C-level traceback ให้อ่าน แทนที่ process จะหายเงียบ
faulthandler.enable()

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

from analysis.correlation import (
    ROLLING_WINDOW_DAYS,
    calculate_correlation_matrix,
    rolling_correlation_summary,
)
from analysis.ai_advisor import ai_suggest_alerts, get_monthly_advice
from analysis.llm import ANTHROPIC_MODEL
from analysis.financial_model import (
    ALLOCATION_UNIT_THB,
    DIVIDEND_MAX,
    EXCLUDED_NO_DATA,
    EXCLUDED_ROUNDED_TO_ZERO,
    EXCLUDED_ZERO_TARGET,
    EXPENSE_MAX,
    MOMENTUM_MAX,
    RELATIVE_STRENGTH_MAX,
    TILT_MAX,
    TILT_MIN,
    TIMING_MAX,
    TREND_MAX,
    VALUATION_MAX,
    VOLATILITY_MAX,
    AllocationPlan,
    _dividend_yield,
    build_etf_scores,
    calculate_allocation_with_status,
    run_full_analysis,
)
from analysis.news_fetcher import (
    KIND_NEWS,
    KIND_SOCIAL,
    STATUS_ERROR,
    STATUS_OFF,
    get_news_with_status,
)
from analysis.ta_compat import ta
from analysis.returns import (
    RETURNS_HISTORY_YEARS,
    calculate_period_returns,
    monthly_seasonality,
    real_bars,
)
from analysis.macro import get_thai_inflation
from analysis.risk import (
    atr_stats,
    calculate_risk_metrics,
    drawdown_episodes,
    mix_vs_benchmark_test,
    underwater_series,
)
from analysis.trend_channel import fit_trend_channel
from alerts.notifier import test_alert
from alerts.price_alert import (
    AlertStoreUnavailable,
    add_alert,
    add_or_update_alert,
    check_alerts,
    check_result_contract_error,
    delete_alert,
    get_active_alerts_with_distance,
    get_current_prices,
    list_alerts,
)
from data.fetcher import PriceDataUnavailableError, fetch_adjusted_close_data
from db.sentiment_models import get_latest_sentiment_summaries
from portfolio.backtest import run_portfolio_backtest
from portfolio.lookthrough import look_through, overlap_pairs, weighted_ratios
from portfolio.dca import COVERAGE_ATTR, describe_coverage, simulate_monthly_dca
from portfolio.targets import (
    RISK_PROFILES,
    InvalidTargetWeights,
    NoTargetForSubset,
    TargetWeightsError,
    get_risk_profile,
    get_target_weights,
    get_target_weights_with_status,
)
from portfolio.costs import (
    US_DIVIDEND_WITHHOLDING,
    estimate_annual_dividend_tax_thb,
    estimate_monthly_costs_thb,
    fx_spread_pct,
    gross_up_net_dividend,
    net_dividend_yield,
)
from portfolio.benchmark import shadow_benchmark, xirr
from portfolio.cashflow_rebalance import rebalance_with_new_money
from portfolio.drip import simulate_drip
from portfolio.fees import DIME_FEE_RATE
from utils.fx import (
    DEFAULT_FALLBACK_RATE as FX_DEFAULT_FALLBACK_RATE,
    MAX_RATE as FX_MAX_RATE,
    MIN_RATE as FX_MIN_RATE,
    FxRateUnavailable,
    get_usdthb,
)
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
def cached_returns_prices(tickers: list[str]) -> pd.DataFrame:
    """ราคายาวพอสำหรับหน้าต่าง 10Y ของตาราง Returns — **แยกจากเฟรมหลักโดยตั้งใจ**.

    หน้าต่างผลตอบแทนวัดเป็น **แท่ง** (10Y = 2,520 + 1 แท่งอ้างอิง) แต่ผู้เรียกขอข้อมูลเป็น
    **ปีปฏิทิน** — ขอ 10 ปีได้ ~2,511 แท่ง สั้นกว่าที่ต้องใช้ ⇒ แถว 10Y เป็น N/A **เสมอ**
    ทั้งบนจอและใน PDF ทั้งที่ backend คำนวณได้ (FIX_PLAN ข้อ 2.8)

    **ห้ามแก้ด้วยการขยายเฟรมหลัก** — risk/correlation/backtest อ่านเฟรมนั้น การขยายช่วง
    จะเปลี่ยนตัวเลขความเสี่ยงและ correlation เงียบ ๆ ทั้งที่ไม่มีใครขอให้เปลี่ยน
    (กติกาเดียวกับ ``etf_service._prices_df_for_returns`` ที่แยก cache ไว้ด้วยเหตุผลนี้)
    """
    return cached_prices(list(tickers), years=RETURNS_HISTORY_YEARS)


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

BANGKOK_TZ = ZoneInfo("Asia/Bangkok")

# รายการหน้าจอของแอป — **แหล่งเดียว** ทั้งของแถบข้าง ปุ่มนำทาง และตัวเลือก
# "หน้าเริ่มต้น" ในหน้า Settings (AUDIT_ROUND2_2026-08-07)
#
# เดิมหน้า Settings มีลิสต์ ``page_options`` เขียนมือของตัวเองอีกชุด และ
# ``_render_custom_sidebar()`` ฮาร์ดโค้ดปุ่มไว้อีก 13 ปุ่ม รวมเป็นสามชุดที่ต้องตรงกันเอง
# พอเพิ่มหน้า Correlation กับ News ลิสต์ในหน้า Settings ก็ตกหล่นไปสองหน้า ⇒ ผู้ใช้ที่ตั้ง
# ``display.default_page`` เป็นหน้าเหล่านั้นไว้ จะโดน selectbox เด้งกลับไป "Overview"
# แล้วปุ่ม "บันทึก Settings" เขียนทับค่าเดิมลง config.json เงียบ ๆ แม้ตั้งใจมาแก้แค่งบ DCA
NAV_GROUPS = [
    ("Main", ["Overview", "Scorecard", "Portfolio"]),
    ("Analysis", ["Backtest", "DCA Simulator", "Technical Signals", "Correlation", "DCF Analysis"]),
    ("AI & Alerts", ["AI Advisor", "Macro", "News", "Price Alerts"]),
    ("System", ["Settings"]),
]

NAV_ITEMS = [item for _, group_items in NAV_GROUPS for item in group_items]


def _nav_button_key(page: str) -> str:
    """คีย์ปุ่มนำทางของหน้า — คงรูปแบบเดิม ``nav_dca_simulator`` ไว้ทุกตัว."""
    return "nav_" + page.lower().replace(" ", "_").replace("&", "and")


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
        /* ป้าย "VAULTIS" กับป้ายหมวดต้องชนะกฎรีเซ็ต p ของ sidebar ด้านบน
           (AUDIT_ROUND2_2026-08-07): กฎนั้นคือ
           [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p → specificity (0,2,1)
           ส่วน .logo / .nav-group เดิมเป็น (0,1,0) แม้จะใส่ !important เท่ากันก็แพ้
           ⇒ padding/margin ที่ตั้งไว้ถูกล้างเป็น 0 ทั้งคู่ พอ stVerticalBlock ตั้ง gap:0rem
           ป้ายหมวดจึงไปทับตัวอักษรของปุ่มบรรทัดถัดไป ("VAULTIS" ทับ "MAIN",
           "ANALYSIS" ทับ "Backtest") · แก้ด้วยการเพิ่ม specificity ให้ชนะจริง
           ห้ามกลับไปใช้ selector คลาสเดี่ยว และห้ามใช้ negative margin/absolute
           มาดันตำแหน่งแทน เพราะจะกลับไปซ้อนกับ container ของ st.button อีก */
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p.logo {{
            font-size: 16px !important;
            font-weight: 600 !important;
            color: #E6EDF3 !important;
            line-height: 1.4 !important;
            padding: 0 !important;
            margin: 8px 0 16px 0 !important;
        }}
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p.nav-group {{
            font-size: 10px !important;
            color: #7D8590 !important;
            letter-spacing: 0.08em !important;
            text-transform: uppercase !important;
            line-height: 1.4 !important;
            padding: 0 !important;
            margin: 14px 0 6px 0 !important;
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
    """ทาสีธีมมืดให้กราฟ — **ห้ามสร้าง ``layout.title`` ให้กราฟที่ไม่ได้ตั้งชื่อ**.

    เดิมบรรทัดนี้เป็น ``title_font=dict(...)`` เดี่ยว ๆ ซึ่งทำให้ plotly สร้าง
    ``layout.title`` ที่มีแต่คีย์ ``font`` ไม่มี ``text`` แล้ว plotly.js เรนเดอร์
    ``text === undefined`` ออกมาเป็นคำว่า "undefined" ตัวหนาเหนือกราฟ — นับได้ 10 กราฟ
    ใน 6 หน้า รวมหน้า Overview และ Technical Signals ที่เป็นหน้าตัดสินใจลงเงินจริง
    (AUDIT_ROUND2_2026-08-07) · ตัวเลขในกราฟไม่ผิด แต่หน้าจอที่ดูเหมือนพังคือหน้าจอ
    ที่เชื่อไม่ได้ จึงตั้งฟอนต์ของหัวกราฟ **ก็ต่อเมื่อกราฟนั้นมีชื่อของตัวเองอยู่แล้ว**
    """
    fig.update_layout(
        plot_bgcolor=THEME["main_bg"],
        paper_bgcolor=THEME["main_bg"],
        font=dict(color=THEME["text_primary"], family="Inter"),
        legend=dict(bgcolor="rgba(0,0,0,0)"),
    )
    title_text = getattr(getattr(fig.layout, "title", None), "text", None)
    if title_text:
        # ส่ง text เดิมกลับไปด้วยเสมอ — เซ็ต font เดี่ยว ๆ คือต้นเหตุของ "undefined"
        fig.update_layout(
            title=dict(text=title_text, font=dict(color=THEME["text_primary"], family="Inter"))
        )
    fig.update_xaxes(gridcolor=THEME["grid"], zerolinecolor=THEME["grid"])
    fig.update_yaxes(gridcolor=THEME["grid"], zerolinecolor=THEME["grid"])
    return fig


def _render_custom_sidebar(default_page: str) -> str:
    """แถบนำทาง — สร้างปุ่มจาก :data:`NAV_GROUPS` ห้ามฮาร์ดโค้ดรายชื่อหน้าซ้ำอีกชุด.

    เดิมปุ่มทั้ง 13 ปุ่มถูกเขียนมือ ทำให้รายชื่อหน้าจอในโปรเจกต์มีสามชุด (NAV_GROUPS,
    ปุ่มในแถบข้าง, ``page_options`` ในหน้า Settings) แล้วชุดหลังก็ drift จนผู้ใช้ตั้ง
    หน้า Correlation/News เป็นหน้าเริ่มต้นไม่ได้ (AUDIT_ROUND2_2026-08-07) — วนลูป
    จากแหล่งเดียวแล้ว drift แบบนั้นเกิดซ้ำไม่ได้อีก
    """
    if "page" not in st.session_state:
        st.session_state["page"] = default_page if default_page in NAV_ITEMS else "Overview"

    with st.sidebar:
        st.markdown('<p class="logo">VAULTIS</p>', unsafe_allow_html=True)

        for group_label, group_items in NAV_GROUPS:
            st.markdown(
                f'<p class="nav-group">{group_label.upper()}</p>', unsafe_allow_html=True
            )
            for item in group_items:
                if st.button(item, key=_nav_button_key(item), use_container_width=True):
                    st.session_state["page"] = item

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


def _browser_can_resolve(host: str) -> bool:
    """เบราว์เซอร์บนเครื่องผู้ใช้มีสิทธิ์ resolve ชื่อโฮสต์นี้ได้ไหม.

    ``backend`` / ``postgres`` เป็นชื่อ service ของ Docker Compose — resolve ได้เฉพาะ
    **ในเครือข่ายของ compose** เท่านั้น ชื่อที่ไม่มีจุดและไม่ใช่ localhost จึงถือว่า
    เป็นชื่อภายใน · ชื่อที่มีจุด (โดเมนจริง/IP) หรือ localhost ถือว่าเบราว์เซอร์ใช้ได้
    """
    host = host.strip().lower()
    if not host:
        return False
    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}:
        return True
    return "." in host or ":" in host


def _ws_prices_url_with_status() -> tuple[str, str | None]:
    """URL ของ WebSocket ราคา + คำเตือนไทยเมื่อ URL ที่ได้เบราว์เซอร์เปิดไม่ได้.

    **BACKEND_URL กับ WS URL อยู่คนละมุมมองเครือข่าย** (AUDIT_ROUND2_2026-08-07):
    ``BACKEND_URL`` ถูกใช้จาก **ในคอนเทนเนอร์** dashboard ส่วน URL ตัวนี้ถูกยัดเข้า
    ``new WebSocket(...)`` ที่รันใน **เบราว์เซอร์ของผู้ใช้** เดิมโค้ดแปลง BACKEND_URL
    เป็น ws:// ตรง ๆ พอ docker-compose ตั้ง ``BACKEND_URL=http://backend:8000``
    เบราว์เซอร์บนโฮสต์ก็ได้ ``ERR_NAME_NOT_RESOLVED`` ⇒ แถบราคาเรียลไทม์ขึ้น
    "⚠️ ดึงไม่ได้ (WS error)" ครบทั้ง 5 ตัวตลอดเวลาในโหมดรันหลักของโปรเจกต์

    ลำดับการตัดสิน:

    1. ตั้ง ``VAULTIS_WS_URL`` ไว้ → ใช้ค่านั้น ไม่เดาต่อ (คำตอบสุดท้ายของผู้ใช้)
    2. โฮสต์ใน ``BACKEND_URL`` เบราว์เซอร์ resolve ได้ → แปลงเป็น ws:// ตามเดิม
    3. เป็นชื่อภายในเครือข่าย Docker → เดาเป็น ``<scheme>://127.0.0.1:<port>/ws/prices``
       (พอร์ตที่ compose เปิดไว้บนโฮสต์) **พร้อมคำเตือนบนหน้าจอว่าเดามาแบบไหน**
       — URL ที่เดาแล้วต่อไม่ติดยังจบที่ ``ws.onerror`` ซึ่งเขียนว่า "ดึงไม่ได้" อยู่แล้ว
       ไม่มีทางกลายเป็นราคาปลอม แต่ผู้ใช้ต้องอ่านออกว่าต้องไปตั้งอะไรถึงจะใช้ได้จริง

    สองจุดที่การเดาข้อ 3 เคยพูดไม่ครบ (AUDIT_ROUND2_2026-08-07 · รอบเก็บตก):

    * **สคีมาถูกฮาร์ดโค้ดเป็น ``ws://``** ทั้งที่ ``BACKEND_URL`` เป็น ``https``:
      หน้าที่เสิร์ฟผ่าน https เปิด WebSocket แบบ ``ws://`` ไม่ได้ (mixed content —
      เบราว์เซอร์บล็อกก่อนถึงเครือข่ายด้วยซ้ำ) จึงต้องยก ``scheme`` เดียวกับที่
      คำนวณไว้แล้วมาใช้ ไม่ใช่ตั้งใหม่เป็นค่าคงที่
    * ``127.0.0.1`` คือ **เครื่องของคนที่เปิดเบราว์เซอร์** ไม่ใช่เครื่องที่รัน Docker
      การเดานี้จึงถูกเฉพาะตอนที่สองอย่างนั้นเป็นเครื่องเดียวกัน — เปิดจากมือถือหรือ
      เครื่องอื่นในบ้าน มันจะยิงกลับเข้าเครื่องตัวเองแล้วไม่มีทางต่อติด ข้อความบน
      หน้าจอต้องพูดตรง ๆ ไม่ใช่ปล่อยให้ผู้ใช้เดาว่าทำไม ⚠️ ไม่หาย
    """
    explicit = os.getenv("VAULTIS_WS_URL", "").strip()
    if explicit:
        return explicit, None

    parsed = urlparse(BACKEND_URL.strip())
    scheme = "wss" if parsed.scheme == "https" else "ws"
    netloc = parsed.netloc or parsed.path.strip("/")
    derived = f"{scheme}://{netloc.rstrip('/')}/ws/prices"
    if _browser_can_resolve(parsed.hostname or ""):
        return derived, None

    port = parsed.port or (443 if scheme == "wss" else 8000)
    # ใช้ ``scheme`` ตัวเดียวกับที่แปลงไว้ข้างบน — https ⇒ wss เสมอ ไม่งั้นเบราว์เซอร์
    # บล็อกทิ้งเพราะ mixed content แล้วผู้ใช้เห็นแค่ ⚠️ โดยไม่รู้ว่าสาเหตุคือสคีมา
    guessed = f"{scheme}://127.0.0.1:{port}/ws/prices"
    note = (
        f"แถบราคาเรียลไทม์: `BACKEND_URL` = `{BACKEND_URL}` เป็นชื่อโฮสต์ภายในเครือข่าย Docker "
        "ซึ่งเบราว์เซอร์ของคุณ resolve ไม่ได้ (คนละมุมมองเครือข่ายกับที่คอนเทนเนอร์ใช้) "
        f"— ระบบจึงเดาไปที่ `{guessed}` ซึ่งเป็นพอร์ตที่ compose เปิดไว้บนเครื่องโฮสต์ "
        "**การเดานี้ใช้ได้เฉพาะตอนที่คุณเปิดเบราว์เซอร์บนเครื่องเดียวกับที่รัน Docker เท่านั้น** "
        "เพราะ 127.0.0.1 คือเครื่องของเบราว์เซอร์เอง ไม่ใช่เครื่องที่รันคอนเทนเนอร์ — "
        "เปิดจากมือถือหรือเครื่องอื่นในเครือข่าย มันจะยิงกลับเข้าเครื่องตัวเองและต่อไม่ติดแน่นอน · "
        "ถ้าแถบด้านบนยังขึ้น ⚠️ ให้ตั้ง `VAULTIS_WS_URL` เป็น URL ที่เบราว์เซอร์เข้าถึง backend ได้"
    )
    return guessed, note


def _ws_prices_url() -> str:
    """WebSocket URL for live prices (override with VAULTIS_WS_URL)."""
    return _ws_prices_url_with_status()[0]


def _render_realtime_price_ticker_bar() -> None:
    """Live ticker via backend WebSocket (iframe ? Streamlit strips <script> in st.markdown)."""
    resolved_url, ws_note = _ws_prices_url_with_status()
    ws_url = json.dumps(resolved_url, ensure_ascii=False)
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
    <span id="ws-updated" style="margin-left:auto; color:#8B949E;">ยังไม่ได้รับข้อมูล</span>
</div>
<script>
(function () {{
    const wsUrl = {ws_url};
    const TICKERS = ["VOO","SCHD","QQQM","XLV","GLDM"];
    const ws = new WebSocket(wsUrl);
    ws.onmessage = function (event) {{
        const raw = typeof event.data === "string"
            ? event.data
            : new TextDecoder("utf-8").decode(event.data);
        const data = JSON.parse(raw);
        if (data.type !== "price_update") {{
            return;
        }}
        const prices = data.data || {{}};
        const unavailable = data.unavailable || [];
        Object.entries(prices).forEach(([ticker, info]) => {{
            const el = document.getElementById("price-" + ticker);
            if (el) {{
                // %เปลี่ยนแปลงที่คำนวณไม่ได้มาเป็น null — ห้ามเทียบ `>= 0` ตรง ๆ เด็ดขาด
                // เพราะ JS แปลง null เป็น 0 ⇒ `null >= 0` เป็น true ⇒ ช่องที่ "ไม่รู้ค่า"
                // ถูกวาดเป็น "+null%" สีเขียว = อ่านได้ว่าวันนี้บวก ทั้งที่แปลว่าไม่ทราบ
                // (AUDIT_ROUND2_2026-08-07 — กฎเดียวกับฝั่ง backend: คำนวณไม่ได้ ≠ ไม่ขยับ)
                const pctUnknown = (
                    info.change_pct === null ||
                    info.change_pct === undefined ||
                    !isFinite(info.change_pct)
                );
                const color = pctUnknown
                    ? "#8B949E"
                    : (info.change_pct >= 0 ? "#3FB950" : "#F85149");
                const pctText = pctUnknown
                    ? "% ไม่ทราบ"
                    : ((info.change_pct >= 0 ? "+" : "") + info.change_pct + "%");
                el.style.opacity = "1";
                // ใส่เหตุผลผ่าน property ไม่ใช่ innerHTML — ข้อความจาก payload ต้องไม่ถูก parse เป็น HTML
                el.title = pctUnknown
                    ? (info.note || "คำนวณ %เปลี่ยนแปลงไม่ได้")
                    : "";
                el.innerHTML = ticker + " $" + info.price +
                    ' <span style="color:' + color + '">' + pctText + "</span>";
            }}
        }});
        // ตัวที่ดึงไม่ได้ต้องล้างราคาเก่าทิ้ง ไม่ใช่ปล่อยค้างไว้จนดูเหมือนราคาไม่เปลี่ยน
        // (AUDIT_2026-08-06 B7 — "ดึงไม่สำเร็จ" ≠ "ราคาเท่าเดิม")
        unavailable.forEach(function (ticker) {{
            const el = document.getElementById("price-" + ticker);
            if (el) {{
                el.style.opacity = "0.55";
                el.innerHTML = ticker + ' <span style="color:#8B949E">⚠️ ดึงไม่ได้</span>';
            }}
        }});
        const stamp = document.getElementById("ws-updated");
        if (stamp) {{
            const when = data.ts ? new Date(data.ts) : null;
            const shown = (when && !isNaN(when.getTime()))
                ? when.toLocaleTimeString("th-TH")
                : (data.ts || "-");
            stamp.textContent = "อัปเดตล่าสุด " + shown;
        }}
    }};
    ws.onerror = function () {{
        TICKERS.forEach(function (t) {{
            const el = document.getElementById("price-" + t);
            if (el) {{
                el.style.opacity = "0.55";
                el.innerHTML = t + ' <span style="color:#8B949E">⚠️ ดึงไม่ได้ (WS error)</span>';
            }}
        }});
    }};
    ws.onclose = function () {{
        const stamp = document.getElementById("ws-updated");
        if (stamp) stamp.textContent += " · การเชื่อมต่อหลุด";
    }};
}})();
</script>
"""
    components.html(html, height=70)
    if ws_note:
        # ต้องอยู่ข้างแถบเสมอ — ไม่งั้นผู้ใช้เห็นแค่ ⚠️ 5 ตัวโดยไม่มีทางรู้ว่าต้องแก้อะไร
        # (ข้อความจริงของ console อยู่ในเบราว์เซอร์ ซึ่งผู้ใช้ทั่วไปไม่ได้เปิดดู)
        st.caption(ws_note)


def _stale_price_notes(prices: pd.DataFrame) -> list[str]:
    """คำเตือนกำกับตัวเลขที่คิดจากราคา — "ดึงไม่ได้" ≠ "ไม่มีข้อมูล" ≠ "ราคาไม่ขยับ" (G7).

    ตาราง Returns เป็นตัวเลขล้วน ไม่มีช่องให้ติดธง ``stale``/``data_ok`` เหมือน
    snapshot จึงต้องพิมพ์บอกข้าง ๆ: ETF ที่ผู้ให้ข้อมูลหยุดส่งแท่งมาหลายสิบวัน
    ยังได้ผลตอบแทนของ**แท่งจริง**ของตัวเอง (ตัวเลขไม่ได้ถูกกุ) แต่มันเป็นผลตอบแทน
    "ถึงวันที่หยุดส่ง" ซึ่งวางปนกับ ETF ตัวอื่นที่เป็นของวันล่าสุดโดยไม่มีอะไรบอก
    """
    notes: list[str] = []
    if prices is None or prices.empty:
        return notes
    try:
        frame_last = pd.Timestamp(prices.index[-1])
    except (TypeError, ValueError):  # index ที่ไม่ใช่เวลา — เทียบความสดไม่ได้
        return notes
    for ticker in prices.columns:
        bars = real_bars(prices[ticker])
        if bars.empty:
            notes.append(f"⚠️ {ticker}: ดึงราคาไม่ได้เลย — ทุกช่องเป็น N/A ไม่ใช่ 0%")
            continue
        try:
            last = pd.Timestamp(bars.index[-1])
        except (TypeError, ValueError):
            continue
        if last < frame_last:
            notes.append(
                f"⚠️ {ticker}: แท่งราคาล่าสุดคือ {last.strftime('%d/%m/%Y')} "
                f"ตามหลังวันล่าสุดของชุดข้อมูล ({frame_last.strftime('%d/%m/%Y')}) — "
                "ตัวเลขของกองนี้คิดถึงวันนั้น ไม่ใช่วันล่าสุด"
            )
    return notes


def _render_overview_metrics(
    prices: pd.DataFrame,
    tickers: list[str],
    returns_prices: pd.DataFrame | None = None,
    returns_history_error: str | None = None,
) -> None:
    """การ์ดภาพรวม — ``returns_prices`` คือเฟรมที่ยาวพอสำหรับหน้าต่าง 10Y (FIX_PLAN ข้อ 2.8).

    เฟรมหลัก 10 ปีให้ ~2,511 แท่ง สั้นกว่า 2,521 ที่หน้าต่าง 10Y ต้องการ ⇒ แถวนั้นเป็น
    ``N/A`` เสมอ · **ผู้เรียกต้องส่งเฟรมยาวเข้ามา ห้ามให้ฟังก์ชันนี้ไปดึงเน็ตเอง** —
    ตัวเรนเดอร์ที่แอบยิงเน็ตทำให้ผลลัพธ์ไม่ขึ้นกับอาร์กิวเมนต์ที่รับมา (เทสต์ที่ป้อนเฟรม
    สังเคราะห์เข้ามาจะได้ตัวเลขของข้อมูลจริงแทน) และย้ายการจัดการ error ออกจากที่ที่มันอยู่
    """
    long_history_error = returns_history_error
    return_df = calculate_period_returns(prices if returns_prices is None else returns_prices)
    # return_df: แถว = ช่วงเวลา (1M/3M/.../10Y), คอลัมน์ = ticker
    # บั๊กเดิม (AUDIT.md H4): หาคอลัมน์ "1Y (%)" ที่ไม่มีจริง → ไปหยิบคอลัมน์ ticker ตัวสุดท้าย
    # ทำให้การ์ด Best/Worst ETF โชว์ชื่อช่วงเวลาแทนชื่อ ETF
    if "1Y" in return_df.index:
        sortable = return_df.loc["1Y"].dropna()
    else:
        sortable = pd.Series(dtype=float)

    # การ์ดนี้วัดจาก **ช่วงที่ทุกกองมีข้อมูลพร้อมกัน** ไม่ใช่ 10 ปี — ป้าย "10Y blended
    # performance" เดิมจึงเป็นตัวเลขที่ติดป้ายผิด (กองที่ลิสต์ทีหลังย่นช่วงร่วมลง: QQQM
    # เพิ่งลิสต์ปี 2020 ⇒ ใช้จริงราว 5.8 ปี) ซึ่งเป็นการกุข้อมูลชนิดเดียวกับป้าย
    # "ย้อนหลัง 10 ปี" ของหน้า Goals ที่เพิ่งแก้ไป — ป้ายต้องรายงานช่วงจริงเสมอ
    total_return = 0.0
    basket_window = ""
    common = prices.ffill().dropna()
    if len(prices.index) > 1 and len(common) > 1:
        latest = common.iloc[-1]
        base = common.iloc[0]
        basket = (latest / base).mean()
        total_return = (float(basket) - 1.0) * 100
        span_days = (common.index[-1] - common.index[0]).days
        # ถ้อยคำต้องตรงกับสิ่งที่วัดจริง: ``ffill().dropna()`` = นับจากวันที่ **ทุกกองมี
        # ข้อมูลแล้ว** (ไม่ใช่ "ทุกกองมีแท่งของวันนั้น" — กองที่ผู้ให้ข้อมูลหยุดส่งจะถูก
        # เติมค่าเดิมไปข้างหน้า ซึ่ง ``_stale_price_notes`` เตือนแยกอยู่แล้ว)
        basket_window = (
            f"{span_days / 365.25:.1f} ปี นับจากวันที่ทุกกองมีข้อมูลครบ "
            f"({common.index[0]:%m/%Y}–{common.index[-1]:%m/%Y})"
        )
    else:
        basket_window = "ยังไม่มีช่วงที่ทุกกองมีข้อมูลครบพร้อมกัน"

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

    if long_history_error:
        # ดึงประวัติช่วงยาวไม่สำเร็จต้องถึงผู้ใช้ ไม่ใช่ลงไปนอนใน log — ตัวเลขบางช่วง
        # (10Y) จะเป็น N/A ด้วยเหตุผลที่ไม่ใช่ "ยังไม่มีข้อมูล"
        st.caption(
            f"⚠️ ดึงราคาช่วงยาวไม่สำเร็จ ({long_history_error}) — "
            "ตัวเลขช่วง 10Y ด้านล่างจะเป็น N/A เพราะข้อมูลไม่พอ ไม่ใช่เพราะไม่มีผลตอบแทน"
        )

    total_return_class = "metric-change-positive" if total_return >= 0 else "metric-change-negative"
    cards = st.columns(4)
    with cards[0]:
        st.markdown(
            f"""
            <div class="metric-card">
              <div class="metric-title">Total Return (Basket)</div>
              <div class="metric-value">{total_return:+.2f}%</div>
              <div class="{total_return_class}">{basket_window}</div>
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

    # กองที่ไม่มีตัวเลข 1Y ถูกตัดออกจากการจัดอันดับ — ต้องบอก ไม่ใช่หายเงียบ ๆ (G7)
    excluded = [str(t) for t in return_df.columns if t not in sortable.index]
    if excluded:
        st.caption(
            "⚠️ ไม่ได้เข้าการจัดอันดับ Best/Worst (1Y): "
            + ", ".join(excluded)
            + " — ไม่มีตัวเลขผลตอบแทน 1 ปี (ดึงราคาไม่ได้ หรือแท่งราคาจริงไม่พอ) ไม่ใช่ 0%"
        )
    for note in _stale_price_notes(prices):
        st.caption(note)


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
        f"ใส่บทวิเคราะห์ AI ในรายงาน (เรียก {ANTHROPIC_MODEL} — มีค่าใช้จ่ายตามจำนวนโทเคนจริง)",
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


def _to_number(value: object) -> float | None:
    """แปลงเป็น ``float`` ที่ใช้งานได้จริง — อ่านไม่ออก/NaN/inf คืน ``None``.

    ห้ามใช้สำนวน ``float(x) or 0.0`` บนเส้นทางเงิน: ``NaN`` เป็น truthy สำนวนนั้น
    จึงดักไม่ได้ และ ``0.0`` อ่านได้ว่า "เท่าทุนพอดี" ซึ่งคนละความหมายกับ "ไม่รู้"
    """
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


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


def _render_target_weights_table(current_tickers: list[str], preset: dict[str, float]) -> None:
    """ตารางเป้าหมาย preset เทียบกับที่ใช้จริง + คำเตือนเมื่อค่าที่ผู้ใช้ตั้งถูกปรับ.

    ``get_target_weights_with_status()`` คืน ``notes`` ภาษาไทยมาให้แล้วเมื่อค่าที่ตั้งไว้
    ถูกตีความ/ปรับ (เขียนเป็นเปอร์เซ็นต์, ผลรวมไม่เท่า 1.0, ตั้งให้ ticker ที่ไม่ได้ติดตาม)
    ถ้าหน้าจอแสดงแต่เลขที่ปรับแล้ว ผู้ใช้จะไม่มีทางรู้ว่าค่าที่ตัวเองตั้งไม่ได้ถูกใช้
    · คอนฟิกผิดรูปต้องเป็นข้อความไทย ไม่ใช่ traceback (AUDIT_2026-08-06 B10)
    """
    try:
        status = get_target_weights_with_status(current_tickers)
    except InvalidTargetWeights as exc:
        st.error(
            f"`portfolio.target_weights` ใน config.json ใช้ไม่ได้: {exc} — "
            "ระบบไม่เดาค่าแทน แก้ไฟล์ให้ถูกต้องแล้วรีเฟรชหน้านี้"
        )
        return

    effective_targets = status.weights
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "ETF": t,
                    "เป้าหมายตาม preset": f"{preset.get(t, 0) * 100:.0f}%",
                    "เป้าหมายที่ใช้จริง": f"{effective_targets.get(t, 0) * 100:.1f}%",
                    "ที่มา": {"custom": "ตั้งเอง", "preset": "preset"}.get(
                        status.source.get(t, ""), "ไม่รู้จัก"
                    ),
                }
                for t in current_tickers
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )
    for note in status.notes:
        st.warning(note)
    st.caption(
        'ถ้าต้องการกำหนดเอง ให้แก้ `portfolio.target_weights` ใน config.json '
        '(เช่น {"VOO": 0.4, "GLDM": 0.05}) — เว้นว่างไว้จะใช้ preset ด้านบน'
    )


def _render_fallback_fx_input(stored_value: object) -> float:
    """ช่อง "อัตราแลกเปลี่ยนสำรอง" — ช่วงต้องผูกกับ ``utils/fx.py`` แหล่งเดียว.

    เดิมช่องนี้ ``min_value=1.0`` และ **ไม่มี** ``max_value`` เลย ทั้งที่ปลายทาง
    ``utils.fx._config_fallback()`` รับเฉพาะ :data:`FX_MIN_RATE`–:data:`FX_MAX_RATE`
    กรอก 1.5 หรือ 900 จึงบันทึกลง ``config.json`` ได้ตามปกติ แล้ววันที่ดึงอัตราสดไม่ได้
    ทุกตัวเลข "บาท" ทั้งระบบก็ดับพร้อมกันด้วย ``FxRateUnavailable`` โดยหน้าจอที่ทำให้เกิด
    ไม่เคยเตือนอะไรเลย — หน้าที่ตั้งค่าต้องกันไว้ตั้งแต่ต้นทาง ไม่ใช่ให้ไปดังตอนใช้จริง

    ค่าที่ **บันทึกไว้แล้ว** นอกช่วง/อ่านไม่ออก ห้ามทำให้หน้าจอพัง (Streamlit จะ error
    ถ้า ``value`` อยู่นอก ``min_value..max_value``) → เตือนพร้อมบอกค่าเดิมที่เจอ
    แล้วหนีบให้อยู่ในช่วงเป็นค่าตั้งต้นของช่อง — ผู้ใช้ยังเห็นว่าของเดิมคืออะไร
    """
    rate = _to_number(stored_value)
    if rate is None:
        st.warning(
            f"ค่าสำรองใน config.json (`display.default_fx_rate` = {stored_value!r}) อ่านเป็นตัวเลขไม่ได้ "
            f"— ช่องด้านล่างตั้งต้นให้ที่ {FX_DEFAULT_FALLBACK_RATE:.2f} บาท/USD กดบันทึกเพื่อยืนยันค่าที่ถูกต้อง"
        )
        rate = float(FX_DEFAULT_FALLBACK_RATE)
    elif not (FX_MIN_RATE <= rate <= FX_MAX_RATE):
        clamped = min(max(rate, FX_MIN_RATE), FX_MAX_RATE)
        st.warning(
            f"ค่าสำรองที่บันทึกไว้ {rate:g} บาท/USD อยู่นอกช่วง {FX_MIN_RATE:.0f}–{FX_MAX_RATE:.0f} "
            "ที่ระบบยอมรับ — วันที่ดึงอัตราสดไม่ได้ ทุกตัวเลขบาทจะคำนวณไม่ได้ทั้งหน้าจอ "
            f"ช่องด้านล่างจึงตั้งต้นให้ที่ {clamped:.2f} กดบันทึกเพื่อแก้ค่าเดิม"
        )
        rate = float(clamped)

    return float(
        st.number_input(
            "อัตราแลกเปลี่ยนสำรอง (ใช้เมื่อดึงค่าสดไม่ได้)",
            min_value=float(FX_MIN_RATE),
            max_value=float(FX_MAX_RATE),
            value=float(rate),
            step=0.1,
            format="%.4f",
            help=(
                f"ช่วงที่ใช้ได้ {FX_MIN_RATE:.0f}–{FX_MAX_RATE:.0f} บาท/USD "
                "(ช่วงเดียวกับที่ utils/fx.py ใช้ตรวจอัตราสด) — ค่านอกช่วงถือว่าข้อมูลผิด ไม่ใช่ค่าจริง"
            ),
        )
    )


def render_settings_page() -> None:
    """หน้าตั้งค่า — บันทึกลง config.json."""
    st.header("Settings")
    st.caption("ตั้งค่า DCA, ETF ที่ติดตาม, การแจ้งเตือน และการแสดงผล")

    config = load_config()
    current_tickers = get_tickers()

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
    _render_target_weights_table(current_tickers, RISK_PROFILES[selected_profile])

    st.divider()
    st.subheader("4) Notification Settings")
    # อ่าน env ก่อนเสมอ — แตะ st.secrets ตอนไม่มี secrets.toml ทำให้ Streamlit
    # ขึ้นกล่อง "No secrets found…" บนหน้า ทั้งที่ค่าจริงมาจาก .env และใช้งานได้ปกติ
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "")
    if not webhook_url.strip():
        try:
            webhook_url = str(st.secrets["DISCORD_WEBHOOK_URL"])
        except Exception:
            webhook_url = ""

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
    # ตัวเลือกมาจาก NAV_ITEMS (แหล่งเดียวกับแถบข้าง) — ห้ามเขียนลิสต์หน้าจอซ้ำที่นี่อีก
    # (AUDIT_ROUND2_2026-08-07) และถ้าค่าที่บันทึกไว้ไม่อยู่ในเมนูจริง ๆ ต้อง **บอก**
    # ก่อนเขียนทับ ไม่ใช่เด้งไป Overview เงียบ ๆ แล้วกลืนค่าของผู้ใช้ตอนกดบันทึก
    current_default_page = str(config["display"]["default_page"])
    if current_default_page not in NAV_ITEMS:
        st.warning(
            f"หน้าเริ่มต้นที่บันทึกไว้ (`{current_default_page}`) ไม่มีอยู่ในเมนูของแอปแล้ว "
            "— ช่องด้านล่างจึงตั้งต้นที่ Overview **กดบันทึกเมื่อไรค่าเดิมจะถูกเขียนทับ** "
            "ถ้าไม่ได้ตั้งใจ ให้เลือกหน้าที่ต้องการก่อนกดบันทึก"
        )
    default_page = st.selectbox(
        "หน้าเริ่มต้นเมื่อเปิดแอป",
        NAV_ITEMS,
        index=NAV_ITEMS.index(current_default_page) if current_default_page in NAV_ITEMS else 0,
    )
    currency = st.radio(
        "สกุลเงินหลักที่แสดงผล",
        options=["THB", "USD"],
        index=0 if str(config["display"]["currency"]).upper() == "THB" else 1,
        horizontal=True,
    )
    default_fx_rate = _render_fallback_fx_input(config["display"].get("default_fx_rate"))

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


def _render_alert_check_result(result: dict) -> None:
    """ผลของปุ่ม "ตรวจ Alert ตอนนี้" — "ตรวจไม่ได้" ห้ามอ่านเป็น "ไม่ถึงเงื่อนไข".

    ``check_alerts()`` คืนมาครบอยู่แล้ว: ``store_error`` (อ่านคลังไม่ได้ = ข้ามทั้งรอบ)
    และ ``unchecked`` (alert ที่ดึงราคาไม่ได้ จึงยังไม่ได้ตรวจจริง) เดิมหน้าจอดูแค่
    ``triggered`` แล้วพิมพ์ "ยังไม่มี Alert ที่ถึงเงื่อนไข" ⇒ ความล้มเหลวกลายเป็น
    คำยืนยันว่าราคายังไม่ถึง (AUDIT_2026-08-06 D1.1)

    รอบนี้ (AUDIT_ROUND2_2026-08-07) เพิ่มอีกสองชั้นที่เคยกลายเป็น "ตรวจแล้วไม่มีอะไร":

    * **ผลผิดสัญญา** — เดิมอ่านทุกคีย์ด้วย ``.get()`` พร้อมค่าดีฟอลต์ ผลลัพธ์ที่ขาดคีย์
      (ผู้เรียกเวอร์ชันเก่า/สตับ/บั๊กในอนาคต) จึงกลายเป็น ``triggered=[]`` +
      ``unchecked=[]`` แล้วพิมพ์ "ยังไม่มี Alert ที่ถึงเงื่อนไข" — เป็นการยืนยันสิ่งที่
      ไม่รู้ ตอนนี้ใช้ตัวตรวจสัญญาที่อยู่ข้างผู้ผลิต (``check_result_contract_error``)
      แล้วรายงานว่า **ไม่ทราบผล**
    * **ไม่มีไฟล์คลังเลย** (``store_status == "missing"``) — คนละเรื่องกับ "มีคลังแต่
      ไม่มี alert ถึงเงื่อนไข" เช่นบนเครื่องที่ยังไม่เคยสร้าง alert หรือใน CI ที่ไฟล์
      ถูก gitignore ไว้ ต้องบอกพาธที่มองหาด้วย ไม่ใช่ตอบว่าตรวจแล้วเรียบร้อย
    """
    contract_error = check_result_contract_error(result)
    if contract_error is not None:
        st.error(
            f"ไม่ทราบผลการตรวจ Alert — ผลลัพธ์ที่ได้ผิดสัญญา ({contract_error}) "
            "**ยังไม่ได้ข้อสรุปว่ามี Alert ถึงเงื่อนไขหรือไม่** ไม่ใช่ว่าตรวจแล้วไม่มีอะไร "
            "(ลองกดตรวจใหม่ ถ้ายังเป็นแบบเดิมแปลว่าเป็นบั๊ก ไม่ใช่สภาพตลาด)"
        )
        return

    if result.get("store_error"):
        st.error(
            f"อ่านคลัง Price Alert ไม่ได้ ({result.get('error') or 'ไม่ทราบสาเหตุ'}) — "
            "ข้ามการตรวจรอบนี้ทั้งหมด ระบบ **ไม่ได้** เขียนทับไฟล์ของคุณ "
            "นี่ไม่ได้แปลว่าไม่มี Alert ค้าง แต่แปลว่าตรวจไม่ได้"
        )
        return

    triggered = list(result.get("triggered") or [])
    unchecked = list(result.get("unchecked") or [])
    checked = int(_to_number(result.get("checked")) or 0)
    store_status = result.get("store_status")
    store_state = store_status.get("status") if isinstance(store_status, dict) else None
    if store_state == "missing" and not triggered and not unchecked:
        # "ไม่มีไฟล์คลัง" ≠ "ไม่มี alert ถึงเงื่อนไข" — และเป็นเรื่องที่ผู้ใช้แก้ได้เอง
        # (ถ้ามีผลจริงติดมาด้วยให้รายงานผลนั้นตามปกติ ห้ามกลืนของที่ตรวจได้จริงทิ้ง)
        st.warning(
            "เครื่องนี้ยังไม่มีไฟล์คลัง Price Alert เลย "
            f"(มองหาที่ `{store_status.get('path') or 'ไม่ทราบพาธ'}`) — จึงยังไม่มีอะไรให้ตรวจ "
            "ไม่ใช่ว่าตรวจแล้วไม่มี Alert ถึงเงื่อนไข · สร้าง Alert สักรายการแล้วไฟล์จะถูกสร้างให้เอง"
        )
        return
    if triggered:
        st.success(f"มี Alert ถึงเงื่อนไข {len(triggered)} รายการ (ส่ง Discord แล้ว)")
    elif not unchecked:
        st.info("ยังไม่มี Alert ที่ถึงเงื่อนไข")
    if unchecked:
        detail = ", ".join(
            f"{str(item.get('ticker') or '-')} ({item.get('reason') or 'ไม่ทราบสาเหตุ'})"
            for item in unchecked
        )
        st.warning(
            f"ตรวจไม่ได้ {len(unchecked)} รายการ (ตรวจได้จริง {checked} รายการ): {detail} — "
            "รายการเหล่านี้ยังค้างเป็น pending ระบบจะตรวจใหม่รอบถัดไป"
        )


def render_price_alerts_page() -> None:
    """หน้า Price Alerts: ระดับที่ระบบแนะนำ, ตั้งเอง, และรายการที่รออยู่."""
    st.header("Price Alerts")
    tickers = get_tickers()
    if not tickers:
        st.warning("ยังไม่มี ETF ในระบบ — เพิ่มได้ที่หน้า Settings")
        return

    try:
        all_alerts = list_alerts(include_triggered=True)
        active_alerts = get_active_alerts_with_distance(near_threshold_pct=2.0)
    except AlertStoreUnavailable as exc:
        # "อ่านคลังไม่ได้" ≠ "ไม่มี alert" — และต้องเป็นข้อความไทย ไม่ใช่ traceback
        # ของ Streamlit ที่ผู้ใช้อ่านไม่ออก (AUDIT_2026-08-06 H2 ฝั่งหน้าจอ)
        st.error(
            f"อ่านคลัง Price Alert ไม่ได้: {exc} — หน้านี้จึงยังแสดง Alert ไม่ได้ "
            "ระบบ **ไม่ได้** เขียนทับไฟล์ของคุณ และนี่ไม่ได้แปลว่าไม่มี Alert ค้างอยู่ "
            "(ตรวจไฟล์ `alerts/data/price_alerts.json` แล้วรีเฟรชหน้านี้)"
        )
        return
    history_alerts = [item for item in all_alerts if bool(item.get("triggered"))]
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
        # ไม่ st.rerun() ต่อท้ายอีกแล้ว — rerun ทิ้งข้อความผลตรวจทั้งหมดก่อนผู้ใช้ได้อ่าน
        # (รายงานที่ไม่มีใครเห็น = ยังตัดเงียบอยู่) กด Refresh Data เองได้ถ้าต้องการรีเฟรชตาราง
        _render_alert_check_result(check_alerts())

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


def _render_atr_section(ticker: str, ohlc: pd.DataFrame) -> None:
    """ช่วงแกว่งเฉลี่ยต่อวัน (ATR) — **สถิติพรรณนา ไม่เข้าเลขคะแนน/จัดสรร DCA**.

    ATR ตอบคำถามเดียว: "ปกติวันหนึ่งราคาขยับกี่ดอลลาร์" ไม่ได้บอกทิศทาง จึงไม่ใช่
    สัญญาณซื้อขายและหน้าจอต้องไม่เขียนให้อ่านเป็นอย่างนั้น — โดยเฉพาะกับเครื่องมือ
    DCA ระยะยาวที่ไม่มีคำสั่งขายอยู่แล้ว ประโยชน์จริงคือ **ตั้งความคาดหวัง**: เห็นว่า
    ราคาที่ลง 1.5% วันนี้คือวันธรรมดาหรือวันที่ผิดปกติ จะได้ไม่ตกใจขายของที่ไม่ควรขาย
    """
    st.subheader(f"ช่วงแกว่งต่อวัน — {ticker} (ATR)")
    try:
        stats = atr_stats(ohlc)
    except ValueError as exc:
        st.error(f"{ticker}: {exc}")  # ข้อมูลไม่พอ = บอกตรง ๆ ห้ามโชว์ 0 (C1)
        return

    atr_value = float(stats["atr"])
    atr_pct = float(stats["atr_pct"])
    percentile = stats["percentile"]

    cols = st.columns(3)
    cols[0].metric(f"ATR({stats['length']})", f"${atr_value:,.2f}")
    cols[1].metric("คิดเป็น % ของราคา", f"{atr_pct:.2f}%")
    cols[2].metric(
        "เทียบตัวเองย้อนหลัง 1 ปี",
        f"อันดับ {percentile:.0f}%" if percentile is not None else "N/A",
        help="ATR วันนี้สูงกว่ากี่ % ของวันในหน้าต่างนี้ — สูงแปลว่าตลาดกำลังผันผวนกว่าปกติของกองนี้เอง",
    )

    band_low, band_high = atr_pct, atr_pct * 2.0
    st.caption(
        f"วันธรรมดาของ {ticker} ราคาขยับราว **${atr_value:,.2f} (~{band_low:.2f}%)** "
        f"— วันที่ขยับเกิน ~{band_high:.2f}% คือวันที่ผิดปกติสำหรับกองนี้ "
        f"(คำนวณแบบ Wilder จาก {stats['bars']:,} แท่ง · True Range นับ gap ตอนเปิดตลาดด้วย) · "
        "ตัวเลข % ต่างหากที่เทียบข้ามกองได้ ส่วนตัวเลขดอลลาร์เทียบไม่ได้เพราะราคาต่อหน่วยต่างกัน · "
        "**เป็นสถิติพรรณนา ไม่ใช่สัญญาณซื้อขาย และไม่เข้าเลขคะแนนหรือแผน DCA**"
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

    _render_atr_section(selected_ticker, selected_ohlc)

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


def render_backtest_page(
    prices: pd.DataFrame,
    default_weights: dict[str, float] | None,
    tickers: list[str],
    weights_error: Exception | None = None,
) -> None:
    """หน้า Backtest: เทียบผลพอร์ตย้อนหลังกับ benchmark.

    ``default_weights=None`` = อ่านสัดส่วนเป้าหมายจาก config.json ไม่ได้ (เหตุผลอยู่ใน
    ``weights_error``) — หน้านี้ต้องบอกเหตุผลแล้วหยุด **แต่ห้ามลาม**ไปทำให้หน้าอื่นดับ
    (AUDIT_ROUND2_2026-08-07 CRITICAL/T7: เดิม ``render_dashboard()`` เรียก
    ``_tracked_target_weights()`` ก่อนวาด sidebar ค่าน้ำหนักผิดคีย์เดียวจึงล็อกผู้ใช้
    ออกจากทั้ง 13 หน้า รวมหน้า Settings ที่เป็นทางเดียวที่จะไปแก้ค่านั้น)
    """
    st.header("Backtest")
    if default_weights is None:
        _render_target_weights_problem(weights_error)
        return
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

        # กองที่ "หายไปจากพอร์ต" ต้องขึ้นก่อนตัวเลขเสมอ — สำนวนเดียวกับหน้า DCA Simulator
        # ที่อยู่ถัดลงไปไม่กี่บรรทัด (``describe_coverage`` ตัวเดียวกัน) เดิมหน้านี้
        # **ไม่เคยเรียกเลย** ทั้งที่ ``run_portfolio_backtest`` แนบรายชื่อมาให้ที่
        # ``.attrs[COVERAGE_ATTR]`` แล้ว ⇒ ตั้งสไลเดอร์ SCHD ไว้ 0% หรือกองที่ถือน้ำหนัก
        # อยู่ไม่มีคอลัมน์ราคา หน้าจอก็วาดเส้นของพอร์ตที่ normalize ใหม่บนกองที่เหลือ
        # ให้ดูเป็นคำตอบของพอร์ตที่กรอกมา (AUDIT_ROUND2_2026-08-07 T6 · ชั้นไลบรารี
        # แก้แล้วแต่ผู้บริโภคยังไม่ได้ต่อสาย)
        coverage_warning = describe_coverage(backtest_df.attrs.get(COVERAGE_ATTR))
        if coverage_warning:
            st.warning(coverage_warning)

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


def render_dca_simulator_page(
    prices: pd.DataFrame,
    default_weights: dict[str, float] | None,
    tickers: list[str],
    weights_error: Exception | None = None,
) -> None:
    """หน้า DCA Simulator: จำลองการทยอยลงทุนรายเดือน.

    ``default_weights=None`` = อ่านสัดส่วนเป้าหมายไม่ได้ — เหตุผลเดียวกับ
    :func:`render_backtest_page` (AUDIT_ROUND2_2026-08-07 CRITICAL/T7)
    """
    st.header("DCA Simulator")
    if default_weights is None:
        _render_target_weights_problem(weights_error)
        return
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

    # ETF ที่เพิ่งเข้าตลาดตัดช่วงต้นของการจำลองทิ้งทั้งเดือน — ต้องบอกก่อนโชว์ตัวเลข
    # ไม่ใช่ปล่อยให้ Total Invested ที่ต่ำกว่าจริงอธิบายตัวเอง (AUDIT_2026-08-06 B8)
    coverage_warning = describe_coverage(dca_df.attrs.get(COVERAGE_ATTR))
    if coverage_warning:
        st.warning(coverage_warning)

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


def _momentum_available(row: dict) -> bool:
    """โมเมนตัมของแถวนี้คำนวณได้ไหม (ค่าเริ่มต้น True เพื่อรองรับ payload เก่าที่ไม่มีคีย์)."""
    return bool(row.get("momentum_available", True))


def _momentum_points(row: dict) -> int | None:
    """คะแนนโมเมนตัม หรือ ``None`` เมื่อคำนวณไม่ได้ (FIX_PLAN ข้อ 1.5).

    ``score_from_prices`` ตัดหน้าต่างที่ข้อมูลไม่พอออกจาก**ทั้งคะแนนและคะแนนเต็ม**
    หน้าจอจึงต้องแสดง "ไม่มีข้อมูล" ห้ามแปลงเป็น 0 ซึ่งอ่านว่า "ราคาไม่ขึ้นเลย"
    """
    value = row.get("momentum_score")
    if not _momentum_available(row) or value is None:
        return None
    return int(value)


def _momentum_max(row: dict) -> int:
    """เพดานคะแนนโมเมนตัมที่ใช้จริงกับแถวนี้ (อาจน้อยกว่า ``MOMENTUM_MAX``)."""
    raw = row.get("momentum_max", MOMENTUM_MAX)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return MOMENTUM_MAX


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
                    "Volatility": None,
                    "Valuation": None,
                    "RelStrength": None,
                    "Expense": None,
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
                # โมเมนตัมที่คำนวณไม่ได้ = N/A ไม่ใช่ 0 (0 อ่านว่า "ราคาไม่ขึ้น" ซึ่งคนละเรื่อง)
                "Momentum": _momentum_points(payload),
                "Dividend": int(payload.get("dividend_score", 0) or 0),
                "Volatility": int(payload.get("volatility_score", 0) or 0),
                "Valuation": int(payload.get("valuation_score", 0) or 0),
                "RelStrength": int(payload.get("relative_strength_score", 0) or 0),
                "Expense": int(payload.get("expense_score", 0) or 0),
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
    # waterfall แทน flat bar (Roadmap "ของแถม" ข้อสุดท้าย) — เห็นการสะสมทีละองค์ประกอบจนถึงคะแนนรวม
    # หมวดที่คำนวณไม่ได้ (โมเมนตัม/ปันผล/Valuation/RelStrength/Expense) ถูก **ตัดออกจากกราฟ
    # และจากคะแนนเต็ม** ไม่ใช่วาดเป็นแท่ง 0 ซึ่งอ่านว่า "ได้ 0 คะแนน" ทั้งที่ความจริงคือ
    # ไม่มีข้อมูล (C1) — ตรงกับที่ ``score_from_prices`` หด ``max_score`` ให้เองแล้ว
    momentum_ok = _momentum_available(selected_raw)
    dividend_ok = bool(selected_raw.get("dividend_available", True))
    components: list[tuple[str, float]] = [
        (f"Trend (เต็ม {TREND_MAX})", float(selected_row["Trend"])),
        (f"Timing (เต็ม {TIMING_MAX})", float(selected_row["Timing"])),
    ]
    excluded: list[str] = []
    if momentum_ok:
        components.append(
            (f"Momentum (เต็ม {_momentum_max(selected_raw)})", float(selected_row["Momentum"]))
        )
    else:
        excluded.append("Momentum")
    if dividend_ok:
        components.append((f"Dividend (เต็ม {DIVIDEND_MAX})", float(selected_row["Dividend"])))
    else:
        excluded.append("Dividend")
    # Volatility คำนวณจาก closes ได้เสมอ (ราคา >= 200 แถวเป็นเงื่อนไขของคะแนนอยู่แล้ว)
    # จึงไม่มีสถานะ "ตัดออก" ต่างจากอีกสามมิติที่ตามมา
    components.append((f"Volatility (เต็ม {VOLATILITY_MAX})", float(selected_row["Volatility"])))
    for label, max_points, available_key in (
        ("Valuation", VALUATION_MAX, "valuation_available"),
        ("RelStrength", RELATIVE_STRENGTH_MAX, "relative_strength_available"),
        ("Expense", EXPENSE_MAX, "expense_available"),
    ):
        if bool(selected_raw.get(available_key)):
            components.append((f"{label} (เต็ม {max_points})", float(selected_row[label])))
        else:
            excluded.append(label)
    component_labels = [label for label, _ in components]
    component_values = [value for _, value in components]
    waterfall_fig = go.Figure(
        go.Waterfall(
            orientation="v",
            x=component_labels + ["คะแนนรวม"],
            measure=["relative"] * len(component_values) + ["total"],
            y=component_values + [0],
            text=[f"+{v:.0f}" for v in component_values] + [f"{sum(component_values):.0f}"],
            textposition="outside",
            connector={"line": {"color": THEME["border"]}},
            increasing={"marker": {"color": THEME["accent"]}},
            totals={"marker": {"color": THEME["positive"]}},
        )
    )
    waterfall_fig.update_layout(
        title=f"{selected_ticker} Score Breakdown", showlegend=False, height=360
    )
    st.plotly_chart(_apply_plotly_dark_theme(waterfall_fig), use_container_width=True)
    if excluded:
        st.caption(
            f"{', '.join(excluded)}: ไม่มีข้อมูลพอจะคำนวณ — ตัดออกจากคะแนนเต็มของ "
            f"{selected_ticker} (คะแนนรวม {selected_raw.get('total_score')}/"
            f"{selected_raw.get('max_score')}) ไม่ใช่ให้ 0 คะแนน"
        )

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
    heat_cols = [
        "Score %",
        "Trend",
        "Timing",
        "Momentum",
        "Dividend",
        "Volatility",
        "Valuation",
        "RelStrength",
        "Expense",
    ]
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


def _render_empty_allocation_reason(result: dict) -> None:
    """บอกว่า "ทำไมแผนจัดสรรว่าง" ตามสาเหตุจริง — ห้ามเหมารวมเป็นคำแนะนำการลงทุน.

    ``calculate_allocation()`` คืน ``{}`` ได้จากสาเหตุที่แยกจากกันไม่ได้ที่ปลายทาง:
    (ก) ไม่มี ticker ไหน ``data_ok=True`` = **ดึงราคาไม่ได้ทั้งหมด**
    (ข) งบ ≤ 0 (ค) งบน้อยกว่าก้อนต่ำสุด 100 บาท (ง) ไม่มีน้ำหนักเป้าหมายรองรับ

    เดิมหน้าจอพิมพ์ "เดือนนี้ไม่มี ETF ที่คะแนนถึงเกณฑ์จัดสรร — โมเดลแนะนำถือเงินสดรอ"
    ทับทุกสาเหตุ ซึ่งผิดสองชั้น: (1) ความล้มเหลวของการดึงข้อมูลกลายเป็นคำแนะนำการลงทุน
    และไปโผล่ในหน้าเดียวกับ "NO DATA: VOO, SCHD, ..." ที่ขัดกันเอง (2) อ้าง "เกณฑ์คะแนน"
    ที่นโยบาย DCA ปัจจุบันไม่มีอยู่แล้ว (คะแนนแค่เอียงน้ำหนัก 0.6–1.4 เท่า ไม่ตัดตัวไหนออก)
    ใช้ถ้อยคำชุดเดียวกับ ``analysis/ai_advisor._allocation_summary_lines()``
    (AUDIT_2026-08-06 C2.2)
    """
    scores = list(result.get("etf_scores") or [])
    no_data = list(result.get("no_data_tickers") or [])
    usable = [
        row
        for row in scores
        if isinstance(row, dict)
        and row.get("data_ok", True)
        and _to_number(row.get("total_pct")) is not None
    ]

    if not usable:
        if scores or no_data:
            # เคยพยายามประเมินแล้วแต่ไม่มีตัวไหนรอด = ดึงข้อมูลไม่ได้ (ความล้มเหลวจริง)
            st.error(
                "ไม่มี ETF ที่มีข้อมูลพร้อมจัดสรร (ดึงข้อมูลไม่ได้) — "
                "นี่คือความล้มเหลวของการดึงข้อมูล **ไม่ใช่คำแนะนำให้ถือเงินสด** "
                "ลองกดคำนวณใหม่อีกครั้ง"
            )
        else:
            # ไม่มีอะไรถูกประเมินเลย — คนละเรื่องกับ "ดึงข้อมูลไม่สำเร็จ"
            st.warning(
                "โมเดลไม่ได้ประเมิน ETF ใดเลยเดือนนี้ — ตรวจสอบรายการ ETF ที่หน้า Settings "
                "(ไม่ใช่คำแนะนำให้ถือเงินสด)"
            )
        return

    budget = _to_number(result.get("budget_thb"))
    if budget is None:
        st.warning("ไม่ทราบงบ DCA ของเดือนนี้ — จึงยังจัดสรรไม่ได้")
        return
    if budget <= 0:
        st.warning("งบ DCA เดือนนี้เป็น 0 บาท — ไม่มีเงินให้จัดสรร (ตั้งงบได้ที่หน้า Settings)")
        return
    if budget < ALLOCATION_UNIT_THB:
        st.warning(
            f"งบ DCA เดือนนี้ {budget:,.0f} บาท น้อยกว่าก้อนต่ำสุด {ALLOCATION_UNIT_THB} บาท "
            "จึงแบ่งไม่ได้สักก้อน — เพิ่มงบหรือสะสมไปเดือนถัดไป"
        )
        return
    st.warning(
        "มี ETF ที่ข้อมูลพร้อม แต่ไม่มีตัวไหนมีน้ำหนักเป้าหมายรองรับ — "
        "ตรวจสอบ `portfolio.target_weights` / โปรไฟล์ความเสี่ยงที่หน้า Settings"
    )


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
        _render_empty_allocation_reason(result)

    advice_text = str(result.get("advice_text") or result.get("advice") or "")
    if result.get("ai_used"):
        st.markdown(f"### คำอธิบายจาก AI ({ANTHROPIC_MODEL})")
        st.markdown(advice_text)
    elif advice_text.startswith("⚠️"):
        # LLM ล้มเหลวจริง (คีย์หาย/provider ล่ม/ตอบว่าง) — get_monthly_advice ไม่ throw แล้ว
        # แต่คืน ai_used=False พร้อมข้อความ ⚠️ ถ้าแสดงเป็นกล่องข้อมูลปกติจะดูเหมือนคำแนะนำ
        st.error(advice_text)
        st.caption(
            "คะแนน/แผนจัดสรรด้านบนยังใช้ได้ตามปกติ — คำนวณในโค้ดทั้งหมด ไม่ได้พึ่ง AI"
        )
    else:
        # AI ถูกปิดไว้เพื่อคุมค่าใช้จ่าย = สถานะปกติ ไม่ใช่ความล้มเหลว
        st.info(advice_text)

    discord_result = result.get("discord_result", {})
    if discord_result.get("success"):
        st.info("ส่งสรุปเข้า Discord แล้ว")
    elif not discord_result.get("skipped"):
        st.warning(f"ส่ง Discord ไม่สำเร็จ: {discord_result.get('error', 'unknown error')}")


class NewsSourcesUnavailable(RuntimeError):
    """แหล่งข่าวจริงล้มเหลวหมด — โยนออกเพื่อไม่ให้ st.cache_data เก็บความล้มเหลวไว้ทั้งชั่วโมง (AUDIT.md C1)."""


@st.cache_data(ttl=1800, show_spinner=False)
def cached_news(symbol: str) -> dict:
    """ข่าวราย ticker (cache 30 นาที) — ไม่เรียก LLM ไม่แตะฐานข้อมูล จึงไม่มีค่าใช้จ่าย.

    ถ้าแหล่งข่าวจริงล้มเหลวหมดจะ raise — Streamlit ไม่เก็บผลของ call ที่ throw
    ความล้มเหลวจึงถูกลองใหม่รอบหน้า ไม่ค้างเป็น "ไม่มีข่าว" ไปอีก 30 นาที
    """
    result = get_news_with_status(symbol)
    if result["all_news_sources_failed"] and not result["items"]:
        details = "; ".join(
            f"{s['name']}: {s['detail']}" for s in result["sources"] if s["status"] == STATUS_ERROR
        )
        raise NewsSourcesUnavailable(details or "ดึงข่าวไม่สำเร็จ")
    return result


def _format_news_time(published_at: str) -> str:
    """แปลงเวลาเป็นเวลาไทย; อ่านไม่ออกคืน '-' (ไม่เดาเวลาให้ข่าว)."""
    raw = str(published_at or "").strip()
    if not raw:
        return "-"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return "-"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BANGKOK_TZ).strftime("%d/%m/%Y %H:%M")


def _news_link(item: dict) -> str:
    """หัวข้อข่าวเป็นลิงก์ — กัน [ ] ในหัวข้อทำ markdown link พัง."""
    title = " ".join(str(item.get("title") or "").split()) or "(ไม่มีหัวข้อ)"
    title = title.replace("[", "(").replace("]", ")")
    url = str(item.get("url") or "").strip()
    return f"[{title}]({url})" if url else title


def _render_news_source_status(sources: list[dict]) -> None:
    """บอกตรง ๆ ว่าแหล่งไหนดึงไม่ได้ — 'ดึงไม่สำเร็จ' ต้องไม่ถูกอ่านเป็น 'ไม่มีข่าว'."""
    failed = [s for s in sources if s["status"] == STATUS_ERROR]
    if failed:
        for s in failed:
            st.warning(f"ดึงจาก {s['name']} ไม่สำเร็จ: {s['detail']} — รายการด้านล่างจึงไม่ครบ")
    off = [s for s in sources if s["status"] == STATUS_OFF]
    ok = [s for s in sources if s["status"] not in (STATUS_ERROR, STATUS_OFF)]
    # จำนวนที่ถูก "ตัดเพราะไม่เกี่ยวกับกองนี้" ต้องขึ้นจอ ไม่ใช่หายไปเงียบ ๆ — ผู้ใช้ต้อง
    # แยกออกว่า "แหล่งนี้มีข่าว 3 ชิ้น" กับ "แหล่งนี้ส่งมา 20 แล้วเราตัดทิ้ง 17" ต่างกัน
    # (``.get`` เพราะสถานะรุ่นเก่าที่ผู้เรียกอื่นประกอบเองอาจยังไม่มีคีย์นี้)
    parts = []
    for s in ok:
        filtered = int(s.get("filtered") or 0)
        parts.append(f"{s['name']} {s['count']}" + (f" (ตัดที่ไม่เกี่ยวออก {filtered})" if filtered else ""))
    caption = "แหล่งที่ดึงได้: " + (" · ".join(parts) if parts else "ไม่มี")
    if off:
        caption += " | ปิดอยู่ (ไม่ได้ตั้ง key): " + ", ".join(s["name"] for s in off)
    st.caption(caption)


def _render_news_items(items: list[dict], empty_text: str) -> None:
    if not items:
        st.caption(empty_text)
        return
    for item in items:
        source = str(item.get("source") or "-")
        st.markdown(f"- {_news_link(item)}")
        st.caption(f"　{source} · {_format_news_time(str(item.get('published_at') or ''))}")


def render_news_page() -> None:
    """ข่าวราย ETF — ดึงตรงจากแหล่ง ไม่ผ่าน AI และไม่ผ่านฐานข้อมูล."""
    st.header("News")
    st.caption(
        "ข่าวราย ETF ดึงตรงจากแหล่ง — ไม่เรียก AI จึงไม่มีค่าใช้จ่าย และไม่ต้องรอ scheduled job · "
        "ข่าวเป็นบริบทประกอบการอ่านสถานการณ์เท่านั้น **ไม่เข้าเลขคะแนนและไม่เข้าการจัดสรร DCA** (invariant ของระบบ)"
    )

    tickers = get_tickers()
    if not tickers:
        st.warning("ยังไม่ได้ตั้งรายการ ETF — เพิ่มได้ที่หน้า Settings")
        return

    symbol = st.radio("เลือก ETF", tickers, horizontal=True, key="news_symbol")
    if st.button("โหลดข่าวใหม่", key="news_refresh"):
        cached_news.clear()
        st.rerun()

    try:
        with st.spinner(f"กำลังดึงข่าวของ {symbol}..."):
            result = cached_news(symbol)
    except NewsSourcesUnavailable as exc:
        # ห้ามแสดง "ไม่มีข่าว" ในกรณีนี้เด็ดขาด — มันคือดึงไม่สำเร็จ ไม่ใช่ไม่มีข่าว
        st.error(f"ดึงข่าวของ {symbol} ไม่สำเร็จทุกแหล่ง: {exc}")
        st.info("ยังไม่ทราบว่ามีข่าวหรือไม่ — กด 'โหลดข่าวใหม่' อีกครั้งในอีกสักครู่")
        return

    _render_news_source_status(result["sources"])

    items = result["items"]
    news_items = [a for a in items if a.get("kind") == KIND_NEWS]
    social_items = [a for a in items if a.get("kind") == KIND_SOCIAL]

    st.subheader(f"ข่าว ({len(news_items)})")
    _render_news_items(news_items, f"ไม่มีข่าวใหม่ของ {symbol} จากแหล่งที่ดึงได้")

    st.subheader(f"โซเชียล ({len(social_items)})")
    st.caption("โพสต์ของนักลงทุนรายย่อย — ไม่ใช่รายงานข่าว และไม่ผ่านการตรวจสอบ")
    _render_news_items(social_items, "ไม่มีโพสต์ในช่วงนี้")


def _render_sentiment_context_box() -> None:
    """ข่าว/sentiment เป็นบริบทข้าง ๆ (Roadmap ข้อ 8) — ห้ามเข้าเลขคะแนน/จัดสรร (invariant)."""
    summaries = get_latest_sentiment_summaries(get_tickers())
    if summaries is None:
        st.caption(
            "บริบทข่าว/sentiment: ไม่มีข้อมูล (ไม่ได้ตั้ง DATABASE_URL หรือเชื่อมต่อไม่ได้) — ไม่กระทบคะแนนใด ๆ · "
            "อ่านพาดหัวข่าวสด ๆ ได้ที่หน้า News (ไม่ต้องใช้ฐานข้อมูลและไม่มีค่าใช้จ่าย)"
        )
        return
    if not summaries:
        # "รอ scheduled job รอบถัดไป" คือคำสัญญาที่ไม่มีวันเกิด (AUDIT_ROUND2_2026-08-07):
        # งาน sentiment เรียก LLM จริงต่อบทความ จึง **ปิดโดยดีฟอลต์** ทั้งสองทาง —
        # step รายสัปดาห์ใน GitHub Actions รันเฉพาะเมื่อตั้ง repository variable
        # ``VAULTIS_SENTIMENT_ENABLED=1`` และไม่มี scheduler ในเครื่อง/Docker ตัวไหน
        # เรียก ``run_sentiment_job()`` เลยสักตัว ⇒ ฐานจะว่างตลอดไปจนกว่าจะสั่งเอง
        # ต้องบอกว่า "ปิดอยู่ + เปิดยังไง" ไม่ใช่ให้ผู้ใช้นั่งรอรอบที่ไม่มีวันมา
        st.caption(
            "บริบทข่าว/sentiment: ยังไม่มีข้อมูลในฐาน — **งานวิเคราะห์ sentiment ปิดอยู่** "
            "(เรียก LLM จริงทุกบทความ = มีค่าใช้จ่าย) ไม่มีงานอัตโนมัติตัวไหนในเครื่องนี้รันมันเลย "
            "จึงไม่ใช่การรอรอบถัดไป · เปิดใน GitHub Actions ด้วย repository variable "
            "`VAULTIS_SENTIMENT_ENABLED=1` (ทุกวันจันทร์ = ยอมจ่าย) หรือรันเองครั้งเดียวด้วย "
            "`VAULTIS_LLM_AUTO=1` + `DATABASE_URL` แล้วเรียก "
            "`python -c \"from analysis.sentiment_analyzer import run_sentiment_job; run_sentiment_job()\"` · "
            "ระหว่างนี้อ่านพาดหัวข่าวสด ๆ ได้ที่หน้า News (ฟรี ไม่ผ่าน AI ไม่ต้องใช้ฐานข้อมูล)"
        )
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

    # ชื่อโมเดลอ่านจาก analysis/llm.py (แหล่งเดียว) — เขียนตายตัวแล้วเคยค้างเป็น Haiku 4.5
    # ทั้งที่ระบบย้ายไป Sonnet 5 (แพงกว่า ~3 เท่า) = บอกต้นทุนผู้ใช้ผิด
    st.caption(
        "ปุ่มซ้าย: คำนวณทุกอย่างในระบบ ไม่เรียก AI ไม่มีค่าใช้จ่าย | "
        f"ปุ่มขวา: เรียก {ANTHROPIC_MODEL} มาอธิบายเพิ่ม — "
        "มีค่าใช้จ่ายจริงตามจำนวนโทเคน (ระบบ log ต้นทุนโดยประมาณทุกครั้งที่เรียก)"
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


def _ledger_report_rows(source: object, key: str) -> list[dict]:
    """อ่านรายงานรายแถวชุดใดชุดหนึ่งของ ``portfolio/tracker.py``.

    รับได้ทั้ง dict สรุป (คีย์ตรง ๆ) และ DataFrame ที่ tracker แนบไว้ที่ ``.attrs``
    ``key`` = ``skipped_rows`` (ถูกตัดออก) · ``derived_fx_rows`` (อัตราถูกคำนวณย้อน)
    · ``inconsistent_rows`` (ยอดบาทขัดกับอัตราที่บันทึก) — **ห้ามยุบรวมกัน**
    เพราะสามชุดนี้แปลว่าคนละเรื่อง (ตัดทิ้ง / ซ่อมแล้วยังนับ / นับอยู่แต่น่าสงสัย)
    """
    if isinstance(source, dict):
        return list(source.get(key) or [])
    attrs = getattr(source, "attrs", None) or {}
    return list(attrs.get(key) or [])


def _ledger_skipped_rows(source: object) -> list[dict]:
    """แถวสมุดบัญชีที่ ``portfolio/tracker.py`` ตัดทิ้งเพราะข้อมูลไม่ครบ (FIX_PLAN ข้อ 1.2).

    รับได้ทั้ง dict สรุป (คีย์ ``skipped_rows``) และ DataFrame ที่ tracker แนบไว้ที่
    ``.attrs['skipped_rows']`` — ข้อมูลที่ "ถูกตัด" ต้องเดินทางมาถึงหน้าจอเสมอ
    ห้ามหายกลางทาง (ตัดเงียบ = ผิดกฎ fail-loud เท่ากับกุตัวเลข)
    """
    return _ledger_report_rows(source, "skipped_rows")


def _render_ledger_skipped_rows(skipped_rows: list[dict], reason: str = "") -> None:
    """เตือนว่าตัวเลขด้านบน **ไม่รวม** แถวเหล่านี้ พร้อมบอกรายแถวว่าขาดอะไร.

    ``reason`` คือข้อความสรุปที่ tracker ส่งมากับ ``skipped_reason``
    (ถ้าไม่มีก็ประกอบข้อความเองจากจำนวนแถว — ห้ามเงียบเด็ดขาด)
    """
    if not skipped_rows:
        return
    st.error(
        reason
        or f"ข้ามธุรกรรม {len(skipped_rows)} แถวเพราะข้อมูลไม่ครบ — ตัวเลขสรุปไม่รวมแถวเหล่านี้"
    )
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "tx_id": str(row.get("tx_id") or ""),
                    "วันที่": str(row.get("date") or "ไม่ทราบ"),
                    "ETF": str(row.get("ticker") or "ไม่ทราบ"),
                    "ช่องที่ขาด": ", ".join(str(f) for f in (row.get("missing_fields") or [])),
                    "เหตุผล": str(row.get("reason") or ""),
                }
                for row in skipped_rows
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )
    st.caption(
        "แก้ไขแถวเหล่านี้ใน `portfolio/data/transactions.csv` (ใส่ค่าที่ขาดให้ครบ) "
        "แล้วกด Refresh Data — ระบบไม่เดาค่าแทนให้ เพราะจะทำให้กำไร/ขาดทุนผิดแบบเงียบ ๆ"
    )


def _render_ledger_derived_fx_rows(rows: list[dict], reason: str = "") -> None:
    """แถวที่อัตราแลกเปลี่ยน **ถูกคำนวณย้อนมาแทน** ค่าที่บันทึกไว้ (C1.3).

    ต่างจาก ``skipped_rows`` ตรงที่แถวเหล่านี้ **ยังอยู่ในทุกตัวเลข** — เดิมมีแค่
    ``logger.warning`` ผู้ใช้จึงไม่มีทางรู้ว่าเงินที่เห็นคิดจากอัตราที่ระบบหาเอง
    """
    if not rows:
        return
    st.warning(
        reason
        or (
            f"อัตราแลกเปลี่ยน {len(rows)} แถวถูกคำนวณย้อนจากยอดเงินบาท "
            "เพราะค่าที่บันทึกไว้ใช้ไม่ได้ (ตัวเลขด้านล่างรวมแถวเหล่านี้อยู่)"
        )
    )
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "tx_id": str(row.get("tx_id") or ""),
                    "วันที่": str(row.get("date") or "ไม่ทราบ"),
                    "ETF": str(row.get("ticker") or "ไม่ทราบ"),
                    "อัตราที่บันทึกไว้": row.get("recorded_fx"),
                    "อัตราที่ใช้จริง": row.get("used_fx"),
                    "เหตุผล": str(row.get("reason") or ""),
                }
                for row in rows
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )
    st.caption(
        "แถวเหล่านี้ยังถูกนับในเงินลงทุน/กำไรทั้งหมด — ถ้าอัตราที่ระบบคำนวณย้อนไม่ใช่อัตราที่คุณจ่ายจริง "
        "ให้แก้ `fx_rate_thb` ในสมุดแล้วกด Refresh Data"
    )


def _render_ledger_inconsistent_rows(rows: list[dict], reason: str = "") -> None:
    """แถวที่ยอดเงินบาทที่จ่ายจริง **ขัดกับ** จำนวนหุ้น × ราคา × อัตรา + ค่าธรรมเนียม (C1.2).

    เตือนอย่างเดียว ไม่ตัดทิ้ง — ข้อมูลครบและระบบบันทึกตามที่ผู้ใช้บอก
    ตัวจุดชนวนที่พบบ่อยคือการย้อนบันทึกไม้เก่าด้วยอัตราแลกเปลี่ยน "วันนี้"
    """
    if not rows:
        return
    st.warning(
        reason
        or (
            f"ยอดเงินบาทของ {len(rows)} แถวไม่ตรงกับ จำนวนหุ้น × ราคา × อัตราแลกเปลี่ยน "
            "(ตัวเลขด้านล่างยังนับแถวเหล่านี้อยู่ ให้ตรวจสอบอัตราที่บันทึกไว้)"
        )
    )
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "tx_id": str(row.get("tx_id") or ""),
                    "วันที่": str(row.get("date") or "ไม่ทราบ"),
                    "ETF": str(row.get("ticker") or "ไม่ทราบ"),
                    "ยอดที่บันทึก (บาท)": row.get("amount_thb"),
                    "ยอดที่ควรเป็น (บาท)": row.get("implied_amount_thb"),
                    "อัตราที่บันทึก": row.get("recorded_fx"),
                    "อัตราที่คำนวณย้อน": row.get("implied_fx"),
                    "ต่างกัน (%)": row.get("diff_pct"),
                }
                for row in rows
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )
    st.caption(
        "ถ้าอัตราที่คำนวณย้อนคือค่าที่ถูก ให้แก้ `fx_rate_thb` ของแถวนั้นในสมุด — "
        "ระบบไม่แก้ให้เอง เพราะยอดเงินที่คุณกรอกอาจถูกและตัวเลขอื่นผิดแทน"
    )


def _render_ledger_reports(source: object) -> None:
    """แสดงรายงานสมุดบัญชีครบทั้งสามชุดจากที่เดียว (ตัด / ซ่อม / ขัดกันเอง).

    ทั้งสามชุดมาคู่กันจาก ``portfolio/tracker.py`` เสมอ ถ้าหน้าจอแสดงแค่ชุดเดียว
    อีกสองชุดจะเงียบสนิททั้งที่มีข้อความไทยพร้อมใช้อยู่แล้ว
    """

    def _reason(key: str) -> str:
        return str(source.get(key) or "") if isinstance(source, dict) else ""

    _render_ledger_skipped_rows(
        _ledger_report_rows(source, "skipped_rows"), _reason("skipped_reason")
    )
    _render_ledger_derived_fx_rows(
        _ledger_report_rows(source, "derived_fx_rows"), _reason("derived_fx_reason")
    )
    _render_ledger_inconsistent_rows(
        _ledger_report_rows(source, "inconsistent_rows"), _reason("inconsistent_reason")
    )


def _render_dividend_section_header(dividend_summary: dict) -> bool:
    """เปิดหัวข้อปันผลพร้อมเตือนแถวที่ถูกตัด — คืน ``True`` ถ้าเปิดหัวข้อแล้ว.

    ต้องเปิดหัวข้อ**แม้ ``count == 0``** เมื่อมีแถวถูกตัด เพราะกรณีที่แถวปันผล
    ถูกตัดทั้งหมดคือกรณีที่ผู้ใช้ต้องรู้มากที่สุด — ยอดที่เห็นเป็น 0 ทั้งที่
    บันทึกปันผลไว้จริง ถ้าซ่อนทั้งบล็อกตาม ``count`` ข้อมูลที่หายไปจะเงียบสนิท
    (FIX_PLAN ข้อ 1.2 / รอบเก็บกวาด C1)
    """
    skipped_rows = _ledger_skipped_rows(dividend_summary)
    if int(dividend_summary.get("count") or 0) <= 0 and not skipped_rows:
        return False
    st.subheader("ปันผลรับจริง (สุทธิหลังภาษี)")
    _render_ledger_skipped_rows(
        skipped_rows, str(dividend_summary.get("skipped_reason") or "")
    )
    return True


def _tracked_target_weights() -> dict[str, float]:
    """สัดส่วนเป้าหมาย **ของทุกกองที่ระบบติดตาม** — สูตรเดียวของทั้งหน้าจอนี้.

    ทั้งโหมด "ดึงพอร์ตเข้าเป้า" และกล่อง drift ต้องเรียกผ่านที่นี่เท่านั้น
    เพราะ ``get_target_weights()`` **normalize ให้รวมเป็น 1.0 บนรายชื่อที่ส่งเข้าไป**
    ส่งเฉพาะกองที่ถืออยู่ = เป้าถูกขยายใหม่บนเซ็ตย่อยนั้น (ถือ VOO+SCHD อยู่ →
    SCHD กลายเป็น 41.7% ทั้งที่ตั้งไว้ 25%) แล้วกองที่ยังไม่เคยซื้อจะหายจากทั้ง
    ตัวหารและตารางผลลัพธ์ ⇒ **ไม่มีวันได้เงิน** และเลขบนจอไม่ตรงกับหน้า Settings
    ซึ่งเป็นบั๊กที่ ``portfolio/targets.py`` ถูกสร้างมาแก้ตั้งแต่แรก
    (AUDIT_2026-08-06 C2 / FIX_PLAN 3.4 · ฝั่ง backend แก้ไปแล้วที่ rebalance_service)

    โยน :class:`InvalidTargetWeights` ต่อให้ผู้เรียก — คอนฟิกเป้าหมายผิดรูปคือ
    "ไม่รู้เป้าหมาย" ห้ามเดาแทนแล้วทำแผนเงินต่อ
    """
    tracked = [str(t).strip().upper() for t in get_tickers() if str(t).strip()]
    return get_target_weights(tracked)


def _render_invalid_target_weights(exc: Exception | None) -> None:
    """ข้อความเดียวที่ใช้ร่วมกันเมื่อ ``portfolio.target_weights`` **ผิดรูปจริง ๆ**.

    ใช้กับ :class:`InvalidTargetWeights` เท่านั้น — ข้อความนี้ชี้ให้ผู้ใช้ไปแก้ config.json
    ซึ่งถูกต้องเมื่อคอนฟิกผิดจริง แต่**ผิดที่**เมื่อสาเหตุคือดึงราคาไม่สำเร็จ
    (:class:`NoTargetForSubset` — ใช้ :func:`_render_no_target_for_subset` แทน
    AUDIT_ROUND2_2026-08-07 G1)
    """
    reason = str(exc) if exc is not None else "ไม่ทราบสาเหตุ"
    st.error(
        f"สัดส่วนพอร์ตเป้าหมายใน config.json ใช้ไม่ได้ ({reason}) — "
        "ยังเทียบพอร์ตกับเป้าหมายไม่ได้ (แก้ `portfolio.target_weights` แล้วกด Refresh Data)"
    )
    st.info("แก้ได้ที่หน้า **Settings** (เมนูซ้าย) ซึ่งมีตารางเทียบเป้าหมาย preset กับที่ใช้จริง")


def _render_no_target_for_subset(exc: NoTargetForSubset) -> None:
    """ดึงราคาไม่สำเร็จ ≠ คอนฟิกผิด — ข้อความต้องชี้ไปที่ข้อมูล ไม่ใช่ config.json.

    AUDIT_ROUND2_2026-08-07 G1: ``calculate_allocation()`` ส่งเฉพาะ ticker ที่ดึงราคา
    สำเร็จเข้า ``get_target_weights(..., partial=True)`` วันไหนตัวที่ถือน้ำหนักดึงราคา
    ไม่ได้ จะเหลือแต่ตัวที่ผู้ใช้ตั้ง ``0`` ไว้ตั้งใจ ⇒ ไม่มีน้ำหนักให้จัดสรร
    **แต่ config.json ถูกต้องทุกบรรทัด** ถ้าหน้าจอบอกให้ไปแก้คอนฟิก ผู้ใช้จะลบเจตนา
    "ตั้งใจไม่ถือตัวนี้" ทิ้งไปแก้ปัญหาที่ yfinance เป็นคนก่อ
    """
    requested = ", ".join(exc.requested) or "—"
    missing = ", ".join(exc.missing) or "—"
    st.error(
        f"ยังจัดสรรงบเดือนนี้ไม่ได้ เพราะ **ดึงราคาไม่สำเร็จ** ไม่ใช่เพราะคอนฟิกผิด — "
        f"ตัวที่มีข้อมูลรอบนี้ ({requested}) ถูกตั้งเป้าหมายไว้ 0% ทั้งหมด "
        f"ส่วนตัวที่ถือน้ำหนักอยู่ ({missing}) ดึงราคาไม่ได้"
    )
    st.info(
        "ไม่ต้องแก้ `portfolio.target_weights` — สัดส่วนที่ตั้งไว้ถูกต้องแล้ว "
        "รอบที่ข้อมูลราคาครบจะจัดสรรได้เอง (กด Refresh Data อีกครั้งในอีกสักครู่)"
    )


def _render_target_weights_problem(exc: Exception | None) -> None:
    """ทางเข้าเดียวของหน้าจอเมื่ออ่านสัดส่วนเป้าหมายไม่ได้ — เลือกข้อความตาม **ชนิด** ของสาเหตุ.

    "คอนฟิกผิด" กับ "ดึงราคาไม่สำเร็จ" คนละเรื่องกันและต้องบอกคนละอย่าง
    (AUDIT_ROUND2_2026-08-07 CRITICAL/G1) — รวมไว้ที่นี่ที่เดียวเพื่อไม่ให้แต่ละหน้า
    เดาชนิดเอง แล้วชี้ผู้ใช้ไปแก้ไฟล์ที่ไม่ได้ผิด
    """
    if isinstance(exc, NoTargetForSubset):
        _render_no_target_for_subset(exc)
    else:
        _render_invalid_target_weights(exc)


def _unpriced_tickers(holdings_df: pd.DataFrame) -> list[str]:
    """ticker ที่ถืออยู่แต่ดึงราคาไม่สำเร็จ — มีแม้ตัวเดียว = เทียบสัดส่วนพอร์ตไม่ได้.

    ตัวหารของทุกสัดส่วน (มูลค่าพอร์ตรวม) ต้องมาจากราคาครบทุกตัว
    ถ้าตัดตัวที่ราคาหายทิ้งเงียบ ๆ ตัวที่เหลือจะดู overweight ทันที
    ซึ่งเป็นบั๊กเดียวกับที่ ``backend/services/rebalance_service.py`` แก้ไปในข้อ 1.3
    """
    if holdings_df.empty or "Price OK" not in holdings_df.columns:
        return []
    unpriced = holdings_df[~holdings_df["Price OK"].astype(bool)]
    return [str(t) for t in unpriced["Ticker"]]


def _priced_rows(holdings_df: pd.DataFrame) -> pd.DataFrame:
    """เฉพาะกองที่ **ดึงราคาวันนี้ได้** — สมุดว่าง/ไม่มีคอลัมน์ = ไม่มีสักแถว (ไม่ใช่ error)."""
    if holdings_df.empty or "Price OK" not in holdings_df.columns:
        return holdings_df.iloc[0:0]
    return holdings_df[holdings_df["Price OK"].astype(bool)]


def _upper_set(values) -> set[str]:
    """ชุด ticker ตัวพิมพ์ใหญ่ — ledger กับตารางพอร์ตต้องเทียบกันด้วยรูปเดียวกัน."""
    return {str(v).strip().upper() for v in values}


#: อายุขั้นต่ำของพอร์ตก่อนที่ตัวเลข "ต่อปี" จะมีความหมาย — ใช้ทั้งกับด่าน XIRR และกับคำเตือน
#: "ยังไม่มีแถวปันผลเลย" (ต่ำกว่านี้ผลของปันผลที่ยังไม่ถูกบันทึกอยู่ใต้ ~0.4 จุด จึงยังไม่ใช่
#: สิ่งที่ต้องรบกวนผู้ใช้ และหน้าจอก็บอกอยู่แล้วว่าพอร์ตยังใหม่เกินกว่าจะตีเป็น %ต่อปี)
_BENCHMARK_MIN_AGE_DAYS = 90


def _render_mix_vs_voo_significance(priced: pd.DataFrame, prices: pd.DataFrame) -> None:
    """ตอบ "ส่วนผสมนี้ดีกว่า VOO จริงไหม" พร้อมช่วงความเชื่อมั่น — ไม่ใช่ตัวเลขจุดเดียว.

    สามช่องด้านบนตอบคำถาม "สมุดของฉันจบที่เท่าไรเทียบกับเงาที่ซื้อ VOO" ซึ่งเป็นผลที่
    **เกิดขึ้นแล้วหนึ่งเส้นทาง** (money-weighted จากไม้จริง) — มันไม่ได้บอกว่าส่วนต่างนั้น
    ใหญ่กว่าความผันผวนของตัวมันเองหรือเปล่า ⇒ ถ้าปล่อยไว้ลอย ๆ ผู้ใช้จะอ่านเป็นคำตัดสินว่า
    กลยุทธ์ดีกว่า/แย่กว่า แล้วเปลี่ยนพอร์ตตามเสียงรบกวน (FIX_PLAN เฟส 4②)

    ที่นี่จึงทดสอบคำถามที่ถูกต้อง: **paired t-test บนผลตอบแทนรายเดือน** ของส่วนผสมปัจจุบัน
    เทียบ VOO · "แยกไม่ออกจากศูนย์" ต้อง**พูดออกมาตรง ๆ** และไม่ใช่คำเดียวกับ "เท่ากัน"
    """
    weights: dict[str, float] = {}
    for _, row in priced.iterrows():
        ticker = str(row.get("Ticker") or "").strip().upper()
        value = _to_number(row.get("Current Value (USD)"))
        if ticker and value is not None and value > 0:
            weights[ticker] = value
    if not weights:
        return
    try:
        stats = mix_vs_benchmark_test(prices, weights, benchmark="VOO")
    except ValueError as exc:
        st.caption(f"ยังทดสอบนัยสำคัญของส่วนต่างไม่ได้: {exc}")
        return

    diff = float(stats["diff_annual_pct"])
    se = float(stats["se_annual_pct"])
    low, high = float(stats["ci95_low_pct"]), float(stats["ci95_high_pct"])
    head = (
        f"ส่วนต่างในสามช่องด้านบนคือผลที่**เกิดขึ้นแล้ว**ของสมุดคุณหนึ่งเส้นทาง — "
        "คำถามว่า *ส่วนผสมนี้ดีกว่า VOO จริงไหม* ต้องตอบด้วยผลตอบแทนรายเดือนย้อนหลัง "
        f"(ส่วนผสมปัจจุบัน สมมติปรับสมดุลรายเดือน · {stats['n_periods']} เดือน "
        f"{stats['overlap_start']} ถึง {stats['overlap_end']}): "
    )
    if se == 0.0:
        st.caption(
            head + "ส่วนผสมของคุณคือ VOO ล้วน จึงไม่มีส่วนต่างให้ทดสอบ (เท่ากันทุกเดือนโดยนิยาม)"
        )
        return
    body = f"**{diff:+.2f}%/ปี** · CI95 **[{low:+.2f}, {high:+.2f}]**"
    if stats["distinguishable_from_zero"]:
        st.info(
            head + body + " ⇒ ส่วนต่างนี้**ต่างจากศูนย์อย่างมีนัยสำคัญ** "
            "(ยังเป็นสถิติอดีต ไม่ใช่การรับประกันอนาคต)"
        )
        return
    tail = " ⇒ **แยกไม่ออกจากศูนย์** — ข้อมูลเท่านี้ยังตอบไม่ได้ว่าดีกว่าหรือแย่กว่า "
    years_needed = stats["years_needed"]
    if years_needed is not None:
        tail += (
            f"(ต้องมีข้อมูลราว {float(years_needed):,.0f} ปีถึงจะสรุปผลขนาดนี้ได้ · "
            f"เล็กสุดที่ข้อมูลชุดนี้จับได้คือ {float(stats['mde_annual_pct']):.2f}%/ปี) "
        )
    tail += "**ห้ามอ่านว่า 'เท่ากัน'** และห้ามเปลี่ยนพอร์ตเพราะตัวเลขในกำแพงเสียงรบกวนนี้"
    st.warning(head + body + tail)


def _zero_safe(value: float, digits: int = 2) -> float:
    """ปัดที่ความละเอียดที่จะแสดงจริง แล้วกลบ ``-0.0`` ทิ้ง.

    ``f"{-4.5e-13:+,.2f}"`` ให้ ``"-0.00"`` ซึ่งผู้ใช้อ่านว่า "ขาดทุน" ทั้งที่ค่าที่แสดงคือศูนย์
    — เครื่องหมายมาจากเศษของ float ล้วน ๆ ไม่ได้มาจากข้อมูล และเคสที่เกิดคือเคสที่สำคัญที่สุด
    ของหัวข้อนี้พอดี: สมุดที่ซื้อ VOO ล้วนต้องได้ส่วนต่าง **ศูนย์** (FIX_PLAN ข้อ 3.1)
    ``-0.0 + 0.0 == 0.0`` ตามมาตรฐาน IEEE 754 ส่วนค่าที่ปัดแล้วไม่เป็นศูนย์ไม่ถูกแตะเลย
    """
    return round(float(value), digits) + 0.0


def _render_benchmark_section(holdings_df: pd.DataFrame) -> None:
    """ชนะ VOO ไหม + %/ปี money-weighted (Roadmap ข้อ 14) — เทียบเงินก้อนเดียวกัน วันเดียวกัน.

    **สองขาต้องอยู่บนไม้ชุดเดียวกันเสมอ** (AUDIT_ROUND2_2026-08-07 T3) — เดิม
    ``actual_value_usd`` นับเฉพาะกองที่ดึงราคาวันนี้ได้ แต่ ``invested_usd`` /
    ``benchmark_value_usd`` มาจาก ``shadow_benchmark(buys, ...)`` ซึ่งนับ **ทุกไม้**
    รวมไม้ของกองที่วันนี้ไม่มีราคา ⇒ ทั้ง %, มูลค่าเงา และ "ส่วนต่าง" เทียบคนละฐาน
    (เคสจริง: จอพิมพ์ −22.44% และแพ้ VOO 1,167 ดอลลาร์ ทั้งที่ฐานเดียวกันคือ +15.76%
    และแพ้ 125 ดอลลาร์ — เพี้ยน 9 เท่า) จึงกรองไม้ของกองที่ไม่มีราคาออกจาก**ทั้งสองขา**
    แล้วบอกผู้ใช้ว่าตัดกองไหนออก

    **และ "ไม้ชุดเดียวกัน" ต้องรวมขาปันผลด้วย** (FIX_PLAN ข้อ 3.1) — สามอาการที่ปิดพร้อมกัน
    ด้วยโมเดลเดียว คือ *กระแสเงินเข้า-ออกจากภายนอกชุดเดียวกัน*:

    - **เทียบคนละฐาน** ขาเงาใช้ ``cached_prices`` = Adjusted Close = total return
      (ปันผลของ VOO ถูกลงทุนต่อให้เองในตัวเลข) ส่วนขาพอร์ตจริงคือ ``Current Value (USD)``
      = ``หุ้น × ราคาปิดดิบ`` ปันผลที่รับเป็นเงินสดไม่ถูกนับกลับ วัดจริง 2026-08-08:
      VOO 3 ปี total **79.29%** vs price **72.38%** ⇒ **เอียงเข้าข้าง VOO 1.58 จุด/ปี
      ตลอดเวลา** สมุดที่ซื้อ VOO ล้วนวันเดียวกันเป๊ะยังโชว์ว่าแพ้ VOO
    - **DRIP ถูกนับเป็นเงินใหม่** ไม้ที่ซื้อด้วยเงินปันผลถูกเหมารวมเป็น "เงินเข้าจากภายนอก"
      ⇒ ขาเงาพองเกินจริง และ ``invested_usd`` ที่พองยังไปเป็นตัวหารของ %พอร์ตจริงอีกชั้น
      — ผู้ใช้ที่ลงทุนปันผลต่ออย่างมีวินัยที่สุดถูกลงโทษหนักที่สุด
    - **ตัวหารต้องเป็นเงินสุทธิจากภายนอก** ``net_external_usd`` = ไม้ซื้อ − ปันผลที่รับ
      (ไม้ DRIP หักล้างตัวเองพอดี) ไม่ใช่ผลรวมไม้ซื้อดิบ ๆ

    ทุก **ไม้ซื้อ** = เงินเข้า → เงาซื้อ VOO วันเดียวกันจำนวนเดียวกัน · ทุก **ปันผลที่รับ**
    = เงินที่พอร์ตจริงคายออกมา → เงา**ขาย** VOO มูลค่าเท่ากันวันเดียวกัน · แล้วเทียบเฉพาะ
    มูลค่าปลายทาง (แท่งล่าสุดของ Adj Close เท่ากับ Close เป๊ะ สองขาจึงจบบนฐานราคาเดียวกัน)

    และ "ไม่รู้มูลค่า" ห้ามกลายเป็น ``0.00`` / ``−100%`` (AUDIT.md C1) — ดึงราคาไม่ได้
    สักกอง = ไม่แสดงกล่องเทียบ ไม่ใช่แสดงว่าพอร์ตเหลือศูนย์
    """
    st.divider()
    st.subheader("ชนะ VOO ไหม (เงินก้อนเดียวกัน วันเดียวกัน)")

    transactions = get_transactions()
    # แถวที่ถูกตัดไม่ได้เข้าทั้งขาพอร์ตจริงและขา VOO เงา — ต้องบอก ไม่งั้น "ชนะ/แพ้" จะอ่านผิด
    ledger_skipped = _ledger_skipped_rows(transactions)
    if ledger_skipped:
        st.warning(
            f"ไม่รวม {len(ledger_skipped)} ไม้ที่ข้อมูลในสมุดบัญชีไม่ครบ — "
            "ตัวเลขเทียบ VOO และ %ต่อปีด้านล่างคิดจากไม้ที่เหลือเท่านั้น (รายละเอียดอยู่ด้านบนของหน้า)"
        )
    buys = transactions[
        (transactions["tx_type"] != TX_DIVIDEND)
        & (pd.to_numeric(transactions["shares"], errors="coerce") > 0)
    ]
    if buys.empty:
        if ledger_skipped:
            st.caption("ไม้ซื้อทุกรายการถูกตัดเพราะข้อมูลไม่ครบ — ยังเทียบ VOO ไม่ได้ (ไม่ใช่ 'ไม่มีรายการซื้อ')")
        else:
            st.caption("ยังไม่มีรายการซื้อใน ledger — ไม่มีอะไรให้เทียบ")
        return

    # ขาพอร์ตจริงตีมูลค่าได้เฉพาะกองที่มีราคาวันนี้ ⇒ ขา VOO เงาต้องสร้างจากไม้ของกอง
    # ชุดเดียวกันเท่านั้น ไม่งั้น "ส่วนต่าง" คือการลบเลขคนละฐาน (T3)
    priced = _priced_rows(holdings_df)
    if priced.empty:
        st.warning(
            "ดึงราคาปัจจุบันไม่ได้สักกอง — ยังไม่รู้มูลค่าพอร์ตวันนี้ จึงเทียบกับ VOO ไม่ได้ "
            "(นี่คือ **ไม่รู้** ไม่ใช่ 'พอร์ตเหลือ 0 บาท' และไม่ใช่ขาดทุน 100%) "
            "— ลองกด Refresh Data อีกครั้งในอีกสักครู่"
        )
        return
    priced_tickers = _upper_set(priced["Ticker"])
    comparable = buys[buys["ticker"].map(lambda t: str(t).strip().upper() in priced_tickers)]
    dropped_from_both = sorted(_upper_set(buys["ticker"]) - priced_tickers)
    if comparable.empty:
        st.warning(
            "ไม้ซื้อทุกรายการเป็นของกองที่ดึงราคาวันนี้ไม่ได้ "
            f"({', '.join(dropped_from_both) or 'ไม่ทราบ'}) — ยังเทียบกับ VOO ไม่ได้"
        )
        return

    # ปันผลต้องถูกกรองด้วย **เกณฑ์เดียวกับไม้ซื้อ** ไม่งั้นขาเงาจะถูกบังคับให้ขายเพื่อจ่าย
    # ปันผลของกองที่ไม่เคยได้เงินซื้อในขาเงาเลย = โทษ VOO ด้วยเงินที่มันไม่เคยได้รับ
    # (ตรงข้ามกับบั๊กเดิมพอดี แต่ผิดกฎ "ไม้ชุดเดียวกัน" ข้อเดียวกัน)
    dividends = get_dividends()
    comparable_dividends = (
        dividends[dividends["ticker"].map(lambda t: str(t).strip().upper() in priced_tickers)]
        if not dividends.empty and "ticker" in dividends.columns
        else dividends
    )

    # อายุของ **ชุดที่เทียบจริง** ไม่ใช่ของทั้งสมุด — ใช้ทั้งกับด่าน %ต่อปี และกับการตัดสินว่า
    # "ไม่มีแถวปันผลเลย" เป็นเรื่องน่าเตือนหรือยัง (พอร์ตที่เพิ่งซื้อสัปดาห์ที่แล้วยังไม่ควรมีปันผล)
    comparable_dates = pd.to_datetime(comparable["date"], errors="coerce").dropna()
    if comparable_dates.empty:
        st.warning("ไม้ซื้อของกองที่เทียบได้ไม่มีวันที่ที่อ่านออกเลย — ยังเทียบกับ VOO ไม่ได้")
        return
    first_buy_date = comparable_dates.min()
    if first_buy_date.tzinfo is not None:
        # ยึดเวลาหน้าปัดเหมือน ``portfolio/benchmark._align_tz`` — ห้าม tz_convert เพราะ
        # มันเลื่อนวันที่ได้ทั้งขึ้นและลง (และ ``Timestamp.today()`` เป็น tz-naive)
        first_buy_date = first_buy_date.tz_localize(None)
    comparable_age_days = int((pd.Timestamp.today() - first_buy_date).days)

    try:
        benchmark_prices = cached_prices(sorted(set(get_tickers()) | {"VOO"}), years=10)
    except PriceDataUnavailableError as exc:
        st.error(f"ดึงราคาไม่สำเร็จ — เทียบ benchmark ไม่ได้: {exc}")
        return
    if "VOO" not in benchmark_prices.columns:
        st.error("ไม่มีข้อมูลราคา VOO — เทียบ benchmark ไม่ได้")
        return

    # ขา VOO เงาต้องสร้างจาก ``comparable`` (ไม้ของกองที่มีราคาวันนี้) ไม่ใช่ ``buys``
    # ทั้งหมด ไม่งั้นตัวตั้ง (พอร์ตจริงเฉพาะกองที่มีราคา) กับตัวหาร (เงินลงทุนทุกไม้)
    # อยู่คนละฐาน แล้ว "ส่วนต่าง" คือการลบเลขคนละเรื่องกัน (T3)
    # ``payouts`` คือขาที่ทำให้การเทียบยุติธรรม: ราคาที่ป้อนเข้าไปเป็น total return
    # แต่พอร์ตจริงตีมูลค่าด้วยราคาล้วน — ไม่บังคับให้เงาคายปันผลออกก้อนเดียวกันวันเดียวกัน
    # ตัวเลขจะเอียงเข้าข้าง VOO ราว 1.6 จุด/ปี ตลอดเวลา (FIX_PLAN ข้อ 3.1)
    try:
        shadow = shadow_benchmark(
            comparable, benchmark_prices["VOO"].dropna(), payouts=comparable_dividends
        )
    except ValueError as exc:
        st.error(f"เทียบไม่ได้: {exc}")
        return
    if shadow["rounds"] == 0:
        st.caption("ไม่มีไม้ซื้อที่เทียบราคา VOO ณ วันซื้อได้")
        return
    if shadow["payouts_skipped"]:
        # ปันผลที่ข้าม ≠ ไม้ซื้อที่ข้าม — ไม้ซื้อที่ข้ามหายจากทั้งตัวตั้งและตัวหาร แต่ปันผล
        # ที่ข้ามแปลว่าเงาเก็บเงินที่ควรคายออกไว้กับตัว = เอียงเข้าข้าง VOO ทางเดียว
        # จึงไม่มีคำตัดสิน "ชนะ/แพ้" ที่เชื่อถือได้ให้แสดง
        st.error(
            f"มีแถวปันผล {shadow['payouts_skipped']} รายการที่ใช้เทียบไม่ได้ "
            f"(ข้อมูลในสมุดใช้ไม่ได้ {shadow['payouts_skipped_bad_row']} · "
            f"ไม่มีราคา VOO ณ วันรับ {shadow['payouts_skipped_no_price']}) — "
            "ข้ามแถวปันผลไปเฉย ๆ จะทำให้ขา VOO เงาเก็บเงินที่ต้องคายออกไว้กับตัว "
            "ตัวเลขจะเอียงเข้าข้าง VOO ทางเดียว จึงไม่แสดงผลเทียบในรอบนี้"
        )
        return

    if dropped_from_both:
        st.warning(
            f"ตัด {', '.join(dropped_from_both)} ออกจาก**ทั้งสองขา** เพราะดึงราคาวันนี้ไม่ได้ — "
            "สามช่องด้านล่างจึงเป็นการเทียบเงินก้อนเดียวกันของ**กองที่เหลือ** ไม่ใช่ทั้งพอร์ต "
            "(ตัดข้างเดียวเมื่อไร ตัวเลขจะผิดทันทีเพราะเทียบคนละฐาน)"
        )

    # "Price OK" แต่มูลค่าอ่านไม่ออกสักแถว = ยังไม่รู้มูลค่ารวม ห้ามให้ `.sum()` ข้าม NaN
    # แล้วได้ยอดที่ดูสมบูรณ์ (ซึ่งจะต่ำกว่าความจริงโดยไม่มีอะไรบอก)
    priced_values_usd = pd.to_numeric(priced["Current Value (USD)"], errors="coerce")
    actual_value_usd = None if priced_values_usd.isna().any() else _to_number(priced_values_usd.sum())
    invested_usd = _to_number(shadow["invested_usd"])
    payout_usd = _to_number(shadow["payout_usd"])
    # ตัวหารของทั้งสองขาคือ **เงินสุทธิจากภายนอก** ไม่ใช่ผลรวมไม้ซื้อ — ไม้ DRIP เข้ามาทาง
    # ``invested_usd`` แล้วถูกปันผลก้อนเดียวกันหักออกตรงนี้พอดี เหลือเฉพาะเงินที่ผู้ใช้
    # ควักจากกระเป๋าจริง ๆ (FIX_PLAN ข้อ 3.1 ข้อย่อย ข)
    net_external_usd = _to_number(shadow["net_external_usd"])
    shadow_value = _to_number(shadow["benchmark_value_usd"])
    if (
        actual_value_usd is None
        or invested_usd is None
        or payout_usd is None
        or net_external_usd is None
        or shadow_value is None
        or invested_usd <= 0
    ):
        # ไม่รู้ตัวใดตัวหนึ่ง = ไม่มีคำตัดสิน "ชนะ/แพ้" ห้ามพิมพ์ 0.00 หรือ −100% แทน (C1)
        st.warning(
            "ตัวเลขที่ใช้เทียบไม่ครบ (มูลค่าพอร์ตวันนี้ หรือเงินลงทุนของขา VOO เงาอ่านไม่ได้) — "
            "ยังเทียบกับ VOO ไม่ได้ · นี่คือ **ไม่รู้** ไม่ใช่ 0 บาท และไม่ใช่ขาดทุน 100%"
        )
        return

    bench_col1, bench_col2, bench_col3 = st.columns(3)
    if net_external_usd > 0:
        actual_pct = (actual_value_usd / net_external_usd - 1.0) * 100.0
        shadow_pct = (shadow_value / net_external_usd - 1.0) * 100.0
        actual_delta: str | None = f"{_zero_safe(actual_pct):+.2f}%"
        shadow_delta: str | None = f"{_zero_safe(shadow_pct):+.2f}%"
    else:
        # ถอนปันผลออกมามากกว่าเงินที่ใส่เข้าไป = ตัวหารเป็นศูนย์/ติดลบ %ที่ได้ไม่มีความหมาย
        # (และเครื่องหมายพลิกได้) — แสดงมูลค่ากับส่วนต่างซึ่งยังเทียบกันได้ แล้วบอกเหตุผล
        actual_delta = shadow_delta = None
    bench_col1.metric("พอร์ตจริง (USD)", f"{actual_value_usd:,.2f}", delta=actual_delta)
    bench_col2.metric("ถ้าซื้อ VOO ล้วน (USD)", f"{shadow_value:,.2f}", delta=shadow_delta)
    bench_col3.metric("ส่วนต่าง", f"{_zero_safe(actual_value_usd - shadow_value):+,.2f} USD")
    if net_external_usd > 0:
        st.caption(
            f"เปอร์เซ็นต์ทั้งสองช่องคิดจากฐานเดียวกัน: เงินสุทธิจากภายนอก {net_external_usd:,.2f} USD "
            f"(ไม้ซื้อ {invested_usd:,.2f} − ปันผลที่รับ {payout_usd:,.2f}) · "
            "ขา VOO เงาถูกบังคับให้ขายเท่ากับปันผลทุกก้อนในวันเดียวกัน ไม้ที่ซื้อด้วยเงินปันผล "
            "(DRIP) จึงหักล้างตัวเองและไม่ถูกนับเป็นเงินใหม่"
        )
    else:
        st.warning(
            f"ปันผลที่รับ ({payout_usd:,.2f} USD) มากกว่าหรือเท่ากับเงินที่ใส่เข้าไป "
            f"({invested_usd:,.2f} USD) — เงินสุทธิจากภายนอกไม่เป็นบวก จึงไม่แสดงเปอร์เซ็นต์ "
            "(ตัวหารที่ ≤ 0 ทำให้ % พลิกเครื่องหมายได้) · มูลค่าและส่วนต่างข้างบนยังเทียบกันได้ตามปกติ"
        )
    # ตัวเลขจุดเดียวข้างบนต้องไม่ยืนอยู่คนเดียว — คำถาม "ชนะไหม" ต้องมาพร้อมช่วงความเชื่อมั่น
    _render_mix_vs_voo_significance(priced, benchmark_prices)
    if shadow["payout_rounds"] == 0 and comparable_age_days >= _BENCHMARK_MIN_AGE_DAYS:
        # ไม่มีแถวปันผลเลยทั้งที่พอร์ตอายุเกิน 90 วัน = ขาเงาได้ปันผล VOO ลงทุนต่อฟรี ๆ
        # (Adjusted Close = total return) ขณะที่ขาพอร์ตจริงตีมูลค่าด้วยราคาล้วน
        st.warning(
            "ไม่มีแถวปันผลของกองที่เทียบอยู่ในสมุดเลย — ราคา VOO ที่ใช้เป็น Adjusted Close "
            "(ปันผลลงทุนต่อให้เอง) ส่วนพอร์ตจริงตีมูลค่าจากราคาล้วน ถ้าคุณเคยได้ปันผลแต่ยังไม่ได้"
            "บันทึก ตัวเลขข้างบนจะเอียงเข้าข้าง VOO อย่างเป็นระบบ (วัด VOO 3 ปีล่าสุด: "
            "total return 79.29% เทียบกับราคาล้วน 72.38% ≈ 1.6 จุด/ปี) — บันทึกปันผลที่รับ "
            "แล้วตัวเลขจะกลับมาเทียบกันได้ตรง ๆ"
        )
    if shadow["skipped"]:
        # เหตุผลต้องตรงชนิด — ``shadow_benchmark`` แยก "แถวในสมุดเสียเอง" ออกจาก
        # "ไม่มีราคา VOO ณ วันซื้อ" มาให้แล้ว เดิมหน้าจออ่านแต่ผลรวมแล้วพิมพ์เหตุผล
        # ตายตัวข้อเดียว ⇒ บอกสาเหตุผิด (AUDIT_2026-08-06 C2.3)
        skipped_no_price = int(shadow.get("skipped_no_price") or 0)
        skipped_bad_row = int(shadow.get("skipped_bad_row") or 0)
        causes: list[str] = []
        if skipped_no_price:
            causes.append(f"{skipped_no_price} ไม้ไม่มีราคา VOO ณ วันซื้อ (ซื้อก่อนช่วงที่มีข้อมูล)")
        if skipped_bad_row:
            causes.append(
                f"{skipped_bad_row} ไม้ข้อมูลในสมุดใช้ไม่ได้ (วันที่/จำนวนหุ้น/ราคาอ่านไม่ออก)"
            )
        st.warning(
            f"{shadow['skipped']} ไม้เทียบไม่ได้ ตัดออกจากขา VOO เงา — "
            + " · ".join(causes or ["ไม่ทราบสาเหตุ"])
        )
    # as-of ของราคา VOO ที่ใช้ตีมูลค่าเงา — docstring ของ shadow_benchmark สั่งให้ผู้เรียก
    # เตือนก่อนเอาไปเทียบ แต่เดิมไม่มีโค้ดโปรดักชันอ่านคีย์นี้เลย (C2.3)
    benchmark_asof = shadow.get("benchmark_asof")
    if benchmark_asof is not None:
        st.caption(
            f"มูลค่า VOO เงาตีด้วยราคาปิดวันที่ {pd.Timestamp(benchmark_asof):%Y-%m-%d} "
            "(พอร์ตจริงตีด้วยราคาล่าสุดที่ดึงได้ — ถ้าคนละวันกัน ส่วนต่างจะรวมผลของวันที่ต่างกันไว้ด้วย)"
        )
    prices_dropped = int(shadow.get("benchmark_prices_dropped") or 0)
    if prices_dropped:
        st.warning(
            f"คัดราคา VOO ทิ้ง {prices_dropped} จุดเพราะใช้ไม่ได้ — "
            "ถ้าจุดที่ทิ้งคือวันล่าสุด มูลค่า VOO เงาจะเป็นราคาของวันก่อนหน้า"
        )

    # %/ปีแบบ money-weighted — **ฐานเงินบาท** เพราะเป็นตัวเลขที่เอาไปหักเงินเฟ้อไทย
    # (AUDIT_2026-08-06 H8) เดิมสร้าง flow จาก shares × price_usd ⇒ ได้ผลตอบแทนฐาน
    # ดอลลาร์ แล้วลบ CPI ไทยทับ: ช่วงบาทแข็งตัวเลขสูงเกินจริงหลายจุด/ปี และพลิก
    # เครื่องหมายได้ (USD บวก แต่บาทติดลบ) · ขาซื้อใช้ ``amount_thb`` = เงินที่จ่ายจริง
    # ซึ่งรวมค่าธรรมเนียมไว้แล้ว (เดิมค่าธรรมเนียมหายไปจากกระแสเงินสดทั้งหมด)
    #
    # กระแสเงินสดต้องมาจาก **ไม้ชุดเดียวกับมูลค่าปลายทาง** (FIX_PLAN ข้อ 3.1 ข้อย่อย ค)
    # เดิมสร้าง flow จาก ``buys``/``dividends`` ทั้งสมุด แต่ปิดท้ายด้วย ``actual_value``
    # ที่นับเฉพาะกองที่มีราคา ⇒ เงินของกองที่ราคาหายถูกใส่เข้าไปเป็นเงินจ่ายออก แต่มูลค่า
    # ของมันไม่เคยกลับมาในแถวสุดท้าย = XIRR ต่ำกว่าจริงอย่างเป็นระบบ (วัดได้ 16.25%/ปี
    # กลายเป็น 7.63%/ปี จากราคาที่หายไปตัวเดียว) ตอนนี้ใช้ ``comparable`` ซึ่งเป็นชุด
    # เดียวกับที่ตีมูลค่าปลายทางได้จริง ตัวเลขจึงเป็น "ผลตอบแทนของกองที่เทียบได้" ครบวง
    flows_thb: list[tuple[pd.Timestamp, float]] = []
    flows_usd: list[tuple[pd.Timestamp, float]] = []
    buys_without_thb = 0
    usd_incomplete = False  # ตัวเลขฐานดอลลาร์เป็นของแถม — ไม่ครบก็ไม่แสดง ห้ามคิดจากไม้ที่เหลือ
    for _, row in comparable.iterrows():
        when = pd.to_datetime(row["date"])
        paid_thb = _to_number(row.get("amount_thb"))
        if paid_thb is None or paid_thb <= 0:
            buys_without_thb += 1
        else:
            flows_thb.append((when, -paid_thb))
        shares = _to_number(row.get("shares"))
        price_usd = _to_number(row.get("price_usd"))
        if shares is None or price_usd is None:
            usd_incomplete = True
        else:
            flows_usd.append((when, -shares * price_usd))
    dividends_without_thb = 0
    for _, dividend_row in comparable_dividends.iterrows():
        when = pd.to_datetime(dividend_row["date"])
        received_thb = _to_number(dividend_row.get("amount_thb"))
        if received_thb is None:
            dividends_without_thb += 1
        else:
            flows_thb.append((when, received_thb))
        received_usd = _to_number(dividend_row.get("amount_usd"))
        if received_usd is None:
            usd_incomplete = True
        else:
            flows_usd.append((when, received_usd))
    today = pd.Timestamp.today().normalize()
    priced_values_thb = pd.to_numeric(priced["Current Value (THB)"], errors="coerce")
    actual_value_thb = None if priced_values_thb.isna().any() else _to_number(priced_values_thb.sum())
    if actual_value_thb is None:
        # มูลค่าปลายทางไม่รู้ = คิด XIRR ไม่ได้ ห้ามใส่ 0 ลงกระแสเงินสด (จะได้ −100%/ปี ปลอม)
        st.caption(
            "มูลค่าพอร์ตปลายทางเป็นเงินบาทอ่านไม่ได้ — ไม่คำนวณ %ต่อปี (XIRR) "
            "ดีกว่าคิดจากมูลค่าที่เดาเอา"
        )
        return
    flows_thb.append((today, actual_value_thb))
    flows_usd.append((today, actual_value_usd))

    unpriced_now = _unpriced_tickers(holdings_df)
    if unpriced_now:
        # เงิน **และ** ปันผลของกองเหล่านี้ถูกตัดออกจากกระแสเงินสดทั้งชุด ไม่ใช่แค่มูลค่า
        # ปลายทาง — %ต่อปีจึงเป็นของกองที่เหลือครบวง ไม่ใช่ตัวเลขที่ "ต่ำกว่าความจริง"
        # แบบเดิมที่เอาเงินจ่ายออกของกองหนึ่งไปหารกับมูลค่าปลายทางของอีกกองหนึ่ง
        st.caption(
            f"%ต่อปีด้านล่างเป็นของกองที่เทียบได้เท่านั้น — เงินและปันผลของ "
            f"{', '.join(unpriced_now)} (ดึงราคาไม่ได้) ถูกตัดออกจากกระแสเงินสดทั้งชุด "
            "ไม่ใช่ตัดเฉพาะมูลค่าปลายทาง"
        )

    if comparable_age_days < _BENCHMARK_MIN_AGE_DAYS:
        st.caption(
            f"อายุพอร์ต {comparable_age_days} วัน — น้อยเกินกว่าจะตีเป็น %ต่อปีอย่างมีความหมาย "
            "(ตัวเลขรวมด้านบนคือของจริงทั้งหมดแล้ว)"
        )
        return
    if buys_without_thb or dividends_without_thb:
        # ขาดยอดบาทของบางไม้ = ฐานเงินบาทไม่ครบ ห้ามคิด %ต่อปีจากไม้ที่เหลือเงียบ ๆ
        st.warning(
            f"คำนวณ %ต่อปีฐานเงินบาทไม่ได้: ไม่มียอดเงินบาทของไม้ซื้อ {buys_without_thb} รายการ "
            f"และปันผล {dividends_without_thb} รายการ — เติมช่อง `amount_thb` ในสมุดแล้วลองใหม่"
        )
        return
    annual_rate = xirr(flows_thb)
    if annual_rate is None:
        # xirr() คืน None เมื่อพิสูจน์รากไม่ผ่าน — ดีกว่าคืนขอบบน +1000%/ปี (FIX_PLAN ข้อ 1.6)
        hint = (
            " (อาจเป็นเพราะไม้ที่ข้อมูลไม่ครบถูกตัดออกจากกระแสเงินสด — ดูรายการด้านบน)"
            if ledger_skipped
            else ""
        )
        st.caption(f"คำนวณ %ต่อปี (XIRR) ไม่ได้จากกระแสเงินสดปัจจุบัน — ไม่แสดงตัวเลขแทน{hint}")
        return
    xirr_text = (
        f"ผลตอบแทนจริง ~{annual_rate * 100.0:+.1f}%/ปี "
        "(ฐานเงินบาท · money-weighted จากเงินบาทที่จ่ายจริงรวมค่าธรรมเนียม และปันผลที่บันทึก)"
    )
    usd_rate = None if usd_incomplete else xirr(flows_usd)
    if usd_rate is not None:
        xirr_text += (
            f" · ฐานดอลลาร์ ~{usd_rate * 100.0:+.1f}%/ปี (ไม่รวมผลของอัตราแลกเปลี่ยน "
            "จึงห้ามเอาไปหักเงินเฟ้อไทย)"
        )
    thai_inflation = get_thai_inflation()
    if thai_inflation is not None:
        real_rate = annual_rate * 100.0 - float(thai_inflation["inflation_pct"])
        xirr_text += (
            f" · หักเงินเฟ้อไทย ~{thai_inflation['inflation_pct']:.1f}% "
            f"→ real return ≈ {real_rate:+.1f}%/ปี"
        )
    else:
        xirr_text += " · ไม่ทราบเงินเฟ้อไทยขณะนี้ — ไม่แสดง real return"
    st.info(xirr_text)


def _render_panic_coach_section(holdings_df: pd.DataFrame) -> None:
    """โค้ชช่วงตลาดผันผวน + stress context (Roadmap ข้อ 13).

    มูลค่าพอร์ตย้อนหลังประมาณด้วย "จำนวนหุ้นปัจจุบันคงที่" — ไม่ใช่เส้นทางพอร์ตจริง
    ที่ทยอยซื้อ จึงใช้เป็นบริบทความผันผวน ไม่ใช่ตัวเลขผลตอบแทน
    """
    priced = holdings_df[holdings_df["Price OK"]] if not holdings_df.empty else holdings_df
    if priced.empty:
        return
    st.divider()
    st.subheader("โค้ชช่วงตลาดผันผวน (stress context)")
    try:
        history_prices = cached_prices(get_tickers(), years=10)
    except PriceDataUnavailableError as exc:
        st.error(f"ดึงราคาย้อนหลังไม่ได้: {exc}")
        return

    shares_map = {
        str(row["Ticker"]): float(row["Shares"]) for _, row in priced.iterrows()
    }
    available = [t for t in shares_map if t in history_prices.columns]
    if not available:
        st.caption("ไม่มีราคาย้อนหลังของ ETF ที่ถืออยู่")
        return
    aligned = history_prices[available].dropna()
    if aligned.empty:
        st.caption("ช่วงเวลาที่ทุกตัวมีราคาพร้อมกันสั้นเกินไป")
        return
    value_series = (aligned * pd.Series({t: shares_map[t] for t in available})).sum(axis=1)

    current_dd = float((underwater_series(value_series) * 100.0).iloc[-1])
    episodes = drawdown_episodes(value_series, min_depth=0.10)
    recovered = [e for e in episodes if e["months_to_recover"] is not None]

    if current_dd > -5.0:
        st.success(
            f"พอร์ต (ตามหุ้นที่ถือปัจจุบัน) ห่างจากจุดสูงสุดเพียง {abs(current_dd):.1f}% — เดินตามแผนปกติ"
        )
    else:
        message = f"ตอนนี้พอร์ตต่ำกว่าจุดสูงสุด {abs(current_dd):.1f}%"
        if recovered:
            deepest = min(float(e["depth_pct"]) for e in recovered)
            median_months = float(
                pd.Series([float(e["months_to_recover"]) for e in recovered]).median()
            )
            message += (
                f" — ส่วนผสมพอร์ตนี้ในอดีตเคยลงลึกสุด {deepest:.1f}% "
                f"และรอบที่ลึกเกิน 10% ฟื้นกลับทุกรอบ (median {median_months:.0f} เดือน)"
            )
        message += " · ตาราง DCA เดือนนี้คือการซื้อของถูกลงตามแผน ไม่ใช่เหตุให้หยุด"
        st.info(message)
    st.caption(
        "ประมาณด้วยจำนวนหุ้นปัจจุบันคงที่ย้อนหลัง 10 ปี (สถิติอดีต ไม่ใช่การพยากรณ์ "
        "และไม่ใช่เส้นทางพอร์ตจริงที่ทยอยซื้อ)"
    )


#: เกณฑ์ "เคลื่อนไหวแทบเป็นตัวเดียวกัน" — ใช้ทั้งกับค่าเดียวและกับค่าสูงสุดของ rolling
_HIGH_CORRELATION = 0.85


def _render_rolling_correlation(prices: pd.DataFrame, base: str = "VOO") -> None:
    """correlation แบบเลื่อนหน้าต่าง — ค่าเดียวซ่อนกรณีเลวร้ายไว้ (FIX_PLAN เฟส 4③).

    วัดจริง 2026-08-08 เทียบ VOO: SCHD แสดงค่าเดียว 0.86 แต่ rolling 1 ปีเคยขึ้นถึง
    **0.98** และตอนนี้อยู่ที่ 0.29 · XLV ค่าเดียว 0.76 แต่เคยขึ้นถึง 0.95 —
    เกณฑ์เตือนที่ดูค่าเดียวจึงมองไม่เห็นว่า **ตัวที่ควรกระจายความเสี่ยงหยุดกระจายพอดี
    ตอนที่ต้องการมันที่สุด**
    """
    try:
        summary = rolling_correlation_summary(prices, base)
    except ValueError as exc:
        st.caption(f"ยังคิด correlation แบบเลื่อนหน้าต่างไม่ได้: {exc}")
        return

    table = summary.rename(
        columns={
            "min": "ต่ำสุด",
            "mean": "เฉลี่ย",
            "max": "สูงสุด",
            "current": "ปัจจุบัน",
            "n_windows": "จำนวนหน้าต่าง",
        }
    )
    st.caption(f"correlation เทียบ {base} แบบเลื่อนหน้าต่าง {ROLLING_WINDOW_DAYS} วัน")
    st.dataframe(
        table.style.format(
            {"ต่ำสุด": "{:+.2f}", "เฉลี่ย": "{:+.2f}", "สูงสุด": "{:+.2f}",
             "ปัจจุบัน": "{:+.2f}", "จำนวนหน้าต่าง": "{:,.0f}"},
            na_rep="N/A",
        )
    )
    ever_high = [t for t in summary.index if float(summary.loc[t, "max"]) >= _HIGH_CORRELATION]
    if ever_high:
        detail = ", ".join(
            f"{t} (สูงสุด {float(summary.loc[t, 'max']):+.2f} · ปัจจุบัน "
            f"{float(summary.loc[t, 'current']):+.2f})"
            for t in ever_high
        )
        st.warning(
            f"เคยเคลื่อนไหวแทบเป็นตัวเดียวกับ {base} (correlation เคยแตะ "
            f"{_HIGH_CORRELATION:.2f}): {detail} — ค่าเฉลี่ยหรือค่าปัจจุบันที่ต่ำ **ไม่ได้**"
            "แปลว่ามันจะกระจายความเสี่ยงให้ในวันที่ตลาดพัง ซึ่งเป็นวันที่ต้องการมันที่สุด"
        )


def _render_lookthrough() -> None:
    """ทะลุกอง ETF ลงไปดูหุ้นและเซกเตอร์ที่ถืออยู่จริง (FIX_PLAN เฟส 4③).

    ตัวเลขเชิงพรรณนาล้วน — **ไม่เข้าเลขคะแนนและไม่เข้าการจัดสรร DCA**
    """
    st.markdown("**ทะลุกองลงไปดูของที่ถือจริง**")
    try:
        weights = _tracked_target_weights()
        result = look_through(weights)
    except (InvalidTargetWeights, NoTargetForSubset, ValueError) as exc:
        st.caption(f"ยังทะลุกองไม่ได้: {exc}")
        return

    if result["unavailable"]:
        st.warning(
            "ดึงโครงสร้างกองไม่ได้: "
            + ", ".join(f"{t} ({why})" for t, why in sorted(result["unavailable"].items()))
            + f" — ตัวเลขด้านล่างคิดจากพอร์ตเพียง {result['covered_weight'] * 100:.1f}%"
        )
    if not result["holdings"] and not result["sectors"]:
        st.caption("ผู้ให้ข้อมูลไม่มีโครงสร้างของกองเหล่านี้ — ยังทะลุกองไม่ได้")
        return

    col_a, col_b = st.columns(2)
    with col_a:
        st.caption("หุ้นรายตัวที่ถือมากที่สุด (ผ่านกองต่าง ๆ รวมกัน)")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "หุ้น": row["symbol"],
                        "% ของพอร์ต": row["weight_pct"],
                        "ผ่านกอง": ", ".join(row["via"]),
                    }
                    for row in result["holdings"][:10]
                ]
            ).style.format({"% ของพอร์ต": "{:.2f}%"}),
            hide_index=True,
        )
    with col_b:
        st.caption("สัดส่วนเซกเตอร์ที่ถือจริง (ครอบทั้งกอง จึงเป็นตัวเลขเต็ม)")
        st.dataframe(
            pd.DataFrame(
                [{"เซกเตอร์": k, "% ของพอร์ต": v} for k, v in list(result["sectors"].items())[:10]]
            ).style.format({"% ของพอร์ต": "{:.2f}%"}),
            hide_index=True,
        )

    overlaps = overlap_pairs(result)
    if overlaps:
        st.warning(
            "หุ้นที่ถือผ่าน **มากกว่าหนึ่งกอง** — ความทับซ้อนที่จำนวนกองบนหน้าจอไม่บอก: "
            + ", ".join(f"{r['symbol']} {r['weight_pct']:.2f}% ({'+'.join(r['via'])})" for r in overlaps[:6])
        )
    st.caption(result["notes"])
    _render_lookthrough_ratios(result)


def _render_lookthrough_ratios(result: dict) -> None:
    """อัตราส่วนพื้นฐานถ่วงน้ำหนักจากหุ้นข้างในกอง — **ไม่เข้าเลขคะแนน/จัดสรร DCA**.

    P/E หรือ ROE ของ ticker ``VOO`` เองไม่มีความหมาย (กองไม่ได้ทำธุรกิจ) ตัวเลขชุดนี้
    จึงมาจากหุ้นที่ทะลุกองเจอเท่านั้น และต้องแสดง ``coverage`` คู่กันเสมอ ไม่งั้นผู้อ่าน
    จะเข้าใจว่าเป็นค่าเฉลี่ยของทั้งพอร์ต ทั้งที่เป็นค่าเฉลี่ยของ **หุ้นใหญ่ที่สุด** เท่านั้น
    """
    st.markdown("**อัตราส่วนพื้นฐานของของที่ถือจริง**")
    try:
        summary = weighted_ratios(result)
    except ValueError as exc:
        st.caption(f"ยังคิดอัตราส่วนไม่ได้: {exc}")
        return

    rows = []
    for item in summary["ratios"].values():
        value = item["value"]
        if value is None:
            shown = "คำนวณไม่ได้"
        elif item["label"] in {"ROE", "Profit margin"}:
            shown = f"{value:.2f}%"
        else:
            shown = f"{value:.2f}"
        dropped = []
        if item["missing"]:
            dropped.append(f"ไม่มีข้อมูล: {', '.join(item['missing'])}")
        if item["not_meaningful"]:
            dropped.append(f"ค่าใช้ไม่ได้: {', '.join(item['not_meaningful'])}")
        rows.append(
            {
                "อัตราส่วน": item["label"],
                "ค่า": shown,
                "วิธีรวม": "ฮาร์มอนิก" if item["method"] == "harmonic" else "เลขคณิต",
                "ตัวหารที่ใช้ (% พอร์ต)": item["weight_pct"],
                "ตัดออก": " · ".join(dropped) or "—",
            }
        )
    st.dataframe(
        pd.DataFrame(rows).style.format({"ตัวหารที่ใช้ (% พอร์ต)": "{:.2f}%"}),
        hide_index=True,
        use_container_width=True,
    )
    st.caption(summary["notes"])


def _render_overlap_section() -> None:
    """ความทับซ้อน/การกระจายจริง (Roadmap ข้อ 10) — correlation คำนวณจริง + ทะลุกองถึงรายหุ้น."""
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

    high_pairs = sorted([p for p in pairs if p[2] >= _HIGH_CORRELATION], key=lambda p: -p[2])
    if high_pairs:
        pair_text = ", ".join(f"{a}–{b} ({c:.2f})" for a, b, c in high_pairs)
        st.warning(
            f"คู่ที่เคลื่อนไหวแทบเป็นตัวเดียวกัน (correlation ≥ {_HIGH_CORRELATION:.2f}): {pair_text} — "
            "การถือทั้งคู่กระจายความเสี่ยงได้น้อยกว่าที่จำนวนตัวบอก"
        )
    _render_rolling_correlation(overlap_prices)
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
    _render_lookthrough()


_UNKNOWN_MONEY_TEXT = "ไม่ทราบ"


def _money_or_unknown(value: float | None) -> str:
    """จำนวนเงินบาทที่ไม่รู้ต้องอ่านว่า "ไม่ทราบ" ไม่ใช่ 0.00."""
    return _UNKNOWN_MONEY_TEXT if value is None else f"{value:,.2f}"


def _column_sum_or_none(rows: pd.DataFrame, column: str) -> float | None:
    """ผลรวมของคอลัมน์ — มีช่องไหนอ่านไม่ออกแม้ช่องเดียว = ``None`` (ไม่รู้).

    ห้ามใช้ ``Series.sum()`` เปล่า ๆ บนเส้นทางเงิน: ``skipna=True`` เป็นค่าปริยาย
    ยอดที่ได้จึงเป็นผลรวมของ "เท่าที่มี" แต่หน้าตาเหมือนยอดเต็ม
    """
    if rows.empty or column not in rows.columns:
        return None
    values = pd.to_numeric(rows[column], errors="coerce")
    if values.isna().any():
        return None
    return _to_number(values.sum())


def _usd_totals(holdings_df: pd.DataFrame | None) -> dict[str, float | None]:
    """ยอดรวม **ฝั่งดอลลาร์** จาก snapshot เดียวกับตารางรายกอง (AUDIT_ROUND2_2026-08-07 T3/F2).

    นิยามเดียวกับ ``backend/services/portfolio_service._summary_payload()`` เป๊ะ ๆ
    เพื่อให้ตัวเลขบนแดชบอร์ดกับที่ ``/api/portfolio`` ตอบเป็นเลขชุดเดียวกัน:

    - ``invested_usd_all``    ดอลลาร์ที่จ่ายจริงทุกไม้ (Σ shares × price ณ วันซื้อ)
    - ``invested_usd_priced`` เฉพาะกองที่ดึงราคาวันนี้ได้ = **ฐานเดียว** ที่ P&L/% คิดมาจาก
    - ``current_value_usd`` / ``pnl_usd`` เฉพาะกองที่มีราคา · ``None`` = ไม่รู้

    เดิมโหมด USD เอา**ยอดบาท**มาหารด้วยอัตราวันนี้ตัวเดียว ทั้งที่ ``invested_thb`` คิดด้วย
    อัตราของ**วันที่ซื้อ** ผลที่ได้จึงไม่ใช่ทั้งดอลลาร์ที่จ่ายจริงและไม่ใช่อะไรที่มีความหมาย
    (เคสจริง: จอโชว์เงินลงทุน 3,234.15 ขณะที่ API ตอบ 3,030.00 และสามช่องบนแถวเดียวกัน
    บวกลบกันไม่ลง) — การกุตัวเลขจากอัตราผิดยุคเป็นเรื่องเดียวกับ ``fillna(0)`` บนเส้นทางราคา
    """
    unknown: dict[str, float | None] = {
        "invested_usd_all": None,
        "invested_usd_priced": None,
        "current_value_usd": None,
        "pnl_usd": None,
        "return_pct": None,
    }
    if holdings_df is None:
        return unknown
    if holdings_df.empty:
        # สมุดว่าง = 0 คือคำตอบจริง (คนละเรื่องกับ "ดึงราคาไม่ได้") — ตรงกับ portfolio_service
        return {k: 0.0 for k in unknown}
    if "Price OK" not in holdings_df.columns:
        return unknown

    priced = holdings_df[holdings_df["Price OK"].astype(bool)]
    invested_all = _column_sum_or_none(holdings_df, "Invested (USD)")
    invested_priced = 0.0 if priced.empty else _column_sum_or_none(priced, "Invested (USD)")
    current_value = None if priced.empty else _column_sum_or_none(priced, "Current Value (USD)")
    pnl = None if priced.empty else _column_sum_or_none(priced, "P&L (USD)")
    return_pct = (
        pnl / invested_priced * 100.0
        if pnl is not None and invested_priced not in (None, 0.0)
        else None
    )
    return {
        "invested_usd_all": invested_all,
        "invested_usd_priced": invested_priced,
        "current_value_usd": current_value,
        "pnl_usd": pnl,
        "return_pct": return_pct,
    }


def _render_portfolio_totals(
    total_summary: dict,
    primary_currency: str,
    today_fx_rate: float,
    holdings_df: pd.DataFrame | None = None,
) -> None:
    """แถบตัวเลขรวมของหน้า Portfolio — ต้องติดป้ายทั้งฐานเงินลงทุนและที่มาของอัตราแลกเปลี่ยน.

    สี่อย่างที่ต้องพูดออกมา ไม่งั้นตัวเลขบนจอเดียวกันจะขัดกันเองโดยไม่มีอะไรบอก:

    1. **เงินลงทุนมีสองฐาน** ``invested_thb_all`` (จ่ายไปจริงทั้งหมด) กับ
       ``invested_thb_priced`` (เฉพาะกองที่มีราคา = ฐานเดียวที่ P&L/% คิดมาจาก)
       เดิมแสดงฐานแรกคู่กับกำไรที่คิดจากฐานที่สอง ผู้ใช้จึงประกอบเลขกลับเองไม่ได้ (H9)
    2. **NaN = "ไม่รู้มูลค่า"** ห้ามพิมพ์เป็น ``0.00`` (อ่านว่า "เท่าทุนพอดี") หรือ ``nan``
    3. **``fx_is_live=False``** = ตัวเลขบาททั้งก้อนคิดจากค่าสำรอง ต้องเตือน (B9/C1.5)
    4. **โหมด USD ไม่ใช่ "เอาบาทมาหารอัตราวันนี้"** (AUDIT_ROUND2_2026-08-07 F2) —
       ``invested_thb_*`` คิดด้วยอัตราของ *วันที่ซื้อ* การหารด้วยอัตราวันนี้จึงได้
       ตัวเลขที่ไม่ใช่ดอลลาร์ที่จ่ายจริงและไม่ตรงกับที่ ``/api/portfolio`` ตอบ
       (เคสจริง: เงินลงทุนเกินจริง +204 USD และกำไรหายไปครึ่งหนึ่ง แล้วสามช่องบน
       แถวเดียวกันบวกลบไม่ลง) · โหมด USD จึงอ่านจาก ``holdings_df`` ซึ่งเป็น snapshot
       เดียวกับตารางรายกองผ่าน :func:`_usd_totals` — นิยามเดียวกับ ``portfolio_service``
       ส่วน ``ค่าธรรมเนียมรวม`` เป็นเงินบาทที่จ่ายจริงเสมอ (ป้าย THB ไม่เปลี่ยนตามโหมด)

    ``holdings_df=None`` ในโหมด USD = ไม่มี snapshot ให้อ่าน ⇒ ทุกช่องเป็น "ไม่ทราบ"
    ซึ่งซื่อสัตย์กว่าการแปลงค่าเงินข้ามยุคให้ดูเหมือนมีคำตอบ
    """
    fx_is_live = total_summary.get("fx_is_live")
    if fx_is_live is False:
        fx_used = _to_number(total_summary.get("fx_rate_thb"))
        if fx_used is None:
            fx_used = _to_number(today_fx_rate)
        fx_text = f"{fx_used:.2f}" if fx_used is not None else _UNKNOWN_MONEY_TEXT
        st.warning(
            f"อัตราแลกเปลี่ยนที่ใช้แปลงมูลค่าวันนี้เป็น**ค่าสำรอง** ({fx_text} บาท/USD) "
            "ไม่ใช่อัตราสด — ตัวเลขบาททุกช่องด้านล่างคลาดเคลื่อนตามไปด้วย"
        )

    fee_total = _to_number(total_summary.get("total_fee_thb"))
    to_usd = str(primary_currency).upper() == "USD"
    unit = "USD" if to_usd else "THB"
    # หน่วยของทุกข้อความใต้กล่องต้องตรงกับหน่วยของ metric ไม่งั้นผู้ใช้กระทบยอดตามไม่ได้
    money_unit = "ดอลลาร์" if to_usd else "บาท"

    if to_usd:
        usd = _usd_totals(holdings_df)
        invested_all = usd["invested_usd_all"]
        invested_priced = usd["invested_usd_priced"]
        current_value = usd["current_value_usd"]
        pnl_value = usd["pnl_usd"]
        return_pct = usd["return_pct"]
        if holdings_df is None:
            st.warning(
                "แสดงผลเป็น USD ไม่ได้เพราะไม่มีรายละเอียดรายกองในรอบนี้ — "
                "ระบบไม่แปลงยอดบาทด้วยอัตราวันนี้แทน เพราะต้นทุนแต่ละไม้คิดด้วยอัตรา"
                "ของวันที่ซื้อ (จะได้ตัวเลขที่ไม่ใช่ทั้งบาทและไม่ใช่ทั้งดอลลาร์)"
            )
    else:
        invested_all = _to_number(
            total_summary.get("invested_thb_all", total_summary.get("total_invested_thb"))
        )
        invested_priced = _to_number(total_summary.get("invested_thb_priced"))
        current_value = _to_number(total_summary.get("current_value_thb"))
        pnl_value = _to_number(total_summary.get("total_pnl_thb"))
        return_pct = _to_number(total_summary.get("total_return_pct"))

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric(f"เงินลงทุนรวม ({unit})", _money_or_unknown(invested_all))
    m2.metric(f"มูลค่าปัจจุบัน ({unit})", _money_or_unknown(current_value))
    m3.metric(
        f"กำไร/ขาดทุน ({unit})",
        _money_or_unknown(pnl_value),
        # % ต้องเป็นฐานเดียวกับตัวเลขในช่อง — ฐานบาทแปะข้างตัวเลขดอลลาร์คือคนละเรื่อง
        delta=(f"{return_pct:.2f}%" if return_pct is not None else None),
    )
    m4.metric("FX Rate วันนี้", f"{float(today_fx_rate):.2f} THB/USD")
    m5.metric("ค่าธรรมเนียมรวม (THB)", _money_or_unknown(fee_total))

    if current_value is None or pnl_value is None:
        st.info(
            "ยังไม่รู้มูลค่า/กำไรของพอร์ต เพราะดึงราคาปัจจุบันไม่ได้เลยสักกอง — "
            f"ช่องที่ขึ้นว่า “{_UNKNOWN_MONEY_TEXT}” คือ **ไม่รู้** ไม่ใช่ 0 {money_unit}"
        )
    if (
        invested_all is not None
        and invested_priced is not None
        and abs(invested_all - invested_priced) > 0.005
    ):
        st.caption(
            f"เงินลงทุนรวมด้านบนคือยอดที่จ่ายไปจริงทั้งหมด {invested_all:,.2f} {money_unit} "
            f"แต่กำไร/ขาดทุนและ % คิดจากฐานเฉพาะกองที่มีราคา {invested_priced:,.2f} {money_unit} "
            f"(ต่างกัน {invested_all - invested_priced:,.2f} {money_unit} = กองที่ดึงราคาไม่ได้) — "
            "สองตัวเลขนี้อยู่คนละฐาน จึงลบกันตรง ๆ ไม่ได้"
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

    st.subheader("Add Transaction")
    # FX มีแหล่งเดียวคือ utils/fx.py (ผ่าน get_today_fx_rate_thb) — ห้ามอ่าน
    # ``display.default_fx_rate`` ตรงจากหน้าจอ (กฎใน CLAUDE.md)
    #
    # เดิมบรรทัดนี้อ่านค่าสำรองจาก config เองแล้วใช้เมื่อ ``not rate or rate <= 0``
    # ซึ่ง (ก) เป็นทางเข้าที่สองสู่ค่าสำรอง ที่ **ข้าม band check 20–50** ของ
    # ``utils.fx._config_fallback()`` ไปเลย และ (ข) เป็น dead code อยู่แล้ว เพราะ
    # ``get_today_fx_rate_thb()`` คืนค่าที่ผ่าน band เสมอ หรือไม่ก็โยน
    # ``FxRateUnavailable`` — มันไม่มีทางคืน 0/None (AUDIT_ROUND2_2026-08-07)
    #
    # เมื่อ FX ใช้ไม่ได้จริง ๆ ต้องหยุดฟอร์มบันทึกธุรกรรม: กรอกอัตราเดาเองแล้วบันทึกลง
    # สมุดจริง = ต้นทุนบาทผิดถาวร — "ดึงไม่ได้" ต้องไม่กลายเป็นตัวเลขในสมุด
    try:
        with st.spinner("กำลังดึงอัตราแลกเปลี่ยน..."):
            today_fx_rate = get_today_fx_rate_thb()
    except FxRateUnavailable as exc:
        st.error(
            f"ยังใช้หน้านี้ไม่ได้ — ไม่มีอัตราแลกเปลี่ยน THB/USD ที่เชื่อถือได้: {exc} "
            f"(ทั้งอัตราสดและค่าสำรองใน config.json ต้องอยู่ในช่วง {FX_MIN_RATE:.0f}–{FX_MAX_RATE:.0f}) "
            "— แก้ค่าสำรองได้ที่หน้า Settings แล้วกลับมาใหม่ · ระบบไม่เดาอัตราให้ "
            "เพราะอัตราที่เดาจะถูกบันทึกเป็นต้นทุนจริงในสมุดพอร์ต"
        )
        return
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
        # ดึงราคา/FX **รอบเดียว** แล้วส่ง snapshot ตัวเดิมต่อ (AUDIT_ROUND2 G2) — เดิม
        # ``get_total_summary()`` ไปดึงเองอีกรอบ ยอดรวมกับตารางรายกองจึงมาจากคนละ
        # snapshot ได้จริง (yfinance ติด rate limit คั่นกลาง) แล้วเลขบนหน้าเดียวกันขัดกันเอง
        # · โหมด USD ที่อ่านจาก ``holdings_df`` ยิ่งต้องเป็น snapshot เดียวกับยอดบาท
        holdings_df = get_portfolio_summary()
        total_summary = get_total_summary(holdings_df)

    # ราคาที่ดึงไม่ได้ = แจ้งชัด ไม่ใช่แสดงเป็นขาดทุน -100% (AUDIT.md C1)
    missing_prices = list(total_summary.get("missing_prices") or [])
    if missing_prices:
        st.error(
            f"ดึงราคาปัจจุบันไม่ได้: {', '.join(missing_prices)} — "
            "มูลค่าและกำไร/ขาดทุนด้านล่างคิดเฉพาะ ETF ที่มีราคาเท่านั้น"
        )

    # แถวธุรกรรมที่ถูกตัด / ถูกซ่อมอัตรา / ยอดเงินขัดกันเอง (FIX_PLAN ข้อ 1.2 + C1.2/C1.3)
    _render_ledger_reports(total_summary)

    # ส่ง snapshot รายกองไปด้วย — โหมด USD ต้องอ่านยอดดอลลาร์จาก snapshot ชุดเดียวกับ
    # ตารางด้านล่าง ไม่ใช่เอายอดบาทมาหารอัตราวันนี้ (AUDIT_ROUND2_2026-08-07 F2)
    _render_portfolio_totals(total_summary, primary_currency, today_fx_rate, holdings_df)

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
                # สองช่องนี้คนละฐานกันโดยตั้งใจ และป้ายต้องบอกให้ชัด (FIX_PLAN ข้อ 3.3) —
                # ``Return (%)`` ฐานบาท (ตัวเดียวกับ %รวมด้านบน รวมผลอัตราแลกเปลี่ยน)
                # ``Return USD (%)`` ฐานดอลลาร์ (ผลของหุ้นล้วน)  เดิมมีช่องเดียวชื่อ
                # ``Return (%)`` ที่เป็นฐานดอลลาร์ วางติดกับ ``P&L (THB)`` ⇒ อ่านคู่กันไม่ลง
                "Return (%)",
                "Return USD (%)",
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
                    "Return USD (%)": "{:,.2f}%",
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

    _render_benchmark_section(holdings_df)

    _render_panic_coach_section(holdings_df)

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
    _render_dividend_section_header(dividend_summary)
    if int(dividend_summary["count"]) > 0:
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
    # "ถูกตัดเพราะข้อมูลไม่ครบ" ≠ "ยังไม่เคยบันทึกธุรกรรม" — ต้องแยกให้ผู้ใช้เห็น (C1)
    # (รายละเอียดรายแถวแสดงไปแล้วที่บล็อกสรุปด้านบนของหน้าเดียวกัน จึงไม่ซ้ำตารางอีกรอบ)
    history_skipped = _ledger_skipped_rows(all_transactions)
    if all_transactions.empty:
        if history_skipped:
            st.warning(
                f"ทุกแถวในสมุดบัญชี ({len(history_skipped)} แถว) ถูกตัดเพราะข้อมูลไม่ครบ — "
                "ไม่ใช่ 'ยังไม่มีธุรกรรม' ดูรายละเอียดด้านบนแล้วเติมค่าที่ขาดใน transactions.csv"
            )
        else:
            st.info("No transactions found.")
        return
    if history_skipped:
        st.warning(
            f"ตารางนี้ไม่รวม {len(history_skipped)} แถวที่ข้อมูลไม่ครบ (รายละเอียดอยู่ด้านบนของหน้า)"
        )

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

# สี 8 องค์ประกอบคะแนน (โทน GitHub dark เดียวกับ THEME)
_SCORE_PARTS = [
    ("Trend", "trend_score", TREND_MAX, THEME["accent"]),
    ("Timing", "timing_score", TIMING_MAX, "#A371F7"),
    ("Momentum", "momentum_score", MOMENTUM_MAX, "#D29922"),
    ("Dividend", "dividend_score", DIVIDEND_MAX, THEME["text_secondary"]),
    ("Volatility", "volatility_score", VOLATILITY_MAX, THEME["negative"]),
    ("Valuation", "valuation_score", VALUATION_MAX, THEME["positive"]),
    ("RelStrength", "relative_strength_score", RELATIVE_STRENGTH_MAX, "#39C5CF"),
    ("Expense", "expense_score", EXPENSE_MAX, "#DB6D28"),
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
    if not _momentum_available(row):
        # ผลตอบแทน 1/3 เดือนคำนวณไม่ได้ (ข้อมูลสั้นเกินไป) — บอกตรง ๆ ห้ามโชว์ 0/20
        chips.append(_chip_html("โมเมนตัม: ไม่มีข้อมูล (ตัดจากคะแนนเต็ม)", THEME["text_secondary"]))
    if not row.get("dividend_available", False):
        chips.append(_chip_html("ปันผล: ไม่มีข้อมูล (ตัดจากคะแนนเต็ม)", THEME["text_secondary"]))
    elif int(row.get("dividend_score") or 0) > 0:
        chips.append(_chip_html(f"ปันผลหนุน +{int(row['dividend_score'])}", THEME["positive"]))
    else:
        chips.append(_chip_html("ปันผลต่ำ/ไม่มี", THEME["text_secondary"]))
    return "".join(chips)


# เหตุผลที่ ETF ไม่ได้เงิน → วิธีแสดงบนจอ (ป้ายไทย + ระดับความเร่งด่วน)
# นิยามของเหตุผลอยู่ที่ analysis/financial_model.py ที่เดียว หน้าจอห้ามคิดเหตุผลเอง
_EXCLUSION_DISPLAY: dict[str, tuple[str, str]] = {
    EXCLUDED_NO_DATA: ("warning", "ดึงข้อมูลไม่สำเร็จรอบนี้"),
    EXCLUDED_ZERO_TARGET: ("info", "ตั้งใจไม่ถือ (เป้าหมาย 0%)"),
    EXCLUDED_ROUNDED_TO_ZERO: ("warning", "ส่วนแบ่งไม่ถึงหนึ่งก้อน"),
}


def _render_allocation_exclusions(plan: AllocationPlan, already_named: set[str]) -> None:
    """ระบุชื่อ ETF ที่ **ไม่ได้เงิน** รอบนี้ทุกตัว พร้อมเหตุผลแยกชนิด (AUDIT_ROUND2_2026-08-07 T7).

    เดิมหน้านี้เรียก ``calculate_allocation()`` ที่คืนเฉพาะตัวที่ได้เงิน แล้วพิมพ์คำโปรย
    ว่า "ไม่มีการเลือกตัวเดียวหรือตัดตัวไหนออก" ไว้เหนือตารางที่ตัดออกไปแล้วจริง ๆ ⇒
    ETF ที่ผู้ใช้ตั้งเป้าไว้ 0% หรือที่งบไม่พอ หายจากจอโดยไม่เหลือร่องรอย และผู้ใช้
    อ่านคำโปรยแล้วเข้าใจว่าครบทุกตัว

    ``already_named`` = ticker ที่หน้าจอเพิ่งเตือนไปแล้วด้านบน (กล่อง "ไม่มีข้อมูล: ...")
    — ไม่พิมพ์ซ้ำ แต่ถ้าวันหนึ่งสองชุดนี้ไม่ตรงกัน ตัวที่ตกหล่นจะยังถูกพิมพ์เสมอ
    """
    for note in plan.notes:
        # หมายเหตุจาก portfolio/targets.py (เช่น "น้ำหนักใช้ครบ 100% แล้ว — XLV ได้ 0%")
        st.caption(note)

    if not plan.excluded:
        if plan.allocation:
            st.caption("รอบนี้ไม่มี ETF ตัวไหนถูกตัดออกจากแผน — ทุกตัวที่ระบบติดตามได้เงินครบ")
        return

    for reason, (level, label) in _EXCLUSION_DISPLAY.items():
        items = [
            item
            for item in plan.excluded
            if item.reason == reason
            and not (reason == EXCLUDED_NO_DATA and item.ticker in already_named)
        ]
        if not items:
            continue
        names = ", ".join(item.ticker for item in items)
        getattr(st, level)(f"ไม่ได้เงินรอบนี้ — {label}: {names}")
        for item in items:
            st.caption(f"• {item.detail}")

    # เหตุผลชนิดใหม่ที่หน้าจอยังไม่รู้จัก ห้ามหายเงียบ — พิมพ์ดิบ ๆ ไปก่อน
    unknown = [item for item in plan.excluded if item.reason not in _EXCLUSION_DISPLAY]
    for item in unknown:
        st.warning(f"ไม่ได้เงินรอบนี้ ({item.reason}): {item.detail}")


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
    def _max_text(available: bool, max_value: int, reason: str) -> str:
        return str(max_value) if available else f"ตัดออก ({reason})"

    dividend_max_text = _max_text(bool(row.get("dividend_available")), DIVIDEND_MAX, "ไม่มีข้อมูลปันผล")
    valuation_max_text = _max_text(
        bool(row.get("valuation_available")), VALUATION_MAX, "ข้อมูลราคาน้อยกว่า ~2 ปี"
    )
    rs_max_text = _max_text(
        bool(row.get("relative_strength_available")), RELATIVE_STRENGTH_MAX, "ไม่มี benchmark เทียบ"
    )
    expense_max_text = _max_text(bool(row.get("expense_available")), EXPENSE_MAX, "ไม่มีข้อมูลค่าธรรมเนียม")
    # ตัวหารของโมเมนตัมต้องเป็นเพดานจริงของแถวนี้ (ข้อ 1.5) — ค่าคงที่ MOMENTUM_MAX จะโกหก
    # เมื่อหน้าต่าง 1M/3M หน้าใดหน้าหนึ่งคำนวณไม่ได้แล้วถูกตัดออกจากทั้งคะแนนและเพดาน
    momentum_points = _momentum_points(row)
    momentum_text = (
        f"{momentum_points}/{_momentum_max(row)}"
        if momentum_points is not None
        else "ตัดออก (ไม่มีข้อมูลผลตอบแทน 1/3 เดือน)"
    )
    st.markdown(
        f"**ชั้น 1 — คะแนนดิบ** (จาก `score_from_prices`)  \n"
        f"Trend {row.get('trend_score')}/{TREND_MAX} · Timing {row.get('timing_score')}/{TIMING_MAX} · "
        f"Momentum {momentum_text} · Dividend {row.get('dividend_score')}/{dividend_max_text} · "
        f"Volatility {row.get('volatility_score')}/{VOLATILITY_MAX} · "
        f"Valuation {row.get('valuation_score')}/{valuation_max_text} · "
        f"RelStrength {row.get('relative_strength_score')}/{rs_max_text} · "
        f"Expense {row.get('expense_score')}/{expense_max_text}  \n"
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


def _render_rebalance_mode(budget_thb: float, scores_by_ticker: dict) -> bool:
    """โหมด opt-in "ดึงพอร์ตเข้าเป้าด้วยเงินใหม่" (Roadmap ข้อ 12) — ไม่ขาย ไม่มีภาษี.

    คืน True เมื่อแสดงแผนโหมดนี้แล้ว (ผู้เรียกข้ามแผน tilt ปกติ)
    เงื่อนไขไม่ครบ (ไม่มีพอร์ต/ราคา) = แจ้งแล้วคืน False — ห้ามสลับโหมดเงียบ ๆ

    **เป้าหมายมาจาก :func:`_tracked_target_weights` เท่านั้น** (ทั้งพอร์ตที่ระบบติดตาม)
    เหมือน :func:`_render_drift_advisory` เป๊ะ ๆ — เดิมส่ง ``list(current_values)``
    คือเฉพาะกองที่ถืออยู่เข้าไป เป้าจึงถูก normalize ใหม่บนเซ็ตย่อยและกองที่ยังไม่เคยซื้อ
    ไม่มีวันได้เงิน ทั้งที่ชื่อโหมดคือ "ดึงพอร์ตเข้าเป้า" (AUDIT_2026-08-06 C2)
    """
    if not st.toggle(
        "โหมดดึงพอร์ตเข้าเป้า — เทงบเดือนนี้เข้าตัวที่ต่ำกว่าเป้า (ไม่ขาย ไม่มีภาษี)",
        value=False,
        key="scorecard_rebalance_mode",
    ):
        return False
    try:
        holdings = get_portfolio_summary()
        # ราคาขาดแม้ตัวเดียว = ไม่ทำแผน (FIX_PLAN ข้อ 1.3): มูลค่าพอร์ตรวมคือตัวหาร
        # ของทุกสัดส่วนในโหมดนี้ ถ้าตัดตัวที่ราคาหายทิ้ง เงินจะถูกเทเข้าตัวที่เหลือผิดสัดส่วน
        unpriced = _unpriced_tickers(holdings)
        if unpriced:
            st.warning(
                f"ดึงราคาไม่สำเร็จ: {', '.join(unpriced)} — ยังไม่มีแผนดึงเข้าเป้า "
                "(คิดสัดส่วนจากพอร์ตที่ไม่ครบจะทำให้เทเงินผิดตัว) แสดงแผน DCA ปกติแทน"
            )
            return False
        priced = holdings[holdings["Price OK"]] if not holdings.empty else holdings
        current_values = (
            {
                str(row["Ticker"]).strip().upper(): float(row["Current Value (THB)"])
                for _, row in priced.iterrows()
            }
            if not priced.empty
            else {}
        )
    except Exception as exc:
        st.warning(f"ใช้โหมดดึงเข้าเป้าไม่ได้: {exc} — แสดงแผน DCA ปกติแทน")
        return False

    try:
        targets = _tracked_target_weights()
    except InvalidTargetWeights as exc:
        # คอนฟิกผิดรูป = ไม่รู้เป้าหมายจริง ห้ามเดาแล้วเทเงินตามที่เดา
        _render_invalid_target_weights(exc)
        return False

    try:
        plan = rebalance_with_new_money(current_values, targets, float(budget_thb))
    except Exception as exc:
        st.warning(f"ใช้โหมดดึงเข้าเป้าไม่ได้: {exc} — แสดงแผน DCA ปกติแทน")
        return False

    st.caption(
        "โหมดนี้แทนแผนคะแนน×tilt เฉพาะครั้งที่คุณเปิดเอง — แจกเงินใหม่เข้าตัวที่ต่ำกว่าเป้า "
        "ไม่มีการขาย จึงไม่มีภาษี/ค่าคอมขาขาย (tax-smart rebalance)"
    )
    plan_df = pd.DataFrame(
        [
            {
                "ETF": ticker,
                "ตอนนี้": item["current_pct"],
                "เป้าหมาย": item["target_pct"],
                "เติมเดือนนี้ (บาท)": item["amount_thb"],
                "หลังเติม": item["projected_pct"],
            }
            for ticker, item in plan.items()
        ]
    )
    st.dataframe(
        plan_df.style.format(
            {
                "ตอนนี้": "{:.1f}%",
                "เป้าหมาย": "{:.1f}%",
                "เติมเดือนนี้ (บาท)": "{:,.0f}",
                "หลังเติม": "{:.1f}%",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

    # --- สามอย่างที่แผนนี้ "ไม่ได้พูด" ถ้าไม่บังคับให้พูด (ตัดทิ้งเงียบ = ผิดพอกับกุตัวเลข) ---
    unallocated_thb = float(plan.unallocated_thb)
    if unallocated_thb > 0:
        st.warning(
            f"เหลือเงินที่แจกไม่ลง {unallocated_thb:,.0f} บาท จากงบ {float(plan.budget_thb):,.0f} บาท "
            "(แผนปัดเป็นหลักร้อย) — ยกไปเดือนหน้าหรือเติมเข้ากองที่ต่ำกว่าเป้ามากที่สุดเอง"
        )

    # "ไม่ได้ติดตามแล้ว" กับ "ตั้งเป้าไว้ 0% เอง" คนละเรื่องกัน — ห้ามยุบเป็นข้อความเดียว
    held_untracked = sorted(t for t in current_values if t not in targets)
    if held_untracked:
        st.warning(
            f"ถืออยู่แต่ไม่ได้อยู่ในรายการที่ระบบติดตาม: {', '.join(held_untracked)} — "
            "มูลค่าของกองเหล่านี้ยังนับอยู่ในตัวหารของทุกสัดส่วนด้านบน แต่ไม่มีแถวในแผน "
            "และจะไม่ได้รับเงินเดือนนี้ (ถ้าตั้งใจถือต่อ ให้เพิ่มเข้ารายการที่ติดตามในหน้า Settings)"
        )
    held_zero_target = sorted(
        t for t in current_values if t in targets and float(targets[t]) <= 0
    )
    if held_zero_target:
        st.caption(
            f"ตั้งเป้าไว้ 0% แต่ยังถืออยู่: {', '.join(held_zero_target)} — "
            "โหมดนี้ไม่ขายอะไรทั้งสิ้น จึงได้แค่ไม่เติมเงินเข้าไปอีก "
            "(มูลค่ายังนับอยู่ในตัวหารของทุกสัดส่วนด้านบน)"
        )

    target_without_money = [t for t, w in targets.items() if float(w) > 0 and t not in plan]
    if target_without_money:
        st.caption(
            f"ไม่ได้รับเงินเดือนนี้: {', '.join(target_without_money)} — "
            "สัดส่วนตอนนี้ถึง/เกินเป้าแล้ว หรือส่วนแบ่งที่ควรได้น้อยกว่าหนึ่งก้อน (100 บาท) "
            "ไม่ใช่ว่าถูกตัดออกจากเป้าหมาย"
        )

    _render_execute_list(plan, scores_by_ticker)
    return True


def _render_execute_list(allocation: dict, scores_by_ticker: dict) -> None:
    """รายการซื้อเดือนนี้พร้อมลงมือ (Roadmap ข้อ 11) — แปลงแผน THB เป็น USD/หุ้น/ค่าคอม.

    เลขตั้งต้น (THB ต่อ ETF) มาจาก calculate_allocation ทั้งหมด — ที่เพิ่มคือการแปลงหน่วย
    ด้วย FX สด (utils/fx แหล่งเดียว) และราคาอ้างอิงจาก score payload
    จำนวนหุ้นเป็นค่าประมาณ ณ ราคาปิดล่าสุด — ราคาจริงตอนกดซื้อย่อมต่างเล็กน้อย
    """
    st.subheader("รายการซื้อเดือนนี้ (พร้อมลงมือ)")
    fx = get_usdthb()
    if not fx.is_live:
        st.warning(
            f"FX ใช้ค่าสำรอง {fx.rate:.2f} บาท/USD (ดึงค่าสดไม่ได้) — ตัวเลข USD/หุ้นด้านล่างเป็นประมาณการหยาบ"
        )

    order_rows: list[dict[str, object]] = []
    order_lines: list[str] = []
    total_fee_thb = 0.0
    for ticker, item in allocation.items():
        amount_thb = float(item.get("amount_thb") or 0)
        fee_thb = amount_thb * DIME_FEE_RATE
        total_fee_thb += fee_thb
        amount_usd = amount_thb / fx.rate
        price = scores_by_ticker.get(str(ticker), {}).get("price")
        shares_est = (amount_usd / float(price)) if price else None
        order_rows.append(
            {
                "ETF": ticker,
                "จ่าย (บาท)": amount_thb,
                "≈ USD": amount_usd,
                "ราคาอ้างอิง": float(price) if price else None,
                "≈ หุ้น": shares_est,
                "ค่าคอม 0.15% (บาท)": fee_thb,
            }
        )
        if shares_est is not None:
            order_lines.append(
                f"{ticker}: {amount_thb:,.0f} บาท ≈ {amount_usd:,.2f} USD ≈ {shares_est:.4f} หุ้น @ ${float(price):,.2f}"
            )
        else:
            order_lines.append(f"{ticker}: {amount_thb:,.0f} บาท ≈ {amount_usd:,.2f} USD (ไม่มีราคาอ้างอิง)")

    st.dataframe(
        pd.DataFrame(order_rows).style.format(
            {
                "จ่าย (บาท)": "{:,.0f}",
                "≈ USD": "{:,.2f}",
                "ราคาอ้างอิง": "${:,.2f}",
                "≈ หุ้น": "{:,.4f}",
                "ค่าคอม 0.15% (บาท)": "{:,.2f}",
            },
            na_rep="N/A",
        ),
        use_container_width=True,
        hide_index=True,
    )
    st.code("\n".join(order_lines), language=None)
    st.caption(
        f"คัดลอกรายการด้านบนไปใช้ตอนกดซื้อในแอปโบรกได้เลย (ใส่เป็นจำนวนเงิน) · "
        f"FX {fx.rate:.2f} บาท/USD ({'สด' if fx.is_live else 'ค่าสำรอง'}) · "
        f"ค่าคอมรวมโดยประมาณ {total_fee_thb:,.2f} บาท หักอัตโนมัติโดยโบรก — "
        "จำนวนหุ้นเป็นประมาณการ ณ ราคาปิดล่าสุด ไม่ใช่ราคาที่จะได้จริง"
    )


def _render_drift_advisory() -> None:
    """เทียบพอร์ตจริงกับเป้าหมาย (Roadmap ข้อ 7) — advisory เท่านั้น ไม่แก้เลขจัดสรร.

    ใช้เกณฑ์ drift 5% เดียวกับ rebalance_service เพื่อไม่สร้างนิยามใหม่
    ราคาของที่ถืออยู่ขาดแม้ตัวเดียว = **ไม่ตอบ** (เหมือนข้อ 1.3 ฝั่ง backend)
    เพราะ drift ทุกตัวหารด้วยมูลค่าพอร์ตรวม ถ้าตัวหารไม่ครบ ตัวเลขจะเอียงทั้งกระดาน

    **เป้าหมายต้องมาจากทั้งพอร์ตที่ระบบติดตามเสมอ** (:func:`_tracked_target_weights`
    ตัวเดียวกับที่โหมด "ดึงพอร์ตเข้าเป้า" ใช้ — ห้ามมีสองสูตร)
    เดิมส่งเฉพาะ ticker ที่ *ถืออยู่แล้ว* เข้าไป → ``portfolio/targets.py`` normalize
    ใหม่ให้รวมเป็น 1.0 บนเซ็ตย่อยนั้น กองที่ยังไม่เคยซื้อจึงหายจากทั้งตัวหารและ
    ตารางผลลัพธ์ ⇒ "ขาดกองนั้นทั้งกอง" ถูกแปลงเป็น drift = 0 แล้วหน้าจอสรุปว่า
    "ใกล้เป้าหมายทุกตัว" ทั้งที่ห่างจากเป้าจริงเกือบ 30 จุด (AUDIT_2026-08-06 H7)
    กองที่ยังไม่ถือต้องอยู่ในรายการด้วย ``actual = 0%`` (drift = −target)
    """
    try:
        holdings = get_portfolio_summary()
    except Exception as exc:
        st.caption(f"อ่านพอร์ตจริงไม่ได้ ({exc}) — ข้ามคำแนะนำ drift")
        return
    if holdings.empty:
        return  # ยังไม่มีพอร์ตจริง — ไม่มีอะไรให้เทียบ
    unpriced = _unpriced_tickers(holdings)
    if unpriced:
        st.warning(
            f"ดึงราคาไม่สำเร็จ: {', '.join(unpriced)} — ยังบอกไม่ได้ว่าพอร์ตเอียงจากเป้าแค่ไหน "
            "(ถ้าคิดจากเฉพาะตัวที่มีราคา ตัวที่เหลือจะดูเกินสัดส่วนทั้งที่ไม่ได้เกินจริง)"
        )
        return
    priced = holdings[holdings["Price OK"]]
    total_value = float(priced["Current Value (THB)"].sum()) if not priced.empty else 0.0
    if total_value <= 0:
        return

    try:
        targets = _tracked_target_weights()  # สูตรเดียวกับโหมด rebalance — ห้ามมีสองสูตร
    except InvalidTargetWeights as exc:
        # คอนฟิกผิดรูป = ไม่รู้เป้าหมายจริง ห้ามเดาแทนแล้วสรุปว่าเอียง/ไม่เอียง
        _render_invalid_target_weights(exc)
        return

    actual_pct_by_ticker = {
        str(holding["Ticker"]).strip().upper(): float(holding["Current Value (THB)"]) / total_value * 100.0
        for _, holding in priced.iterrows()
    }
    # union ของ "กองที่ระบบติดตาม/มีเป้าหมาย" กับ "กองที่ถืออยู่จริง" — ไม่มีฝั่งไหนหาย
    # (``targets`` ครอบ ticker ที่ระบบติดตามครบอยู่แล้ว เพราะถามด้วย get_tickers())
    universe = list(dict.fromkeys([*targets, *actual_pct_by_ticker]))
    drifts: list[tuple[str, float, bool]] = []
    for ticker in universe:
        actual_pct = actual_pct_by_ticker.get(ticker, 0.0)
        target_pct = float(targets.get(ticker, 0.0)) * 100.0
        drifts.append((ticker, actual_pct - target_pct, ticker not in actual_pct_by_ticker))

    DRIFT_THRESHOLD_PCT = 5.0
    off_target = [item for item in drifts if abs(item[1]) >= DRIFT_THRESHOLD_PCT]
    if not off_target:
        st.caption(f"พอร์ตจริงใกล้เป้าหมายทุกตัว (drift < {DRIFT_THRESHOLD_PCT:.0f}%) — DCA ตามแผนได้เลย")
        return
    drift_text = ", ".join(
        f"{t} {d:+.1f}% จากเป้า" + (" (ยังไม่ได้ซื้อ)" if not_held else "")
        for t, d, not_held in sorted(off_target, key=lambda x: -abs(x[1]))
    )
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

    # --- การ์ดคำตัดสินเดือนนี้ ---
    st.subheader("คำตัดสินเดือนนี้")
    st.caption(
        f"ซื้อทุกตัวที่มีข้อมูล**และมีน้ำหนักเป้าหมาย** ตามแผน DCA — คะแนนแค่เอียงน้ำหนักจากเป้า "
        f"({TILT_MIN:.1f}–{TILT_MAX:.1f} เท่า) ไม่มีการเลือกตัวเดียวเพราะคะแนนสูง "
        "ตัวที่ไม่ได้เงินรอบนี้จะถูกระบุชื่อพร้อมเหตุผลไว้ด้านล่างเสมอ"
    )
    # คำเตือน "ไม่มีข้อมูล: ..." ต้องขึ้นจอ **ก่อน** จัดสรร (AUDIT_ROUND2_2026-08-07 G1)
    # เดิมอยู่หลัง calculate_allocation() พอการจัดสรรโยน exception หน้าทั้งหน้าก็ตายไป
    # พร้อมกับคำเตือนที่บอกสาเหตุจริง ผู้ใช้จึงเห็นแต่ข้อความที่ชี้ไปผิดที่
    if no_data_rows:
        missing = ", ".join(str(r.get("ticker")) for r in no_data_rows)
        st.warning(f"ไม่มีข้อมูล: {missing} — ไม่ถูกนำมาคิดคะแนน/จัดสรร งบส่วนนั้นกระจายให้ตัวที่เหลือ")

    # แยกสาเหตุให้ตรงชนิด: คอนฟิกผิด (แก้ config.json ได้) ≠ ดึงราคาไม่สำเร็จ (รอรอบหน้า)
    # และห้ามปล่อยให้หลุดไปที่ ``except Exception`` ของ render_dashboard ซึ่งจะกลืนทั้งหน้า
    allocation_error: TargetWeightsError | None = None
    allocation: dict[str, dict] = {}
    plan: AllocationPlan | None = None
    try:
        # ``_with_status`` ไม่ใช่ ``calculate_allocation`` เปล่า ๆ — ต้องได้ ``excluded``
        # มาด้วย ไม่งั้น ETF ที่ตั้งเป้าไว้ 0% หรือส่วนแบ่งไม่ถึงก้อนละ 100 บาท
        # จะหายจากตารางเงียบ ๆ ใต้คำโปรยที่บอกว่า "ไม่ตัดตัวไหนออก" (T7)
        plan = calculate_allocation_with_status(scores_by_ticker, float(budget_thb))
        allocation = plan.allocation
    except NoTargetForSubset as exc:
        allocation_error = exc
        _render_no_target_for_subset(exc)
    except InvalidTargetWeights as exc:
        allocation_error = exc
        _render_invalid_target_weights(exc)

    if plan is not None:
        _render_allocation_exclusions(
            plan, already_named={str(r.get("ticker")) for r in no_data_rows}
        )

    if allocation_error is not None:
        # บอกเหตุผลจริงไปแล้วด้านบน — ห้ามตกไปที่ "ไม่มี ETF ที่ข้อมูลพร้อม หรืองบเป็นศูนย์"
        # ซึ่งเป็นคนละสาเหตุ · ส่วนที่เหลือของหน้า (คะแนน/drift) ไม่ต้องใช้แผนจัดสรร จึงเดินต่อ
        pass
    elif allocation and _render_rebalance_mode(float(budget_thb), scores_by_ticker):
        pass  # โหมดดึงเข้าเป้า render แผนของตัวเองแล้ว — ข้ามแผน tilt ปกติของเดือนนี้
    elif allocation:
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

        _render_execute_list(allocation, scores_by_ticker)
    else:
        st.warning("จัดสรรไม่ได้ — ไม่มี ETF ที่ข้อมูลพร้อม หรืองบเป็นศูนย์")

    _render_drift_advisory()

    # --- stacked bar: คะแนนรวมแยก 8 องค์ประกอบ ---
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
    no_momentum = [str(r.get("ticker")) for r in ranked if not _momentum_available(r)]
    if no_momentum:
        # แท่ง Momentum ของตัวเหล่านี้ยาว 0 เพราะ "ไม่มีข้อมูล" ไม่ใช่ "ได้ 0 คะแนน" — ต้องบอก
        st.caption(
            f"{', '.join(no_momentum)}: ข้อมูลราคาไม่พอคำนวณผลตอบแทน 1/3 เดือน — "
            "หมวด Momentum ถูกตัดออกจากคะแนนเต็มของตัวนั้น (แท่งจึงว่าง ไม่ใช่ได้ 0 คะแนน)"
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

        # ลำดับสำคัญ: **วาด sidebar แล้วแยกหน้าก่อน แล้วค่อยแตะข้อมูลราคา**
        # (AUDIT_ROUND2_2026-08-07 — รูปเดียวกับบั๊ก CRITICAL ของ target_weights)
        #
        # เดิม ``cached_prices()`` ถูกเรียกก่อน แล้ว ``PriceDataUnavailableError``
        # ทำให้ฟังก์ชัน ``return`` ทิ้งตั้งแต่ยังไม่ได้วาดแถบข้าง ⇒ yfinance ติด rate limit
        # ครั้งเดียวลากหน้า News / Price Alerts / Settings ดับไปด้วย ทั้งที่ทั้งสามหน้า
        # ไม่ได้ใช้ราคาจากตรงนี้เลยสักหน้า (News ตั้งใจให้ทำงานได้แม้ทุกอย่างล่ม)
        # เฉพาะหน้าที่ใช้ ``prices`` จริงเท่านั้นที่ควรดับตาม
        config = load_config()
        default_page = str(config["display"]["default_page"])
        _render_custom_sidebar(default_page)
        page = st.session_state.get("page", "Overview")

        # --- หน้าที่ไม่ต้องใช้ราคาชุดนี้: ส่งต่อได้ทันที ---
        if page == "Scorecard":
            render_scorecard_page()
            return
        elif page == "Portfolio":
            render_portfolio_page()
            return
        elif page == "DCF Analysis":
            render_dcf_analysis_page()
            return
        elif page == "AI Advisor":
            render_ai_advisor_page()
            return
        elif page == "Macro":
            render_macro_page()
            return
        elif page == "News":
            render_news_page()
            return
        elif page == "Price Alerts":
            render_price_alerts_page()
            return
        elif page == "Settings":
            render_settings_page()
            return

        # --- ตั้งแต่บรรทัดนี้ลงไปคือหน้าที่ใช้ราคาจริง (Overview/Correlation/Backtest/
        #     DCA Simulator/Technical Signals) ราคาพังจึงยอมให้ดับได้เฉพาะหน้าเหล่านี้ ---
        try:
            with st.spinner("กำลังโหลดข้อมูลราคา..."):
                prices = cached_prices(tickers, years=10)
        except PriceDataUnavailableError as exc:
            # ข้อมูลราคาพัง = หยุดทันที ห้ามแสดงหน้าวิเคราะห์จากข้อมูลว่าง (AUDIT.md C1)
            st.error(f"ดึงข้อมูลราคา ETF ไม่สำเร็จ: {exc}")
            st.info("ลองกด Refresh Data อีกครั้งในอีกสักครู่ (yfinance อาจจำกัดการเรียกชั่วคราว)")
            st.caption(
                "หน้าอื่นที่ไม่ได้ใช้ราคาย้อนหลัง (News, Price Alerts, Portfolio, Settings) "
                "ยังเข้าได้ตามปกติจากแถบด้านซ้าย"
            )
            return

        # เฟรมที่ยาวพอสำหรับหน้าต่าง 10Y — **แยกจากเฟรมหลักโดยตั้งใจ** เพราะ
        # risk/correlation/backtest อ่านเฟรมหลัก การขยายช่วงจะเปลี่ยนตัวเลขความเสี่ยง
        # เงียบ ๆ ทั้งที่ไม่มีใครขอ (กติกาเดียวกับ ``etf_service._prices_df_for_returns``)
        # ดึงไม่สำเร็จ = ถอยไปใช้เฟรมหลัก **พร้อมบอกเหตุผล** ไม่ใช่ทำให้หน้าดับ และไม่ใช่
        # ปล่อยให้ N/A ของ 10Y ถูกอ่านว่า "ไม่มีผลตอบแทน" (FIX_PLAN ข้อ 2.8)
        returns_prices = prices
        returns_history_error: str | None = None
        try:
            returns_prices = cached_returns_prices(tickers)
        except PriceDataUnavailableError as exc:
            # **ห้าม return ตรงนี้** — เฟรมหลักดึงมาได้แล้ว หน้าอื่นทั้งหน้าใช้ได้ปกติ
            # ขาดแค่แท่งส่วนเกินของหน้าต่าง 10Y ⇒ เดินต่อโดยใช้เฟรมหลักแล้วบอกเหตุผล
            returns_history_error = str(exc)

        # สัดส่วนเป้าหมายจากแหล่งเดียว (portfolio/targets.py) — ตรงกับที่ DCA/rebalance ใช้
        # ผ่าน _tracked_target_weights() ตัวเดียวกับหน้า Scorecard เพื่อไม่ให้มีสองทางเข้า
        #
        # **ห้ามให้บรรทัดนี้ล้มก่อนวาด sidebar** (AUDIT_ROUND2_2026-08-07 CRITICAL/T7):
        # ``get_target_weights()`` โยน InvalidTargetWeights เมื่อ config.json ผิด (ถูกต้อง
        # ตามกฎ fail-loud) แต่เดิม exception ตกไปที่ ``except Exception`` ท้ายฟังก์ชัน
        # ⇒ sidebar ไม่เคยถูกวาด ทุกหน้าดับพร้อมกัน รวม News/Price Alerts ที่ไม่ได้ใช้
        # น้ำหนักเป้าหมายเลย และรวม **หน้า Settings ซึ่งเป็นทางเดียวในแอปที่จะไปแก้ค่านั้น**
        # ค่านี้ถูกใช้แค่ 2 หน้า (Backtest / DCA Simulator) ทั้งคู่รับ None ได้และแสดง
        # เหตุผลของตัวเอง — ที่เหลือต้องเดินต่อตามปกติ
        default_weights: dict[str, float] | None
        try:
            default_weights = _tracked_target_weights()
            weights_error: Exception | None = None
        except TargetWeightsError as exc:
            default_weights, weights_error = None, exc

        if page == "Backtest":
            render_backtest_page(prices, default_weights, tickers, weights_error)
            return
        elif page == "DCA Simulator":
            render_dca_simulator_page(prices, default_weights, tickers, weights_error)
            return
        elif page == "Technical Signals":
            render_technical_signals_page(prices)
            return

        # Overview / Correlation ใช้เนื้อหาชุดเดียวกันด้านล่าง
        _render_pdf_export_panel(
            section_key="overview",
            prepare_label="Export Monthly Report",
            download_label="ดาวน์โหลด PDF",
        )
        st.divider()
        _render_realtime_price_ticker_bar()
        _render_overview_metrics(prices, tickers, returns_prices, returns_history_error)
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
            with st.spinner("กำลังคำนวณผลตอบแทนย้อนหลัง..."):
                returns_df = calculate_period_returns(returns_prices)
            st.dataframe(returns_df.style.format("{:.2f}%", na_rep="N/A"))
            st.caption("*QQQM เริ่มซื้อขายปี 2020 — ช่วงก่อนหน้าจึงไม่มีข้อมูล")
            # N/A ในตารางมีได้สองเหตุผล (ยังไม่เกิด / ดึงไม่ได้) — แยกให้ผู้ใช้เห็น (G7)
            for note in _stale_price_notes(prices):
                st.caption(note)

        with col2:
            st.subheader("Risk Metrics")
            # ทุกสูตรความเสี่ยงข้าม NaN รายคอลัมน์ ⇒ แต่ละ ETF ถูกวัดด้วย **ช่วงเวลาของ
            # ตัวเอง** แล้ววางเรียงกันเหมือนเทียบกันได้ (FIX_PLAN ข้อ 2.7) — QQQM ลิสต์ปี
            # 2020 จึงไม่เคยเจอโควิดครัช วัดจริงแล้วช่องว่างระหว่าง VOO กับ QQQM ห่างกัน
            # 10.5 จุดเมื่อเทียบช่วงเดียวกัน และ "ตัวที่ drawdown ตื้นสุด" เปลี่ยนตัว
            # หน้า Return Analysis ข้าง ๆ มี caption เตือนเรื่องนี้อยู่แล้ว ตารางนี้ไม่มี
            common_only = st.toggle(
                "เทียบเฉพาะช่วงที่ทุกกองมีข้อมูลพร้อมกัน",
                value=False,
                key="risk_common_window",
                help=(
                    "ปิดอยู่ = แต่ละกองใช้ประวัติของตัวเองเต็มช่วง (ตัวเลขเยอะกว่าแต่ "
                    "**เทียบข้ามกองไม่ได้**) · เปิด = ตัดเหลือช่วงร่วม ซึ่งเป็นโหมดเดียว "
                    "ที่เอาตัวเลขมาเรียงเทียบกันได้จริง"
                ),
            )
            with st.spinner("กำลังคำนวณตัวชี้วัดความเสี่ยง..."):
                try:
                    risk_df = calculate_risk_metrics(prices, common_window=common_only)
                except ValueError as exc:
                    st.warning(f"เทียบช่วงร่วมไม่ได้: {exc}")
                    risk_df = calculate_risk_metrics(prices)
                    common_only = False
            st.dataframe(
                risk_df.style.format(
                    {
                        "Volatility": "{:.4f}",
                        "Sharpe Ratio": "{:.4f}",
                        "Max Drawdown": "{:.4f}",
                        "Days": "{:,.0f}",
                    },
                    na_rep="N/A",
                )
            )
            if common_only:
                st.caption(
                    "โหมดช่วงร่วม: ทุกกองถูกวัดบนวันเดียวกันทั้งหมด ⇒ ตัวเลขในคอลัมน์เดียวกัน"
                    "เอามาเรียงเทียบกันได้ · ช่วงจะสั้นเท่ากองที่ลิสต์ทีหลังที่สุด"
                )
            else:
                st.caption(
                    "⚠️ แต่ละกองวัดจาก **ช่วงเวลาของตัวเอง** (ดูคอลัมน์ Data Start/Days) — "
                    "กองที่ลิสต์ทีหลังยังไม่เคยเจอวิกฤตที่กองเก่าเจอมาแล้ว ตัวเลข MaxDD/"
                    "Volatility จึงดูดีกว่าโดยไม่ได้แปลว่าเสี่ยงน้อยกว่า "
                    "เปิดสวิตช์ด้านบนเพื่อเทียบบนช่วงเดียวกัน"
                )

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
