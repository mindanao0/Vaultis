"""Net Worth service: snapshot persistence and current-value calculation."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from utils import fx

from ..models.networth_models import Asset, Liability, NetWorthResponse, SnapshotRequest
from ..models.orm import NetWorthSnapshot
from ..services.portfolio_service import get_holdings


def _etf_assets_live(db: Session) -> list[Asset]:
    """ETF holdings ที่มีราคาจริง → Asset (THB).

    ใช้ FX สดจากแหล่งกลาง — เดิมใช้ ``default_fx_rate`` 33.5 คงที่จาก config
    ทำให้มูลค่า Net Worth ต่างจากหน้า Portfolio (AUDIT.md M5)
    ถือครองที่ดึงราคาไม่ได้จะถูกข้าม (ไม่นับเป็น 0 — AUDIT.md C1)
    """
    holdings = get_holdings()
    rate = fx.get_usdthb_rate()
    return [
        Asset(
            name=h["ticker"],
            type="etf",
            value_thb=round(float(h["current_value_usd"]) * rate, 2),
        )
        for h in holdings
        if h.get("price_ok") and h.get("current_value_usd")
    ]


def _latest_snapshot(db: Session) -> NetWorthSnapshot | None:
    return (
        db.query(NetWorthSnapshot)
        .order_by(NetWorthSnapshot.snapshot_date.desc(), NetWorthSnapshot.id.desc())
        .first()
    )


def get_current(db: Session) -> NetWorthResponse:
    """Live net worth: ETF values from yfinance + non-ETF assets from latest snapshot."""
    etf_assets = _etf_assets_live(db)
    etf_live = bool(etf_assets)

    # Pull non-ETF assets from the latest snapshot (cash, fund, bond, อื่นๆ)
    non_etf_assets: list[Asset] = []
    liabilities: list[Liability] = []
    latest = _latest_snapshot(db)
    if latest:
        saved = json.loads(latest.assets_json)
        non_etf_assets = [
            Asset(**a) for a in saved if a.get("type") != "etf"
        ]
        liabilities = [Liability(**l) for l in json.loads(latest.liabilities_json)]

    assets = non_etf_assets + etf_assets
    total_assets = sum(a.value_thb for a in assets)
    total_liabilities = sum(l.value_thb for l in liabilities)

    return NetWorthResponse(
        snapshot_date=date.today().isoformat(),
        assets=assets,
        liabilities=liabilities,
        total_assets_thb=round(total_assets, 2),
        total_liabilities_thb=round(total_liabilities, 2),
        net_worth_thb=round(total_assets - total_liabilities, 2),
        etf_live=etf_live,
    )


def get_history(db: Session, months: int = 12) -> list[NetWorthResponse]:
    """Return one snapshot per date for the past N months, newest first."""
    cutoff = (date.today() - timedelta(days=months * 30)).isoformat()
    rows = (
        db.query(NetWorthSnapshot)
        .filter(NetWorthSnapshot.snapshot_date >= cutoff)
        .order_by(NetWorthSnapshot.snapshot_date.desc(), NetWorthSnapshot.id.desc())
        .all()
    )
    seen: set[str] = set()
    result: list[NetWorthResponse] = []
    for row in rows:
        if row.snapshot_date in seen:
            continue
        seen.add(row.snapshot_date)
        assets = [Asset(**a) for a in json.loads(row.assets_json)]
        liabilities = [Liability(**l) for l in json.loads(row.liabilities_json)]
        result.append(
            NetWorthResponse(
                snapshot_date=row.snapshot_date,
                assets=assets,
                liabilities=liabilities,
                total_assets_thb=row.total_assets_thb,
                total_liabilities_thb=row.total_liabilities_thb,
                net_worth_thb=row.net_worth_thb,
                etf_live=False,
            )
        )
    return result


def save_snapshot(db: Session, payload: SnapshotRequest) -> NetWorthResponse:
    """Persist a manual snapshot and return it."""
    snapshot_date = payload.snapshot_date or date.today().isoformat()
    total_assets = sum(a.value_thb for a in payload.assets)
    total_liabilities = sum(l.value_thb for l in payload.liabilities)
    net_worth = total_assets - total_liabilities

    row = NetWorthSnapshot(
        snapshot_date=snapshot_date,
        total_assets_thb=round(total_assets, 2),
        total_liabilities_thb=round(total_liabilities, 2),
        net_worth_thb=round(net_worth, 2),
        assets_json=json.dumps([a.model_dump() for a in payload.assets], ensure_ascii=False),
        liabilities_json=json.dumps([l.model_dump() for l in payload.liabilities], ensure_ascii=False),
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return NetWorthResponse(
        snapshot_date=snapshot_date,
        assets=payload.assets,
        liabilities=payload.liabilities,
        total_assets_thb=row.total_assets_thb,
        total_liabilities_thb=row.total_liabilities_thb,
        net_worth_thb=row.net_worth_thb,
        etf_live=False,
    )
