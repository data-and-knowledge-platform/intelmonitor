"""`im export labels`: the labels the runs gave, as an Excel workbook for the team's audit
(ticket 24). It opens the store read-only, so it runs no migration and writes nothing there, and
it calls no model. `im export version` writes one prompt version's workbook the same way
(`version_workbook`, `link_ratings_workbook`), and `im prompts compare` writes two versions'
side by side (`compare_workbook`); both name and sort subjects by the store's seed as rated
(trial.py), not the working tree's.

The labels workbook's sheets:
Direct, Indirect and Dropped cover the latest complete run only. A run reads every document from
the corpus start to its date, so the latest run holds every development of the earlier runs.

- Direct: one row per development and entity it is about. A development that names no entity
  gets one row per topic instead, since each such topic is rated itself.
- Indirect: one row per indirect impact (development_impacts).
- Links: the step 2a review sheet, one row per rated link in the store's copy of
  relationships.yaml (every type but belongs_to_sector), with `rated_by` from transmission.yaml.
- Dropped: the findings the run left out as promotional or price moves.
"""

import sqlite3
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from intelmonitor.ingest.models import corpus_start
from intelmonitor.pipeline.developments import COLLECTION_RANK, load_findings
from intelmonitor.pipeline.params import PRIORITY_LABELS
from intelmonitor.pipeline.subjects import PrioritySubject, all_subjects, priority_subjects
from intelmonitor.relationships import (
    Relationship,
    load_ratings,
    load_relationships,
    rateable,
)
from intelmonitor.store.db import PROMPT_VERSION_TABLES, open_store
from intelmonitor.trial import (
    Comparison,
    Inputs,
    Label,
    Labels,
    changed_developments,
    complete_runs,
    count_table,
    run_pairs,
    version_row,
)

Cell = str | int | bool | date | None


@dataclass
class Sheet:
    name: str
    columns: list[tuple[str, int]]  # header and column width in characters
    rows: list[list[Cell]]


def _names(conn: sqlite3.Connection) -> dict[str, str]:
    """Display names for every id a label can name: the store's link endpoints and the seed's
    topics, groups and entities."""
    names = {
        r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM relationship_endpoints")
    }
    names.update({i: s.name for i, s in all_subjects().items()})
    return names


