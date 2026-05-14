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
    last_post_date    TEXT,
    last_message_id   TEXT,
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
    entry_key       TEXT,
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
            # Ensure schema migrations are applied for existing databases
            await conn.execute(
                """
                ALTER TABLE feed_state
                ADD COLUMN IF NOT EXISTS last_status TEXT
                """
            )
            await conn.execute(
                """
                ALTER TABLE feed_state
                ADD COLUMN IF NOT EXISTS last_post_date TEXT
                """
            )
            await conn.execute(
                """
                ALTER TABLE feed_state
                ADD COLUMN IF NOT EXISTS last_message_id TEXT
                """
            )
            await conn.execute(
                """
                ALTER TABLE feed_history
                ADD COLUMN IF NOT EXISTS entry_key TEXT
                """
            )
            # Deduplicate existing rows before creating/upholding unique index semantics.
            await conn.execute(
                """
                DELETE FROM feed_history fh
                USING (
                    SELECT id
                    FROM (
                        SELECT id,
                               row_number() OVER (
                                   PARTITION BY guild_id, feed_id, entry_key
                                   ORDER BY id
                               ) AS rn
                        FROM feed_history
                        WHERE entry_key IS NOT NULL
                    ) t
                    WHERE t.rn > 1
                ) d
                WHERE fh.id = d.id
                """
            )
            # Rebuild the unique index in a form ON CONFLICT can infer.
            # A plain unique index still allows multiple NULL entry_key values in Postgres.
            await conn.execute("DROP INDEX IF EXISTS uq_feed_history_entry")
            await conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_feed_history_entry
                    ON feed_history (guild_id, feed_id, entry_key)
                """
            )
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

    async def add_history_entry(self, guild_id: str, feed_id: str, entry: Dict) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                INSERT INTO feed_history
                    (guild_id, feed_id, pub_date, title, content, entry_key,
                     status, closed_fields, contains_soccer)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9)
                ON CONFLICT (guild_id, feed_id, entry_key) DO NOTHING
                """,
                guild_id,
                feed_id,
                entry.get("pub_date"),
                entry.get("title", ""),
                entry.get("content", ""),
                entry.get("entry_key"),
                entry.get("status"),
                json.dumps(entry.get("closed_fields", [])),
                entry.get("contains_soccer", False),
            )
            return result == "INSERT 0 1"

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

    async def get_post_state(self, guild_id: str, feed_id: str) -> Dict[str, Optional[str]]:
        """Get daily posting state for a feed."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT last_post_date, last_message_id, last_status
                FROM feed_state
                WHERE guild_id = $1 AND feed_id = $2
                """,
                guild_id,
                feed_id,
            )
            if not row:
                return {"last_post_date": None, "last_message_id": None, "last_status": None}
            return {
                "last_post_date": row["last_post_date"],
                "last_message_id": row["last_message_id"],
                "last_status": row["last_status"],
            }

    async def set_post_state(
        self,
        guild_id: str,
        feed_id: str,
        post_date: str,
        message_id: str,
        status: str,
    ):
        """Persist message tracking for daily post/edit behavior."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO feed_state
                    (guild_id, feed_id, last_post_date, last_message_id, last_status, updated_at)
                VALUES ($1, $2, $3, $4, $5, NOW())
                ON CONFLICT (guild_id, feed_id) DO UPDATE
                    SET last_post_date = $3,
                        last_message_id = $4,
                        last_status = $5,
                        updated_at = NOW()
                """,
                guild_id,
                feed_id,
                post_date,
                message_id,
                status,
            )


