from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Float, Integer, String, Text

from ..database import Base


def _utcnow() -> datetime:
    """เวลาปัจจุบันแบบ timezone-aware (``datetime.utcnow`` ถูก deprecate แล้ว)."""
    return datetime.now(UTC)


class NetWorthSnapshot(Base):
    __tablename__ = "networth_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    snapshot_date = Column(String, nullable=False, index=True)  # YYYY-MM-DD
    total_assets_thb = Column(Float, nullable=False)
    total_liabilities_thb = Column(Float, nullable=False, default=0.0)
    net_worth_thb = Column(Float, nullable=False)
    assets_json = Column(Text, nullable=False)        # JSON [{name, type, value_thb}]
    liabilities_json = Column(Text, nullable=False, default="[]")  # JSON [{name, value_thb}]
    created_at = Column(DateTime, default=_utcnow, nullable=False)


# หมายเหตุ (AUDIT.md H2): ORM ``Transaction`` และ ``PriceAlert`` ถูกถอดออก —
# เป็น ledger/alert ชุดที่ 2 ที่ไม่ sync กับของจริง และพังมาตลอด
#   - POST /api/portfolio/add โยน TypeError ทุกครั้ง (ตาราง transactions มี 0 แถวเสมอ)
#   - alert ที่ตั้งผ่าน API ไม่มี job ไหนตรวจ เพราะ cron อ่านจากไฟล์ JSON
# ตอนนี้ ledger เดียว = portfolio/data/transactions.csv (portfolio/tracker.py)
#        alert store เดียว = alerts/data/price_alerts.json (alerts/price_alert.py)


class Config(Base):
    __tablename__ = "config"

    key = Column(String, primary_key=True, index=True)
    value = Column(Text, nullable=False)


class InvestmentGoal(Base):
    __tablename__ = "investment_goals"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    target_amount_thb = Column(Float, nullable=False)
    current_amount_thb = Column(Float, nullable=False, default=0.0)
    monthly_contribution_thb = Column(Float, nullable=False)
    target_date = Column(String, nullable=False)  # YYYY-MM-DD
    risk_profile = Column(String, nullable=False, default="moderate")
    created_at = Column(DateTime, default=_utcnow, nullable=False)


class MonthlyReport(Base):
    __tablename__ = "monthly_reports"

    id = Column(Integer, primary_key=True, index=True)
    month = Column(String, nullable=False, unique=True, index=True)  # YYYY-MM
    content = Column(Text, nullable=False)
    sent_at = Column(DateTime, nullable=False)
