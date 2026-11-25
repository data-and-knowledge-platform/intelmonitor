"""`im` command line: uv run im <command>."""

import asyncio
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

if TYPE_CHECKING:
    from intelmonitor.store.publish import Bucket
    from intelmonitor.trial import Pair

app = typer.Typer(no_args_is_help=True, add_completion=False)
series_app = typer.Typer(no_args_is_help=True, help="Fetch measured series (api/seed/series.yaml).")
app.add_typer(series_app, name="series")
check_app = typer.Typer(no_args_is_help=True, help="Checks the walkthrough runs on fixture data.")
app.add_typer(check_app, name="check")
export_app = typer.Typer(no_args_is_help=True, help="Export what the runs stored, for the team.")
app.add_typer(export_app, name="export")
trial_app = typer.Typer(no_args_is_help=True, help="Try a prompt on a store's pairs.")
app.add_typer(trial_app, name="trial")
prompts_app = typer.Typer(help="Prompt versions: list, show and compare.")
app.add_typer(prompts_app, name="prompts")

# The --store option of the prompt version commands: a path to a store, the app's by default.
StoreOption = Annotated[
    str | None, typer.Option("--store", help="The store's .db file; the app's store by default")
]


def _store(store: str | None) -> Path:
    from intelmonitor.store.db import DB_PATH

    path = Path(store) if store else DB_PATH
    if not path.exists():
        raise typer.BadParameter(f"no store at {path}")
    return path


def _open(store: str | None, *, write: bool = False) -> sqlite3.Connection:
    """The store at --store opened as it is, read-only unless `write`: no schema or migration
    runs, so a frozen store such as the baseline keeps its schema. It must hold the prompt
    version tables as the code has them."""
    from intelmonitor.store.db import PROMPT_VERSION_TABLES, StoreLacks, open_store

    try:
        return open_store(_store(store), PROMPT_VERSION_TABLES, write=write)
    except StoreLacks as error:
        raise typer.BadParameter(str(error)) from error


@app.command()
def ingest(
    stats: Annotated[bool, typer.Option("--stats", help="Only print corpus stats")] = False,
    t0: Annotated[
        str | None, typer.Option("--t0", help="Override the stored T0, YYYY-MM-DD")
    ] = None,
    seed: Annotated[
        bool,
        typer.Option(
            "--seed",
            help="Search the 60 days ending at T0 instead of the week: the history for the first "
            "runs, or with --subjects for newly added subjects",
        ),
    ] = False,
    subjects: Annotated[
        str | None,
        typer.Option("--subjects", help="With --seed: comma-separated subject ids"),
    ] = None,
) -> None:
    """Fetch the measured series, then fetch, cache, parse and dedup the week's corpus (60 days
    with --seed) and search trends, and print its stats."""
    from datetime import date

    from intelmonitor.ingest.run import run_ingest
    from intelmonitor.ingest.series.collect import fetch_series
    from intelmonitor.ingest.stats import ingest_stats
    from intelmonitor.pipeline.subjects import all_subjects
    from intelmonitor.store.db import connect

    if subjects and not seed:
        raise typer.BadParameter("--subjects only applies with --seed")
    if not stats:
        fetch_series(connect(), date.today(), log=typer.echo)
        typer.echo("")
        ids = {s.strip() for s in subjects.split(",") if s.strip()} if subjects else None
        if unknown := (ids or set()) - set(all_subjects()):
            raise typer.BadParameter(f"unknown subjects: {', '.join(sorted(unknown))}")
        result = run_ingest(date.fromisoformat(t0) if t0 else None, seed, ids, log=typer.echo)
        typer.echo(
            f"added {sum(result.added.values())} {dict(result.added)}, "
            f"{result.duplicates} duplicates, {len(result.failures)} failures\n"
        )
    typer.echo(ingest_stats(connect()))


@app.command()
def redate() -> None:
    """Re-date stored documents whose URL carries a date more than 2 days earlier than the
    stored one (the ingest rule, for documents stored before it), removing those it dates before
    the T0 window. Recompute the runs that used them afterwards."""
    from intelmonitor.ingest.run import redate_stored
    from intelmonitor.store.db import connect

    changed = redate_stored(connect())
    for r in changed:
        action = "removed, before the window" if r.removed else "re-dated"
        typer.echo(f"{r.id} {r.was.date()} -> {r.now.date()} {action}: {r.title} ({r.url})")
    removed = sum(r.removed for r in changed)
    typer.echo(f"{len(changed) - removed} documents re-dated, {removed} removed")


