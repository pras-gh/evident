"""Shared dependencies."""
from __future__ import annotations

import os
from collections.abc import AsyncIterator

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evident_db import Company, async_session_factory

_factory = None


def factory():
    global _factory
    if _factory is None:
        _factory = async_session_factory(os.environ.get("DATABASE_URL"))
    return _factory


async def get_db() -> AsyncIterator[AsyncSession]:
    async with factory()() as session:
        yield session


async def get_company(ticker: str, db: AsyncSession = Depends(get_db)) -> Company:
    """Resolve a ticker or 404 naming it.

    A missing company returns 404 rather than an empty 200 — an empty result
    reads as "we looked and there is nothing", which is a different and more
    misleading claim than "we have never heard of this company".
    """
    company = (await db.execute(
        select(Company).where(Company.ticker == ticker.upper())
    )).scalar_one_or_none()
    if company is None:
        raise HTTPException(404, f"No memory built for {ticker.upper()}")
    return company
