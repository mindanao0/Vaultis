from __future__ import annotations

import requests
import streamlit as st

API_BASE_URL = "http://localhost:8000"


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
