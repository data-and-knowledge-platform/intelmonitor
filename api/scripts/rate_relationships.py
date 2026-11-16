"""Rate each link Strong, Moderate or Weak in each direction (ticket 18), once, into
api/seed/collected/transmission.yaml.

Reads api/seed/relationships.yaml and makes one Claude Sonnet 5.5 call per link that is new or
whose input changed (every type but belongs_to_sector). The system prompt is the team's text
(TEAM_PROMPT); the user prompt names entity A (the link's `from`) and entity B (its `to`), each
with its kind and description, and gives the type, the reason and the stored evidence. A link is
rated again only when the hash of the model and both prompts changes, and an entry marked
`rated_by: hand` is never touched. Entries for links that no longer exist are dropped, hand
entries excepted.

Ratings are written as they arrive. A run where any link failed exits non-zero; rerun it to rate
the failed links. After a run, rerun scripts/build_relationships.py to merge the ratings into
relationships.yaml.

Usage (from api/):
  uv run python scripts/rate_relationships.py [--limit N] [--sheet <file>.csv]
--limit rates at most N links this run (the rest stay unrated); --sheet writes the review sheet.
Both print the spread of strengths per type and direction.
"""

import argparse
import asyncio
import csv
import hashlib
from collections import Counter
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from intelmonitor.pipeline.claude import ask_claude
from intelmonitor.relationships import (
    REL_TYPES,
    RELATIONSHIPS_PATH,
    TRANSMISSION_PATH,
    Rating,
    RatingEntry,
    RatingsFile,
    Relationship,
    RelationshipsFile,
    Strength,
    load_ratings,
    rateable,
)
from intelmonitor.seed import load_subjects

MODEL = "claude-sonnet-5-5"
EFFORT = "medium"
CONCURRENCY = 8
# transmission.yaml is rewritten after every this many ratings, and once more at the end (also on
# Ctrl-C or a crash), so a stopped run keeps the ratings already paid for.
WRITE_EVERY = 20

# The team's prompt (wayfind/topics-watchlists-views/assets/16/transmission-prompt.md, deck of 10
# Nov 2026, slides 5 and 6) with its zero-width spaces stripped. The answer comes back through a
# structured output schema with the same keys as its OUTPUT FORMAT.
TEAM_PROMPT = """\
You are classifying the directional impact transmission strength of an existing, evidenced \
relationship between two entities.
The existence and type of the relationship have already been established. Do NOT determine \
whether a relationship exists.
Your task is only to classify how strongly impact could transmit through the relationship in \
EACH direction.
DEFINITION
For each direction, answer:
"If the source entity experiences a material adverse event, how materially could the target \
entity be affected specifically because of this relationship?"
Classify each direction as exactly one of:
- Strong
- Moderate
- Weak
STRONG
A material adverse event at the source entity could reasonably cause a material or significant \
impact on the target entity through this relationship.
Examples of supporting evidence:
- critical or difficult-to-replace dependency
- controlling or highly material ownership
- large financial or economic exposure
- high concentration
- low substitutability
- relationship is critical to core operations
MODERATE
A material adverse event at the source entity could cause a meaningful but contained impact on \
the target entity.
Examples of supporting evidence:
- material but diversified exposure
- important dependency with viable alternatives
- significant but non-controlling ownership
- meaningful relationship affecting only part of the target's operations
WEAK
A material adverse event at the source entity would normally cause only limited impact on the \
target entity through this relationship.
Examples of supporting evidence:
- small exposure or ownership
- non-critical dependency
- high substitutability
- highly diversified suppliers, customers or exposures
- limited operational or financial significance
RULES
1. Evaluate both directions independently. A→B and B→A may have different ratings.
2. Rate IMPACT TRANSMISSION, not general relationship importance, closeness, similarity or \
prominence.
3. Assume a MATERIAL adverse event occurs at the source entity. Do not vary the rating based on \
the severity of a specific news finding.
4. Use the provided relationship type and relationship reason as the primary evidence. Give \
particular weight to quantitative evidence such as ownership %, exposure %, concentration and \
dependency levels.
5. Do not automatically rate any relationship type as Strong. For example, a depends_on \
relationship may be Strong, Moderate or Weak depending on criticality and substitutability.
6. Consider direction carefully. A supplier may be critical to a customer while that customer \
represents only a small part of the supplier's business.
7. Do not assume symmetry. The same relationship can be Strong in one direction and Weak in the \
other.
8. Do not invent missing facts. Where the evidence does not support a higher rating, use the \
more conservative supported rating.
9. The relationship already exists. Do not output None, Unknown, null, or create another \
relationship type.
10. Return valid JSON only, with no markdown or additional commentary.
OUTPUT FORMAT
{
  "entity_a_to_entity_b": {
\t"transmission": "Strong",
\t"reason": "Concise explanation based only on the available relationship evidence."
  },
  "entity_b_to_entity_a": {
\t"transmission": "Weak",
\t"reason": "Concise explanation based only on the available relationship evidence."
  }
}
"transmission" must be exactly one of:
"Strong"
"Moderate"
"Weak"
"""


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Direction(_Out):
    transmission: Strength
    reason: str = Field(
        description="Concise explanation based only on the available relationship evidence."
    )


