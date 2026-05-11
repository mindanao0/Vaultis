"""Pydantic models for the Net Worth Tracker."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AssetType = Literal["cash", "etf", "fund", "bond", "อื่นๆ"]


class Asset(BaseModel):
    name: str
    type: AssetType
    value_thb: float = Field(gt=0)


class Liability(BaseModel):
    name: str
    value_thb: float = Field(gt=0)


class SnapshotRequest(BaseModel):
    assets: list[Asset]
    liabilities: list[Liability] = []
    snapshot_date: str | None = None  # YYYY-MM-DD; defaults to today if omitted


class NetWorthResponse(BaseModel):
    snapshot_date: str
    assets: list[Asset]
    liabilities: list[Liability]
    total_assets_thb: float
    total_liabilities_thb: float
    net_worth_thb: float
    etf_live: bool = False  # True when ETF values are from live prices, not snapshot
