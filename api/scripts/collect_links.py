"""Collect links of the 21 collected types for each entity from the web (ticket 03 of
link-types-and-inferred-impact, step 5 of its spec) into api/seed/collected/links-new-types.yaml.

For each entity, one Claude Sonnet 5.5 turn with the web search and web fetch server tools
(pipeline/claude.py `ask_claude_web`): the model searches and opens pages as often as it chooses,
the script sends the turn back on every pause until the model ends it, with no cap on searches or
resumes, and the answer comes back as structured output. The system prompt (COLLECTION_PROMPT)
holds the deck's identification rules 1 to 8 (16 Nov 2026, page 5), the 22 types with their
meanings and examples (LINK_TYPES, pages 1 to 4), the materiality rule, the entities a link may run
to (all 142, hidden ones included) and what finished means; it ends with THINK_FIRST. Adaptive
thinking at effort high: ticket 03 names no effort, and the priority pick is high.

Finished means the model ended its turn and its answer has an entry for each of the 21 collected
types (every type but belongs_to_sector, which comes from the seed's groups): the rows found, or
none with the search queries tried. An answer that leaves a type out, or gives a type neither rows
nor queries, is rejected and the model is asked again in the same conversation, ATTEMPTS answers in
all; an API error is retried the same way. An entity that ends otherwise (a refusal, max_tokens,
an API error on every attempt, the rejections used up, or its cost passing --stop-at-usd) is listed
as incomplete, keeps no rows and is collected again on the next run.

A finished entity's result is kept in data/link-collection/<prompt version>/<entity>.json (the
whole conversation under transcripts/, and the text of each page a row cites under pages/, by
content hash); a later run makes no call for an entity that has a result under the current
version. Results under another version are left where they are and not read: the
entity is collected again, a turn that ends incomplete leaves it no rows, and putting an old prompt
back finds its results again. The version is made from the prompt's template, which holds a
placeholder where the list of entities goes, so a change to the seed's entities does not make every
result stale; the filters below run on every write, so a row to an entity that has left the seed is
dropped then. Each run filters every kept result again and writes the rows that pass to --out. Code
keeps a row only if both ends are seed entities, the type is one of the 21, the link is not already
stored, the quote is found in the page the request fetched from the row's source URL (each
side's text normalised: markdown links, footnote markers and invisible characters out, quotes,
dashes and spaces unified, case ignored), the quote names at least one of the two entities and the
page names both (`names`). Stored means built from a collected file other than
links-new-types.yaml (build_relationships.build_collected), so a run after a build keeps the rows
the file itself put into relationships.yaml.

Each row that passes those filters then goes to the check (CHECK_PROMPT): one tool-free call
through pipeline/claude.py `ask_claude`, so it follows settings.claude_via_api, on Claude Sonnet
5.5 at effort high with adaptive thinking. It reads the row, its two entities and type, and the
passage of the page around the quote (WINDOW characters either side), and answers whether the
passage shows the relationship, by six tests in order (evidence, parties, type, direction,
material, current), naming the first that fails. Only a supported row is kept. Each verdict is
stored under data/link-collection/checks/<check version>/, keyed by the row's content, so a later
run makes no call for a row already checked; a refused or failed check stores nothing, and the row
is left out, listed as unchecked and asked again on the next run.

The file holds one row per link: of the supported rows that hold one link, across every result,
the row with the newest source date is kept, then the row from the result of the link's `from`
entity, then of its `to` entity (`one_per_link`); the others are dropped as a repeat. Dropped rows
are listed with the reason. Kept rows are written with evidence `reported`, the prompt version,
the check version and the date collected; the file lists the versions its rows name in `prompts`
and `checks`, as transmission.yaml does.

The prompt, its schema, the model and the effort make a prompt version, whose id is made as
pipeline/prompts.py makes a stage's; the check's make another, of stage link_check.

Each entity's usage is recorded and printed: searches, page fetches, resumes, input tokens
(uncached, cache writes, cache reads), output tokens and the measured cost, web search fee
included. The running cost is printed after every resume. Recall is reported per entity: of its
stored links of the 21 types, how many the candidates found again on both ends and
type, with matches on the ends alone beside them, counted before the "already stored" filter.
The check is reported per entity (rows checked, supported, not supported by test, unchecked) and
its calls and measured cost for the run.

Usage (from api/):
  uv run python scripts/collect_links.py [--entities dbs,singtel] [--stop-at-usd 15]
      [--out <file>.yaml] [--report <dir>] [--concurrency N]
--entities limits the run to those ids (default: every entity); --stop-at-usd stops resending an
entity's turn once its measured cost passes the figure; --out writes the kept rows elsewhere (a
trial keeps them out of the build); --report writes kept-rows.csv, dropped-rows.csv (both with the
check's verdict, failed test and reason) and report.txt.
"""

import argparse
import asyncio
import base64
import csv
import hashlib
import io
import json
import re
import time
from bisect import bisect_right
from contextlib import redirect_stdout
from dataclasses import dataclass, replace
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

import build_relationships as building
import yaml
from anthropic.types.beta import BetaMessage, BetaMessageParam
from pydantic import BaseModel, ConfigDict, Field

from intelmonitor.config import DATA_DIR, SEED_DIR
from intelmonitor.ingest.parse import canonical_url, pdf_to_text
from intelmonitor.pipeline.claude import (
    ClaudeBilled,
    ClaudeRefusal,
    WebUsage,
    ask_claude,
    ask_claude_web,
)
from intelmonitor.pipeline.prompts import THINK_FIRST, schema_text, version_id
from intelmonitor.relationships import (
    LINK_TYPES,
    REL_TYPES,
    SYMMETRIC,
    LinkType,
    PromptVersion,
    Relationship,
)
from intelmonitor.seed import Entity, load_subjects

STAGE = "link_collection"
MODEL = "claude-sonnet-5-5"
EFFORT: Literal["high"] = "high"
ATTEMPTS = 3
CONCURRENCY = 4
OUT = SEED_DIR / "collected" / "links-new-types.yaml"
RESULTS = DATA_DIR / "link-collection"
PAGES = RESULTS / "pages"  # the text of each page a row cites, by content hash

