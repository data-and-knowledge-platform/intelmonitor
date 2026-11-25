"""`im run`: compute a pipeline run as of a date, from the documents published from the corpus
start (meta `corpus_start`) to that date. Documents stored from before the corpus start stay in
the store but outside every run. `im weekly` ingests and computes each complete week in turn, and
`im clear-runs` deletes every run before a rebuild (`im weekly --publish --rerun` does the same on
the published store). `im impacts` rewrites only the inferred impacts of stored runs.

Every model call is cached in inference_records by its input, and every derived table is rebuilt
from those calls, so rerunning after a failure resumes where it stopped.

Recomputing a complete run keeps it complete while it runs: each step swaps its tables in one
transaction, so the app keeps serving the run instead of falling back to an older one.

Order (phase1-plan.md, section 5): sync the relationship store, detectors, screen, extract, merge,
headlines with the development's subjects, priority, the activated link types, inferred impact by
table, upcoming dates, indicators, diff, briefs.
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
from intelmonitor.pipeline.impact import (
    SETTINGS,
    STORED,
    ImpactStats,
    Reached,
    Setting,
    store_impacts,
)
from intelmonitor.pipeline.indicators import store_run_indicators
from intelmonitor.pipeline.inference import Inference
from intelmonitor.pipeline.params import NEEDS_ATTENTION_LEVEL, PRIORITY_LABELS, RUN_SPACING_DAYS
from intelmonitor.pipeline.screen import screen_documents
from intelmonitor.pipeline.subjects import monitored_subjects
from intelmonitor.pipeline.summaries import write_summaries
from intelmonitor.pipeline.upcoming import store_upcoming
from intelmonitor.relationships import sync_relationships
from intelmonitor.store.db import RERUN, RERUN_ROLLBACK, connect, get_meta
from intelmonitor.store.documents import list_documents

# Entities whose inferred row counts each run prints, for the picks of the two switches (P6, P7),
# and the sample rows it prints for each switch.
INDIRECT_REPORTED = ("dbs", "ocbc", "uob", "mas")
INDIRECT_SAMPLES = 10

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
    write_briefs: bool = True,
) -> None:
    """Computes the run in the app's store, or in `conn`. With `recorded_only`, every model call
    must replay a recorded one (`Inference`). Without `write_briefs`, the run's stored briefs are
    kept as they are (`im check past-run`, on a run whose briefs predate `im impacts`)."""
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
    inf = Inference(conn, run_id, recorded_only=recorded_only, rebuilds=True)

    def step(name: str) -> None:
        log(
            f"[{run_id}] {name} ({time.monotonic() - started:.0f}s, "
            f"{inf.calls} calls, {inf.cache_hits} cached)"
        )

    try:
        step(f"relationships: {len(synced.relationships)} links")
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
        heads = merge.headlines
        step(
            f"headlines: {heads.written} written, {heads.from_code} from detectors, "
            f"{heads.refused} refused; {heads.dropped} subjects of {heads.narrowed} developments "
            "made mentions"
        )
        step(
            f"{merge.developments} developments from {merge.findings} findings "
            f"({merge.merged_pairs}/{merge.pairs} candidate pairs merged), {merge.high} High; "
            f"left out {merge.promotional} promotional and {merge.price_move} price-move findings; "
            f"{merge.priority_refused} priority ratings refused"
        )
        step(
            f"link types: {merge.link_types_asked} developments with an entity at Medium or "
            f"High{' or Low' if STORED.from_low else ''}"
            f"{', adverse for one of them,' if STORED.adverse_only else ''} asked, "
            f"{merge.link_types_refused} "
            "refused (their entities activate no type)"
        )
        impact = store_impacts(conn, run_id)
        step(impact_step(impact))
        for line in [*impact_lines(conn, run_id, impact), *_topic_only_lines(conn, run_id)]:
            log(f"[{run_id}] {line}")
        upcoming = store_upcoming(conn, run_id, as_of, devs)
        base_id = previous_run(conn, run_id)
        changes = development_changes(conn, run_id, base_id)
        previous_as_of = date.fromisoformat(base_id) if base_id else None
        states = store_run_indicators(conn, run_id, as_of, previous_as_of)
        step(f"{upcoming} upcoming dates; indicators {dict(states)}")
        if base_id:
            step(f"{store_changes(conn, run_id, base_id, changes)} changes vs {base_id}")
        conn.execute("UPDATE runs SET previous_run_id = ? WHERE id = ?", (base_id, run_id))
        if write_briefs:
            briefs, unbriefed, briefs_refused = await write_summaries(
                inf, conn, run_id, as_of, base_id, changes
            )
            step(
                f"{briefs} briefs, {unbriefed} still breaking their rules, {briefs_refused} refused"
            )
        else:
            step("briefs kept as stored")
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


def impact_step(impact: ImpactStats) -> str:
    """The impact step's line: the stored paths, the development and entity pairs they reach (one
    best path each), the developments they come from, and the named entities at Medium or High
    with their types and with none asked, so a run from before the link_types stage, which finds
    no path, does not read as one where the rules found nothing."""
    stored = impact.stored
    line = (
        f"inferred impact by table: {len(stored)} paths, {sum(r.best for _, r in stored)} "
        f"development and entity pairs reached, from {len({d for d, _ in stored})} developments "
        f"(INDIRECT_ADVERSE_ONLY {STORED.adverse_only}, INDIRECT_FROM_LOW {STORED.from_low}); "
        f"named entities at Medium or High, each per development: {impact.typed} with their "
        f"types, {impact.unasked} not asked"
    )
    if impact.unasked and not impact.typed:
        line += " (the link_types stage has not run on this run, so nothing passes)"
    return line


def impact_lines(conn: sqlite3.Connection, run_id: str, impact: ImpactStats) -> list[str]:
    """The inferred rows under each setting of the two switches, as development and entity pairs
    reached (best paths) and paths, in total and for INDIRECT_REPORTED, over every development
    and over the shown ones (those the source gate passes, the only ones a page shows); the
    stored setting's paths that are not best; its inferred High rows, every one; then up to
    INDIRECT_SAMPLES rows that flipping each switch adds or removes, one per development. With no
    entity at Low asked for its types (the link_types stage asks them only with INDIRECT_FROM_LOW
    on), the Low row's counts and samples would equal those without it by construction, so it
    says they are not counted instead. With no development adverse for none of its entities
    asked (the stage asks those only with INDIRECT_ADVERSE_ONLY off), the counts and samples with
    the adverse rule off would lack them, so it says those are not counted either. A subject no
    longer in the seed shows by its id."""
    subjects = monitored_subjects()
    shown = shown_developments(conn, run_id)
    titles = {
        r["id"]: r["title"]
        for r in conn.execute("SELECT id, title FROM developments WHERE run_id = ?", (run_id,))
    }

    def name(subject_id: str) -> str:
        return subjects[subject_id].name if subject_id in subjects else subject_id

    def counts(setting: Setting, only_shown: bool) -> str:
        rows = [(d, r) for d, r in impact.paths[setting] if not only_shown or d in shown]

        def n(subject_id: str | None = None) -> str:
            mine = [r for _, r in rows if subject_id is None or r.subject_id == subject_id]
            return f"{sum(r.best for r in mine)}/{len(mine)}"

        return ", ".join([f"total {n()}", *(f"{name(s)} {n(s)}" for s in INDIRECT_REPORTED)])

    def label(s: Setting) -> str:
        words = (
            f"adverse gate {'on' if s.adverse_only else 'off'}, "
            f"Low row {'on' if s.from_low else 'off'}"
        )
        return words + (" (stored)" if s == STORED else "")

    def row(dev_id: str, r: Reached) -> str:
        gate = "" if dev_id in shown else " (unconfirmed, not shown)"
        return (
            f"{name(r.subject_id)}, {PRIORITY_LABELS[r.level]}, from {name(r.source_id)} by "
            f"{r.relationship.type} ({r.strength}): {dev_id} {titles[dev_id][:90]}{gate}"
        )

    def uncounted(s: Setting) -> str | None:
        """Why the setting's paths cannot be counted, or None when they can."""
        if s.from_low and not impact.low_asked:
            return (
                "not counted, no entity at Low has its types (link_types asks them only with "
                "INDIRECT_FROM_LOW on)"
            )
        if not s.adverse_only and not impact.calm_asked:
            return (
                "not counted, no development adverse for none of its entities was asked for "
                "types (link_types asks those only with INDIRECT_ADVERSE_ONLY off)"
            )
        return None

    lines: list[str] = []
    for only_shown in (False, True):
        scope = "shown developments only" if only_shown else "all developments"
        lines.append(f"inferred rows, development and entity pairs reached/paths, {scope}:")
        lines += [f"  {label(s)}: {uncounted(s) or counts(s, only_shown)}" for s in SETTINGS]
    stored = impact.stored
    lines.append(f"paths not best: {sum(not r.best for _, r in stored)}")
    high = [(d, r) for d, r in stored if r.best and r.level == NEEDS_ATTENTION_LEVEL]
    lines.append(f"inferred High rows: {len(high)}, {sum(d in shown for d, _ in high)} shown")
    lines += [f"  {row(d, r)}" for d, r in high]
    flips = [
        ("Low row on adds", STORED._replace(from_low=False), STORED._replace(from_low=True)),
        (
            "adverse gate on drops",
            STORED._replace(adverse_only=True),
            STORED._replace(adverse_only=False),
        ),
    ]
    for words, fewer, more in flips:
        if why := uncounted(fewer) or uncounted(more):
            lines.append(f"  {words}: {why}")
            continue
        kept = {(d, r.subject_id) for d, r in impact.paths[fewer] if r.best}
        seen: set[str] = set()
        for d, r in impact.paths[more]:
            if not r.best or (d, r.subject_id) in kept or d in seen:
                continue
            if len(seen) == INDIRECT_SAMPLES:
                break
            seen.add(d)
            lines.append(f"  {words} {row(d, r)}")
    return lines


