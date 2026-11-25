"""SQLite store at data/intelmonitor.db. Each phase adds its tables to SCHEMA."""

import sqlite3
from collections.abc import Iterable
from functools import cache
from pathlib import Path

from intelmonitor.config import DATA_DIR
from intelmonitor.store import search

DB_PATH = DATA_DIR / "intelmonitor.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_documents (
    id TEXT PRIMARY KEY,               -- sha256(canonical_url)[:16], stable across reruns
    source TEXT NOT NULL,              -- an official adapter (mas, fed, fatf, ...), exa or detector
    collection TEXT NOT NULL,          -- dms, official, news, or data (detections)
    doc_type TEXT NOT NULL,            -- e.g. Media Releases, Speeches, News
    classification TEXT NOT NULL,      -- public for every POC document
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL UNIQUE,
    domain TEXT NOT NULL,
    title TEXT NOT NULL,
    published_at TEXT NOT NULL,        -- ISO 8601, UTC
    text TEXT NOT NULL,
    content_hash TEXT NOT NULL UNIQUE, -- sha256 of normalized text
    ingested_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS source_documents_published ON source_documents (published_at);

-- Pipeline. Screening, tags and findings are per document and shared by every run; everything
-- keyed by run_id is rebuilt for that run from the documents published from the corpus start
-- (meta corpus_start) to its date.

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,               -- the as-of date, YYYY-MM-DD
    as_of TEXT NOT NULL,
    previous_run_id TEXT,              -- the run this one is diffed against
    status TEXT NOT NULL,              -- running, complete or failed
    documents INTEGER NOT NULL,        -- documents published on or before as_of
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error TEXT,
    impacts_recomputed TEXT            -- when `im impacts` last rewrote its inferred impacts
);