# Every type but belongs_to_sector, which the build derives from the seed's groups.
CollectedType = Literal[
    "holds_stake_in",
    "lends_to",
    "invests_in",
    "funds",
    "insures",
    "provides_technology_to",
    "provides_connectivity_to",
    "provides_business_service_to",
    "processes_transactions_for",
    "shares_or_processes_data_with",
    "distributes_products_for",
    "provides_customer_benefits_with",
    "jointly_offers_product_with",
    "material_customer_of",
    "material_supplier_to",
    "regulated_by",
    "operates_in",
    "exposed_to",
    "member_of",
    "participates_in",
    "listed_on",
]
COLLECTED_TYPES: tuple[CollectedType, ...] = CollectedType.__args__
assert COLLECTED_TYPES == tuple(t for t in REL_TYPES if t != "belongs_to_sector")


def entity_line(e: Entity, names: dict[str, str]) -> str:
    """An entity as the list in the prompt gives it: id, name, kind, other names, a region's
    members."""
    parts = [e.kind.replace("_", " ")]
    if e.aliases:
        parts.append("also " + "; ".join(e.aliases))
    if e.members:
        parts.append("covers " + ", ".join(names[m] for m in e.members))
    return f"- {e.id}: {e.name} ({'; '.join(parts)})"


def types_text() -> str:
    """The 22 types in the deck's three groups, each with its meaning and example."""
    lines: list[str] = []
    group = ""
    for t, lt in LINK_TYPES.items():
        if lt.group != group:
            group = lt.group
            lines.append(f"{group}:")
        lines.append(f"- {t}: {lt.meaning}. Example: {lt.example}")
    return "\n".join(lines)


SEED = load_subjects()
_NAMES = {e.id: e.name for e in SEED.entities}
_ENTITIES = {e.id: e for e in SEED.entities}
_TYPES: dict[str, LinkType] = {
    t: lt for t, lt in LINK_TYPES.items()
}  # LINK_TYPES, by a candidate's type

# The deck of 16 Nov 2026: page 5's opening and identification rules 1 to 8 word for word, the
# types from LINK_TYPES (pages 1 to 4). The deck has no separate materiality sentence; the rule
# below quotes the types' own "Material ..." meanings and ticket 03's example (Q4). The paragraph
# on payment systems and market infrastructures is ticket 02's P2, which the stored links follow
# (a system processes_transactions_for its participant). The framing
# around them (one entity against the list, the web as the evidence, the quote and what finished
# means) is ours. No JSON shape: the answer's fields are described in its schema (ticket 09).
COLLECTION_TEMPLATE = f"""\
You are identifying evidenced relationships between Entity A and Entity B.
A pair of entities may have multiple relationship types. Return each distinct relationship \
separately.
Here Entity A is the entity named in the message, and Entity B is any other entity on the \
ENTITIES list below. The evidence is what you find with web search and read with web fetch.

ALLOWED RELATIONSHIP TYPES
Each relationship reads from its first entity to its second as its type's name says \
(A holds_stake_in B, A lends_to B, A material_customer_of B, A regulated_by B). \
{" and ".join(SYMMETRIC)} have no direction.
{types_text()}
belongs_to_sector is taken from the entity list's groups: do not look for it.
A firm's participation in, membership of or settlement through an entity whose kind is payment \
system or market infrastructure is stored as that system processes_transactions_for the firm \
(FAST processes_transactions_for Bank A), not as participates_in or member_of. participates_in \
is for schemes, programmes and initiatives, and member_of for associations and networks that are \
not payment systems or market infrastructures. A company whose securities trade on an exchange \
is listed_on it.

MATERIALITY
A relationship must be material as its type's meaning says: "Material lending or credit \
exposure", "Material investment other than lending", "Material funding or liquidity provision", \
"Material insurance coverage", "Material outsourced or professional service", "Customer is \
materially important to the provider", "Material supply of goods, equipment or non-technology \
inputs", "Material operations in a jurisdiction", "Material economic, country or market \
exposure". A bank's ordinary interbank line to another bank is not a lends_to relationship.

RELATIONSHIP IDENTIFICATION RULES
1. Return every distinct relationship between the entities that is supported by the provided \
evidence.
2. A pair of entities may have several relationships. Do not combine them into one generic \
relationship.
3. Use only the 22 allowed relationship types.
4. Store the relationship in its canonical direction. Do not create inverse types such as \
owned_by, borrower_of or serviced_by.
5. The relationship reason must be one concise factual sentence capturing the relevant service, \
product, geography, ownership, exposure, amount, percentage, concentration or other material \
detail.
6. Do not infer a relationship merely because the entities operate in the same industry, \
geography or ecosystem, or appear together in news.
7. Select the most specific allowed relationship type. Use relationship_reason to capture the \
precise product, service, equipment, customer arrangement, geography or financial exposure. Do \
not create a new type where an existing allowed type already describes the pathway.
8. If no allowed relationship is sufficiently evidenced, return an empty relationships array.

EVIDENCE
Open every page you rely on with web fetch. Each relationship names that page's URL and title, \
the date it was published (or the date of the fact it reports), and a quote: one passage copied \
word for word from the fetched page that shows the relationship, with no ellipsis, no joined \
pieces and no paraphrase. Each quote is checked against the page.

WHEN YOU ARE FINISHED
You are finished when you have searched for every one of the 21 types other than \
belongs_to_sector, for relationships between Entity A and the entities on the list. Your answer \
has one entry for each of those 21 types: the relationships of that type you found, or, when you \
found none, the search queries you tried for it.

ENTITIES
{{entities}}

{THINK_FIRST}"""
# The system prompt: the template with the seed's entities listed.
COLLECTION_PROMPT = COLLECTION_TEMPLATE.replace(
    "{entities}", "\n".join(entity_line(e, _NAMES) for e in SEED.entities)
)
# Stored with the version when it is first used; edit it with the prompt.
PROMPT_NOTE = (
    "The deck's identification rules 1 to 8 (16 Nov 2026, page 5), the 22 types with their "
    "meanings, the materiality rule, ticket 02's P2 for payment systems and market "
    "infrastructures, the seed's entities and what finished means, with THINK_FIRST; web "
    "search and web fetch, effort high."
)


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_by_name=True)


class Found(_Out):
    from_: str = Field(
        alias="from",
        description="The id, from the entity list, of the relationship's first entity",
    )
    to: str = Field(description="The id, from the entity list, of its second entity")
    detail: str = Field(
        description="The relationship reason (rule 5): one concise factual sentence"
    )
    source_url: str = Field(description="The URL of the page fetched with web fetch")
    source_title: str = Field(description="The page's title")
    quote: str = Field(description="One passage copied word for word from the fetched page")
    as_of: date | None = Field(
        description="The date the source was published, or of the fact it reports; null when "
        "the page gives none"
    )


