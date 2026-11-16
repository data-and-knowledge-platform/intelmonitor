"""`im trial priority` and `im prompts compare`: a priority prompt version tried on a store's
stored pairs, and two versions' labels side by side (ticket 08 of link-types-and-inferred-impact,
Design 6 and 7).

A pair is a development and a subject in a run whose development was new or updated in that run
(every development in a run with no previous run). The pairs a trial rates are those priority
version 1 rated by a call: entities, or the topics of a development that names no entity, and
never a pair a detector's rule set alone. A trial rebuilds each pair's input from the store, as
the run gave it to version 1 (`priority_prompt`), and rates it with the current code's priority
prompt through `Inference` with no run: calls are recorded with their prompt version, a version
already tried replays from the cache, and no `development_subjects` row is written.

A pair's input is its subject and the development's prompt in its run (`Inputs`); two pairs share
one when a call on one would replay the other's. The input_ref of a call does not identify it: a
development can keep its findings count while its findings change. So a version's label of a pair
is found as `Inference` finds a recorded call, by the pair's cache key under that version
(`Labels`), and a trial rates each input once.

Tried on the baseline (data/intelmonitor-baseline-89-entities-2026-11-16.db), whose developments
never change, so two versions differ only by prompt."""

import asyncio
import json
import random
import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from intelmonitor.eval.cases import PRIORITY_CASES, Outcome, check_priority, priority_pairs
from intelmonitor.pipeline.developments import (
    PRIORITY_DECLINED,
    Development,
    PriorityRating,
    load_findings,
    priority_prompt,
    rate_subject,
)
from intelmonitor.pipeline.inference import Inference, cache_key
from intelmonitor.pipeline.params import PRIORITY_LABELS, priority_system
from intelmonitor.pipeline.subjects import monitored_subjects, singapore_phrase

# The sample: this many pairs at each level under version 1, drawn with a fixed seed, the
# hand-set priority cases' pairs first.
SAMPLE_PER_LEVEL = 100
SAMPLE_SEED = 20261116


@dataclass(frozen=True)
class Pair:
    run_id: str  # the run whose new or updated development it is
    development_id: str
    subject_id: str


@dataclass(frozen=True)
class Label:
    level: int  # 0 Low, 1 Medium, 2 High
    reason: str


def complete_runs(conn: sqlite3.Connection) -> list[str]:
    """The complete runs' ids (their dates), oldest first."""
    query = "SELECT id FROM runs WHERE status = 'complete' ORDER BY id"
    return [r["id"] for r in conn.execute(query)]


def changed_developments(conn: sqlite3.Connection, run_id: str) -> set[str]:
    """The developments new or updated in the run; every one when it has no previous run."""
    previous = conn.execute("SELECT previous_run_id FROM runs WHERE id = ?", (run_id,)).fetchone()
    if previous is None or previous["previous_run_id"] is None:
        query = "SELECT id FROM developments WHERE run_id = ?"
    else:
        query = (
            "SELECT ref_id AS id FROM run_changes WHERE run_id = ? "
            "AND kind IN ('new_development', 'updated_development')"
        )
    return {r["id"] for r in conn.execute(query, (run_id,))}


def run_pairs(conn: sqlite3.Connection, run_id: str) -> list[Pair]:
    """Every subject of the developments new or updated in the run, as pairs."""
    changed = changed_developments(conn, run_id)
    rows = conn.execute(
        "SELECT development_id, subject_id FROM development_subjects WHERE run_id = ? "
        "ORDER BY development_id, subject_id",
        (run_id,),
    )
    return [
        Pair(run_id, r["development_id"], r["subject_id"])
        for r in rows
        if r["development_id"] in changed
    ]


class Inputs:
    """The priority call's input of any pair of the store, rebuilt as its run gave it: the
    development's findings in that run, as the latest complete run loads them."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        latest = date.fromisoformat(complete_runs(conn)[-1])
        self.findings = {f.id: f for f in load_findings(conn, latest)}
        self.subjects = monitored_subjects()
        self._sources: dict[tuple[str, str], list[str]] = {}
        for r in conn.execute("SELECT run_id, development_id, finding_id FROM development_sources"):
            self._sources.setdefault((r["run_id"], r["development_id"]), []).append(r["finding_id"])
        self._prompts: dict[tuple[str, str], str] = {}

    def development(self, run_id: str, development_id: str) -> Development:
        ids = self._sources[(run_id, development_id)]
        return Development(development_id, [self.findings[i] for i in ids])

    def of(self, p: Pair) -> tuple[str, str]:
        """The pair's input: its subject and its development's prompt in its run."""
        at = (p.run_id, p.development_id)
        if (prompt := self._prompts.get(at)) is None:
            prompt = self._prompts[at] = priority_prompt(self.development(*at))
        return p.subject_id, prompt


def version_row(conn: sqlite3.Connection, stage: str, number: int) -> sqlite3.Row:
    """The stage's version with that number; raises LookupError when the store has none."""
    row = conn.execute(
        "SELECT * FROM prompt_versions WHERE stage = ? AND number = ?", (stage, number)
    ).fetchone()
    if row is None:
        raise LookupError(f"no {stage} version {number} in the store")
    return row


