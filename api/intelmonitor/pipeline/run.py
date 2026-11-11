"""`im run`: compute a pipeline run as of a date, from the documents published from the corpus
start (meta `corpus_start`) to that date. Documents stored from before the corpus start stay in
the store but outside every run. `im weekly` ingests and computes each complete week in turn, and
`im clear-runs` deletes every run before a rebuild.

Every model call is cached in inference_records by its input, and every derived table is rebuilt
from those calls, so rerunning after a failure resumes where it stopped.

Recomputing a complete run keeps it complete while it runs: each step swaps its tables in one
transaction, so the app keeps serving the run instead of falling back to an older one.

Order (phase1-plan.md, section 5): sync the relationship store, detectors, screen, extract, merge
and priority, headlines, indirect impact by table, upcoming dates, indicators and notes, diff,
briefs, graph.
"""

import sqlite3
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from intelmonitor.ingest.models import corpus_start
from intelmonitor.pipeline import runlog
from intelmonitor.pipeline.corroboration import shown_developments
from intelmonitor.pipeline.detectors import store_detections
from intelmonitor.pipeline.developments import build_developments
from intelmonitor.pipeline.diff import development_changes, previous_run, store_changes
from intelmonitor.pipeline.extract import extract_documents
from intelmonitor.pipeline.graph import store_graph
from intelmonitor.pipeline.headlines import write_headlines
from intelmonitor.pipeline.impact import ImpactStats, store_impacts
from intelmonitor.pipeline.indicators import store_run_indicators
from intelmonitor.pipeline.inference import Inference
from intelmonitor.pipeline.notes import write_notes
from intelmonitor.pipeline.params import INDIRECT_FROM_LOW, PRIORITY_LABELS, RUN_SPACING_DAYS
from intelmonitor.pipeline.screen import screen_documents
from intelmonitor.pipeline.subjects import monitored_subjects
from intelmonitor.pipeline.summaries import write_summaries
from intelmonitor.pipeline.upcoming import store_upcoming
from intelmonitor.relationships import sync_relationships
from intelmonitor.store.db import connect, get_meta
from intelmonitor.store.documents import list_documents

# Entities whose indirect row counts each run prints, for the Low direct item decision.
INDIRECT_REPORTED = ("dbs", "ocbc", "uob", "mas")
INDIRECT_LOW_SAMPLES = 10

# Tables with a run_id column that `im clear-runs` keeps: every model call (replayed by later
# runs) and the execution history.
KEPT_RUN_TABLES = ("inference_records", "run_log")


def run_dates(conn: sqlite3.Connection) -> tuple[date, date]:
    """Run 1 (T0 minus RUN_SPACING_DAYS) and Run 2 (T0), from the T0 stored by ingest."""
    stored = get_meta(conn, "t0")
    if stored is None:
        raise RuntimeError("no T0 in the store: run `im ingest` first")
    t0 = date.fromisoformat(stored)
    return t0 - timedelta(days=RUN_SPACING_DAYS), t0


