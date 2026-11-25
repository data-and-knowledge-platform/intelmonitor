"""An entity or topic page's Data tab (docs/archive/structured-data-plan.md, F6): the subject's
series over the 12 months to the latest run, the High and Medium developments marked on them, and
the weeks of the runs in that span."""

from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from intelmonitor.pipeline.detectors import corpus_start
from intelmonitor.pipeline.indicators import months_before
from intelmonitor.pipeline.key_data import mapped_series
from intelmonitor.pipeline.params import MATERIAL_LEVEL, RUN_SPACING_DAYS
from intelmonitor.seed import SeriesDef
from intelmonitor.server.models import (
    DevelopmentSummary,
    LinePoint,
    RunRef,
    SeriesInfo,
    SubjectRef,
)
from intelmonitor.server.queries import Queries, subject, subject_ref
from intelmonitor.server.series import series_info
from intelmonitor.store.documents import first_document_day
from intelmonitor.store.series import read_observations

TREND_MONTHS = 12

# needs_attention: High priority. watch: Medium.
MarkBadge = Literal["needs_attention", "watch"]


class TrendSeries(BaseModel):
    series: SeriesInfo
    line: list[LinePoint] = Field(
        description="Observations over the 12 months to the run date, as known by then"
    )


class TrendRun(BaseModel):
    run: RunRef
    since: date = Field(description="The week's start: the previous run's date")


class MarkedDevelopment(BaseModel):
    """A development marked on the chart."""

    development: DevelopmentSummary
    on: date = Field(
        description="Where it sits: its event date when in the chart's 12 months, else its first "
        "publication"
    )
    badge: MarkBadge


class SubjectTrend(BaseModel):
    subject: SubjectRef
    run: RunRef
    start: date = Field(description="The chart's start, 12 months before the run date")
    documents_from: date = Field(description="The first document's day: none before it")
    series: list[TrendSeries] = Field(
        description="The subject's own listed series, else its listed context ones, in "
        "catalogue order"
    )
    developments: list[MarkedDevelopment] = Field(
        description="The subject's High and Medium developments dated in the 12 months, newest "
        "first"
    )
    runs: list[TrendRun] = Field(
        description="Complete runs within the 12 months, oldest first; the last is the latest"
    )


def trend_series(subject_id: str) -> list[SeriesDef]:
    """The series a subject's Trend view covers: its own listed series, else the listed series it
    has as context (a bank's system-wide figures), in catalogue order."""
    listed = [s for s in mapped_series(subject_id) if s.listed]
    own = [s for s in listed if subject_id in s.subjects.primary]
    return own or listed


def has_trend(subject_id: str) -> bool:
    return bool(trend_series(subject_id))


def documents_from(q: Queries, as_of: date) -> date:
    """The day of the earliest stored document, else the corpus start: the chart has no marks
    before it because nothing was collected, not because nothing happened."""
    return first_document_day(q.conn) or corpus_start(q.conn, as_of)


def marked(q: Queries, subject_id: str, as_of: date) -> list[MarkedDevelopment]:
    """The subject's High and Medium developments, by priority for it, each at its event date
    within the 12 months to `as_of`, else at its first publication."""
    start = months_before(as_of, 12)
    out: list[MarkedDevelopment] = []
    for d in q.developments_in([subject_id]):
        if d.materiality_level < MATERIAL_LEVEL:
            continue
        on = d.event_date if d.event_date and start <= d.event_date <= as_of else d.first_published
        if start <= on <= as_of:
            badge: MarkBadge = "needs_attention" if d.needs_attention else "watch"
            out.append(MarkedDevelopment(development=d, on=on, badge=badge))
    return sorted(out, key=lambda m: m.on, reverse=True)


def trend(q: Queries, subject_id: str) -> SubjectTrend:
    """The Trend view as of the latest run."""
    subject(subject_id)
    as_of = q.run.as_of
    start = months_before(as_of, TREND_MONTHS)
    runs = [
        RunRef(id=r["id"], as_of=r["as_of"], previous_run_id=r["previous_run_id"])
        for r in q.conn.execute(
            "SELECT id, as_of, previous_run_id FROM runs WHERE status = 'complete' "
            "AND as_of >= ? AND as_of <= ? ORDER BY as_of",
            (start.isoformat(), as_of.isoformat()),
        )
    ]
    trend_runs = [
        TrendRun(
            run=run,
            since=date.fromisoformat(run.previous_run_id)
            if run.previous_run_id
            else run.as_of - timedelta(days=RUN_SPACING_DAYS),
        )
        for run in runs
    ]
    return SubjectTrend(
        subject=subject_ref(subject_id),
        run=q.run,
        start=start,
        documents_from=documents_from(q, as_of),
        series=[
            TrendSeries(
                series=series_info(s),
                line=[
                    LinePoint(period=o.period, value=o.value, preliminary=o.preliminary)
                    for o in read_observations(q.conn, s.id, as_of)
                    if o.period >= start
                ],
            )
            for s in trend_series(subject_id)
        ],
        developments=marked(q, subject_id, as_of),
        runs=trend_runs,
    )