@series_app.command("fetch")
def series_fetch() -> None:
    """Fetch every series up to today; cached responses make reruns within a day free."""
    from datetime import date

    from intelmonitor.ingest.series.collect import fetch_series
    from intelmonitor.store.db import connect

    fetch_series(connect(), date.today(), log=typer.echo)


@series_app.command("backfill")
def series_backfill(
    from_: Annotated[
        str, typer.Option("--from", help="Earliest period to keep, YYYY-MM-DD")
    ] = "2021-07-01",
) -> None:
    """Fetch every series' history from --from to today, and keep that floor for later fetches."""
    from datetime import date

    from intelmonitor.ingest.series.collect import fetch_series
    from intelmonitor.store.db import connect

    fetch_series(connect(), date.today(), backfill_from=date.fromisoformat(from_), log=typer.echo)


@app.command()
def run(
    as_of: Annotated[str | None, typer.Option("--as-of", help="Run date, YYYY-MM-DD")] = None,
    both: Annotated[bool, typer.Option("--both", help="Compute Run 1 and Run 2")] = False,
) -> None:
    """Compute one pipeline run, or both (Run 1 as of T0 - 7 days, then Run 2 as of T0)."""
    from datetime import date

    from intelmonitor.pipeline.run import compute_run, run_dates
    from intelmonitor.store.db import connect

    if both == (as_of is not None):
        raise typer.BadParameter("pass either --as-of <date> or --both")
    dates = list(run_dates(connect())) if both else [date.fromisoformat(str(as_of))]
    for d in dates:
        asyncio.run(compute_run(d, lambda line: typer.echo(line)))


# The runner's limit on one firing: past it, `im weekly --publish` exits non-zero, publishing
# nothing more, and the platform reports the failed run.
RUNNER_TIMEOUT_HOURS = 4