def recompute_impacts(
    conn: sqlite3.Connection, run_ids: list[str], log: Callable[[str], None] = print
) -> None:
    """`im impacts`: rewrites the inferred impacts of complete runs from their stored
    development_subjects (levels, adverse flags, activated types) and the relationship store,
    synced from relationships.yaml first as a run does, and sets each run's impacts_recomputed
    mark. No other stage runs and no model is called: the briefs keep the impacts they were
    written from until `im summarize`. Prints what the run's impact step prints."""
    synced = sync_relationships(conn)
    log(f"relationships: {len(synced.relationships)} links")
    at = datetime.now(UTC).isoformat()
    for run_id in run_ids:
        impact = store_impacts(conn, run_id, recomputed_at=at)
        for line in [impact_step(impact), *impact_lines(conn, run_id, impact)]:
            log(f"[{run_id}] {line}")


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


def clear_runs(
    conn: sqlite3.Connection, *, rerun: str | None = None, rollback: str | None = None
) -> dict[str, int]:
    """Delete every run and its per-run rows in one transaction. Documents and their per-document
    rows, inference_records, series and run_log stay. Returns the rows deleted per table.
    `im weekly --rerun` passes its label as `rerun` and the key of the published store it pulled
    as `rollback` (None from an empty bucket); the same transaction records them as meta RERUN
    and RERUN_ROLLBACK, so the store never holds the label without the clear."""
    deleted: dict[str, int] = {}
    with conn:
        for table in [*run_tables(conn), "runs"]:
            deleted[table] = conn.execute(f"DELETE FROM {table}").rowcount  # noqa: S608
        if rerun is not None:
            conn.execute("DELETE FROM meta WHERE key IN (?, ?)", (RERUN, RERUN_ROLLBACK))
            conn.executemany(
                "INSERT INTO meta (key, value) VALUES (?, ?)",
                [(k, v) for k, v in ((RERUN, rerun), (RERUN_ROLLBACK, rollback)) if v is not None],
            )
    return deleted
