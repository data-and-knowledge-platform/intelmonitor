"""Search trends for the trends table, one snapshot per ingest T0: what is trending on Google in
Singapore and four regional geos, from the Trends RSS feed per geo (no key). The feed only shows
what is trending at fetch time, so it is skipped for a T0 more than 7 days back.

Each trending term is flagged with the tracked subjects it names. Requests are keyed by T0, so a
rerun of the same ingest is served from the cache.
"""

import json
import re
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from functools import lru_cache
from xml.etree import ElementTree

from intelmonitor.ingest.http import CachedHttp, FetchError, JSONValue
from intelmonitor.seed import load_subjects

TRENDS_RSS = "https://trends.google.com.sg/trending/rss"
TRENDING_GEOS = ["SG", "MY", "ID", "TH", "HK"]
TRENDING_DAYS = 7  # how far back a fetch still describes a T0
# Names too common to flag a trending search on their own once case is ignored.
_AMBIGUOUS = {
    "fast",
    "nets",
    "bot",
    "swift",
    "visa",
    "giro",
    "ibg",
    "oci",
    "gcp",
    "m365",
    "hk",
    "circle",
}


@lru_cache
def _trend_matchers() -> list[tuple[str, re.Pattern[str]]]:
    """Case-insensitive matchers for trending searches (typed in lower case): entity names and
    aliases, except countries (nearly every Singapore search names Singapore) and common words,
    and topic names, keywords and match terms."""
    seed = load_subjects()
    named = [(t.id, [t.name, *t.keywords, *t.match_terms]) for t in seed.topics]
    named += [(e.id, [e.name, *e.aliases]) for e in seed.entities if e.kind != "country"]
    matchers: list[tuple[str, re.Pattern[str]]] = []
    for subject_id, terms in named:
        kept = [re.escape(t) for t in terms if t.lower() not in _AMBIGUOUS]
        if kept:
            matchers.append((subject_id, re.compile(rf"(?<!\w)(?:{'|'.join(kept)})(?!\w)", re.I)))
    return matchers


def trend_subjects(terms: Iterable[str]) -> list[str]:
    """Ids of tracked subjects that any of the terms names."""
    text = "\n".join(terms)
    return [subject_id for subject_id, pattern in _trend_matchers() if pattern.search(text)]


@dataclass(frozen=True)
class TrendRow:
    source: str  # google_rss
    kind: str  # trending
    geo: str
    term: str
    observed_at: str
    value: float | None
    subjects: list[str] = field(default_factory=list[str])
    detail: dict[str, JSONValue] = field(default_factory=dict[str, JSONValue])


def store_trends(conn: sqlite3.Connection, snapshot: date, rows: list[TrendRow]) -> None:
    fetched_at = datetime.now(UTC).isoformat()
    conn.executemany(
        "INSERT OR REPLACE INTO trends (snapshot, source, kind, geo, term, observed_at, value, "
        "subjects, detail, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                snapshot.isoformat(),
                r.source,
                r.kind,
                r.geo,
                r.term,
                r.observed_at,
                r.value,
                json.dumps(r.subjects),
                json.dumps(r.detail),
                fetched_at,
            )
            for r in rows
        ],
    )
    conn.commit()


_HT = "{https://trends.google.com/trending/rss}"


def rss_trending(http: CachedHttp, geo: str, t0: date) -> list[TrendRow]:
    """The Trends RSS feed: what is trending in a geo at fetch time, every category."""
    response = http.get("trends_rss", TRENDS_RSS, params={"geo": geo}, vary=t0.isoformat())
    if not response.ok:
        raise FetchError(f"Trends RSS {geo}: HTTP {response.status}")
    rows: list[TrendRow] = []
    for item in ElementTree.fromstring(response.content).iter("item"):
        term = item.findtext("title") or ""
        traffic = re.sub(r"\D", "", item.findtext(f"{_HT}approx_traffic") or "")
        published = item.findtext("pubDate")
        news = [n.findtext(f"{_HT}news_item_title") or "" for n in item.iter(f"{_HT}news_item")]
        rows.append(
            TrendRow(
                "google_rss",
                "trending",
                geo,
                term,
                parsedate_to_datetime(published).astimezone(UTC).isoformat() if published else "",
                float(traffic) if traffic else None,
                trend_subjects([term]),
                {"news": list[JSONValue](news)},
            )
        )
    return rows


@dataclass
class TrendsResult:
    rows: Counter[str] = field(default_factory=Counter[str])  # by source and kind
    flagged: list[str] = field(default_factory=list[str])  # trending terms naming a subject
    failures: list[str] = field(default_factory=list[str])


def fetch_trends(
    conn: sqlite3.Connection,
    http: CachedHttp,
    t0: date,
    log: Callable[[str], None] = print,
) -> TrendsResult:
    """Fetch and store the trends snapshot for T0."""
    result = TrendsResult()
    rows: list[TrendRow] = []

    def attempt(label: str, fetch: Callable[[], list[TrendRow]]) -> None:
        try:
            rows.extend(fetch())
        except FetchError as e:
            result.failures.append(f"{label}: {e}")
            log(f"failed: {label}: {e}")

    # The feed shows what trends at fetch time, so it only stands for a recent T0.
    if datetime.now(UTC).date() - t0 > timedelta(days=TRENDING_DAYS):
        log(f"trends: T0 more than {TRENDING_DAYS} days back, skipped")
        return result
    for geo in TRENDING_GEOS:
        attempt(f"trends rss {geo}", lambda geo=geo: rss_trending(http, geo, t0))
    store_trends(conn, t0, rows)
    result.rows.update(f"{r.source} {r.kind}" for r in rows)
    result.flagged = [
        f"{r.source} {r.geo} '{r.term}': {', '.join(r.subjects)}" for r in rows if r.subjects
    ]
    log(f"trends: {dict(result.rows)}")
    for line in result.flagged:
        log(f"  trending {line}")
    return result