@app.command()
def weekly(
    max_weeks: Annotated[
        int, typer.Option("--max-weeks", min=1, help="Run at most this many weeks, oldest first")
    ] = 4,
    publish: Annotated[
        bool,
        typer.Option(
            "--publish",
            help="The runner: start from the published store, log runs as scheduled, publish "
            "after each week, and stop after 4 hours. Needs STORE_BUCKET and no local store",
        ),
    ] = False,
    start_from: Annotated[
        datetime | None,
        typer.Option(
            "--start-from",
            formats=["%Y-%m-%d"],
            help="With --publish, when nothing is published: start a new store with this date as "
            "its corpus start and run the complete weeks from it. Once a store is published it "
            "changes nothing, so it can stay on the command",
        ),
    ] = None,
    rerun: Annotated[
        str | None,
        typer.Option(
            "--rerun",
            help="With --publish: rerun every stored week once. Unless the pulled store records "
            "this label, delete its runs as `im clear-runs` does and record the label, so every "
            "week from the corpus start is due again. The label reaches the bucket with the "
            "first week published after, so the same label later changes nothing and a new "
            "label reruns again",
        ),
    ] = None,
) -> None:
    """For each complete week (Monday to Sunday UTC) since the last complete run, or since the
    corpus start, oldest first: ingest that week's window with the Sunday as T0, then compute its
    run, dated the Sunday. Fetches the measured series once first. Exits 0 when no week is due.
    Before the first week it checks the Exa and Claude keys with one search and one Haiku call.
    Exits 1 when a week left out a whole source (an official source not reached, most news
    searches failed, a series key missing or refused), after computing and publishing it. With
    --publish, an empty bucket is refused unless --start-from gives the corpus start: the date is
    the opt-in, so a mistyped bucket never starts a paid rebuild. --rerun deletes the pulled
    store's runs at most once per label. The label is recorded in the local store with the clear,
    in one transaction, and reaches the bucket only inside the first week published after it; a
    firing that stops before that publish leaves the bucket as it was, and the next one clears
    again. Until every due week is published, the site shows only the weeks rerun so far. The
    store published before the rerun is never pruned. Both options need --publish."""
    import threading
    from datetime import UTC, date, timedelta

    from intelmonitor.ingest.models import corpus_start, week_window
    from intelmonitor.ingest.run import run_ingest
    from intelmonitor.ingest.series.collect import fetch_series
    from intelmonitor.pipeline.run import clear_runs, compute_run, weekly_dates
    from intelmonitor.preflight import preflight
    from intelmonitor.store.db import DB_PATH, RERUN, connect, get_meta, set_meta
    from intelmonitor.store.publish import publish as publish_store

    today = datetime.now(UTC).date()
    if not publish and (start_from is not None or rerun is not None):
        raise typer.BadParameter("--start-from and --rerun only apply with --publish")
    if rerun is not None and not rerun.strip():
        raise typer.BadParameter("--rerun needs a label, such as the date it was asked for")
    start = start_from.date() if start_from is not None else None
    # A valid date stays valid, so the option can stay on the command; a future one is a typo.
    if start is not None and (first := start + timedelta(days=(6 - start.weekday()) % 7)) >= today:
        raise typer.BadParameter(
            f"--start-from {start}: its first week ends {first}, not before today ({today} UTC), "
            "so it gives no complete week"
        )

    bucket = _bucket() if publish else None
    published: str | None = None  # the key latest.json names, which each publish replaces
    if bucket is not None:
        if DB_PATH.exists():
            raise typer.BadParameter(
                f"--publish starts from the published store, and {DB_PATH} exists: set "
                "INTELMONITOR_DATA_DIR to an empty directory"
            )
        latest = bucket.latest()
        if latest is not None:
            bucket.download(latest, DB_PATH)
            published = latest.key
            typer.echo(f"pulled {latest.key} (run {latest.run_id})")
        elif start is None:
            raise typer.BadParameter(
                f"nothing published in {bucket.name}: pass --start-from <corpus start> to start "
                "a new store there, or run `im publish`"
            )

        def time_out() -> None:
            typer.echo(
                f"stopped after {RUNNER_TIMEOUT_HOURS} hours, mid-week: the weeks published "
                "before stay published; start the runner again for the rest",
                err=True,
            )
            os._exit(1)

        timer = threading.Timer(RUNNER_TIMEOUT_HOURS * 3600, time_out)
        timer.daemon = True
        timer.start()

    conn = connect()
    if start is not None:  # with --publish, so `published` is None only for an empty bucket
        if published is None:
            set_meta(conn, "corpus_start", start.isoformat())
            typer.echo(f"nothing published: a new store, corpus start {start}")
        elif (kept := corpus_start(conn)) != start:
            typer.echo(f"--start-from {start} ignored: the store's corpus start is {kept}")
    if rerun is not None:
        if get_meta(conn, RERUN) == rerun:
            typer.echo(f"rerun {rerun}: already applied to this store, nothing cleared")
        elif (begins := corpus_start(conn)) is None:
            raise typer.BadParameter("--rerun: the store has no corpus start to rerun from")
        else:
            deleted = clear_runs(conn, rerun=rerun, rollback=published)
            typer.echo(
                f"rerun {rerun}: {deleted['runs']} stored runs deleted, every week from {begins} "
                "is due again. Until all are published the site shows only the weeks rerun so far"
            )
            if published is not None:
                typer.echo(f"the store published before the rerun, {published}, is kept")
    weeks = weekly_dates(conn, today)
    if not weeks:
        typer.echo("no complete week since the last complete run")
        return
    due = weeks[:max_weeks]
    typer.echo(f"{len(weeks)} weeks due, running {', '.join(map(str, due))}")
    try:
        asyncio.run(preflight(week_window(due[0], corpus_start(conn))))
    except Exception as error:
        typer.echo(f"preflight failed, nothing computed: {error!r}", err=True)
        raise typer.Exit(1) from error
    typer.echo("preflight: Exa and Claude answered\n")
    series = fetch_series(conn, date.today(), log=typer.echo)
    problems = sorted(
        {
            f"series: {reason}"
            for reason in series.failures.values()
            if "key rejected" in reason or "is not set" in reason
        }
    )
    for sunday in due:
        window = week_window(sunday, corpus_start(conn))
        typer.echo(f"\nweek {window.start} to {sunday}")
        result = run_ingest(sunday, days=window.days, log=typer.echo)
        typer.echo(
            f"added {sum(result.added.values())} {dict(result.added)}, "
            f"{result.duplicates} duplicates, {len(result.failures)} failures\n"
        )
        problems += [f"week {sunday}: {p}" for p in result.problems]
        trigger = "scheduled" if bucket else "cli"
        asyncio.run(compute_run(sunday, lambda line: typer.echo(line), trigger))
        if bucket is not None:
            published = publish_store(bucket, DB_PATH, replaces=published).key
            typer.echo(f"published {published}")
    if left := weeks[len(due) :]:
        again = "start the runner again" if bucket else "run `im weekly` again"
        typer.echo(f"\n{len(left)} weeks still due, from {left[0]}: {again}")
    if problems:
        typer.echo("\ncomputed, but sources were missing:\n" + "\n".join(problems), err=True)
        raise typer.Exit(1)


