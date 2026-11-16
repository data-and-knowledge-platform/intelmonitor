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

Tried on the baseline (data/intelmonitor-baseline-89-entities-2026-11-16.db), whose developments
never change, so two versions differ only by prompt."""

import asyncio
import random
import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from intelmonitor.eval.cases import PRIORITY_CASES, check_priority, priority_pairs
from intelmonitor.pipeline.developments import (
    PRIORITY_DECLINED,
    Development,
    PriorityRating,
    load_findings,
    rate_subject,
)
from intelmonitor.pipeline.inference import Inference
from intelmonitor.pipeline.params import PRIORITY_LABELS
from intelmonitor.pipeline.subjects import monitored_subjects

# The sample: this many pairs at each level under version 1, drawn with a fixed seed, the
# hand-set priority cases' pairs first.
SAMPLE_PER_LEVEL = 100
SAMPLE_SEED = 20261116


@dataclass(frozen=True)
class Pair:
    run_id: str  # the run whose new or updated development it is
    development_id: str
    subject_id: str
    findings: int  # the development's findings in that run

    @property
    def input_ref(self) -> str:
        """The priority call's input_ref, which names the pair's input."""
        return f"dev:{self.development_id}:{self.findings}:{self.subject_id}"


@dataclass(frozen=True)
class Label:
    level: int  # 0 Low, 1 Medium, 2 High
    reason: str


def input_ref(development_id: str, findings: int, subject_id: str) -> str:
    return Pair("", development_id, subject_id, findings).input_ref


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
        "SELECT s.development_id, s.subject_id, n.findings FROM development_subjects s "
        "JOIN (SELECT development_id, count(*) AS findings FROM development_sources "
        "WHERE run_id = ? GROUP BY development_id) n ON n.development_id = s.development_id "
        "WHERE s.run_id = ? ORDER BY s.development_id, s.subject_id",
        (run_id, run_id),
    )
    return [
        Pair(run_id, r["development_id"], r["subject_id"], r["findings"])
        for r in rows
        if r["development_id"] in changed
    ]


def version_row(conn: sqlite3.Connection, stage: str, number: int) -> sqlite3.Row:
    """The stage's version with that number; raises LookupError when the store has none."""
    row = conn.execute(
        "SELECT * FROM prompt_versions WHERE stage = ? AND number = ?", (stage, number)
    ).fetchone()
    if row is None:
        raise LookupError(f"no {stage} version {number} in the store")
    return row


def labels(conn: sqlite3.Connection, version: str) -> dict[str, Label]:
    """The priority labels a version gave, by input_ref, from its recorded calls: a refused call
    is Low with PRIORITY_DECLINED, as the pipeline stores it."""
    out: dict[str, Label] = {}
    for r in conn.execute(
        "SELECT input_ref, status, output FROM inference_records WHERE prompt_version = ? "
        "AND status IN ('ok', 'refused') ORDER BY id",
        (version,),
    ):
        if r["status"] == "refused":
            out[r["input_ref"]] = Label(0, PRIORITY_DECLINED)
        else:
            rating = PriorityRating.model_validate_json(r["output"])
            out[r["input_ref"]] = Label(PRIORITY_LABELS.index(rating.priority), rating.reason)
    return out


def _latest_refs(conn: sqlite3.Connection) -> tuple[str, dict[str, int]]:
    """The latest complete run and the findings count of each of its developments."""
    run = complete_runs(conn)[-1]
    counts = {
        r["development_id"]: r["n"]
        for r in conn.execute(
            "SELECT development_id, count(*) AS n FROM development_sources WHERE run_id = ? "
            "GROUP BY development_id",
            (run,),
        )
    }
    return run, counts


def case_refs(conn: sqlite3.Connection) -> set[str]:
    """The input_refs of the hand-set priority cases' pairs in the latest run."""
    run, counts = _latest_refs(conn)
    return {
        input_ref(dev, counts[dev], sid)
        for case in PRIORITY_CASES
        for dev, sid in priority_pairs(conn, run, case)
    }