def _latest_run(conn: sqlite3.Connection) -> str | None:
    """The latest complete run's id; a run still computing or failed is left out. None, when
    there is no complete run, matches no row in a `run_id = ?` query."""
    row = conn.execute(
        "SELECT id FROM runs WHERE status = 'complete' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


def _day(iso: str) -> date:
    return date.fromisoformat(iso[:10])


def _per_development(
    conn: sqlite3.Connection, sql: str, run: str | None
) -> dict[str, list[sqlite3.Row]]:
    """Rows of `sql`, which selects development_id among its columns and ends in
    `run_id = ?` for the run, keyed by development_id."""
    out: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in conn.execute(sql, (run,)):
        out[r["development_id"]].append(r)
    return out


DIRECT_COLUMNS = [
    ("Run date", 11),
    ("Development id", 20),
    ("Headline", 60),
    ("Subject", 24),
    ("Kind", 7),
    ("Topics", 30),
    ("Level", 8),
    ("Reason", 80),
    ("Adverse", 8),
    ("Category", 12),
    ("Sources", 8),
    ("First published", 11),
    ("Last published", 11),
    ("Top source URL", 50),
]
LEVEL_COLUMN = 6


def direct_rows(
    conn: sqlite3.Connection,
    names: dict[str, str],
    subjects: Mapping[str, PrioritySubject],
    run: str | None,
    only: set[str] | None = None,
    given: Mapping[tuple[str, str], Label] | None = None,
) -> list[list[Cell]]:
    """One row per development of the run (those in `only`, when given) and entity it is about,
    or per topic when it names no entity, with the stored level and reason; `subjects` says
    which ids are topics. With `given`, a prompt version's labels of the run's pairs by
    development and subject, the level and reason are the version's, and a pair it did not rate
    has no row."""
    devs = conn.execute(
        "SELECT run_id, id, coalesce(headline, title) AS headline, category, first_published, "
        "last_published FROM developments WHERE run_id = ? ORDER BY id",
        (run,),
    ).fetchall()
    if only is not None:
        devs = [d for d in devs if d["id"] in only]
    tagged = _per_development(
        conn,
        "SELECT development_id, subject_id, adverse, priority_level, priority_reason "
        "FROM development_subjects WHERE run_id = ? ORDER BY subject_id",
        run,
    )
    sources = _per_development(
        conn,
        "SELECT s.development_id, s.finding_id, s.document_id, d.collection, "
        "d.published_at, d.url FROM development_sources s "
        "JOIN source_documents d ON d.id = s.document_id WHERE s.run_id = ?",
        run,
    )
    rows: list[list[Cell]] = []
    for d in devs:
        key = d["id"]
        # Development subjects are entities and topics; one not in `subjects` counts as an entity.
        topics = [
            s
            for s in tagged[key]
            if (sub := subjects.get(s["subject_id"])) is not None and sub.type == "topic"
        ]
        entities = [s for s in tagged[key] if s not in topics]
        docs = sources[key]
        # The development's lead source, as the pipeline ranks them: primary first, then earliest.
        top = min(
            docs,
            key=lambda s: (
                COLLECTION_RANK.get(s["collection"], len(COLLECTION_RANK)),
                s["published_at"][:10],
                s["finding_id"],
            ),
            default=None,
        )
        for s in entities or topics:
            label = Label(s["priority_level"], s["priority_reason"])
            if given is not None:
                found = given.get((key, s["subject_id"]))
                if found is None:
                    continue
                label = found
            rows.append(
                [
                    _day(d["run_id"]),
                    d["id"],
                    d["headline"],
                    names.get(s["subject_id"], s["subject_id"]),
                    "entity" if entities else "topic",
                    ", ".join(names[t["subject_id"]] for t in topics),
                    PRIORITY_LABELS[label.level],
                    label.reason,
                    bool(s["adverse"]),
                    d["category"],
                    len({x["document_id"] for x in docs}),
                    _day(d["first_published"]),
                    _day(d["last_published"]),
                    top["url"] if top else None,
                ]
            )
    return rows


def direct_sheet(conn: sqlite3.Connection, names: dict[str, str]) -> Sheet:
    rows = direct_rows(conn, names, priority_subjects(), _latest_run(conn))
    return Sheet("Direct", DIRECT_COLUMNS, rows)


def indirect_sheet(conn: sqlite3.Connection, names: dict[str, str]) -> Sheet:
    found = conn.execute(
        "SELECT i.run_id, i.development_id, coalesce(d.headline, d.title) AS headline, "
        "i.source_id, s.priority_level AS source_level, i.subject_id, i.relationship_id, "
        "i.region, i.strength, i.level, r.data AS link FROM development_impacts i "
        "JOIN developments d ON d.run_id = i.run_id AND d.id = i.development_id "
        "LEFT JOIN development_subjects s ON s.run_id = i.run_id "
        "AND s.development_id = i.development_id AND s.subject_id = i.source_id "
        "LEFT JOIN relationships r ON r.id = i.relationship_id "
        "WHERE i.run_id = ? ORDER BY i.development_id, i.subject_id",
        (_latest_run(conn),),
    ).fetchall()
    rows: list[list[Cell]] = []
    for i in found:
        # The link id is "from:type:to"; the store's copy gives its reason, when it still holds
        # the link (an older run may have used a link since removed).
        from_, type_, to = i["relationship_id"].split(":")
        link = Relationship.model_validate_json(i["link"]) if i["link"] else None
        rows.append(
            [
                _day(i["run_id"]),
                i["development_id"],
                i["headline"],
                names.get(i["source_id"], i["source_id"]),
                PRIORITY_LABELS[i["source_level"]] if i["source_level"] is not None else None,
                names.get(i["subject_id"], i["subject_id"]),
                f"{names.get(from_, from_)} {type_} {names.get(to, to)}",
                type_,
                "from_to" if from_ == i["source_id"] else "to_from",
                names.get(i["region"], i["region"]) if i["region"] else None,
                link.reason if link else None,
                i["strength"],
                PRIORITY_LABELS[i["level"]],
            ]
        )
    return Sheet(
        "Indirect",
        [
            ("Run date", 11),
            ("Development id", 20),
            ("Headline", 60),
            ("Source entity", 24),
            ("Source level", 8),
            ("Reached entity", 24),
            ("Link", 50),
            ("Link type", 16),
            ("Direction", 9),
            ("Via region", 16),
            ("Link reason", 60),
            ("Strength", 10),
            ("Level", 8),
        ],
        rows,
    )


LINK_COLUMNS = [
    ("Id", 40),
    ("From", 24),
    ("Type", 16),
    ("To", 24),
    ("Reason", 50),
    ("From-to strength", 10),
    ("From-to reason", 60),
    ("To-from strength", 10),
    ("To-from reason", 60),
    ("Rated by", 8),
]


def links_sheet(conn: sqlite3.Connection, names: dict[str, str]) -> Sheet:
    ratings = load_ratings()
    rows: list[list[Cell]] = []
    for row in conn.execute("SELECT data FROM relationships ORDER BY id"):
        r = Relationship.model_validate_json(row["data"])
        if not rateable(r):
            continue
        t = r.transmission
        e = ratings.get(r.id)
        rows.append(
            [
                r.id,
                names.get(r.from_, r.from_),
                r.type,
                names.get(r.to, r.to),
                r.reason,
                t.from_to.strength if t else None,
                t.from_to.reason if t else None,
                t.to_from.strength if t else None,
                t.to_from.reason if t else None,
                e.rated_by if e else None,
            ]
        )
    return Sheet("Links", LINK_COLUMNS, rows)


def dropped_sheet(conn: sqlite3.Connection, names: dict[str, str]) -> Sheet:
    run = _latest_run(conn)
    rows: list[list[Cell]] = []
    if run:
        dropped = [
            f
            for f in load_findings(conn, _day(run), corpus_start(conn))
            if f.promotional or f.price_move
        ]
        ids = [f.document_id for f in dropped]
        urls = {
            r["id"]: r["url"]
            for r in conn.execute(
                f"SELECT id, url FROM source_documents WHERE id IN ({','.join('?' * len(ids))})",  # noqa: S608
                ids,
            )
        }
        for f in dropped:
            rows.append(
                [
                    f.id,
                    f.published,
                    f.domain,
                    f.title,
                    urls[f.document_id],
                    f.statement,
                    ", ".join(names.get(s, s) for s in f.subjects),
                    f.category,
                    f.promotional,
                    f.price_move,
                ]
            )
    return Sheet(
        "Dropped",
        [
            ("Finding id", 20),
            ("Published", 11),
            ("Outlet", 20),
            ("Title", 50),
            ("URL", 50),
            ("Statement", 80),
            ("Subjects", 30),
            ("Category", 12),
            ("Promotional", 11),
            ("Price move", 10),
        ],
        rows,
    )


def _clean(v: Cell) -> Cell:
    """Strip the control characters Excel cannot hold from text."""
    return ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v


def write_workbook(sheets: list[Sheet], out: Path) -> None:
    """One worksheet per sheet: a bold, frozen header row with filters, then the rows."""
    wb = Workbook()
    wb.remove(wb.worksheets[0])  # the default empty sheet
    for s in sheets:
        ws = wb.create_sheet(s.name)
        ws.append([h for h, _ in s.columns])
        for c in ws[1]:
            c.font = Font(bold=True)
        for row in s.rows:
            ws.append([_clean(v) for v in row])
        for n, (_, width) in enumerate(s.columns, start=1):
            ws.column_dimensions[get_column_letter(n)].width = width
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)


