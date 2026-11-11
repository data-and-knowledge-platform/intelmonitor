"""`im ingest`: fetch, cache, parse and dedup the corpus for the WEEK_DAYS ending at T0, or with
`--seed` for the SEED_DAYS ending at T0. A seed builds the history for the first runs;
`--seed --subjects` builds it for newly added subjects only, through their news and gap searches.
The weekly ingest then searches each week once; `im weekly` runs it for each week, Monday to
Sunday, with T0 the Sunday.

Order matters for dedup (the first copy of a document wins): official sources (feeds, APIs, Exa
searches over official sites, and over a feed's sites when the feed misses the window start),
then news searches, then gap searches for subjects that still have fewer than GAP_TARGET
candidates, then search trends. T0 is fixed on the first run and stored
in the meta table, so reruns send the same requests and are served entirely from the cache.

`im redate` applies the URL-date rule to documents stored before it existed, removing those it
dates before the window.
"""

import sqlite3
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from intelmonitor.config import get_settings
from intelmonitor.ingest.candidates import candidate_counts, normalize, subject_matchers
from intelmonitor.ingest.http import CachedHttp, FetchError
from intelmonitor.ingest.models import (
    SEED_DAYS,
    WEEK_DAYS,
    Listing,
    RawDocument,
    Window,
    corpus_start,
    record_corpus_start,
)
from intelmonitor.ingest.parse import (
    canonical_url,
    content_hash,
    document_id,
    domain_of,
    unreverse_lines,
)
from intelmonitor.ingest.sources.official import (
    API_FALLBACKS,
    OFFICIAL_SOURCES,
    SITE_SEARCHES,
    feed_backfill,
    fetch_listing,
    official_listings,
    site_search_documents,
)
from intelmonitor.ingest.sources.search import (
    EXA_RESULTS_PER_QUERY,
    GAP_NEWS_RESULTS,
    GAP_RESULTS_PER_QUERY,
    NEWS_DOMAINS,
    SearchQuery,
    exa_search,
    gap_query,
    news_queries,
    subject_label,
    url_dated,
)
from intelmonitor.ingest.sources.trends import fetch_trends
from intelmonitor.seed import load_subjects
from intelmonitor.store.db import connect, get_meta, set_meta
from intelmonitor.store.documents import SourceDocument, insert_document, list_documents

# Fewer candidates than this makes a subject thin: over a seed window, and over one week.
GAP_TARGET = 8
WEEK_GAP_TARGET = 2
_PAGE_WORKERS = 8
_SEARCH_WORKERS = 4


@dataclass
class IngestResult:
    t0: date
    added: Counter[str] = field(default_factory=Counter[str])
    duplicates: int = 0
    failures: list[str] = field(default_factory=list[str])
    gap_subjects: list[str] = field(default_factory=list[str])


def resolve_t0(conn: sqlite3.Connection, override: date | None) -> date:
    """T0 from --t0, else the stored T0, else today. The result is stored for later runs."""
    stored = get_meta(conn, "t0")
    t0 = override or (date.fromisoformat(stored) if stored else date.today())
    if stored != t0.isoformat():
        set_meta(conn, "t0", t0.isoformat())
    return t0