async def compute_run(
    as_of: date,
    log: Callable[[str], None] = print,
    trigger: runlog.Trigger = "cli",
    *,
    conn: sqlite3.Connection | None = None,
    recorded_only: bool = False,
) -> None:
    """Computes the run in the app's store, or in `conn`. With `recorded_only`, every model call
    must replay a recorded one (`Inference`)."""
    conn = conn or connect()
    run_id = as_of.isoformat()
    # The relationship store first: it commits any open transaction, so before the run's writes.
    synced = sync_relationships(conn)
    # Detectors write their events as data documents first, so the run takes them like the rest.
    detected = store_detections(conn, as_of)
    start = corpus_start(conn) or date.min
    docs = [d for d in list_documents(conn) if start <= d.published_at.date() <= as_of]
    # Data documents come with their findings; only the others are screened and extracted.
    texts = [d for d in docs if d.collection != "data"]
    started = time.monotonic()
    started_at = datetime.now(UTC).isoformat()
    existing = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
    recompute = existing is not None and existing["status"] == "complete"
    execution_id = runlog.start(conn, run_id, trigger, len({d.source for d in docs}), len(docs))
    if recompute:
        conn.execute("UPDATE runs SET documents = ? WHERE id = ?", (len(docs), run_id))
    else:
        conn.execute(
            "INSERT OR REPLACE INTO runs (id, as_of, status, documents, started_at) "
            "VALUES (?, ?, 'running', ?, ?)",
            (run_id, run_id, len(docs), started_at),
        )
    conn.commit()
    inf = Inference(conn, run_id, recorded_only=recorded_only)

    def step(name: str) -> None:
        log(
            f"[{run_id}] {name} ({time.monotonic() - started:.0f}s, "
            f"{inf.calls} calls, {inf.cache_hits} cached)"
        )

    try:
        step(f"relationships: {len(synced.relationships)} links, {len(synced.endpoints)} endpoints")
        step(
            f"detectors: {detected.events} events in the corpus window "
            f"{dict(detected.by_detector)}, {detected.added} new documents"
        )
        step(f"screen {len(texts)} documents published {start} to {as_of}")
        screens = await screen_documents(inf, conn, texts)
        relevant = sum(s.relevant for s in screens.values())
        to_extract = sum(s.relevant and bool(s.subjects) for s in screens.values())
        step(
            f"screened {len(screens)} documents, {relevant} relevant "
            f"({relevant / max(len(screens), 1):.0%}); extract from the {to_extract} relevant "
            f"documents with a tagged subject"
        )
        _, ex = await extract_documents(inf, conn, texts, screens)
        step(
            f"extracted {ex.kept}/{ex.proposed} findings, "
            f"{ex.quotes_verified}/{ex.quotes_proposed} quotes verified; merge"
        )
        devs, merge = await build_developments(inf, conn, run_id, as_of, start)
        step(
            f"{merge.developments} developments from {merge.findings} findings "
            f"({merge.merged_pairs}/{merge.pairs} candidate pairs merged), {merge.high} High; "
            f"left out {merge.promotional} promotional and {merge.price_move} price-move findings; "
            f"{merge.priority_refused} priority ratings refused"
        )
        heads = await write_headlines(inf, conn, run_id)
        step(
            f"headlines: {heads.written} written, {heads.from_code} from detectors, "
            f"{heads.refused} refused"
        )
        impact = store_impacts(conn, run_id)
        step(
            f"impact by table: {impact.impacts} indirect impacts from {impact.developments} "
            f"developments (INDIRECT_FROM_LOW {INDIRECT_FROM_LOW}); {impact.without_low} rows "
            f"without Low direct items, {impact.with_low} with them"
        )
        for line in [*_impact_lines(conn, run_id, impact), *_topic_only_lines(conn, run_id)]:
            log(f"[{run_id}] {line}")
        upcoming = store_upcoming(conn, run_id, as_of, devs)
        base_id = previous_run(conn, run_id)
        changes = development_changes(conn, run_id, base_id)
        previous_as_of = date.fromisoformat(base_id) if base_id else None
        states = store_run_indicators(conn, run_id, as_of, previous_as_of)
        step(f"{upcoming} upcoming dates; indicators {dict(states)}")
        noted, unnoted, notes_refused = await write_notes(inf, conn, run_id, as_of, base_id)
        step(f"{noted} notes, {unnoted} still breaking their rules, {notes_refused} refused")
        if base_id:
            step(f"{store_changes(conn, run_id, base_id, changes)} changes vs {base_id}")
        conn.execute("UPDATE runs SET previous_run_id = ? WHERE id = ?", (base_id, run_id))
        briefs, unbriefed, briefs_refused = await write_summaries(
            inf, conn, run_id, as_of, base_id, changes
        )
        step(f"{briefs} briefs, {unbriefed} still breaking their rules, {briefs_refused} refused")
        nodes, edges = await store_graph(inf, conn, run_id, changes)
        step(f"graph {nodes} nodes, {edges} edges")
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        runlog.fail(conn, execution_id, message)
        if not recompute:
            conn.execute(
                "UPDATE runs SET status = 'failed', finished_at = ?, error = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), message, run_id),
            )
            conn.commit()
        raise
    conn.execute(
        "UPDATE runs SET status = 'complete', started_at = ?, finished_at = ?, error = NULL "
        "WHERE id = ?",
        (started_at, datetime.now(UTC).isoformat(), run_id),
    )
    conn.commit()
    # Like the run's stored changes, the counts compare with the previous as-of date, so a rerun
    # of a date reports what the date found, not what the rerun changed.
    outcome = runlog.outcome(conn, run_id, changes, base_id is None)
    runlog.finish(conn, execution_id, outcome)
    previous = date.fromisoformat(base_id) if base_id else None
    step(f"complete: {runlog.result_line(outcome, previous)}")