def _bucket() -> "Bucket":
    from intelmonitor.store.publish import Bucket

    bucket = Bucket.from_settings()
    if bucket is None:
        raise typer.BadParameter("no bucket: set STORE_BUCKET and the STORE_S3_* variables")
    return bucket


@app.command("publish")
def publish_cmd(
    key: Annotated[
        str | None,
        typer.Option("--key", help="Repoint latest.json at this published store file instead"),
    ] = None,
) -> None:
    """Upload the store as a new immutable file named by its latest complete run, repoint
    latest.json at it and keep the STORE_KEEP newest files; or, with --key, roll back or forward by
    repointing latest.json at a file already in the bucket."""
    from intelmonitor.pipeline import runlog
    from intelmonitor.store.db import DB_PATH, connect
    from intelmonitor.store.publish import LatestMoved, publish

    bucket = _bucket()
    if key is not None:
        try:
            latest = bucket.stored(key)
        except LookupError as error:
            raise typer.BadParameter(str(error)) from error
        bucket.point(latest)
        typer.echo(f"latest.json now names {key} (run {latest.run_id})")
        return
    current = bucket.latest()
    conn = connect()  # migrated, with its run log as the site shows it
    runlog.backfill(conn)
    runlog.migrate(conn)
    conn.close()
    try:
        latest = publish(bucket, DB_PATH, replaces=current and current.key)
    except (LookupError, LatestMoved) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"published {latest.key} (run {latest.run_id}, sha256 {latest.sha256})")


@app.command()
def pull(
    replace: Annotated[
        bool, typer.Option("--replace", help="Replace the local store if there is one")
    ] = False,
) -> None:
    """Download the store latest.json names as the local store."""
    from intelmonitor.store.db import DB_PATH

    bucket = _bucket()
    if DB_PATH.exists() and not replace:
        raise typer.BadParameter(f"{DB_PATH} exists: back it up, then pass --replace")
    latest = bucket.latest()
    if latest is None:
        raise typer.BadParameter(f"nothing published in {bucket.name}")
    for leftover in (DB_PATH.with_name(DB_PATH.name + s) for s in ("-wal", "-shm")):
        leftover.unlink(missing_ok=True)
    bucket.download(latest, DB_PATH)
    typer.echo(f"pulled {latest.key} (run {latest.run_id}) to {DB_PATH}")


@app.command("clear-runs")
def clear_runs(
    yes: Annotated[bool, typer.Option("--yes", help="Do not ask for confirmation")] = False,
) -> None:
    """Delete every stored run with its per-run rows, in one transaction. Documents, their tags
    and findings, inference_records, series and run_log stay. Back up the store first."""
    from intelmonitor.pipeline.run import clear_runs as clear
    from intelmonitor.store.db import connect

    conn = connect()
    runs = conn.execute("SELECT count(*) FROM runs").fetchone()[0]
    if not yes:
        typer.confirm(f"Delete all {runs} runs and their per-run rows?", abort=True)
    deleted = clear(conn)
    for table, count in deleted.items():
        typer.echo(f"{table}: {count} rows deleted")


