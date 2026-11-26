"""Who signed in: an identity provider's token in, a verified identity out.

Parallx does not handle passwords or one-time codes. Firebase Authentication
does — Google sign-in, email and password with verification and resets, phone
numbers with SMS codes, their expiry, resend limits and abuse protection — in
the browser, with Google's servers. What reaches the API is a Firebase ID
token: a JWT signed by Google. `FirebaseVerifier` checks its signature against
Google's published keys, and its audience, issuer, expiry and subject, before
anything in it is believed. Then the API issues its own session (`viewer.py`).

    AUTH_PROVIDER=firebase       FIREBASE_PROJECT_ID=your-project
    AUTH_PROVIDER=dev            local development only: "dev:you@example.com"
                                 signs in as that address, no password. Refused
                                 when APP_ENV=production.

Identities are normalised here, so one person is one account: an email is
lower-cased, and Gmail's dots and any address's +tag dropped; a phone number
is E.164.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

#: Google's x509 certificates for Firebase ID tokens, rotated every few days.
FIREBASE_CERTS = ("https://www.googleapis.com/robot/v1/metadata/x509/"
                  "securetoken@system.gserviceaccount.com")
#: Firebase's sign_in_provider claim → ours.
PROVIDERS = {"google.com": "google", "password": "password", "phone": "phone"}
GMAIL = frozenset({"gmail.com", "googlemail.com"})


class IdentityError(Exception):
    """The token cannot be believed. `code` is for the client."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True, slots=True)
class Identity:
    uid: str
    #: google | password | phone
    provider: str
    email: Optional[str] = None
    email_verified: bool = False
    phone: Optional[str] = None
    name: Optional[str] = None


def normalize_email(email: str) -> str:
    """One person, one key: "First.Last+promo@GoogleMail.com" and
    "firstlast@gmail.com" are the same account."""
    local, _, domain = email.strip().lower().rpartition("@")
    if not local or not domain:
        raise ValueError(f"not an email address: {email!r}")
    local = local.split("+", 1)[0]
    if domain in GMAIL:
        local, domain = local.replace(".", ""), "gmail.com"
    return f"{local}@{domain}"


_PHONE = re.compile(r"^\+[1-9]\d{6,14}$")


def normalize_phone(phone: str, *, default_country: str = "+91") -> str:
    """E.164: "+91 98123 45678", "098123-45678" (with +91 assumed) → "+919812345678"."""
    raw = phone.strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+"):
        out = "+" + digits
    else:
        out = default_country + digits.lstrip("0")
    if not _PHONE.match(out):
        raise ValueError(f"not a phone number: {phone!r}")
    return out


class FirebaseVerifier:
    """Verifies Firebase ID tokens. `certs` (kid → PEM) stands in for Google's
    endpoint in tests; otherwise they are fetched and cached as long as the
    endpoint's Cache-Control allows."""

    def __init__(self, project_id: str, *, certs: Optional[dict[str, str]] = None,
                 clock: Callable[[], float] = time.time) -> None:
        if not project_id:
            raise IdentityError("not_configured", "FIREBASE_PROJECT_ID is not set")
        self.project_id = project_id
        self._certs, self._certs_until = certs, float("inf") if certs else 0.0
        self._clock = clock
        self._lock = threading.Lock()

    def _keys(self) -> dict[str, str]:
        with self._lock:
            if self._certs is None or self._clock() >= self._certs_until:
                with urllib.request.urlopen(FIREBASE_CERTS, timeout=10) as r:
                    self._certs = json.load(r)
                    m = re.search(r"max-age=(\d+)", r.headers.get("Cache-Control", ""))
                    self._certs_until = self._clock() + (int(m.group(1)) if m else 3600)
            return self._certs

    def verify(self, token: str) -> Identity:
        import jwt
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise IdentityError("invalid_token", "not a Firebase ID token") from exc
        if header.get("alg") != "RS256":
            raise IdentityError("invalid_token", "unexpected token algorithm")
        pem = self._keys().get(header.get("kid", ""))
        if pem is None:
            raise IdentityError("invalid_token", "token signed with an unknown key")
        from cryptography.x509 import load_pem_x509_certificate
        key = load_pem_x509_certificate(pem.encode()).public_key()
        try:
            claims = jwt.decode(
                token, key, algorithms=["RS256"], audience=self.project_id,
                issuer=f"https://securetoken.google.com/{self.project_id}",
                options={"require": ["exp", "iat", "sub", "aud", "iss"]}, leeway=30)
        except jwt.ExpiredSignatureError as exc:
            raise IdentityError("expired_token", "the sign-in has expired; sign in again") from exc
        except jwt.PyJWTError as exc:
            raise IdentityError("invalid_token", f"token rejected: {exc}") from exc
        if not claims.get("sub") or claims.get("auth_time", 0) > self._clock() + 30:
            raise IdentityError("invalid_token", "token has no subject, or a future sign-in")
        firebase = claims.get("firebase") or {}
        provider = PROVIDERS.get(firebase.get("sign_in_provider", ""))
        if provider is None:
            raise IdentityError("unsupported_provider",
                                f"sign-in with {firebase.get('sign_in_provider')!r} is not offered")
        return Identity(uid=claims["sub"], provider=provider, email=claims.get("email"),
                        email_verified=bool(claims.get("email_verified")),
                        phone=claims.get("phone_number"), name=claims.get("name"))


class DevVerifier:
    """Local development without a Firebase project: "dev:you@example.com" or
    "dev:+919812345678" is that person, verified. Never in production."""

    def verify(self, token: str) -> Identity:
        if not token.startswith("dev:"):
            raise IdentityError("invalid_token", "dev tokens look like dev:you@example.com")
        who = token[4:].strip()
        if who.startswith("+"):
            phone = normalize_phone(who)
            return Identity(uid=f"dev-{phone}", provider="phone", phone=phone)
        return Identity(uid=f"dev-{normalize_email(who)}", provider="password", email=who,
                        email_verified=True, name=who.split("@")[0])


_verifier = None


def get_verifier():
    """The configured verifier, built once."""
    global _verifier
    if _verifier is None:
        kind = os.environ.get("AUTH_PROVIDER", "firebase")
        if kind == "dev":
            if os.environ.get("APP_ENV", "development") == "production":
                raise IdentityError("not_configured",
                                    "AUTH_PROVIDER=dev is refused when APP_ENV=production")
            _verifier = DevVerifier()
        else:
            _verifier = FirebaseVerifier(os.environ.get("FIREBASE_PROJECT_ID", ""))
    return _verifier


def set_verifier(v) -> None:
    """Tests: a verifier with test keys, or None to build from the environment."""
    global _verifier
    _verifier = v
