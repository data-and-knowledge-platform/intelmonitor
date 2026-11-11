"""Measured series: one series' observations and revisions, and a subject's indicators
(docs/archive/structured-data-plan.md, F1). The catalogue comes from api/seed/series.yaml."""

import sqlite3
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from intelmonitor.ingest.parse import document_id
from intelmonitor.pipeline.detectors import Detection, live_events, materiality_level
from intelmonitor.pipeline.indicators import Indicator, compute_indicators, months_before
from intelmonitor.pipeline.key_data import KeyData, key_data
from intelmonitor.pipeline.params import (
    NEEDS_ATTENTION_LEVEL,
    PRIORITY_LABELS,
    RUN_SPACING_DAYS,
    Priority,
)
from intelmonitor.seed import Deviation, SeriesDef, load_series, providers, source_label
from intelmonitor.server.models import LinePoint, SeriesChart, SeriesInfo, SubjectRef
from intelmonitor.server.queries import NotFoundError, subject, subject_ref
from intelmonitor.store.series import (
    Observation,
    ObservationSource,
    Revision,
    read_observations,
    read_revisions,
    read_sources,
)

# primary: the subject's own series. context: system-wide or market-wide context for it.
SeriesRole = Literal["primary", "context"]


class SeriesSubject(BaseModel):
    subject: SubjectRef
    role: SeriesRole


class SeriesDetail(BaseModel):
    series: SeriesInfo
    subjects: list[SeriesSubject]
    observations: list[Observation] = Field(description="By period, at their current value")
    revisions: list[Revision] = Field(description="Of periods in the range, in the order made")


# From the detector rules on the series (api/seed/detectors.yaml): needs_attention when a live
# event's rule is High, watch for a lower one, else stable.
IndicatorStatus = Literal["stable", "watch", "needs_attention"]


class IndicatorEvent(BaseModel):
    """A detector event behind an indicator's status."""

    statement: str = Field(description="The sentence code wrote for it")
    materiality_label: Priority = Field(description="Its rule's priority, e.g. Medium")
    known_on: date = Field(description="When every value it rests on was known")
    development_id: str | None = Field(
        description="The development carrying it in the latest run by the date, if any"
    )


class SubjectIndicator(Indicator):
    series: SeriesInfo
    role: SeriesRole
    status: IndicatorStatus
    events: list[IndicatorEvent] = Field(
        description="Detector events known since the previous run, or still holding at the "
        "latest point, worst first"
    )
    line: list[LinePoint] = Field(
        description="Observations over the 12 months to the date, as known by then; none for "
        "a series not listed"
    )
    sources: list[ObservationSource] = Field(
        description="Where the latest figure and each figure in the line come from, by period, "
        "for a series collected by hand; empty for one fetched from an API"
    )


class SubjectIndicators(BaseModel):
    subject: SubjectRef
    as_of: date
    indicators: list[SubjectIndicator] = Field(
        description="Primary series first, then context, each in catalogue order"
    )
    key_data: KeyData = Field(description="The series the Overview's Key data shows, in order")


_ROLES: list[SeriesRole] = ["primary", "context"]


def _members(s: SeriesDef, role: SeriesRole) -> list[str]:
    return s.subjects.primary if role == "primary" else s.subjects.context


def mapped_series(subject_id: str) -> list[tuple[SeriesDef, SeriesRole]]:
    """The series mapped to a subject, primary then context, each in catalogue order."""
    seed = load_series().series
    return [(s, role) for role in _ROLES for s in seed if subject_id in _members(s, role)]


def series_info(s: SeriesDef) -> SeriesInfo:
    by_id = {d.id: d for d in load_series().series}
    return SeriesInfo(
        id=s.id,
        name=s.name,
        short_name=s.short_name or s.name,
        group=s.group,
        scope=s.scope,
        listed=s.listed,
        unit=s.unit,
        frequency=s.frequency,
        year_to_date=s.year_to_date,
        decimals=s.decimals,
        bad_direction=s.bad_direction,
        baseline=0.0 if isinstance(s.source, Deviation) else None,
        source=s.source_name,
        source_label=source_label(s, by_id),
        providers=list(dict.fromkeys(providers(s, by_id))),
        source_url=s.source_url,
    )


def series_detail(
    conn: sqlite3.Connection, series_id: str, start: date | None, end: date | None
) -> SeriesDetail:
    """A series' observations with periods from `start` to `end` (both optional, inclusive) and
    the revisions of those periods."""
    s = next((s for s in load_series().series if s.id == series_id), None)
    if s is None:
        raise NotFoundError(f"series {series_id}")

    def within(period: date) -> bool:
        return (start is None or period >= start) and (end is None or period <= end)

    subjects = [SeriesSubject(subject=subject_ref(i), role="primary") for i in s.subjects.primary]
    subjects += [SeriesSubject(subject=subject_ref(i), role="context") for i in s.subjects.context]
    return SeriesDetail(
        series=series_info(s),
        subjects=subjects,
        observations=[o for o in read_observations(conn, series_id) if within(o.period)],
        revisions=[r for r in read_revisions(conn, series_id) if within(r.period)],
    )