@app.command()
def impacts(
    as_of: Annotated[str | None, typer.Option("--as-of", help="Run date, YYYY-MM-DD")] = None,
    all_: Annotated[bool, typer.Option("--all", help="Every complete run, oldest first")] = False,
) -> None:
    """(Re)compute the inferred impacts of complete runs from their stored rows and the links,
    with no model call and no other stage, and mark each run recomputed. Briefs keep the impacts
    they were written from until `im summarize`."""
    from intelmonitor.pipeline.run import recompute_impacts
    from intelmonitor.store.db import connect

    if all_ == (as_of is not None):
        raise typer.BadParameter("pass either --as-of <date> or --all")
    conn = connect()
    complete = [
        r["id"]
        for r in conn.execute("SELECT id FROM runs WHERE status = 'complete' ORDER BY as_of")
    ]
    if as_of is not None and as_of not in complete:
        raise typer.BadParameter(f"no complete run as of {as_of}")
    recompute_impacts(conn, complete if all_ else [str(as_of)], typer.echo)


@app.command()
def summarize(
    as_of: Annotated[str | None, typer.Option("--as-of", help="Run date, YYYY-MM-DD")] = None,
    all_: Annotated[bool, typer.Option("--all", help="Every complete run, oldest first")] = False,
    subject: Annotated[
        list[str] | None, typer.Option("--subject", help="Only these topic or entity ids")
    ] = None,
) -> None:
    """(Re)write the subject briefs of complete runs from their stored rows, without
    recomputing the rest of the run."""
    from intelmonitor.pipeline.subjects import monitored_subjects
    from intelmonitor.pipeline.summaries import summarize_run
    from intelmonitor.store.db import connect

    if all_ == (as_of is not None):
        raise typer.BadParameter("pass either --as-of <date> or --all")
    if unknown := [s for s in subject or [] if s not in monitored_subjects()]:
        raise typer.BadParameter(
            f"not a monitored topic or entity (groups have no brief): {', '.join(unknown)}"
        )
    conn = connect()
    complete = [
        r["id"]
        for r in conn.execute("SELECT id FROM runs WHERE status = 'complete' ORDER BY as_of")
    ]
    if as_of is not None and as_of not in complete:
        raise typer.BadParameter(f"no complete run as of {as_of}")
    for run_id in complete if all_ else [str(as_of)]:
        written, broken, refused = asyncio.run(summarize_run(conn, run_id, subject or None))
        typer.echo(
            f"[{run_id}] {written} briefs, {broken} still breaking their rules, {refused} refused"
        )


@check_app.command("series")
def check_series() -> None:
    """Replay a series backfill and a daily fetch from cached responses in a scratch store; print
    JSON."""
    from intelmonitor.checks import series_check

    typer.echo(series_check().model_dump_json(indent=1))


@check_app.command("past-run")
def check_past_run() -> None:
    """Recompute a past run in a copy of the store from recorded model calls; print JSON."""
    from intelmonitor.checks import past_run_check

    typer.echo(asyncio.run(past_run_check()).model_dump_json(indent=1))


@check_app.command("detector")
def check_detector() -> None:
    """Fire the loan-to-deposit detector on fixture values in a scratch store; print JSON."""
    from intelmonitor.checks import detector_check

    typer.echo(asyncio.run(detector_check()).model_dump_json(indent=1))


@export_app.command("labels")
def export_labels(
    out: Annotated[str, typer.Option("--out", help="The .xlsx file to write")],
) -> None:
    """Write the latest complete run's labels to an Excel workbook: Direct, Indirect, Links and
    Dropped sheets (ticket 24). Opens the store read-only and calls no model."""
    from pathlib import Path

    from intelmonitor.export import export_labels as export
    from intelmonitor.store.db import DB_PATH

    if not out.endswith(".xlsx"):
        raise typer.BadParameter("--out must name an .xlsx file")
    counts = export(DB_PATH, Path(out))
    typer.echo(f"wrote {out}: " + ", ".join(f"{n} {name}" for name, n in counts.items()))


