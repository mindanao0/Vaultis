# -*- coding: utf-8 -*-
"""Portfolio tracker: เก็บธุรกรรมและสรุปพอร์ตจากไฟล์ CSV.

นโยบายราคา (AUDIT.md C1): ราคาที่ดึงไม่ได้ = NaN + ธง "Price OK" = False
ห้ามเติม 0 เด็ดขาด (เดิมทำให้พอร์ตโชว์ขาดทุน -100% ปลอมและหลอก AI advisor)
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import yfinance as yf
from utils.config import load_config

logger = logging.getLogger(__name__)

CSV_COLUMNS = ["date", "ticker", "shares", "price_usd", "fx_rate_thb", "amount_thb", "fee_thb", "note"]
TRACKER_DIR = Path(__file__).resolve().parent
DATA_DIR = TRACKER_DIR / "data"
TRANSACTIONS_FILE = DATA_DIR / "transactions.csv"
DEFAULT_USDTHB = float(load_config()["display"]["default_fx_rate"])
FEE_RATE = 0.0015


def _ensure_storage() -> None:
    """สร้างโฟลเดอร์และไฟล์ transactions.csv หากยังไม่มี."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not TRANSACTIONS_FILE.exists():
        pd.DataFrame(columns=CSV_COLUMNS).to_csv(TRANSACTIONS_FILE, index=False)
        return

    existing_df = pd.read_csv(TRANSACTIONS_FILE)
    changed = False
    for col in CSV_COLUMNS:
        if col not in existing_df.columns:
            existing_df[col] = 0.0 if col == "fee_thb" else ""
            changed = True

    if changed:
        existing_df = existing_df[CSV_COLUMNS]
        existing_df.to_csv(TRANSACTIONS_FILE, index=False)


def _calculate_dime_fee_info(transactions: pd.DataFrame) -> pd.DataFrame:
    """คำนวณลำดับเทรดรายเดือนและค่าธรรมเนียม Dime ของแต่ละรายการ."""
    if transactions.empty:
        result = transactions.copy()
        result["trade_number_in_month"] = pd.Series(dtype="int64")
        result["fee_thb"] = pd.Series(dtype="float64")
        return result

    result = transactions.sort_values("date").reset_index(drop=True).copy()
    result["trade_month"] = result["date"].dt.to_period("M")
    result["trade_number_in_month"] = result.groupby("trade_month").cumcount() + 1
    result["trade_value_usd"] = result["shares"] * result["price_usd"]
    result["fee_thb"] = (
        result["trade_value_usd"] * FEE_RATE * result["fx_rate_thb"]
    ).where(result["trade_number_in_month"] > 1, 0.0)
    return result.drop(columns=["trade_month", "trade_value_usd"])


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
    df["fx_rate_thb"] = pd.to_numeric(df["fx_rate_thb"], errors="coerce").fillna(DEFAULT_USDTHB)
    df["amount_thb"] = pd.to_numeric(df["amount_thb"], errors="coerce")
    df["fee_thb"] = pd.to_numeric(df["fee_thb"], errors="coerce")
    df["note"] = df["note"].fillna("").astype(str)
    df = df.dropna(subset=["date", "ticker", "shares", "price_usd", "fx_rate_thb", "amount_thb"])
    return _calculate_dime_fee_info(df)


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
    except Exception as exc:
        logger.warning("ดึงราคาล่าสุดไม่สำเร็จ (%s): %s", tickers, exc)
        return {}

    if downloaded.empty:
        logger.warning("ดึงราคาล่าสุดได้ผลว่างเปล่า (%s)", tickers)
        return {}

    prices: dict[str, float] = {}
    if isinstance(downloaded.columns, pd.MultiIndex):
        available = set(downloaded.columns.get_level_values(0))
        for ticker in tickers:
            if ticker not in available:
                continue
            close_series = _close_series_from(downloaded[ticker])
            if not close_series.empty:
                prices[ticker] = float(close_series.iloc[-1])
        return prices

    close_series = _close_series_from(downloaded)
    if len(tickers) == 1 and not close_series.empty:
        prices[tickers[0]] = float(close_series.iloc[-1])
    return prices