class TypeEntry(_Out):
    type: CollectedType
    relationships: list[Found] = Field(
        description="The relationships of this type found; empty when none was found"
    )
    queries: list[str] = Field(description="The web search queries tried for this type")


class Collection(_Out):
    entries: list[TypeEntry] = Field(
        description="One entry for each of the 21 types other than belongs_to_sector"
    )


VERSION = version_id(
    STAGE, COLLECTION_TEMPLATE, schema_text(Collection.model_json_schema()), MODEL, EFFORT
)


def user_prompt(e: Entity) -> str:
    lines = [f"Entity A: {e.name} (id {e.id}; {e.kind.replace('_', ' ')})"]
    if e.aliases:
        lines.append(f"Also known as: {'; '.join(e.aliases)}")
    if e.domains:
        lines.append(f"Website: {', '.join(e.domains)}")
    if e.covers:
        lines.append(f"Covers: {e.covers}")
    if e.members:
        lines.append(f"Members: {', '.join(_NAMES[m] for m in e.members)}")
    lines.append(
        f"Find every relationship between {e.name} and the other entities on the list, of each "
        "of the 21 types."
    )
    return "\n".join(lines)


def gaps(c: Collection) -> list[str]:
    """Why the answer is not finished: each type with no entry, and each entry with neither rows
    nor queries. Empty when it is finished."""
    entries: dict[str, list[TypeEntry]] = {}
    for x in c.entries:
        entries.setdefault(x.type, []).append(x)
    found: list[str] = []
    for t in COLLECTED_TYPES:
        xs = entries.get(t, [])
        if not xs:
            found.append(f"{t} has no entry")
        elif not any(x.relationships or x.queries for x in xs):
            found.append(f"{t} lists neither relationships nor the queries tried")
    return found


# Normalising a quote and a page alike before the quote is looked for in the page.
_MD_LINK = re.compile(r"!?\[([^\[\]]*)\]\([^)]*\)")
_FOOTNOTE = re.compile(r"\[\d+\]")
_MARKUP = re.compile(r"[*_`#|>]+")
_CHARS = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "‑": "-",
        " ": " ",
        "…": "...",
        # Invisible characters, which some pages put inside names ("United Overseas ​Bank")
        "​": None,
        "‌": None,
        "‍": None,
        "⁠": None,
        "﻿": None,
        "­": None,
    }
)


def normal(text: str) -> str:
    """Text with markdown links reduced to their words, footnote markers, markup and invisible
    characters out, quotes, dashes and spaces unified, in lower case."""
    for _ in range(2):  # "[[12]](url)" takes two passes
        text = _MD_LINK.sub(r"\1", text)
    text = _MARKUP.sub(" ", _FOOTNOTE.sub(" ", text.translate(_CHARS)))
    return re.sub(r"\s+", " ", text).strip().lower()


def _and(text: str) -> str:
    """Normalised text with "&" read as "and", for matching names."""
    return re.sub(r"\s+", " ", text.replace("&", " and "))


_LEGAL_FORM = re.compile(
    r"(?:\s+(?:corporation|corp|company|co|limited|ltd|inc|plc|pte|private|bhd|berhad|ag|sa|se|"
    r"tbk|llc|\(pte\)|\(private\)|\(singapore\)))+\.?$"
)


def _name_pattern(e: Entity) -> re.Pattern[str]:
    """The entity's name and aliases as whole words in normalised text (see `names`), each also
    without a trailing legal form when two words or more are left ("Oversea-Chinese Banking
    Corporation" also matches "Oversea-Chinese Banking Corp")."""
    forms = {_and(normal(n)) for n in [e.name, *e.aliases]}
    cores = {c for f in forms if len((c := _LEGAL_FORM.sub("", f)).split()) >= 2}
    words = sorted((forms | cores) - {""}, key=len, reverse=True)
    return re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, words)) + r")(?!\w)")


_NAME_PATTERNS = {e.id: _name_pattern(e) for e in SEED.entities}


def names(entity: str, text: str) -> bool:
    """Whether normalised `text` names the entity: its name or an alias as whole words, case
    ignored, after the quote check's normalising, "&" read as "and".

    It wrongly keeps a row when a name is also a common word or a short acronym, since case is
    ignored (Circle, Grab, Wise, Visa, Apple, Meta, FAST, Fed, BOT, US as "us", SEC, HK, a
    sector's "construction" or "mortgages"); when a country is named only inside another entity's
    name (China in Bank of China, Singapore in Monetary Authority of Singapore); when the entity is
    named but is not a party to what the quote shows; and on long pages such as annual reports,
    which name nearly every entity. It wrongly drops a row when the quote refers to an entity by a
    pronoun, "the Bank" or "the Group", or by a name that is not among its aliases (Amazon for
    AWS, a subsidiary, product or ticker, a demonym such as Chinese); when it names a region's
    member country, or a country's city, instead of the region or country; and when markup the
    normalising leaves in (an HTML entity, say) breaks a name."""
    return _NAME_PATTERNS[entity].search(_and(text)) is not None


def fetched_pages(responses: list[BetaMessage]) -> dict[str, str]:
    """The text of every page the turn fetched, by canonical URL; a PDF's text is read from every
    page."""
    pages: dict[str, str] = {}
    for r in responses:
        for b in r.content:
            if b.type != "web_fetch_tool_result" or b.content.type != "web_fetch_result":
                continue
            source = b.content.content.source
            if source.type == "text":
                text = source.data
            else:
                try:
                    text = pdf_to_text(base64.b64decode(source.data), max_pages=None)
                except (RuntimeError, ValueError):
                    continue
            pages[canonical_url(b.content.url)] = text
    return pages


def save_page(text: str) -> str:
    """Keep a fetched page's text under PAGES, named by its content hash, which is returned."""
    h = hashlib.sha256(text.encode()).hexdigest()[:16]
    path = PAGES / f"{h}.txt"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return h


@lru_cache(maxsize=64)
def page_text(h: str) -> tuple[str, str]:
    """A kept page's text and its normalised text, by content hash."""
    text = (PAGES / f"{h}.txt").read_text()
    return text, normal(text)


QuoteCheck = Literal["found", "page not fetched", "not in page"]


