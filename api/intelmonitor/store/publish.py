"""The store handoff through an S3-compatible bucket (docs/deploy.md).

The runner publishes each finished run as an immutable file `stores/intelmonitor-<run date>-<short
hash>.db`, then repoints `latest.json` at it, last, so a reader never sees a half-written store.
The site pulls the file `latest.json` names and opens it read-only (`PublishedStore`). Only the S3
API and the STORE_* settings are used, so any S3-compatible bucket works."""

import hashlib
import logging
import sqlite3
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import boto3
from botocore.exceptions import ClientError
from pydantic import BaseModel

from intelmonitor.config import get_settings
from intelmonitor.store.db import connect

if TYPE_CHECKING:  # the stubs are a dev dependency, absent from the image
    from mypy_boto3_s3 import S3Client

LATEST = "latest.json"
PREFIX = "stores/"

log = logging.getLogger(__name__)


class Latest(BaseModel):
    """What `latest.json` holds: the store file readers should use."""

    key: str
    run_id: str
    sha256: str
    published_at: datetime


class Bucket:
    """The bucket named by the STORE_* settings."""

    def __init__(self, client: "S3Client", name: str) -> None:
        self.client = client
        self.name = name

    @classmethod
    def from_settings(cls) -> "Bucket | None":
        """The configured bucket, or None when STORE_BUCKET is unset (local development)."""
        s = get_settings()
        if not s.store_bucket:
            return None
        # boto3-stubs types only the s3 overload installed; the others are unknown to pyright.
        client: S3Client = boto3.client(  # pyright: ignore[reportUnknownMemberType]
            "s3",
            endpoint_url=s.store_s3_endpoint or None,
            region_name=s.store_s3_region or None,
            aws_access_key_id=s.store_s3_access_key_id or None,
            aws_secret_access_key=s.store_s3_secret_access_key or None,
        )
        return cls(client, s.store_bucket)

    def latest(self) -> Latest | None:
        """What `latest.json` names, or None before the first publish."""
        try:
            body = self.client.get_object(Bucket=self.name, Key=LATEST)["Body"].read()
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
                return None
            raise
        return Latest.model_validate_json(body)

    def stored(self, key: str) -> Latest:
        """A published store file as `latest.json` would name it, from the metadata it was
        uploaded with. Raises LookupError when there is no such file."""
        try:
            head = self.client.head_object(Bucket=self.name, Key=key)
        except ClientError as error:
            raise LookupError(f"no store {key} in the bucket {self.name}") from error
        meta = head["Metadata"]
        return Latest(
            key=key, run_id=meta["run-id"], sha256=meta["sha256"], published_at=datetime.now(UTC)
        )

    def point(self, latest: Latest) -> None:
        """Repoint `latest.json`: the one write readers act on."""
        self.client.put_object(
            Bucket=self.name,
            Key=LATEST,
            Body=latest.model_dump_json(indent=1).encode(),
            ContentType="application/json",
            CacheControl="no-cache",
        )

    def download(self, latest: Latest, dest: Path) -> None:
        """Download the store `latest` names to `dest` through a temp file beside it, checking
        its sha256, so `dest` is either absent or complete. Raises ValueError on a mismatch."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.part")
        self.client.download_file(self.name, latest.key, str(tmp))
        if (digest := sha256(tmp)) != latest.sha256:
            tmp.unlink()
            raise ValueError(f"{latest.key}: sha256 {digest}, latest.json says {latest.sha256}")
        tmp.replace(dest)

    def prune(self, keep: int | None = None) -> list[str]:
        """Delete all but the `keep` newest store files (STORE_KEEP by default), never the one
        `latest.json` names. Returns the deleted keys."""
        keep = get_settings().store_keep if keep is None else keep
        pages = self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.name, Prefix=PREFIX
        )
        files = [
            (o.get("LastModified"), o.get("Key", ""))
            for page in pages
            for o in page.get("Contents", [])
        ]
        files.sort(reverse=True)
        current = (latest := self.latest()) and latest.key
        old = [key for _, key in files[keep:] if key != current]
        for key in old:
            self.client.delete_object(Bucket=self.name, Key=key)
        return old


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


class LatestMoved(RuntimeError):
    """latest.json changed while this store was computed: another run published first."""


def publish(bucket: Bucket, store: Path, *, replaces: str | None) -> Latest:
    """Upload a compact, self-contained copy of the store (VACUUM INTO, in rollback-journal mode
    so it opens read-only as one file) under a new key, repoint `latest.json` at it, then prune.
    `replaces` is the key latest.json named when the store was pulled (None before the first
    publish): if it names another now, two runs overlapped, and LatestMoved is raised before
    latest.json is touched; the uploaded file stays for a rollback. The copy is named by the
    store's latest complete run; raises LookupError when it has none."""
    with tempfile.TemporaryDirectory(dir=store.parent) as tmp:
        copy = Path(tmp) / "store.db"
        source = sqlite3.connect(f"{store.resolve().as_uri()}?mode=ro", uri=True)
        try:
            source.execute("VACUUM INTO ?", (str(copy),))
        finally:
            source.close()
        conn = sqlite3.connect(copy)
        try:
            conn.execute("PRAGMA journal_mode=DELETE")
            row = conn.execute("SELECT max(id) FROM runs WHERE status = 'complete'").fetchone()
        finally:
            conn.close()
        if row[0] is None:
            raise LookupError(f"the store at {store} has no complete run to publish")
        digest = sha256(copy)
        latest = Latest(
            key=f"{PREFIX}intelmonitor-{row[0]}-{digest[:8]}.db",
            run_id=row[0],
            sha256=digest,
            published_at=datetime.now(UTC),
        )
        bucket.client.upload_file(
            str(copy),
            bucket.name,
            latest.key,
            ExtraArgs={"Metadata": {"sha256": digest, "run-id": latest.run_id}},
        )
    if (found := (now := bucket.latest()) and now.key) != replaces:
        raise LatestMoved(f"latest.json names {found}, not {replaces}: another run published")
    bucket.point(latest)
    bucket.prune()
    return latest