@export_app.command("version")
def export_version(
    stage: Annotated[str, typer.Option("--stage", help="priority, link_types or link_rating")],
    version: Annotated[int, typer.Option("--version", help="The version's number")],
    out: Annotated[str, typer.Option("--out", help="The .xlsx file to write")],
    store: StoreOption = None,
) -> None:
    """Write one prompt version's workbook. Priority and link_types: About, Summary and a sheet
    per run, from the store opened read-only, with no migration, and its seed as rated.
    link_rating: the review sheet of the ratings in transmission.yaml that the version made.
    Calls no model."""
    from intelmonitor.export import link_ratings_workbook, version_workbook
    from intelmonitor.link_type_trial import version_workbook as link_types_workbook

    if not out.endswith(".xlsx"):
        raise typer.BadParameter("--out must name an .xlsx file")
    try:
        if stage == "link_rating":
            counts = link_ratings_workbook(version, Path(out))
        elif stage == "link_types":
            counts = link_types_workbook(_store(store), version, Path(out))
        else:
            counts = version_workbook(_store(store), stage, version, Path(out))
    except LookupError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"wrote {out}: " + ", ".join(f"{n} {name}" for name, n in counts.items()))


@trial_app.command("priority")
def trial_priority(
    store: Annotated[str, typer.Option("--store", help="The store's .db file, the baseline")],
    weeks: Annotated[
        str, typer.Option("--weeks", help="sample (300 pairs), a run date, or all")
    ] = "sample",
    candidate: Annotated[
        str,
        typer.Option(
            "--candidate",
            help="live (the current priority prompt) or a candidate of trial.py: medium, "
            "medium-think, high, high-think, price-promo, high-think-price-promo, "
            "high-think-price-promo-fi, high-think-price-promo-fi-lower, anchored, "
            "local-anchored",
        ),
    ] = "live",
) -> None:
    """Rate the store's pairs that priority version 1 rated with the current priority prompt, or
    a candidate's template, schema and effort, from the same input: the subject's name and
    Singapore phrase come from the store's seed as rated. Calls are recorded with their prompt
    version, so a version already tried replays from the cache; no development_subjects row is
    written, and the store is opened with no migration. Pairs version 1 rated whose record is not
    found are listed."""
    from contextlib import closing

    from intelmonitor.pipeline.inference import Inference
    from intelmonitor.trial import CANDIDATES
    from intelmonitor.trial import trial_priority as trial

    if candidate not in CANDIDATES:
        raise typer.BadParameter(f"no candidate {candidate}; one of {', '.join(CANDIDATES)}")
    with closing(_open(store, write=True)) as conn:
        try:
            r = asyncio.run(trial(Inference(conn, None), weeks, CANDIDATES[candidate]))
        except (LookupError, ValueError) as error:
            raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"priority version {r.number} ({r.version}) on {r.pairs} pairs: {r.calls} calls, "
        f"{r.cache_hits} from the cache, ${r.cost_usd:.4f} (measured), {r.refused} refused, "
        f"{len(r.failed)} failed; "
        + ", ".join(f"{r.levels[label]} {label}" for label in ("High", "Medium", "Low"))
    )
    v = r.recorded
    refused = ", ".join(f"{n} {category}" for category, n in v.refused.items()) or "none"
    typer.echo(
        f"version {r.number}'s recorded calls: {v.calls} ({v.answered} answered, {v.cut_off} cut "
        f"off, {v.errors} other errors, refused: {refused}); mean output tokens "
        f"{v.mean_output_tokens:.0f}; ${v.cost_usd:.4f} in all, ${v.cost_per_call:.5f} a call "
        "(measured)"
    )
    for failure in r.failed:
        typer.echo(f"failed: {failure}")
    _echo_unfound(r.unfound)