def _close_series_from(df: pd.DataFrame) -> pd.Series:
    """ดึงคอลัมน์ Close เป็น Series เดียว รองรับทั้ง MultiIndex และคอลัมน์ธรรมดา.

    yfinance รุ่นใหม่คืน MultiIndex (Price, Ticker) แม้ดึง ticker เดียว →
    ``df.get("Close")`` จะได้ DataFrame ทำให้ pd.to_numeric พัง
    (บั๊กเดิมทำให้ FX rate ตกไปใช้ค่า default 33.5 ตลอดโดยไม่มีใครรู้)
    """
    if df.empty:
        return pd.Series(dtype=float)
    if isinstance(df.columns, pd.MultiIndex):
        if "Close" not in df.columns.get_level_values(0):
            return pd.Series(dtype=float)
        close = df.xs("Close", axis=1, level=0)
    else:
        if "Close" not in df.columns:
            return pd.Series(dtype=float)
        close = df["Close"]
    if isinstance(close, pd.DataFrame):
        if close.empty or close.shape[1] == 0:
            return pd.Series(dtype=float)
        close = close.iloc[:, 0]
    return pd.to_numeric(close, errors="coerce").dropna()


def _get_usdthb_rate() -> float:
    """ดึงอัตราแลกเปลี่ยน THB/USD ล่าสุด; ล้มเหลวให้ใช้ค่า default."""
    try:
        fx_df = yf.download("THB=X", period="5d", progress=False, auto_adjust=True)
        close_series = _close_series_from(fx_df)
        if close_series.empty:
            logger.warning("ดึงอัตราแลกเปลี่ยน THB=X ไม่ได้ — ใช้ค่า default %.2f", DEFAULT_USDTHB)
            return DEFAULT_USDTHB
        rate = float(close_series.iloc[-1])
        # sanity check: THB/USD ที่สมเหตุสมผลอยู่ราว 25-45 — นอกช่วงนี้แปลว่าข้อมูลผิด
        if not 20.0 <= rate <= 50.0:
            logger.warning("อัตราแลกเปลี่ยนผิดปกติ (%.4f) — ใช้ค่า default %.2f", rate, DEFAULT_USDTHB)
            return DEFAULT_USDTHB
        return rate
    except Exception as exc:
        logger.warning("ดึงอัตราแลกเปลี่ยนไม่สำเร็จ (%s) — ใช้ค่า default %.2f", exc, DEFAULT_USDTHB)
        return DEFAULT_USDTHB


def get_today_fx_rate_thb() -> float:
    """คืนค่าอัตราแลกเปลี่ยน THB/USD ล่าสุดพร้อม fallback."""
    return _get_usdthb_rate()


def estimate_dime_fee_thb(
    trade_date: str | pd.Timestamp,
    shares: float,
    price_usd: float,
    fx_rate_thb: float,
) -> tuple[int, float]:
    """คำนวณลำดับเทรดของเดือนและค่าธรรมเนียม Dime โดยประมาณ."""
    transaction_date = pd.to_datetime(trade_date)
    transactions = _load_transactions()
    same_month_count = int(
        (transactions["date"].dt.to_period("M") == transaction_date.to_period("M")).sum()
    )
    trade_number_in_month = same_month_count + 1
    trade_value_usd = float(shares) * float(price_usd)
    fee_thb = trade_value_usd * FEE_RATE * float(fx_rate_thb) if trade_number_in_month > 1 else 0.0
    return trade_number_in_month, fee_thb


