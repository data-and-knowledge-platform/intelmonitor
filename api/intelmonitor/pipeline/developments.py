"""Steps 5 and 6: findings that report the same event are merged into Developments, and Claude rates
each development's priority for each of its entities, except where a detector's rule sets it
(pipeline/detectors.py). Promotional and price-move findings never join a development.
Developments are rebuilt per run from the findings in documents published on or before the run
date; a development's id is its earliest finding's id, so the same event keeps its id from one run
to the next. Its title is its lead finding's statement, or a detection's sentence when a detector
measured the event.

A development's candidate subjects are those its findings say it happened to or that did it; they
are topics or entities, never groups. Before priority, the headline call (pipeline/headlines.py)
writes its headline and points and keeps the candidates the development is about; the others
become mentions. It is adverse to a subject when one of the findings that tag it says so. Subjects
that only comment on it or are mentioned in it are kept apart, as mentions, and are never rated. A
topic takes the highest priority among the development's entities, and is rated itself only where
the development names no entity. Its level (materiality_level, 0 Low to 2 High) is its highest
priority over its subjects.

After priority, one more Sonnet call per development with an entity at Medium or High (the
link_types stage, deck of 16 Nov 2026, pages 9 and 10) picks for each such entity the relationship
types the development activates, which the inferred impact step follows. With INDIRECT_FROM_LOW
on, entities at Low are asked too, so a Low entity can pass its Low row. With
INDIRECT_ADVERSE_ONLY on, a development adverse for none of those entities is not asked, since
none of them could pass anything."""

import asyncio
import atexit
import json
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from typing import Literal

import numpy as np
from claude_agent_sdk import EffortLevel
from fastembed import TextEmbedding
from numpy.typing import NDArray
from pydantic import BaseModel, Field, TypeAdapter

from intelmonitor.config import MODELS_DIR
from intelmonitor.pipeline.detectors import materiality_level
from intelmonitor.pipeline.extract import TONE_ORDER, FindingRole, Tone
from intelmonitor.pipeline.headlines import (
    HEADLINE_EFFORT,
    HEADLINE_INSTRUCTIONS,
    HEADLINE_SYSTEM,
    Candidate,
    Headline,
    detection_headline,
    headline_prompt,
)
from intelmonitor.pipeline.impact import passes_adverse_rule
from intelmonitor.pipeline.inference import ClaudeRefused, Inference, StepFailed
from intelmonitor.pipeline.params import (
    EMBEDDING_MODEL,
    HAIKU_MODEL,
    INDIRECT_ADVERSE_ONLY,
    INDIRECT_FROM_LOW,
    LINK_TYPES_EFFORT,
    LINK_TYPES_PROMPT,
    MATERIAL_LEVEL,
    MERGE_MAX_NEIGHBOURS,
    MERGE_MIN_SIMILARITY,
    MERGE_WINDOW_DAYS,
    NEEDS_ATTENTION_LEVEL,
    PRIORITY_EFFORT,
    PRIORITY_LABELS,
    PRIORITY_PROMPT,
    Priority,
    priority_system,
)
from intelmonitor.pipeline.subjects import (
    PrioritySubject,
    Subject,
    monitored_subjects,
    priority_subjects,
    resolve_groups,
)
from intelmonitor.relationships import RelType
from intelmonitor.seed import SubjectType, load_detectors
from intelmonitor.store import search

# Primary sources first: a measured value ranks after official text and before news reporting it.
COLLECTION_RANK = {"dms": 0, "official": 1, "data": 2, "news": 3}
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
        return (COLLECTION_RANK.get(self.collection, 4), self.published, self.id)


def _detection_roles(subjects: list[str], tone: Tone) -> dict[str, FindingRole]:
    """A detection measured its series' subjects (a group's members for a group): they are
    affected, with the move's tone. A measured move is not something that happened at the
    subject, so it is never adverse."""
    return {s: FindingRole(role="affected", adverse=False, tone=tone) for s in subjects}