def _impact_lines(conn: sqlite3.Connection, run_id: str, impact: ImpactStats) -> list[str]:
    """The indirect row counts without and with Low direct items, in total and for
    INDIRECT_REPORTED, over every development and over the shown ones (those the source gate
    passes, the only ones a page shows); then up to INDIRECT_LOW_SAMPLES rows that only Low
    direct items reach, one per development. A subject no longer in the seed shows by its id."""
    subjects = monitored_subjects()
    shown = shown_developments(conn, run_id)

    def name(subject_id: str) -> str:
        return subjects[subject_id].name if subject_id in subjects else subject_id

    def counts(only_shown: bool) -> str:
        def n(low: bool, subject_id: str | None = None) -> int:
            return sum(
                (subject_id is None or s == subject_id) and (not only_shown or dev in shown)
                for dev, s in impact.pairs[low]
            )

        by = [f"{name(s)} {n(False, s)}/{n(True, s)}" for s in INDIRECT_REPORTED]
        return ", ".join([f"total {n(False)}/{n(True)}", *by])

    lines = [
        f"indirect rows without/with Low direct items, all developments: {counts(False)}",
        f"indirect rows without/with Low direct items, shown developments only: {counts(True)}",
    ]
    seen: set[str] = set()
    for dev_id, r in impact.low_only:
        if dev_id in seen or len(seen) == INDIRECT_LOW_SAMPLES:
            continue
        seen.add(dev_id)
        title = conn.execute(
            "SELECT title FROM developments WHERE run_id = ? AND id = ?", (run_id, dev_id)
        ).fetchone()["title"]
        gate = "" if dev_id in shown else " (unconfirmed, not shown)"
        lines.append(
            f"  Low adds {name(r.subject_id)}, {PRIORITY_LABELS[r.level]}, from "
            f"{name(r.source_id)} by {r.relationship.type} ({r.strength}): "
            f"{dev_id} {title[:90]}{gate}"
        )
    return lines


def _topic_only_lines(conn: sqlite3.Connection, run_id: str) -> list[str]:
    """Per topic, the developments tagged with it that name no entity, by level: the topic's own
    rating is their only priority. Each count is over every development, then over the shown
    ones (those the source gate passes, the only ones a page shows). A subject no longer in the
    seed counts as a topic and shows by its id."""
    names = monitored_subjects()
    shown = shown_developments(conn, run_id)
    tagged: dict[str, list[tuple[str, int]]] = {}
    for r in conn.execute(
        "SELECT development_id, subject_id, priority_level FROM development_subjects "
        "WHERE run_id = ?",
        (run_id,),
    ):
        tagged.setdefault(r["development_id"], []).append(
            (r["subject_id"], int(r["priority_level"]))
        )
    # topic -> (all developments, shown ones), each counted by level
    levels: dict[str, tuple[Counter[int], Counter[int]]] = {}
    developments, shown_count = 0, 0
    for dev_id, subjects in tagged.items():
        if any(s in names and names[s].type == "entity" for s, _ in subjects):
            continue
        developments += 1
        shown_count += dev_id in shown
        for s, level in subjects:
            every, visible = levels.setdefault(s, (Counter[int](), Counter[int]()))
            every[level] += 1
            visible[level] += dev_id in shown

    def by_level(by: Counter[int]) -> str:
        return f"{by.total()} ({', '.join(f'{PRIORITY_LABELS[i]} {by[i]}' for i in (2, 1, 0))})"

    lines = [
        f"developments naming no entity: {developments} in all, {shown_count} shown; "
        "per topic, all then shown:"
    ]
    for topic_id, (every, visible) in sorted(levels.items(), key=lambda item: -item[1][0].total()):
        name = names[topic_id].name if topic_id in names else topic_id
        lines.append(f"  {name}: all {by_level(every)}; shown {by_level(visible)}")
    return lines


def weekly_dates(conn: sqlite3.Connection, today: date) -> list[date]:
    """The Sundays of the complete weeks (Monday to Sunday UTC, ended before `today`) after the
    latest complete run, or from the corpus start when there is none, oldest first."""
    last = conn.execute("SELECT max(as_of) FROM runs WHERE status = 'complete'").fetchone()[0]
    if last is not None:
        start = date.fromisoformat(last) + timedelta(days=1)
    elif (stored := corpus_start(conn)) is not None:
        start = stored
    else:
        raise RuntimeError("no run and no corpus start in the store: set meta corpus_start")
    sunday = start + timedelta(days=(6 - start.weekday()) % 7)
    weeks: list[date] = []
    while sunday < today:
        weeks.append(sunday)
        sunday += timedelta(days=7)
    return weeks


def run_tables(conn: sqlite3.Connection) -> list[str]:
    """The tables holding per-run rows: every table with a run_id column but KEPT_RUN_TABLES."""
    return [
        r["name"]
        for r in conn.execute(
            "SELECT m.name FROM sqlite_master m, pragma_table_info(m.name) p "
            "WHERE m.type = 'table' AND p.name = 'run_id' ORDER BY m.name"
        )
        if r["name"] not in KEPT_RUN_TABLES
    ]


def clear_runs(conn: sqlite3.Connection) -> dict[str, int]:
    """Delete every run and its per-run rows in one transaction. Documents and their per-document
    rows, inference_records, series and run_log stay. Returns the rows deleted per table."""
    deleted: dict[str, int] = {}
    with conn:
        for table in [*run_tables(conn), "runs"]:
            deleted[table] = conn.execute(f"DELETE FROM {table}").rowcount  # noqa: S608
    return deleted