def add_transaction(
    date: str,
    ticker: str,
    shares: float,
    price_usd: float,
    fx_rate_thb: float,
    amount_thb: float,
    note: str = "",
) -> None:
    """บันทึกรายการซื้อใหม่ลง CSV."""
    if not ticker or shares <= 0 or price_usd <= 0 or fx_rate_thb <= 0 or amount_thb <= 0:
        raise ValueError("ticker, shares, price_usd, fx_rate_thb และ amount_thb ต้องมีค่ามากกว่า 0")

    _ensure_storage()
    trade_number_in_month, fee_thb = estimate_dime_fee_thb(
        trade_date=date,
        shares=float(shares),
        price_usd=float(price_usd),
        fx_rate_thb=float(fx_rate_thb),
    )

    row = {
        "date": pd.to_datetime(date).strftime("%Y-%m-%d"),
        "ticker": ticker.strip().upper(),
        "shares": float(shares),
        "price_usd": float(price_usd),
        "fx_rate_thb": float(fx_rate_thb),
        "amount_thb": float(amount_thb),
        "fee_thb": float(fee_thb),
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
                "FX Rate (Buy)",
                "Fee (THB)",
                "P&L (USD)",
                "P&L (THB)",
                "Return (%)",
                "Price OK",
            ]
        )

    transactions["cost_usd"] = transactions["shares"] * transactions["price_usd"]
    transactions["cost_thb"] = transactions["cost_usd"] * transactions["fx_rate_thb"]
    transactions["fx_cost_weight"] = transactions["fx_rate_thb"] * transactions["cost_usd"]
    grouped = (
        transactions.groupby("ticker", as_index=False)
        .agg(
            shares=("shares", "sum"),
            invested_usd=("cost_usd", "sum"),
            invested_thb=("cost_thb", "sum"),
            fx_weight_sum=("fx_cost_weight", "sum"),
            total_fee_thb=("fee_thb", "sum"),
        )
        .sort_values("ticker")
    )
    grouped["avg_cost_usd"] = grouped["invested_usd"] / grouped["shares"]
    grouped["fx_rate_buy"] = grouped["fx_weight_sum"] / grouped["invested_usd"]

    tickers = grouped["ticker"].tolist()
    latest_prices = _get_latest_prices(tickers)
    fx_rate = _get_usdthb_rate()

    # ราคาที่ดึงไม่ได้ต้องเป็น NaN — เดิม fillna(0) ทำให้ P&L โชว์ -100% ปลอม (AUDIT.md C1)
    grouped["current_price_usd"] = grouped["ticker"].map(latest_prices)
    grouped["price_ok"] = grouped["current_price_usd"].notna()
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
            "fx_rate_buy": "FX Rate (Buy)",
            "total_fee_thb": "Fee (THB)",
            "pnl_usd": "P&L (USD)",
            "pnl_thb": "P&L (THB)",
            "return_pct": "Return (%)",
            "price_ok": "Price OK",
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
            "FX Rate (Buy)",
            "Fee (THB)",
            "P&L (USD)",
            "P&L (THB)",
            "Return (%)",
            "Price OK",
        ]
    ]


def get_total_summary() -> dict[str, object]:
    """สรุปภาพรวมพอร์ตทั้งหมดในหน่วย THB.

    ถ้าดึงราคาบาง ticker ไม่ได้: มูลค่า/กำไรคิดเฉพาะตัวที่มีราคา และรายชื่อตัวที่ขาด
    อยู่ใน ``missing_prices`` — ผู้เรียกต้องแสดงคำเตือนนี้เสมอ (AUDIT.md C1)
    """
    holdings = get_portfolio_summary()
    if holdings.empty:
        return {
            "total_invested_thb": 0.0,
            "current_value_thb": 0.0,
            "total_pnl_thb": 0.0,
            "total_return_pct": 0.0,
            "total_fee_thb": 0.0,
            "missing_prices": [],
        }

    ok = holdings[holdings["Price OK"]]
    missing_prices = holdings.loc[~holdings["Price OK"], "Ticker"].astype(str).tolist()

    total_invested = float(holdings["Invested (THB)"].sum())
    invested_ok = float(ok["Invested (THB)"].sum())
    current_value = float(ok["Current Value (THB)"].sum())
    total_pnl = current_value - invested_ok
    total_return_pct = (total_pnl / invested_ok * 100.0) if invested_ok else 0.0
    total_fee_thb = float(holdings["Fee (THB)"].sum())
    return {
        "total_invested_thb": total_invested,
        "current_value_thb": current_value,
        "total_pnl_thb": total_pnl,
        "total_return_pct": total_return_pct,
        "total_fee_thb": total_fee_thb,
        "missing_prices": missing_prices,
    }


def get_transactions(ticker: str | None = None) -> pd.DataFrame:
    """ดึงประวัติการซื้อขายทั้งหมด หรือกรองตาม ticker."""
    transactions = _load_transactions()
    if ticker:
        ticker_upper = ticker.strip().upper()
        transactions = transactions[transactions["ticker"] == ticker_upper]
    return transactions.sort_values("date", ascending=False).reset_index(drop=True)
