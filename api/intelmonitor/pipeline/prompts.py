"""Prompt versions: which prompt made each stored label.

A version is a stage's prompt template, output schema, model and effort. Its id is the first 12
hex characters of the SHA-256 of those and the stage, so nobody bumps a number: change any of them
and the next call registers a new version, numbered up from 1 per stage. The template is what the
call site passes as `template` to `Inference.claude` (the priority call passes PRIORITY_PROMPT
before it is filled per entity); every other stage's system prompt is its template.

`Inference` registers the version of every call and cache hit in `prompt_versions`, stamps the
records it writes with it, and notes each run's use of it in `run_prompts`, which is what says
which version a run's labels came from when the run replays every call from the cache. Prompt
text stays in code, and git has its history; the store links each label to its version, and
`im prompts`, `im trial` and `im export version` show it. A candidate version that `im trial`
tries registers with a note of its own."""

import hashlib
import json
import sqlite3
from datetime import UTC, datetime

# The line the guide "Prompting Claude Sonnet 5.5" gives for the end of a system prompt whose
# reply is structured output: it has the model work the problem out in its thinking, which at low
# and medium effort it often skips (ticket 09 of link-types-and-inferred-impact). A prompt that
# uses it ends with it.
THINK_FIRST = "Think the problem through before you answer."

# One line per stage on what its prompt is, edited in the same commit as the prompt. A version
# stores the stage's note when it is first used.
PROMPT_NOTES: dict[str, str] = {
    "screen": "Haiku screen: relevance, category, stage, subject tags and jurisdictions.",
    "extract": "Findings with quotes, dates and each subject's role, adverse flag and tone.",
    "same_event": "Haiku: whether two findings report the same event.",
    "priority": "The team's direct High, Medium, Low prompt (deck of 10 Nov 2026, slide 7), as "
    "built for the 11 Nov rebuild.",
    "link_types": "The relationship types a development activates for each direct entity at "
    "Medium or High (deck of 16 Nov 2026, pages 9 and 10), with page 9's examples; ends with "
    "THINK_FIRST.",
    "headline": "A development's display headline and key points.",
    "note": "A subject's note on why its series moved in the week.",
    "brief": "A subject's brief: state, concerns and direction.",
}


def schema_text(schema: dict[str, object]) -> str:
    """An output schema as stored and hashed: its JSON with sorted keys."""
    return json.dumps(schema, sort_keys=True)


def version_id(stage: str, template: str, schema: str, model: str, effort: str | None) -> str:
    """The first 12 hex characters of the SHA-256 of the five parts of a version."""
    parts = {
        "stage": stage,
        "template": template,
        "schema": schema,
        "model": model,
        "effort": effort,
    }
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:12]


def register_version(
    conn: sqlite3.Connection,
    stage: str,
    template: str,
    schema: str,
    model: str,
    effort: str | None,
    number: int | None = None,
    first_used: str | None = None,
    note: str | None = None,
) -> str:
    """The version's id, registered first if the store does not hold it: numbered one past the
    stage's last, with `note` or else the stage's PROMPT_NOTES line, first used now. The migration
    that registers version 1 passes its `number` and `first_used`; `im trial` passes a candidate's
    note. Commits."""
    vid = version_id(stage, template, schema, model, effort)
    conn.execute(
        "INSERT OR IGNORE INTO prompt_versions "
        "(id, stage, number, template, schema, model, effort, note, first_used) "
        "SELECT ?, ?, coalesce(?, coalesce(max(number), 0) + 1), ?, ?, ?, ?, ?, ? "
        "FROM prompt_versions WHERE stage = ?",
        (
            vid,
            stage,
            number,
            template,
            schema,
            model,
            effort,
            PROMPT_NOTES.get(stage, "") if note is None else note,
            first_used or datetime.now(UTC).isoformat(),
            stage,
        ),
    )
    conn.commit()
    return vid


def note_run(
    conn: sqlite3.Connection, run_id: str, stage: str, version: str, *, replace: bool = False
) -> None:
    """Records that the run's labels of the stage came from the version; with `replace`, first
    drops the run's earlier versions of the stage, whose labels a recompute is replacing.
    Commits."""
    if replace:
        conn.execute("DELETE FROM run_prompts WHERE run_id = ? AND stage = ?", (run_id, stage))
    conn.execute(
        "INSERT OR IGNORE INTO run_prompts (run_id, stage, version) VALUES (?, ?, ?)",
        (run_id, stage, version),
    )
    conn.commit()