class Labels:
    """A priority version's labels from its recorded calls: a pair's is the record of its cache
    key under the version, as `Inference` would replay it, and a refused call is Low with
    PRIORITY_DECLINED, as the pipeline stores it. A pair it did not rate has none."""

    def __init__(self, conn: sqlite3.Connection, version: sqlite3.Row, inputs: Inputs) -> None:
        self.version = version
        self.inputs = inputs
        self._schema = json.loads(version["schema"])
        self._systems: dict[str, str] = {}
        self._by_key: dict[str, Label] = {}
        # An answer wins over a refusal of the same input, as in Inference's cache.
        for r in conn.execute(
            "SELECT input_hash, status, output FROM inference_records WHERE prompt_version = ? "
            "AND status IN ('ok', 'refused') ORDER BY status = 'ok', id",
            (version["id"],),
        ):
            if r["status"] == "refused":
                self._by_key[r["input_hash"]] = Label(0, PRIORITY_DECLINED)
            else:
                rating = PriorityRating.model_validate_json(r["output"])
                self._by_key[r["input_hash"]] = Label(
                    PRIORITY_LABELS.index(rating.priority), rating.reason
                )

    def get(self, p: Pair) -> Label | None:
        subject_id, prompt = self.inputs.of(p)
        if (system := self._systems.get(subject_id)) is None:
            if (subject := self.inputs.subjects.get(subject_id)) is None:
                return None  # left the seed, so no call of this code can be found
            system = self._systems[subject_id] = priority_system(
                subject.name, singapore_phrase(subject_id), self.version["template"]
            )
        key = cache_key(self.version["model"], system, prompt, self._schema, self.version["effort"])
        return self._by_key.get(key)


def case_pairs(conn: sqlite3.Connection) -> list[Pair]:
    """The hand-set priority cases' pairs in the latest run."""
    run = complete_runs(conn)[-1]
    return [
        Pair(run, dev, sid)
        for case in PRIORITY_CASES
        for dev, sid in priority_pairs(conn, run, case)
    ]


def trial_pairs(conn: sqlite3.Connection, weeks: str, base: Labels) -> list[Pair]:
    """The pairs a trial rates, one per input, its first run's: version 1's pairs (those `base`
    labels) of every week ("all"), of one run ("<date>"), or the sample ("sample")."""
    runs = complete_runs(conn)
    if weeks not in ("all", "sample"):
        date.fromisoformat(weeks)  # raises on anything else
        if weeks not in runs:
            raise LookupError(f"no complete run as of {weeks}")
        runs = [weeks]
    unique: dict[tuple[str, str], tuple[Pair, Label]] = {}
    for run in runs:
        for p in run_pairs(conn, run):
            if (label := base.get(p)) is not None:
                unique.setdefault(base.inputs.of(p), (p, label))
    order = sorted(unique.values(), key=lambda pl: (pl[0].development_id, pl[0].subject_id))
    if weeks != "sample":
        return [p for p, _ in order]
    cases = {base.inputs.of(p) for p in case_pairs(conn)}
    rng = random.Random(SAMPLE_SEED)
    chosen: list[Pair] = []
    for level in range(len(PRIORITY_LABELS)):
        at = [p for p, label in order if label.level == level]
        picked = [p for p in at if base.inputs.of(p) in cases][:SAMPLE_PER_LEVEL]
        rest = [p for p in at if base.inputs.of(p) not in cases]
        picked += rng.sample(rest, min(SAMPLE_PER_LEVEL - len(picked), len(rest)))
        chosen += picked
    return sorted(chosen, key=lambda p: (p.development_id, p.subject_id, p.run_id))


@dataclass
class TrialResult:
    version: str
    number: int
    pairs: int
    calls: int  # calls made, attempts included
    cache_hits: int
    cost_usd: float  # measured: the cost the new records store
    refused: int
    levels: Counter[str]  # pairs at each level under the version tried