def export_labels(store: Path, out: Path) -> dict[str, int]:
    """Write the workbook from the store at `store` to `out`; the row count of each sheet."""
    conn = open_store(store)
    try:
        names = _names(conn)
        sheets = [
            direct_sheet(conn, names),
            indirect_sheet(conn, names),
            links_sheet(conn, names),
            dropped_sheet(conn, names),
        ]
    finally:
        conn.close()
    write_workbook(sheets, out)
    return {s.name: len(s.rows) for s in sheets}


def _about(rows: list[tuple[str, Cell]]) -> Sheet:
    return Sheet("About", [("Field", 18), ("Value", 120)], [[k, v] for k, v in rows])


def dataset_entities(conn: sqlite3.Connection, subjects: Mapping[str, PrioritySubject]) -> int:
    """The entities a store's runs monitored: those tagged in a document from the corpus start
    on, which screening tags with every seed subject it finds, counted as entities by the seed
    the runs used (`subjects`)."""
    entities = {i for i, s in subjects.items() if s.type == "entity"}
    start = corpus_start(conn)
    tagged = conn.execute(
        "SELECT DISTINCT t.subject_id FROM document_subjects t "
        "JOIN source_documents d ON d.id = t.document_id "
        "WHERE substr(d.published_at, 1, 10) >= ?",
        ((start.isoformat() if start else ""),),
    )
    return sum(r["subject_id"] in entities for r in tagged)


