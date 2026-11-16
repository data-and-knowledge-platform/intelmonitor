"""Read side of the API: everything is computed from the latest complete run and its base run.

`Queries` is built per request around one connection. The run's developments are loaded once per
run version and shared across requests, since the API reads far more often than runs change."""

import json
import sqlite3
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from functools import cached_property

from intelmonitor.pipeline.corroboration import corroboration
from intelmonitor.pipeline.extract import TONE_ORDER, Tone
from intelmonitor.pipeline.params import (
    JURISDICTION_THRESHOLD,
    NEEDS_ATTENTION_LEVEL,
    PRIORITY_LABELS,
    display_key,
)
from intelmonitor.pipeline.screen import JURISDICTION_NAMES
from intelmonitor.pipeline.subjects import Subject, all_subjects, scope
from intelmonitor.pipeline.summaries import stored_concerns
from intelmonitor.seed import load_subjects
from intelmonitor.server.models import (
    BriefConcern,
    Collection,
    DevelopmentSummary,
    EntityBrief,
    FunctionDetail,
    FunctionRef,
    RunRef,
    SourceTypeCount,
    SubjectDetail,
    SubjectLinks,
    SubjectRef,
    SubjectSummary,
    UpcomingItem,
)

UPCOMING_HORIZON_DAYS = 90
COLLECTION_ORDER: list[Collection] = ["official", "dms", "data", "news"]


class NoRunError(RuntimeError):
    """No complete pipeline run exists yet."""


class NotFoundError(LookupError):
    """A requested id does not exist."""


def subject(subject_id: str) -> Subject:
    found = all_subjects().get(subject_id)
    if found is None:
        raise NotFoundError(f"subject {subject_id}")
    return found


def page_subject(subject_id: str) -> Subject:
    """A subject that has a page: any topic, group or displayed entity. A hidden entity is found
    by subject() and named wherever it is tagged or reached, but has no page."""
    found = subject(subject_id)
    if not found.displayed:
        raise NotFoundError(f"subject {subject_id} has no page")
    return found


def subject_ref(subject_id: str) -> SubjectRef:
    s = subject(subject_id)
    seed = load_subjects()
    entity = next((e for e in seed.entities if e.id == subject_id), None)
    topic = next((t for t in seed.topics if t.id == subject_id), None)
    return SubjectRef(
        id=s.id,
        name=s.name,
        type=s.type,
        kind=s.kind,
        logo=entity.logo if entity else None,
        logo_full=entity.logo_full if entity else None,
        icon=topic.icon if topic else None,
        displayed=s.displayed,
    )


def functions() -> list[FunctionDetail]:
    """MAS's five functions in seed order, with their topics and fixed watchlists."""
    return [
        FunctionDetail(id=f.id, name=f.name, topics=f.topics, watchlist=f.watchlist)
        for f in load_subjects().functions
    ]


def function_ref(function_id: str) -> FunctionRef:
    f = next(f for f in load_subjects().functions if f.id == function_id)
    return FunctionRef(id=f.id, name=f.name)


def subject_links(subject_id: str) -> SubjectLinks:
    seed = load_subjects()
    s = subject(subject_id)
    if s.type == "topic":
        groups = [g for g in seed.groups if s.id in g.topics]
        topics: list[str] = []
        entities = [m for g in groups for m in g.members]
    elif s.type == "group":
        group = next(g for g in seed.groups if g.id == s.id)
        groups = []
        topics = group.topics
        entities = group.members
    else:
        groups = [g for g in seed.groups if s.id in g.members]
        topics = [t for g in groups for t in g.topics]
        entities = []
    return SubjectLinks(
        topics=[subject_ref(t) for t in dict.fromkeys(topics)],
        groups=[subject_ref(g.id) for g in groups],
        entities=[subject_ref(e) for e in dict.fromkeys(entities)],
    )


def peers(subject_id: str) -> list[str]:
    """The other members of the groups an entity belongs to, in seed order; none for topics and
    groups."""
    groups = [g for g in load_subjects().groups if subject(subject_id).id in g.members]
    return [m for m in dict.fromkeys(m for g in groups for m in g.members) if m != subject_id]


def aliases(subject_id: str) -> list[str]:
    return [a for e in load_subjects().entities if e.id == subject_id for a in e.aliases]


