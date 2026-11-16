"""Rate each link Strong, Moderate, Weak or Non-propagating in each direction (ticket 04 of
link-types-and-inferred-impact), once, into api/seed/collected/transmission.yaml.

Reads api/seed/relationships.yaml and makes one Claude Sonnet 5.5 call per link that is new or
whose input changed, belongs_to_sector included. The system prompt is the team's
(TRANSMISSION_PROMPT: the transmission half of the deck of 16 Nov 2026, page 6, then page 13's
examples as typical ratings); the user prompt names entity A (the link's `from`) and entity B (its
`to`), each with its kind and description, and gives the type with its meaning, the reason and the
stored evidence. The call runs at effort high with adaptive thinking, and the prompt ends with
THINK_FIRST, as the priority pick of 16 Nov 2026 does.

The prompt, its schema, the model and the effort make a prompt version, whose id is made as
pipeline/prompts.py makes a stage's. transmission.yaml lists every version in `prompts` (id,
number, date, note, model, effort, text) and each entry names the version that rated it. A link is
rated again only when the hash of the version and the link's user prompt changes, and an entry
marked `rated_by: hand` is never touched. Entries for links that no longer exist are dropped, hand
entries excepted.

Ratings are written as they arrive. A call that stops before its output is complete (a cut-off) or
fails is tried up to ATTEMPTS times; a refusal is not retried. A run where any link failed exits
non-zero; rerun it to rate the failed links. After a run, rerun scripts/build_relationships.py to
merge the ratings into relationships.yaml.

Usage (from api/):
  uv run python scripts/rate_relationships.py [--limit N] [--sheet <file>.csv] [--ratings <file>]
--limit rates at most N links this run (the rest stay unrated); --sheet writes the review sheet;
--ratings reads and writes another copy of transmission.yaml. Each run prints the spread of
strengths per type and direction.
"""

import argparse
import asyncio
import csv
import hashlib
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from intelmonitor.config import SEED_DIR
from intelmonitor.pipeline.claude import ClaudeBilled, ClaudeCutOff, ClaudeRefusal, ask_claude
from intelmonitor.pipeline.prompts import THINK_FIRST, schema_text, version_id
from intelmonitor.relationships import (
    LINK_TYPES,
    REL_TYPES,
    RELATIONSHIPS_PATH,
    TRANSMISSION_PATH,
    PromptVersion,
    Rating,
    RatingEntry,
    RatingsFile,
    Relationship,
    RelationshipsFile,
    Strength,
    load_ratings_file,
)
from intelmonitor.seed import load_subjects

STAGE = "link_rating"
MODEL = "claude-sonnet-5-5"
# Taken from the priority pick of 16 Nov 2026 (version 8: high with THINK_FIRST); no sweep was run
# for this stage.
EFFORT: Literal["high"] = "high"
CONCURRENCY = 8
ATTEMPTS = 3
# transmission.yaml is rewritten after every this many ratings, and once more at the end (also on
# Ctrl-C or a crash), so a stopped run keeps the ratings already paid for.
WRITE_EVERY = 20

