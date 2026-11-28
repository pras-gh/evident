"""The filing store, on disk and in a bucket: the same operations either way,
and neither hands out anything outside its root.

The bucket tests run against moto's in-process S3, the API Cloudflare R2
speaks; they skip without moto (requirements-dev.txt installs it).
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from unittest import mock

from parallx_db import store

try:
    import boto3
    from moto import mock_aws
except ImportError:                 # pragma: no cover - moto is a dev dependency
    mock_aws = None

ESCAPES = ["../x", "a/../../x", "/etc/passwd", "a//b", "a\\b", "", "a/./b"]


async def _body(response) -> bytes:
    return b"".join([chunk async for chunk in response.body_iterator])


class OnDisk(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"FILING_STORE": self.dir.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.dir.cleanup()

    def test_write_read_exists_open(self):
        rel = store.page_path("0001045810-24-000029", 3)
        self.assertFalse(store.is_remote())
        self.assertFalse(store.exists(rel))
        self.assertEqual(store.write(rel, b"page"), rel)
        self.assertTrue(store.exists(rel))
        self.assertEqual(store.read(rel), b"page")
        stored = store.open_stored(rel)
        self.assertEqual((stored.size, b"".join(stored.chunks)), (4, b"page"))
        stored.close()

    def test_missing_and_escapes(self):
        with self.assertRaises(FileNotFoundError):
            store.read("nothing/pages/1.webp")
        for bad in ("../x", "a/../../x", "/etc/passwd"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                store.read(bad)


@unittest.skipUnless(mock_aws, "needs moto: pip install -r requirements-dev.txt")
class InABucket(unittest.TestCase):
    """FILING_STORE=s3://filings/prod: objects under prod/, typed for serving."""

    def setUp(self):
        self.aws = mock_aws()
        self.aws.start()
        self.env = mock.patch.dict(os.environ, {
            "FILING_STORE": "s3://filings/prod", "S3_REGION": "us-east-1",
            "S3_ACCESS_KEY_ID": "test", "S3_SECRET_ACCESS_KEY": "test"})
        self.env.start()
        store._make_client.cache_clear()
        self.s3 = boto3.client("s3", region_name="us-east-1", aws_access_key_id="test",
                               aws_secret_access_key="test")
        self.s3.create_bucket(Bucket="filings")

    def tearDown(self):
        store._make_client.cache_clear()
        self.env.stop()
        self.aws.stop()

    def test_write_read_exists_under_the_prefix(self):
        rel = store.page_path("0001045810-24-000029", 3, thumbnail=True)
        self.assertTrue(store.is_remote())
        self.assertFalse(store.exists(rel))
        store.write(rel, b"webp bytes")
        self.assertTrue(store.exists(rel))
        self.assertEqual(store.read(rel), b"webp bytes")
        obj = self.s3.get_object(Bucket="filings", Key=f"prod/{rel}")
        self.assertEqual(obj["ContentType"], "image/webp")
        pdf = store.write(store.pdf_path("0001045810-24-000029"), b"%PDF-1.7")
        self.assertEqual(self.s3.head_object(Bucket="filings", Key=f"prod/{pdf}")["ContentType"],
                         "application/pdf")

    def test_a_missing_object_is_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            store.read("nothing/pages/1.webp")
        with self.assertRaises(FileNotFoundError):
            store.open_stored("nothing/filing.pdf")

    def test_keys_never_leave_the_prefix(self):
        for bad in ESCAPES:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                store.write(bad, b"x")
        self.assertEqual([o["Key"] for o in self.s3.list_objects_v2(Bucket="filings")
                          .get("Contents", [])], [])

    def test_there_is_no_directory(self):
        with self.assertRaises(RuntimeError):
            store.resolve("a/pages/1.webp")

    def test_check_finds_the_bucket_or_says_so(self):
        store.check()
        with mock.patch.dict(os.environ, {"FILING_STORE": "s3://no-such-bucket"}):
            with self.assertRaises(Exception):
                store.check()

    def test_the_api_streams_from_the_bucket(self):
        from fastapi import HTTPException
        from api.routers import documents

        rel = store.write(store.pdf_path("0001045810-24-000029"), b"%PDF-1.7 body")
        response = asyncio.run(documents._file(rel, "application/pdf",
                                               filename="0001045810-24-000029.pdf", inline=True))
        self.assertEqual(asyncio.run(_body(response)), b"%PDF-1.7 body")
        self.assertEqual(response.headers["content-length"], "13")
        self.assertEqual(response.headers["content-disposition"],
                         'inline; filename="0001045810-24-000029.pdf"')
        # behind an access check: browsers may keep it, shared caches may not
        self.assertTrue(response.headers["cache-control"].startswith("private,"))
        with self.assertRaises(HTTPException) as gone:
            asyncio.run(documents._file("0001045810-24-000029/pages/9.webp", "image/webp"))
        self.assertEqual(gone.exception.status_code, 404)
        with self.assertRaises(HTTPException) as outside:
            asyncio.run(documents._file("../../.env", "text/plain"))
        self.assertEqual(outside.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