-- One row per execution of a run: `im run` or `im weekly` on the command line (Run now in the app
-- before it was removed). There is no scheduler yet. A rerun recomputes an existing run in place,
-- so runs keeps one row per date and the executions live here. The counts compare against the
-- previous run, the latest earlier as-of date, so every execution of a date reports what that
-- date found.
CREATE TABLE IF NOT EXISTS run_log (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,              -- the runs row it computed
    trigger TEXT NOT NULL,             -- cli (`im run`); manual on rows from Run now, since removed
    triggered_by TEXT,                 -- the profile's display name, on manual rows
    status TEXT NOT NULL,              -- running, complete or failed
    sources INTEGER NOT NULL,          -- distinct ingest sources among its documents
    documents INTEGER NOT NULL,
    developments INTEGER,              -- set when complete, as are the counts below
    first_run INTEGER,                 -- 1 when there was nothing to compare against
    new_count INTEGER,
    updated_count INTEGER,
    needs_attention_count INTEGER,     -- new or updated developments needing attention
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS run_log_run ON run_log (run_id);

CREATE TABLE IF NOT EXISTS document_tags (
    document_id TEXT PRIMARY KEY REFERENCES source_documents (id),
    relevance REAL NOT NULL,           -- 1 or 0 from the Haiku screen; Jev's probability before
    relevant INTEGER NOT NULL,         -- the final decision
    relevance_source TEXT NOT NULL,    -- claude, or refused when Claude declined; jev before
    category TEXT NOT NULL,
    category_confidence REAL NOT NULL, -- 1 from the Haiku screen
    stage TEXT,                        -- only for regulatory items
    stage_confidence REAL,
    subject_scores TEXT NOT NULL,      -- JSON {subject_id: score}, 1/n for the nth listed tag
    jurisdictions TEXT NOT NULL,       -- JSON {jurisdiction: 1}; probabilities in older stores
    screened_chars INTEGER NOT NULL,   -- characters of text the screen saw
    truncated INTEGER NOT NULL         -- 1 when screened on title plus leading chunks
);

CREATE TABLE IF NOT EXISTS document_subjects (
    document_id TEXT NOT NULL REFERENCES source_documents (id),
    subject_id TEXT NOT NULL,
    confidence REAL NOT NULL,          -- the tag's subject_scores score
    source TEXT NOT NULL,              -- claude or detector; jev in older stores
    PRIMARY KEY (document_id, subject_id)
);

-- The merge's embedding of each finding statement (pipeline/developments.py), so a run embeds
-- only statements it has not seen. Keyed by the model and the text, so either changing embeds
-- again.
CREATE TABLE IF NOT EXISTS embeddings (
    key TEXT PRIMARY KEY,              -- sha256 of the model name and the text
    vector BLOB NOT NULL               -- float32, unit length
);

CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,               -- <document_id>-<index>
    document_id TEXT NOT NULL REFERENCES source_documents (id),
    statement TEXT NOT NULL,
    category TEXT NOT NULL,
    event_date TEXT,                   -- YYYY-MM-DD
    subjects TEXT NOT NULL,            -- JSON [subject_id]: every subject it involves
    quotes TEXT NOT NULL,              -- JSON [{text, start, end}], offsets into the document text
    dates TEXT NOT NULL,               -- JSON [{date, label}] mentioned in the document
    -- JSON {subject_id: {role, adverse, tone}} from extraction: role is affected, actor,
    -- commentator or mentioned. Empty for detections, whose subjects are all affected.
    roles TEXT NOT NULL DEFAULT '{}',
    promotional INTEGER NOT NULL DEFAULT 0, -- 1 for marketing or routine product news
    -- 1 when it only reports a share price, market value or index move, or an analyst's price
    -- target or rating. Promotional and price-move findings never join a development.
    price_move INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS findings_document ON findings (document_id);

CREATE TABLE IF NOT EXISTS developments (
    run_id TEXT NOT NULL REFERENCES runs (id),
    id TEXT NOT NULL,                  -- id of its earliest finding, stable across runs
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    stage TEXT,
    event_date TEXT,
    first_published TEXT NOT NULL,
    last_published TEXT NOT NULL,
    materiality_level INTEGER NOT NULL, -- its highest priority_level over its subjects
    materiality_score REAL NOT NULL,    -- that subject's priority_score
    materiality_confidence REAL NOT NULL,
    needs_attention INTEGER NOT NULL,   -- materiality_level 2: High priority (0 Low, 1 Medium)
    PRIMARY KEY (run_id, id)
);

-- The subjects a development happened to (affected) or that did it (actor), with its priority
-- for each. Subjects that only comment on it or are mentioned are in development_mentions.
CREATE TABLE IF NOT EXISTS development_subjects (
    run_id TEXT NOT NULL,
    development_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    confidence REAL NOT NULL,          -- the screening tag's confidence
    source TEXT NOT NULL,              -- who tagged it: claude or detector; jev in older stores
    role TEXT NOT NULL DEFAULT 'affected', -- affected or actor
    adverse INTEGER NOT NULL DEFAULT 0, -- 1 when a finding says something bad happened to it
    tone TEXT NOT NULL DEFAULT 'neutral', -- positive, negative or neutral, for this subject
    priority_level INTEGER NOT NULL DEFAULT 0, -- 0 Low, 1 Medium, 2 High, for this subject
    priority_score REAL NOT NULL DEFAULT 0, -- the level; Jev's expected score in older runs
    priority_confidence REAL NOT NULL DEFAULT 0, -- 1.0 once rated
    priority_reason TEXT NOT NULL DEFAULT '', -- the rating's one sentence on why
    link_types TEXT,                   -- JSON list of the types it activates; null: not asked
    PRIMARY KEY (run_id, development_id, subject_id)
);

-- Subjects that only comment on a development or are mentioned in it. They never drive a page,
-- a priority or an impact.
CREATE TABLE IF NOT EXISTS development_mentions (
    run_id TEXT NOT NULL,
    development_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    role TEXT NOT NULL,                -- commentator or mentioned
    PRIMARY KEY (run_id, development_id, subject_id)
);

-- Inferred impacts (pipeline/impact.py): every path by which a development reaches an entity it
-- does not name through one stored link of a type it activates, at the level the fixed table
-- gives, kept apart from development_subjects. Each entity reached has one best path.
CREATE TABLE IF NOT EXISTS development_impacts (
    run_id TEXT NOT NULL,
    development_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,          -- the entity reached
    source_id TEXT NOT NULL,           -- the named entity the path starts from
    relationship_id TEXT NOT NULL,     -- the link it passes through
    region TEXT,                       -- the region entity, when the link covers the source in it
    strength TEXT NOT NULL,            -- from the source's end: Strong, Moderate or Weak
    level INTEGER NOT NULL,            -- 0 Low, 1 Medium or 2 High
    best INTEGER NOT NULL,             -- 1 on the entity's best path for the development
    reason TEXT NOT NULL,              -- the path in words, with the link's rating reason
    PRIMARY KEY (run_id, development_id, subject_id, source_id, relationship_id)
);
CREATE INDEX IF NOT EXISTS development_impacts_subject ON development_impacts (run_id, subject_id);

CREATE TABLE IF NOT EXISTS development_sources (
    run_id TEXT NOT NULL,
    development_id TEXT NOT NULL,
    finding_id TEXT NOT NULL REFERENCES findings (id),
    document_id TEXT NOT NULL REFERENCES source_documents (id),
    PRIMARY KEY (run_id, development_id, finding_id)
);

CREATE TABLE IF NOT EXISTS upcoming_dates (
    run_id TEXT NOT NULL,
    development_id TEXT NOT NULL,
    finding_id TEXT NOT NULL,
    date TEXT NOT NULL,                -- YYYY-MM-DD, on or after the run date
    label TEXT NOT NULL,
    PRIMARY KEY (run_id, finding_id, date, label)
);

CREATE TABLE IF NOT EXISTS run_changes (
    run_id TEXT NOT NULL,
    base_run_id TEXT NOT NULL,
    kind TEXT NOT NULL,                -- new_development or updated_development
    subject_id TEXT,                   -- null; set for the question_state kind, since removed
    ref_id TEXT NOT NULL,              -- the development id
    detail TEXT NOT NULL               -- JSON
);
CREATE INDEX IF NOT EXISTS run_changes_run ON run_changes (run_id, kind);

CREATE TABLE IF NOT EXISTS inference_records (
    id INTEGER PRIMARY KEY,
    run_id TEXT,                       -- the run that made the call
    stage TEXT NOT NULL,               -- screen, extract, same_event, priority, headline, ...
    engine TEXT NOT NULL,              -- claude; jev in older stores
    model TEXT NOT NULL,               -- requested model
    model_version TEXT,                -- model reported by the response
    input_ref TEXT NOT NULL,           -- e.g. doc:<id>, pair:<a>|<b>, subject:<id>
    input_hash TEXT NOT NULL,          -- sha256 of the full input; a cache hit skips the call
    prompt_version TEXT,               -- prompt_versions id; null for calls made before versions
    output TEXT,                       -- JSON
    confidence REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    duration_ms INTEGER NOT NULL,
    status TEXT NOT NULL,              -- ok, error, or refused (a usage-policy refusal)
    error TEXT,
    refusal_category TEXT,             -- a refused call's stop_details.category, if it names one
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS inference_records_cache ON inference_records (stage, input_hash, status);

-- Prompt versions (pipeline/prompts.py): a stage's template, schema, model and effort, registered
-- by the first call or cache hit that uses them, numbered from 1 per stage.
CREATE TABLE IF NOT EXISTS prompt_versions (
    id TEXT PRIMARY KEY,               -- sha256 of stage, template, schema, model, effort; 12 hex
    stage TEXT NOT NULL,
    number INTEGER NOT NULL,           -- 1, 2, ... per stage, in order of first use
    template TEXT NOT NULL,            -- the system prompt, or the template it is filled from
    schema TEXT NOT NULL,              -- the output schema's JSON
    model TEXT NOT NULL,
    effort TEXT,                       -- null: the model's own
    note TEXT NOT NULL,                -- the stage's PROMPT_NOTES line when first used
    first_used TEXT NOT NULL,
    UNIQUE (stage, number)
);

-- The versions each run's labels came from, per stage. A run that replays every call from the
-- cache writes no inference record, so this is what says which prompt made its labels.
CREATE TABLE IF NOT EXISTS run_prompts (
    run_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    version TEXT NOT NULL,             -- prompt_versions id
    PRIMARY KEY (run_id, stage, version)
);

-- Structured data (docs/archive/structured-data-plan.md, store/series.py). The catalogue tables
-- mirror api/seed/series.yaml and are rewritten on every series fetch; observations keep the
-- latest value per period, and a changed value leaves a revision row.
CREATE TABLE IF NOT EXISTS series (
    id TEXT PRIMARY KEY,               -- e.g. mas.ltd.overall
    source TEXT NOT NULL,              -- mas, fred, coingecko, defillama, yahoo, manual or derived
    name TEXT NOT NULL,
    unit TEXT NOT NULL,
    frequency TEXT NOT NULL,           -- daily, monthly, quarterly or yearly
    decimals INTEGER NOT NULL,
    bad_direction TEXT NOT NULL,       -- up, down or none
    source_url TEXT NOT NULL,
    derived_from TEXT                  -- JSON operation, e.g. {"kind": "share", ...}; null if raw
);

CREATE TABLE IF NOT EXISTS series_subjects (
    series_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    role TEXT NOT NULL,                -- primary or context
    PRIMARY KEY (series_id, subject_id)
);
CREATE INDEX IF NOT EXISTS series_subjects_subject ON series_subjects (subject_id);

CREATE TABLE IF NOT EXISTS observations (
    series_id TEXT NOT NULL,
    period TEXT NOT NULL,              -- period end, YYYY-MM-DD
    value REAL NOT NULL,
    preliminary INTEGER NOT NULL,      -- 1 while the source flags it preliminary
    available_at TEXT NOT NULL,        -- ISO 8601 UTC, when the value could first have been known
    fetched_at TEXT NOT NULL,          -- when it was stored or last changed
    PRIMARY KEY (series_id, period)
);

CREATE TABLE IF NOT EXISTS observation_revisions (
    id INTEGER PRIMARY KEY,
    series_id TEXT NOT NULL,
    period TEXT NOT NULL,
    old_value REAL NOT NULL,
    new_value REAL NOT NULL,
    revised_at TEXT NOT NULL           -- ISO 8601 UTC, the fetch that saw the new value
);
CREATE INDEX IF NOT EXISTS observation_revisions_series
ON observation_revisions (series_id, period);

-- Where each figure of a hand-collected series comes from (item M, ingest/series/manual.py).
-- Each fetch replaces the series' sources; its observations are saved as for any other series.
CREATE TABLE IF NOT EXISTS observation_sources (
    series_id TEXT NOT NULL,
    period TEXT NOT NULL,              -- period end, YYYY-MM-DD, as in observations
    url TEXT NOT NULL,
    title TEXT NOT NULL,               -- the publication
    quote TEXT,                        -- the verbatim sentence or table row
    chart_reading INTEGER NOT NULL,    -- 1 when the value was read from a chart
    PRIMARY KEY (series_id, period)
);

-- What each run saw per series, so runs diff.
CREATE TABLE IF NOT EXISTS run_indicators (
    run_id TEXT NOT NULL,
    series_id TEXT NOT NULL,
    period TEXT NOT NULL,              -- the latest period available as of the run
    value REAL NOT NULL,
    change TEXT NOT NULL,              -- JSON {window: change}
    state TEXT NOT NULL,
    detail TEXT NOT NULL,              -- JSON
    PRIMARY KEY (run_id, series_id)
);

-- Each run's brief per subject for the top of its monitor page (pipeline/summaries.py; item B of
-- phase1-plan.md). Claude writes the state, concerns and direction from the week's evidence and
-- the subject's standing position; code checks them and computes the counts. Rows written before
-- the brief have concerns and points null; briefs written before concerns have points instead.
CREATE TABLE IF NOT EXISTS run_summaries (
    run_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    text TEXT NOT NULL,                -- the state: the subject's situation now
    direction TEXT,                    -- worse, better, unchanged or mixed, against the last brief
    direction_basis TEXT NOT NULL,     -- code's signals for the direction, in words
    developments_in_scope INTEGER NOT NULL, -- its developments with a source in the last 7 days
    needs_attention INTEGER NOT NULL,  -- of those, High for it
    adverse INTEGER NOT NULL,          -- of those, adverse to it
    model TEXT NOT NULL,               -- the model that wrote the brief; none when code wrote it
    created_at TEXT NOT NULL,
    direction_reason TEXT NOT NULL DEFAULT '', -- the model's one line on the direction
    direction_flagged INTEGER NOT NULL DEFAULT 0, -- 1: the direction disagrees with code's signals
    concerns TEXT,                     -- JSON [{text, development_ids}]
    points TEXT,                       -- earlier briefs: JSON [{claim, why_it_matters, ...}]
    indirect TEXT NOT NULL DEFAULT '[]', -- earlier briefs: JSON [development_id]
    coming_up TEXT NOT NULL DEFAULT '[]', -- JSON [{date, label, development_id}]
    latest_news TEXT,                  -- YYYY-MM-DD, its latest development's last source
    problems TEXT NOT NULL DEFAULT '[]', -- JSON [rule], what the brief still broke after the retry
    PRIMARY KEY (run_id, subject_id)
);

-- Detector firings (pipeline/detectors.py): each one is a source document in the data collection
-- with one finding. Written once per event: the document id comes from the detector, the series and
-- the event's first period, so reruns find the row already there.
CREATE TABLE IF NOT EXISTS detections (
    document_id TEXT PRIMARY KEY REFERENCES source_documents (id),
    detector_id TEXT NOT NULL,         -- api/seed/detectors.yaml; its materiality applies
    series_id TEXT NOT NULL,
    start TEXT NOT NULL,               -- first period of the event, YYYY-MM-DD
    period TEXT NOT NULL,              -- the period whose value fired it
    value REAL NOT NULL,
    direction TEXT NOT NULL            -- the move's tone: positive, negative or neutral
);

-- Search trends (ingest/sources/trends.py), one snapshot per ingest T0. Trending searches hold one
-- row per trending term. Older stores also hold Google interest over time from SerpApi, one row
-- per term and day, relative (0 to 100) within the call that fetched it.
CREATE TABLE IF NOT EXISTS trends (
    snapshot TEXT NOT NULL,            -- the ingest T0, YYYY-MM-DD
    source TEXT NOT NULL,              -- google_rss; older stores also hold google (SerpApi)
    kind TEXT NOT NULL,                -- trending or interest
    geo TEXT NOT NULL,                 -- SG, MY, ...
    term TEXT NOT NULL,
    observed_at TEXT NOT NULL,         -- trending: when it started; interest: the day
    value REAL,                        -- search volume or interest (0 to 100)
    subjects TEXT NOT NULL DEFAULT '[]', -- JSON ids of tracked subjects the term names
    detail TEXT NOT NULL DEFAULT '{}', -- JSON: increase, categories, related terms, call terms
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (snapshot, source, kind, geo, term, observed_at)
);

-- Full-text search (store/search.py). Both tables keep their own copy of the text, so they do not
-- depend on rowids staying put. Triggers index documents on every write; the pipeline indexes a
-- run's developments when it stores them, with the statements of all their findings as summary.
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    title, text, document_id UNINDEXED, tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS source_documents_fts_insert AFTER INSERT ON source_documents BEGIN
    INSERT INTO documents_fts (title, text, document_id) VALUES (new.title, new.text, new.id);
END;
CREATE TRIGGER IF NOT EXISTS source_documents_fts_delete AFTER DELETE ON source_documents BEGIN
    DELETE FROM documents_fts WHERE document_id = old.id;
END;
CREATE TRIGGER IF NOT EXISTS source_documents_fts_update AFTER UPDATE OF title, text
ON source_documents BEGIN
    DELETE FROM documents_fts WHERE document_id = old.id;
    INSERT INTO documents_fts (title, text, document_id) VALUES (new.title, new.text, new.id);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS developments_fts USING fts5(
    title, summary, run_id UNINDEXED, development_id UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""

# Columns added to tables after stores were created. connect() adds any a store lacks, in place.
ADDED_COLUMNS = [
    # The detector whose rule set the level; null when the rating set it (pipeline/developments.py).
    ("developments", "materiality_rule", "TEXT"),
    # Roles, adverse and priority per subject (phase1-plan.md, item A).
    ("findings", "roles", "TEXT NOT NULL DEFAULT '{}'"),
    ("findings", "promotional", "INTEGER NOT NULL DEFAULT 0"),
    ("development_subjects", "role", "TEXT NOT NULL DEFAULT 'affected'"),
    ("development_subjects", "adverse", "INTEGER NOT NULL DEFAULT 0"),
    ("development_subjects", "tone", "TEXT NOT NULL DEFAULT 'neutral'"),
    ("development_subjects", "priority_level", "INTEGER NOT NULL DEFAULT 0"),
    ("development_subjects", "priority_score", "REAL NOT NULL DEFAULT 0"),
    ("development_subjects", "priority_confidence", "REAL NOT NULL DEFAULT 0"),
    # The structured entity brief (phase1-plan.md, item B).
    ("run_summaries", "direction_reason", "TEXT NOT NULL DEFAULT ''"),
    ("run_summaries", "direction_flagged", "INTEGER NOT NULL DEFAULT 0"),
    ("run_summaries", "points", "TEXT"),
    ("run_summaries", "indirect", "TEXT NOT NULL DEFAULT '[]'"),
    ("run_summaries", "coming_up", "TEXT NOT NULL DEFAULT '[]'"),
    ("run_summaries", "latest_news", "TEXT"),
    ("run_summaries", "problems", "TEXT NOT NULL DEFAULT '[]'"),
    # The brief's concerns, in place of points and indirect (pipeline/summaries.py).
    ("run_summaries", "concerns", "TEXT"),
    # Display headline and key points (pipeline/headlines.py); null until written or if refused.
    ("developments", "headline", "TEXT"),
    ("developments", "points", "TEXT"),  # JSON list
    # Dropped findings and the priority reason (pipeline/developments.py).
    ("findings", "price_move", "INTEGER NOT NULL DEFAULT 0"),
    ("development_subjects", "priority_reason", "TEXT NOT NULL DEFAULT ''"),
    # The prompt version of each call (pipeline/prompts.py).
    ("inference_records", "prompt_version", "TEXT"),
    # A refused call's category (pipeline/claude.py).
    ("inference_records", "refusal_category", "TEXT"),
    # The relationship types a development activates for an entity at Medium or High, or at Low
    # with INDIRECT_FROM_LOW on, a JSON list; null when it was not asked (pipeline/developments.py,
    # the link_types stage).
    ("development_subjects", "link_types", "TEXT"),
    # When `im impacts` last rewrote the run's inferred impacts; null when its own run did.
    ("runs", "impacts_recomputed", "TEXT"),
]


def _add_columns(conn: sqlite3.Connection) -> None:
    for table, column, declaration in ADDED_COLUMNS:
        columns = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
            except sqlite3.OperationalError as error:
                if "duplicate column" not in str(error):  # another process added it first
                    raise
    conn.commit()


def _per_subject_priority(conn: sqlite3.Connection) -> None:
    """Stores made before per-subject priority kept one materiality level, adverse score and
    direction per development, from the document-level screening. Each development subject takes
    its development's level until the next run scores it, with no adverse flag, and the
    development-level adverse and direction go, so nothing reads them."""
    stale = "SELECT 1 FROM pragma_table_info('developments') WHERE name = 'adverse'"
    if conn.execute(stale).fetchone() is None:
        return
    conn.execute("BEGIN IMMEDIATE")  # another connection may be migrating too
    if conn.execute(stale).fetchone() is None:
        conn.rollback()
        return
    conn.execute(
        "UPDATE development_subjects SET priority_level = d.materiality_level, "
        "priority_score = d.materiality_score, priority_confidence = d.materiality_confidence "
        "FROM developments d WHERE d.run_id = development_subjects.run_id "
        "AND d.id = development_subjects.development_id"
    )
    conn.execute("ALTER TABLE developments DROP COLUMN adverse")
    conn.execute("ALTER TABLE developments DROP COLUMN direction")
    conn.commit()


# Columns dropped once nothing read them: screening's document-level adverse and direction, and a
# detection's adverse score (phase1-decisions.md, S7). Tone is per subject, from extraction.
DROPPED_COLUMNS = [
    ("document_tags", "adverse"),
    ("document_tags", "direction"),
    ("document_tags", "direction_confidence"),
    ("detections", "adverse"),
]


def _drop_columns(conn: sqlite3.Connection) -> None:
    """Drops each column in DROPPED_COLUMNS that a store still has."""

    def present() -> list[tuple[str, str]]:
        return [
            (table, column)
            for table, column in DROPPED_COLUMNS
            if conn.execute(
                f"SELECT 1 FROM pragma_table_info('{table}') WHERE name = ?", (column,)
            ).fetchone()
        ]

    if not present():
        return
    conn.execute("BEGIN IMMEDIATE")  # another connection may be dropping them too
    for table, column in present():
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    conn.commit()


# X post-count series (since removed); their ids all start with this.
_X_SERIES = "x.posts.%"


def _drop_social(conn: sqlite3.Connection) -> None:
    """Stores made while social media was a source hold X posts and forum threads, X trends and
    X post-count series. They go with every row that refers to them: the social documents, the
    posts from subjects' own X accounts (stored as official) and the detections on X series, with
    their tags, findings and development sources; the series' observations and indicators; and the
    social_posts table. A development left with no sources no longer passes the source
    gate. Stores dropped before own-account posts were included still hold those, so either
    leftover triggers it."""
    exists = (
        "SELECT 1 FROM sqlite_master WHERE name = 'social_posts' "
        "UNION ALL SELECT 1 FROM source_documents WHERE source = 'x' LIMIT 1"
    )
    if conn.execute(exists).fetchone() is None:
        return
    conn.execute("BEGIN IMMEDIATE")  # another connection may be dropping them too
    if conn.execute(exists).fetchone() is None:
        conn.rollback()
        return
    conn.execute(
        "CREATE TEMP TABLE dropped_documents AS SELECT id FROM source_documents "
        "WHERE collection = 'social' OR source = 'x' "
        "OR id IN (SELECT document_id FROM detections WHERE series_id LIKE ?)",
        (_X_SERIES,),
    )
    doomed = "SELECT id FROM dropped_documents"
    conn.execute(
        "DELETE FROM upcoming_dates WHERE finding_id IN "  # noqa: S608
        f"(SELECT id FROM findings WHERE document_id IN ({doomed}))"
    )
    for table in ["development_sources", "findings", "document_tags", "document_subjects"]:
        conn.execute(f"DELETE FROM {table} WHERE document_id IN ({doomed})")  # noqa: S608
    conn.execute(f"DELETE FROM detections WHERE document_id IN ({doomed})")  # noqa: S608
    conn.execute(f"DELETE FROM source_documents WHERE id IN ({doomed})")  # noqa: S608
    conn.execute("DROP TABLE dropped_documents")
    for table in [
        "observations",
        "observation_revisions",
        "observation_sources",
        "run_indicators",
        "series_subjects",
    ]:
        conn.execute(f"DELETE FROM {table} WHERE series_id LIKE ?", (_X_SERIES,))  # noqa: S608
    conn.execute("DELETE FROM series WHERE id LIKE ?", (_X_SERIES,))
    conn.execute("DELETE FROM trends WHERE source = 'x'")
    conn.execute("DROP TABLE IF EXISTS social_posts")
    conn.commit()


def _impacts_by_path(conn: sqlite3.Connection) -> None:
    """Stores made before every inferred path was kept (spec step 8 of
    link-types-and-inferred-impact) hold one row per entity reached, never High, with no best
    flag; older ones kept Claude's judgement there. The table is recreated in its new shape,
    empty: its rows are per run, and `im impacts` or the next run of each date writes them again.
    Run twice, the second finds the best column and does nothing."""
    stale = (
        "SELECT 1 FROM sqlite_master WHERE name = 'development_impacts' AND NOT EXISTS "
        "(SELECT 1 FROM pragma_table_info('development_impacts') WHERE name = 'best')"
    )
    if conn.execute(stale).fetchone() is None:
        return
    conn.execute("BEGIN IMMEDIATE")  # another connection may be recreating it too
    if conn.execute(stale).fetchone() is None:
        conn.rollback()
        return
    conn.execute("DROP TABLE development_impacts")
    conn.commit()
    conn.executescript(SCHEMA)


# Priority version 1: the built priority prompt (PRIORITY_PROMPT, PriorityRating, Sonnet 5.5 at
# PRIORITY_EFFORT) that made every Claude priority call of the 11 Nov 2026 rebuild of the six runs,
# 4 Oct to 8 Nov. Its id is pinned: the migration registers it only while the code's priority
# prompt still has this id, so a store opened after the prompt changes is not stamped wrongly.
PRIORITY_V1 = "b54f28dab077"
PRIORITY_V1_MODEL = "claude-sonnet-5-5"
# The rebuild's window (UTC): its first run started at 21:32 on 10 Nov, its last finished at 01:53.
REBUILD_FROM, REBUILD_TO = "2026-11-10T21:30", "2026-11-11T02:00"
_REBUILD_PRIORITY = (
    "FROM inference_records WHERE stage = 'priority' AND engine = 'claude' AND model = ? "
    "AND created_at BETWEEN ? AND ?"
)


def _priority_version_1(conn: sqlite3.Connection) -> None:
    """Stores holding the 11 Nov rebuild's priority calls, made before prompt versions: the built
    priority prompt is registered as priority version 1, the rebuild's Claude priority records
    are stamped with it, and each run that made them uses it, in one transaction. Git shows
    PRIORITY_PROMPT, PRIORITY_EFFORT and PriorityRating unchanged from their first commit, and
    replaying the rebuild's inputs with the code hits every record's cache key. A store that
    holds any priority version is done: it was migrated, or its priority calls came later."""
    if conn.execute("SELECT 1 FROM prompt_versions WHERE stage = 'priority'").fetchone():
        return
    window = (PRIORITY_V1_MODEL, REBUILD_FROM, REBUILD_TO)
    first = conn.execute(f"SELECT min(created_at) {_REBUILD_PRIORITY}", window).fetchone()[0]  # noqa: S608
    if first is None:
        return
    from intelmonitor.pipeline.developments import PriorityRating
    from intelmonitor.pipeline.params import PRIORITY_EFFORT, PRIORITY_PROMPT
    from intelmonitor.pipeline.prompts import register_version, schema_text, version_id

    parts = (
        "priority",
        PRIORITY_PROMPT,
        schema_text(PriorityRating.model_json_schema()),
        PRIORITY_V1_MODEL,
        PRIORITY_EFFORT,
    )
    if version_id(*parts) != PRIORITY_V1:
        return  # the prompt has changed since: the code no longer holds version 1
    conn.execute("BEGIN IMMEDIATE")  # another connection may be migrating too
    if conn.execute("SELECT 1 FROM prompt_versions WHERE stage = 'priority'").fetchone():
        conn.rollback()
        return
    conn.execute(
        f"UPDATE inference_records SET prompt_version = ? "  # noqa: S608
        f"WHERE id IN (SELECT id {_REBUILD_PRIORITY})",
        (PRIORITY_V1, *window),
    )
    conn.execute(
        f"INSERT OR IGNORE INTO run_prompts (run_id, stage, version) "  # noqa: S608
        f"SELECT DISTINCT run_id, 'priority', ? {_REBUILD_PRIORITY} AND run_id IS NOT NULL",
        (PRIORITY_V1, *window),
    )
    register_version(conn, *parts, number=1, first_used=first)  # commits the transaction


# Tables and meta keys of the features removed on 4 Nov 2026 (docs/removed-features-2026-11-04.md):
# dashboards, profiles, visits, follows, reviews and notes, analyst feedback, custom questions,
# triage, the alert reads triage replaced, and the standing questions' answers and facts.
REMOVED_TABLES = [
    "dashboards",
    "profiles",
    "visits",
    "follows",
    "analyst_actions",
    "feedback",
    "custom_question_answers",
    "custom_questions",
    "development_triage",
    "alert_reads",
    "question_answers",
    "run_facts",
]
REMOVED_META = [
    "active_profile",
    "profile",
    "profiles_seeded",
    "dashboards_seeded",
    "follows_seeded",
    "tour_seen_at",
]


def _drop_removed_features(conn: sqlite3.Connection) -> None:
    """Stores made before 4 Nov 2026 hold dashboards, profiles, visits, follows, reviews, notes,
    analyst feedback, custom questions, triage and standing answers. Their tables, meta keys and
    the run changes of standing answers' states go."""
    tables = ",".join("?" for _ in REMOVED_TABLES)
    keys = ",".join("?" for _ in REMOVED_META)
    exists = (
        f"SELECT 1 FROM sqlite_master WHERE type = 'table' AND name IN ({tables}) "  # noqa: S608
        f"UNION ALL SELECT 1 FROM meta WHERE key IN ({keys}) "
        "UNION ALL SELECT 1 FROM run_changes WHERE kind = 'question_state' LIMIT 1"
    )
    args = (*REMOVED_TABLES, *REMOVED_META)
    if conn.execute(exists, args).fetchone() is None:
        return
    conn.execute("BEGIN IMMEDIATE")  # another connection may be dropping them too
    if conn.execute(exists, args).fetchone() is None:
        conn.rollback()
        return
    for table in REMOVED_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.execute(f"DELETE FROM meta WHERE key IN ({keys})", REMOVED_META)  # noqa: S608
    conn.execute("DELETE FROM run_changes WHERE kind = 'question_state'")
    conn.commit()


# The graph view's tables, written by the run's graph stage until both were removed on 16 Nov 2026
# (spec step 3b of link-types-and-inferred-impact).
GRAPH_TABLES = ["graph_nodes", "graph_edges"]


def _drop_graph(conn: sqlite3.Connection) -> None:
    """Stores made before the graph view was removed hold its per-run nodes and edges; both
    tables go."""
    marks = ",".join("?" for _ in GRAPH_TABLES)
    exists = f"SELECT 1 FROM sqlite_master WHERE type = 'table' AND name IN ({marks})"  # noqa: S608
    if conn.execute(exists, GRAPH_TABLES).fetchone() is None:
        return
    conn.execute("BEGIN IMMEDIATE")  # another connection may be dropping them too
    if conn.execute(exists, GRAPH_TABLES).fetchone() is None:
        conn.rollback()
        return
    for table in GRAPH_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()


def _drop_notes(conn: sqlite3.Connection) -> None:
    """Stores made before the note on why a subject's series moved was removed (25 Nov 2026) hold
    each run's notes in run_notes; the table goes. The note stage's prompt versions and inference
    records stay, as history and cost."""
    exists = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'run_notes'"
    if conn.execute(exists).fetchone() is None:
        return
    conn.execute("BEGIN IMMEDIATE")  # another connection may be dropping it too
    if conn.execute(exists).fetchone() is None:
        conn.rollback()
        return
    conn.execute("DROP TABLE run_notes")
    conn.commit()


def _drop_endpoints(conn: sqlite3.Connection) -> None:
    """Stores synced before every link joined two entities (16 Nov 2026, spec step 3b of
    link-types-and-inferred-impact) hold relationship_endpoints and links to endpoints. The table
    goes, with every stored link that has an end the seed lacks (an entity, or a seed group for
    belongs_to_sector), so no stored link ends at an endpoint. It reads only the seed, never
    relationships.yaml, so a file the code cannot load yet (links not rated, say) never stops the
    store from opening; _relink then replaces links the model no longer reads."""
    exists = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'relationship_endpoints'"
    if conn.execute(exists).fetchone() is None:
        return
    from intelmonitor.seed import load_subjects

    seed = load_subjects()
    entities = [e.id for e in seed.entities]
    groups = [g.id for g in seed.groups]
    conn.execute("BEGIN IMMEDIATE")  # another connection may be migrating too
    if conn.execute(exists).fetchone() is None:
        conn.rollback()
        return
    marks = ",".join("?" * len(entities))
    conn.execute(
        f"DELETE FROM relationships WHERE from_id NOT IN ({marks}) OR (to_id NOT IN ({marks}) "  # noqa: S608
        f"AND NOT (type = 'belongs_to_sector' AND to_id IN ({','.join('?' * len(groups))})))",
        (*entities, *entities, *groups),
    )
    conn.execute("DROP TABLE relationship_endpoints")
    conn.commit()


# Meta keys `im weekly --rerun` writes in the transaction that clears the runs (pipeline/run.py
# clear_runs): the operator's label for the rerun, so a later firing with the same label clears
# nothing (each publish also marks it in the bucket, so a rollback to a store without it is
# refused), and the key of the store published before it, which a publish never prunes
# (store/publish.py), so it stays a rollback target.
RERUN = "rerun"
RERUN_ROLLBACK = "rerun_rollback"

# The meta key holding the version of the link model the stored links were last checked against.
LINKS_CHECKED = "links_checked"


@cache
def _links_version() -> str:
    """The link model's version: the first 12 hex characters of the SHA-256 of its JSON schema."""
    import hashlib
    import json

    from intelmonitor.relationships import Relationship

    schema = json.dumps(Relationship.model_json_schema(), sort_keys=True)
    return hashlib.sha256(schema.encode()).hexdigest()[:12]


def _relink(conn: sqlite3.Connection) -> None:
    """Only a run's sync rewrites the stored links, so a store synced before the link model
    changed (the 22 types and the end of rating_from, 17 Nov 2026, spec steps 4 and 6 of
    link-types-and-inferred-impact) holds rows the Relationship model rejects, and every reader of
    them raises. Once per version of the model (_links_version, kept in meta LINKS_CHECKED)
    every stored row is parsed. If one fails, the links are replaced with relationships.yaml, as a
    run's sync would; if the file cannot load, only the failing rows go, until a run syncs the
    store."""
    import yaml
    from pydantic import ValidationError

    from intelmonitor.relationships import Relationship, insert_relationships, load_relationships

    table = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'relationships'"
    if conn.execute(table).fetchone() is None:
        return  # never synced: nothing to read
    version = _links_version()
    checked = "SELECT 1 FROM meta WHERE key = ? AND value = ?"
    if conn.execute(checked, (LINKS_CHECKED, version)).fetchone() is not None:
        return
    conn.execute("BEGIN IMMEDIATE")  # another connection may be migrating too
    if conn.execute(checked, (LINKS_CHECKED, version)).fetchone() is not None:
        conn.rollback()
        return
    failing: list[str] = []
    for row in conn.execute("SELECT id, data FROM relationships"):
        try:
            Relationship.model_validate_json(row["data"])
        except ValidationError:
            failing.append(row["id"])
    if failing:
        try:
            insert_relationships(conn, load_relationships())
        except (OSError, ValueError, yaml.YAMLError):
            conn.executemany("DELETE FROM relationships WHERE id = ?", [(i,) for i in failing])
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (LINKS_CHECKED, version),
    )
    conn.commit()


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    """Open the store and make sure the schema exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # The API opens one connection per request, which FastAPI may use from a worker thread.
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # the API reads while a pipeline run writes
    indexed = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'developments_fts'").fetchone()
    conn.executescript(SCHEMA)
    _add_columns(conn)
    _per_subject_priority(conn)
    _drop_columns(conn)
    _drop_social(conn)
    _drop_removed_features(conn)
    _drop_graph(conn)
    _drop_notes(conn)
    _drop_endpoints(conn)
    _relink(conn)
    _impacts_by_path(conn)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS inference_records_version "
        "ON inference_records (prompt_version, input_ref)"
    )
    _priority_version_1(conn)
    if indexed is None:
        search.rebuild(conn)  # the search tables are new: index what is already stored
    return conn


class StoreLacks(LookupError):
    """A store opened as it is lacks a table or column the command needs."""


@cache
def _schema_columns() -> dict[str, frozenset[str]]:
    """Every table's columns as SCHEMA and ADDED_COLUMNS give them, from an empty store."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _add_columns(conn)
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    columns = {t["name"]: frozenset(_columns(conn, t["name"])) for t in tables}
    conn.close()
    return columns


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r["name"] for r in conn.execute("SELECT name FROM pragma_table_info(?)", (table,))]