class Candidate(BaseModel):
    """A row the model returned, with its type, the quote check made against the turn's fetched
    pages, and the content hash of the page fetched from its source URL (kept under PAGES; None
    when the turn fetched no such page)."""

    from_: str = Field(alias="from")
    to: str
    type: str
    detail: str
    source_url: str
    source_title: str
    quote: str
    as_of: date | None
    quote_check: QuoteCheck
    page: str | None = None

    model_config = ConfigDict(validate_by_name=True, serialize_by_alias=True)

    def link_id(self) -> str:
        src, dst = self.from_, self.to
        if self.type in SYMMETRIC:
            src, dst = min(src, dst), max(src, dst)
        return f"{src}:{self.type}:{dst}"


class CoverageEntry(BaseModel):
    type: str
    rows: int
    queries: list[str]


class Result(BaseModel):
    """A finished entity, as data/link-collection/<entity>.json keeps it."""

    entity: str
    prompt_version: str
    finished: date
    model: str
    attempts: int
    wall_seconds: float
    usage: dict[str, float]
    coverage: list[CoverageEntry]
    candidates: list[Candidate]


class Outcome(BaseModel):
    """One entity's run: its result when finished, else why it is incomplete; its usage."""

    entity: str
    result: Result | None
    incomplete: str | None
    usage: dict[str, float]
    wall_seconds: float


def candidates(c: Collection, pages: dict[str, str]) -> list[Candidate]:
    """The answer's rows, each with its quote checked against the page fetched from its source
    URL; every such page is kept under PAGES."""
    out: list[Candidate] = []
    for x in c.entries:
        for f in x.relationships:
            text = pages.get(canonical_url(f.source_url))
            h = None if text is None else save_page(text)
            q: QuoteCheck = (
                "page not fetched"
                if h is None
                else "found"
                if normal(f.quote) and normal(f.quote) in page_text(h)[1]
                else "not in page"
            )
            out.append(Candidate(**f.model_dump(by_alias=True), type=x.type, quote_check=q, page=h))
    return out


def save_transcript(e: Entity, rs: list[BetaMessage]) -> None:
    """The turn's user prompt and every response, for reading rows against their sources."""
    path = RESULTS / "transcripts" / f"{e.id}-{VERSION}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "prompt_version": VERSION,
        "user": user_prompt(e),
        "responses": [r.model_dump(mode="json") for r in rs],
    }
    path.write_text(json.dumps(doc, indent=1))


async def collect(e: Entity, stop_at: float | None, sem: asyncio.Semaphore) -> Outcome:
    """Run one entity's turn until it is finished or incomplete (see the module docstring)."""
    usage = WebUsage()
    start = time.monotonic()

    def keep_going(u: WebUsage) -> bool:
        print(
            f"  {e.id}: resume {u.resumes}, {u.searches} searches, {u.fetches} fetches, "
            f"${u.cost_usd:.4f} so far",
            flush=True,
        )
        return stop_at is None or u.cost_usd <= stop_at

    def done(incomplete: str | None, result: Result | None = None) -> Outcome:
        return Outcome(
            entity=e.id,
            result=result,
            incomplete=incomplete,
            usage=usage.__dict__.copy(),
            wall_seconds=round(time.monotonic() - start, 1),
        )

    messages: list[BetaMessageParam] = [{"role": "user", "content": user_prompt(e)}]
    responses: list[BetaMessage] = []
    error = ""
    async with sem:
        print(f"{e.id}: collecting", flush=True)
        for attempt in range(1, ATTEMPTS + 1):
            try:
                turn = await ask_claude_web(
                    messages, Collection, COLLECTION_PROMPT, MODEL, EFFORT, usage, keep_going
                )
            except Exception as x:  # an API error or an answer that is not the schema
                error = f"{type(x).__name__}: {x}"
                print(f"  {e.id}: attempt {attempt} failed: {error}", flush=True)
                continue
            responses += turn.responses
            if turn.stop_reason == "stopped":
                save_transcript(e, responses)
                return done(f"cost passed --stop-at-usd {stop_at} (${usage.cost_usd:.4f})")
            if turn.output is None:
                save_transcript(e, responses)
                why = f"refused ({turn.refusal})" if turn.refusal else turn.stop_reason
                return done(f"ended with {why}")
            missing = gaps(turn.output)
            if missing:
                print(f"  {e.id}: attempt {attempt} rejected: {'; '.join(missing)}", flush=True)
                error = "answer rejected: " + "; ".join(missing)
                messages = [
                    *turn.messages,
                    {
                        "role": "user",
                        "content": "Your answer is not finished: "
                        + "; ".join(missing)
                        + ". Search for those types, then answer again in full, with one entry "
                        "for each of the 21 types.",
                    },
                ]
                continue
            save_transcript(e, responses)
            seconds = round(time.monotonic() - start, 1)
            result = Result(
                entity=e.id,
                prompt_version=VERSION,
                finished=date.today(),
                model=responses[-1].model,
                attempts=attempt,
                wall_seconds=seconds,
                usage=usage.__dict__.copy(),
                coverage=[
                    CoverageEntry(type=x.type, rows=len(x.relationships), queries=list(x.queries))
                    for x in sorted(
                        turn.output.entries, key=lambda x: COLLECTED_TYPES.index(x.type)
                    )
                ],
                candidates=candidates(turn.output, fetched_pages(responses)),
            )
            path = RESULTS / VERSION / f"{e.id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(result.model_dump_json(indent=1, by_alias=True))
            return done(None, result)
        if responses:
            save_transcript(e, responses)
        return done(f"no finished answer in {ATTEMPTS} attempts ({error})")


def load_results() -> dict[str, Result]:
    """The finished results under the current prompt version, by entity id."""
    return {
        p.stem: Result.model_validate_json(p.read_text())
        for p in sorted((RESULTS / VERSION).glob("*.json"))
    }


# The check reads this many characters of page text either side of the quote: enough for the
# paragraph, its heading and the section around it (whose list a name is in, what a figure
# counts, the date), about 1,500 tokens in all, while an annual report's million characters stay
# out.
WINDOW = 3000


