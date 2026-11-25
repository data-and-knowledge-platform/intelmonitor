"""FastAPI app. Run with `uv run im serve` (behind Portless in development)."""

import asyncio
import logging
import sqlite3
from collections import Counter
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from intelmonitor.config import DATA_DIR
from intelmonitor.pipeline import runlog
from intelmonitor.pipeline.params import (
    MATERIAL_LEVEL,
    NEEDS_ATTENTION_LEVEL,
    PRIORITY_DEFINITIONS,
    PRIORITY_LABELS,
)
from intelmonitor.pipeline.subjects import all_subjects
from intelmonitor.seed import load_detectors
from intelmonitor.server import runs
from intelmonitor.server import series as series_views
from intelmonitor.server import trend as trend_view
from intelmonitor.server.developments import DevelopmentQuery, development_list
from intelmonitor.server.evidence import development_sources, document_detail, new_documents
from intelmonitor.server.impacts import (
    IMPACT_PERIOD_DAYS,
    development_impacts,
    direct_subjects,
    subject_impacts,
)
from intelmonitor.server.lists import subject_list
from intelmonitor.server.models import (
    DataSources,
    DataSourceStats,
    DevelopmentDetail,
    DevelopmentList,
    DocumentDetail,
    FunctionDetail,
    LatestRun,
    ListName,
    MaterialityLevel,
    MonitorPage,
    Period,
    RelatedDays,
    RelatedGraph,
    RunInfo,
    SearchResults,
    SubjectConfidence,
    SubjectDetail,
    SubjectId,
    SubjectImpact,
    SubjectList,
    SubjectSummary,
)
from intelmonitor.server.queries import (
    NoRunError,
    NotFoundError,
    Queries,
    expand,
    functions,
    page_subject,
    peers,
    subject_ref,
)
from intelmonitor.server.related_graph import related_graph
from intelmonitor.server.search import search as search_all
from intelmonitor.store.db import connect, get_meta
from intelmonitor.store.publish import Bucket, PublishedStore

WEB_ORIGIN = "https://intelmonitor.web.localhost"
# How often the site checks the bucket for a newly published store.
POLL_SECONDS = 600

log = logging.getLogger(__name__)

# With a bucket configured (the deployed site), the API serves the published store read-only;
# without one (local development), the local store, migrated on start.
_bucket = Bucket.from_settings()
_published = PublishedStore(_bucket, DATA_DIR / "stores") if _bucket else None


async def _poll(store: PublishedStore) -> None:
    """Swap in each newly published store; a failed check keeps serving the current one."""
    while True:
        await asyncio.sleep(POLL_SECONDS)
        try:
            await asyncio.to_thread(store.refresh)
        except Exception:
            log.exception("checking the bucket for a new store failed")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    if _published is None:
        conn = connect()
        runlog.interrupt_stale(conn)
        runlog.backfill(conn)
        runlog.migrate(conn)
        conn.close()
        yield
        return
    await asyncio.to_thread(_published.refresh)
    poll = asyncio.create_task(_poll(_published))
    yield
    poll.cancel()


def _open() -> sqlite3.Connection:
    return _published.open() if _published else connect()


def _operation_id(route: APIRoute) -> str:
    """Operation ids are the handler names, so the generated TS client reads naturally."""
    return route.name


app = FastAPI(
    title="IntelMonitor API",
    lifespan=lifespan,
    generate_unique_id_function=_operation_id,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[WEB_ORIGIN, "http://localhost:3000"],
    # Another Portless name, such as a worktree's web server next to the main one.
    allow_origin_regex=r"https://[a-z0-9.-]+\.localhost",
    allow_methods=["*"],
    allow_headers=["*"],
)
# The list pages' rows carry full subject refs; gzip takes /lists/entities from about 51 KB to 7 KB.
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.exception_handler(NotFoundError)
async def _not_found(_: Request, error: NotFoundError) -> JSONResponse:
    return JSONResponse({"detail": f"not found: {error}"}, status_code=404)


@app.exception_handler(NoRunError)
async def _no_run(_: Request, error: NoRunError) -> JSONResponse:
    return JSONResponse({"detail": str(error)}, status_code=503)


def _queries() -> Iterator[Queries]:
    conn = _open()
    try:
        yield Queries(conn)
    finally:
        conn.close()


Q = Annotated[Queries, Depends(_queries)]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ---- subjects, entity and topic pages, search


