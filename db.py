import json
import logging
from typing import Dict, List, Optional

import asyncpg

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS feed_status (
    feed_id           TEXT PRIMARY KEY,
    last_pub_date     TEXT,
    last_entry_key    TEXT,
    last_message_id   TEXT,
    last_status       TEXT,
    render_version    TEXT,
    updated_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS feed_history (
    id              SERIAL PRIMARY KEY,
    guild_id        TEXT     NOT NULL,
    feed_id         TEXT NOT NULL,
    pub_date        TEXT,
    title           TEXT,
    content         TEXT,
    status          TEXT,
    entry_key       TEXT,
    closed_fields   JSONB    DEFAULT '[]',
    contains_soccer BOOLEAN  DEFAULT FALSE,
    detected_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_feed_history_lookup
    ON feed_history (feed_id, detected_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS uq_feed_history_entry
    ON feed_history (guild_id, feed_id, entry_key);
"""


class Database:
    def __init__(self, url: str):
        self.url = url
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        if not self.url:
            raise ValueError("Database URL is required")

        self.pool = await asyncpg.create_pool(self.url, min_size=1, max_size=5)
        async with self.pool.acquire() as conn:
            legacy_state_exists = await conn.fetchval(
                "SELECT to_regclass('public.feed_state') IS NOT NULL"
            )
            status_exists = await conn.fetchval(
                "SELECT to_regclass('public.feed_status') IS NOT NULL"
            )

            await conn.execute(SCHEMA)
            await conn.execute(
                "ALTER TABLE feed_status ADD COLUMN IF NOT EXISTS render_version TEXT"
            )

            if legacy_state_exists and not status_exists:
                await conn.execute(
                    """
                    INSERT INTO feed_status
                        (feed_id, last_pub_date, last_entry_key, last_message_id, last_status, render_version, updated_at)
                    SELECT DISTINCT ON (feed_id)
                        feed_id,
                        last_pub_date,
                        NULL,
                        last_message_id,
                        last_status,
                        NULL,
                        COALESCE(updated_at, NOW())
                    FROM feed_state
                    ORDER BY feed_id, updated_at DESC
                    ON CONFLICT (feed_id) DO UPDATE
                        SET last_pub_date = EXCLUDED.last_pub_date,
                            last_entry_key = EXCLUDED.last_entry_key,
                            last_message_id = EXCLUDED.last_message_id,
                            last_status = EXCLUDED.last_status,
                            render_version = EXCLUDED.render_version,
                            updated_at = EXCLUDED.updated_at
                    """
                )

        logger.info("Database connected and schema initialized")

    async def close(self):
        if self.pool:
            await self.pool.close()

    async def get_feed_status(self, feed_id: str) -> Optional[Dict[str, Optional[str]]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT last_pub_date, last_entry_key, last_message_id, last_status, render_version
                FROM feed_status
                WHERE feed_id = $1
                """,
                feed_id,
            )
            return dict(row) if row else None

    async def set_feed_status(
        self,
        feed_id: str,
        last_pub_date: Optional[str] = None,
        last_entry_key: Optional[str] = None,
        last_message_id: Optional[str] = None,
        last_status: Optional[str] = None,
        render_version: Optional[str] = None,
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO feed_status
                    (feed_id, last_pub_date, last_entry_key, last_message_id, last_status, render_version, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, NOW())
                ON CONFLICT (feed_id) DO UPDATE
                    SET last_pub_date = EXCLUDED.last_pub_date,
                        last_entry_key = EXCLUDED.last_entry_key,
                        last_message_id = EXCLUDED.last_message_id,
                        last_status = EXCLUDED.last_status,
                        render_version = EXCLUDED.render_version,
                        updated_at = NOW()
                """,
                feed_id,
                last_pub_date,
                last_entry_key,
                last_message_id,
                last_status,
                render_version,
            )

    async def add_history_entry(self, guild_id: int, feed_id: str, entry: Dict) -> bool:
        async with self.pool.acquire() as conn:
            guild_id_text = str(guild_id)
            entry_key = entry.get("entry_key")

            if entry_key:
                existing = await conn.fetchval(
                    """
                    SELECT 1
                    FROM feed_history
                    WHERE guild_id = $1 AND feed_id = $2 AND entry_key = $3
                    LIMIT 1
                    """,
                    guild_id_text,
                    feed_id,
                    entry_key,
                )
                if existing:
                    return False

            result = await conn.execute(
                """
                INSERT INTO feed_history
                    (guild_id, feed_id, pub_date, title, content, entry_key,
                     status, closed_fields, contains_soccer)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9)
                """,
                guild_id_text,
                feed_id,
                entry.get("pub_date"),
                entry.get("title", ""),
                entry.get("content", ""),
                entry_key,
                entry.get("status"),
                json.dumps(entry.get("closed_fields", [])),
                entry.get("contains_soccer", False),
            )
            return result == "INSERT 0 1"

    async def get_history(self, feed_id: str, limit: int = 10) -> List[Dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT pub_date, title, content, status,
                       closed_fields, contains_soccer, detected_at
                FROM feed_history
                WHERE feed_id = $1
                ORDER BY detected_at DESC
                LIMIT $2
                """,
                feed_id,
                limit,
            )
            result = []
            for row in rows:
                item = dict(row)
                item["closed_fields"] = list(item["closed_fields"]) if item["closed_fields"] else []
                item["detected_at"] = item["detected_at"].isoformat()
                result.append(item)
            return result

    async def get_last_status(self, feed_id: str) -> Optional[str]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT last_status FROM feed_status WHERE feed_id = $1",
                feed_id,
            )
            if row and row["last_status"]:
                return row["last_status"]

            row = await conn.fetchrow(
                """
                SELECT status FROM feed_history
                WHERE feed_id = $1
                ORDER BY detected_at DESC
                LIMIT 1
                """,
                feed_id,
            )
            return row["status"] if row else None