def load_findings(
    conn: sqlite3.Connection, as_of: date, start: date | None = None
) -> list[RunFinding]:
    """Findings in documents published by the date, and on or after `start` when given:
    screened documents' findings with their roles, and detections with their rule's level. A
    detection whose detector has left the seed is left out; one stored tagged to a group counts
    for the group's members."""
    detectors = {d.id: d for d in load_detectors().detectors}
    rows = conn.execute(
        "SELECT f.*, d.published_at, d.collection, d.domain, d.title, t.stage, "
        "k.detector_id, "
        "k.direction AS detection_tone FROM findings f "
        "JOIN source_documents d ON d.id = f.document_id "
        "LEFT JOIN document_tags t ON t.document_id = f.document_id "
        "LEFT JOIN detections k ON k.document_id = f.document_id "
        "WHERE substr(d.published_at, 1, 10) BETWEEN ? AND ? "
        "AND (t.document_id IS NOT NULL OR k.document_id IS NOT NULL) "
        "ORDER BY d.published_at, f.id",
        ((start or date.min).isoformat(), as_of.isoformat()),
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
    return TextEmbedding(EMBEDDING_MODEL, cache_dir=str(MODELS_DIR))


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
    # The relationship types the development activates for this entity (`activate_link_types`);
    # None when it was not asked: a topic, an entity at Low with INDIRECT_FROM_LOW off, an entity
    # of a development adverse for none of the entities the stage would ask about with
    # INDIRECT_ADVERSE_ONLY on, or before the stage ran.
    link_types: list[RelType] | None = None

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
    # For display, from the headline call; None when it was refused, and the app shows the title.
    headline: str | None = None
    points: list[str] | None = None

    def keep_subjects(self, ids: Iterable[str]) -> list[str]:
        """Keeps the subjects in `ids`, and those a detection measured, and makes the rest
        mentions; keeps every subject when `ids` names none of them. The subjects made mentions."""
        keep = set(ids) & self.subjects.keys()
        if not keep:
            return []
        keep |= {s for f in self.findings if f.rule_level is not None for s in f.subjects}
        dropped = [sid for sid in self.subjects if sid not in keep]
        for sid in dropped:
            del self.subjects[sid]
            self.mentions.setdefault(sid, "mentioned")
        return dropped

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
        the most material rule's first, so the feed and search state the number."""
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
    says was affected or acted is a subject even where another only mentions it. Groups and subjects
    no longer in the seed are left out."""
    names = monitored_subjects()
    tags = _subject_confidence(conn, list({f.document_id for d in devs for f in d.findings}))
    for dev in devs:
        direct: dict[str, list[tuple[RunFinding, FindingRole]]] = {}
        for f in dev.findings:
            for sid, r in f.roles.items():
                if sid not in names:
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


def candidates(dev: Development, names: Mapping[str, Subject]) -> list[Candidate]:
    """The development's subjects as the headline call lists them, most findings first."""
    counts = Counter(
        sid
        for f in dev.findings
        for sid, r in f.roles.items()
        if sid in dev.subjects and r.role in ("actor", "affected")
    )
    return [
        Candidate(sid, names[sid].description, counts[sid])
        for sid in sorted(dev.subjects, key=lambda s: -counts[s])
    ]


async def write_headline(inf: Inference, dev: Development, article: str) -> Headline | None:
    """One Sonnet call: the development's headline, points and the subjects it is about, from its
    findings, its candidates and `article`, the lead source's text. None when Claude refuses."""
    try:
        return await inf.claude(
            "headline",
            f"dev:{dev.id}:{len(dev.findings)}",
            headline_prompt(dev.findings, article, candidates(dev, monitored_subjects())),
            Headline,
            HEADLINE_SYSTEM,
            effort=HEADLINE_EFFORT,
            template=f"{HEADLINE_SYSTEM}\n\n{HEADLINE_INSTRUCTIONS}",
        )
    except ClaudeRefused:
        return None


@dataclass
class HeadlineStats:
    written: int = 0
    from_code: int = 0
    refused: int = 0
    narrowed: int = 0  # developments that lost a subject to the call
    dropped: int = 0  # subjects made mentions


async def write_headlines(
    inf: Inference, conn: sqlite3.Connection, devs: list[Development]
) -> HeadlineStats:
    """The headline stage, after `link_subjects` and before priority: each development's headline
    and points, and its subjects narrowed to those the call keeps. A development made only of
    detections takes them from code and keeps its subjects. A refused call leaves the headline
    unset and every subject in place."""
    stats = HeadlineStats()
    failures: list[str] = []

    async def one(dev: Development) -> None:
        if all(f.rule_level is not None for f in dev.findings):
            stats.from_code += 1
            dev.headline, dev.points = detection_headline(dev.lead.statement)
            return
        row = conn.execute(
            "SELECT text FROM source_documents WHERE id = ?", (dev.lead.document_id,)
        ).fetchone()
        try:
            h = await write_headline(inf, dev, row["text"] if row else "")
        except Exception as error:
            failures.append(f"{dev.id}: {type(error).__name__}: {error}")
            return
        if h is None:
            stats.refused += 1
            return
        stats.written += 1
        dev.headline, dev.points = h.headline.strip(), [p.strip() for p in h.points]
        dropped = dev.keep_subjects(h.subjects)
        stats.narrowed += bool(dropped)
        stats.dropped += len(dropped)

    await asyncio.gather(*(one(d) for d in devs))
    if failures:
        raise StepFailed("headline", failures)
    return stats


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


async def rate_subject(
    inf: Inference,
    dev: Development,
    subject: PrioritySubject,
    *,
    template: str = PRIORITY_PROMPT,
    schema: type[PriorityRating] = PriorityRating,
    effort: EffortLevel = PRIORITY_EFFORT,
) -> PriorityRating:
    """One Sonnet call: the development's priority for one subject, by slide 7's prompt with the
    subject's name and Singapore phrase; its prompt version is of the unfilled PRIORITY_PROMPT. A
    refusal leaves the subject at Low with PRIORITY_DECLINED as its reason, so one declined pair
    does not fail the run. `im trial` passes a candidate's template, schema and effort instead,
    and the subject as its store's frozen seed gives it."""
    try:
        return await inf.claude(
            "priority",
            f"dev:{dev.id}:{len(dev.findings)}:{subject.id}",
            priority_prompt(dev),
            schema,
            priority_system(
                subject.name, subject.singapore, template, subject.anchor, subject.local_anchor
            ),
            effort=effort,
            template=template,
        )
    except ClaudeRefused:
        return PriorityRating(priority="Low", reason=PRIORITY_DECLINED)


def called_subjects(
    dev: Development, subject_ids: Iterable[str], types: Mapping[str, SubjectType]
) -> list[str]:
    """The development's subjects that `rate` asks Claude about: none when it is made only of
    detections, whose rule sets the level; else its entities, or its topics when it has none."""
    if dev.findings and all(f.rule_level is not None for f in dev.findings):
        return []
    known = [sid for sid in subject_ids if sid in types]
    entities = [sid for sid in known if types[sid] == "entity"]
    return entities or [sid for sid in known if types[sid] == "topic"]


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
    rated = called_subjects(dev, dev.subjects, {i: s.type for i, s in names.items()})
    entities = [sid for sid in dev.subjects if names[sid].type == "entity"]
    topics = [sid for sid in dev.subjects if names[sid].type == "topic"]
    subjects = priority_subjects()
    ratings = await asyncio.gather(*(rate_subject(inf, dev, subjects[sid]) for sid in rated))
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


@dataclass(frozen=True)
class DirectEntity:
    """A direct entity as the link_types call reads it: its id and name, and its priority level
    and reason."""

    id: str
    name: str
    level: int  # 1 Medium or 2 High; 0 Low only with INDIRECT_FROM_LOW
    reason: str


class EntityLinkTypes(BaseModel):
    entity: str = Field(description="The entity's id, as the message lists it.")
    types: list[RelType] = Field(
        description="The relationship types the finding could activate for this entity; empty "
        "when it activates none."
    )


# The link_types call's answer. (No docstrings: pydantic would send them to the model as the
# schema's descriptions.)
class LinkTypeSelection(BaseModel):
    entities: list[EntityLinkTypes] = Field(
        description="One entry for each entity the message lists."
    )


def link_types_prompt(dev: Development, entities: Sequence[DirectEntity]) -> str:
    """What the link_types call reads: the development as the priority call reads it, then each
    entity with its level and priority reason. No links."""
    listed = "\n".join(
        f"- {e.id} ({e.name}), {PRIORITY_LABELS[e.level]}: {e.reason}" for e in entities
    )
    return f"{priority_prompt(dev)}\n\nEntities:\n{listed}"


def link_types_entities(
    dev: Development,
    names: Mapping[str, Subject],
    *,
    from_low: bool = INDIRECT_FROM_LOW,
    adverse_only: bool = INDIRECT_ADVERSE_ONLY,
) -> list[DirectEntity]:
    """The development's entities the link_types call asks about: those at Medium or High, and
    those at Low too with `from_low`, by id; none when the adverse rule (`adverse_only`,
    impact.passes_adverse_rule) lets none of them pass, since none could pass anything. When any
    passes, every one is asked, adverse or not, so the call's input does not depend on the
    rule. Topics are not asked."""
    lowest = 0 if from_low else MATERIAL_LEVEL
    listed = [
        (sid, s)
        for sid, s in sorted(dev.subjects.items())
        if names[sid].type == "entity" and s.priority_level >= lowest
    ]
    if not any(passes_adverse_rule(s.adverse, adverse_only=adverse_only) for _, s in listed):
        return []
    return [
        DirectEntity(sid, names[sid].name, s.priority_level, s.priority_reason) for sid, s in listed
    ]


async def select_link_types(
    inf: Inference,
    dev: Development,
    entities: Sequence[DirectEntity],
    *,
    template: str = LINK_TYPES_PROMPT,
    schema: type[LinkTypeSelection] = LinkTypeSelection,
    effort: EffortLevel | None = LINK_TYPES_EFFORT,
) -> dict[str, list[RelType]] | None:
    """One Sonnet call: the relationship types the development activates for each of the
    entities, by LINK_TYPES_PROMPT (`entity_types`). None when Claude declines. `im trial
    link-types` passes a candidate's template, schema and effort instead; the eval's cases pass
    an effort."""
    try:
        reply = await inf.claude(
            "link_types",
            f"dev:{dev.id}:{len(dev.findings)}",
            link_types_prompt(dev, entities),
            schema,
            template,
            effort=effort or LINK_TYPES_EFFORT,
        )
    except ClaudeRefused:
        return None
    return entity_types(reply, entities)


def entity_types(
    reply: LinkTypeSelection, entities: Sequence[DirectEntity]
) -> dict[str, list[RelType]]:
    """Each entity's types from a link_types reply, each type once; an entity the reply leaves
    out gets none, and an entity it adds is dropped."""
    picked: dict[str, list[RelType]] = {}
    for answer in reply.entities:
        picked.setdefault(answer.entity, []).extend(answer.types)
    return {e.id: list(dict.fromkeys(picked.get(e.id, []))) for e in entities}


async def activate_link_types(
    inf: Inference,
    devs: list[Development],
    *,
    from_low: bool = INDIRECT_FROM_LOW,
    adverse_only: bool = INDIRECT_ADVERSE_ONLY,
) -> tuple[int, int]:
    """The link_types stage, after priority: one call per development with an entity at Medium or
    High, which sets each such entity's `link_types`. With `from_low` (INDIRECT_FROM_LOW), entities
    at Low are asked as well, so a development whose entities are all Low is asked too; without
    it, they are not asked and keep None. With `adverse_only` (INDIRECT_ADVERSE_ONLY), a
    development adverse for none of the entities it would ask about is not asked and they keep
    None (link_types_entities). A refused call leaves the entities asked with none. Topics and
    developments that name no entity are not asked. The developments asked and the calls
    refused."""
    names = monitored_subjects()
    failures: list[str] = []
    asked = [
        (d, e)
        for d in devs
        if (e := link_types_entities(d, names, from_low=from_low, adverse_only=adverse_only))
    ]
    refused = 0

    async def one(dev: Development, entities: list[DirectEntity]) -> None:
        nonlocal refused
        try:
            picked = await select_link_types(inf, dev, entities)
        except Exception as error:
            failures.append(f"{dev.id}: {type(error).__name__}: {error}")
            return
        refused += picked is None
        for e in entities:
            dev.subjects[e.id].link_types = [] if picked is None else picked[e.id]

    await asyncio.gather(*(one(d, e) for d, e in asked))
    if failures:
        raise StepFailed("link_types", failures)
    return len(asked), refused


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
    link_types_asked: int = 0  # developments the link_types call asked about
    link_types_refused: int = 0  # of those, the calls Claude declined
    headlines: HeadlineStats = field(default_factory=HeadlineStats)


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
    inf: Inference, conn: sqlite3.Connection, run_id: str, as_of: date, start: date | None = None
) -> tuple[list[Development], MergeStats]:
    """The run's developments from the findings published from `start` (when given) to `as_of`."""
    findings = load_findings(conn, as_of, start)
    devs, stats = await merge(inf, [f for f in findings if not (f.promotional or f.price_move)])
    stats.promotional = sum(f.promotional for f in findings)
    stats.price_move = sum(f.price_move for f in findings)
    link_subjects(conn, devs)
    stats.headlines = await write_headlines(inf, conn, devs)
    await score_priorities(inf, devs)
    stats.link_types_asked, stats.link_types_refused = await activate_link_types(inf, devs)
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
            "materiality_confidence, needs_attention, materiality_rule, headline, points) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                d.headline,
                None if d.points is None else json.dumps(d.points),
            ),
        )
        conn.executemany(
            "INSERT INTO development_subjects (run_id, development_id, subject_id, confidence, "
            "source, role, adverse, tone, priority_level, priority_score, priority_confidence, "
            "priority_reason, link_types) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    None if s.link_types is None else json.dumps(s.link_types),
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
