"""Redis-backed JSON cache; degrades to no-op when Redis is unavailable."""

from __future__ import annotations

import json
import os
from typing import Any, Optional

import redis.asyncio as redis
from dotenv import load_dotenv
from redis.exceptions import RedisError

load_dotenv()

ETF_INFO_TTL = 6 * 60 * 60  # 6 hours
TECHNICAL_TTL = 15 * 60  # 15 minutes


def etf_info_cache_key(symbol: str) -> str:
    return f"etf_info:{symbol.strip().upper()}"


def etf_technical_cache_key(symbol: str) -> str:
    return f"etf_technical:{symbol.strip().upper()}"


class CacheService:
    def __init__(self) -> None:
        self.redis: redis.Redis | None = None
        url = os.getenv("REDIS_URL", "redis://localhost:6379")
        try:
            self.redis = redis.from_url(url, decode_responses=True)
        except Exception:
            self.redis = None

    async def get(self, key: str) -> Optional[dict[str, Any]]:
        if self.redis is None:
            return None
        try:
            value = await self.redis.get(key)
            if value:
                return json.loads(value)
            return None
        except (RedisError, json.JSONDecodeError, TypeError, ValueError):
            return None

    async def set(self, key: str, value: dict, ttl: int) -> None:
        if self.redis is None:
            return
        try:
            await self.redis.setex(key, ttl, json.dumps(value, default=str))
        except (RedisError, TypeError, ValueError):
            pass