@trial_app.command("link-types")
def trial_link_types(
    store: Annotated[str, typer.Option("--store", help="The store's .db file, the baseline")],
    effort: Annotated[
        str | None,
        typer.Option(
            "--effort", help="low, medium, high, xhigh or max; the stage's own by default"
        ),
    ] = None,
) -> None:
    """Ask the link_types call about a fixed sample of 100 of the store's developments with an
    entity at Medium or High, with the live link_types prompt and schema at the effort given,
    from the input the stage would have read in each development's run: its entities' levels
    and reasons as stored, named by the store's seed as rated. Calls are recorded with their
    prompt version, so a version already tried replays from the cache; no development_subjects
    row is written, and the store is opened with no migration."""
    from contextlib import closing
    from typing import get_args

    from claude_agent_sdk import EffortLevel

    from intelmonitor.link_type_trial import trial_link_types as trial
    from intelmonitor.pipeline.inference import Inference
    from intelmonitor.pipeline.params import LINK_TYPES_EFFORT
    from intelmonitor.relationships import REL_TYPES

    levels: dict[str, EffortLevel] = {e: e for e in get_args(EffortLevel)}
    if (chosen := levels.get(effort or LINK_TYPES_EFFORT)) is None:
        raise typer.BadParameter(f"no effort {effort}; one of {', '.join(levels)}")
    with closing(_open(store, write=True)) as conn:
        try:
            r = asyncio.run(trial(Inference(conn, None), effort=chosen))
        except (LookupError, ValueError) as error:
            raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"link_types version {r.number} ({r.version}), effort {chosen}, on {r.developments} "
        f"developments with {r.entities} entities at Medium or High: {r.calls} calls, "
        f"{r.cache_hits} from the cache, ${r.cost_usd:.4f} (measured), {r.refused} refused, "
        f"{len(r.failed)} failed"
    )
    answered = r.per_entity.total()
    mean = sum(n * k for n, k in r.per_entity.items()) / answered if answered else 0.0
    spread = ", ".join(f"{n} types {r.per_entity[n]}" for n in sorted(r.per_entity))
    typer.echo(f"types per entity, {answered} entities answered: mean {mean:.2f}; {spread}")
    typer.echo("entities each type was picked for:")
    for t in sorted(REL_TYPES, key=lambda t: -r.by_type[t]):
        typer.echo(f"  {t} {r.by_type[t]}")
    v = r.recorded
    refused = ", ".join(f"{n} {category}" for category, n in v.refused.items()) or "none"
    typer.echo(
        f"version {r.number}'s recorded calls: {v.calls} ({v.answered} answered, {v.cut_off} cut "
        f"off, {v.errors} other errors, refused: {refused}); mean output tokens "
        f"{v.mean_output_tokens:.0f}; ${v.cost_usd:.4f} in all, ${v.cost_per_call:.5f} a call "
        "(measured)"
    )
    typer.echo("developments with an entity at Medium or High, per run (all, new or updated):")
    for run, every, changed in r.per_run:
        typer.echo(f"  {run} {every}, {changed}")
    for failure in r.failed:
        typer.echo(f"failed: {failure}")


def _echo_unfound(pairs: "list[Pair]") -> None:
    """The count of pairs version 1 rated by a call whose record is not found, then each one."""
    typer.echo(f"{len(pairs)} pairs version 1 rated by a call have no version 1 record found")
    for p in pairs:
        typer.echo(f"unfound: {p.run_id} {p.development_id} {p.subject_id}")


@prompts_app.callback(invoke_without_command=True)
def prompts(ctx: typer.Context, store: StoreOption = None) -> None:
    """List each stage's prompt versions in the store, with their calls and runs."""
    if ctx.invoked_subcommand is not None:
        if store is not None:  # the command would read the app's store without saying so
            raise typer.BadParameter(f"give --store after {ctx.invoked_subcommand}")
        return
    from contextlib import closing

    with closing(_open(store)) as conn:
        rows = conn.execute(
            "SELECT v.stage, v.number, v.id, v.model, v.effort, v.first_used, v.note, "
            "(SELECT count(*) FROM inference_records r WHERE r.prompt_version = v.id) AS calls, "
            "(SELECT count(*) FROM run_prompts p WHERE p.version = v.id) AS runs "
            "FROM prompt_versions v ORDER BY v.stage, v.number"
        ).fetchall()
    for r in rows:
        typer.echo(
            f"{r['stage']} {r['number']}  {r['id']}  {r['model']} {r['effort'] or '-'}  "
            f"first used {r['first_used'][:10]}  {r['calls']} calls, {r['runs']} runs  "
            f"{r['note']}"
        )
    if not rows:
        typer.echo("no prompt versions in the store")


@prompts_app.command("show")
def prompts_show(stage: str, number: int, store: StoreOption = None) -> None:
    """Print a version's template."""
    from contextlib import closing

    from intelmonitor.trial import version_row

    try:
        with closing(_open(store)) as conn:
            v = version_row(conn, stage, number)
    except LookupError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"{v['stage']} {v['number']} ({v['id']}), {v['model']}, effort {v['effort']}")
    typer.echo(f"{v['note']}\n\n{v['template']}")