def version_workbook(store: Path, stage: str, number: int, out: Path) -> dict[str, int]:
    """One priority version's workbook from the store at `store`, written to `out`: About,
    Summary, then a sheet per run with the pairs of a development and an entity (a topic, when
    it names none) new or updated in that run. A run that used the version shows its stored
    labels; any other run shows the version's recorded labels of the pairs whose input it rated,
    and has no sheet when it rated none. A run shares an input with another where a development's
    findings did not change between them, so a trial of one week can also fill rows of another:
    the rows a trial of that week would replay from the cache. The row count of each sheet."""
    if stage != "priority":
        raise LookupError(f"no workbook for {stage} versions yet")
    conn = open_store(store, PROMPT_VERSION_TABLES)
    try:
        version = version_row(conn, stage, number)
        inputs = Inputs(conn)
        names = {i: s.name for i, s in inputs.subjects.items()}
        used = {
            r["run_id"]
            for r in conn.execute(
                "SELECT run_id FROM run_prompts WHERE stage = ? AND version = ?",
                (stage, version["id"]),
            )
        }
        given = Labels(conn, version, inputs)
        weeks: list[Sheet] = []
        for run in complete_runs(conn):
            only = changed_developments(conn, run)
            run_given = None
            if run not in used:
                run_given = {
                    (p.development_id, p.subject_id): label
                    for p in run_pairs(conn, run)
                    if (label := given.get(p)) is not None
                }
            rows = direct_rows(conn, names, inputs.subjects, run, only, run_given)
            if rows:
                weeks.append(Sheet(run, DIRECT_COLUMNS, rows))
        runs = complete_runs(conn)
        entities = dataset_entities(conn, inputs.subjects)
    finally:
        conn.close()
    about = _about(
        [
            ("Stage", stage),
            ("Version", version["number"]),
            ("Id", version["id"]),
            ("Note", version["note"]),
            ("Model", version["model"]),
            ("Effort", version["effort"] or "the model's own"),
            ("First used", version["first_used"][:10]),
            ("Dataset", store.stem.removeprefix("intelmonitor-")),
            ("Entities", f"{entities}, tagged in the documents of the runs"),
            ("Runs", f"{runs[0]} to {runs[-1]}" if runs else "none"),
            ("Written", date.today()),
            ("Prompt", version["template"]),
        ]
    )
    summary = Sheet(
        "Summary",
        [("Run date", 11), ("Pairs", 8), ("High", 8), ("Medium", 8), ("Low", 8)],
        [
            [
                _day(w.name),
                len(w.rows),
                *(
                    sum(r[LEVEL_COLUMN] == label for r in w.rows)
                    for label in ("High", "Medium", "Low")
                ),
            ]
            for w in weeks
        ],
    )
    sheets = [about, summary, *weeks]
    write_workbook(sheets, out)
    return {s.name: len(s.rows) for s in sheets}


def link_ratings_workbook(out: Path) -> dict[str, int]:
    """Link rating version 1's workbook, the review sheet: one row per rating in
    transmission.yaml as it stands, named from relationships.yaml and the seed. transmission.yaml
    records no prompt versions yet, so every rating in it is version 1. The row count."""
    data = load_relationships()
    names = {e.id: e.name for e in data.endpoints}
    names.update({i: s.name for i, s in all_subjects().items()})
    links = {r.id: r for r in data.relationships}
    rows: list[list[Cell]] = []
    for e in load_ratings().values():
        from_, type_, to = e.id.split(":")
        r = links.get(e.id)
        rows.append(
            [
                e.id,
                names.get(from_, from_),
                type_,
                names.get(to, to),
                r.reason if r else None,
                e.from_to.strength,
                e.from_to.reason,
                e.to_from.strength,
                e.to_from.reason,
                e.rated_by,
                e.model,
                e.date,
            ]
        )
    sheet = Sheet("Links", [*LINK_COLUMNS, ("Model", 18), ("Date", 11)], rows)
    write_workbook([sheet], out)
    return {sheet.name: len(rows)}


def compare_workbook(c: Comparison, out: Path) -> dict[str, int]:
    """Two versions compared: the count table of level under a against level under b, then a
    sheet per run with the pairs whose level moved, both levels and both reasons."""
    a, b = f"v{c.a['number']}", f"v{c.b['number']}"
    table = count_table(c)
    counts = Sheet("Counts", [(str(h), 12) for h in table[0]], [list(r) for r in table[1:]])
    weeks = [
        Sheet(
            run,
            [
                ("Development id", 20),
                ("Subject", 24),
                (f"Level {a}", 9),
                (f"Reason {a}", 70),
                (f"Level {b}", 9),
                (f"Reason {b}", 70),
            ],
            [
                [
                    m.pair.development_id,
                    c.names.get(m.pair.subject_id, m.pair.subject_id),
                    PRIORITY_LABELS[m.a.level],
                    m.a.reason,
                    PRIORITY_LABELS[m.b.level],
                    m.b.reason,
                ]
                for m in moved
            ],
        )
        for run, moved in sorted(c.moved.items())
    ]
    sheets = [counts, *weeks]
    write_workbook(sheets, out)
    return {s.name: len(s.rows) for s in sheets}