def passage(text: str, quote: str) -> str:
    """The page text around the quote, WINDOW characters either side, cut at spaces, "[...]"
    where it is cut. The quote is found in the normalised text line by line and placed back in
    the page's own text; the page's middle stands in when it is not found that way (a markdown
    link across a line break, say)."""
    q = normal(quote)
    starts: list[int] = []  # each line's start in the page's text
    offsets: list[int] = []  # and in the normalised lines joined by spaces
    norms: list[str] = []
    at = pos = 0
    for line in text.splitlines(keepends=True):
        if n := normal(line):
            starts.append(at)
            offsets.append(pos)
            norms.append(n)
            pos += len(n) + 1
        at += len(line)
    k = " ".join(norms).find(q) if q else -1
    if k < 0:
        centre, span = len(text) // 2, len(quote)
    else:
        i = bisect_right(offsets, k) - 1
        line_len = len(text[starts[i] :].split("\n", 1)[0]) or 1
        centre = starts[i] + (k - offsets[i]) * line_len // max(len(norms[i]), 1)
        span = len(quote) * 2  # room for the markup the normalising took out
    lo, hi = max(0, centre - WINDOW), min(len(text), centre + span + WINDOW)
    if lo > 0:
        lo = text.find(" ", lo) + 1 or lo
    if hi < len(text):
        hi = text.rfind(" ", lo, hi) if " " in text[lo:hi] else hi
    return ("[...] " if lo > 0 else "") + text[lo:hi].strip() + (" [...]" if hi < len(text) else "")


CHECK_STAGE = "link_check"
CHECKS = RESULTS / "checks"  # each verdict, under the check's version, by the row's content
CHECK_CONCURRENCY = 8

# The check: one tool-free call per row that passes the code filters. Rules 1, 3, 4, 6 and 7 are
# the deck's (16 Nov 2026, page 5) word for word, the types are LINK_TYPES (pages 1 to 4), and the
# materiality rule and the P2 paragraph are the collection prompt's. The frame (one row against
# its passage), rule 8 read for one row, the six tests, the loan book, the programme's owner and
# what a quote must show are ours. No JSON shape: the fields are described in the schema
# (ticket 09).
CHECK_PROMPT = f"""\
You are checking one relationship between Entity A and Entity B that was found on the web, \
against a passage of the page it was found on. The message gives the two entities, the \
relationship's type, its reason, the page's title, URL and date, the quote it was found with, \
and the passage of the page around that quote.

The relationship was identified under these rules:
1. Return every distinct relationship between the entities that is supported by the provided \
evidence.
3. Use only the 22 allowed relationship types.
4. Store the relationship in its canonical direction. Do not create inverse types such as \
owned_by, borrower_of or serviced_by.
6. Do not infer a relationship merely because the entities operate in the same industry, \
geography or ecosystem, or appear together in news.
7. Select the most specific allowed relationship type. Use relationship_reason to capture the \
precise product, service, equipment, customer arrangement, geography or financial exposure. Do \
not create a new type where an existing allowed type already describes the pathway.
Under rule 8, a relationship that is not sufficiently evidenced is not returned. Your task is to \
say whether this one is.

ALLOWED RELATIONSHIP TYPES
Each relationship reads from its first entity to its second as its type's name says \
(A holds_stake_in B, A lends_to B, A material_customer_of B, A regulated_by B). \
{" and ".join(SYMMETRIC)} have no direction.
{types_text()}
A firm's participation in, membership of or settlement through an entity whose kind is payment \
system or market infrastructure is stored as that system processes_transactions_for the firm \
(FAST processes_transactions_for Bank A), not as participates_in or member_of. participates_in \
is for schemes, programmes and initiatives, and member_of for associations and networks that are \
not payment systems or market infrastructures. A company whose securities trade on an exchange \
is listed_on it.
A bank's loan book by sector or country is exposed_to toward the sector or country entity; \
lends_to is for a loan or facility to a named borrower that is itself one of the two entities.
participates_in is used only when the scheme, programme or initiative is itself the second \
entity; a programme's owner, operator or sponsor does not stand in for it.

MATERIALITY
A relationship must be material as its type's meaning says: "Material lending or credit \
exposure", "Material investment other than lending", "Material funding or liquidity provision", \
"Material insurance coverage", "Material outsourced or professional service", "Customer is \
materially important to the provider", "Material supply of goods, equipment or non-technology \
inputs", "Material operations in a jurisdiction", "Material economic, country or market \
exposure". A bank's ordinary interbank line to another bank is not a lends_to relationship.

THE TESTS
The relationship is supported only if the passage shows it and it passes every test below. \
When it fails, name the first test it fails, in this order.
- evidence: the passage itself shows the relationship, and the quote is a passage that shows \
it and names Entity A or Entity B. A heading, a code or a name alone is not a quote that shows a \
relationship, and two entities named on one page, in one list or in one story are not a \
relationship.
- parties: the relationship is between these two entities, not between one of them and a third \
party. A unit of an entity, such as its branch in a country or a subsidiary that carries its \
name, counts as the entity; a vehicle, consortium or joint venture it belongs to, a company the \
two own together, or the customers of either do not.
- type: the type is the most specific allowed type for what the passage shows, and not a \
neighbouring one, under the meanings and the paragraphs above.
- direction: the relationship reads from its first entity to its second as the type's name \
says. A type without direction passes.
- material: the relationship is material as the type's meaning says, to the business of at \
least one of the two entities.
- current: the relationship stands as of the date the row was collected, as far as the page \
shows. A one-off event that is over, such as a single transaction or an event held, fails. An \
arrangement that runs over time, such as a service, a facility, a stake, a product the two offer \
or operations in a country, is taken to stand unless the page shows that it ended or was \
replaced, or the latest date on the page (published or updated) is more than five years before \
the row was collected.

{THINK_FIRST}"""
CHECK_NOTE = (
    "One row against the passage around its quote: the deck's rules 1, 3, 4, 6 and 7 (16 Nov "
    "2026, page 5), the 22 types with their meanings, P2, the loan book and programme's owner "
    "rules, the materiality rule, and six tests (evidence, parties, type, direction, material, "
    "current, with a five-year horizon for an ongoing arrangement), with THINK_FIRST; no tools, "
    "effort high."
)

CheckTest = Literal["evidence", "parties", "type", "direction", "material", "current"]


class CheckAnswer(_Out):
    supported: bool = Field(
        description="Whether the passage shows this relationship and it passes every test"
    )
    failed_test: CheckTest | None = Field(
        description="When it is not supported, the first test it fails, in the order the tests "
        "are listed; null when it is supported"
    )
    reason: str = Field(description="One sentence explaining the answer")


CHECK_VERSION = version_id(
    CHECK_STAGE, CHECK_PROMPT, schema_text(CheckAnswer.model_json_schema()), MODEL, EFFORT
)


