# -*- coding: utf-8 -*-
"""Portfolio tracker: เก็บธุรกรรมและสรุปพอร์ตจากไฟล์ CSV.

นโยบายราคา (AUDIT.md C1): ราคาที่ดึงไม่ได้ = NaN + ธง "Price OK" = False
ห้ามเติม 0 เด็ดขาด (เดิมทำให้พอร์ตโชว์ขาดทุน -100% ปลอมและหลอก AI advisor)
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

import pandas as pd
import yfinance as yf

from data.fetcher import normalize_close_series
from portfolio.fees import dime_fee_thb
from utils import fx
from utils.config import load_config

logger = logging.getLogger(__name__)

CSV_COLUMNS = [
    "tx_id",
    "date",
    "ticker",
    "shares",
    "price_usd",
    "fx_rate_thb",
    "amount_thb",
    "fee_thb",
    "note",
    "tx_type",
]

# ประเภทธุรกรรมใน ledger (Roadmap Phase 2 ข้อ 5 — เดิม buy-only)
# แถวเก่า/ค่าว่าง/ค่าที่ไม่รู้จัก ถูก normalize เป็น buy เสมอ (backward compatible)
TX_BUY = "buy"
TX_DIVIDEND = "dividend"
TRACKER_DIR = Path(__file__).resolve().parent
DATA_DIR = TRACKER_DIR / "data"
TRANSACTIONS_FILE = DATA_DIR / "transactions.csv"
DEFAULT_USDTHB = float(load_config()["display"]["default_fx_rate"])


def _ensure_storage() -> None:
    """สร้างไฟล์ transactions.csv และเติมคอลัมน์ที่ขาด (รวม tx_id ให้แถวเก่า)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not TRANSACTIONS_FILE.exists():
        pd.DataFrame(columns=CSV_COLUMNS).to_csv(TRANSACTIONS_FILE, index=False)
        return

    existing_df = pd.read_csv(TRANSACTIONS_FILE)
    changed = False
    for col in CSV_COLUMNS:
        if col not in existing_df.columns:
            if col == "fee_thb":
                existing_df[col] = 0.0
            elif col == "tx_type":
                existing_df[col] = TX_BUY  # แถวเก่าทั้งหมดคือรายการซื้อ
            else:
                existing_df[col] = ""
            changed = True

    # แถวเก่าที่ยังไม่มี tx_id → ออกให้ (ใช้อ้างอิงตอนลบผ่าน API)
    if "tx_id" in existing_df.columns and not existing_df.empty:
        missing_id = existing_df["tx_id"].isna() | (existing_df["tx_id"].astype(str).str.strip() == "")
        if missing_id.any():
            existing_df.loc[missing_id, "tx_id"] = [
                str(uuid.uuid4()) for _ in range(int(missing_id.sum()))
            ]
            changed = True

    if changed:
        existing_df = existing_df[CSV_COLUMNS]
        existing_df.to_csv(TRANSACTIONS_FILE, index=False)


def _calculate_dime_fee_info(transactions: pd.DataFrame) -> pd.DataFrame:
    """เติมลำดับเทรดรายเดือน และคำนวณค่าธรรมเนียม Dime *เฉพาะแถวที่ยังไม่มีค่าบันทึกไว้*.

    สูตรกลาง 0.15% ทุก transaction จาก ``portfolio/fees.py`` (มติ 2026-07-16 —
    เดิมคิดเทรดแรกของเดือนฟรี ซึ่งไม่ตรงกับบัญชีจริง)

    (AUDIT.md M12: เดิมคำนวณทับทุกครั้งที่โหลด — ถ้ากติกาโบรกเกอร์เปลี่ยน
    ประวัติค่าธรรมเนียมจริงที่บันทึกไว้จะถูกเขียนทับด้วยสูตรปัจจุบัน)
    """
    if transactions.empty:
        result = transactions.copy()
        result["trade_number_in_month"] = pd.Series(dtype="int64")
        result["fee_thb"] = pd.Series(dtype="float64")
        return result

    result = transactions.sort_values("date").reset_index(drop=True).copy()
    result["trade_month"] = result["date"].dt.to_period("M")
    # ปันผลไม่ใช่เทรด — ไม่นับลำดับเทรดของเดือนและไม่ประมาณค่าธรรมเนียม
    if "tx_type" in result.columns:
        is_trade = result["tx_type"] != TX_DIVIDEND
    else:
        is_trade = pd.Series(True, index=result.index)
    result["trade_number_in_month"] = 0
    result.loc[is_trade, "trade_number_in_month"] = (
        result[is_trade].groupby("trade_month").cumcount() + 1
    )
    result["trade_value_usd"] = result["shares"] * result["price_usd"]

    estimated_fee = dime_fee_thb(result["trade_value_usd"], result["fx_rate_thb"])
    estimated_fee = estimated_fee.where(is_trade, 0.0)
    # ใช้ค่าที่บันทึกไว้ก่อน; เติมด้วยค่าประมาณเฉพาะแถวที่ไม่มีค่า
    result["fee_thb"] = pd.to_numeric(result.get("fee_thb"), errors="coerce").fillna(estimated_fee)
    return result.drop(columns=["trade_month", "trade_value_usd"])


