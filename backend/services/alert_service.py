from __future__ import annotations

from sqlalchemy.orm import Session

from alerts.price_alert import get_current_prices

from ..models import PriceAlert
from ..schemas import PriceAlertCreate


def list_alerts(db: Session) -> list[PriceAlert]:
    return db.query(PriceAlert).order_by(PriceAlert.created_at.desc()).all()


def create_alert(db: Session, payload: PriceAlertCreate) -> PriceAlert:
    row = PriceAlert(
        ticker=payload.ticker.upper(),
        alert_type=payload.alert_type.lower(),
        target_price=payload.target_price,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def delete_alert(db: Session, alert_id: int) -> bool:
    row = db.query(PriceAlert).filter(PriceAlert.id == alert_id).first()
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


def check_alerts(db: Session) -> dict:
    pending = db.query(PriceAlert).filter(PriceAlert.is_triggered.is_(False)).all()
    tickers = sorted({row.ticker for row in pending})
    prices = get_current_prices(tickers)
    triggered: list[dict] = []
    for row in pending:
        price = prices.get(row.ticker)
        if price is None:
            continue
        is_match = (row.alert_type == "above" and price >= row.target_price) or (
            row.alert_type == "below" and price <= row.target_price
        )
        if is_match:
            row.is_triggered = True
            triggered.append(
                {
                    "id": row.id,
                    "ticker": row.ticker,
                    "alert_type": row.alert_type,
                    "target_price": row.target_price,
                    "current_price": price,
                }
            )
    db.commit()
    return {"checked": len(pending), "triggered": triggered}
