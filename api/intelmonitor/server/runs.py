"""Run history for `GET /runs`, from the run log (pipeline/runlog.py)."""

import sqlite3
from datetime import date, datetime

from intelmonitor.pipeline import runlog
from intelmonitor.server.models import RunInfo


def _how(trigger: str, triggered_by: str | None) -> str:
    """How an execution started; Run now, since removed, named who pressed it."""
    if trigger == "manual":
        return f"Run now, {triggered_by}" if triggered_by else "Run now"
    return "Scheduled" if trigger == "scheduled" else "Command line"


def _run_info(r: sqlite3.Row) -> RunInfo:
    started = datetime.fromisoformat(r["started_at"])
    finished = datetime.fromisoformat(r["finished_at"]) if r["finished_at"] else None
    complete = r["status"] == "complete"
    previous = date.fromisoformat(r["previous_run_id"]) if r["previous_run_id"] else None
    outcome = runlog.Outcome(
        developments=r["developments"] or 0,
        first_run=bool(r["first_run"]),
        new_count=r["new_count"] or 0,
        updated_count=r["updated_count"] or 0,
        needs_attention_count=r["needs_attention_count"] or 0,
    )
    return RunInfo(
        execution_id=r["id"],
        id=r["run_id"],
        as_of=date.fromisoformat(r["as_of"]),
        previous_run_id=r["previous_run_id"],
        status=r["status"],
        trigger=r["trigger"],
        how=_how(r["trigger"], r["triggered_by"]),
        sources=r["sources"],
        documents=r["documents"],
        developments=outcome.developments,
        new_count=outcome.new_count,
        updated_count=outcome.updated_count,
        needs_attention_count=outcome.needs_attention_count,
        result=runlog.result_line(outcome, previous) if complete else None,
        started_at=started,
        finished_at=finished,
        duration_seconds=(finished - started).total_seconds() if finished else None,
    )


def list_runs(conn: sqlite3.Connection) -> list[RunInfo]:
    """Every execution, newest first."""
    rows = conn.execute(
        "SELECT l.*, r.as_of, r.previous_run_id FROM run_log l JOIN runs r ON r.id = l.run_id "
        "ORDER BY l.started_at DESC, l.id DESC"
    ).fetchall()
    return [_run_info(r) for r in rows]
