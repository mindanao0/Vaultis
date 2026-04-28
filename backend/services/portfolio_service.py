from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from alerts.price_alert import get_current_prices

from ..models import Transaction
from ..schemas import TransactionCreate


def add_transaction(db: Session, payload: TransactionCreate) -> Transaction:
    row = Transaction(**payload.model_dump(), ticker=payload.ticker.upper())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def delete_transaction(db: Session, tx_id: int) -> bool:
    row = db.query(Transaction).filter(Transaction.id == tx_id).first()
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


def get_history(db: Session) -> list[Transaction]:
    return db.query(Transaction).order_by(Transaction.date.desc(), Transaction.id.desc()).all()


def get_holdings(db: Session) -> list[dict]:
    rows = db.query(Transaction).all()
    if not rows:
        return []

    agg = defaultdict(lambda: {"shares": 0.0, "invested_usd": 0.0, "invested_thb": 0.0, "fee": 0.0})
    for tx in rows:
        invested_usd = tx.shares * tx.price_usd
        ticker = tx.ticker.upper()
        agg[ticker]["shares"] += tx.shares
        agg[ticker]["invested_usd"] += invested_usd
        agg[ticker]["invested_thb"] += tx.amount_thb
        agg[ticker]["fee"] += tx.fee

    tickers = sorted(agg.keys())
    prices = get_current_prices(tickers)
    result: list[dict] = []
    for ticker in tickers:
        item = agg[ticker]
        shares = item["shares"]
        current_price = float(prices.get(ticker, 0.0))
        current_value_usd = shares * current_price
        pnl_usd = current_value_usd - item["invested_usd"]
        return_pct = (pnl_usd / item["invested_usd"] * 100) if item["invested_usd"] else 0.0
        result.append(
            {
                "ticker": ticker,
                "shares": shares,
                "avg_cost_usd": item["invested_usd"] / shares if shares else 0.0,
                "invested_usd": item["invested_usd"],
                "invested_thb": item["invested_thb"],
                "current_price_usd": current_price,
                "current_value_usd": current_value_usd,
                "pnl_usd": pnl_usd,
                "return_pct": return_pct,
                "fee": item["fee"],
            }
        )
    return result


def get_portfolio_summary(db: Session) -> dict:
    holdings = get_holdings(db)
    return {
        "holdings_count": len(holdings),
        "invested_usd": sum(item["invested_usd"] for item in holdings),
        "invested_thb": sum(item["invested_thb"] for item in holdings),
        "current_value_usd": sum(item["current_value_usd"] for item in holdings),
        "pnl_usd": sum(item["pnl_usd"] for item in holdings),
        "total_fee": sum(item["fee"] for item in holdings),
    }
