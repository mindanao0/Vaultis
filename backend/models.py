from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text

from .database import Base


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(String, nullable=False, index=True)
    ticker = Column(String, nullable=False, index=True)
    shares = Column(Float, nullable=False)
    price_usd = Column(Float, nullable=False)
    amount_thb = Column(Float, nullable=False)
    fx_rate = Column(Float, nullable=False)
    fee = Column(Float, default=0.0)
    note = Column(Text, default="")


class PriceAlert(Base):
    __tablename__ = "price_alerts"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, nullable=False, index=True)
    alert_type = Column(String, nullable=False)
    target_price = Column(Float, nullable=False)
    is_triggered = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Config(Base):
    __tablename__ = "config"

    key = Column(String, primary_key=True, index=True)
    value = Column(Text, nullable=False)