class Checked(BaseModel):
    """A stored verdict: the check's answer for one row's content under one check version, with
    the call's measured cost."""

    version: str
    link: str
    supported: bool
    failed_test: CheckTest | None
    reason: str
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    checked: date


def check_message(c: Candidate, collected: date) -> str:
    """The check's user message: the row, its two entities, its type and the passage."""
    a, b = _ENTITIES[c.from_], _ENTITIES[c.to]
    lt = _TYPES[c.type]
    reads = (
        f"{c.type} has no direction."
        if c.type in SYMMETRIC
        else f"It reads from the first entity to the second: {a.name} {c.type} {b.name}."
    )
    assert c.page
    return "\n".join(
        [
            f"First entity (Entity A): {entity_line(a, _NAMES)[2:]}",
            f"Second entity (Entity B): {entity_line(b, _NAMES)[2:]}",
            f"Relationship: {a.name} {c.type} {b.name}",
            f"Type {c.type} ({lt.group}): {lt.meaning}. Example: {lt.example}",
            reads,
            f"Reason given: {c.detail}",
            f"Source: {c.source_title}",
            f"URL: {c.source_url}",
            f"Date of the source: {c.as_of or 'none given'}",
            f"Row collected on: {collected}",
            f"Quote: {c.quote}",
            "",
            'Passage of the page around the quote ("[...]" marks where it is cut):',
            "<passage>",
            passage(page_text(c.page)[0], c.quote),
            "</passage>",
        ]
    )


def row_key(c: Candidate, collected: date) -> str:
    """A row's content, as the check reads it, hashed: a verdict is stored under it."""
    parts = [c.from_, c.type, c.to, c.detail, c.source_url, c.source_title, c.quote]
    parts += [str(c.as_of), c.page or "", str(collected)]
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:16]


@dataclass
class CheckSpend:
    """The check's calls in this run (failed attempts included) and their measured cost."""

    calls: int = 0
    cost_usd: float = 0.0