class Verdict(_Out):
    entity_a_to_entity_b: Direction
    entity_b_to_entity_a: Direction


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

    return "\n".join(
        [
            end("A", r.from_),
            end("B", r.to),
            f"Relationship type: A {r.type} B",
            # A reason built from a share starts at its verb ("has 12.2% of its loans in ..."),
            # so it gets A's name; a detail sentence stands alone.
            f"Relationship reason: {names[r.from_].name} {r.reason}"
            if r.reason != r.detail
            else f"Relationship reason: {r.reason}",
            "Evidence:",
            *(f"- {line}" for line in evidence(r)),
        ]
    )


def input_hash(prompt: str) -> str:
    """Of the model, the system prompt and the link's user prompt."""
    return hashlib.sha256(f"{MODEL}\n{TEAM_PROMPT}\n{prompt}".encode()).hexdigest()[:16]


async def rate(r: Relationship, prompt: str, sem: asyncio.Semaphore) -> tuple[RatingEntry, float]:
    """One call for one link: its entry and what the call cost in USD."""
    async with sem:
        reply = await ask_claude(prompt, Verdict, system=TEAM_PROMPT, model=MODEL, effort=EFFORT)
    v = reply.output
    entry = RatingEntry(
        id=r.id,
        from_to=Rating(
            strength=v.entity_a_to_entity_b.transmission, reason=v.entity_a_to_entity_b.reason
        ),
        to_from=Rating(
            strength=v.entity_b_to_entity_a.transmission, reason=v.entity_b_to_entity_a.reason
        ),
        model=reply.model_version or MODEL,
        date=date.today(),
        input_hash=input_hash(prompt),
        rated_by="model",
    )
    return entry, reply.cost_usd or 0.0


HEADER = """\
# Written by api/scripts/rate_relationships.py; merged into api/seed/relationships.yaml by
# api/scripts/build_relationships.py.
#
# ratings[]: one per link but belongs_to_sector, by link id ("from:type:to").
#   from_to, to_from  strength (Strong | Moderate | Weak) and reason: how strongly a material
#                     adverse event at the first end reaches the second
#   model, date       the model that rated it and when; input_hash: of the model and the
#                     prompts it saw. The script rates a link again only when the hash changes.
#   rated_by          model | hand. To correct a rating, edit it and set rated_by: hand (model
#                     and input_hash may go); the script never touches a hand entry.
"""


