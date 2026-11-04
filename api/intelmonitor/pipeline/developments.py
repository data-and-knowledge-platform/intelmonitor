"""Steps 5 and 6: findings that report the same event are merged into Developments, and Jev scores
each development's priority for each of its subjects, except where a detector's rule sets it
(pipeline/detectors.py). Developments are rebuilt per run from the findings in documents
published on or before the run date; a development's id is its earliest finding's id, so the same
event keeps its id from one run to the next. Its title is its lead finding's statement, or a
detection's sentence when a detector measured the event.

A development's subjects are those its findings say it happened to or that did it, less the tags
the analyst removed from it; they are topics or entities, never groups. It is adverse to a subject
when one of those findings says so. Subjects that only comment on it or are mentioned in it are
kept apart, as mentions, and are never scored. Its level (materiality_level, 0 Low to 2 High) is
its highest priority over its subjects."""

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
from typesafe_sdk import Noul, Score

from intelmonitor.config import DATA_DIR
from intelmonitor.pipeline.detectors import materiality_level
from intelmonitor.pipeline.extract import TONE_ORDER, FindingRole, Tone
from intelmonitor.pipeline.feedback import removed_tags
from intelmonitor.pipeline.inference import Inference, StepFailed, noul, score
from intelmonitor.pipeline.params import (
    EMBEDDING_MODEL,
    JURISDICTION_THRESHOLD,
    MARKET_MAX_LEVEL,
    MERGE_MAX_NEIGHBOURS,
    MERGE_MIN_SIMILARITY,
    MERGE_WINDOW_DAYS,
    NEEDS_ATTENTION_LEVEL,
    PRIORITY_LABELS,
    PRIORITY_RUBRIC,
    PRIORITY_SURE,
    PROMOTIONAL_MAX_LEVEL,
    SAME_EVENT_THRESHOLD,
    Priority,
)
from intelmonitor.pipeline.singapore import NO_LINK, singapore_links
from intelmonitor.pipeline.subjects import Subject, monitored_subjects, resolve_groups
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
    first_quote: str
    published: date
    collection: str
    domain: str
    title: str
    stage: str | None
    rule_level: int | None  # a detection's priority level, set by its detector
    rule_id: str | None  # that detector
    singapore: bool  # its document reports on Singapore

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
        "SELECT f.*, d.published_at, d.collection, d.domain, d.title, t.stage, t.jurisdictions, "
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
            first_quote=json.loads(r["quotes"])[0]["text"],
            published=date.fromisoformat(r["published_at"][:10]),
            collection=r["collection"],
            domain=r["domain"],
            title=r["title"],
            stage=r["stage"],
            rule_level=materiality_level(detectors[r["detector_id"]]) if r["detector_id"] else None,
            rule_id=r["detector_id"],
            singapore=json.loads(r["jurisdictions"] or "{}").get("sg", 0.0)
            >= JURISDICTION_THRESHOLD,
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


SAME_EVENT = Noul(
    instructions=(
        "Do finding A and finding B report the same real-world event or development (the same "
        "announcement, decision, action or incident, possibly described differently), rather than "
        "two separate events on a similar topic?"
    )
)


def _describe(label: str, f: RunFinding) -> str:
    when = f"event date {f.event_date}" if f.event_date else "event date not given"
    return (
        f"Finding {label} (published {f.published} by {f.domain}, {when}):\n{f.statement}\n"
        f'Quote: "{f.first_quote}"'
    )


async def same_event(inf: Inference, a: RunFinding, b: RunFinding) -> float:
    state = f"{_describe('A', a)}\n\n{_describe('B', b)}"
    answers = await inf.jev("same_event", f"pair:{a.id}|{b.id}", state, {"same_event": SAME_EVENT})
    return noul(answers, "same_event")


Mention = Literal["commentator", "mentioned"]


@dataclass
class DevelopmentSubject:
    """A subject the development happened to or that did it, as its findings describe it, with
    the development's priority for that subject."""

    role: Literal["affected", "actor"]  # affected when any finding says so
    adverse: bool  # a finding reports the cause of harm to it
    tone: Tone  # its findings' most common tone
    confidence: float  # the screening tag's confidence
    source: str  # who tagged it: jev, claude or detector
    priority_level: int = 0
    priority_score: float = 0.0
    priority_confidence: float = 0.0
    # The detector whose rule set the level: every source is a detection, or the rule measured
    # this subject and its level is at least Jev's. None when Jev's score set it.
    rule: str | None = None


