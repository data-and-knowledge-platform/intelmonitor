"""Steps 2 and 3: one Claude Haiku call per document decides whether it is relevant, which
subjects it is materially about (most central first), its category, its regulatory stage and the
jurisdictions it reports on. Results go to document_tags and document_subjects, shared by every
run."""

import asyncio
import json
import sqlite3
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, JsonValue, create_model

from intelmonitor.pipeline.inference import ClaudeRefused, Inference, StepFailed
from intelmonitor.pipeline.params import CHARS_PER_TOKEN, HAIKU_MODEL, SCREEN_STATE_TOKENS
from intelmonitor.pipeline.subjects import monitored_subjects
from intelmonitor.seed import load_subjects
from intelmonitor.store.documents import SourceDocument

CATEGORIES: dict[str, str] = {
    "incident": "An operational, technology, cyber or conduct incident at an institution",
    "regulatory": "Rules, consultations, guidance, licensing, supervisory or enforcement action",
    "financial": "Earnings, capital, liquidity, asset quality or other financial results",
    "management": "Senior management or board changes",
    "strategy": "M&A, new products, technology moves, partnerships or market entry",
    "policy": "Monetary, fiscal, FX, trade, tariff or sanctions policy",
    "security": "Geopolitical, military or territorial security events",
    "market": "Market moves, prices, capital flows or depegs",
    "other": "None of the above",
}
Category = Literal[
    "incident",
    "regulatory",
    "financial",
    "management",
    "strategy",
    "policy",
    "security",
    "market",
    "other",
]

Stage = Literal["signal", "consultation", "proposed", "enacted", "enforced"]
STAGES: dict[Stage, str] = {
    "signal": "A speech, statement or report hinting at future regulation",
    "consultation": "A consultation paper or request for comment",
    "proposed": "A proposed or draft rule, bill or framework",
    "enacted": "A rule, law or framework that has been adopted or taken effect",
    "enforced": "Enforcement: a penalty, sanction, licence action or supervisory finding",
}

Jurisdiction = Literal[
    "sg", "my", "id", "th", "vn", "hk", "cn", "tw", "in", "au", "kr", "us", "eu", "uk", "jp", "intl"
]

JURISDICTIONS: dict[Jurisdiction, str] = {
    "sg": "Singapore",
    "my": "Malaysia",
    "id": "Indonesia",
    "th": "Thailand",
    "vn": "Vietnam",
    "hk": "Hong Kong",
    "cn": "mainland China",
    "tw": "Taiwan",
    "in": "India",
    "au": "Australia",
    "kr": "South Korea",
    "us": "the United States",
    "eu": "the European Union or euro area",
    "uk": "the United Kingdom",
    "jp": "Japan",
    "intl": "international bodies such as the BIS, FSB, Basel Committee, IMF or FATF",
}

# Display names for JURISDICTIONS, which are phrased for the screening prompt.
JURISDICTION_NAMES: dict[Jurisdiction, str] = {
    "sg": "Singapore",
    "my": "Malaysia",
    "id": "Indonesia",
    "th": "Thailand",
    "vn": "Vietnam",
    "hk": "Hong Kong",
    "cn": "China",
    "tw": "Taiwan",
    "in": "India",
    "au": "Australia",
    "kr": "South Korea",
    "us": "United States",
    "eu": "European Union",
    "uk": "United Kingdom",
    "jp": "Japan",
    "intl": "International",
}


def relevance_criterion() -> str:
    """What the dashboard's users track, from subjects.yaml: each topic with what it covers, and
    the entities by name."""
    seed = load_subjects()
    topics = "\n".join(f"- {t.name}: {t.covers}" for t in seed.topics)
    countries = ", ".join(e.name for e in seed.entities if e.kind == "country")
    sectors = ", ".join(e.name for e in seed.entities if e.kind == "sector")
    others = ", ".join(e.name for e in seed.entities if e.kind not in ("country", "sector"))
    return (
        f"Staff at the Monetary Authority of Singapore track these topics:\n{topics}\n"
        f"They also track these institutions and systems: {others}; the economies, markets and "
        f"financial systems of {countries}; and these sectors: {sectors}. A document is relevant "
        "if it reports a concrete development, decision, action, statement or data point on any "
        "of these, including an institution's results, incidents, regulation, management or "
        "strategy, and an economy's markets and policy. Passing mentions, personal finance "
        "tips, and companies or topics unrelated to these are not relevant."
    )


@lru_cache
def screen_system() -> str:
    """The fixed part of every screen call, byte for byte the same on each so it is cached: the
    task, the relevance criterion, the subjects, categories, stages and jurisdictions."""
    listed = monitored_subjects().values()
    entities = "\n".join(f"- {s.id}: {s.description}" for s in listed if s.type == "entity")
    topics = "\n".join(f"- {s.id}: {s.description}" for s in listed if s.type == "topic")
    categories = "\n".join(f"- {k}: {v}" for k, v in CATEGORIES.items())
    stages = "\n".join(f"- {k}: {v}" for k, v in STAGES.items())
    places = "\n".join(f"- {k}: {v}" for k, v in JURISDICTIONS.items())
    return f"""\
You screen documents for the monitoring dashboard of supervisors at the Monetary Authority of \
Singapore. Decide strictly from the document text.

relevant: is the document relevant? {relevance_criterion()}

subjects: the ids of the entities and topics below that the document is materially about, the \
most central first:
- every entity the document reports a development at, by or for (its results, a launch, a deal, \
an appointment, a penalty, an outage), or whose economy, markets or policy it reports on;
- every topic that development falls under.
Leave out an entity that is only quoted, cited as a source, named in a list or mentioned in \
passing. Use only these ids, and leave the list empty when the document is not relevant.

Entities:
{entities}

Topics:
{topics}

category: what kind of development the document mainly reports, one of:
{categories}

stage: when the category is regulatory, the stage the regulation has reached, one of:
{stages}
Otherwise null.

jurisdictions: the codes of the places whose actions, events or policy the document reports, \
from:
{places}"""