def domains(subject_id: str) -> list[str]:
    return [d for e in load_subjects().entities if e.id == subject_id for d in e.domains]


def description(subject_id: str) -> str:
    seed = load_subjects()
    if topic := next((t for t in seed.topics if t.id == subject_id), None):
        return topic.covers
    return subject(subject_id).description


@dataclass(frozen=True)
class SubjectPriority:
    """A development's priority, adverse flag and tone for one of its subjects, and how sure
    screening was of the tag."""

    level: int
    adverse: bool
    tone: Tone
    confidence: float


def telling_tone(tones: Iterable[Tone]) -> Tone:
    """The most telling of some tones: negative, then positive, else neutral."""
    return min([*tones, "neutral"], key=TONE_ORDER.index)


def priority_fields(priorities: dict[str, SubjectPriority]) -> dict[str, object]:
    """A summary's priority fields over some of its subjects, by subject id: the highest level,
    the subjects at that priority when the others rank lower, adverse if it is adverse to any of
    them, and their most telling tone."""
    level = max(p.level for p in priorities.values())
    top = PRIORITY_LABELS[level]
    at_top = [s for s, p in priorities.items() if PRIORITY_LABELS[p.level] == top]
    return {
        "materiality_level": level,
        "materiality_label": PRIORITY_LABELS[level],
        "needs_attention": level >= NEEDS_ATTENTION_LEVEL,
        "priority": top,
        "priority_for": [subject_ref(s) for s in at_top] if len(at_top) < len(priorities) else [],
        "adverse": any(p.adverse for p in priorities.values()),
        "direction": telling_tone(p.tone for p in priorities.values()),
    }


@dataclass(frozen=True)
class RunDevelopments:
    """A run's developments by id, in display order, with the subjects each one is tagged to:
    those it happened to or that did it, with its priority for each. A summary's own priority
    fields are over all of them. `by_id` holds the shown ones, so every consumer leaves the
    unconfirmed ones out (pipeline/corroboration.py) unless it asks for `unconfirmed`."""

    by_id: dict[str, DevelopmentSummary]
    unconfirmed: dict[str, DevelopmentSummary]
    subject_ids: dict[str, frozenset[str]]
    priorities: dict[str, dict[str, SubjectPriority]]

    def listed(self, unconfirmed: bool = False) -> list[DevelopmentSummary]:
        """The shown developments in display order, with the unconfirmed ones among them when
        asked."""
        if not unconfirmed:
            return list(self.by_id.values())
        return sorted([*self.by_id.values(), *self.unconfirmed.values()], key=_display_key)

    def scoped(self, d: DevelopmentSummary, subject_ids: set[str]) -> DevelopmentSummary:
        """The development as the subjects see it: its priority fields over those it is tagged
        to."""
        mine = {s: p for s, p in self.priorities.get(d.id, {}).items() if s in subject_ids}
        return d.model_copy(update=priority_fields(mine)) if mine else d


_run_cache: dict[tuple[str, str], RunDevelopments] = {}


