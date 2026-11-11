"""Steps 5 and 6: findings that report the same event are merged into Developments, and Claude rates
each development's priority for each of its entities, except where a detector's rule sets it
(pipeline/detectors.py). Promotional and price-move findings never join a development.
Developments are rebuilt per run from the findings in documents published on or before the run
date; a development's id is its earliest finding's id, so the same event keeps its id from one run
to the next. Its title is its lead finding's statement, or a detection's sentence when a detector
measured the event.

A development's subjects are those its findings say it happened to or that did it, less the tags
the analyst removed from it; they are topics or entities, never groups. It is adverse to a subject
when one of those findings says so. Subjects that only comment on it or are mentioned in it are
kept apart, as mentions, and are never rated. A topic takes the highest priority among the
development's entities, and is rated itself only where the development names no entity. Its level
(materiality_level, 0 Low to 2 High) is its highest priority over its subjects."""

import asyncio
import atexit
import json
import sqlite3
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from typing import Literal

import numpy as np
from fastembed import TextEmbedding
from numpy.typing import NDArray
from pydantic import BaseModel, TypeAdapter

from intelmonitor.config import DATA_DIR
from intelmonitor.pipeline.detectors import materiality_level
from intelmonitor.pipeline.extract import TONE_ORDER, FindingRole, Tone
from intelmonitor.pipeline.feedback import removed_tags
from intelmonitor.pipeline.inference import ClaudeRefused, Inference, StepFailed
from intelmonitor.pipeline.params import (
    EMBEDDING_MODEL,
    HAIKU_MODEL,
    MERGE_MAX_NEIGHBOURS,
    MERGE_MIN_SIMILARITY,
    MERGE_WINDOW_DAYS,
    NEEDS_ATTENTION_LEVEL,
    PRIORITY_EFFORT,
    PRIORITY_LABELS,
    Priority,
    priority_system,
)
from intelmonitor.pipeline.subjects import (
    Subject,
    monitored_subjects,
    resolve_groups,
    singapore_phrase,
)
from intelmonitor.seed import load_detectors
from intelmonitor.store import search

# Primary sources first: a measured value ranks after official text and before news reporting it.
_COLLECTION_RANK = {"dms": 0, "official": 1, "data": 2, "news": 3}
_ROLES = TypeAdapter(dict[str, FindingRole])


@dataclass(frozen=True)
class RunFinding:
    """A verified finding with what the merge needs from its document and tags."""

    id: str
    document_id: str
    statement: str
    category: str
    event_date: date | None
    subjects: tuple[str, ...]  # every subject it involves, whatever the role
    roles: Mapping[str, FindingRole]  # by subject
    promotional: bool
    price_move: bool  # it only reports a share price or index move
    first_quote: str
    published: date
    collection: str
    domain: str
    title: str
    stage: str | None
    rule_level: int | None  # a detection's priority level, set by its detector
    rule_id: str | None  # that detector

    @property
    def when(self) -> date:
        return self.event_date or self.published

    @property
    def rank(self) -> tuple[int, date, str]:
        """Primary sources first, then earliest."""
        return (_COLLECTION_RANK.get(self.collection, 4), self.published, self.id)


def _detection_roles(subjects: list[str], tone: Tone) -> dict[str, FindingRole]:
    """A detection measured its series' subjects (a group's members for a group): they are
    affected, with the move's tone. A measured move is not something that happened at the
    subject, so it is never adverse."""
    return {s: FindingRole(role="affected", adverse=False, tone=tone) for s in subjects}