async def trial_priority(inf: Inference, weeks: str) -> TrialResult:
    """Rates the trial's pairs with the current priority prompt; `inf` has no run."""
    conn = inf.conn
    inputs = Inputs(conn)
    pairs = trial_pairs(conn, weeks, Labels(conn, version_row(conn, "priority", 1), inputs))
    before = conn.execute("SELECT coalesce(max(id), 0) FROM inference_records").fetchone()[0]

    async def one(p: Pair) -> PriorityRating:
        dev = inputs.development(p.run_id, p.development_id)
        return await rate_subject(inf, dev, inputs.subjects[p.subject_id])

    ratings = await asyncio.gather(*(one(p) for p in pairs))
    if len(inf.versions) != 1:
        raise RuntimeError(f"the trial used {len(inf.versions)} priority versions: {inf.versions}")
    (version,) = inf.versions
    cost = conn.execute(
        "SELECT coalesce(sum(cost_usd), 0) FROM inference_records WHERE id > ?", (before,)
    ).fetchone()[0]
    return TrialResult(
        version=version,
        number=conn.execute(
            "SELECT number FROM prompt_versions WHERE id = ?", (version,)
        ).fetchone()[0],
        pairs=len(pairs),
        calls=inf.calls,
        cache_hits=inf.cache_hits,
        cost_usd=cost,
        refused=sum(r.reason == PRIORITY_DECLINED for r in ratings),
        levels=Counter(r.priority for r in ratings),
    )


@dataclass(frozen=True)
class Moved:
    pair: Pair
    a: Label
    b: Label


@dataclass
class Comparison:
    a: sqlite3.Row  # the two prompt_versions rows
    b: sqlite3.Row
    counts: dict[tuple[int, int], int]  # pairs by (level under a, level under b), each input once
    moved: dict[str, list[Moved]]  # by run, the pairs whose level moved
    cases: list[tuple[str, Outcome, Outcome, str, str]]  # id, under a, under b, details a and b


def compare(conn: sqlite3.Connection, stage: str, a: int, b: int) -> Comparison:
    """Two priority versions' labels on the pairs both rated, and the hand-set cases under each."""
    if stage != "priority":
        raise LookupError(f"only priority versions can be compared so far, not {stage}")
    va, vb = version_row(conn, stage, a), version_row(conn, stage, b)
    inputs = Inputs(conn)
    la, lb = Labels(conn, va, inputs), Labels(conn, vb, inputs)
    counts: Counter[tuple[int, int]] = Counter()
    seen: set[tuple[str, str]] = set()
    moved: dict[str, list[Moved]] = {}
    for run in complete_runs(conn):
        for p in run_pairs(conn, run):
            if (pa := la.get(p)) is None or (pb := lb.get(p)) is None:
                continue
            if (given := inputs.of(p)) not in seen:
                seen.add(given)
                counts[(pa.level, pb.level)] += 1
            if pa.level != pb.level:
                moved.setdefault(run, []).append(Moved(p, pa, pb))
    latest = complete_runs(conn)[-1]

    def level_in(given: Labels) -> Callable[[str, str], int | None]:
        def level_of(development_id: str, subject_id: str) -> int | None:
            label = given.get(Pair(latest, development_id, subject_id))
            return None if label is None else label.level

        return level_of

    cases: list[tuple[str, Outcome, Outcome, str, str]] = []
    for case in PRIORITY_CASES:
        ra = check_priority(conn, latest, case, level_in(la))
        rb = check_priority(conn, latest, case, level_in(lb))
        cases.append((case.id, ra.outcome, rb.outcome, ra.detail, rb.detail))
    return Comparison(va, vb, dict(counts), moved, cases)


def count_table(c: Comparison) -> list[list[str | int]]:
    """Rows of level under a (High first) against columns of level under b, with totals."""
    order = list(reversed(range(len(PRIORITY_LABELS))))
    head: list[str | int] = [
        f"v{c.a['number']} \\ v{c.b['number']}",
        *(PRIORITY_LABELS[i] for i in order),
        "Total",
    ]
    rows: list[list[str | int]] = [head]
    for i in order:
        row = [c.counts.get((i, j), 0) for j in order]
        rows.append([PRIORITY_LABELS[i], *row, sum(row)])
    totals = [sum(c.counts.get((i, j), 0) for i in order) for j in order]
    rows.append(["Total", *totals, sum(totals)])
    return rows
