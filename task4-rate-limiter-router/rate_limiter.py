import aiosqlite
import time

TOKENS_PER_MINUTE = 50_000
WINDOW_SECONDS = 60
DB_PATH = "rate_limiter.db"


async def init_db(db_path: str = DB_PATH):
    """Create the token_usage table if it doesn't exist."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                api_key TEXT NOT NULL,
                tokens INTEGER NOT NULL,
                timestamp REAL NOT NULL
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_key_ts ON token_usage (api_key, timestamp)
        """)
        await db.commit()


async def check_rate_limit(api_key: str, requested_tokens: int, db_path: str = DB_PATH) -> tuple[bool, int]:
    """Check if the request fits within the rate limit.

    Returns (allowed, remaining_tokens).
    """
    now = time.time()
    window_start = now - WINDOW_SECONDS

    async with aiosqlite.connect(db_path) as db:
        # Evict expired rows
        await db.execute("DELETE FROM token_usage WHERE timestamp < ?", (window_start,))

        # Sum tokens in the current window for this tenant
        cursor = await db.execute(
            "SELECT COALESCE(SUM(tokens), 0) FROM token_usage WHERE api_key = ? AND timestamp > ?",
            (api_key, window_start),
        )
        row = await cursor.fetchone()
        used_tokens = row[0]

        remaining = TOKENS_PER_MINUTE - used_tokens

        if used_tokens + requested_tokens > TOKENS_PER_MINUTE:
            await db.commit()
            return False, remaining

        # Record the usage
        await db.execute(
            "INSERT INTO token_usage (api_key, tokens, timestamp) VALUES (?, ?, ?)",
            (api_key, requested_tokens, now),
        )
        await db.commit()
        return True, remaining - requested_tokens