def load_findings(conn: sqlite3.Connection, as_of: date) -> list[RunFinding]:
    """Findings in documents published by the date: screened documents' findings with their
    roles, and detections with their rule's level. A detection whose detector has left the
    seed is left out; one stored tagged to a group counts for the group's members."""
    detectors = {d.id: d for d in load_detectors().detectors}
    rows = conn.execute(
        "SELECT f.*, d.published_at, d.collection, d.domain, d.title, t.stage, "
        "k.detector_id, "
        "k.direction AS detection_tone FROM findings f "
        "JOIN source_documents d ON d.id = f.document_id "
        "LEFT JOIN document_tags t ON t.document_id = f.document_id "
        "LEFT JOIN detections k ON k.document_id = f.document_id "
        "WHERE substr(d.published_at, 1, 10) <= ? "
        "AND (t.document_id IS NOT NULL OR k.document_id IS NOT NULL) "
        "ORDER BY d.published_at, f.id",
        (as_of.isoformat(),),
    ).fetchall()
    return [
        RunFinding(
            id=r["id"],
            document_id=r["document_id"],
            statement=r["statement"],
            category=r["category"],
            event_date=date.fromisoformat(r["event_date"]) if r["event_date"] else None,
            subjects=tuple(resolve_groups(json.loads(r["subjects"])))
            if r["detector_id"]
            else tuple(json.loads(r["subjects"])),
            roles=_detection_roles(resolve_groups(json.loads(r["subjects"])), r["detection_tone"])
            if r["detector_id"]
            else _ROLES.validate_json(r["roles"]),
            promotional=bool(r["promotional"]),
            price_move=bool(r["price_move"]),
            first_quote=json.loads(r["quotes"])[0]["text"],
            published=date.fromisoformat(r["published_at"][:10]),
            collection=r["collection"],
            domain=r["domain"],
            title=r["title"],
            stage=r["stage"],
            rule_level=materiality_level(detectors[r["detector_id"]]) if r["detector_id"] else None,
            rule_id=r["detector_id"],
        )
        for r in rows
        if r["detector_id"] is None or r["detector_id"] in detectors
    ]


@lru_cache
def _embedder() -> TextEmbedding:
    return TextEmbedding(EMBEDDING_MODEL, cache_dir=str(DATA_DIR / "models"))


# Close the ONNX session before the interpreter tears down: left to finalization, it sometimes
# aborts the process on exit ("recursive_mutex lock failed") after a run has finished.
atexit.register(_embedder.cache_clear)


def embed(texts: list[str]) -> NDArray[np.float32]:
    """Unit-length embeddings, one row per text."""
    vectors = np.array(list(_embedder().embed(texts)), dtype=np.float32)
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def candidate_pairs(findings: list[RunFinding]) -> list[tuple[RunFinding, RunFinding]]:
    """Pairs from different documents that share a subject, fall within MERGE_WINDOW_DAYS and are
    similar, keeping each finding's MERGE_MAX_NEIGHBOURS most similar partners."""
    if len(findings) < 2:
        return []
    vectors = embed([f.statement for f in findings])
    sims = vectors @ vectors.T
    pairs: set[tuple[int, int]] = set()
    for i, a in enumerate(findings):
        near: list[tuple[float, int]] = []
        for j, b in enumerate(findings):
            if (
                j != i
                and a.document_id != b.document_id
                and sims[i, j] >= MERGE_MIN_SIMILARITY
                and abs((a.when - b.when).days) <= MERGE_WINDOW_DAYS
                and set(a.subjects) & set(b.subjects)
            ):
                near.append((float(sims[i, j]), j))
        for _, j in sorted(near, reverse=True)[:MERGE_MAX_NEIGHBOURS]:
            pairs.add((min(i, j), max(i, j)))
    ordered = sorted(pairs, key=lambda p: (findings[p[0]].id, findings[p[1]].id))
    return [(findings[i], findings[j]) for i, j in ordered]


class SameEvent(BaseModel):
    same_event: bool


SAME_EVENT_SYSTEM = (
    "You compare two findings for a financial supervisor's monitoring dashboard. Decide whether "
    "finding A and finding B report the same real-world event or development (the same "
    "announcement, decision, action or incident, possibly described differently), rather than "
    "two separate events on a similar topic."
)


def _describe(label: str, f: RunFinding) -> str:
    when = f"event date {f.event_date}" if f.event_date else "event date not given"
    return (
        f"Finding {label} (published {f.published} by {f.domain}, {when}):\n{f.statement}\n"
        f'Quote: "{f.first_quote}"'
    )


async def same_event(inf: Inference, a: RunFinding, b: RunFinding) -> bool:
    """One Haiku call on the pair."""
    reply = await inf.claude(
        "same_event",
        f"pair:{a.id}|{b.id}",
        f"{_describe('A', a)}\n\n{_describe('B', b)}",
        SameEvent,
        SAME_EVENT_SYSTEM,
        model=HAIKU_MODEL,
    )
    return reply.same_event


Mention = Literal["commentator", "mentioned"]


