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
an API error on every attempt, the rejections used up, or its cost found past --stop-at-usd when
the server pauses the turn) is listed as incomplete, keeps no rows and is collected again on the
next run.

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
dashes and spaces unified, case ignored), the quote names at least one of the two entities, the
quote holds words beyond the two entities' names (`states_more_than_names`: not a name, a heading or
a code alone), the page's text is stored (a result kept before rows named their page has none) and
the page names both (`names`). Stored means built from a collected file other than
links-new-types.yaml (build_relationships.build_collected), so a run after a build keeps the rows
the file itself put into relationships.yaml.

Each row that passes those filters then goes to the check (CHECK_PROMPT): one tool-free call
through pipeline/claude.py `ask_claude`, so it follows settings.claude_via_api, on Claude Sonnet
5.5 at effort high with adaptive thinking. It reads the row, its two entities and type, and the
passage of the page around the quote (WINDOW characters either side, centred on the quote's first
occurrence after the page's front matter), with the front matter's title and publication lines,
and answers whether the passage shows the relationship, by six tests in order (evidence, parties,
type, direction, material, current), naming the first that fails. Only a supported row is kept.
Each verdict is stored under data/link-collection/checks/<check version>/, keyed by a hash of the
exact message sent, so a later run makes no call for a message already answered, and any change to
what the check reads (the row, the seed's lines for its entities, the passage, the message's
template) asks again; a refused or failed check stores nothing, and the row
is left out, listed as unchecked and asked again on the next run. So is a row whose quote cannot be
placed in its page's text (`passage`), with no call.

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
The check is reported per entity (rows checked, supported, not supported by test, unchecked, and
its calls and measured cost in this run). It runs over every finished result, not only those of
--entities, since --out holds every finished entity's kept rows; the report's last line names the
entities whose rows were checked and the calls and cost spent on entities it does not list.

Usage (from api/):
  uv run python scripts/collect_links.py [--entities dbs,singtel] [--stop-at-usd 15]
      [--out <file>.yaml] [--report <dir>] [--concurrency N]
--entities limits the run to those ids (default: every entity); --stop-at-usd stops an entity's
turn when the server pauses it with its measured cost past the figure. It is checked only at a
pause, and four of the five trial turns never paused, so it does not bound one entity's cost.
--out writes the kept rows elsewhere (a trial keeps them out of the build); --report writes
kept-rows.csv, dropped-rows.csv (both with the check's verdict, failed test and reason) and
report.txt.
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
from bisect import bisect_left, bisect_right
from contextlib import redirect_stdout
from dataclasses import dataclass, field, replace
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
# LINK_TYPES, looked up by a candidate's type
_TYPES: dict[str, LinkType] = {t: lt for t, lt in LINK_TYPES.items()}

# The deck of 16 Nov 2026: page 5's opening and identification rules 1 to 8 word for word, the
# types from LINK_TYPES (pages 1 to 4). The deck has no separate materiality sentence; the rule
# below quotes the types' own "Material ..." meanings and ticket 03's example (Q4). The paragraph
# on payment systems and market infrastructures is ticket 02's P2, which the stored links follow
# (a system processes_transactions_for its participant). The three paragraphs after it, on a loan
# book (exposed_to, as the stored links have it), on participates_in (only toward a programme
# that is itself an entity) and on two owners of one company (each holds_stake_in it, not a joint
# product), and the EVIDENCE section's rule that the quote names Entity A or B, are ours (18 Nov
# 2026, from the trial's hand reading). The framing around them (one entity
# against the list, the web as the evidence, the quote and what finished means) is ours. No JSON
# shape: the answer's fields are described in its schema (ticket 09).
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
A bank's loan book by sector or country is exposed_to toward the sector or country entity, as \
the stored links have it; lends_to is for a loan or facility to a named borrower that is itself \
on the ENTITIES list.
participates_in is used only when the scheme, programme or initiative is itself on the ENTITIES \
list; a programme's owner, operator or sponsor does not stand in for it.
Two owners of one company each hold a stake in it (each holds_stake_in the company); owning, \
setting up or backing the same company is not jointly_offers_product_with between the owners.

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
pieces and no paraphrase. The quote must name Entity A or Entity B, and the page must name both; \
a heading, a code or a name alone is not a quote that shows a relationship. Each quote is checked \
against the page.

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
    "infrastructures, our rules that a loan book is exposed_to, that participates_in ends only "
    "at a programme on the list, that two owners of one company each hold a stake in it and do "
    "not jointly offer a product through it, and that the quote names Entity A or B, the seed's "
    "entities and "
    "what finished means, with THINK_FIRST; web search and web fetch, effort high."
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


_LEGAL_WORDS = (
    "corporation corp company co limited ltd inc plc pte private bhd berhad ag sa se tbk llc"
).split()
_LEGAL_FORM = re.compile(
    rf"(?:\s+(?:{'|'.join(_LEGAL_WORDS)}|\(pte\)|\(private\)|\(singapore\)))+\.?$"
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


# What is left of a quote once the two entities' names are out, for `states_more_than_names`.
_URL = re.compile(r"\(?https?://\S*")  # the target of a markdown link the quote cut in two
_FIGURE = re.compile(r"\d[\d,.]*%?")
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")  # kana, Han
_CJK_LEGAL_FORM = re.compile(r"股份有限公司|有限責任公司|有限责任公司|有限公司|株式会社")
# A code as the page writes it: capitals and digits, 8 to 20 of them (a BIC, an ISIN, an LEI)
_CODE = re.compile(r"(?<!\w)[A-Z0-9]{8,20}(?!\w)")
_CODE_WORDS = set("code codes bic swift isin cusip sedol lei ticker symbol iban routing id".split())
_FUNCTION_WORDS = set(
    "a an the of for to in on at by with from and or as is are was were be been being has have "
    "had it its this that these those which who our we us their they".split()
)
MIN_WORDS = 3


def states_more_than_names(quote: str, a: str, b: str) -> bool:
    """Whether the quote holds enough besides the names of entities a and b to state a
    relationship: a name, a heading or a code alone does not ("## DBS Bank", "Licensed Bank", "DBS
    BANK (HONG KONG) LIMITED (星展銀行(香港)有限公司)", "The Swift/BIC code for DBS Bank is
    DBSSSGSGXXX."), though the check's evidence test says so too and the check kept such rows.

    Every name and alias of a and b, punctuation, link targets and legal-form words ("Limited",
    "有限公司") are taken out of the normalised quote. A figure (28%, 61,416) counts as one word,
    since in a table row it is what the name is given; a run of Chinese or Japanese characters
    counts as one word per four characters, rounded up. The quote passes with at least MIN_WORDS
    words, of which at least one is not a function word ("the", "of", "is"), a code or a word that
    names a code ("BIC", "ISIN"). MIN_WORDS is from the trial's 235 rows and controls: at 3 the
    filter drops the four quotes above and "Barclays Capital Inc. | | | 2,109,545" (one word and a
    figure), and no other row; at 2 it keeps "Licensed Bank" and the Hong Kong name line; at 4 it
    also drops "Vanguard Portfolio Management, LLC | 2.29%", a holder's row the check supported.

    It wrongly drops a real row whose quote says little beyond the names: "Temasek owns DBS" has
    one word left ("Temasek holds 28% of DBS" passes on its figure); a table row whose holder's name
    is all alias ("BlackRock, Inc. | 3.26%" has a figure alone), or a loan book row when its
    region or sector is named in full by an alias ("Rest of Greater China 61,416 58,429" passes
    on its three figures, a row with one figure does not); a quote in a language without spaces
    other than Chinese or Japanese (Thai), which comes out as one word; a Chinese quote under nine
    characters; and a quote in capitals, whose words of eight letters or more read as codes. It
    wrongly keeps a name the seed does not list as an alias, whose words count ("Vanguard
    Portfolio Management, LLC | 2.29%" passes on two name words and a figure), and a heading of
    three words or more."""
    codes = {m.lower() for m in _CODE.findall(quote)}
    text = _URL.sub(" ", _and(normal(quote)))
    for e in (a, b):
        text = _NAME_PATTERNS[e].sub(" ", text)
    figures = len(_FIGURE.findall(text))
    text = _CJK_LEGAL_FORM.sub(" ", _FIGURE.sub(" ", text))
    words = [w for w in re.sub(r"[\W\d_]+", " ", text).split() if w not in _LEGAL_WORDS]
    count = figures + sum(-(-len(w) // 4) if _CJK.fullmatch(w) else 1 for w in words)
    plain = _FUNCTION_WORDS | _CODE_WORDS | codes
    return count >= MIN_WORDS and (figures > 0 or any(w not in plain for w in words))


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
    when the turn fetched no such page, or the result was kept before rows named their page)."""

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
SNAP = 200  # a cut moves to whitespace only within this many characters (CJK text has little)


def _segments(text: str, sep: str) -> list[tuple[int, str]]:
    """The pieces of text between matches of sep, each with its start in the text."""
    out: list[tuple[int, str]] = []
    at = 0
    for m in re.finditer(sep, text):
        out.append((at, text[at : m.start()]))
        at = m.end()
    return [*out, (at, text[at:])]


def body_start(text: str) -> int:
    """Where a page's body starts: after the front matter the fetch puts at its top (its title,
    dates and meta tags between two "---" lines); 0 when it has none."""
    if not text.startswith("---\n"):
        return 0
    end = text.find("\n---\n", 3)
    return 0 if end < 0 else end + 5


# The front matter's title and publication date lines, which the check reads whatever part of
# the page its passage is cut from. A modified or updated time is left out: it says when the page
# was edited, not that what it reports continues.
_HEAD_KEY = re.compile(r"title|(?!.*last).*(?:publish|pub-date).*")


def page_head(text: str) -> list[str]:
    """The front matter's title and publication date lines, in the page's order."""
    lines = text[: body_start(text)].splitlines()
    return [x for x in lines if ": " in x and _HEAD_KEY.fullmatch(x.split(": ", 1)[0].lower())]


def passage(text: str, quote: str) -> str | None:
    """The page text around the quote, WINDOW characters either side, cut at whitespace near
    each edge, "[...]" where it is cut; None when the quote cannot be placed in it. The quote is
    found in the normalised text line by line, then paragraph by paragraph (a markdown link
    across a line break), and placed back in the page's own text; a window that does not hold the
    quote once normalised is not returned. The window is centred on the quote's first occurrence
    after the front matter (`body_start`), or on its first in the front matter (a meta
    description, say) when the body does not hold it."""
    q = normal(quote)
    if not q:
        return None
    body = body_start(text)
    for sep in (r"\n", r"\n\s*\n"):
        starts: list[int] = []  # each piece's start in the page's text
        sizes: list[int] = []  # its length there
        offsets: list[int] = []  # its start in the normalised pieces joined by spaces
        norms: list[str] = []
        pos = 0
        for at, piece in _segments(text, sep):
            if n := normal(piece):
                starts.append(at)
                sizes.append(len(piece))
                offsets.append(pos)
                norms.append(n)
                pos += len(n) + 1
        joined = " ".join(norms)
        k = joined.find(q)
        if k < 0:
            continue
        b = bisect_left(starts, body)  # the first piece of the body
        if b < len(starts) and k < offsets[b] and (later := joined.find(q, offsets[b])) >= 0:
            k = later
        i = bisect_right(offsets, k) - 1
        centre = starts[i] + (k - offsets[i]) * sizes[i] // len(norms[i])
        span = len(quote) * 2  # room for the markup the normalising took out
        lo, hi = max(0, centre - WINDOW), min(len(text), centre + span + WINDOW)
        if lo > 0 and (m := re.search(r"\s", text[lo : lo + SNAP])):
            lo += m.end()
        if hi < len(text) and (m := re.search(r"\s\S*$", text[max(lo, hi - SNAP) : hi])):
            hi = max(lo, hi - SNAP) + m.start()
        window = text[lo:hi].strip()
        if q in normal(window):
            return ("[...] " if lo > 0 else "") + window + (" [...]" if hi < len(text) else "")
    return None


CHECK_STAGE = "link_check"
CHECKS = RESULTS / "checks"  # each verdict, under the check's version, by its message's hash
CHECK_CONCURRENCY = 8

# The check: one tool-free call per row that passes the code filters. Rules 1, 2, 3, 4, 6 and 7 are
# the deck's (16 Nov 2026, page 5) word for word, the types are LINK_TYPES (pages 1 to 4), and the
# materiality rule and the P2 paragraph are the collection prompt's. The frame (one row against
# its passage), rule 8 read for one row, the six tests, the loan book, the programme's owner, the
# joint owners of a company and what a quote must show are ours. No JSON shape: the fields are
# described in the schema (ticket 09).
CHECK_PROMPT = f"""\
You are checking one relationship between Entity A and Entity B that was found on the web, \
against a passage of the page it was found on. The message gives the two entities, the \
relationship's type, its reason, the page's title, URL and date, the quote it was found with, \
the title and publication date lines from the top of the page, and the passage of the page \
around that quote.

The relationship was identified under these rules:
1. Return every distinct relationship between the entities that is supported by the provided \
evidence.
2. A pair of entities may have several relationships. Do not combine them into one generic \
relationship.
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
Two entities that own a company together, such as a joint venture or a digital bank, each \
holds_stake_in that company; setting it up, backing it or running it together, and its products, \
are not a product the two jointly offer.

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
two own together, or the customers of either do not, and two owners of one company are not \
parties to a relationship with each other through it.
- type: the passage shows this type's meaning for this pair, under the meanings and the \
paragraphs above. Another type also fitting the pair is not a reason to fail: under rule 2 the \
pair may have that relationship as well as this one. A type whose meaning the passage does not \
show fails, such as a company the two own together filed as a product they jointly offer, or the \
acquisition of a unit filed as invests_in.
- direction: the relationship reads from its first entity to its second as the type's name \
says. A type without direction passes.
- material: the relationship is material as the type's meaning says, to the business of at \
least one of the two entities.
- current: the relationship stands as of the date the row was collected, as far as the page \
shows. A one-off event that is over, such as a single transaction or an event held, fails. An \
arrangement that runs over time, such as a service, a facility, a stake, a product the two offer \
or operations in a country, fails when the page shows that it ended or was replaced, or that \
its stated term ran out before the row was collected. Otherwise it passes when the page gives a \
term that runs to the collection date or past it (a 15-year agreement signed in 2015), or another \
sign that the arrangement continues, such as an agreement described as long-term or multi-year, \
a subsidiary or branch network in the country, or a stake with no sale reported; present tense \
on an old page is not by itself such a sign. Only when the page gives neither a term nor such a \
sign does its date decide: the arrangement passes unless the date the page was published, or the \
date of the fact it reports, is more than five years before the row was collected. A modified or \
updated time on the page is not that date and is not evidence that the arrangement continues.

{THINK_FIRST}"""
CHECK_NOTE = (
    "One row against the passage around its quote and the page's title and publication lines: "
    "the deck's rules 1, 2, 3, 4, 6 and 7 (16 Nov 2026, page 5), the 22 types with their "
    "meanings, P2, the loan book, programme's owner and joint owners rules, the materiality "
    "rule, and six tests (evidence, parties, type, which another type fitting the pair does not "
    "fail, direction, material, current, with a five-year horizon from the publication or fact "
    "date, not a modified time, only for an ongoing arrangement whose page gives no term or sign "
    "that it continues), with THINK_FIRST; no tools, effort high."
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
    """A stored verdict: the check's answer to one message under one check version, with the
    call's measured cost."""

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


def check_message(c: Candidate, collected: date) -> str | None:
    """The check's user message: the row, its two entities, its type, the page's title and
    publication lines (`page_head`) and the passage around its quote; None when the quote cannot
    be placed in the page's text (`passage`). The row must have its page text stored, as every
    row that passes `dropped_by_code` has."""
    assert c.page
    text = page_text(c.page)[0]
    around = passage(text, c.quote)
    if around is None:
        return None
    a, b = _ENTITIES[c.from_], _ENTITIES[c.to]
    lt = _TYPES[c.type]
    reads = (
        f"{c.type} has no direction."
        if c.type in SYMMETRIC
        else f"It reads from the first entity to the second: {a.name} {c.type} {b.name}."
    )
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
            "Title and publication lines from the top of the page:",
            *(page_head(text) or ["none"]),
            "",
            'Passage of the page around the quote ("[...]" marks where it is cut):',
            "<passage>",
            around,
            "</passage>",
        ]
    )


def verdict_path(message: str) -> Path:
    """Where the verdict on a message is stored: under the check version (the system prompt, the
    schema, the model and the effort), named by a hash of the message, so a verdict is reused only
    when the check would read exactly the same."""
    return CHECKS / CHECK_VERSION / f"{hashlib.sha256(message.encode()).hexdigest()[:16]}.json"


@dataclass
class CheckSpend:
    """The check's calls in this run (failed attempts included) and their measured cost, by the
    entity whose result holds the row."""

    calls: dict[str, int] = field(default_factory=lambda: {})
    cost_usd: dict[str, float] = field(default_factory=lambda: {})

    def add(self, entity: str, cost: float | None) -> None:
        self.calls[entity] = self.calls.get(entity, 0) + 1
        self.cost_usd[entity] = self.cost_usd.get(entity, 0.0) + (cost or 0.0)


async def check_row(
    c: Candidate, message: str | None, entity: str, sem: asyncio.Semaphore, spend: CheckSpend
) -> Checked | str:
    """The stored verdict on the row's message, or a new one from the check, stored, its spend
    put to `entity`; when the quote could not be placed in its page (no message, no call), or the
    check refuses or fails ATTEMPTS times, why, and nothing is stored, so the row is asked again
    next run."""
    if message is None:
        return "quote not placed in its page, no call"
    path = verdict_path(message)
    if path.exists():
        return Checked.model_validate_json(path.read_text())
    error = ""
    async with sem:
        for _ in range(ATTEMPTS):
            try:
                reply = await ask_claude(message, CheckAnswer, CHECK_PROMPT, MODEL, EFFORT)
            except ClaudeRefusal as x:
                spend.add(entity, x.cost_usd)
                return f"refused ({x.category or 'no category'})"
            except Exception as x:  # an API error, a cut-off or an answer that is not the schema
                spend.add(entity, x.cost_usd if isinstance(x, ClaudeBilled) else None)
                error = f"{type(x).__name__}: {x}"
                continue
            spend.add(entity, reply.cost_usd)
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
    if not states_more_than_names(c.quote, c.from_, c.to):
        return "quote is a name, a heading or a code alone"
    if c.page is None or not (PAGES / f"{c.page}.txt").exists():
        return "page text not stored"
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
    todo = [
        (i, v, check_message(v.c, results[i].finished))
        for i, vs in verdicts.items()
        for v in vs
        if v.drop is None
    ]
    have = sum(m is not None and verdict_path(m).exists() for _, _, m in todo)
    print(
        f"check version {CHECK_VERSION}: {len(todo)} rows to check, of "
        f"{len({i for i, _, _ in todo})} entities, {have} checked before (no call)"
    )
    sem = asyncio.Semaphore(CHECK_CONCURRENCY)
    answers = await asyncio.gather(*(check_row(v.c, m, i, sem, spend) for i, v, m in todo))
    for (_, v, _), a in zip(todo, answers, strict=True):
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
            + f"; {reasons.get(UNCHECKED, 0)} unchecked; {spend.calls.get(entity, 0)} calls "
            f"this run, ${spend.cost_usd.get(entity, 0.0):.4f} measured"
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
    calls, cost = sum(spend.calls.values()), sum(spend.cost_usd.values())
    per = f", ${cost / calls:.4f} a call" if calls else ""
    print(
        f"check version {CHECK_VERSION}: the rows of every finished result were checked, not only "
        f"those listed above, since the output holds every finished entity's kept rows: "
        f"{len(verdicts)} entities ({', '.join(verdicts) or 'none'}); {calls} calls this run "
        f"(failed attempts included), ${cost:.4f} measured{per}"
    )
    others = [i for i in verdicts if i not in results and spend.calls.get(i)]
    if others:
        print(
            f"  of which for entities not listed above: "
            f"{sum(spend.calls[i] for i in others)} calls, "
            f"${sum(spend.cost_usd[i] for i in others):.4f} ("
            + ", ".join(f"{i} {spend.calls[i]} calls ${spend.cost_usd[i]:.4f}" for i in others)
            + ")"
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
#   naming one of the two entities and more than names, the page naming both, and the check
#   finding the relationship supported by the passage around the quote. evidence is reported;
#   prompt_version names the version that found the row, check_version the one that checked it;
#   collected is when.
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
    parser.add_argument(
        "--stop-at-usd",
        type=float,
        help="stop an entity's turn at a server pause once its cost has passed this; checked only "
        "at a pause, so it does not bound one entity's cost",
    )
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
