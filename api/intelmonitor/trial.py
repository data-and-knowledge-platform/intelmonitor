"""`im trial priority` and `im prompts compare`: a priority prompt version tried on a store's
stored pairs, and two versions' labels side by side (ticket 08 of link-types-and-inferred-impact,
Design 6 and 7).

A pair is a development and a subject in a run whose development was new or updated in that run
(every development in a run with no previous run). The pairs a trial rates are those priority
version 1 rated by a call: entities, or the topics of a development that names no entity, and
never a pair a detector's rule set alone. A trial rebuilds each pair's input from the store, as
the run gave it to version 1 (`priority_prompt`), and rates it with the current code's priority
prompt, or with a candidate's (`CANDIDATES`), through `Inference` with no run: calls are recorded
with their prompt version, a version already tried replays from the cache, and no
`development_subjects` row is written. A candidate is a template, schema and effort given to the
trial, so trying one never edits PRIORITY_PROMPT, PriorityRating or PRIORITY_EFFORT, and the live
prompt stays at the version the stored runs were rated with.

A pair's input is its subject and the development's prompt in its run (`Inputs`); two pairs share
one when a call on one would replay the other's. The input_ref of a call does not identify it: a
development can keep its findings count while its findings change. So a version's label of a pair
is found as `Inference` finds a recorded call, by the pair's cache key under that version
(`Labels`), and a trial rates each input once.

Tried on the baseline (data/intelmonitor-baseline-89-entities-2026-11-16.db), whose developments
never change, so two versions differ only by prompt. The baseline is frozen: the commands open it
without migrations (`open_store`), and it carries the seed its runs were rated with
(`seed_as_rated`), which every prompt rebuilt on it is filled from, so a later seed edit does not
move a pair off its version 1 record. A store that holds no frozen seed, such as the live one,
is filled from the working tree's seed, which its runs are rerun with. A pair version 1 rated by
a call whose record is still not found is listed by the trial and the comparison (`unfound`),
never dropped silently."""

import asyncio
import json
import random
import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from claude_agent_sdk import EffortLevel
from pydantic import BaseModel, ConfigDict, Field

from intelmonitor.config import get_settings
from intelmonitor.eval.cases import PRIORITY_CASES, Outcome, check_priority, priority_pairs
from intelmonitor.pipeline.developments import (
    PRIORITY_DECLINED,
    Development,
    PriorityRating,
    called_subjects,
    load_findings,
    priority_prompt,
    rate_subject,
)
from intelmonitor.pipeline.inference import Inference, cache_key
from intelmonitor.pipeline.params import (
    PRIORITY_EFFORT,
    PRIORITY_LABELS,
    PRIORITY_PROMPT,
    Priority,
    priority_system,
)
from intelmonitor.pipeline.prompts import THINK_FIRST, register_version, schema_text
from intelmonitor.pipeline.subjects import PrioritySubject, priority_subjects, priority_subjects_of
from intelmonitor.seed import SubjectsSeed, SubjectType
from intelmonitor.store.db import get_meta, set_meta

# The sample: this many pairs at each level under version 1, drawn with a fixed seed, the
# hand-set priority cases' pairs first.
SAMPLE_PER_LEVEL = 100
SAMPLE_SEED = 20261116


@dataclass(frozen=True)
class Candidate:
    """A priority prompt a trial rates with in place of the live one."""

    template: str
    schema: type[PriorityRating]
    effort: EffortLevel
    note: str | None  # stored with the version when first used; None takes PROMPT_NOTES'


# PriorityRating with each field's meaning in its description, where the live prompt's "## Output"
# section gave it. Titled as PriorityRating, so its schema differs only by the descriptions. (No
# docstring: pydantic would send it to the model as the schema's description.)
class DescribedPriorityRating(PriorityRating):
    model_config = ConfigDict(title="PriorityRating")
    priority: Priority = Field(
        description="The finding's priority for the subject, by the priority levels and rules."
    )
    reason: str = Field(
        description="One sentence explaining the priority, citing the Singapore link if High."
    )