@dataclass
class DevelopmentSubject:
    """A subject the development happened to or that did it, as its findings describe it, with
    the development's priority for that subject."""

    role: Literal["affected", "actor"]  # affected when any finding says so
    adverse: bool  # a finding reports the cause of harm to it
    tone: Tone  # its findings' most common tone
    confidence: float  # the screening tag's confidence
    source: str  # who tagged it: claude or detector
    priority_level: int = 0
    priority_score: float = 0.0  # the level, as a float
    priority_confidence: float = 0.0  # 1.0 once rated
    priority_reason: str = ""  # one sentence on why
    # The detector whose rule set the level: every source is a detection, or the rule measured
    # this subject and its level is at least the rated one. None when the rating set it.
    rule: str | None = None

    def set_priority(self, level: int, reason: str) -> None:
        self.priority_level, self.priority_score, self.priority_confidence = (
            level,
            float(level),
            1.0,
        )
        self.priority_reason = reason


@dataclass
class Development:
    id: str
    findings: list[RunFinding]
    subjects: dict[str, DevelopmentSubject] = field(default_factory=dict[str, DevelopmentSubject])
    mentions: dict[str, Mention] = field(default_factory=dict[str, Mention])

    @property
    def top(self) -> DevelopmentSubject | None:
        """The subject it matters most to, whose priority is the development's; a rule's level
        first on a tie, as a rule's level stands when the rating is no higher. None without
        subjects."""
        return max(
            self.subjects.values(),
            key=lambda s: (s.priority_level, s.rule is not None, s.priority_score),
            default=None,
        )

    @property
    def materiality_level(self) -> int:
        return self.top.priority_level if self.top else 0

    @property
    def lead(self) -> RunFinding:
        return min(self.findings, key=lambda f: f.rank)

    @property
    def title(self) -> str:
        """The lead's statement, unless a detector measured the event: then the measured sentence,
        the most material rule's first, so What changed, search and alerts state the number."""
        detection = self.detection
        return detection.statement if detection else self.lead.statement

    @property
    def detection(self) -> RunFinding | None:
        """The detection of the most material rule, primary and earliest first; None without."""
        detections = [f for f in self.findings if f.rule_level is not None]
        return min(detections, key=lambda f: (-(f.rule_level or 0), f.rank), default=None)

    @property
    def category(self) -> str:
        counts = Counter(f.category for f in self.findings)
        return max(counts, key=lambda c: (counts[c], c == self.lead.category))

    @property
    def stage(self) -> str | None:
        if self.category != "regulatory":
            return None
        stages = Counter(f.stage for f in self.findings if f.stage)
        return stages.most_common(1)[0][0] if stages else None

    @property
    def document_ids(self) -> list[str]:
        return list(dict.fromkeys(f.document_id for f in self.findings))