@dataclass
class Development:
    id: str
    findings: list[RunFinding]
    subjects: dict[str, DevelopmentSubject] = field(default_factory=dict[str, DevelopmentSubject])
    mentions: dict[str, Mention] = field(default_factory=dict[str, Mention])

    @property
    def top(self) -> DevelopmentSubject | None:
        """The subject it matters most to, whose priority is the development's; a rule's level
        first on a tie, as a rule's level stands when Jev scores no higher. None without
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
    def promotional(self) -> bool:
        """Most of its findings are marketing or routine product news."""
        return 2 * sum(f.promotional for f in self.findings) > len(self.findings)

    @property
    def market_move(self) -> bool:
        """Most of its findings report market moves: prices, indices or flows."""
        return 2 * sum(f.category == "market" for f in self.findings) > len(self.findings)

    @property
    def singapore(self) -> bool:
        """A Singapore financial institution did it or was affected, or a source reports on
        Singapore."""
        links = singapore_links()
        return any(f.singapore for f in self.findings) or any(
            links.get(s, NO_LINK).kind == "singapore_fi" for s in self.subjects
        )

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


PRIORITY_CRITERIA = [
    f"{name}: {desc}" for name, desc in zip(PRIORITY_LABELS, PRIORITY_RUBRIC, strict=True)
]
PRIORITY_GUIDE = (
    "Decide two things. Where it happened: at a financial institution in Singapore, at a foreign "
    "institution or third party strongly linked to Singapore, or neither; the institutions "
    "listed with the development say how each links to Singapore, and an institution not listed "
    "is judged from the text. A loan exposure alone is not a strong link. Then whether it is an "
    "incident (something went wrong: an outage, attack, breach, fraud, penalty, loss or failure) "
    "or a development. Share price, index and market moves are never High."
)


def priority_question(subject: Subject, link: DevelopmentSubject) -> Score:
    """The development's priority for one subject: how directly it concerns the subject, where it
    happened relative to Singapore's financial institutions, and whether it is an incident."""
    name = subject.name
    if subject.type == "topic":
        how = f"it is a development within {name}"
    elif link.role == "affected":
        how = f"it happened to {name} or directly changes its business"
    else:
        how = f"{name} did it"
    return Score(
        instructions=(
            "How important is this development for a supervisor at the Monetary Authority of "
            f"Singapore who monitors {subject.description}? Rate it for {name} alone, and only "
            f"as far as it concerns {name} ({how}). {PRIORITY_GUIDE} "
            f"Something adverse {'did' if link.adverse else 'did not'} happen to {name}."
        ),
        criteria=PRIORITY_CRITERIA,
    )


def priority_state(dev: Development) -> str:
    """What priority is scored from: the lead statement, the category, how each institution it
    concerns links to Singapore (pipeline/singapore.py), the number of sources and the top
    sources."""
    names = monitored_subjects()
    links = singapore_links()
    institutions = "\n".join(
        f"- {names[s].name}: {links[s].describe()}" for s in dev.subjects if s in links
    )
    sources = "\n".join(
        f'- {f.published} {f.domain}: {f.title}\n  "{f.first_quote}"'
        for f in sorted(dev.findings, key=lambda f: f.rank)[:6]
    )
    return (
        f"Development: {dev.lead.statement}\nCategory: {dev.category}\n"
        f"Institutions:\n{institutions or 'none listed'}\n"
        f"Sources ({len(dev.document_ids)}):\n{sources}"
    )


class SubjectPriority(BaseModel):
    subject_id: str
    priority: Priority


class PriorityDecision(BaseModel):
    """Claude's priority for the subjects Jev was unsure about."""

    priorities: list[SubjectPriority]


PRIORITY_SYSTEM = (
    "You rate news developments for supervisors at the Monetary Authority of Singapore. Rate each "
    "listed subject High, Medium or Low against the rubric, for that subject alone."
)