# The deck of 16 Nov 2026, page 6, its transmission half word for word (saved with page 13 in
# wayfind/link-types-and-inferred-impact/assets/04/transmission-prompt.md), less rule 8, "Return
# valid JSON only.", and the output format: the answer comes back through a structured output
# schema whose field descriptions say what each field holds (ticket 09, change 5). Then page 13's
# examples, marked as typical ratings and not fixed (our addition, ticket 04 Q3), and THINK_FIRST.
TRANSMISSION_PROMPT = f"""\
DIRECTIONAL TRANSMISSION
For every identified relationship, classify impact transmission independently in both directions.
Question: "If the source entity experiences a material adverse event relevant to this \
relationship, how materially could the target entity be affected through this relationship?"
Allowed values: Strong | Moderate | Weak | Non-propagating
Strong: A relevant material event at the source could cause a material or significant impact on \
the target. Indicators include critical dependency, control, high exposure or concentration, or \
low substitutability.
Moderate: A relevant material event at the source could cause a meaningful but contained impact \
on the target. The relationship is material, but alternatives, diversification or limited scope \
reduce the impact.
Weak: A relevant material event at the source would normally cause only limited impact on the \
target. The relationship is small, diversified, replaceable or non-critical.
Non-propagating: The relationship exists, but an event at the source should not normally be \
inferred to affect the target in this direction.
TRANSMISSION RULES
1. Assess each relationship separately.
2. Assess both directions independently.
3. Rate impact transmission, not general closeness, prominence or strategic importance.
4. Base the rating on the relationship reason and factual evidence, including ownership \
percentage, exposure, concentration, criticality and substitutability.
5. Do not automatically rate any relationship type Strong.
6. Do not invent missing materiality, concentration or dependency.
7. Where evidence is limited, use the more conservative supported rating.
TYPICAL RATINGS
These are typical ratings for some relationship types and directions. They are not fixed: the \
evidence for each relationship decides its rating in each direction.
- member_of, bank event → association: Weak or Moderate, depending on significance
- member_of, association event → bank: Often Non-propagating, unless the event directly affects \
members
- listed_on, company event → exchange: Usually Non-propagating or Weak
- listed_on, exchange outage → company: Moderate or Strong
- regulated_by, firm event → regulator: Usually Weak
- regulated_by, new regulation → firm: Moderate or Strong
- provides_* (critical provider), bank event → provider: Weak, unless the bank is a concentrated \
customer of the provider
- provides_* (critical provider), provider outage → bank: Moderate or Strong
- belongs_to_sector, sector-wide event → member: Weak or Moderate
- belongs_to_sector, member event → sector: Usually Non-propagating, unless the member is \
systemically significant
{THINK_FIRST}"""
# Stored with the version when it is first used; edit it with the prompt.
PROMPT_NOTE = (
    "The transmission half of the team's prompt (deck of 16 Nov 2026, page 6) with four values, "
    "page 13's examples as typical ratings, and THINK_FIRST; effort high, as the priority pick."
)


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Direction(_Out):
    rating: Strength = Field(
        description="How materially a material adverse event at this direction's source could "
        "affect its target through the relationship"
    )
    reason: str = Field(description="One sentence explaining the rating from the evidence given")


# Each direction says which end is its source in its class's description: the API's schema keeps
# a class's description, not a description on a field that holds a class.
class SourceToTarget(Direction):
    """Entity A as the source and entity B as the target."""


class TargetToSource(Direction):
    """Entity B as the source and entity A as the target."""


class Verdict(_Out):
    source_to_target_transmission: SourceToTarget
    target_to_source_transmission: TargetToSource


# The version every call of this script makes.
VERSION = version_id(
    STAGE, TRANSMISSION_PROMPT, schema_text(Verdict.model_json_schema()), MODEL, EFFORT
)


class End(BaseModel):
    """A link end as the prompt names it."""

    name: str
    kind: str
    description: str | None


def ends() -> dict[str, End]:
    """Every id a link may end at: entities (kind, and `covers` where the seed has one, or the
    places a region covers) and seed groups (their members)."""
    seed = load_subjects()
    names = {e.id: e.name for e in seed.entities}
    out = {
        e.id: End(
            name=e.name,
            kind=e.kind.replace("_", " "),
            description=e.covers or (f"covers {', '.join(e.members)}" if e.members else None),
        )
        for e in seed.entities
    }
    for g in seed.groups:
        members = ", ".join(names[m] for m in g.members)
        out[g.id] = End(name=g.name, kind="group of entities", description=f"members: {members}")
    return out


def percent(x: float) -> str:
    return f"{x * 100:.4g}%"


def evidence(r: Relationship) -> list[str]:
    """The link's stored evidence, one line each: share, measure, further figures, service,
    source title, quote and date."""
    lines: list[str] = []
    if r.weight is not None:
        what = "stake held" if r.type == "holds_stake_in" else "share of the measure's total"
        lines.append(f"Share: {percent(r.weight)} ({what})")
    if r.measure:
        lines.append(f"Measure: {r.measure}, {r.value:g} {r.unit}")
    for f in r.figures:
        share = f", {percent(f.share)} of the total" if f.share is not None else ""
        lines.append(
            f"Further figure: {f.measure} by {f.dimension}, {f.segment}: {f.value:g} {f.unit}"
            f"{share}, {f.period}"
        )
    if r.service:
        lines.append(f"Service: {r.service}")
    if r.source_title:
        lines.append(f"Source: {r.source_title}")
    if r.quote:
        lines.append(f"Quote: {r.quote}")
    if r.as_of:
        lines.append(f"Date: {r.as_of}")
    return lines