def trial_pairs(conn: sqlite3.Connection, weeks: str, base: dict[str, Label]) -> list[Pair]:
    """The pairs a trial rates, one per input: version 1's pairs (those in `base`) of every week
    ("all"), of one run ("<date>"), or the sample ("sample")."""
    runs = complete_runs(conn)
    if weeks not in ("all", "sample"):
        date.fromisoformat(weeks)  # raises on anything else
        if weeks not in runs:
            raise LookupError(f"no complete run as of {weeks}")
        runs = [weeks]
    unique: dict[str, Pair] = {}
    for run in runs:
        for p in run_pairs(conn, run):
            if p.input_ref in base:
                unique.setdefault(p.input_ref, p)
    pairs = sorted(unique.values(), key=lambda p: p.input_ref)
    if weeks != "sample":
        return pairs
    cases = case_refs(conn)
    rng = random.Random(SAMPLE_SEED)
    chosen: list[Pair] = []
    for level in range(len(PRIORITY_LABELS)):
        at = [p for p in pairs if base[p.input_ref].level == level]
        picked = [p for p in at if p.input_ref in cases][:SAMPLE_PER_LEVEL]
        rest = [p for p in at if p.input_ref not in cases]
        picked += rng.sample(rest, min(SAMPLE_PER_LEVEL - len(picked), len(rest)))
        chosen += picked
    return sorted(chosen, key=lambda p: p.input_ref)


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
    base = labels(conn, version_row(conn, "priority", 1)["id"])
    pairs = trial_pairs(conn, weeks, base)
    findings = {f.id: f for f in load_findings(conn, date.fromisoformat(complete_runs(conn)[-1]))}
    sources: dict[tuple[str, str], list[str]] = {}
    for p in pairs:
        sources.setdefault((p.run_id, p.development_id), [])
    for (run, dev), ids in sources.items():
        ids.extend(
            r["finding_id"]
            for r in conn.execute(
                "SELECT finding_id FROM development_sources "
                "WHERE run_id = ? AND development_id = ?",
                (run, dev),
            )
        )
    subjects = monitored_subjects()
    before = conn.execute("SELECT coalesce(max(id), 0) FROM inference_records").fetchone()[0]

    async def one(p: Pair) -> PriorityRating:
        dev = Development(
            p.development_id, [findings[i] for i in sources[(p.run_id, p.development_id)]]
        )
        return await rate_subject(inf, dev, subjects[p.subject_id])

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
    cases: list[tuple[str, bool, bool, str, str]]  # id, ok under a, under b, details a and b


def compare(conn: sqlite3.Connection, stage: str, a: int, b: int) -> Comparison:
    """Two priority versions' labels on the pairs both rated, and the hand-set cases under each."""
    if stage != "priority":
        raise LookupError(f"only priority versions can be compared so far, not {stage}")
    va, vb = version_row(conn, stage, a), version_row(conn, stage, b)
    la, lb = labels(conn, va["id"]), labels(conn, vb["id"])
    counts: Counter[tuple[int, int]] = Counter()
    seen: set[str] = set()
    moved: dict[str, list[Moved]] = {}
    for run in complete_runs(conn):
        for p in run_pairs(conn, run):
            ref = p.input_ref
            if ref not in la or ref not in lb:
                continue
            if ref not in seen:
                seen.add(ref)
                counts[(la[ref].level, lb[ref].level)] += 1
            if la[ref].level != lb[ref].level:
                moved.setdefault(run, []).append(Moved(p, la[ref], lb[ref]))
    latest, findings = _latest_refs(conn)

    def level_in(given: dict[str, Label]) -> Callable[[str, str], int | None]:
        def level_of(development_id: str, subject_id: str) -> int | None:
            label = given.get(input_ref(development_id, findings[development_id], subject_id))
            return None if label is None else label.level

        return level_of

    cases: list[tuple[str, bool, bool, str, str]] = []
    for case in PRIORITY_CASES:
        ra = check_priority(conn, latest, case, level_in(la))
        rb = check_priority(conn, latest, case, level_in(lb))
        cases.append((case.id, ra.ok, rb.ok, ra.detail, rb.detail))
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
