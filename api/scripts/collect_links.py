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

A finished entity's result is kept in data/link-collection/<prompt version>/<entity>.json (and the
whole conversation under transcripts/); a later run makes no call for an entity that has a result
under the current version. Results under another version are left where they are and not read: the
entity is collected again, a turn that ends incomplete leaves it no rows, and putting an old prompt
back finds its results again. The version is made from the prompt's template, which holds a
placeholder where the list of entities goes, so a change to the seed's entities does not make every
result stale; the filters below run on every write, so a row to an entity that has left the seed is
dropped then. Each run filters every kept result again and writes the rows that pass to --out. Code
keeps a row only if both ends are seed entities, the type is one of the 21, the link is not already
stored, and the quote is found in the page the request fetched from the row's source URL (each
side's text normalised: markdown links and footnote markers out, quotes, dashes and spaces unified,
case ignored). Dropped rows are listed with the reason. Stored means built from a collected file
other than links-new-types.yaml (build_relationships.build_collected), so a run after a build keeps
the rows the file itself put into relationships.yaml. Kept rows are written with evidence
`reported`, the prompt version and the date collected; the file lists every version its rows name
in `prompts`, as transmission.yaml does.

The prompt, its schema, the model and the effort make a prompt version, whose id is made as
pipeline/prompts.py makes a stage's.

Each entity's usage is recorded and printed: searches, page fetches, resumes, input tokens
(uncached, cache writes, cache reads), output tokens and the measured cost, web search fee
included. The running cost is printed after every resume. Recall is reported per entity: of its
stored links of the 21 types, how many the candidates found again on both ends and
type, with matches on the ends alone beside them, counted before the "already stored" filter.

Usage (from api/):
  uv run python scripts/collect_links.py [--entities dbs,singtel] [--stop-at-usd 15]
      [--out <file>.yaml] [--report <dir>] [--concurrency N]
--entities limits the run to those ids (default: every entity); --stop-at-usd stops resending an
entity's turn once its measured cost passes the figure; --out writes the kept rows elsewhere (a
trial keeps them out of the build); --report writes kept-rows.csv, dropped-rows.csv and report.txt.
"""

import argparse
import asyncio
import base64
import csv
import io
import json
import re
import time
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from typing import Literal

import build_relationships as building
import yaml
from anthropic.types.beta import BetaMessage, BetaMessageParam
from pydantic import BaseModel, ConfigDict, Field

from intelmonitor.config import DATA_DIR, SEED_DIR
from intelmonitor.ingest.parse import canonical_url, pdf_to_text
from intelmonitor.pipeline.claude import WebUsage, ask_claude_web
from intelmonitor.pipeline.prompts import THINK_FIRST, schema_text, version_id
from intelmonitor.relationships import (
    LINK_TYPES,
    REL_TYPES,
    SYMMETRIC,
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
    }
)


def normal(text: str) -> str:
    """Text with markdown links reduced to their words, footnote markers and markup out, quotes,
    dashes and spaces unified, in lower case."""
    for _ in range(2):  # "[[12]](url)" takes two passes
        text = _MD_LINK.sub(r"\1", text)
    text = _MARKUP.sub(" ", _FOOTNOTE.sub(" ", text.translate(_CHARS)))
    return re.sub(r"\s+", " ", text).strip().lower()


def fetched_pages(responses: list[BetaMessage]) -> dict[str, str]:
    """The normalised text of every page the turn fetched, by canonical URL; a PDF's text is read
    from every page."""
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
            pages[canonical_url(b.content.url)] = normal(text)
    return pages


QuoteCheck = Literal["found", "page not fetched", "not in page"]


class Candidate(BaseModel):
    """A row the model returned, with its type and the quote check made against the turn's
    fetched pages."""

    from_: str = Field(alias="from")
    to: str
    type: str
    detail: str
    source_url: str
    source_title: str
    quote: str
    as_of: date | None
    quote_check: QuoteCheck

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


def check(c: Collection, pages: dict[str, str]) -> list[Candidate]:
    out: list[Candidate] = []
    for x in c.entries:
        for f in x.relationships:
            page = pages.get(canonical_url(f.source_url))
            q: QuoteCheck = (
                "page not fetched"
                if page is None
                else "found"
                if normal(f.quote) and normal(f.quote) in page
                else "not in page"
            )
            out.append(Candidate(**f.model_dump(by_alias=True), type=x.type, quote_check=q))
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
                candidates=check(turn.output, fetched_pages(responses)),
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


Verdict = tuple[Candidate, str | None]  # a candidate and why it is dropped, None when kept


def judge(r: Result, entities: set[str], stored: set[str]) -> list[Verdict]:
    """Each candidate with the reason code drops it, or None when it is kept."""
    out: list[Verdict] = []
    for c in r.candidates:
        outside = [x for x in (c.from_, c.to) if x not in entities]
        reason = (
            f"an end is not a seed entity: {', '.join(outside)}"
            if outside
            else "type is not one of the 21 collected types"
            if c.type not in COLLECTED_TYPES
            else "already stored"
            if c.link_id() in stored
            else "source page not fetched in the request"
            if c.quote_check == "page not fetched"
            else "quote not found in the fetched page"
            if c.quote_check == "not in page"
            else None
        )
        out.append((c, reason))
    return out


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
) -> None:
    ran = {o.entity: o for o in outcomes}
    for entity, r in results.items():
        o = ran.get(entity)
        vs = verdicts[entity]
        kept = [c for c, why in vs if why is None]
        reasons: dict[str, int] = {}
        for _, why in vs:
            if why is not None:
                key = "an end is not a seed entity" if why.startswith("an end") else why
                reasons[key] = reasons.get(key, 0) + 1
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


ROW_FIELDS = ["entity", "from", "type", "to", "detail", "source_url", "source_title", "quote"]


def write_rows(path: Path, rows: list[tuple[str, Candidate, str | None]], dropped: bool) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([*ROW_FIELDS, "as_of", "quote_check", *(["reason"] if dropped else [])])
        for entity, c, why in rows:
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
                    *([why] if dropped else []),
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
# relationships[]: the rows code kept: both ends seed entities, one of the 21 collected types, not
#   built from another collected file, the quote found in the page the request fetched. evidence is
#   reported; prompt_version names the version that found the row; collected is when.
"""