async def check_row(
    c: Candidate, collected: date, sem: asyncio.Semaphore, spend: CheckSpend
) -> Checked | str:
    """The row's stored verdict, or a new one from the check, stored; when the check refuses or
    fails ATTEMPTS times, why, and nothing is stored, so the row is asked again next run."""
    path = CHECKS / CHECK_VERSION / f"{row_key(c, collected)}.json"
    if path.exists():
        return Checked.model_validate_json(path.read_text())
    error = ""
    async with sem:
        for _ in range(ATTEMPTS):
            try:
                reply = await ask_claude(
                    check_message(c, collected), CheckAnswer, CHECK_PROMPT, MODEL, EFFORT
                )
            except ClaudeRefusal as x:
                spend.calls += 1
                spend.cost_usd += x.cost_usd or 0.0
                return f"refused ({x.category or 'no category'})"
            except Exception as x:  # an API error, a cut-off or an answer that is not the schema
                spend.calls += 1
                if isinstance(x, ClaudeBilled):
                    spend.cost_usd += x.cost_usd or 0.0
                error = f"{type(x).__name__}: {x}"
                continue
            spend.calls += 1
            spend.cost_usd += reply.cost_usd or 0.0
            out = reply.output
            verdict = Checked(
                version=CHECK_VERSION,
                link=c.link_id(),
                supported=out.supported,
                failed_test=None if out.supported else out.failed_test,
                reason=out.reason,
                model=reply.model_version,
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
                cost_usd=reply.cost_usd,
                checked=date.today(),
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(verdict.model_dump_json(indent=1))
            return verdict
    return f"failed {ATTEMPTS} times ({error})"


@dataclass
class Verdict:
    """A candidate, why it is left out (by code, by the check, or as a repeat; None when it is
    kept), and the check's verdict when it has one."""

    c: Candidate
    drop: str | None
    check: Checked | None = None


def dropped_by_code(c: Candidate, entities: set[str], stored: set[str]) -> str | None:
    """Why code drops the candidate, the first reason that applies; None when it passes."""
    outside = [x for x in (c.from_, c.to) if x not in entities]
    if outside:
        return f"an end is not a seed entity: {', '.join(outside)}"
    if c.type not in COLLECTED_TYPES:
        return "type is not one of the 21 collected types"
    if c.link_id() in stored:
        return "already stored"
    if c.quote_check == "page not fetched":
        return "source page not fetched in the request"
    if c.quote_check == "not in page":
        return "quote not found in the fetched page"
    quote = normal(c.quote)
    if not (names(c.from_, quote) or names(c.to, quote)):
        return "quote names neither entity"
    assert c.page, f"{c.link_id()}: a found quote's page is not kept under {PAGES}"
    page = page_text(c.page)[1]
    if not (names(c.from_, page) and names(c.to, page)):
        return "fetched page does not name both entities"
    return None


def judge(r: Result, entities: set[str], stored: set[str]) -> list[Verdict]:
    """Each candidate with the reason code drops it, or None when it passes."""
    return [Verdict(c, dropped_by_code(c, entities, stored)) for c in r.candidates]


NOT_SUPPORTED = "not supported by the check"
UNCHECKED = "unchecked"


async def check_all(
    results: dict[str, Result], verdicts: dict[str, list[Verdict]], spend: CheckSpend
) -> None:
    """Check every row that passes the code filters, a stored verdict making no call, and drop
    the rows the check does not support, with the test they failed, and those it could not
    check."""
    todo = [(results[i].finished, v) for i, vs in verdicts.items() for v in vs if v.drop is None]
    have = sum((CHECKS / CHECK_VERSION / f"{row_key(v.c, d)}.json").exists() for d, v in todo)
    print(
        f"check version {CHECK_VERSION}: {len(todo)} rows to check, {have} checked before (no call)"
    )
    sem = asyncio.Semaphore(CHECK_CONCURRENCY)
    answers = await asyncio.gather(*(check_row(v.c, d, sem, spend) for d, v in todo))
    for (_, v), a in zip(todo, answers, strict=True):
        if isinstance(a, str):
            v.drop = f"{UNCHECKED}: {a}"
        else:
            v.check = a
            if not a.supported:
                v.drop = f"{NOT_SUPPORTED}: {a.failed_test or 'no test named'}"


REPEAT = "repeat of a link kept from another row"


def one_per_link(verdicts: dict[str, list[Verdict]]) -> dict[str, list[Verdict]]:
    """Every result's verdicts, by entity, with one kept row per link id across them all: the row
    with the newest source date (a row with none counts as oldest), then a row from the result of
    the link's `from` entity, then of its `to` entity, then the first by entity id and row order.
    The other kept rows of the link are dropped as a REPEAT."""
    best: dict[str, tuple[tuple[int, int, str, int], str, int]] = {}
    for entity, vs in verdicts.items():
        for i, v in enumerate(vs):
            if v.drop is not None:
                continue
            c = v.c
            side = 0 if entity == c.from_ else 1 if entity == c.to else 2
            key = (-(c.as_of or date.min).toordinal(), side, entity, i)
            if (b := best.get(c.link_id())) is None or key < b[0]:
                best[c.link_id()] = (key, entity, i)
    winners = {(entity, i) for _, entity, i in best.values()}
    return {
        entity: [
            replace(v, drop=REPEAT) if v.drop is None and (entity, i) not in winners else v
            for i, v in enumerate(vs)
        ]
        for entity, vs in verdicts.items()
    }


def recall(entity: str, r: Result, stored: list[Relationship]) -> list[str]:
    """Lines on how many of the entity's stored links of the 21 types the candidates found again:
    on both ends and type, and on the ends alone (another type)."""
    mine = [x for x in stored if entity in (x.from_, x.to) and x.type in COLLECTED_TYPES]
    found_ids = {c.link_id() for c in r.candidates}
    found_pairs: dict[frozenset[str], set[str]] = {}
    for c in r.candidates:
        found_pairs.setdefault(frozenset((c.from_, c.to)), set()).add(c.type)
    both = [x for x in mine if x.id in found_ids]
    ends_only = [
        (x, sorted(found_pairs[pair]))
        for x in mine
        if x.id not in found_ids and (pair := frozenset((x.from_, x.to))) in found_pairs
    ]
    missed = [
        x for x in mine if x.id not in found_ids and frozenset((x.from_, x.to)) not in found_pairs
    ]
    lines = [
        f"  recall: {len(both)} of {len(mine)} stored links found again on both ends and type"
        f" ({len(both) / max(len(mine), 1):.0%}); {len(ends_only)} more on the ends alone",
        *(f"    found: {x.id}" for x in both),
        *(f"    ends alone: {x.id} (found as {', '.join(ts)})" for x, ts in ends_only),
        *(f"    not found: {x.id}" for x in missed),
    ]
    return lines


def report(
    outcomes: list[Outcome],
    results: dict[str, Result],
    verdicts: dict[str, list[Verdict]],
    stored: list[Relationship],
    spend: CheckSpend,
) -> None:
    ran = {o.entity: o for o in outcomes}
    for entity, r in results.items():
        o = ran.get(entity)
        vs = verdicts[entity]
        kept = [v for v in vs if v.drop is None]
        reasons: dict[str, int] = {}
        for v in vs:
            if v.drop is not None:
                key = next((k for k in ("an end", UNCHECKED) if v.drop.startswith(k)), v.drop)
                key = "an end is not a seed entity" if key == "an end" else key
                reasons[key] = reasons.get(key, 0) + 1
        checked = [v.check for v in vs if v.check]
        failed: dict[str, int] = {}
        for x in checked:
            if not x.supported:
                test = x.failed_test or "no test named"
                failed[test] = failed.get(test, 0) + 1
        u = r.usage
        print(
            f"\n{entity}: {'collected in this run' if o else 'finished before, no call'} "
            f"(prompt version {r.prompt_version}, {r.finished}, {r.model}, {r.attempts} "
            f"answer(s))"
        )
        print(
            f"  {int(u['requests'])} requests, {int(u['resumes'])} resumes, "
            f"{int(u['searches'])} searches, {int(u['fetches'])} page fetches; tokens: "
            f"{int(u['input_tokens'])} input uncached, {int(u['cache_write_tokens'])} cache "
            f"write, {int(u['cache_read_tokens'])} cache read, {int(u['output_tokens'])} output; "
            f"${u['cost_usd']:.4f} measured; wall time {r.wall_seconds:.0f} s"
        )
        print(
            f"  {len(r.candidates)} candidate rows: {len(kept)} kept, "
            f"{len(r.candidates) - len(kept)} dropped"
            + "".join(f"; {n} {why}" for why, n in sorted(reasons.items()))
        )
        print(f"  quote check: {sum(c.quote_check == 'found' for c in r.candidates)} found")
        print(
            f"  check: {len(checked)} checked, {sum(x.supported for x in checked)} supported, "
            f"{len(checked) - sum(x.supported for x in checked)} not supported"
            + "".join(f"; {n} failing {t}" for t, n in sorted(failed.items()))
            + f"; {reasons.get(UNCHECKED, 0)} unchecked"
        )
        for v in vs:
            if v.drop and v.drop.startswith(UNCHECKED):
                print(f"    asked again next run: {v.c.link_id()}: {v.drop}")
        print(f"  coverage, {len(r.coverage)} entries:")
        for x in r.coverage:
            what = f"{x.rows} rows" if x.rows else "none found"
            print(f"    {x.type:32}{what:>12}; queries: {' | '.join(x.queries) or '-'}")
        for line in recall(entity, r, stored):
            print(line)
    incomplete = [o for o in outcomes if o.result is None]
    print(f"\n{len(incomplete)} incomplete entities")
    for o in incomplete:
        print(
            f"  {o.entity}: {o.incomplete}; ${o.usage['cost_usd']:.4f}, "
            f"{int(o.usage['searches'])} searches, {int(o.usage['resumes'])} resumes"
        )
    spent = sum(o.usage["cost_usd"] for o in outcomes)
    print(f"spent this run: ${spent:.4f} measured, in {len(outcomes)} entities called")
    per = f", ${spend.cost_usd / spend.calls:.4f} a call" if spend.calls else ""
    print(
        f"check version {CHECK_VERSION}: {spend.calls} calls this run (failed attempts included), "
        f"${spend.cost_usd:.4f} measured{per}"
    )


ROW_FIELDS = ["entity", "from", "type", "to", "detail", "source_url", "source_title", "quote"]


def write_rows(path: Path, rows: list[tuple[str, Verdict]], dropped: bool) -> None:
    """The rows as CSV, with the quote check, the check's verdict (supported, not supported, or
    blank when the row was not checked), its failed test and reason, and why a dropped row is
    dropped."""
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                *ROW_FIELDS,
                "as_of",
                "quote_check",
                "check",
                "failed_test",
                "check_reason",
                *(["reason"] if dropped else []),
            ]
        )
        for entity, v in rows:
            c, x = v.c, v.check
            w.writerow(
                [
                    entity,
                    c.from_,
                    c.type,
                    c.to,
                    c.detail,
                    c.source_url,
                    c.source_title,
                    c.quote,
                    c.as_of or "",
                    c.quote_check,
                    "" if x is None else "supported" if x.supported else "not supported",
                    (x and x.failed_test) or "",
                    x.reason if x else "",
                    *([v.drop] if dropped else []),
                ]
            )


