"""The Entities and Topics list pages: every entity or topic with its figures for a period, built
in one pass over the latest run's cached developments (server/queries.py `RunDevelopments`).

A development is in the period when it was last published after the period's start. Counts,
ranking and the top development use direct priority only (`development_subjects`, where a topic
holds the highest level among the development's entities); indirect impact never counts. New and
the latest date ignore the period. Hidden entities have no row."""

from dataclasses import dataclass, field
from datetime import date, timedelta

from intelmonitor.pipeline.params import PRIORITY_LABELS, RUN_SPACING_DAYS, display_key
from intelmonitor.pipeline.subjects import all_subjects
from intelmonitor.seed import load_subjects
from intelmonitor.server.models import (
    DevelopmentSummary,
    ListName,
    ListRow,
    Period,
    PriorityCounts,
    SubjectList,
    TopDevelopment,
)
from intelmonitor.server.queries import Queries, SubjectPriority, function_ref, subject_ref

# Days before the run date that the longer periods start; "week" starts at the previous run.
PERIOD_DAYS: dict[Period, int] = {"4w": 28, "3m": 91}


def period_start(q: Queries, period: Period) -> date:
    """The period's exclusive start. This week runs from the previous run's date, so it covers
    what New covers; a first run, with none before it, starts a run spacing back."""
    if period != "week":
        return q.run.as_of - timedelta(days=PERIOD_DAYS[period])
    previous = q.run.previous_run_id
    if previous is None:
        return q.run.as_of - timedelta(days=RUN_SPACING_DAYS)
    row = q.conn.execute("SELECT as_of FROM runs WHERE id = ?", (previous,)).fetchone()
    return date.fromisoformat(row["as_of"] if row else previous)


@dataclass
class _Tally:
    """One subject's figures while the developments are walked."""

    counts: list[int] = field(default_factory=lambda: [0] * len(PRIORITY_LABELS))
    top: tuple[tuple[int, bool, int, str], DevelopmentSummary, int] | None = None
    new: int = 0
    latest: date | None = None


def subject_list(q: Queries, name: ListName, period: Period) -> SubjectList:
    kind = "entity" if name == "entities" else "topic"
    subjects = [s for s in all_subjects().values() if s.type == kind and s.displayed]
    since = period_start(q, period)
    tallies = {s.id: _Tally() for s in subjects}
    # Topics in seed order, which breaks ties for a development's lead topic.
    topic_order = {
        sid: n for n, sid in enumerate(s.id for s in all_subjects().values() if s.type == "topic")
    }
    for d in q.devs.by_id.values():
        mine = q.devs.priorities.get(d.id, {})
        for sid, p in mine.items():
            t = tallies.get(sid)
            if t is None:
                continue
            if d.change == "new":
                t.new += 1
            t.latest = max(t.latest or d.last_published, d.last_published)
            if d.last_published <= since:
                continue
            t.counts[p.level] += 1
            key = display_key(p.level, p.adverse, d.last_published, d.id)
            if t.top is None or key < t.top[0]:
                t.top = (key, d, p.level)

    def top(t: _Tally) -> TopDevelopment | None:
        if t.top is None:
            return None
        _, d, level = t.top
        topic = None if kind == "topic" else _lead_topic(q.devs.priorities[d.id], topic_order)
        return TopDevelopment(
            id=d.id,
            headline=d.headline,
            priority=PRIORITY_LABELS[level],
            topic=subject_ref(topic) if topic else None,
        )

    groups = load_subjects().groups
    rows: list[ListRow] = []
    for s in subjects:
        t = tallies[s.id]
        rows.append(
            ListRow(
                **subject_ref(s.id).model_dump(),
                groups=[subject_ref(g.id) for g in groups if s.id in g.members],
                function=function_ref(s.function) if s.function else None,
                counts=PriorityCounts(high=t.counts[2], medium=t.counts[1], low=t.counts[0]),
                top=top(t),
                new=t.new,
                latest=t.latest,
            )
        )
    rows.sort(key=lambda r: (-r.counts.high, -r.counts.medium, r.name.casefold()))
    return SubjectList(run=q.run, period=period, since=since, rows=rows)


def _lead_topic(priorities: dict[str, SubjectPriority], topic_order: dict[str, int]) -> str | None:
    """A development's highest-priority topic, then the one screening was surest of, then the
    first in the seed's topic order (`topic_order`, topic id to position)."""
    tagged = [
        (p.level, p.confidence, -topic_order[sid], sid)
        for sid, p in priorities.items()
        if sid in topic_order
    ]
    return max(tagged)[3] if tagged else None
