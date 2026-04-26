"""Portfolio tracker: เก็บธุรกรรมและสรุปพอร์ตจากไฟล์ CSV."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import yfinance as yf

CSV_COLUMNS = ["date", "ticker", "shares", "price_usd", "amount_thb", "note"]
TRACKER_DIR = Path(__file__).resolve().parent
DATA_DIR = TRACKER_DIR / "data"
TRANSACTIONS_FILE = DATA_DIR / "transactions.csv"
DEFAULT_USDTHB = 33.5


def _ensure_storage() -> None:
    """สร้างโฟลเดอร์และไฟล์ transactions.csv หากยังไม่มี."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not TRANSACTIONS_FILE.exists():
        pd.DataFrame(columns=CSV_COLUMNS).to_csv(TRANSACTIONS_FILE, index=False)


def _load_transactions() -> pd.DataFrame:
    """อ่านธุรกรรมจาก CSV และ normalize ชนิดข้อมูล."""
    _ensure_storage()
    df = pd.read_csv(TRANSACTIONS_FILE)
    if df.empty:
        return pd.DataFrame(columns=CSV_COLUMNS)

    for col in CSV_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    df = df[CSV_COLUMNS].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["shares"] = pd.to_numeric(df["shares"], errors="coerce")
    df["price_usd"] = pd.to_numeric(df["price_usd"], errors="coerce")
    df["amount_thb"] = pd.to_numeric(df["amount_thb"], errors="coerce")
    df["note"] = df["note"].fillna("").astype(str)
    df = df.dropna(subset=["date", "ticker", "shares", "price_usd", "amount_thb"])
    return df.sort_values("date").reset_index(drop=True)


def _get_latest_prices(tickers: list[str]) -> dict[str, float]:
    """ดึงราคาล่าสุดของแต่ละ ticker จาก yfinance."""
    if not tickers:
        return {}

    try:
        downloaded = yf.download(
            tickers=tickers,
            period="5d",
            interval="1d",
            auto_adjust=False,
            progress=False,
            group_by="ticker",
        )
    except Exception:
        return {}

    if downloaded.empty:
        return {}

    prices: dict[str, float] = {}
    if isinstance(downloaded.columns, pd.MultiIndex):
        for ticker in tickers:
            if ticker not in downloaded.columns.get_level_values(0):
                continue
            close_series = pd.to_numeric(downloaded[ticker].get("Close"), errors="coerce").dropna()
            if not close_series.empty:
                prices[ticker] = float(close_series.iloc[-1])
        return prices

    close_series = pd.to_numeric(downloaded.get("Close"), errors="coerce").dropna()
    if len(tickers) == 1 and not close_series.empty:
        prices[tickers[0]] = float(close_series.iloc[-1])
    return prices


def _get_usdthb_rate() -> float:
    """ดึงอัตราแลกเปลี่ยน USD/THB ล่าสุด; ล้มเหลวให้ใช้ค่า default."""
    try:
        fx_df = yf.download("THB=X", period="5d", interval="1d", auto_adjust=False, progress=False)
        if fx_df.empty:
            return DEFAULT_USDTHB
        close_series = pd.to_numeric(fx_df.get("Close"), errors="coerce").dropna()
        if close_series.empty:
            return DEFAULT_USDTHB
        return float(close_series.iloc[-1])
    except Exception:
        return DEFAULT_USDTHB


def add_transaction(
    date: str,
    ticker: str,
    shares: float,
    price_usd: float,
    amount_thb: float,
    note: str = "",
) -> None:
    """บันทึกรายการซื้อใหม่ลง CSV."""
    if not ticker or shares <= 0 or price_usd <= 0 or amount_thb <= 0:
        raise ValueError("ticker, shares, price_usd และ amount_thb ต้องมีค่ามากกว่า 0")

    _ensure_storage()
    row = {
        "date": pd.to_datetime(date).strftime("%Y-%m-%d"),
        "ticker": ticker.strip().upper(),
        "shares": float(shares),
        "price_usd": float(price_usd),
        "amount_thb": float(amount_thb),
        "note": note.strip(),
    }
    pd.DataFrame([row], columns=CSV_COLUMNS).to_csv(
        TRANSACTIONS_FILE,
        mode="a",
        header=False,
        index=False,
    )