def user_prompt(r: Relationship, names: dict[str, End]) -> str:
    def end(label: str, i: str) -> str:
        e = names[i]
        about = f": {e.description}" if e.description else ""
        return f"Entity {label}: {e.name} ({e.kind}){about}"

    found = evidence(r)
    return "\n".join(
        [
            end("A", r.from_),
            end("B", r.to),
            f"Relationship type: A {r.type} B ({LINK_TYPES[r.type].meaning})",
            # A reason built from a share starts at its verb ("has 12.2% of its loans in ..."),
            # so it gets A's name; a detail sentence stands alone.
            f"Relationship reason: {names[r.from_].name} {r.reason}"
            if r.reason != r.detail
            else f"Relationship reason: {r.reason}",
            "Evidence:" if found else "Evidence: none beyond the reason",
            *(f"- {line}" for line in found),
        ]
    )


def input_hash(prompt: str) -> str:
    """Of the prompt version (template, schema, model and effort) and the link's user prompt."""
    return hashlib.sha256(f"{VERSION}\n{prompt}".encode()).hexdigest()[:16]


class Outcome(BaseModel):
    """One link's calls: its entry, or none when every attempt failed; what they cost in USD;
    the cut-offs met on the way; the refusal category when the API declined it."""

    entry: RatingEntry | None
    cost: float
    calls: int
    cutoffs: int
    refused: str | None = None
    error: str | None = None


async def rate(r: Relationship, prompt: str, sem: asyncio.Semaphore) -> Outcome:
    """Rate one link, trying a cut-off or a failed call up to ATTEMPTS times; a refusal is final.
    Every billed attempt's cost counts."""
    cost, cutoffs, error = 0.0, 0, ""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            async with sem:
                reply = await ask_claude(
                    prompt, Verdict, system=TRANSMISSION_PROMPT, model=MODEL, effort=EFFORT
                )
        except ClaudeRefusal as e:
            return Outcome(
                entry=None,
                cost=cost + (e.cost_usd or 0.0),
                calls=attempt,
                cutoffs=cutoffs,
                refused=e.category or "no category",
                error=str(e),
            )
        except Exception as e:  # a cut-off or a failed call is tried again
            if isinstance(e, ClaudeBilled):
                cost += e.cost_usd or 0.0
            cutoffs += isinstance(e, ClaudeCutOff)
            error = f"{type(e).__name__}: {e}"
            continue
        v = reply.output
        entry = RatingEntry(
            id=r.id,
            from_to=Rating(
                strength=v.source_to_target_transmission.rating,
                reason=v.source_to_target_transmission.reason,
            ),
            to_from=Rating(
                strength=v.target_to_source_transmission.rating,
                reason=v.target_to_source_transmission.reason,
            ),
            model=reply.model_version or MODEL,
            date=date.today(),
            input_hash=input_hash(prompt),
            prompt_version=VERSION,
            rated_by="model",
        )
        return Outcome(
            entry=entry, cost=cost + (reply.cost_usd or 0.0), calls=attempt, cutoffs=cutoffs
        )
    return Outcome(entry=None, cost=cost, calls=ATTEMPTS, cutoffs=cutoffs, error=error)