def _empty_transactions() -> pd.DataFrame:
    """DataFrame ว่างที่ dtype ถูกต้อง.

    (เดิมคืน ``pd.DataFrame(columns=CSV_COLUMNS)`` ซึ่งคอลัมน์ date เป็น object →
    ``transactions["date"].dt`` พัง → **การเพิ่มธุรกรรมแรกสุดลงสมุดที่ว่างจะ crash**)
    """
    empty = pd.DataFrame(
        {
            "tx_id": pd.Series(dtype="object"),
            "date": pd.Series(dtype="datetime64[ns]"),
            "ticker": pd.Series(dtype="object"),
            "shares": pd.Series(dtype="float64"),
            "price_usd": pd.Series(dtype="float64"),
            "fx_rate_thb": pd.Series(dtype="float64"),
            "amount_thb": pd.Series(dtype="float64"),
            "fee_thb": pd.Series(dtype="float64"),
            "note": pd.Series(dtype="object"),
            "tx_type": pd.Series(dtype="object"),
        }
    )
    empty["trade_number_in_month"] = pd.Series(dtype="int64")
    return empty


def _load_transactions() -> pd.DataFrame:
    """อ่านธุรกรรมจาก CSV และ normalize ชนิดข้อมูล."""
    _ensure_storage()
    df = pd.read_csv(TRANSACTIONS_FILE)
    if df.empty:
        return _empty_transactions()

    for col in CSV_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    df = df[CSV_COLUMNS].copy()
    df["tx_id"] = df["tx_id"].astype(str).str.strip()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["shares"] = pd.to_numeric(df["shares"], errors="coerce")
    df["price_usd"] = pd.to_numeric(df["price_usd"], errors="coerce")
    df["fx_rate_thb"] = pd.to_numeric(df["fx_rate_thb"], errors="coerce").fillna(DEFAULT_USDTHB)
    df["amount_thb"] = pd.to_numeric(df["amount_thb"], errors="coerce")
    df["fee_thb"] = pd.to_numeric(df["fee_thb"], errors="coerce")
    df["note"] = df["note"].fillna("").astype(str)
    # ค่าว่าง/ไม่รู้จัก = buy เสมอ — ห้ามทิ้งแถวเก่าเงียบ ๆ เพราะ schema เพิ่มทีหลัง
    tx_type = df["tx_type"].fillna("").astype(str).str.strip().str.lower()
    df["tx_type"] = tx_type.where(tx_type.isin([TX_BUY, TX_DIVIDEND]), TX_BUY)
    df = df.dropna(subset=["date", "ticker", "shares", "price_usd", "fx_rate_thb", "amount_thb"])
    if df.empty:
        return _empty_transactions()
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
            close_series = normalize_close_series(downloaded[ticker])
            if not close_series.empty:
                prices[ticker] = float(close_series.iloc[-1])
        return prices

    close_series = normalize_close_series(downloaded)
    if len(tickers) == 1 and not close_series.empty:
        prices[tickers[0]] = float(close_series.iloc[-1])
    return prices


def _get_usdthb_rate() -> float:
    """อัตราแลกเปลี่ยน THB/USD จากแหล่งกลางเดียวของระบบ (utils/fx.py)."""
    return fx.get_usdthb_rate()


def get_today_fx_rate_thb() -> float:
    """คืนค่าอัตราแลกเปลี่ยน THB/USD ล่าสุดพร้อม fallback."""
    return _get_usdthb_rate()


def estimate_dime_fee_thb(
    trade_date: str | pd.Timestamp,
    shares: float,
    price_usd: float,
    fx_rate_thb: float,
) -> tuple[int, float]:
    """คำนวณลำดับเทรดของเดือนและค่าธรรมเนียม Dime โดยประมาณ.

    0.15% ทุก transaction (มติ 2026-07-16) — ลำดับเทรดของเดือนคงไว้เพื่อแสดงผลเท่านั้น
    ไม่มีผลต่อค่าธรรมเนียมอีกต่อไป
    """
    transaction_date = pd.to_datetime(trade_date)
    transactions = _load_transactions()
    if "tx_type" in transactions.columns:
        transactions = transactions[transactions["tx_type"] != TX_DIVIDEND]
    same_month_count = int(
        (transactions["date"].dt.to_period("M") == transaction_date.to_period("M")).sum()
    )
    trade_number_in_month = same_month_count + 1
    trade_value_usd = float(shares) * float(price_usd)
    fee_thb = dime_fee_thb(trade_value_usd, float(fx_rate_thb))
    return trade_number_in_month, fee_thb


