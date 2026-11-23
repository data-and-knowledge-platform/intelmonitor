"""`im trial link-types`, `im prompts compare-link-types` and `im export version --stage
link_types`: a link_types prompt version tried on a store's developments, two versions' answers
side by side, and one version's workbook (spec step 7 of link-types-and-inferred-impact; ticket 08,
Design 6 to 8). They work as the priority ones of trial.py and export.py do, on developments in
place of pairs, and are kept in a module of their own so that the priority commands stay as they
are.

A development's link_types input in a run is the development as trial.py's `Inputs` rebuilds it,
then its entities at Medium or High in that run with their stored levels and reasons, named and
typed by the store's seed as rated: what the stage would have read in that run. The inputs a
trial or a comparison takes are those of the developments new or updated in each run, one per
input, its first run's. A trial asks with the live prompt, or a candidate's template, schema and
effort, through `Inference` with no run: calls are recorded with their prompt version, a version
already tried replays from the cache, and no development_subjects row is written. A version's
answer for an input is its record under the input's cache key (`TypesLabels`).

The sample is LINK_TYPES_SAMPLE inputs drawn with trial.py's SAMPLE_SEED; on the baseline it is
100 of its Medium or High developments."""

import asyncio
import json
import random
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from claude_agent_sdk import EffortLevel

from intelmonitor.config import get_settings
from intelmonitor.export import Cell, Sheet, dataset_entities, write_workbook
from intelmonitor.pipeline.developments import (
    Development,
    DirectEntity,
    LinkTypeSelection,
    entity_types,
    link_types_prompt,
    select_link_types,
)
from intelmonitor.pipeline.inference import Inference, cache_key
from intelmonitor.pipeline.params import (
    LINK_TYPES_EFFORT,
    LINK_TYPES_PROMPT,
    MATERIAL_LEVEL,
    PRIORITY_LABELS,
)
from intelmonitor.pipeline.prompts import register_version, schema_text
from intelmonitor.relationships import REL_TYPES, RelType
from intelmonitor.store.db import PROMPT_VERSION_TABLES, open_store
from intelmonitor.trial import (
    SAMPLE_SEED,
    Inputs,
    VersionCalls,
    changed_developments,
    complete_runs,
    version_calls,
    version_row,
)

STAGE = "link_types"
LINK_TYPES_SAMPLE = 100


@dataclass(frozen=True)
class TypesInput:
    """A development's link_types input in a run."""

    run_id: str
    development_id: str
    entities: tuple[DirectEntity, ...]  # at Medium or High, by id


