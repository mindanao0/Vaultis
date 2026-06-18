"""In-process TTL dict cache for ETF snapshots."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

ETF_INFO_TTL = 6 * 60 * 60  # 6 hours
TECHNICAL_TTL = 15 * 60  # 15 minutes


def etf_info_cache_key(symbol: str) -> str:
    return f"etf_info:{symbol.strip().upper()}"


def etf_technical_cache_key(symbol: str) -> str:
    return f"etf_technical:{symbol.strip().upper()}"


class CacheService:
    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any]] = {}
        self._expiry: dict[str, datetime] = {}

    async def get(self, key: str) -> Optional[dict[str, Any]]:
        if key in self._cache:
            if datetime.now() < self._expiry[key]:
                return self._cache[key]
            del self._cache[key]
            del self._expiry[key]
        return None

    async def set(self, key: str, value: dict, ttl: int) -> None:
        self._cache[key] = value
        self._expiry[key] = datetime.now() + timedelta(seconds=ttl)