def load_developments(conn: sqlite3.Connection, run: RunRef) -> RunDevelopments:
    tags: dict[str, list[str]] = {}
    actors: dict[str, list[str]] = {}
    priorities: dict[str, dict[str, SubjectPriority]] = {}
    for r in conn.execute(
        "SELECT development_id, subject_id, role, priority_level, adverse, tone, confidence "
        "FROM development_subjects WHERE run_id = ? "
        "ORDER BY priority_level DESC, priority_score DESC, confidence DESC",
        (run.id,),
    ):
        tags.setdefault(r["development_id"], []).append(r["subject_id"])
        if r["role"] == "actor":
            actors.setdefault(r["development_id"], []).append(r["subject_id"])
        priorities.setdefault(r["development_id"], {})[r["subject_id"]] = SubjectPriority(
            r["priority_level"], bool(r["adverse"]), r["tone"], r["confidence"]
        )
    documents: dict[str, dict[str, tuple[str, str]]] = {}
    jurisdictions: dict[str, set[str]] = {}
    for r in conn.execute(
        "SELECT s.development_id, s.document_id, d.collection, d.domain, t.jurisdictions "
        "FROM development_sources s JOIN source_documents d ON d.id = s.document_id "
        "LEFT JOIN document_tags t ON t.document_id = s.document_id WHERE s.run_id = ?",
        (run.id,),
    ):
        dev = r["development_id"]
        documents.setdefault(dev, {})[r["document_id"]] = (r["collection"], r["domain"])
        scores: dict[str, float] = json.loads(r["jurisdictions"] or "{}")
        jurisdictions.setdefault(dev, set()).update(
            JURISDICTION_NAMES[c]
            for c, p in scores.items()
            if p >= JURISDICTION_THRESHOLD and c in JURISDICTION_NAMES
        )
    changes: dict[str, tuple[str, int]] = {}
    for r in conn.execute(
        "SELECT kind, ref_id, detail FROM run_changes WHERE run_id = ?",
        (run.id,),
    ):
        kind = "new" if r["kind"] == "new_development" else "updated"
        changes[r["ref_id"]] = (kind, len(json.loads(r["detail"])["new_documents"]))
    names = all_subjects()
    indirect: dict[str, list[str]] = {}
    for r in conn.execute(
        "SELECT development_id, subject_id FROM development_impacts WHERE run_id = ? "
        "ORDER BY development_id, level DESC, subject_id",
        (run.id,),
    ):
        reached = indirect.setdefault(r["development_id"], [])
        if r["subject_id"] not in reached and r["subject_id"] in names:
            reached.append(r["subject_id"])
    by_id: dict[str, DevelopmentSummary] = {}
    for r in conn.execute("SELECT * FROM developments WHERE run_id = ?", (run.id,)):
        change = changes.get(r["id"])
        mine = priorities.get(r["id"], {})
        level = int(r["materiality_level"])
        docs = documents.get(r["id"], {})
        per_type = Counter(c for c, _ in docs.values())
        backing = corroboration(docs.values())
        direct = [s for s in tags.get(r["id"], []) if s in names]
        by_id[r["id"]] = DevelopmentSummary(
            id=r["id"],
            title=r["title"],
            headline=r["headline"] or r["title"],
            points=json.loads(r["points"] or "[]"),
            category=r["category"],
            stage=r["stage"],
            event_date=r["event_date"],
            first_published=r["first_published"],
            last_published=r["last_published"],
            materiality_level=level,
            materiality_label=PRIORITY_LABELS[level],
            needs_attention=bool(r["needs_attention"]),
            priority=PRIORITY_LABELS[level],
            priority_for=[],
            adverse=False,
            direction="neutral",
            subjects=[subject_ref(s) for s in direct],
            actors=[s for s in actors.get(r["id"], []) if s in names],
            also_affects=[subject_ref(s) for s in indirect.get(r["id"], []) if s not in direct],
            source_count=len(docs),
            outlet_count=backing.outlets,
            unconfirmed=not backing.shown,
            source_types=[
                SourceTypeCount(collection=c, count=per_type[c])
                for c in COLLECTION_ORDER
                if per_type[c]
            ],
            collections=[c for c in COLLECTION_ORDER if per_type[c]],
            jurisdictions=sorted(jurisdictions.get(r["id"], ())),
            change="new" if change and change[0] == "new" else "updated" if change else None,
            new_source_count=change[1] if change else 0,
        )
        if mine:
            by_id[r["id"]] = by_id[r["id"]].model_copy(update=priority_fields(mine))
    ordered = sorted(by_id.values(), key=_display_key)
    return RunDevelopments(
        by_id={d.id: d for d in ordered if not d.unconfirmed},
        unconfirmed={d.id: d for d in ordered if d.unconfirmed},
        subject_ids={d.id: frozenset(tags.get(d.id, [])) for d in ordered},
        priorities=priorities,
    )


def _display_key(d: DevelopmentSummary) -> tuple[int, bool, int, str]:
    """Display order (pipeline/params.py `display_key`), with priority and adverse as the
    summary's scope sees them."""
    return display_key(d.materiality_level, d.adverse, d.last_published, d.id)