def _previous_run_date(conn: sqlite3.Connection, as_of: date) -> date:
    """The latest completed run before `as_of`, or RUN_SPACING_DAYS before it without one."""
    row = conn.execute(
        "SELECT max(as_of) FROM runs WHERE status = 'complete' AND as_of < ?",
        (as_of.isoformat(),),
    ).fetchone()
    return date.fromisoformat(row[0]) if row[0] else as_of - timedelta(days=RUN_SPACING_DAYS)


def _event_views(
    conn: sqlite3.Connection, events: list[Detection], as_of: date
) -> dict[str, list[IndicatorEvent]]:
    """Each series' events, worst first, with the development that carries each one in the latest
    run by the date."""
    run = conn.execute(
        "SELECT max(id) FROM runs WHERE status = 'complete' AND as_of <= ?", (as_of.isoformat(),)
    ).fetchone()[0]
    out: dict[str, list[IndicatorEvent]] = {}
    worst_first = sorted(
        events, key=lambda e: (-materiality_level(e.detector), -e.known_at.timestamp())
    )
    for e in worst_first:
        row = conn.execute(
            "SELECT development_id FROM development_sources WHERE run_id = ? AND finding_id = ?",
            (run, f"{document_id(e.document_key)}-0"),
        ).fetchone()
        out.setdefault(e.series.id, []).append(
            IndicatorEvent(
                statement=e.statement,
                materiality_label=PRIORITY_LABELS[materiality_level(e.detector)],
                known_on=e.known_at.date(),
                development_id=row[0] if row else None,
            )
        )
    return out


def _status(events: list[IndicatorEvent]) -> IndicatorStatus:
    if not events:
        return "stable"
    worst = PRIORITY_LABELS.index(events[0].materiality_label)
    return "needs_attention" if worst >= NEEDS_ATTENTION_LEVEL else "watch"


def subject_indicators(
    conn: sqlite3.Connection, subject_id: str, as_of: date, series_id: str | None = None
) -> SubjectIndicators:
    """The indicators of every series mapped to the subject, or only `series_id`, from what was
    available by the end of `as_of`: each with its status from the detector events known since
    the previous run or still holding and its line over the 12 months to the date; and which of
    them Key data shows."""
    subject(subject_id)
    mapped: list[tuple[SeriesDef, SeriesRole]] = [
        (s, r) for s, r in mapped_series(subject_id) if series_id in (None, s.id)
    ]
    ids = [s.id for s, _ in mapped]
    computed = compute_indicators(conn, ids, as_of)
    live = live_events(conn, ids, as_of, _previous_run_date(conn, as_of))
    events = _event_views(conn, live, as_of)
    start = months_before(as_of, 12)
    indicators: list[SubjectIndicator] = []
    for s, role in mapped:
        own = events.get(s.id, [])
        latest = computed[s.id].latest
        line = [
            LinePoint(period=o.period, value=o.value, preliminary=o.preliminary)
            for o in (read_observations(conn, s.id, as_of) if s.listed else [])
            if o.period >= start
        ]
        shown = {p.period for p in line} | ({latest.period} if latest else set[date]())
        sources = read_sources(conn, s.id)
        indicators.append(
            SubjectIndicator(
                **computed[s.id].model_dump(),
                series=series_info(s),
                role=role,
                status=_status(own),
                events=own,
                line=line,
                sources=[sources[p] for p in sorted(shown) if p in sources],
            )
        )
    return SubjectIndicators(
        subject=subject_ref(subject_id),
        as_of=as_of,
        indicators=indicators,
        key_data=key_data(subject_id, computed, live),
    )


def chart_start(s: SeriesDef, history: list[Observation], as_of: date) -> date:
    """Where a detection's series chart starts: 12 months before the run date for a daily series, 24
    for a monthly one, and the start of the stored history for a quarterly or yearly one."""
    if s.frequency == "daily":
        return months_before(as_of, 12)
    if s.frequency == "monthly":
        return months_before(as_of, 24)
    return history[0].period if history else as_of


def series_chart(
    conn: sqlite3.Connection,
    s: SeriesDef,
    as_of: date,
    cited_from: date | None,
    cited_to: date | None,
) -> SeriesChart:
    """The series over its chart span to `as_of`, as known by the end of that day, with the periods
    from `cited_from` to `cited_to` highlighted and the value at `cited_to` stated."""
    history = read_observations(conn, s.id, as_of)
    start = chart_start(s, history, as_of)
    cited = next((o for o in history if o.period == cited_to), None)
    return SeriesChart(
        series=series_info(s),
        start=start,
        end=as_of,
        line=[
            LinePoint(period=o.period, value=o.value, preliminary=o.preliminary)
            for o in history
            if o.period >= start
        ],
        cited_from=cited_from,
        cited_to=cited_to,
        cited=cited,
        revisions=[
            r for r in read_revisions(conn, s.id, as_of) if cited and r.period == cited.period
        ],
    )