def _without_output(template: str) -> str:
    """The template without its "## Output" section ("Respond with JSON only" and the JSON
    shape), which structured outputs make redundant."""
    head, found, _ = template.partition("\n\n## Output\n")
    if not found:
        raise ValueError("the priority template has no Output section to remove")
    return head


_TIDY = _without_output(PRIORITY_PROMPT)
_TIDY_NOTE = "Slide 7 prompt without its JSON lines, field meanings in the schema"
# The sweep of ticket 09: the tidied prompt at medium and high effort, each with and without
# THINK_FIRST as its last line. The levels and rules are the live prompt's, unchanged. "live" is
# the current PRIORITY_PROMPT, PriorityRating and PRIORITY_EFFORT.
CANDIDATES: dict[str, Candidate] = {
    "live": Candidate(PRIORITY_PROMPT, PriorityRating, PRIORITY_EFFORT, None),
    "medium": Candidate(_TIDY, DescribedPriorityRating, "medium", f"{_TIDY_NOTE}; medium effort."),
    "medium-think": Candidate(
        f"{_TIDY}\n\n{THINK_FIRST}",
        DescribedPriorityRating,
        "medium",
        f"{_TIDY_NOTE}; medium effort; ends with THINK_FIRST.",
    ),
    "high": Candidate(_TIDY, DescribedPriorityRating, "high", f"{_TIDY_NOTE}; high effort."),
    "high-think": Candidate(
        f"{_TIDY}\n\n{THINK_FIRST}",
        DescribedPriorityRating,
        "high",
        f"{_TIDY_NOTE}; high effort; ends with THINK_FIRST.",
    ),
}


@dataclass(frozen=True)
class Pair:
    run_id: str  # the run whose new or updated development it is
    development_id: str
    subject_id: str


@dataclass(frozen=True)
class Label:
    level: int  # 0 Low, 1 Medium, 2 High
    reason: str


# The meta key of the seed a frozen store's runs were rated with (`freeze_seed`). The baseline's
# was written once on 16 Nov 2026 from api/seed/subjects.yaml at 2693b13, whose names, kinds and
# homes are those of the 11 Nov rebuild: every pair version 1 rated by a call finds its record.
# Only a frozen store holds it; the live store's runs follow the working tree's seed.
SEED_KEY = "seed_as_rated"


class SeedAsRated(BaseModel):
    """The seed as a store's runs rated with it: each topic's and entity's name, type and
    Singapore phrase, and where that seed came from."""

    source: str  # e.g. "api/seed/subjects.yaml at <commit>"
    subjects: list[PrioritySubject]


def freeze_seed(conn: sqlite3.Connection, seed: SubjectsSeed, source: str) -> int:
    """Writes into the store, once, the seed its runs were rated with, as `priority_subjects_of`
    derives it. The derived names and phrases are stored rather than the seed file, so neither a
    later seed edit nor later code that reads the seed changes a prompt rebuilt on the store.
    Raises ValueError when the store already holds one. The subjects written; commits."""
    if get_meta(conn, SEED_KEY) is not None:
        raise ValueError("the store already holds its seed as rated, which is never replaced")
    subjects = list(priority_subjects_of(seed).values())
    set_meta(conn, SEED_KEY, SeedAsRated(source=source, subjects=subjects).model_dump_json())
    return len(subjects)


