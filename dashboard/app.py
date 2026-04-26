"""Streamlit dashboard สำหรับวิเคราะห์ ETF ระยะยาว."""

from __future__ import annotations

from datetime import datetime
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf
from plotly.subplots import make_subplots

# เพิ่ม path ของ root โปรเจกต์เพื่อให้ import โมดูลข้ามโฟลเดอร์ได้เมื่อรันผ่าน Streamlit
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from analysis.correlation import calculate_correlation_matrix
from analysis.ai_advisor import get_monthly_advice
from analysis.ta_compat import ta
from analysis.returns import calculate_period_returns
from analysis.risk import calculate_risk_metrics
from alerts.notifier import test_alert
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


def _render_pdf_export_panel(section_key: str, prepare_label: str, download_label: str) -> None:
    """Render monthly PDF export controls with Streamlit download button."""
    config = load_config()
    month_text = datetime.today().strftime("%B %Y")
    default_budget = float(config["dca"]["monthly_budget_thb"])
    budget_thb = st.number_input(
        "งบ DCA สำหรับ AI Summary (THB)",
        min_value=500.0,
        value=default_budget,
        step=500.0,
        format="%.0f",
        key=f"{section_key}_pdf_budget",
    )

    cache_key = f"{section_key}_pdf_bytes"
    file_key = f"{section_key}_pdf_filename"
    if st.button(prepare_label, key=f"{section_key}_prepare_pdf"):
        with st.spinner("กำลังสร้างรายงาน PDF..."):
            st.session_state[cache_key] = generate_monthly_report(month=month_text, budget_thb=float(budget_thb))
            st.session_state[file_key] = f"vaultis_monthly_report_{datetime.today():%Y_%m}.pdf"
        st.success("สร้างไฟล์รายงานเรียบร้อยแล้ว กดปุ่มด้านล่างเพื่อดาวน์โหลด")

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
    """หน้าตั้งค่าระบบผ่านไฟล์ config.json."""
    st.header("Settings")
    st.caption("ตั้งค่าการลงทุน, ETF, การแจ้งเตือน และการแสดงผลของแอป")

    config = load_config()
    current_tickers = get_tickers()
    page_options = ["Overview", "Portfolio", "Backtest", "DCA Simulator", "Technical Signals", "AI Advisor", "Macro", "Settings"]

    st.subheader("1) DCA Settings")
    dca_budget = st.number_input(
        "งบ DCA ต่อเดือน (THB)",
        min_value=100.0,
        value=float(config["dca"]["monthly_budget_thb"]),
        step=100.0,
        format="%.0f",
    )
    dca_day = st.number_input(
        "วันที่ DCA ทุกเดือน",
        min_value=1,
        max_value=31,
        value=int(config["dca"]["day_of_month"]),
        step=1,
    )

    st.divider()
    st.subheader("2) ETF Management")
    st.caption("ETF ปัจจุบันทั้งหมด")
    for ticker in current_tickers:
        col_ticker, col_remove = st.columns([4, 1])
        with col_ticker:
            st.text(ticker)
        with col_remove:
            if st.button("❌", key=f"remove_{ticker}"):
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
    webhook_url = st.text_input(
        "Discord Webhook URL",
        value=str(config["notifications"]["discord_webhook_url"]),
        type="password",
    )
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
                "discord_webhook_url": webhook_url.strip(),
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
        return "🔴 Overbought"
    if price >= ma50 and price >= ma200 and rsi_value <= 70:
        return "🟢 Buy Zone"
    return "🟡 Neutral"


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
    st.plotly_chart(fig, use_container_width=True)

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

        st.plotly_chart(
            px.line(
                comparison_df,
                x=comparison_df.index,
                y=["Portfolio Value", f"Benchmark ({benchmark_ticker})"],
                title=f"Portfolio vs Benchmark ({benchmark_ticker})",
            ),
            use_container_width=True,
        )

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

    st.plotly_chart(
        px.line(
            dca_df,
            x=dca_df.index,
            y=["Total Invested", "Portfolio Value"],
            title="เงินสะสม vs มูลค่าพอร์ต",
        ),
        use_container_width=True,
    )

    total_invested = float(dca_df["Total Invested"].iloc[-1])
    current_value = float(dca_df["Portfolio Value"].iloc[-1])
    profit = current_value - total_invested

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Invested", f"${total_invested:,.2f}")
    col2.metric("Current Value", f"${current_value:,.2f}")
    col3.metric("Profit", f"${profit:,.2f}", delta=f"{(profit / total_invested) * 100:.2f}%")


