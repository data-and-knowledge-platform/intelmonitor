"""Shapes passed between source adapters and the ingest run."""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from pydantic import BaseModel

from intelmonitor.store.db import get_meta, set_meta
from intelmonitor.store.documents import Collection

# A seed ingest builds the history for the first runs, or for newly added subjects; the weekly
# ingest covers exactly one week, Monday to Sunday (UTC), with T0 the Sunday.
SEED_DAYS = 60
WEEK_DAYS = 7
MIN_TEXT_CHARS = 300


@dataclass(frozen=True)
class Window:
    """The `days` days ending at T0, both ends included."""

    t0: date
    days: int = WEEK_DAYS

    @property
    def start(self) -> date:
        return self.t0 - timedelta(days=self.days - 1)

    def contains(self, published: datetime) -> bool:
        return self.start <= published.date() <= self.t0


def corpus_start(conn: sqlite3.Connection) -> date | None:
    """Meta `corpus_start`: the first day every run reads documents from, and the first week
    `im weekly` ingests. A first seed ingest records its window start; otherwise it is set by hand
    (the October rebuild sets 2026-10-01). Documents stored from before it stay outside every
    run. None when it is not set; T0 moves every week, so it is no fallback."""
    stored = get_meta(conn, "corpus_start")
    return date.fromisoformat(stored) if stored else None


def week_window(sunday: date, start: date | None) -> Window:
    """The ingest window of the week ending on `sunday`: Monday to Sunday, or from the corpus
    `start` when it falls inside the week, so the first week searches nothing the runs never
    read."""
    if sunday.weekday() != 6:
        raise ValueError(f"{sunday} is not a Sunday")
    days = WEEK_DAYS if start is None else min(WEEK_DAYS, (sunday - start).days + 1)
    if days < 1:
        raise ValueError(f"the week ending {sunday} is before the corpus start {start}")
    return Window(sunday, days)


def record_corpus_start(conn: sqlite3.Connection, window: Window) -> date | None:
    """Record a seed window's start as the corpus start when none is set. A start already set is
    kept, even when the seed reaches further back, since every run reads from it; it is returned
    so the caller can say so. Returns None when this window's start was recorded."""
    start = corpus_start(conn)
    if start is None:
        set_meta(conn, "corpus_start", window.start.isoformat())
    return start


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