def seed_as_rated(conn: sqlite3.Connection) -> dict[str, PrioritySubject]:
    """The seed the store's runs were rated with, by subject id: the one frozen into it, as in
    the baseline, else the working tree's, which a live store's runs are rerun with. A pair the
    seed no longer rebuilds as rated is not lost: its record is not found, and `unfound` lists
    it."""
    if (value := get_meta(conn, SEED_KEY)) is None:
        return priority_subjects()
    return {s.id: s for s in SeedAsRated.model_validate_json(value).subjects}


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
    development's findings in that run, as the latest complete run loads them, and the subject
    from the store's seed as rated (`seed_as_rated`)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.subjects = seed_as_rated(conn)
        latest = date.fromisoformat(complete_runs(conn)[-1])
        self.findings = {f.id: f for f in load_findings(conn, latest)}
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
                return None  # not in the seed as rated, so no call can be found
            system = self._systems[subject_id] = priority_system(
                subject.name, subject.singapore, self.version["template"]
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


def trial_runs(conn: sqlite3.Connection, weeks: str) -> list[str]:
    """The runs a trial draws from: every complete run ("all", "sample"), or the one dated."""
    runs = complete_runs(conn)
    if weeks in ("all", "sample"):
        return runs
    date.fromisoformat(weeks)  # raises on anything else
    if weeks not in runs:
        raise LookupError(f"no complete run as of {weeks}")
    return [weeks]


def unfound(conn: sqlite3.Connection, base: Labels, runs: list[str]) -> list[Pair]:
    """The runs' pairs that version 1 (`base`) rated by a call, as `rate` chose them from each
    development's stored subjects and the seed as rated, whose record is not found under their
    cache key. A trial cannot try them and a comparison has no level for them, so the commands
    list them."""
    types: dict[str, SubjectType] = {i: s.type for i, s in base.inputs.subjects.items()}
    out: list[Pair] = []
    for run in runs:
        subjects: dict[str, list[str]] = {}
        for p in run_pairs(conn, run):
            subjects.setdefault(p.development_id, []).append(p.subject_id)
        for dev, sids in subjects.items():
            for sid in called_subjects(base.inputs.development(run, dev), sids, types):
                if base.get(p := Pair(run, dev, sid)) is None:
                    out.append(p)
    return out


def trial_pairs(conn: sqlite3.Connection, weeks: str, base: Labels) -> list[Pair]:
    """The pairs a trial rates, one per input, its first run's: version 1's pairs (those `base`
    labels) of every week ("all"), of one run ("<date>"), or the sample ("sample")."""
    runs = trial_runs(conn, weeks)
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
class VersionCalls:
    """What a version's recorded calls cost and returned, every trial of it included (measured:
    the tokens and cost the API reported, priced by pipeline/claude.py)."""

    answered: int  # calls with an output
    cut_off: int  # calls stopped before the output was complete (max_tokens)
    errors: int  # other failed calls
    refused: Counter[str]  # refused calls by stop_details category ("none" when it names none)
    mean_output_tokens: float  # of the answered calls, thinking included
    cost_usd: float  # of every recorded call

    @property
    def calls(self) -> int:
        return self.answered + self.cut_off + self.errors + self.refused.total()

    @property
    def cost_per_call(self) -> float:
        return self.cost_usd / self.calls if self.calls else 0.0


def version_calls(conn: sqlite3.Connection, version: str) -> VersionCalls:
    """The version's recorded calls, counted."""
    rows = conn.execute(
        "SELECT status, error, refusal_category, output_tokens, cost_usd FROM inference_records "
        "WHERE prompt_version = ?",
        (version,),
    ).fetchall()
    answered = [r for r in rows if r["status"] == "ok"]
    failed = [r for r in rows if r["status"] == "error"]
    cut_off = sum((r["error"] or "").startswith("ClaudeCutOff") for r in failed)
    return VersionCalls(
        answered=len(answered),
        cut_off=cut_off,
        errors=len(failed) - cut_off,
        refused=Counter(r["refusal_category"] or "none" for r in rows if r["status"] == "refused"),
        mean_output_tokens=(
            sum(r["output_tokens"] or 0 for r in answered) / len(answered) if answered else 0.0
        ),
        cost_usd=sum(r["cost_usd"] or 0.0 for r in rows),
    )


@dataclass
class TrialResult:
    version: str
    number: int
    pairs: int
    calls: int  # calls made, attempts included
    cache_hits: int
    cost_usd: float  # measured: the cost the new records store
    refused: int
    failed: list[str]  # pairs left unrated after every attempt failed, with the last error
    unfound: list[Pair]  # pairs of the runs tried that version 1 rated, with no record found
    levels: Counter[str]  # pairs at each level under the version tried
    recorded: VersionCalls  # the version's calls over every trial of it


async def trial_priority(inf: Inference, weeks: str, candidate: Candidate) -> TrialResult:
    """Rates the trial's pairs with the candidate's priority prompt; `inf` has no run. A pair
    whose every attempt fails is left unrated and listed, so one pair does not stop the trial."""
    conn = inf.conn
    inputs = Inputs(conn)
    base = Labels(conn, version_row(conn, "priority", 1), inputs)
    pairs = trial_pairs(conn, weeks, base)
    missing = unfound(conn, base, trial_runs(conn, weeks))
    before = conn.execute("SELECT coalesce(max(id), 0) FROM inference_records").fetchone()[0]
    schema = schema_text(candidate.schema.model_json_schema())
    # Registered before the first call so that the version keeps the candidate's note.
    expected = register_version(
        conn,
        "priority",
        candidate.template,
        schema,
        get_settings().anthropic_model,
        candidate.effort,
        note=candidate.note,
    )

    async def one(p: Pair) -> PriorityRating:
        dev = inputs.development(p.run_id, p.development_id)
        return await rate_subject(
            inf,
            dev,
            inputs.subjects[p.subject_id],
            template=candidate.template,
            schema=candidate.schema,
            effort=candidate.effort,
        )

    results = await asyncio.gather(*(one(p) for p in pairs), return_exceptions=True)
    ratings: list[PriorityRating] = []
    failed: list[str] = []
    for p, r in zip(pairs, results, strict=True):
        if isinstance(r, PriorityRating):
            ratings.append(r)
        elif isinstance(r, Exception):
            failed.append(f"{p.run_id} {p.development_id} {p.subject_id}: {r}")
        else:
            raise r
    if inf.versions - {expected}:
        raise RuntimeError(f"the trial used versions {inf.versions}, not {expected}")
    cost = conn.execute(
        "SELECT coalesce(sum(cost_usd), 0) FROM inference_records WHERE id > ?", (before,)
    ).fetchone()[0]
    return TrialResult(
        version=expected,
        number=conn.execute(
            "SELECT number FROM prompt_versions WHERE id = ?", (expected,)
        ).fetchone()[0],
        pairs=len(pairs),
        calls=inf.calls,
        cache_hits=inf.cache_hits,
        cost_usd=cost,
        refused=sum(r.reason == PRIORITY_DECLINED for r in ratings),
        failed=failed,
        unfound=missing,
        levels=Counter(r.priority for r in ratings),
        recorded=version_calls(conn, expected),
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
    unfound: list[Pair]  # pairs version 1 rated, with no record found
    names: dict[str, str]  # subject names, from the seed as rated


def compare(conn: sqlite3.Connection, stage: str, a: int, b: int) -> Comparison:
    """Two priority versions' labels on the pairs both rated, and the hand-set cases under each."""
    if stage != "priority":
        raise LookupError(f"only priority versions can be compared so far, not {stage}")
    va, vb = version_row(conn, stage, a), version_row(conn, stage, b)
    inputs = Inputs(conn)
    la, lb = Labels(conn, va, inputs), Labels(conn, vb, inputs)
    base = la if a == 1 else lb if b == 1 else Labels(conn, version_row(conn, stage, 1), inputs)
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
    missing = unfound(conn, base, complete_runs(conn))
    names = {i: s.name for i, s in inputs.subjects.items()}
    return Comparison(va, vb, dict(counts), moved, cases, missing, names)


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