def _extract_allocation_df(parsed_advice: dict | None) -> pd.DataFrame:
    """แปลง JSON allocations จาก Claude ให้เป็น DataFrame."""
    if not parsed_advice or "allocations" not in parsed_advice:
        return pd.DataFrame()

    allocations = parsed_advice.get("allocations", [])
    if not isinstance(allocations, list):
        return pd.DataFrame()

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

        allocation_df = _extract_allocation_df(result.get("parsed_advice"))
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
            st.plotly_chart(pie, use_container_width=True)
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
        return "🟢 สงบ"
    if vix_value <= 30:
        return "🟡 ระวัง"
    return "🔴 กลัว"


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

    st.markdown(
        "เกณฑ์ VIX: 🟢 < 20 (สงบ) | 🟡 20-30 (ระวัง) | 🔴 > 30 (กลัว)"
    )

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
        vix_fig.add_hline(y=20, line_dash="dash", line_color="green", annotation_text="Calm")
        vix_fig.add_hline(y=30, line_dash="dash", line_color="orange", annotation_text="Caution")
        st.plotly_chart(vix_fig, use_container_width=True)

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
        prepare_label="📄 Export Portfolio Report",
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
        m1.metric("💰 เงินลงทุนทั้งหมด (USD)", f"{invested:,.2f}")
        m2.metric("📈 มูลค่าปัจจุบัน (USD)", f"{current:,.2f}")
        m3.metric(
            "✅ กำไร/ขาดทุน (USD)",
            f"{pnl_value:,.2f}",
            delta=f"{total_summary['total_return_pct']:.2f}%",
        )
    else:
        m1.metric("💰 เงินลงทุนทั้งหมด (THB)", f"{total_summary['total_invested_thb']:,.2f}")
        m2.metric("📈 มูลค่าปัจจุบัน (THB)", f"{total_summary['current_value_thb']:,.2f}")
        m3.metric(
            "✅ กำไร/ขาดทุน (THB)",
            f"{total_summary['total_pnl_thb']:,.2f}",
            delta=f"{total_summary['total_return_pct']:.2f}%",
        )
    m4.metric("💱 FX Rate วันนี้", f"{today_fx_rate:.2f} THB/USD")
    m5.metric("💸 ค่าธรรมเนียมรวมทั้งหมด (THB)", f"{total_summary['total_fee_thb']:,.2f}")

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
        st.plotly_chart(pie_fig, use_container_width=True)

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
        st.title("Vaultis - Long-term ETF Analyzer")
        tickers = get_tickers()
        st.caption(f"วิเคราะห์ ETF: {', '.join(tickers)} (ย้อนหลัง 10 ปี)")

        if st.button("🔄 Refresh ข้อมูล"):
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

        st.sidebar.header("Pages")
        page_options = ["Overview", "Portfolio", "Backtest", "DCA Simulator", "Technical Signals", "AI Advisor", "Macro", "Settings"]
        default_page = str(config["display"]["default_page"])
        default_page_index = page_options.index(default_page) if default_page in page_options else 0
        page = st.sidebar.radio(
            "เลือกหน้า",
            page_options,
            index=default_page_index,
        )

        if page == "Portfolio":
            render_portfolio_page()
            return

        if page == "Backtest":
            render_backtest_page(prices, default_weights, tickers)
            return

        if page == "DCA Simulator":
            render_dca_simulator_page(prices, default_weights, tickers)
            return

        if page == "Technical Signals":
            render_technical_signals_page(prices)
            return

        if page == "AI Advisor":
            render_ai_advisor_page()
            return

        if page == "Macro":
            render_macro_page()
            return

        if page == "Settings":
            render_settings_page()
            return

        _render_pdf_export_panel(
            section_key="overview",
            prepare_label="📄 Export รายงานเดือนนี้",
            download_label="ดาวน์โหลด PDF รายงานเดือนนี้",
        )
        st.divider()
        st.subheader("Price Trend (Normalized = 100)")
        normalized_prices = prices.ffill().apply(
            lambda series: (series / series.dropna().iloc[0]) * 100 if not series.dropna().empty else series
        )
        st.plotly_chart(
            px.line(normalized_prices, x=normalized_prices.index, y=normalized_prices.columns),
            use_container_width=True,
        )

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
                [0.0, "#2b6cb0"],   # น้ำเงิน = correlation ต่ำ (-1)
                [0.5, "#ffffff"],   # ขาว = correlation กลาง (0)
                [1.0, "#c53030"],   # แดง = correlation สูง (1)
            ],
            zmin=-1,
            zmax=1,
            origin="lower",
            text_auto=".2f",
        )
        heatmap.update_layout(coloraxis_colorbar_title="Correlation")
        st.plotly_chart(heatmap, use_container_width=True)

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

        st.markdown("**Insight จาก Correlation Heatmap**")
        st.markdown(
            f"- คู่ที่ correlation สูงสุด: **{max_pair[0]} - {max_pair[1]} ({max_value:.2f})** → ถือทั้งคู่อาจซ้ำซ้อน"
        )
        st.markdown(
            f"- คู่ที่ correlation ต่ำสุด: **{min_pair[0]} - {min_pair[1]} ({min_value:.2f})** → กระจายความเสี่ยงได้ดี"
        )
        st.markdown("- ควรผสมสินทรัพย์ที่มี correlation ต่างกันเพื่อลดความผันผวนรวมของพอร์ต")

        st.info("เลือกหน้า Backtest หรือ DCA Simulator จาก Sidebar เพื่อใช้งานเครื่องมือจำลอง")
    except Exception as exc:
        st.error(f"เกิดข้อผิดพลาดใน dashboard: {exc}")


if __name__ == "__main__":
    render_dashboard()
