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
    guild_id          TEXT NOT NULL,
    feed_id           TEXT NOT NULL,
    last_pub_date     TEXT,
    last_status       TEXT,
    updated_at        TIMESTAMPTZ DEFAULT NOW(),
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

    def _normalize_config(self, cfg):
        if isinstance(cfg, dict):
            return cfg

        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg)
            except json.JSONDecodeError:
                return {"__raw_config": cfg}
            return self._normalize_config(cfg)

        if isinstance(cfg, list):
            if all(isinstance(item, (list, tuple)) and len(item) == 2 for item in cfg):
                return dict(cfg)
            if all(isinstance(item, dict) for item in cfg):
                merged = {}
                for item in cfg:
                    merged.update(item)
                return merged
            return {"__raw_config": cfg}

        return {"__raw_config": cfg}

    async def load_all_guild_configs(self) -> Dict[str, Dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT guild_id, config FROM guild_configs")
            result = {}
            for row in rows:
                try:
                    cfg = self._normalize_config(row["config"])
                    result[row["guild_id"]] = cfg
                except Exception as e:
                    logger.error(
                        f"Failed to recover config for guild {row['guild_id']}: {e}"
                    )
                    result[row["guild_id"]] = {"__raw_config": row["config"]}
            return result

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

    async def set_last_pub_date(self, guild_id: str, feed_id: str, pub_date: str):
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO feed_state (guild_id, feed_id, last_pub_date, updated_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (guild_id, feed_id) DO UPDATE
                    SET last_pub_date = $3, updated_at = NOW()
                """,
                guild_id,
                feed_id,
                pub_date,
            )

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
        """Get the last known status for a feed from feed_state, falling back to history."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT last_status FROM feed_state
                WHERE guild_id = $1 AND feed_id = $2
                """,
                guild_id,
                feed_id,
            )
            if row and row["last_status"]:
                return row["last_status"]

            # Fallback for legacy rows that may not have last_status yet
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

    async def set_last_status(self, guild_id: str, feed_id: str, status: str):
        """Store the last known status for a feed."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO feed_state (guild_id, feed_id, last_status, updated_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (guild_id, feed_id) DO UPDATE
                    SET last_status = $3, updated_at = NOW()
                """,
                guild_id,
                feed_id,
                status,
            )


