"""Production: the settings that stop the API, the ones that only warn, the
pool settings a hosted database needs, and the deploy check's answer."""
from __future__ import annotations

import asyncio
import os
import unittest
from unittest import mock

from api import production

DSN = os.environ.get("TEST_DATABASE_URL")

GOOD = {
    "APP_ENV": "production", "DATABASE_URL": "postgresql://u@db.example:5432/postgres",
    "APP_URL": "https://app.ll.ax", "AUTH_PROVIDER": "firebase",
    "FIREBASE_PROJECT_ID": "parallx", "CORS_ORIGINS": "https://app.ll.ax",
    "FILING_STORE": "s3://parallx-filings/prod", "S3_ACCESS_KEY_ID": "id",
    "S3_SECRET_ACCESS_KEY": "secret", "SEC_USER_AGENT": "Parallx ops@example.com",
    "EMBEDDING_PROVIDER": "voyage", "VOYAGE_API_KEY": "key",
}


class Settings(unittest.TestCase):
    def test_development_needs_none_of_it(self):
        self.assertEqual(production.problems({}), [])
        self.assertEqual(production.warnings({}), [])

    def test_a_complete_production_config_starts(self):
        self.assertEqual(production.problems(GOOD), [])

    def test_each_unsafe_setting_is_named(self):
        cases = {
            "AUTH_PROVIDER": ("dev", "AUTH_PROVIDER must be firebase"),
            "APP_URL": ("http://app.ll.ax", "APP_URL must be"),
            "FILING_STORE": ("/srv/filings", "FILING_STORE must be a bucket"),
            "CORS_ORIGINS": ("http://localhost:3000", "https:// origins only"),
            "EMBEDDING_PROVIDER": ("hashing", "EMBEDDING_PROVIDER must be voyage, gemini, openai"),
        }
        for name, (value, says) in cases.items():
            with self.subTest(name=name):
                found = production.problems({**GOOD, name: value})
                self.assertEqual(len(found), 1, found)
                self.assertIn(says, found[0])

    def test_gemini_embeddings_need_the_gemini_key(self):
        env = {**GOOD, "EMBEDDING_PROVIDER": "gemini"}
        self.assertIn("GEMINI_API_KEY", " ".join(production.problems(env)))
        self.assertEqual(production.problems({**env, "GEMINI_API_KEY": "g"}), [])

    def test_missing_secrets_are_named(self):
        env = {k: v for k, v in GOOD.items()
               if k not in ("VOYAGE_API_KEY", "S3_SECRET_ACCESS_KEY", "FIREBASE_PROJECT_ID")}
        found = " ".join(production.problems(env))
        for name in ("VOYAGE_API_KEY", "S3_SECRET_ACCESS_KEY", "FIREBASE_PROJECT_ID"):
            self.assertIn(name, found)

    def test_billing_and_answers_warn_without_stopping(self):
        said = " ".join(production.warnings(GOOD))
        self.assertIn("billing is off", said)
        self.assertIn("STRIPE_PRICE_IIX_MONTHLY", said)
        self.assertIn("AI answers are off", said)
        complete = {**GOOD, "STRIPE_SECRET_KEY": "sk", "STRIPE_WEBHOOK_SECRET": "wh",
                    "STRIPE_PRICE_IIX_MONTHLY": "p", "STRIPE_PRICE_IIX_ANNUAL": "p",
                    "STRIPE_PRICE_IIX_ANALYST_MONTHLY": "p", "STRIPE_PRICE_IIX_ANALYST_ANNUAL": "p",
                    "GEMINI_API_KEY": "g"}
        self.assertEqual(production.warnings(complete), [])

    def test_enforce_stops_with_the_reasons(self):
        with mock.patch("sys.stderr"):
            with self.assertRaises(SystemExit):
                production.enforce({**GOOD, "AUTH_PROVIDER": "dev"})
            production.enforce(GOOD)        # warnings only: returns


