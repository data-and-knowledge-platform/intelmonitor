"""`im export labels`: the labels the runs gave, as an Excel workbook for the team's audit
(ticket 24). It opens the store read-only, so it runs no migration and writes nothing there, and
it calls no model.

Sheets:
- Direct: one row per development and entity it is about, in every complete run. A development
  that names no entity gets one row per topic instead, since each such topic is rated itself.
- Indirect: one row per indirect impact (development_impacts), in every complete run.
- Links: the step 2a review sheet, one row per rated link in the store's copy of
  relationships.yaml (every type but belongs_to_sector), with `rated_by` from transmission.yaml.
- Dropped: the findings the latest complete run left out as promotional or price moves. A run
  reads every document from the corpus start to its date, so the latest run's set holds every
  earlier run's.
"""

import sqlite3
from collections import defaultdict
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
from intelmonitor.pipeline.subjects import all_subjects
from intelmonitor.relationships import Relationship, load_ratings, rateable

Cell = str | int | bool | date | None


@dataclass
class Sheet:
    name: str
    columns: list[tuple[str, int]]  # header and column width in characters
    rows: list[list[Cell]]


def connect_readonly(path: Path) -> sqlite3.Connection:
    """Open the store read-only: no schema or migration runs, and any write raises."""
    if not path.exists():
        raise FileNotFoundError(f"no store at {path}")
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _names(conn: sqlite3.Connection) -> dict[str, str]:
    """Display names for every id a label can name: the store's link endpoints and the seed's
    topics, groups and entities."""
    names = {
        r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM relationship_endpoints")
    }
    names.update({i: s.name for i, s in all_subjects().items()})
    return names


def _complete_runs(conn: sqlite3.Connection) -> list[str]:
    """Complete run ids, oldest first; a run still computing or failed is left out."""
    return [
        r["id"] for r in conn.execute("SELECT id FROM runs WHERE status = 'complete' ORDER BY id")
    ]


def _day(iso: str) -> date:
    return date.fromisoformat(iso[:10])


def _per_development(
    conn: sqlite3.Connection, sql: str, runs: list[str]
) -> dict[tuple[str, str], list[sqlite3.Row]]:
    """Rows of `sql`, which selects run_id and development_id among its columns and ends in
    `run_id IN (...)` for the given runs, keyed by (run_id, development_id)."""
    out: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for r in conn.execute(sql.format(runs=",".join("?" * len(runs))), runs):
        out[(r["run_id"], r["development_id"])].append(r)
    return out


def direct_sheet(conn: sqlite3.Connection, names: dict[str, str]) -> Sheet:
    subjects = all_subjects()
    runs = _complete_runs(conn)
    devs = conn.execute(
        "SELECT run_id, id, coalesce(headline, title) AS headline, category, first_published, "
        "last_published FROM developments "
        f"WHERE run_id IN ({','.join('?' * len(runs))}) ORDER BY run_id, id",  # noqa: S608
        runs,
    ).fetchall()
    tagged = _per_development(
        conn,
        "SELECT run_id, development_id, subject_id, adverse, priority_level, priority_reason "
        "FROM development_subjects WHERE run_id IN ({runs}) ORDER BY subject_id",
        runs,
    )
    sources = _per_development(
        conn,
        "SELECT s.run_id, s.development_id, s.finding_id, s.document_id, d.collection, "
        "d.published_at, d.url FROM development_sources s "
        "JOIN source_documents d ON d.id = s.document_id WHERE s.run_id IN ({runs})",
        runs,
    )
    rows: list[list[Cell]] = []
    for d in devs:
        key = (d["run_id"], d["id"])
        # Development subjects are entities and topics; one that left the seed counts as an entity.
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
            rows.append(
                [
                    _day(d["run_id"]),
                    d["id"],
                    d["headline"],
                    names.get(s["subject_id"], s["subject_id"]),
                    "entity" if entities else "topic",
                    ", ".join(names[t["subject_id"]] for t in topics),
                    PRIORITY_LABELS[s["priority_level"]],
                    s["priority_reason"],
                    bool(s["adverse"]),
                    d["category"],
                    len({x["document_id"] for x in docs}),
                    _day(d["first_published"]),
                    _day(d["last_published"]),
                    top["url"] if top else None,
                ]
            )
    return Sheet(
        "Direct",
        [
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
        ],
        rows,
    )


def indirect_sheet(conn: sqlite3.Connection, names: dict[str, str]) -> Sheet:
    runs = _complete_runs(conn)
    found = conn.execute(
        "SELECT i.run_id, i.development_id, coalesce(d.headline, d.title) AS headline, "
        "i.source_id, s.priority_level AS source_level, i.subject_id, i.relationship_id, "
        "i.region, i.strength, i.level, r.data AS link FROM development_impacts i "
        "JOIN developments d ON d.run_id = i.run_id AND d.id = i.development_id "
        "LEFT JOIN development_subjects s ON s.run_id = i.run_id "
        "AND s.development_id = i.development_id AND s.subject_id = i.source_id "
        "LEFT JOIN relationships r ON r.id = i.relationship_id "
        f"WHERE i.run_id IN ({','.join('?' * len(runs))}) "  # noqa: S608
        "ORDER BY i.run_id, i.development_id, i.subject_id",
        runs,
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
    return Sheet(
        "Links",
        [
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
        ],
        rows,
    )


def dropped_sheet(conn: sqlite3.Connection, names: dict[str, str]) -> Sheet:
    runs = _complete_runs(conn)
    rows: list[list[Cell]] = []
    if runs:
        latest = _day(runs[-1])
        dropped = [
            f
            for f in load_findings(conn, latest, corpus_start(conn))
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
    conn = connect_readonly(store)
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
