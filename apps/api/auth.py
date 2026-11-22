"""Who is calling: API keys in, a principal out.

    Authorization: Bearer plx_…

Only endpoints that spend money or show usage require a key; the read-only
data API stays public as before. Roles decide what usage a caller can see:

    member          their own requests
    org_admin       their organization's
    platform_admin  everyone's — internal organization only

`usage_scope` is the one place that turns a caller and their filters into
what they may see, so no endpoint can forget to apply it.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from parallx_db import ApiKey, Organization, User
from parallx_db.keys import hash_key

from .deps import get_db


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    role: str
    email: str
    organization_name: str
    organization_slug: str
    is_internal: bool

    @property
    def is_platform_admin(self) -> bool:
        return self.role == "platform_admin" and self.is_internal


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})


async def get_principal(authorization: str | None = Header(None),
                        db: AsyncSession = Depends(get_db)) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorized("an API key is required: Authorization: Bearer plx_…")
    key = authorization[7:].strip()
    row = (await db.execute(
        select(ApiKey, User, Organization)
        .join(User, User.id == ApiKey.user_id)
        .join(Organization, Organization.id == User.organization_id)
        .where(ApiKey.key_hash == hash_key(key), ApiKey.revoked_at.is_(None)))).first()
    if row is None:
        raise _unauthorized("unknown or revoked API key")
    api_key, user, org = row
    if user.role == "platform_admin" and not org.is_internal:
        # A platform role outside the internal organization is a misconfiguration;
        # refuse it rather than grant everyone's data.
        raise HTTPException(403, "platform_admin is only valid in the internal organization")
    now = datetime.now(timezone.utc)
    if api_key.last_used_at is None or now - api_key.last_used_at > timedelta(minutes=5):
        await db.execute(update(ApiKey).where(ApiKey.id == api_key.id).values(last_used_at=now))
        await db.commit()
    return Principal(user_id=user.id, organization_id=org.id, role=user.role, email=user.email,
                     organization_name=org.name, organization_slug=org.slug,
                     is_internal=org.is_internal)


async def require_platform_admin(p: Principal = Depends(get_principal)) -> Principal:
    if not p.is_platform_admin:
        raise HTTPException(403, "platform admins only")
    return p


def usage_scope(p: Principal, organization_id: uuid.UUID | None,
                user_id: uuid.UUID | None) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """(organization filter, user filter) this caller may use. None means all.

    Asking for another organization's or user's usage is a 403, not an empty
    result: an empty answer would read as "they spent nothing".
    """
    if p.is_platform_admin:
        return organization_id, user_id
    if organization_id is not None and organization_id != p.organization_id:
        raise HTTPException(403, "you can only see your own organization's usage")
    if p.role == "org_admin":
        return p.organization_id, user_id
    if user_id is not None and user_id != p.user_id:
        raise HTTPException(403, "you can only see your own usage")
    return p.organization_id, p.user_id