def priority_prompt(dev: Development, unsure: list[str]) -> str:
    names = monitored_subjects()
    rubric = "\n".join(f"- {c}" for c in reversed(PRIORITY_CRITERIA))
    subjects = "\n".join(
        f"- {sid}: {names[sid].name}, {names[sid].description}; "
        f"{dev.subjects[sid].role}{', adverse to it' if dev.subjects[sid].adverse else ''}"
        for sid in unsure
    )
    return (
        f"{priority_state(dev)}\n\nRubric:\n{rubric}\n\n{PRIORITY_GUIDE}\n\n"
        f"Rate each of these subjects, by id:\n{subjects}"
    )


async def score_priority(inf: Inference, dev: Development) -> None:
    """Jev scores the development for each of its subjects in one call, a question per subject;
    Claude decides the subjects whose top label Jev gives less than PRIORITY_SURE."""
    names = monitored_subjects()
    answers = await inf.jev(
        "priority",
        f"dev:{dev.id}:{len(dev.findings)}",
        priority_state(dev),
        {sid: priority_question(names[sid], s) for sid, s in dev.subjects.items()},
    )
    unsure: list[str] = []
    for sid, s in dev.subjects.items():
        a = score(answers, sid)
        # Levels come back indexed 0 to 2 in the order sent; the most likely one is the level.
        s.priority_level = max(a.probabilities, key=lambda k: a.probabilities[k])
        s.priority_score = a.score
        s.priority_confidence = a.probabilities[s.priority_level]
        if s.priority_confidence < PRIORITY_SURE:
            unsure.append(sid)
    if not unsure:
        return
    decision = await inf.claude(
        "priority_check",
        f"dev:{dev.id}:{len(dev.findings)}",
        priority_prompt(dev, unsure),
        PriorityDecision,
        PRIORITY_SYSTEM,
    )
    for p in decision.priorities:
        if p.subject_id in unsure:
            dev.subjects[p.subject_id].priority_level = PRIORITY_LABELS.index(p.priority)


async def rate(inf: Inference, dev: Development) -> None:
    """The development's priority for each subject. A development made only of detections takes
    its rule's level; otherwise Jev scores it (Claude where Jev is unsure), promotional news is
    Low, market moves are at most Medium and Low without Singapore,
    and a merged detection's rule raises the level for the subjects it measured when the score
    is lower."""
    rule = dev.detection
    level = rule.rule_level if rule else None
    if rule and level is not None and all(f.rule_level is not None for f in dev.findings):
        for s in dev.subjects.values():
            s.priority_level, s.priority_score, s.priority_confidence = level, float(level), 1.0
            s.rule = rule.rule_id
        return
    if dev.subjects:
        await score_priority(inf, dev)
    market_cap = MARKET_MAX_LEVEL if dev.singapore else 0
    for sid, s in dev.subjects.items():
        for capped, cap in (
            (dev.promotional, PROMOTIONAL_MAX_LEVEL),
            (dev.market_move, market_cap),
        ):
            if capped:
                s.priority_level = min(s.priority_level, cap)
                s.priority_score = min(s.priority_score, float(cap))
        if rule is None or level is None or level < s.priority_level or sid not in rule.subjects:
            continue
        s.rule = rule.rule_id
        if level > s.priority_level:
            s.priority_level = level
            s.priority_score = max(s.priority_score, float(level))


@dataclass
class MergeStats:
    findings: int = 0
    pairs: int = 0
    merged_pairs: int = 0
    developments: int = 0
    high: int = 0  # developments needing attention: High for at least one subject


async def merge(inf: Inference, findings: list[RunFinding]) -> tuple[list[Development], MergeStats]:
    """Developments from the findings, merging the pairs Jev says report the same event."""
    pairs = candidate_pairs(findings)
    failures: list[str] = []

    async def decide(a: RunFinding, b: RunFinding) -> bool:
        try:
            return await same_event(inf, a, b) >= SAME_EVENT_THRESHOLD
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
    devs, stats = await merge(inf, load_findings(conn, as_of))
    link_subjects(conn, devs)
    await score_priorities(inf, devs)
    store_developments(conn, run_id, devs)
    stats.high = sum(d.materiality_level >= NEEDS_ATTENTION_LEVEL for d in devs)
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
            "source, role, adverse, tone, priority_level, priority_score, priority_confidence) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
