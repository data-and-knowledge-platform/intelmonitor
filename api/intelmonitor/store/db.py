"""SQLite store at data/intelmonitor.db. Each phase adds its tables to SCHEMA."""

import sqlite3
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
    error TEXT
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

-- Indirect impacts (pipeline/impact.py): entities a development reaches through one stored link,
-- at the level the fixed table gives, kept apart from development_subjects.
CREATE TABLE IF NOT EXISTS development_impacts (
    run_id TEXT NOT NULL,
    development_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,          -- the entity reached
    source_id TEXT NOT NULL,           -- the tagged entity the development is adverse for
    relationship_id TEXT NOT NULL,     -- the link it passes through
    region TEXT,                       -- the region endpoint, when the link covers the source in it
    strength TEXT NOT NULL,            -- from the source's end: Strong, Moderate or Weak
    level INTEGER NOT NULL,            -- 0 Low or 1 Medium, never High
    PRIMARY KEY (run_id, development_id, subject_id)
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

CREATE TABLE IF NOT EXISTS graph_nodes (
    run_id TEXT NOT NULL,
    id TEXT NOT NULL,                  -- subject or endpoint id, or dev:<development_id>
    type TEXT NOT NULL,                -- topic, group, entity, endpoint or development
    label TEXT NOT NULL,
    data TEXT NOT NULL,                -- JSON
    PRIMARY KEY (run_id, id)
);

CREATE TABLE IF NOT EXISTS graph_edges (
    run_id TEXT NOT NULL,
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    type TEXT NOT NULL,                -- a relationship type in capitals, AFFECTS or IMPACTS
    source TEXT NOT NULL,              -- relationships (a stored link), claude or detector
                                       -- (AFFECTS), table (IMPACTS); seed and jev when older
    confidence REAL NOT NULL,
    label TEXT NOT NULL DEFAULT '',    -- the link in plain words
    detail TEXT NOT NULL DEFAULT '{}', -- JSON: a link's relationship_id, evidence, weight, reason,
                                       -- from_to and to_from; IMPACTS: route, level, strength,
                                       -- source_id and link_id
    PRIMARY KEY (run_id, from_id, to_id, type)
);

CREATE TABLE IF NOT EXISTS inference_records (
    id INTEGER PRIMARY KEY,
    run_id TEXT,                       -- the run that made the call
    stage TEXT NOT NULL,               -- screen, extract, same_event, priority, theme, ...
    engine TEXT NOT NULL,              -- claude; jev in older stores
    model TEXT NOT NULL,               -- requested model
    model_version TEXT,                -- model reported by the response
    input_ref TEXT NOT NULL,           -- e.g. doc:<id>, pair:<a>|<b>, subject:<id>
    input_hash TEXT NOT NULL,          -- sha256 of the full input; a cache hit skips the call
    output TEXT,                       -- JSON
    confidence REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    duration_ms INTEGER NOT NULL,
    status TEXT NOT NULL,              -- ok, error, or refused (a usage-policy refusal)
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS inference_records_cache ON inference_records (stage, input_hash, status);

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

-- Each run's note per subject on why its series moved in the week to the run (pipeline/notes.py),
-- with the facts code computed for it.
CREATE TABLE IF NOT EXISTS run_notes (
    run_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    since TEXT NOT NULL,               -- the week's start: the previous run's date
    headline TEXT NOT NULL,
    body TEXT NOT NULL,                -- [n] cites development_ids[n - 1]
    development_ids TEXT NOT NULL,     -- JSON, in citation order
    series_id TEXT,                    -- the series the headline is about
    moves TEXT NOT NULL,               -- JSON [SeriesMove], the facts the note was given
    PRIMARY KEY (run_id, subject_id)
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
    # Plain labels and relationship details on graph edges (phase1-plan.md, item I).
    ("graph_edges", "label", "TEXT NOT NULL DEFAULT ''"),
    ("graph_edges", "detail", "TEXT NOT NULL DEFAULT '{}'"),
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
    their tags, findings and development sources; the series' observations, indicators and notes;
    and the social_posts table. A development left with no sources no longer passes the source
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
        "run_notes",
        "series_subjects",
    ]:
        conn.execute(f"DELETE FROM {table} WHERE series_id LIKE ?", (_X_SERIES,))  # noqa: S608
    conn.execute("DELETE FROM series WHERE id LIKE ?", (_X_SERIES,))
    conn.execute("DELETE FROM trends WHERE source = 'x'")
    conn.execute("DROP TABLE IF EXISTS social_posts")
    conn.commit()


def _impacts_by_table(conn: sqlite3.Connection) -> None:
    """Stores made before indirect impact by table kept Claude's judgement in development_impacts
    (why, direction, confidence, a multi-link route). The table is recreated in its new shape,
    empty: its rows are per run, and the next run of each date writes them again."""
    stale = "SELECT 1 FROM pragma_table_info('development_impacts') WHERE name = 'why'"
    if conn.execute(stale).fetchone() is None:
        return
    conn.execute("BEGIN IMMEDIATE")  # another connection may be recreating it too
    if conn.execute(stale).fetchone() is None:
        conn.rollback()
        return
    conn.execute("DROP TABLE development_impacts")
    conn.commit()
    conn.executescript(SCHEMA)


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
    _impacts_by_table(conn)
    if indexed is None:
        search.rebuild(conn)  # the search tables are new: index what is already stored
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