@app.get("/subjects")
def list_subjects(q: Q) -> list[SubjectSummary]:
    """The catalogue: every topic, group and displayed entity, with a topic's function."""
    return [q.subject_summary(i) for i, s in all_subjects().items() if s.displayed]


@app.get("/functions")
def list_functions() -> list[FunctionDetail]:
    """MAS's five functions, each with the topics it owns and its view's fixed watchlist."""
    return functions()


@app.get("/lists/{list_name}")
def get_list(q: Q, list_name: ListName, period: Period = "week") -> SubjectList:
    """Every entity or every topic with its developments in the period counted by priority, its
    top development, its New count and latest date, ranked High, Medium, then name. This week
    runs from the previous run's date; 4w and 3m are 28 and 91 days to the run date."""
    return subject_list(q, list_name, period)


@app.get("/subjects/{subject_id}")
def get_subject(q: Q, subject_id: str) -> SubjectDetail:
    """A topic, group or displayed entity; 404 for a hidden entity, which has no page."""
    page_subject(subject_id)
    return q.subject_detail(subject_id)


@app.get("/subjects/{subject_id}/page")
def get_monitor_page(q: Q, subject_id: str) -> MonitorPage:
    """An entity or topic page: the subject, the run, its upcoming dates, whether it has a Data
    tab, its brief and the developments that reach it indirectly. 404 for a hidden entity."""
    page_subject(subject_id)
    return MonitorPage(
        subject=q.subject_detail(subject_id),
        run=q.run,
        upcoming=q.upcoming([subject_id]),
        trend=trend_view.has_trend(subject_id),
        brief=q.brief(subject_id),
        impacts=subject_impacts(q, subject_id, IMPACT_PERIOD_DAYS),
    )


@app.get("/subjects/{subject_id}/impacts")
def get_subject_impacts(
    q: Q,
    subject_id: str,
    days: Annotated[int, Query(ge=1, le=365)] = IMPACT_PERIOD_DAYS,
) -> list[SubjectImpact]:
    """Developments in the latest run that reach the subject indirectly through stored
    relationships, last published within `days` of the run date (day, week or month filters).
    Never news about the subject itself."""
    page_subject(subject_id)
    return subject_impacts(q, subject_id, days)


@app.get("/subjects/{subject_id}/related-graph")
def get_related_graph(q: Q, subject_id: str, days: RelatedDays = RelatedDays.week) -> RelatedGraph:
    """Every entity linked to the subject in the relationship store, either direction, each with
    its links and the shown developments of the last `days` (7, as the brief, 30 or 90) that
    reach the subject through it."""
    page_subject(subject_id)
    return related_graph(q, subject_id, days)


@app.get("/subjects/{subject_id}/trend")
def get_subject_trend(q: Q, subject_id: str) -> trend_view.SubjectTrend:
    """The subject's series over the 12 months to the latest run with its High and Medium
    developments, and each run's note on why the series moved with the developments it cites
    and the facts it was given."""
    page_subject(subject_id)
    return trend_view.trend(q, subject_id)


@app.get("/subjects/{subject_id}/indicators")
def get_subject_indicators(
    q: Q, subject_id: str, as_of: date | None = None
) -> series_views.SubjectIndicators:
    """The subject's measured series, primary then context, each with its latest value, change
    over 1 week, 1 month, 3 months and 1 year, percentile in its history and state, from what was
    available by the end of `as_of` (default today)."""
    page_subject(subject_id)
    return series_views.subject_indicators(q.conn, subject_id, as_of or date.today())


@app.get("/series/{series_id}")
def get_series(
    q: Q,
    series_id: str,
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
) -> series_views.SeriesDetail:
    """A series' observations with periods from `from` to `to` (inclusive, both optional) and
    the revisions of those periods."""
    return series_views.series_detail(q.conn, series_id, start, end)


@app.get("/search")
def search(
    q: Q,
    text: Annotated[str, Query(alias="q", min_length=1)],
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
) -> SearchResults:
    """Subjects by name and alias, plus up to `limit` of the latest run's developments and up to
    `limit` of their source documents by full text."""
    return search_all(q, text, limit)


# ---- developments, evidence and documents


@app.get("/developments")
def list_developments(q: Q, query: Annotated[DevelopmentQuery, Query()]) -> DevelopmentList:
    """The latest run's developments in display order for some subjects, and an entity's peers
    when asked, narrowed by facet filters, with each facet's counts."""
    ids = list(query.subject_id)
    if query.peers_of is not None:
        ids += peers(query.peers_of)
    return development_list(q, ids, query)


