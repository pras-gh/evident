"""Engine and session factories.

Both sync and async are provided on purpose: the workers are batch processes
where a sync session is simpler to reason about, while the API is async and
should not block its event loop on Postgres.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_URL = "postgresql+psycopg://localhost/evident"


def database_url(url: str | None = None, *, async_: bool = False) -> str:
    raw = url or os.environ.get("DATABASE_URL", DEFAULT_URL)
    # Accept the plain libpq form people paste from psql and normalise it.
    if raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+psycopg://", 1)
    return raw


def make_engine(url: str | None = None, **kw):
    return create_engine(database_url(url), pool_pre_ping=True, **kw)


def make_async_engine(url: str | None = None, **kw):
    return create_async_engine(database_url(url), pool_pre_ping=True, **kw)


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    """A transaction that commits on success and rolls back on anything else.

    Workers use this so a filing is written whole or not at all — a
    half-ingested document that answers queries with missing pages is worse
    than one that is absent.
    """
    engine = make_engine(url)
    factory = sessionmaker(engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        engine.dispose()


def async_session_factory(url: str | None = None) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(make_async_engine(url), expire_on_commit=False)