HEADER = """\
# Written by api/scripts/collect_links.py; read by api/scripts/build_relationships.py like any
# links-*.yaml file.
#
# prompts[]: the version of the link collection prompt its rows name, numbered from 1 in the
#   order versions were first used: id (the first 12 hex characters of the SHA-256 of the stage,
#   template, schema, model and effort, as api/intelmonitor/pipeline/prompts.py makes them), date,
#   note, model, effort and its template's text (the seed's entities go where {entities} stands).
# checks[]: the version of the link check prompt that kept the rows, listed the same way (stage
#   link_check), with its text.
# relationships[]: the rows kept, one per link: both ends seed entities, one of the 21 collected
#   types, not built from another collected file, the quote found in the page the request fetched,
#   naming one of the two entities, the page naming both, and the check finding the relationship
#   supported by the passage around the quote. evidence is reported; prompt_version names the
#   version that found the row, check_version the one that checked it; collected is when.
"""


class _Dumper(yaml.SafeDumper):
    """Writes a prompt's text as a literal block, line for line, and repeated values (such as the
    shared collected date) in full, with no anchors."""

    def ignore_aliases(self, data: object) -> bool:
        return True


def _text(dumper: yaml.SafeDumper, s: str) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", s, style="|" if "\n" in s else None)  # pyright: ignore[reportUnknownMemberType]


_Dumper.add_representer(str, _text)


def _version(old: list[PromptVersion], vid: str, note: str, text: str) -> PromptVersion:
    """The version as the file listed it, else numbered one past the file's last, dated today."""
    return next((p for p in old if p.id == vid), None) or PromptVersion(
        id=vid,
        number=max((p.number for p in old), default=0) + 1,
        date=date.today(),
        note=note,
        model=MODEL,
        effort=EFFORT,
        text=text,
    )


def write_out(path: Path, kept: list[tuple[Result, Candidate]]) -> None:
    """The kept rows as a links-*.yaml file, with the collection and check versions they name."""
    old = (
        building.TeamLinksFile.model_validate(doc)
        if path.exists() and (doc := yaml.safe_load(path.read_text()))
        else None
    )
    prompt = _version(old.prompts if old else [], VERSION, PROMPT_NOTE, COLLECTION_TEMPLATE)
    check = _version(old.checks if old else [], CHECK_VERSION, CHECK_NOTE, CHECK_PROMPT)
    order: list[str] = list(REL_TYPES)
    rows = [
        {
            "from": c.from_,
            "to": c.to,
            "type": c.type,
            "share": None,
            "detail": c.detail,
            "evidence": "reported",
            "as_of": c.as_of,
            "source_url": c.source_url,
            "source_title": c.source_title,
            "quote": c.quote,
            "prompt_version": r.prompt_version,
            "check_version": CHECK_VERSION,
            "collected": r.finished,
        }
        for r, c in sorted(kept, key=lambda rc: (order.index(rc[1].type), rc[1].from_, rc[1].to))
    ]
    doc = {
        "collected": max((r.finished for r, _ in kept), default=date.today()),
        "prompts": [prompt.model_dump()],
        "checks": [check.model_dump()],
        "relationships": rows,
        "service_links": [],
        "groups": [],
        "new_entities": [],
    }
    body = yaml.dump(doc, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER + "\n" + body)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Collect links of the 21 types from the web.")
    parser.add_argument("--entities", help="comma-separated entity ids (default: every entity)")
    parser.add_argument("--stop-at-usd", type=float, help="stop an entity whose cost passes this")
    parser.add_argument("--out", type=Path, default=OUT, help="where to write the kept rows")
    parser.add_argument("--report", type=Path, help="directory for the rows and the report")
    parser.add_argument("--concurrency", type=int, default=CONCURRENCY)
    args = parser.parse_args()

    by_id = {e.id: e for e in SEED.entities}
    wanted = args.entities.split(",") if args.entities else list(by_id)
    unknown = [i for i in wanted if i not in by_id]
    if unknown:
        parser.error(f"not seed entities: {', '.join(unknown)}")
    RESULTS.mkdir(parents=True, exist_ok=True)
    have = load_results()
    todo = [i for i in wanted if i not in have]
    print(
        f"{len(wanted)} entities: {len(wanted) - len(todo)} finished with prompt version "
        f"{VERSION}, no call; {len(todo)} to collect"
    )
    sem = asyncio.Semaphore(args.concurrency)
    outcomes = list(await asyncio.gather(*(collect(by_id[i], args.stop_at_usd, sem) for i in todo)))
    results = {i: r for i, r in load_results().items() if i in wanted}
    # Not relationships.yaml, which holds the rows of links-new-types.yaml once they are built
    others = list(building.build_collected(skip=building.NEW_TYPES_FILE)[0].rels.values())
    stored = {x.id for x in others}
    entities = set(by_id)
    every = load_results()  # the file holds every finished entity's kept rows, not only these
    verdicts = {i: judge(r, entities, stored) for i, r in every.items()}
    spend = CheckSpend()
    await check_all(every, verdicts, spend)
    verdicts = one_per_link(verdicts)
    kept = [(every[i], v.c) for i, vs in verdicts.items() for v in vs if v.drop is None]
    write_out(args.out, kept)
    text = io.StringIO()
    with redirect_stdout(text):
        report(outcomes, results, verdicts, others, spend)
        print(f"\nwrote {len(kept)} kept rows to {args.out}")
    print(text.getvalue(), end="")
    if args.report:
        args.report.mkdir(parents=True, exist_ok=True)
        rows = [(i, v) for i in results for v in verdicts[i]]
        write_rows(args.report / "kept-rows.csv", [x for x in rows if x[1].drop is None], False)
        write_rows(args.report / "dropped-rows.csv", [x for x in rows if x[1].drop], True)
        (args.report / "report.txt").write_text(text.getvalue())
        print(f"wrote kept-rows.csv, dropped-rows.csv and report.txt to {args.report}")
    return 1 if any(o.result is None for o in outcomes) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