class Pools(unittest.TestCase):
    KEYS = ("DB_POOL_SIZE", "DB_MAX_OVERFLOW", "DB_PREPARED_STATEMENTS")

    def test_sizes_and_prepared_statements_come_from_the_environment(self):
        from sqlalchemy.pool import NullPool
        from parallx_db.session import engine_options
        with mock.patch.dict(os.environ, {"DB_POOL_SIZE": "3", "DB_MAX_OVERFLOW": "2",
                                          "DB_PREPARED_STATEMENTS": "off"}):
            opts = engine_options()
            self.assertEqual((opts["pool_size"], opts["max_overflow"]), (3, 2))
            self.assertEqual(opts["connect_args"], {"prepare_threshold": None})
            self.assertNotIn("pool_size", engine_options(poolclass=NullPool))
            self.assertEqual(engine_options(pool_size=9)["pool_size"], 9)

    def test_unset_leaves_sqlalchemy_defaults(self):
        from parallx_db.session import engine_options
        with mock.patch.dict(os.environ):
            for key in self.KEYS:
                os.environ.pop(key, None)
            self.assertEqual(engine_options(), {"pool_pre_ping": True})

    @unittest.skipUnless(DSN, "set TEST_DATABASE_URL to run")
    def test_a_pooler_safe_engine_still_connects(self):
        """prepare_threshold=None is a real psycopg argument, sync and async."""
        from sqlalchemy import text
        from parallx_db.session import make_async_engine, make_engine
        with mock.patch.dict(os.environ, {"DB_PREPARED_STATEMENTS": "off", "DB_POOL_SIZE": "1",
                                          "DB_MAX_OVERFLOW": "0"}):
            engine = make_engine(DSN)
            with engine.connect() as c:
                for _ in range(8):      # past psycopg's default threshold of 5
                    self.assertEqual(c.execute(text("select 1")).scalar(), 1)
            engine.dispose()

            async def run():
                aengine = make_async_engine(DSN)
                async with aengine.connect() as c:
                    for _ in range(8):
                        self.assertEqual((await c.execute(text("select 1"))).scalar(), 1)
                await aengine.dispose()
            asyncio.run(run())


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class _Session:
    def __init__(self, version, fail):
        self.version, self.fail = version, fail

    async def __aenter__(self):
        if self.fail:
            raise OSError("connection refused")
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _statement):
        return _Result(self.version)


class Ready(unittest.TestCase):
    """The deploy check: 200 only when the database is at this code's
    migration and the filing store answers."""

    def _get(self, *, version=None, db_fails=False, store_fails=False):
        from fastapi.testclient import TestClient
        from api import main
        version = main.expected_migration() if version is None else version

        def check():
            if store_fails:
                raise RuntimeError("bucket unreachable")

        with mock.patch("api.deps.factory", lambda: (lambda: _Session(version, db_fails))), \
             mock.patch.object(main.store, "check", check):
            return TestClient(main.app).get("/ready")

    def test_ready(self):
        r = self._get()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ready")

    def test_not_ready_when_anything_is_off(self):
        for kw, failing in (({"version": "0001"}, "migration"), ({"db_fails": True}, "database"),
                            ({"store_fails": True}, "store")):
            with self.subTest(failing=failing):
                with self.assertLogs("parallx.ready", "WARNING"):
                    r = self._get(**kw)
                self.assertEqual(r.status_code, 503)
                self.assertFalse(r.json()[failing])



class Headers(unittest.TestCase):
    """Every API response carries the security headers, streamed ones too,
    without overriding a header the handler chose."""

    def _client(self, *, hsts):
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse, StreamingResponse
        from fastapi.testclient import TestClient
        from api.security import SecurityHeaders
        app = FastAPI()

        @app.get("/json")
        async def json_():
            return {"ok": True}

        @app.get("/framed")
        async def framed():
            return JSONResponse({}, headers={"X-Frame-Options": "DENY"})

        @app.get("/stream")
        async def stream():
            return StreamingResponse(iter([b"%PDF-", b"1.7"]), media_type="application/pdf")

        app.add_middleware(SecurityHeaders, hsts=hsts)
        return TestClient(app)

    def test_added_everywhere_kept_when_set_and_streams_untouched(self):
        client = self._client(hsts=False)
        r = client.get("/json")
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertEqual(r.headers["x-frame-options"], "SAMEORIGIN")
        self.assertEqual(r.headers["referrer-policy"], "strict-origin-when-cross-origin")
        self.assertNotIn("strict-transport-security", r.headers)
        self.assertEqual(client.get("/framed").headers["x-frame-options"], "DENY")
        s = client.get("/stream")
        self.assertEqual((s.content, s.headers["x-content-type-options"]), (b"%PDF-1.7", "nosniff"))

    def test_hsts_when_served_over_https(self):
        r = self._client(hsts=True).get("/json")
        self.assertTrue(r.headers["strict-transport-security"].startswith("max-age="))

    def test_the_api_has_them(self):
        from api import main
        from api.security import SecurityHeaders
        self.assertTrue(any(m.cls is SecurityHeaders for m in main.app.user_middleware))

if __name__ == "__main__":
    unittest.main()
