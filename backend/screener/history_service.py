# CREATE TABLE IF NOT EXISTS screener_history (
#   id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
#   symbol VARCHAR(20),
#   preset_name VARCHAR(50),
#   matched_rules TEXT,
#   price FLOAT,
#   signal_strength FLOAT,
#   created_at TIMESTAMP DEFAULT now()
# );

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from backend.screener.models import ScreenerResult

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()


class ScreenerHistoryService:
    def __init__(self) -> None:
        self.engine: Engine | None = None
        if DATABASE_URL:
            # Expects a psycopg2-backed URL like postgresql+psycopg2://...
            self.engine = create_engine(DATABASE_URL, pool_pre_ping=True)

    async def save_results(self, results: list[ScreenerResult], preset_name: str):
        if self.engine is None or not results:
            return

        insert_sql = text(
            """
            INSERT INTO screener_history
                (symbol, preset_name, matched_rules, price, signal_strength, created_at)
            VALUES
                (:symbol, :preset_name, :matched_rules, :price, :signal_strength, NOW())
            """
        )

        try:
            with self.engine.begin() as conn:
                for result in results:
                    conn.execute(
                        insert_sql,
                        {
                            "symbol": result.symbol,
                            "preset_name": preset_name,
                            "matched_rules": json.dumps(result.matched_rules),
                            "price": result.price,
                            "signal_strength": result.signal_strength,
                        },
                    )
        except Exception as e:
            print(f"[screener_history] save_results error: {e}")

    async def get_history(self, symbol: str = None, limit: int = 50) -> list[dict]:
        if self.engine is None:
            return []

        try:
            query = """
                SELECT id, symbol, preset_name, matched_rules, price, signal_strength, created_at
                FROM screener_history
            """
            params: dict = {"limit": limit}
            if symbol:
                query += " WHERE symbol = :symbol"
                params["symbol"] = symbol
            query += " ORDER BY created_at DESC LIMIT :limit"

            with self.engine.begin() as conn:
                rows = conn.execute(text(query), params).mappings().all()

            return [dict(row) for row in rows]
        except Exception as e:
            print(f"[screener_history] get_history error: {e}")
            return []