# The tables the prompt version commands (`im trial`, `im prompts`, `im export version`) read and
# write; a store from before prompt versions lacks them.
PROMPT_VERSION_TABLES = ("inference_records", "prompt_versions", "run_prompts")


def open_store(
    path: Path, tables: Iterable[str] = (), *, write: bool = False
) -> sqlite3.Connection:
    """Open an existing store as it is, read-only unless `write`. Unlike connect(), it runs no
    schema or migration, so a frozen store such as the baseline keeps its schema whatever
    migrations the code gains; the commands that take --store open it this way. Each of `tables`
    must have every column the code's schema gives it: raises StoreLacks naming what is missing,
    before anything reads or writes, and FileNotFoundError when there is no store at `path`."""
    if not path.is_file():
        raise FileNotFoundError(f"no store at {path}")
    mode = "rw" if write else "ro"  # rw never creates a file
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode={mode}", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    expected = _schema_columns()
    lacks: list[str] = []
    for table in tables:
        if not (present := set(_columns(conn, table))):
            lacks.append(f"the table {table}")
        elif missing := sorted(expected[table] - present):
            lacks.append(f"{table}'s columns {', '.join(missing)}")
    if lacks:
        conn.close()
        raise StoreLacks(
            f"the store at {path} lacks {'; '.join(lacks)}, which this command needs. It is "
            "opened without migrations, so that a frozen store keeps its schema: run the command "
            "on a store that has them, or migrate a copy with connect()"
        )
    return conn


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
