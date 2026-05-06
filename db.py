import json
import logging
from typing import Optional, List, Dict

import asyncpg

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_configs (
    guild_id  TEXT PRIMARY KEY,
    config    JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS feed_state (
    guild_id     TEXT NOT NULL,
    feed_id      TEXT NOT NULL,
    last_pub_date TEXT,
    updated_at   TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (guild_id, feed_id)
);

CREATE TABLE IF NOT EXISTS feed_history (
    id              SERIAL PRIMARY KEY,
    guild_id        TEXT NOT NULL,
    feed_id         TEXT NOT NULL,
    pub_date        TEXT,
    title           TEXT,
    content         TEXT,
    status          TEXT,
    closed_fields   JSONB    DEFAULT '[]',
    contains_soccer BOOLEAN  DEFAULT FALSE,
    detected_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_feed_history_lookup
    ON feed_history (guild_id, feed_id, detected_at DESC);
"""


class Database:
    def __init__(self, url: str):
        self.url = url
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(self.url, min_size=1, max_size=5)
        async with self.pool.acquire() as conn:
            await conn.execute(SCHEMA)
        logger.info("Database connected and schema initialized")

    async def close(self):
        if self.pool:
            await self.pool.close()

    # ── Guild config ──────────────────────────────────────────────────────────

    async def load_all_guild_configs(self) -> Dict[str, Dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT guild_id, config FROM guild_configs")
            return {row["guild_id"]: dict(row["config"]) for row in rows}

    async def save_guild_config(self, guild_id: str, config: Dict):
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO guild_configs (guild_id, config, updated_at)
                VALUES ($1, $2::jsonb, NOW())
                ON CONFLICT (guild_id) DO UPDATE
                    SET config = $2::jsonb, updated_at = NOW()
                """,
                guild_id,
                json.dumps(config),
            )

    # ── Feed state (pub-date tracking) ────────────────────────────────────────

    async def get_last_pub_date(self, guild_id: str, feed_id: str) -> Optional[str]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT last_pub_date FROM feed_state WHERE guild_id=$1 AND feed_id=$2",
                guild_id,
                feed_id,
            )
            return row["last_pub_date"] if row else None


    async def load_all_feed_states(self) -> Dict[str, str]:
        """Return {'{guild_id}:{feed_id}': last_pub_date} for all feeds."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT guild_id, feed_id, last_pub_date FROM feed_state"
            )
            return {
                f"{row['guild_id']}:{row['feed_id']}": row["last_pub_date"]
                for row in rows
            }

    # ── Feed history ──────────────────────────────────────────────────────────

    async def add_history_entry(self, guild_id: str, feed_id: str, entry: Dict):
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO feed_history
                    (guild_id, feed_id, pub_date, title, content,
                     status, closed_fields, contains_soccer)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
                """,
                guild_id,
                feed_id,
                entry.get("pub_date"),
                entry.get("title", ""),
                entry.get("content", ""),
                entry.get("status"),
                json.dumps(entry.get("closed_fields", [])),
                entry.get("contains_soccer", False),
            )

    async def get_history(
        self, guild_id: str, feed_id: str, limit: int = 10
    ) -> List[Dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT pub_date, title, content, status,
                       closed_fields, contains_soccer, detected_at
                FROM feed_history
                WHERE guild_id = $1 AND feed_id = $2
                ORDER BY detected_at DESC
                LIMIT $3
                """,
                guild_id,
                feed_id,
                limit,
            )
            result = []
            for row in rows:
                d = dict(row)
                d["closed_fields"] = list(d["closed_fields"]) if d["closed_fields"] else []
                d["detected_at"] = d["detected_at"].isoformat()
                result.append(d)
            return result

    async def get_last_status(self, guild_id: str, feed_id: str) -> Optional[str]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT status FROM feed_history
                WHERE guild_id = $1 AND feed_id = $2
                ORDER BY detected_at DESC
                LIMIT 1
                """,
                guild_id,
                feed_id,
            )
            return row["status"] if row else None