def run_ingest(
    t0_override: date | None = None,
    seeding: bool = False,
    subject_ids: set[str] | None = None,
    log: Callable[[str], None] = print,
    *,
    days: int | None = None,
) -> IngestResult:
    """The weekly ingest, or when `seeding` the SEED_DAYS history; `days` sets another window
    length (`im weekly` shortens the week the corpus starts in). Seeding `subject_ids` runs only
    their news and gap searches: official sources and trends are not per subject."""
    settings = get_settings()
    conn = connect()
    length = days or (SEED_DAYS if seeding else WEEK_DAYS)
    window = Window(resolve_t0(conn, t0_override), length)
    if seeding and (kept := record_corpus_start(conn, window)) and window.start < kept:
        log(
            f"corpus start stays {kept}: this seed reaches back to {window.start}, but runs "
            f"read only from {kept}. Set meta corpus_start by hand to move it."
        )
    result = IngestResult(t0=window.t0)
    http = CachedHttp()
    # This run's unique documents, deduped like the store (canonical URL and content hash).
    seen: list[RawDocument] = []
    seen_keys: set[str] = set()
    # Documents each source returned this run, stored now or before.
    returned: Counter[str] = Counter()
    log(f"T0 {window.t0}, window {window.start} to {window.t0}")

    def keep(docs: Iterable[RawDocument]) -> None:
        for doc in docs:
            returned[doc.source] += 1
            keys = {canonical_url(doc.url), content_hash(doc.text)}
            if not keys & seen_keys:
                seen.append(doc)
            seen_keys.update(keys)
            if _store(conn, doc):
                result.added[doc.source] += 1
            else:
                result.duplicates += 1
        conn.commit()

    def fail(label: str, error: FetchError) -> None:
        result.failures.append(f"{label}: {error}")
        log(f"failed: {label}: {error}")

    # 1. Official sources: list the window, then fetch each page; then search the official sites
    # that have no feed, and backfill each feed that does not reach the window start.
    if subject_ids is None:
        official = official_listings(http, window)
        for source, error in official.failed.items():
            fail(f"official {source}", error)

        def fetch(listing: Listing) -> RawDocument | None:
            try:
                return fetch_listing(http, listing)
            except FetchError as e:
                fail(listing.url, e)
                return None

        with ThreadPoolExecutor(_PAGE_WORKERS) as pool:
            keep(d for d in pool.map(fetch, official.listings) if d is not None)
        failed = set(official.failed)
        # An API source that failed is read through Exa instead, where it has a search set.
        for source in official.failed:
            if fallback := API_FALLBACKS.get(source):
                try:
                    docs = site_search_documents(http, settings.exa_api_key, fallback, window)
                except FetchError as e:
                    fail(f"official fallback search {source}", e)
                    continue
                log(f"official {source}: API failed, {len(docs)} read through Exa")
                keep(docs)
        for site in SITE_SEARCHES:
            try:
                keep(site_search_documents(http, settings.exa_api_key, site, window))
            except FetchError as e:
                failed.add(site.source)
                fail(f"official site search {site.source}", e)
        # A feed holding only its latest items may have missed the start of the window, so its
        # sites are searched for the window too; the feed's own items are left out.
        backfilled: dict[str, int] = {}
        for feed in official.short_feeds:
            label = f"{feed.source} {feed.doc_type}"
            listed = [item for item in official.listings if item.source == feed.source]
            try:
                docs = feed_backfill(http, settings.exa_api_key, feed, window, listed)
            except FetchError as e:
                failed.add(feed.source)
                fail(f"official backfill {label}", e)
                continue
            backfilled[label] = len(docs)
            keep(docs)
        # Short: a feed missed the window start and Exa found nothing either, so the source may
        # have had items we could not reach. Quiet: reached, and returned nothing for the window.
        short_sources = {f.source for f in official.short_feeds}
        silent = [s for s in OFFICIAL_SOURCES if not returned[s] and s not in failed]
        short = [s for s in silent if s in short_sources]
        quiet = [s for s in silent if s not in short_sources]
        stored = sum(result.added.values())
        log(
            f"official: {len(official.listings)} listed by feeds and APIs, "
            f"{stored} stored with searches"
        )
        log(f"official returned by source: {dict(returned)}; stored: {dict(result.added)}")
        if backfilled:
            log(f"feeds whose oldest item is after {window.start}, Exa backfill: {backfilled}")
        log(f"official failed: {sorted(failed)}")
        log(f"official short: {short}")
        log(f"official quiet: {quiet}")

    # 2. News: Exa over reputable domains, queries generated from subjects.yaml.
    seed = load_subjects()
    queries = [q for q in news_queries(seed) if subject_ids is None or q.subject_id in subject_ids]

    def search_news(query: SearchQuery) -> list[RawDocument]:
        try:
            return exa_search(
                http, settings.exa_api_key, query.text, window, NEWS_DOMAINS, EXA_RESULTS_PER_QUERY
            )
        except FetchError as e:
            fail(f"exa {query.text}", e)
            return []

    with ThreadPoolExecutor(_SEARCH_WORKERS) as pool:
        for batch in pool.map(search_news, queries):
            keep(batch)
    log(f"news: {len(queries)} queries, {result.added['exa']} stored")

    # 3. Gaps: for thinly covered subjects, a wider Exa news search and Exa on the subject's own
    # domains. Only hits that name the subject are kept. Thin subjects are decided from this run's
    # results (not the store), so a rerun picks the same subjects. Groups are not monitored, so
    # only topics and entities get gap searches.
    counts = candidate_counts((d.title, d.text) for d in seen)
    matchers = {m.id: m for m in subject_matchers() if m.type != "group"}
    target = GAP_TARGET if seeding else WEEK_GAP_TARGET
    result.gap_subjects = [
        m for m in matchers if counts[m] < target and (subject_ids is None or m in subject_ids)
    ]
    entities = {e.id: e for e in seed.entities}

    def fill_gap(subject_id: str) -> list[RawDocument]:
        label = subject_label(seed, subject_id)
        entity = entities.get(subject_id)
        name = entity.name if entity else label
        own = entity.domains if entity else []
        n = GAP_RESULTS_PER_QUERY
        docs: list[RawDocument] = []
        try:
            phrase = gap_query(entity) if entity else f'"{name}"'
            docs += exa_search(
                http, settings.exa_api_key, phrase, window, NEWS_DOMAINS, GAP_NEWS_RESULTS, own
            )
            if own:
                docs += exa_search(http, settings.exa_api_key, label, window, own, n, own)
        except FetchError as e:
            fail(f"gap {label}", e)
        matcher = matchers[subject_id]
        return [d for d in docs if matcher.matches(normalize(f"{d.title}\n{d.text}"))]

    for subject_id in result.gap_subjects:
        keep(fill_gap(subject_id))
    log(f"gaps: {result.gap_subjects}")

    # 4. Search trends.
    if subject_ids is None:
        fetched = fetch_trends(conn, http, window.t0, log=log)
        result.failures += fetched.failures

    network, cached = sum(http.network.values()), sum(http.cached.values())
    log(f"requests: {network} external, {cached} served from cache")
    log(f"external by source: {dict(sorted(http.network.items()))}")
    return result


