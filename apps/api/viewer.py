"""Who is asking: a signed-in person (session cookie) or an API key.

A person signs in through Firebase (`identity.py`); `POST /v1/auth/session`
trades the verified ID token for a Parallx session: 32 random bytes in an
httpOnly cookie, stored as their SHA-256. A session ends 30 days after sign-in,
or 7 days after its last request, whichever comes first; each request pushes
the idle limit on (at most once every few minutes, to spare the database).
Signing out revokes it.

API keys (`Authorization: Bearer plx_…`) work as they always have. A key of
the internal organization, or a platform admin's, is staff: research without
credits or limits.

Cookies carry ambient authority, so a request that changes something and was
authenticated by the cookie must come from Parallx's own pages: its Origin
must be the app's (APP_URL, CORS_ORIGINS). The cookie is SameSite=Lax as well.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlsplit

from fastapi import Depends, Header, Request, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from parallx_db import AuthSession, Organization, User

from .deps import get_db
from .errors import ApiError

COOKIE = "parallx_session"
SESSION_PREFIX = "pxs_"
UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def session_days() -> int:
    return int(os.environ.get("SESSION_DAYS", "30"))


def idle_days() -> int:
    return int(os.environ.get("SESSION_IDLE_DAYS", "7"))


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def app_origins() -> set[str]:
    raw = [os.environ.get("APP_URL", "http://localhost:3000")] + \
        os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",")
    out = set()
    for u in raw:
        p = urlsplit(u.strip())
        if p.scheme and p.netloc:
            out.add(f"{p.scheme}://{p.netloc}")
    return out


@dataclass(frozen=True, slots=True)
class Viewer:
    user_id: uuid.UUID
    email: Optional[str]
    phone: Optional[str]
    name: Optional[str]
    role: str
    auth_provider: Optional[str]
    organization_id: uuid.UUID
    organization_slug: str
    organization_name: str
    is_internal: bool
    #: session | api_key
    via: str
    session_id: Optional[uuid.UUID] = None

    @property
    def is_staff(self) -> bool:
        """Research without credits: the internal organization's keys and
        platform admins. A person signing in is never staff by that alone."""
        return (self.role == "platform_admin" and self.is_internal) or \
            (self.via == "api_key" and self.is_internal)


def _viewer(user: User, org: Organization, via: str, session_id=None) -> Viewer:
    return Viewer(user.id, user.email, user.phone, user.name, user.role, user.auth_provider,
                  org.id, org.slug, org.name, org.is_internal, via, session_id)


async def create_session(db: AsyncSession, user_id: uuid.UUID, *,
                         user_agent: Optional[str] = None) -> tuple[str, AuthSession]:
    token = SESSION_PREFIX + secrets.token_urlsafe(32)
    now = _now()
    row = AuthSession(user_id=user_id, token_hash=hash_token(token), created_at=now,
                      expires_at=now + timedelta(days=session_days()),
                      idle_expires_at=now + timedelta(days=idle_days()),
                      last_seen_at=now, user_agent=(user_agent or "")[:255] or None)
    db.add(row)
    await db.flush()
    return token, row


def set_session_cookie(response: Response, token: str) -> None:
    secure = os.environ.get("APP_URL", "").startswith("https://")
    response.set_cookie(COOKIE, token, max_age=session_days() * 86400, httponly=True,
                        secure=secure, samesite="lax", path="/")


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")


async def _from_session(db: AsyncSession, token: str) -> Optional[Viewer]:
    row = (await db.execute(
        select(AuthSession, User, Organization)
        .join(User, User.id == AuthSession.user_id)
        .join(Organization, Organization.id == User.organization_id)
        .where(AuthSession.token_hash == hash_token(token)))).first()
    if row is None:
        return None
    s, user, org = row
    now = _now()
    if s.revoked_at is not None or now >= s.expires_at or now >= s.idle_expires_at:
        return None
    if now - s.last_seen_at > timedelta(minutes=5):
        await db.execute(update(AuthSession).where(AuthSession.id == s.id).values(
            last_seen_at=now,
            idle_expires_at=min(now + timedelta(days=idle_days()), s.expires_at)))
        await db.commit()
    return _viewer(user, org, "session", s.id)


async def _from_api_key(db: AsyncSession, authorization: str) -> Viewer:
    from .auth import get_principal
    p = await get_principal(authorization=authorization, db=db)
    user = await db.get(User, p.user_id)
    org = await db.get(Organization, p.organization_id)
    return _viewer(user, org, "api_key")


def _check_origin(request: Request) -> None:
    if request.method not in UNSAFE:
        return
    origin = request.headers.get("origin")
    if (origin and origin not in app_origins()) or (
            not origin and request.headers.get("sec-fetch-site") == "cross-site"):
        raise ApiError(403, "bad_origin", "This request did not come from Parallx.")


async def optional_viewer(request: Request, authorization: Optional[str] = Header(None),
                          db: AsyncSession = Depends(get_db)) -> Optional[Viewer]:
    """The caller, or None when nobody signed in. An invalid API key is an
    error, not anonymity."""
    if authorization and authorization.lower().startswith("bearer ") and \
            not authorization[7:].strip().startswith(SESSION_PREFIX):
        return await _from_api_key(db, authorization)
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    token = token or request.cookies.get(COOKIE)
    if not token:
        return None
    viewer = await _from_session(db, token)
    if viewer is not None and not authorization:
        _check_origin(request)
    return viewer


async def get_viewer(viewer: Optional[Viewer] = Depends(optional_viewer)) -> Viewer:
    if viewer is None:
        raise ApiError(401, "sign_in_required", "Sign in to research companies.")
    return viewer