def get_portfolio_summary() -> pd.DataFrame:
    """สรุปพอร์ตปัจจุบันรายสินทรัพย์ พร้อม P&L และ % Return."""
    transactions = _load_transactions()
    if transactions.empty:
        return pd.DataFrame(
            columns=[
                "Ticker",
                "Shares",
                "Avg Cost (USD)",
                "Current Price (USD)",
                "Invested (USD)",
                "Invested (THB)",
                "Current Value (USD)",
                "Current Value (THB)",
                "P&L (USD)",
                "P&L (THB)",
                "Return (%)",
            ]
        )

    transactions["cost_usd"] = transactions["shares"] * transactions["price_usd"]
    grouped = (
        transactions.groupby("ticker", as_index=False)
        .agg(
            shares=("shares", "sum"),
            invested_usd=("cost_usd", "sum"),
            invested_thb=("amount_thb", "sum"),
        )
        .sort_values("ticker")
    )
    grouped["avg_cost_usd"] = grouped["invested_usd"] / grouped["shares"]

    tickers = grouped["ticker"].tolist()
    latest_prices = _get_latest_prices(tickers)
    fx_rate = _get_usdthb_rate()

    grouped["current_price_usd"] = grouped["ticker"].map(latest_prices).fillna(0.0)
    grouped["current_value_usd"] = grouped["shares"] * grouped["current_price_usd"]
    grouped["current_value_thb"] = grouped["current_value_usd"] * fx_rate
    grouped["pnl_usd"] = grouped["current_value_usd"] - grouped["invested_usd"]
    grouped["pnl_thb"] = grouped["current_value_thb"] - grouped["invested_thb"]
    grouped["return_pct"] = grouped["pnl_usd"] / grouped["invested_usd"] * 100.0

    return grouped.rename(
        columns={
            "ticker": "Ticker",
            "shares": "Shares",
            "avg_cost_usd": "Avg Cost (USD)",
            "current_price_usd": "Current Price (USD)",
            "invested_usd": "Invested (USD)",
            "invested_thb": "Invested (THB)",
            "current_value_usd": "Current Value (USD)",
            "current_value_thb": "Current Value (THB)",
            "pnl_usd": "P&L (USD)",
            "pnl_thb": "P&L (THB)",
            "return_pct": "Return (%)",
        }
    )[
        [
            "Ticker",
            "Shares",
            "Avg Cost (USD)",
            "Current Price (USD)",
            "Invested (USD)",
            "Invested (THB)",
            "Current Value (USD)",
            "Current Value (THB)",
            "P&L (USD)",
            "P&L (THB)",
            "Return (%)",
        ]
    ]


def get_total_summary() -> dict[str, float]:
    """สรุปภาพรวมพอร์ตทั้งหมดในหน่วย THB."""
    holdings = get_portfolio_summary()
    if holdings.empty:
        return {
            "total_invested_thb": 0.0,
            "current_value_thb": 0.0,
            "total_pnl_thb": 0.0,
            "total_return_pct": 0.0,
        }

    total_invested = float(holdings["Invested (THB)"].sum())
    current_value = float(holdings["Current Value (THB)"].sum())
    total_pnl = current_value - total_invested
    total_return_pct = (total_pnl / total_invested * 100.0) if total_invested else 0.0
    return {
        "total_invested_thb": total_invested,
        "current_value_thb": current_value,
        "total_pnl_thb": total_pnl,
        "total_return_pct": total_return_pct,
    }


def get_transactions(ticker: str | None = None) -> pd.DataFrame:
    """ดึงประวัติการซื้อขายทั้งหมด หรือกรองตาม ticker."""
    transactions = _load_transactions()
    if ticker:
        ticker_upper = ticker.strip().upper()
        transactions = transactions[transactions["ticker"] == ticker_upper]
    return transactions.sort_values("date", ascending=False).reset_index(drop=True)
