"""Test callers for an API that now requires one.

Research endpoints need a signed-in person or an API key. The suites that
exercise the research itself call as staff — an API key of the internal
organization, which researches without credits — so they test what they test,
and the accounts suites test the credits.
"""
from __future__ import annotations


def staff_headers(dsn: str) -> dict[str, str]:
    """A fresh staff API key (and the plans, for databases made by
    `create_all`), as request headers."""
    from parallx_db import INTERNAL_ORG_SLUG, ApiKey, Organization, User, session_scope
    from parallx_db.accounts import ensure_plans
    from parallx_db.keys import display_prefix, hash_key, new_key
    key = new_key()
    with session_scope(dsn) as db:
        ensure_plans(db)
        org = db.query(Organization).filter_by(slug=INTERNAL_ORG_SLUG).one_or_none()
        if org is None:
            org = Organization(slug=INTERNAL_ORG_SLUG, name="Parallx (internal)", is_internal=True)
            db.add(org)
            db.flush()
        user = db.query(User).filter_by(email="staff@parallx.test").one_or_none()
        if user is None:
            user = User(organization_id=org.id, email="staff@parallx.test", role="platform_admin")
            db.add(user)
            db.flush()
        db.add(ApiKey(user_id=user.id, key_hash=hash_key(key), prefix=display_prefix(key),
                      name="tests"))
    return {"Authorization": f"Bearer {key}"}
