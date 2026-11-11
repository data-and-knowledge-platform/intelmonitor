"""Shapes passed between source adapters and the ingest run."""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from pydantic import BaseModel

from intelmonitor.store.db import get_meta, set_meta
from intelmonitor.store.documents import Collection

# A seed ingest builds the history for the first runs, or for newly added subjects; the weekly
# ingest after it covers the week to T0 and 2 days before it, for articles indexed late.
SEED_DAYS = 60
WEEK_DAYS = 9
MIN_TEXT_CHARS = 300


@dataclass(frozen=True)
class Window:
    """The `days` days ending at T0 (inclusive)."""

    t0: date
    days: int = WEEK_DAYS

    @property
    def start(self) -> date:
        return self.t0 - timedelta(days=self.days)

    def contains(self, published: datetime) -> bool:
        return self.start <= published.date() <= self.t0


def corpus_start(conn: sqlite3.Connection) -> date | None:
    """The earliest window start of any seed ingest: where the stored corpus begins. Stores seeded
    before it was recorded fall back to the stored T0's seed window."""
    if stored := get_meta(conn, "corpus_start"):
        return date.fromisoformat(stored)
    t0 = get_meta(conn, "t0")
    return Window(date.fromisoformat(t0), SEED_DAYS).start if t0 else None


def extend_corpus(conn: sqlite3.Connection, window: Window) -> None:
    """Record a seed window's start as the corpus start when it reaches further back."""
    start = corpus_start(conn)
    if start is None or window.start < start:
        set_meta(conn, "corpus_start", window.start.isoformat())


class Listing(BaseModel):
    """An index entry from an official source, before its page is fetched."""

    source: str
    collection: Collection
    doc_type: str
    url: str
    title: str
    published_at: datetime


class RawDocument(BaseModel):
    """A fetched and parsed document, before dedup and storage."""

    source: str
    collection: Collection
    doc_type: str
    url: str
    title: str
    published_at: datetime
    text: str