class Queries:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ---- runs

    @cached_property
    def _run_row(self) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT * FROM runs WHERE status = 'complete' ORDER BY as_of DESC LIMIT 1"
        ).fetchone()
        if row is None:
            raise NoRunError("no complete run: run `im run --both` first")
        return row

    @cached_property
    def run(self) -> RunRef:
        r = self._run_row
        return RunRef(id=r["id"], as_of=r["as_of"], previous_run_id=r["previous_run_id"])

    # ---- developments

    @cached_property
    def devs(self) -> RunDevelopments:
        key = (self.run.id, str(self._run_row["finished_at"]))
        if key not in _run_cache:
            _run_cache.clear()
            _run_cache[key] = load_developments(self.conn, self.run)
        return _run_cache[key]

    def development(self, development_id: str) -> DevelopmentSummary:
        """A development by id, unconfirmed ones included, so a listed one always opens."""
        found = self.devs.by_id.get(development_id) or self.devs.unconfirmed.get(development_id)
        if found is None:
            raise NotFoundError(f"development {development_id} in run {self.run.id}")
        return found

    def developments_in(
        self, subject_ids: Iterable[str], unconfirmed: bool = False
    ) -> list[DevelopmentSummary]:
        """Developments tagged to any of the subjects or, for groups, their members, in display
        order with their priority fields over those subjects. Unconfirmed ones only when asked."""
        ids = expand(subject_ids)
        scoped = [
            self.devs.scoped(d, ids)
            for d in self.devs.listed(unconfirmed)
            if self.devs.subject_ids[d.id] & ids
        ]
        return sorted(scoped, key=_display_key)

    def subject_summary(self, subject_id: str) -> SubjectSummary:
        """The subject as the catalogue lists it; a group's members are its displayed ones."""
        s = subject(subject_id)
        subjects = all_subjects()
        return SubjectSummary(
            **subject_ref(subject_id).model_dump(),
            aliases=aliases(subject_id),
            members=[m for m in s.members if subjects[m].displayed],
            function=function_ref(s.function) if s.function else None,
        )

    def subject_detail(self, subject_id: str) -> SubjectDetail:
        summary = self.subject_summary(subject_id)
        return SubjectDetail(
            **summary.model_dump(),
            description=description(subject_id),
            domains=domains(subject_id),
            links=subject_links(subject_id),
        )

    # ---- upcoming dates and briefs

    def upcoming(self, subject_ids: Iterable[str]) -> list[UpcomingItem]:
        in_scope = {d.id: d for d in self.developments_in(subject_ids)}
        horizon = self.run.as_of + timedelta(days=UPCOMING_HORIZON_DAYS)
        items: dict[tuple[str, str], UpcomingItem] = {}
        for r in self.conn.execute(
            "SELECT date, label, development_id FROM upcoming_dates WHERE run_id = ? "
            "AND date >= ? AND date <= ? ORDER BY date, label",
            (self.run.id, self.run.as_of.isoformat(), horizon.isoformat()),
        ):
            dev = in_scope.get(r["development_id"])
            if dev is None or (dev.id, r["date"]) in items:
                continue
            items[(dev.id, r["date"])] = UpcomingItem(
                date=r["date"],
                label=r["label"],
                development_id=dev.id,
                development_title=dev.title,
                subjects=dev.subjects,
            )
        return list(items.values())

    def _summary_row(self, subject_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM run_summaries WHERE run_id = ? AND subject_id = ?",
            (self.run.id, subject_id),
        ).fetchone()

    def brief(self, subject_id: str) -> EntityBrief | None:
        """The run's brief of the subject; None for a row written before briefs. A concern's
        ids no longer shown in the run go."""
        row = self._summary_row(subject_id)
        if row is None or (concerns := stored_concerns(row)) is None:
            return None
        return EntityBrief(
            state=row["text"],
            # A flagged direction shows as unchanged, so the model's reason for it goes too.
            direction="unchanged" if row["direction_flagged"] else row["direction"],
            direction_reason="" if row["direction_flagged"] else row["direction_reason"],
            direction_flagged=bool(row["direction_flagged"]),
            concerns=[
                BriefConcern(
                    label=c.label or None,
                    text=c.text,
                    development_ids=[i for i in c.development_ids if i in self.devs.by_id],
                )
                for c in concerns
            ],
            coming_up=json.loads(row["coming_up"]),
            developments=row["developments_in_scope"],
            high=row["needs_attention"],
            adverse=row["adverse"],
            latest_news=row["latest_news"],
            model=row["model"],
        )


def expand(subject_ids: Iterable[str]) -> set[str]:
    """The subjects plus, for groups, their members."""
    return {i for sid in subject_ids for i in scope(subject(sid))}
