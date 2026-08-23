"""Connection pool and shared dependencies."""
from __future__ import annotations

from typing import Any

_pool: Any = None


async def open_pool(dsn: str) -> None:
    global _pool
    from psycopg_pool import AsyncConnectionPool
    _pool = AsyncConnectionPool(dsn, open=False, min_size=1, max_size=8)
    await _pool.open()


async def close_pool() -> None:
    if _pool is not None:
        await _pool.close()


async def pool_ready() -> bool:
    if _pool is None:
        return False
    async with _pool.connection() as conn, conn.cursor() as cur:
        await cur.execute("select 1")
        return (await cur.fetchone())[0] == 1


def get_pool() -> Any:
    if _pool is None:
        raise RuntimeError("pool not open — the app lifespan did not run")
    return _pool