def add_transaction(
    date: str,
    ticker: str,
    shares: float,
    price_usd: float,
    fx_rate_thb: float,
    amount_thb: float,
    note: str = "",
) -> dict[str, object]:
    """บันทึกรายการซื้อใหม่ลง CSV; คืนรายการที่บันทึก (มี ``tx_id`` สำหรับอ้างอิง/ลบ)."""
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
        "tx_id": str(uuid.uuid4()),
        "date": pd.to_datetime(date).strftime("%Y-%m-%d"),
        "ticker": ticker.strip().upper(),
        "shares": float(shares),
        "price_usd": float(price_usd),
        "fx_rate_thb": float(fx_rate_thb),
        "amount_thb": float(amount_thb),
        "fee_thb": float(fee_thb),
        "note": note.strip(),
        "tx_type": TX_BUY,
    }
    pd.DataFrame([row], columns=CSV_COLUMNS).to_csv(
        TRANSACTIONS_FILE,
        mode="a",
        header=False,
        index=False,
    )
    return row


def add_dividend(
    date: str,
    ticker: str,
    amount_usd: float,
    fx_rate_thb: float,
    note: str = "",
) -> dict[str, object]:
    """บันทึกปันผลที่ได้รับจริง (Roadmap Phase 2 ข้อ 5).

    ``amount_usd`` = ยอด **สุทธิ** ที่เข้าบัญชีจริง (โบรกหักภาษี ณ ที่จ่าย 15% แล้ว)
    — บันทึกตามที่รับจริง ไม่คำนวณกลับ; ยอด gross/ภาษีเป็นชั้นแสดงผล (portfolio/costs.py)
    แถวปันผล: shares=0, price_usd=0 → ไม่กระทบ cost basis และไม่นับเป็นเทรด
    """
    if not ticker or amount_usd <= 0 or fx_rate_thb <= 0:
        raise ValueError("ticker, amount_usd และ fx_rate_thb ต้องมีค่ามากกว่า 0")

    _ensure_storage()
    row = {
        "tx_id": str(uuid.uuid4()),
        "date": pd.to_datetime(date).strftime("%Y-%m-%d"),
        "ticker": ticker.strip().upper(),
        "shares": 0.0,
        "price_usd": 0.0,
        "fx_rate_thb": float(fx_rate_thb),
        "amount_thb": float(amount_usd) * float(fx_rate_thb),
        "fee_thb": 0.0,
        "note": note.strip(),
        "tx_type": TX_DIVIDEND,
    }
    pd.DataFrame([row], columns=CSV_COLUMNS).to_csv(
        TRANSACTIONS_FILE,
        mode="a",
        header=False,
        index=False,
    )
    return row


def get_dividends(ticker: str | None = None) -> pd.DataFrame:
    """แถวปันผลจาก ledger (ใหม่→เก่า); เพิ่มคอลัมน์ ``amount_usd`` (สุทธิ ณ วันรับ)."""
    transactions = _load_transactions()
    dividends = transactions[transactions["tx_type"] == TX_DIVIDEND].copy()
    if ticker:
        dividends = dividends[dividends["ticker"] == ticker.strip().upper()]
    if dividends.empty:
        return dividends
    dividends["amount_usd"] = dividends["amount_thb"] / dividends["fx_rate_thb"]
    return dividends.sort_values("date", ascending=False).reset_index(drop=True)


def get_dividend_summary() -> dict[str, object]:
    """สรุปปันผลสุทธิที่รับจริงทั้งหมด (THB/USD) รวมและรายปีปัจจุบัน."""
    dividends = get_dividends()
    if dividends.empty:
        return {
            "total_thb": 0.0,
            "total_usd": 0.0,
            "count": 0,
            "this_year_thb": 0.0,
            "by_ticker_thb": {},
        }
    this_year = dividends[dividends["date"].dt.year == pd.Timestamp.today().year]
    return {
        "total_thb": float(dividends["amount_thb"].sum()),
        "total_usd": float(dividends["amount_usd"].sum()),
        "count": int(len(dividends)),
        "this_year_thb": float(this_year["amount_thb"].sum()),
        "by_ticker_thb": dividends.groupby("ticker")["amount_thb"].sum().to_dict(),
    }


def delete_transaction(tx_id: str) -> bool:
    """ลบธุรกรรมตาม ``tx_id``; คืน True ถ้าลบสำเร็จ."""
    target = str(tx_id).strip()
    if not target:
        return False

    _ensure_storage()
    df = pd.read_csv(TRANSACTIONS_FILE)
    if df.empty or "tx_id" not in df.columns:
        return False

    keep = df["tx_id"].astype(str).str.strip() != target
    if bool(keep.all()):
        return False

    df[keep].to_csv(TRANSACTIONS_FILE, index=False)
    return True


def get_portfolio_summary() -> pd.DataFrame:
    """สรุปพอร์ตปัจจุบันรายสินทรัพย์ พร้อม P&L และ % Return.

    คิดจากรายการซื้อเท่านั้น — ปันผล (tx_type=dividend) ไม่เข้า cost basis/จำนวนหุ้น
    (P&L ที่ได้จึงเป็นกำไรจากราคาล้วน; รายรับปันผลดูจาก ``get_dividend_summary``)
    """
    transactions = _load_transactions()
    if "tx_type" in transactions.columns:
        transactions = transactions[transactions["tx_type"] != TX_DIVIDEND]
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