@app.get("/developments/{development_id}")
def get_development(
    q: Q,
    development_id: str,
    subject_id: Annotated[list[SubjectId] | None, Query()] = None,
) -> DevelopmentDetail:
    """One development with its sources and who it impacts. Priority, adverse and tone are for
    the subjects in view when given, else over all its subjects."""
    return _development_detail(q, development_id, list(subject_id or []))


def _development_detail(
    q: Queries, development_id: str, scope: list[str] | None = None
) -> DevelopmentDetail:
    d = q.development(development_id)
    if scope:
        d = q.devs.scoped(d, expand(scope))
    confidence = [
        SubjectConfidence(
            subject=subject_ref(r["subject_id"]), confidence=r["confidence"], source=r["source"]
        )
        for r in q.conn.execute(
            "SELECT subject_id, confidence, source FROM development_subjects "
            "WHERE run_id = ? AND development_id = ? ORDER BY confidence DESC",
            (q.run.id, development_id),
        )
        if r["subject_id"] in all_subjects()
    ]
    row = q.conn.execute(
        "SELECT materiality_confidence, materiality_rule FROM developments "
        "WHERE run_id = ? AND id = ?",
        (q.run.id, development_id),
    ).fetchone()
    rule = row["materiality_rule"]
    detectors = {x.id: x.name for x in load_detectors().detectors}
    return DevelopmentDetail(
        **d.model_dump(),
        run=q.run,
        materiality_confidence=row["materiality_confidence"],
        materiality_rule=detectors.get(rule, rule) if rule else None,
        subject_confidence=confidence,
        sources=development_sources(
            q.conn,
            q.run.id,
            q.run.as_of,
            development_id,
            new_documents(q.conn, q.run.id, development_id),
        ),
        direct_subjects=direct_subjects(q, development_id),
        impacts=development_impacts(q, development_id),
    )


@app.get("/documents/{document_id}")
def get_document(q: Q, document_id: str) -> DocumentDetail:
    doc = document_detail(q.conn, document_id)
    if doc is None:
        raise NotFoundError(f"document {document_id}")
    return doc


@app.get("/materiality-levels")
def materiality_levels(q: Q) -> list[MaterialityLevel]:
    """Materiality levels for Help, most material first, with the latest run's count of shown
    developments at each."""
    counts = Counter(d.materiality_level for d in q.devs.by_id.values())
    return [
        MaterialityLevel(
            level=level,
            name=name,
            description=PRIORITY_DEFINITIONS[level].definition,
            examples=PRIORITY_DEFINITIONS[level].examples,
            material=level >= MATERIAL_LEVEL,
            needs_attention=level >= NEEDS_ATTENTION_LEVEL,
            developments=counts[level],
        )
        for level, name in reversed(list(enumerate(PRIORITY_LABELS)))
    ]


# ---- runs and data sources


@app.get("/runs")
def list_runs(q: Q) -> list[RunInfo]:
    """Every run execution, newest first, with how it started and what it found."""
    return runs.list_runs(q.conn)


@app.get("/runs/latest")
def latest_run(q: Q) -> LatestRun:
    """The latest complete run and the date of the latest news it covers."""
    news = max((d.last_published for d in q.devs.by_id.values()), default=None)
    return LatestRun(**q.run.model_dump(), latest_news=min(news, q.run.as_of) if news else None)


@app.get("/data-sources")
def data_sources(q: Q) -> DataSources:
    rows = q.conn.execute(
        "SELECT d.source, d.collection, count(*) AS documents, "
        "coalesce(sum(t.relevant), 0) AS relevant, min(substr(d.published_at, 1, 10)) AS first, "
        "max(substr(d.published_at, 1, 10)) AS last FROM source_documents d "
        "LEFT JOIN document_tags t ON t.document_id = d.id "
        "GROUP BY d.source, d.collection ORDER BY documents DESC"
    ).fetchall()
    t0 = get_meta(q.conn, "t0")
    return DataSources(
        t0=date.fromisoformat(t0) if t0 else None,
        total_documents=sum(r["documents"] for r in rows),
        sources=[
            DataSourceStats(
                source=r["source"],
                collection=r["collection"],
                documents=r["documents"],
                relevant_documents=r["relevant"],
                first_published=r["first"],
                last_published=r["last"],
            )
            for r in rows
        ],
    )