def cluster(
    findings: list[RunFinding], merges: list[tuple[RunFinding, RunFinding]]
) -> list[Development]:
    """Union the merged pairs; each cluster is one development, id'd by its earliest finding."""
    parent = {f.id: f.id for f in findings}

    def root(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in merges:
        parent[root(a.id)] = root(b.id)
    groups: dict[str, list[RunFinding]] = {}
    for f in findings:
        groups.setdefault(root(f.id), []).append(f)
    devs = [
        Development(id=min(g, key=lambda f: (f.published, f.id)).id, findings=g)
        for g in groups.values()
    ]
    return sorted(devs, key=lambda d: d.id)


def _subject_confidence(
    conn: sqlite3.Connection, doc_ids: list[str]
) -> dict[tuple[str, str], tuple[float, str]]:
    marks = ",".join("?" for _ in doc_ids)
    rows = conn.execute(
        f"SELECT document_id, subject_id, confidence, source FROM document_subjects "
        f"WHERE document_id IN ({marks})",
        doc_ids,
    ).fetchall()
    return {(r["document_id"], r["subject_id"]): (r["confidence"], r["source"]) for r in rows}


def link_subjects(conn: sqlite3.Connection, devs: list[Development]) -> None:
    """Each development's subjects and mentions from its findings' roles. A subject one finding
    says was affected or acted is a subject even where another only mentions it. Tags the analyst
    removed, groups and subjects no longer in the seed are left out."""
    names = monitored_subjects()
    tags = _subject_confidence(conn, list({f.document_id for d in devs for f in d.findings}))
    removed = removed_tags(conn)
    for dev in devs:
        direct: dict[str, list[tuple[RunFinding, FindingRole]]] = {}
        for f in dev.findings:
            for sid, r in f.roles.items():
                if sid not in names or (dev.id, sid) in removed:
                    continue
                if r.role == "commentator" or r.role == "mentioned":
                    if r.role == "commentator" or sid not in dev.mentions:
                        dev.mentions[sid] = r.role
                else:
                    direct.setdefault(sid, []).append((f, r))
        for sid, found in direct.items():
            tones = Counter[Tone](r.tone for _, r in found)
            confidence, source = max(
                (tags.get((f.document_id, sid), (0.0, "claude")) for f, _ in found),
                key=lambda t: t[0],
            )
            dev.subjects[sid] = DevelopmentSubject(
                role="affected" if any(r.role == "affected" for _, r in found) else "actor",
                adverse=any(r.adverse for _, r in found),
                tone=min(tones, key=lambda t: (-tones[t], TONE_ORDER.index(t))),
                confidence=confidence,
                source=source,
            )
            dev.mentions.pop(sid, None)


class PriorityRating(BaseModel):
    priority: Priority
    reason: str


# Sources the priority call reads, by rank.
PRIORITY_SOURCES = 6
# The reason a subject keeps when Claude declines to rate it; it stays at Low.
PRIORITY_DECLINED = "Claude declined to rate this development for this subject; left at Low."


def priority_prompt(dev: Development) -> str:
    """What priority is rated from: the lead statement and the top sources' titles and first
    quotes, one per document."""
    firsts = {f.document_id: f for f in sorted(dev.findings, key=lambda f: f.rank, reverse=True)}
    top = sorted(firsts.values(), key=lambda f: f.rank)[:PRIORITY_SOURCES]
    sources = "\n".join(f'- {f.title}\n  "{f.first_quote}"' for f in top)
    return f"Finding: {dev.lead.statement}\n\nSources:\n{sources}"


async def rate_subject(inf: Inference, dev: Development, subject: Subject) -> PriorityRating:
    """One Sonnet call: the development's priority for one subject, by slide 7's prompt with the
    subject's name and Singapore phrase. A refusal leaves the subject at Low with
    PRIORITY_DECLINED as its reason, so one declined pair does not fail the run."""
    try:
        return await inf.claude(
            "priority",
            f"dev:{dev.id}:{len(dev.findings)}:{subject.id}",
            priority_prompt(dev),
            PriorityRating,
            priority_system(subject.name, singapore_phrase(subject.id)),
            effort=PRIORITY_EFFORT,
        )
    except ClaudeRefused:
        return PriorityRating(priority="Low", reason=PRIORITY_DECLINED)


async def rate(inf: Inference, dev: Development) -> None:
    """The development's priority for each subject. A development made only of detections takes
    its rule's level. Otherwise Claude rates each entity; each topic takes the highest level among
    the entities, or is rated itself when there is none. A merged detection's rule raises the
    level for the subjects it measured when the rating is lower."""
    rule = dev.detection
    level = rule.rule_level if rule else None
    if rule and level is not None and all(f.rule_level is not None for f in dev.findings):
        for s in dev.subjects.values():
            s.set_priority(level, f"Set by the {rule.rule_id} detector's rule.")
            s.rule = rule.rule_id
        return
    names = monitored_subjects()
    entities = [sid for sid in dev.subjects if names[sid].type == "entity"]
    topics = [sid for sid in dev.subjects if names[sid].type == "topic"]
    rated = entities or topics
    ratings = await asyncio.gather(*(rate_subject(inf, dev, names[sid]) for sid in rated))
    for sid, r in zip(rated, ratings, strict=True):
        dev.subjects[sid].set_priority(PRIORITY_LABELS.index(r.priority), r.reason)

    def apply_rule(sids: list[str]) -> None:
        if rule is None or level is None:
            return
        for sid in sids:
            s = dev.subjects[sid]
            if sid not in rule.subjects or level < s.priority_level:
                continue
            s.rule = rule.rule_id
            if level > s.priority_level:
                s.set_priority(level, f"Raised by the {rule.rule_id} detector's rule.")

    apply_rule(rated)
    if entities:
        top = max(entities, key=lambda e: dev.subjects[e].priority_level)
        for sid in topics:
            dev.subjects[sid].set_priority(
                dev.subjects[top].priority_level,
                f"{names[top].name}: {dev.subjects[top].priority_reason}",
            )
        apply_rule(topics)


@dataclass
class MergeStats:
    findings: int = 0
    pairs: int = 0
    merged_pairs: int = 0
    developments: int = 0
    high: int = 0  # developments needing attention: High for at least one subject
    promotional: int = 0  # findings left out as promotional
    price_move: int = 0  # findings left out as price moves
    priority_refused: int = 0  # development and subject pairs Claude declined to rate


async def merge(inf: Inference, findings: list[RunFinding]) -> tuple[list[Development], MergeStats]:
    """Developments from the findings, merging the pairs Haiku says report the same event."""
    pairs = candidate_pairs(findings)
    failures: list[str] = []

    async def decide(a: RunFinding, b: RunFinding) -> bool:
        try:
            return await same_event(inf, a, b)
        except Exception as error:
            failures.append(f"{a.id}|{b.id}: {type(error).__name__}: {error}")
            return False

    decisions = await asyncio.gather(*(decide(a, b) for a, b in pairs))
    if failures:
        raise StepFailed("same_event", failures)
    merges = [p for p, same in zip(pairs, decisions, strict=True) if same]
    devs = cluster(findings, merges)
    return devs, MergeStats(len(findings), len(pairs), len(merges), len(devs))


async def score_priorities(inf: Inference, devs: list[Development]) -> None:
    failures: list[str] = []

    async def one(dev: Development) -> None:
        try:
            await rate(inf, dev)
        except Exception as error:
            failures.append(f"{dev.id}: {type(error).__name__}: {error}")

    await asyncio.gather(*(one(d) for d in devs))
    if failures:
        raise StepFailed("priority", failures)


async def build_developments(
    inf: Inference, conn: sqlite3.Connection, run_id: str, as_of: date
) -> tuple[list[Development], MergeStats]:
    findings = load_findings(conn, as_of)
    devs, stats = await merge(inf, [f for f in findings if not (f.promotional or f.price_move)])
    stats.promotional = sum(f.promotional for f in findings)
    stats.price_move = sum(f.price_move for f in findings)
    link_subjects(conn, devs)
    await score_priorities(inf, devs)
    store_developments(conn, run_id, devs)
    stats.high = sum(d.materiality_level >= NEEDS_ATTENTION_LEVEL for d in devs)
    stats.priority_refused = sum(
        s.priority_reason == PRIORITY_DECLINED for d in devs for s in d.subjects.values()
    )
    return devs, stats


def store_developments(conn: sqlite3.Connection, run_id: str, devs: list[Development]) -> None:
    tables = ("developments", "development_subjects", "development_mentions", "development_sources")
    for table in tables:
        conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
    for d in devs:
        published = [f.published for f in d.findings]
        events = [f.event_date for f in d.findings if f.event_date]
        top = d.top
        conn.execute(
            "INSERT INTO developments (run_id, id, title, category, stage, event_date, "
            "first_published, last_published, materiality_level, materiality_score, "
            "materiality_confidence, needs_attention, materiality_rule) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                d.id,
                d.title,
                d.category,
                d.stage,
                min(events).isoformat() if events else None,
                min(published).isoformat(),
                max(published).isoformat(),
                d.materiality_level,
                top.priority_score if top else 0.0,
                top.priority_confidence if top else 0.0,
                int(d.materiality_level >= NEEDS_ATTENTION_LEVEL),
                top.rule if top else None,
            ),
        )
        conn.executemany(
            "INSERT INTO development_subjects (run_id, development_id, subject_id, confidence, "
            "source, role, adverse, tone, priority_level, priority_score, priority_confidence, "
            "priority_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    run_id,
                    d.id,
                    sid,
                    s.confidence,
                    s.source,
                    s.role,
                    int(s.adverse),
                    s.tone,
                    s.priority_level,
                    s.priority_score,
                    s.priority_confidence,
                    s.priority_reason,
                )
                for sid, s in d.subjects.items()
            ],
        )
        conn.executemany(
            "INSERT INTO development_mentions (run_id, development_id, subject_id, role) "
            "VALUES (?, ?, ?, ?)",
            [(run_id, d.id, sid, role) for sid, role in d.mentions.items()],
        )
        conn.executemany(
            "INSERT INTO development_sources (run_id, development_id, finding_id, document_id) "
            "VALUES (?, ?, ?, ?)",
            [(run_id, d.id, f.id, f.document_id) for f in d.findings],
        )
    search.index_developments(conn, run_id)
    conn.commit()