def screen_state(doc: SourceDocument) -> tuple[str, bool]:
    """Title, provenance and text for the screen. Text beyond the state budget is cut at a
    paragraph boundary, so long documents are screened on their title plus leading chunks."""
    header = (
        f"Title: {doc.title}\nSource: {doc.domain} ({doc.doc_type}), published "
        f"{doc.published_at:%Y-%m-%d}\n\n"
    )
    budget = SCREEN_STATE_TOKENS * CHARS_PER_TOKEN - len(header)
    if len(doc.text) <= budget:
        return header + doc.text, False
    kept: list[str] = []
    used = 0
    for chunk in doc.text.split("\n\n"):
        if used + len(chunk) + 2 > budget:
            break
        kept.append(chunk)
        used += len(chunk) + 2
    text = "\n\n".join(kept) if kept else doc.text[:budget]
    return header + text, True


class ScreenReply(BaseModel):
    relevant: bool
    subjects: list[str] = Field(description="Subject ids, the most central first")
    category: Category
    stage: Stage | None
    jurisdictions: list[Jurisdiction]


@lru_cache
def screen_reply() -> type[ScreenReply]:
    """ScreenReply with its subject ids limited to the monitored subjects' ids in the schema, so
    Haiku cannot answer with a jurisdiction code (uk, intl) or an id the seed does not have."""
    ids: list[JsonValue] = list(monitored_subjects())
    return create_model(
        "ScreenReply",
        __base__=ScreenReply,
        subjects=(
            list[str],
            Field(
                description="Subject ids, the most central first",
                json_schema_extra={"items": {"type": "string", "enum": ids}},
            ),
        ),
    )


class DocumentScreen(BaseModel):
    """The screening outcome for one document."""

    relevant: bool
    refused: bool  # Claude declined the document; it counts as not relevant
    category: Category
    stage: Stage | None
    # Tagged subject id -> its score from its place in the list: 1 for the most central, then
    # 1/2, 1/3 and so on, which the list pages break ties on.
    subjects: dict[str, float]
    jurisdictions: list[Jurisdiction]
    screened_chars: int
    truncated: bool


async def screen_document(inf: Inference, doc: SourceDocument) -> DocumentScreen:
    state, truncated = screen_state(doc)
    try:
        reply = await inf.claude(
            "screen", f"doc:{doc.id}", state, screen_reply(), screen_system(), model=HAIKU_MODEL
        )
    except ClaudeRefused:
        reply = None
    known = monitored_subjects()
    ids = list(dict.fromkeys(s for s in reply.subjects if s in known)) if reply else []
    relevant = reply is not None and reply.relevant
    return DocumentScreen(
        relevant=relevant,
        refused=reply is None,
        category=reply.category if reply else "other",
        stage=reply.stage if reply and reply.category == "regulatory" else None,
        subjects={s: 1 / (i + 1) for i, s in enumerate(ids)} if relevant else {},
        jurisdictions=list(dict.fromkeys(reply.jurisdictions)) if reply else [],
        screened_chars=len(state),
        truncated=truncated,
    )


def store_screen(conn: sqlite3.Connection, doc_id: str, s: DocumentScreen) -> None:
    """The screen's yes or no is stored as relevance 1 or 0 and each listed place as 1, in the
    columns that held Jev's probabilities."""
    conn.execute(
        "INSERT OR REPLACE INTO document_tags (document_id, relevance, relevant, "
        "relevance_source, category, category_confidence, stage, stage_confidence, "
        "subject_scores, jurisdictions, screened_chars, truncated) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            doc_id,
            float(s.relevant),
            int(s.relevant),
            "refused" if s.refused else "claude",
            s.category,
            1.0,
            s.stage,
            1.0 if s.stage else None,
            json.dumps(s.subjects),
            json.dumps(dict.fromkeys(s.jurisdictions, 1.0)),
            s.screened_chars,
            int(s.truncated),
        ),
    )
    conn.execute("DELETE FROM document_subjects WHERE document_id = ?", (doc_id,))
    conn.executemany(
        "INSERT INTO document_subjects (document_id, subject_id, confidence, source) "
        "VALUES (?, ?, ?, 'claude')",
        [(doc_id, sid, score) for sid, score in s.subjects.items()],
    )
    conn.commit()


async def screen_documents(
    inf: Inference, conn: sqlite3.Connection, docs: list[SourceDocument]
) -> dict[str, DocumentScreen]:
    """Screen every document (cached calls are free) and return the outcomes by document id.
    Failures are collected so the other documents still finish; the step then raises."""
    results: dict[str, DocumentScreen] = {}
    failures: list[str] = []

    async def one(doc: SourceDocument) -> None:
        try:
            results[doc.id] = outcome = await screen_document(inf, doc)
        except Exception as error:
            failures.append(f"{doc.id}: {type(error).__name__}: {error}")
            return
        store_screen(conn, doc.id, outcome)

    await asyncio.gather(*(one(d) for d in docs))
    if failures:
        raise StepFailed("screen", failures)
    return results