HEADER = """\
# Written by api/scripts/rate_relationships.py; merged into api/seed/relationships.yaml by
# api/scripts/build_relationships.py.
#
# prompts[]: every version of the link rating prompt, numbered from 1 in the order first used.
#   id                the first 12 hex characters of the SHA-256 of the stage, template, schema,
#                     model and effort (as api/intelmonitor/pipeline/prompts.py makes them)
#   date, note, model, effort, text   when it was first used, what it is, and its template
# ratings[]: one per link, belongs_to_sector included, by link id ("from:type:to").
#   from_to, to_from  strength (Strong | Moderate | Weak | Non-propagating) and reason: how
#                     strongly a material adverse event at the first end reaches the second
#   model, date       the model that rated it and when; input_hash: of the prompt version and the
#                     link's prompt. The script rates a link again only when the hash changes.
#   prompt_version    the id of the version that rated it
#   rated_by          model | hand. To correct a rating, edit it and set rated_by: hand (model,
#                     input_hash and prompt_version may go); the script never touches a hand entry.
"""


class _Dumper(yaml.SafeDumper):
    """Writes a prompt's text as a literal block, line for line."""


def _text(dumper: yaml.SafeDumper, s: str) -> yaml.ScalarNode:
    style = "|" if "\n" in s else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", s, style=style)  # pyright: ignore[reportUnknownMemberType]


_Dumper.add_representer(str, _text)


def write(doc: RatingsFile, path: Path) -> None:
    doc = doc.model_copy(update={"ratings": sorted(doc.ratings, key=lambda e: e.id)})
    body = yaml.dump(
        doc.model_dump(exclude_none=True),
        Dumper=_Dumper,
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )
    path.write_text(HEADER + "\n" + body)


def with_version(prompts: list[PromptVersion]) -> list[PromptVersion]:
    """The prompts list with this script's version added, numbered one past the last, if it is
    not there yet."""
    if any(p.id == VERSION for p in prompts):
        return prompts
    number = max((p.number for p in prompts), default=0) + 1
    return [
        *prompts,
        PromptVersion(
            id=VERSION,
            number=number,
            date=date.today(),
            note=PROMPT_NOTE,
            model=MODEL,
            effort=EFFORT,
            text=TRANSMISSION_PROMPT,
        ),
    ]


def spread(rels: list[Relationship], entries: dict[str, RatingEntry]) -> None:
    """Each value's count per type and direction, over the links rated."""
    counts: Counter[tuple[str, str, str]] = Counter()
    for r in rels:
        if (e := entries.get(r.id)) is not None:
            counts[(r.type, "from_to", e.from_to.strength)] += 1
            counts[(r.type, "to_from", e.to_from.strength)] += 1
    strengths: tuple[Strength, ...] = Strength.__args__
    print(f"\n  {'type':32}{'direction':10}" + "".join(f"{s:>16}" for s in strengths))
    for t in REL_TYPES:
        for d in ("from_to", "to_from"):
            row = [counts[(t, d, s)] for s in strengths]
            if sum(row):
                print(f"  {t:32}{d:10}" + "".join(f"{n:>16}" for n in row))


# The collected files whose rows may be partner_of, outside the 22 types.
LINK_FILES = ("entity-links.yaml", "links-*.yaml")


def outside_rows() -> list[dict[str, str]]:
    """The collected partner_of rows, outside the 22 types and not built, for the review sheet."""
    collected = SEED_DIR / "collected"
    paths = sorted({p for pattern in LINK_FILES for p in collected.glob(pattern)})
    return [
        row
        for path in paths
        for row in yaml.safe_load(path.read_text())["relationships"]
        if row.get("outside_types")
    ]