class TypesInputs:
    """The link_types input of every development of the store's complete runs that has an entity
    at Medium or High."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.inputs = Inputs(conn)
        self.runs = complete_runs(conn)
        listed: dict[tuple[str, str], list[DirectEntity]] = {}
        for r in conn.execute(
            "SELECT run_id, development_id, subject_id, priority_level, priority_reason "
            "FROM development_subjects WHERE priority_level >= ? "
            "ORDER BY run_id, development_id, subject_id",
            (MATERIAL_LEVEL,),
        ):
            s = self.inputs.subjects.get(r["subject_id"])
            if s is not None and s.type == "entity":
                listed.setdefault((r["run_id"], r["development_id"]), []).append(
                    DirectEntity(s.id, s.name, r["priority_level"], r["priority_reason"])
                )
        self._by_run: dict[str, list[TypesInput]] = {}
        for (run, dev), entities in listed.items():
            self._by_run.setdefault(run, []).append(TypesInput(run, dev, tuple(entities)))
        self._changed = {run: changed_developments(conn, run) for run in self.runs}
        self._prompts: dict[TypesInput, str] = {}

    def of_run(self, run: str) -> list[TypesInput]:
        """Every development of the run with an entity at Medium or High."""
        return self._by_run.get(run, [])

    def changed(self, run: str) -> list[TypesInput]:
        """Those new or updated in the run, which the run asks about afresh."""
        return [t for t in self.of_run(run) if t.development_id in self._changed[run]]

    def development(self, t: TypesInput) -> Development:
        return self.inputs.development(t.run_id, t.development_id)

    def prompt(self, t: TypesInput) -> str:
        if (p := self._prompts.get(t)) is None:
            p = self._prompts[t] = link_types_prompt(self.development(t), t.entities)
        return p

    def unique(self) -> list[TypesInput]:
        """One per input over every run's new or updated developments, its first run's, by
        development and run."""
        first: dict[str, TypesInput] = {}
        for run in self.runs:
            for t in self.changed(run):
                first.setdefault(self.prompt(t), t)
        return sorted(first.values(), key=lambda t: (t.development_id, t.run_id))

    def sample(self) -> list[TypesInput]:
        """LINK_TYPES_SAMPLE of the inputs, drawn with SAMPLE_SEED."""
        every = self.unique()
        rng = random.Random(SAMPLE_SEED)
        chosen = rng.sample(every, min(LINK_TYPES_SAMPLE, len(every)))
        return sorted(chosen, key=lambda t: (t.development_id, t.run_id))


@dataclass(frozen=True)
class TypesLabel:
    types: dict[str, list[RelType]]  # by entity id
    refused: bool  # Claude declined, so every entity has none


class TypesLabels:
    """A link_types version's answers from its recorded calls: an input's is the record of its
    cache key under the version, as `Inference` would replay it."""

    def __init__(self, conn: sqlite3.Connection, version: sqlite3.Row, typed: TypesInputs) -> None:
        self.version = version
        self.typed = typed
        self._schema = json.loads(version["schema"])
        self._by_key: dict[str, str | None] = {}  # the output, None when refused
        # An answer wins over a refusal of the same input, as in Inference's cache.
        for r in conn.execute(
            "SELECT input_hash, status, output FROM inference_records WHERE prompt_version = ? "
            "AND status IN ('ok', 'refused') ORDER BY status = 'ok', id",
            (version["id"],),
        ):
            self._by_key[r["input_hash"]] = r["output"] if r["status"] == "ok" else None

    def get(self, t: TypesInput) -> TypesLabel | None:
        v = self.version
        key = cache_key(v["model"], v["template"], self.typed.prompt(t), self._schema, v["effort"])
        if key not in self._by_key:
            return None
        if (output := self._by_key[key]) is None:
            return TypesLabel({e.id: [] for e in t.entities}, True)
        reply = LinkTypeSelection.model_validate_json(output)
        return TypesLabel(entity_types(reply, t.entities), False)


@dataclass
class TypesTrialResult:
    version: str
    number: int
    developments: int  # inputs asked
    entities: int  # their entities, refused calls' included
    calls: int  # calls made, attempts included
    cache_hits: int
    cost_usd: float  # measured: the cost the new records store
    refused: int  # developments
    failed: list[str]  # developments left unasked after every attempt failed, with the last error
    per_entity: Counter[int]  # entities of answered calls by the number of types picked
    by_type: Counter[str]  # entities of answered calls each type was picked for
    recorded: VersionCalls  # the version's calls over every trial of it
    per_run: list[tuple[str, int, int]]  # run, developments with an entity at Medium or High,
    # of them new or updated in the run


async def trial_link_types(
    inf: Inference,
    template: str = LINK_TYPES_PROMPT,
    schema: type[LinkTypeSelection] = LinkTypeSelection,
    effort: EffortLevel = LINK_TYPES_EFFORT,
    note: str | None = None,
) -> TypesTrialResult:
    """Asks the sample with the template, schema and effort given, the live ones by default;
    `inf` has no run. A version takes PROMPT_NOTES' note unless `note` is given. A development
    whose every attempt fails is left unasked and listed, so one does not stop the trial."""
    conn = inf.conn
    typed = TypesInputs(conn)
    sample = typed.sample()
    before = conn.execute("SELECT coalesce(max(id), 0) FROM inference_records").fetchone()[0]
    # Registered before the first call so that the version keeps the note given.
    expected = register_version(
        conn,
        STAGE,
        template,
        schema_text(schema.model_json_schema()),
        get_settings().anthropic_model,
        effort,
        note=note,
    )

    async def one(t: TypesInput) -> dict[str, list[RelType]] | None:
        return await select_link_types(
            inf, typed.development(t), t.entities, template=template, schema=schema, effort=effort
        )

    results = await asyncio.gather(*(one(t) for t in sample), return_exceptions=True)
    failed: list[str] = []
    refused = 0
    per_entity: Counter[int] = Counter()
    by_type: Counter[str] = Counter()
    for t, r in zip(sample, results, strict=True):
        if isinstance(r, BaseException):
            if not isinstance(r, Exception):
                raise r
            failed.append(f"{t.run_id} {t.development_id}: {r}")
        elif r is None:
            refused += 1
        else:
            for types in r.values():
                per_entity[len(types)] += 1
                by_type.update(types)
    if inf.versions - {expected}:
        raise RuntimeError(f"the trial used versions {inf.versions}, not {expected}")
    cost = conn.execute(
        "SELECT coalesce(sum(cost_usd), 0) FROM inference_records WHERE id > ?", (before,)
    ).fetchone()[0]
    return TypesTrialResult(
        version=expected,
        number=conn.execute(
            "SELECT number FROM prompt_versions WHERE id = ?", (expected,)
        ).fetchone()[0],
        developments=len(sample),
        entities=sum(len(t.entities) for t in sample),
        calls=inf.calls,
        cache_hits=inf.cache_hits,
        cost_usd=cost,
        refused=refused,
        failed=failed,
        per_entity=per_entity,
        by_type=by_type,
        recorded=version_calls(conn, expected),
        per_run=[(run, len(typed.of_run(run)), len(typed.changed(run))) for run in typed.runs],
    )


@dataclass(frozen=True)
class TypesChange:
    """An entity whose types differ between the two versions."""

    run_id: str
    development_id: str
    finding: str  # the development's title
    entity: DirectEntity
    a: list[RelType]
    b: list[RelType]


@dataclass
class TypesComparison:
    a: sqlite3.Row  # the two prompt_versions rows
    b: sqlite3.Row
    developments: int  # inputs both versions answered, each once
    entities: int
    changed_developments: int
    changed_entities: int
    refused: tuple[int, int]  # inputs each version declined
    picked: tuple[Counter[str], Counter[str]]  # entities each type was picked for, under a and b
    added: Counter[str]  # entities picked under b and not under a, by type
    removed: Counter[str]  # entities picked under a and not under b, by type
    changes: dict[str, list[TypesChange]]  # by run


def compare_link_types(conn: sqlite3.Connection, a: int, b: int) -> TypesComparison:
    """Two link_types versions' answers on the inputs both answered. The counts take each input
    once; the changes list it in every run it is new or updated in."""
    va, vb = version_row(conn, STAGE, a), version_row(conn, STAGE, b)
    typed = TypesInputs(conn)
    la, lb = TypesLabels(conn, va, typed), TypesLabels(conn, vb, typed)
    seen: set[str] = set()
    c = TypesComparison(
        va, vb, 0, 0, 0, 0, (0, 0), (Counter(), Counter()), Counter(), Counter(), {}
    )
    refused_a = refused_b = 0
    for run in typed.runs:
        for t in typed.changed(run):
            if (ta := la.get(t)) is None or (tb := lb.get(t)) is None:
                continue
            first = typed.prompt(t) not in seen
            seen.add(typed.prompt(t))
            changed = False
            for e in t.entities:
                xa, xb = ta.types[e.id], tb.types[e.id]
                if set(xa) != set(xb):
                    changed = True
                    finding = typed.development(t).title
                    c.changes.setdefault(run, []).append(
                        TypesChange(run, t.development_id, finding, e, xa, xb)
                    )
                if first:
                    c.entities += 1
                    c.changed_entities += set(xa) != set(xb)
                    c.picked[0].update(xa)
                    c.picked[1].update(xb)
                    c.added.update(set(xb) - set(xa))
                    c.removed.update(set(xa) - set(xb))
            if first:
                c.developments += 1
                c.changed_developments += changed
                refused_a += ta.refused
                refused_b += tb.refused
    c.refused = (refused_a, refused_b)
    return c


def type_table(c: TypesComparison) -> list[list[str | int]]:
    """One row per type of the 22: entities it was picked for under a and under b, added and
    removed, with totals."""
    a, b = f"v{c.a['number']}", f"v{c.b['number']}"
    rows: list[list[str | int]] = [["Type", a, b, f"Added in {b}", f"Removed in {b}"]]
    for t in REL_TYPES:
        rows.append([t, c.picked[0][t], c.picked[1][t], c.added[t], c.removed[t]])
    rows.append(
        [
            "Total",
            c.picked[0].total(),
            c.picked[1].total(),
            c.added.total(),
            c.removed.total(),
        ]
    )
    return rows


def compare_workbook(c: TypesComparison, out: Path) -> dict[str, int]:
    """Two versions compared: Counts, Types (the type table), Prompts (both versions), then a
    sheet per run with the entities whose types differ."""
    a, b = f"v{c.a['number']}", f"v{c.b['number']}"
    counts = Sheet(
        "Counts",
        [("Measure", 44), ("Count", 10)],
        [
            ["Developments both versions answered", c.developments],
            ["Their entities at Medium or High", c.entities],
            ["Developments whose types differ", c.changed_developments],
            ["Entities whose types differ", c.changed_entities],
            [f"Developments {a} declined", c.refused[0]],
            [f"Developments {b} declined", c.refused[1]],
        ],
    )
    table = type_table(c)
    types = Sheet(
        "Types",
        [(str(h), 30 if i == 0 else 12) for i, h in enumerate(table[0])],
        [list(r) for r in table[1:]],
    )
    prompts = Sheet(
        "Prompts",
        [("Field", 10), (a, 90), (b, 90)],
        [
            [field, c.a[column], c.b[column]]
            for field, column in (
                ("Number", "number"),
                ("Id", "id"),
                ("Model", "model"),
                ("Effort", "effort"),
                ("Note", "note"),
                ("Prompt", "template"),
            )
        ],
    )

    def joined(types: list[RelType]) -> str:
        return ", ".join(types)

    weeks = [
        Sheet(
            run,
            [
                ("Development id", 20),
                ("Finding", 60),
                ("Entity", 24),
                ("Level", 8),
                (f"Types {a}", 50),
                (f"Types {b}", 50),
                (f"Added in {b}", 40),
                (f"Removed in {b}", 40),
            ],
            [
                [
                    x.development_id,
                    x.finding,
                    x.entity.name,
                    PRIORITY_LABELS[x.entity.level],
                    joined(x.a),
                    joined(x.b),
                    joined([t for t in x.b if t not in x.a]),
                    joined([t for t in x.a if t not in x.b]),
                ]
                for x in changes
            ],
        )
        for run, changes in sorted(c.changes.items())
    ]
    sheets = [counts, types, prompts, *weeks]
    write_workbook(sheets, out)
    return {s.name: len(s.rows) for s in sheets}


TYPES_COLUMNS = [
    ("Run date", 11),
    ("Development id", 20),
    ("Finding", 60),
    ("Entity", 24),
    ("Level", 8),
    ("Reason", 80),
    ("Types", 60),
]


def version_workbook(store: Path, number: int, out: Path) -> dict[str, int]:
    """One link_types version's workbook from the store at `store`, written to `out`: About,
    Summary, then a sheet per run with each development new or updated in it and each of its
    entities at Medium or High, with the types picked. A run that used the version shows its
    stored types; any other shows the version's recorded answers for the inputs it asked, and has
    no sheet when it asked none. The row count of each sheet."""
    conn = open_store(store, PROMPT_VERSION_TABLES)
    try:
        version = version_row(conn, STAGE, number)
        typed = TypesInputs(conn)
        names = {i: s.name for i, s in typed.inputs.subjects.items()}
        used = {
            r["run_id"]
            for r in conn.execute(
                "SELECT run_id FROM run_prompts WHERE stage = ? AND version = ?",
                (STAGE, version["id"]),
            )
        }
        labels = TypesLabels(conn, version, typed)
        weeks: list[Sheet] = []
        for run in typed.runs:
            titles = {
                r["id"]: r["title"]
                for r in conn.execute(
                    "SELECT id, coalesce(headline, title) AS title FROM developments "
                    "WHERE run_id = ?",
                    (run,),
                )
            }
            rows: list[list[Cell]] = []
            if run in used:
                changed = changed_developments(conn, run)
                for r in conn.execute(
                    "SELECT development_id, subject_id, priority_level, priority_reason, "
                    "link_types FROM development_subjects WHERE run_id = ? "
                    "AND link_types IS NOT NULL ORDER BY development_id, subject_id",
                    (run,),
                ):
                    if r["development_id"] in changed:
                        rows.append(
                            [
                                date.fromisoformat(run),
                                r["development_id"],
                                titles.get(r["development_id"]),
                                names.get(r["subject_id"], r["subject_id"]),
                                PRIORITY_LABELS[r["priority_level"]],
                                r["priority_reason"],
                                ", ".join(json.loads(r["link_types"])),
                            ]
                        )
            else:
                for t in typed.changed(run):
                    if (label := labels.get(t)) is None:
                        continue
                    for e in t.entities:
                        rows.append(
                            [
                                date.fromisoformat(run),
                                t.development_id,
                                titles.get(t.development_id),
                                e.name,
                                PRIORITY_LABELS[e.level],
                                e.reason,
                                "declined" if label.refused else ", ".join(label.types[e.id]),
                            ]
                        )
            if rows:
                weeks.append(Sheet(run, TYPES_COLUMNS, rows))
        entities = dataset_entities(conn, typed.inputs.subjects)
    finally:
        conn.close()
    runs = typed.runs
    about = Sheet(
        "About",
        [("Field", 18), ("Value", 120)],
        [
            ["Stage", STAGE],
            ["Version", version["number"]],
            ["Id", version["id"]],
            ["Note", version["note"]],
            ["Model", version["model"]],
            ["Effort", version["effort"] or "the model's own"],
            ["First used", version["first_used"][:10]],
            ["Dataset", store.stem.removeprefix("intelmonitor-")],
            ["Entities", f"{entities}, tagged in the documents of the runs"],
            ["Runs", f"{runs[0]} to {runs[-1]}" if runs else "none"],
            ["Written", date.today()],
            ["Prompt", version["template"]],
        ],
    )
    summary = Sheet(
        "Summary",
        [("Run date", 11), ("Developments", 13), ("Entities", 9), ("With no type", 13)],
        [
            [
                date.fromisoformat(w.name),
                len({r[1] for r in w.rows}),
                len(w.rows),
                sum(r[-1] == "" for r in w.rows),
            ]
            for w in weeks
        ],
    )
    sheets = [about, summary, *weeks]
    write_workbook(sheets, out)
    return {s.name: len(s.rows) for s in sheets}