class _Dumper(yaml.SafeDumper):
    """Writes a prompt's text as a literal block, line for line."""


def _text(dumper: yaml.SafeDumper, s: str) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", s, style="|" if "\n" in s else None)  # pyright: ignore[reportUnknownMemberType]


_Dumper.add_representer(str, _text)


def write_out(path: Path, kept: list[tuple[Result, Candidate]]) -> None:
    """The kept rows as a links-*.yaml file, with the prompt versions they name."""
    old: list[PromptVersion] = []
    if path.exists() and (doc := yaml.safe_load(path.read_text())):
        old = [PromptVersion.model_validate(p) for p in doc.get("prompts", [])]
    prompts = old
    if not any(p.id == VERSION for p in old):
        prompts = [
            *old,
            PromptVersion(
                id=VERSION,
                number=max((p.number for p in old), default=0) + 1,
                date=date.today(),
                note=PROMPT_NOTE,
                model=MODEL,
                effort=EFFORT,
                text=COLLECTION_TEMPLATE,
            ),
        ]
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
            "collected": r.finished,
        }
        for r, c in sorted(kept, key=lambda rc: (order.index(rc[1].type), rc[1].from_, rc[1].to))
    ]
    doc = {
        "collected": max((r.finished for r, _ in kept), default=date.today()),
        "prompts": [p.model_dump() for p in prompts if p.id == VERSION],
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
    verdicts = {i: judge(r, entities, stored) for i, r in results.items()}
    every = load_results()  # the file holds every finished entity's kept rows, not only these
    kept = [(r, c) for r in every.values() for c, why in judge(r, entities, stored) if why is None]
    write_out(args.out, kept)
    text = io.StringIO()
    with redirect_stdout(text):
        report(outcomes, results, verdicts, others)
        print(f"\nwrote {len(kept)} kept rows to {args.out}")
    print(text.getvalue(), end="")
    if args.report:
        args.report.mkdir(parents=True, exist_ok=True)
        rows = [(i, c, why) for i, vs in verdicts.items() for c, why in vs]
        write_rows(args.report / "kept-rows.csv", [x for x in rows if x[2] is None], False)
        write_rows(args.report / "dropped-rows.csv", [x for x in rows if x[2] is not None], True)
        (args.report / "report.txt").write_text(text.getvalue())
        print(f"wrote kept-rows.csv, dropped-rows.csv and report.txt to {args.report}")
    return 1 if any(o.result is None for o in outcomes) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