def sheet(
    path: Path,
    rels: list[Relationship],
    doc: RatingsFile,
    names: dict[str, End],
) -> None:
    """The review sheet for the team: one row per rated link with the number of the prompt
    version that rated it, then the partner_of rows left outside the 22 types, unrated."""
    entries = {e.id: e for e in doc.ratings}
    numbers = {p.id: p.number for p in doc.prompts}
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "id",
                "from",
                "type",
                "to",
                "reason",
                "from_to_strength",
                "from_to_reason",
                "to_from_strength",
                "to_from_reason",
                "rated_by",
                "prompt_version",
            ]
        )
        for r in rels:
            if (e := entries.get(r.id)) is None:
                continue
            w.writerow(
                [
                    r.id,
                    names[r.from_].name,
                    r.type,
                    names[r.to].name,
                    r.reason,
                    e.from_to.strength,
                    e.from_to.reason,
                    e.to_from.strength,
                    e.to_from.reason,
                    e.rated_by,
                    numbers.get(e.prompt_version or "", ""),
                ]
            )
        for row in outside_rows():
            src, dst = row["from"], row["to"]
            w.writerow(
                [
                    f"{src}:partner_of:{dst}",
                    names[src].name if src in names else src,
                    "partner_of",
                    names[dst].name if dst in names else dst,
                    row["detail"],
                    "",
                    "",
                    "",
                    "",
                    "outside the 22 types, not built",
                    "",
                ]
            )
    print(f"\nwrote the review sheet {path}")


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rate each link Strong, Moderate, Weak or Non-propagating in each direction."
    )
    parser.add_argument("--limit", type=int, help="rate at most this many links")
    parser.add_argument("--sheet", type=Path, help="write the review sheet to this CSV file")
    parser.add_argument(
        "--ratings", type=Path, default=TRANSMISSION_PATH, help="the transmission.yaml to use"
    )
    args = parser.parse_args()

    data = RelationshipsFile.model_validate(yaml.safe_load(RELATIONSHIPS_PATH.read_text()))
    names = ends()
    rels = data.relationships
    ids = {r.id for r in rels}
    old = load_ratings_file(args.ratings)
    entries = {e.id: e for e in old.ratings if e.id in ids or e.rated_by == "hand"}
    todo: list[tuple[Relationship, str]] = []
    for r in rels:
        prompt = user_prompt(r, names)
        e = entries.get(r.id)
        if e is None or (e.rated_by == "model" and e.input_hash != input_hash(prompt)):
            todo.append((r, prompt))
    hand = sum(e.rated_by == "hand" for e in entries.values())
    print(
        f"{len(rels)} links: {len(rels) - len(todo)} rated and unchanged ({hand} by hand), "
        f"{len(todo)} to rate with prompt version {VERSION}; "
        f"{len(old.ratings) - len(entries)} entries for links that no longer exist dropped"
    )
    batch = todo[: args.limit] if args.limit is not None else todo
    prompts = with_version(old.prompts) if batch else old.prompts

    def doc() -> RatingsFile:
        return RatingsFile(prompts=prompts, ratings=list(entries.values()))

    outcomes: list[tuple[Relationship, Outcome]] = []
    if batch:
        sem = asyncio.Semaphore(CONCURRENCY)

        async def one(r: Relationship, p: str) -> tuple[Relationship, Outcome]:
            return r, await rate(r, p, sem)

        try:
            for done in asyncio.as_completed([one(r, p) for r, p in batch]):
                r, outcome = await done
                outcomes.append((r, outcome))
                if outcome.entry is not None:
                    entries[r.id] = outcome.entry
                    if len(outcomes) % WRITE_EVERY == 0:
                        write(doc(), args.ratings)
        finally:
            write(doc(), args.ratings)
    elif len(entries) != len(old.ratings):
        write(doc(), args.ratings)
    rated = [o for _, o in outcomes if o.entry is not None]
    cost = sum(o.cost for _, o in outcomes)
    calls = sum(o.calls for _, o in outcomes)
    refused = [(r, o) for r, o in outcomes if o.refused]
    failed = [(r, o) for r, o in outcomes if o.entry is None and not o.refused]
    print(
        f"rated {len(rated)} links on {MODEL} at effort {EFFORT} in {calls} calls for "
        f"${cost:.4f} (${cost / max(len(rated), 1):.5f} a link rated, measured from the API's "
        f"usage); {sum(o.cutoffs for _, o in outcomes)} cut-offs retried, {len(refused)} "
        f"refusals, {len(failed)} failures; {len(todo) - len(batch)} left unrated by --limit"
    )
    for r, o in refused:
        print(f"  refused ({o.refused}): {r.id}")
    for r, o in failed:
        print(f"  failed: {r.id}: {o.error}")
    spread(rels, entries)
    if args.sheet:
        sheet(args.sheet, rels, doc(), names)
    if batch:
        print("\nnow rerun scripts/build_relationships.py to merge the ratings")
    return 1 if refused or failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