def write(entries: dict[str, RatingEntry], path: Path = TRANSMISSION_PATH) -> None:
    doc = RatingsFile(ratings=[entries[i] for i in sorted(entries)])
    body = yaml.safe_dump(
        doc.model_dump(exclude_none=True), sort_keys=False, allow_unicode=True, width=100
    )
    path.write_text(HEADER + "\n" + body)


def spread(rels: list[Relationship], entries: dict[str, RatingEntry]) -> None:
    """Strong, Moderate and Weak per type and direction, over the links rated."""
    counts: Counter[tuple[str, str, str]] = Counter()
    for r in rels:
        if (e := entries.get(r.id)) is not None:
            counts[(r.type, "from_to", e.from_to.strength)] += 1
            counts[(r.type, "to_from", e.to_from.strength)] += 1
    strengths: tuple[Strength, ...] = Strength.__args__
    print(f"\n  {'type':16}{'direction':10}" + "".join(f"{s:>10}" for s in strengths))
    for t in REL_TYPES:
        for d in ("from_to", "to_from"):
            row = [counts[(t, d, s)] for s in strengths]
            if sum(row):
                print(f"  {t:16}{d:10}" + "".join(f"{n:>10}" for n in row))


def sheet(
    path: Path, rels: list[Relationship], entries: dict[str, RatingEntry], names: dict[str, End]
) -> None:
    """The review sheet for the team: one row per rated link."""
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
                ]
            )
    print(f"\nwrote the review sheet {path}")


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rate each link Strong, Moderate or Weak in each direction."
    )
    parser.add_argument("--limit", type=int, help="rate at most this many links")
    parser.add_argument("--sheet", type=Path, help="write the review sheet to this CSV file")
    args = parser.parse_args()

    data = RelationshipsFile.model_validate(yaml.safe_load(RELATIONSHIPS_PATH.read_text()))
    names = ends()
    rels = [r for r in data.relationships if rateable(r)]
    ids = {r.id for r in rels}
    old = load_ratings()
    entries = {i: e for i, e in old.items() if i in ids or e.rated_by == "hand"}
    todo: list[tuple[Relationship, str]] = []
    for r in rels:
        prompt = user_prompt(r, names)
        e = entries.get(r.id)
        if e is None or (e.rated_by == "model" and e.input_hash != input_hash(prompt)):
            todo.append((r, prompt))
    hand = sum(e.rated_by == "hand" for e in entries.values())
    print(
        f"{len(rels)} rateable links: {len(rels) - len(todo)} rated and unchanged "
        f"({hand} by hand), {len(todo)} to rate; "
        f"{len(old) - len(entries)} entries for links that no longer exist dropped"
    )
    batch = todo[: args.limit] if args.limit is not None else todo
    cost = 0.0
    failed: list[str] = []
    if batch:
        sem = asyncio.Semaphore(CONCURRENCY)

        async def one(
            r: Relationship, p: str
        ) -> tuple[Relationship, tuple[RatingEntry, float] | Exception]:
            try:
                return r, await rate(r, p, sem)
            except Exception as e:  # one link's failure leaves the rest running
                return r, e

        rated = 0
        try:
            for done in asyncio.as_completed([one(r, p) for r, p in batch]):
                r, res = await done
                if isinstance(res, Exception):
                    failed.append(f"{r.id}: {res}")
                    continue
                entries[r.id] = res[0]
                cost += res[1]
                rated += 1
                if rated % WRITE_EVERY == 0:
                    write(entries)
        finally:
            write(entries)
    elif len(entries) != len(old):
        write(entries)
    print(
        f"rated {len(batch) - len(failed)} links on {MODEL} for ${cost:.2f}"
        f" (${cost / max(len(batch) - len(failed), 1):.4f} a link); "
        f"{len(todo) - len(batch)} left unrated by --limit"
    )
    for f in failed:
        print(f"  failed: {f}")
    spread(rels, entries)
    if args.sheet:
        sheet(args.sheet, rels, entries, names)
    if batch:
        print("\nnow rerun scripts/build_relationships.py to merge the ratings")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