def _store(conn: sqlite3.Connection, doc: RawDocument) -> bool:
    canonical = canonical_url(doc.url)
    text = unreverse_lines(doc.text)
    published = doc.published_at
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    return insert_document(
        conn,
        SourceDocument(
            id=document_id(canonical),
            source=doc.source,
            collection=doc.collection,
            doc_type=doc.doc_type,
            classification="public",
            url=doc.url,
            canonical_url=canonical,
            domain=domain_of(doc.url),
            title=doc.title,
            published_at=published.astimezone(UTC),
            text=text,
            content_hash=content_hash(text),
            ingested_at=datetime.now(UTC),
        ),
    )


@dataclass(frozen=True)
class Redated:
    id: str
    url: str
    title: str
    was: datetime
    now: datetime
    removed: bool  # dated before the corpus start, so ingest would have dropped it


# Per-document rows a removed document leaves behind; run rows go when the runs are recomputed.
_DOCUMENT_TABLES = ["document_tags", "document_subjects", "findings", "detections"]


def redate_stored(conn: sqlite3.Connection) -> list[Redated]:
    """Apply the URL-date rule (search.url_dated) to every stored document. A document it dates
    before the corpus start is removed with its per-document rows, as ingest drops it.
    Returns what changed; the affected runs must be recomputed afterwards."""
    start = corpus_start(conn)
    if start is None:
        raise RuntimeError("no corpus start in the store: run `im ingest --seed` first")
    changed: list[Redated] = []
    for doc in list_documents(conn):
        now = url_dated(doc.url, doc.published_at)
        if now != doc.published_at:
            removed = now.date() < start
            changed.append(Redated(doc.id, doc.url, doc.title, doc.published_at, now, removed))
    with conn:
        conn.executemany(
            "UPDATE source_documents SET published_at = ? WHERE id = ?",
            [(r.now.isoformat().replace("+00:00", "Z"), r.id) for r in changed if not r.removed],
        )
        removed = [(r.id,) for r in changed if r.removed]
        for table in [*_DOCUMENT_TABLES, "source_documents"]:
            column = "id" if table == "source_documents" else "document_id"
            conn.executemany(f"DELETE FROM {table} WHERE {column} = ?", removed)  # noqa: S608
    return changed