class PublishedStore:
    """The site's store: the file `latest.json` names, pulled into `directory` and opened
    read-only and immutable, so no request can write and none needs a lock. `refresh` swaps in a
    newly published file; a request already holding a connection finishes on the old one. The
    pulled copy is brought to this code's schema before it is served, as a store published by
    older code lacks tables and columns the API reads; the bucket's file is never changed."""

    def __init__(self, bucket: Bucket, directory: Path) -> None:
        self.bucket = bucket
        self.directory = directory
        self.latest: Latest | None = None
        self._path: Path | None = None
        self._lock = threading.Lock()  # one refresh at a time

    def refresh(self) -> bool:
        """Pull and swap in the store `latest.json` names when it changed. Returns whether it
        swapped; raises when there is no published store yet or the download fails, leaving the
        current store in place."""
        with self._lock:
            latest = self.bucket.latest()
            if latest is None:
                raise LookupError(f"nothing published in the bucket {self.bucket.name} yet")
            if self.latest is not None and latest.key == self.latest.key:
                return False
            path = self.directory / Path(latest.key).name
            if not path.exists() or sha256(path) != latest.sha256:
                self.bucket.download(latest, path)
            _migrate(path)
            previous = self._path
            self._path, self.latest = path, latest
            # Keep the file being replaced, which requests may be about to open, and delete older.
            for old in self.directory.glob("intelmonitor-*.db"):
                if old not in (path, previous):
                    old.unlink()
            log.info("serving %s (run %s)", latest.key, latest.run_id)
            return True

    def open(self) -> sqlite3.Connection:
        if self._path is None:
            raise RuntimeError("no store pulled yet: call refresh() first")
        conn = sqlite3.connect(
            f"{self._path.resolve().as_uri()}?mode=ro&immutable=1",
            uri=True,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        return conn


def _migrate(path: Path) -> None:
    """Run the schema migrations on a pulled store, then leave it in rollback-journal mode with
    no side files, so it opens read-only and immutable as one file."""
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
    finally:
        conn.close()
