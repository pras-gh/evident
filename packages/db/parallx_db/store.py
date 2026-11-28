"""The filing store: files that belong to a filing but not in a table.

    {accession}/source/{primary document}     the bytes as fetched
    {accession}/filing.pdf                    a PDF of the filing
    {accession}/pages/{page}.webp             a page as rendered
    {accession}/pages/{page}.thumb.webp       …and its thumbnail

The database stores paths relative to the store's root, so the root can move
(FILING_STORE) without rewriting rows. The root is a directory or a bucket:

    FILING_STORE=/srv/filings          a directory (default: data/filings)
    FILING_STORE=s3://bucket/prefix    an S3-compatible bucket; in production,
                                       Cloudflare R2

A container's disk goes with every deploy, so production keeps filings in a
bucket. S3_ENDPOINT_URL names the service (R2:
https://<account id>.r2.cloudflarestorage.com), S3_ACCESS_KEY_ID and
S3_SECRET_ACCESS_KEY the key, S3_REGION the region ("auto", R2's only one).

Every path is checked before it is used, and one that would leave the root is
refused: these paths reach an HTTP handler, and `../../.env` must not be
servable.
"""
from __future__ import annotations

import functools
import mimetypes
import os
import re
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_REPO = Path(__file__).resolve().parents[3]
_SAFE = re.compile(r"[^A-Za-z0-9._-]")
_CHUNK = 64 * 1024


def _setting() -> str:
    return os.environ.get("FILING_STORE") or str(_REPO / "data" / "filings")


def is_remote() -> bool:
    """True when the store is a bucket rather than a directory."""
    return _setting().startswith("s3://")


def root() -> Path:
    """The directory of a local store."""
    if is_remote():
        raise RuntimeError("the filing store is a bucket (FILING_STORE=s3://...), not a directory")
    return Path(_setting())


def _clean(part: str) -> str:
    part = _SAFE.sub("_", part).strip("._")
    if not part:
        raise ValueError("empty path part")
    return part


def source_path(accession: str, filename: str) -> str:
    return f"{_clean(accession)}/source/{_clean(filename)}"


def pdf_path(accession: str) -> str:
    return f"{_clean(accession)}/filing.pdf"


def page_path(accession: str, page: int, *, thumbnail: bool = False) -> str:
    return f"{_clean(accession)}/pages/{int(page)}{'.thumb' if thumbnail else ''}.webp"


def resolve(relative: str) -> Path:
    """The absolute path for a stored relative path in a local store, or
    ValueError if it would escape the store."""
    base = root().resolve()
    target = (base / relative).resolve()
    if target != base and base not in target.parents:
        raise ValueError(f"{relative!r} is outside the filing store")
    return target


@dataclass
class Stored:
    """An open stored file: its size, and its bytes in chunks."""
    size: int
    chunks: Iterator[bytes]
    close: Callable[[], None]


def write(relative: str, data: bytes) -> str:
    """Store bytes at a relative path, whole: a reader never sees half a page
    image (an S3 PUT replaces an object atomically; a file is renamed into
    place)."""
    if is_remote():
        bucket, key = _object(relative)
        _client().put_object(Bucket=bucket, Key=key, Body=data, ContentType=_type(relative))
        return relative
    target = resolve(relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as fh:
        fh.write(data)
        tmp = fh.name
    os.replace(tmp, target)
    return relative


def read(relative: str) -> bytes:
    """A stored file's bytes; FileNotFoundError if there is none."""
    if is_remote():
        stored = open_stored(relative)
        try:
            return b"".join(stored.chunks)
        finally:
            stored.close()
    return resolve(relative).read_bytes()


def exists(relative: str) -> bool:
    if is_remote():
        bucket, key = _object(relative)
        try:
            _client().head_object(Bucket=bucket, Key=key)
        except Exception as exc:
            if _missing(exc):
                return False
            raise
        return True
    return resolve(relative).is_file()


def open_stored(relative: str) -> Stored:
    """A stored file to stream; FileNotFoundError if there is none."""
    if is_remote():
        bucket, key = _object(relative)
        try:
            obj = _client().get_object(Bucket=bucket, Key=key)
        except Exception as exc:
            if _missing(exc):
                raise FileNotFoundError(relative) from exc
            raise
        body = obj["Body"]
        return Stored(size=int(obj["ContentLength"]), chunks=body.iter_chunks(_CHUNK),
                      close=body.close)
    path = resolve(relative)
    fh = path.open("rb")        # FileNotFoundError when it is not there
    size = os.fstat(fh.fileno()).st_size
    return Stored(size=size, chunks=iter(lambda: fh.read(_CHUNK), b""), close=fh.close)


def check() -> None:
    """Raise unless the store can be used: the bucket answers, or the
    directory exists or can be made."""
    if is_remote():
        bucket, _ = _location()
        _client().head_bucket(Bucket=bucket)
        return
    root().mkdir(parents=True, exist_ok=True)


# ----------------------------------------------------------------- buckets

def _location() -> tuple[str, str]:
    """(bucket, prefix) from FILING_STORE=s3://bucket/prefix."""
    rest = _setting()[len("s3://"):]
    bucket, _, prefix = rest.partition("/")
    if not bucket:
        raise ValueError("FILING_STORE=s3://... names no bucket")
    return bucket, prefix.strip("/")


def _object(relative: str) -> tuple[str, str]:
    """(bucket, key) for a relative path. Object keys are names, not paths, so
    nothing normalises them on the way: anything that is not a plain relative
    path is refused rather than interpreted."""
    parts = relative.split("/") if relative else []
    if (not parts or "\\" in relative or "\x00" in relative
            or any(p in ("", ".", "..") for p in parts)):
        raise ValueError(f"{relative!r} is outside the filing store")
    bucket, prefix = _location()
    return bucket, f"{prefix}/{relative}" if prefix else relative


def _type(relative: str) -> str:
    if relative.endswith(".webp"):
        return "image/webp"
    return mimetypes.guess_type(relative)[0] or "application/octet-stream"


def _missing(exc: Exception) -> bool:
    response = getattr(exc, "response", None) or {}
    code = str(response.get("Error", {}).get("Code", ""))
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in ("404", "NoSuchKey", "NotFound") or status == 404


def _client():
    return _make_client(os.environ.get("S3_ENDPOINT_URL") or None,
                        os.environ.get("S3_REGION") or "auto",
                        os.environ.get("S3_ACCESS_KEY_ID") or None,
                        os.environ.get("S3_SECRET_ACCESS_KEY") or None)


@functools.lru_cache(maxsize=4)
def _make_client(endpoint: str | None, region: str, key_id: str | None, secret: str | None):
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:      # pragma: no cover - a deployment error
        raise RuntimeError("FILING_STORE=s3://... needs boto3: pip install boto3") from exc
    # Checksums only where an operation requires one: R2 accepts the S3 API,
    # not every optional header newer SDKs add by default.
    config = Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"},
                    request_checksum_calculation="when_required",
                    response_checksum_validation="when_required")
    return boto3.client("s3", endpoint_url=endpoint, region_name=region,
                        aws_access_key_id=key_id, aws_secret_access_key=secret, config=config)
