"""`im` command line: uv run im <command>."""

import asyncio
import os
from typing import Annotated

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False)
series_app = typer.Typer(no_args_is_help=True, help="Fetch measured series (api/seed/series.yaml).")
app.add_typer(series_app, name="series")
check_app = typer.Typer(no_args_is_help=True, help="Checks the walkthrough runs on fixture data.")
app.add_typer(check_app, name="check")


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


@app.command()
def weekly(
    max_weeks: Annotated[
        int, typer.Option("--max-weeks", min=1, help="Run at most this many weeks, oldest first")
    ] = 4,
) -> None:
    """For each complete week (Monday to Sunday UTC) since the last complete run, or since the
    corpus start, oldest first: ingest that week's window with the Sunday as T0, then compute its
    run, dated the Sunday. Fetches the measured series once first. Exits 0 when no week is due."""
    from datetime import UTC, date, datetime

    from intelmonitor.ingest.models import corpus_start, week_window
    from intelmonitor.ingest.run import run_ingest
    from intelmonitor.ingest.series.collect import fetch_series
    from intelmonitor.pipeline.run import compute_run, weekly_dates
    from intelmonitor.store.db import connect

    conn = connect()
    weeks = weekly_dates(conn, datetime.now(UTC).date())
    if not weeks:
        typer.echo("no complete week since the last complete run")
        return
    due = weeks[:max_weeks]
    typer.echo(f"{len(weeks)} weeks due, running {', '.join(map(str, due))}\n")
    fetch_series(conn, date.today(), log=typer.echo)
    for sunday in due:
        window = week_window(sunday, corpus_start(conn))
        typer.echo(f"\nweek {window.start} to {sunday}")
        result = run_ingest(sunday, days=window.days, log=typer.echo)
        typer.echo(
            f"added {sum(result.added.values())} {dict(result.added)}, "
            f"{result.duplicates} duplicates, {len(result.failures)} failures\n"
        )
        asyncio.run(compute_run(sunday, lambda line: typer.echo(line)))
    if left := weeks[len(due) :]:
        typer.echo(f"\n{len(left)} weeks still due, from {left[0]}: run `im weekly` again")


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
def headlines(
    as_of: Annotated[str | None, typer.Option("--as-of", help="Run date, YYYY-MM-DD")] = None,
    all_: Annotated[bool, typer.Option("--all", help="Every complete run, oldest first")] = False,
) -> None:
    """(Re)write the development headlines and key points of complete runs from their stored
    findings, without recomputing the rest of the run."""
    from intelmonitor.pipeline.headlines import write_headlines
    from intelmonitor.pipeline.inference import Inference
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
    for run_id in complete if all_ else [str(as_of)]:
        stats = asyncio.run(write_headlines(Inference(conn, run_id), conn, run_id))
        typer.echo(
            f"[{run_id}] {stats.written} written, {stats.from_code} from detectors, "
            f"{stats.refused} refused"
        )


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