@prompts_app.command("compare")
def prompts_compare(
    stage: str,
    a: int,
    b: int,
    out: Annotated[str, typer.Option("--out", help="The .xlsx file to write")],
    store: StoreOption = None,
) -> None:
    """Compare two versions on the pairs both rated, from the store opened read-only with no
    migration and its seed as rated: write each version's pairs by level, the two prompts and,
    per run, the pairs that moved with their headlines and sources; print the table of level under
    a against level under b, the hand-set cases under each, and the
    pairs version 1 rated whose record is not found. Calls no model."""
    from contextlib import closing

    from intelmonitor.export import compare_workbook
    from intelmonitor.trial import compare, count_table

    if not out.endswith(".xlsx"):
        raise typer.BadParameter("--out must name an .xlsx file")
    with closing(_open(store)) as conn:
        try:
            c = compare(conn, stage, a, b)
        except LookupError as error:
            raise typer.BadParameter(str(error)) from error
        counts = compare_workbook(conn, c, Path(out))
    for row in count_table(c):
        typer.echo("".join(f"{v!s:>10}" for v in row))
    for case, outcome_a, outcome_b, detail_a, detail_b in c.cases:
        typer.echo(f"case {case}: v{a} {outcome_a}, v{b} {outcome_b}")
        for v, detail in ((a, detail_a), (b, detail_b)):
            typer.echo(f"  v{v}: {detail}")
    _echo_unfound(c.unfound)
    typer.echo(f"wrote {out}: " + ", ".join(f"{n} {name}" for name, n in counts.items()))


@prompts_app.command("compare-link-types")
def prompts_compare_link_types(
    a: int,
    b: int,
    out: Annotated[str, typer.Option("--out", help="The .xlsx file to write")],
    store: StoreOption = None,
) -> None:
    """Compare two link_types versions on the developments both answered, from the store opened
    read-only with no migration and its seed as rated: write the counts, the types added and
    removed, both prompts and, per run, the entities whose types differ; print the counts and
    the type table. Calls no model."""
    from contextlib import closing

    from intelmonitor.link_type_trial import compare_link_types, compare_workbook, type_table

    if not out.endswith(".xlsx"):
        raise typer.BadParameter("--out must name an .xlsx file")
    with closing(_open(store)) as conn:
        try:
            c = compare_link_types(conn, a, b)
        except LookupError as error:
            raise typer.BadParameter(str(error)) from error
    counts = compare_workbook(c, Path(out))
    typer.echo(
        f"{c.developments} developments both answered, {c.entities} entities; types differ for "
        f"{c.changed_entities} entities in {c.changed_developments} developments; declined: "
        f"v{a} {c.refused[0]}, v{b} {c.refused[1]}"
    )
    for row in type_table(c):
        typer.echo(f"{row[0]!s:<34}" + "".join(f"{v!s:>14}" for v in row[1:]))
    typer.echo(f"wrote {out}: " + ", ".join(f"{n} {name}" for name, n in counts.items()))


@app.command()
def report() -> None:
    """Write the pipeline report to artifacts/pipeline/<timestamp>/report.md."""
    from intelmonitor.pipeline.report import write_report

    typer.echo(write_report())


@app.command()
def serve(
    reload: Annotated[bool, typer.Option(help="Reload on code changes")] = True,
) -> None:
    """Serve the API on $PORT (set by Portless)."""
    import uvicorn

    uvicorn.run(
        "intelmonitor.server.app:app",
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8000")),
        reload=reload,
    )


@app.command()
def openapi(
    out: Annotated[str, typer.Argument(help="Where to write the OpenAPI JSON schema")],
) -> None:
    """Write the API's OpenAPI schema, the source for the web client's generated types."""
    import json
    from pathlib import Path

    from intelmonitor.server.app import app as api

    Path(out).write_text(json.dumps(api.openapi(), indent=2) + "\n")


@app.command()
def smoke() -> None:
    """Make one Claude Haiku call and one call on the default model and print both answers."""
    from intelmonitor.smoke import run_smoke

    asyncio.run(run_smoke())
