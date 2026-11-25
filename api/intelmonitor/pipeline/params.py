"""Pipeline tuning: models, budgets, prompts and concurrency, all in one place."""

from datetime import date
from typing import Literal

from intelmonitor.pipeline.prompts import THINK_FIRST
from intelmonitor.relationships import LINK_TYPES, RelType, Strength
from intelmonitor.seed import EntityKind

# Screen and same_event run on Claude Haiku 5.5 with thinking off; a Haiku
# refusal is retried once on REFUSAL_FALLBACK_MODEL (pipeline/inference.py). The other stages run
# on settings.anthropic_model.
HAIKU_MODEL = "claude-haiku-5-5"
REFUSAL_FALLBACK_MODEL = "claude-sonnet-5-5"

# Documents longer than this state budget are screened on their title plus leading chunks, to
# keep the screen's cost down (about 4 characters per token).
SCREEN_STATE_TOKENS = 24_000
CHARS_PER_TOKEN = 4

# Claude reads the full text for extraction, capped for the very longest PDFs.
EXTRACT_MAX_CHARS = 200_000

# Same-event candidates: findings sharing a subject, event dates within MERGE_WINDOW_DAYS, cosine
# similarity at or above MERGE_MIN_SIMILARITY, at most MERGE_MAX_NEIGHBOURS per finding.
# Haiku decides each pair.
MERGE_WINDOW_DAYS = 10
MERGE_MIN_SIMILARITY = 0.6
MERGE_MAX_NEIGHBOURS = 5
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

# Priority labels, indexed by level 0 to 2. Claude rates a development's priority for each entity
# it happened to or that did it, with PRIORITY_PROMPT; the development's level is its highest.
Priority = Literal["Low", "Medium", "High"]
PRIORITY_LABELS: list[Priority] = ["Low", "Medium", "High"]
# The team's direct H/M/L prompt (deck of 10 Nov 2026, slide 7), as written, with the subject's
# name in {entity} and its Singapore phrase (pipeline/subjects.py) where the slide says "the
# subject's Singapore branch" or "the Singapore branch". Fill it with `priority_system`.
PRIORITY_PROMPT = """\
You are a risk analyst at the Monetary Authority of Singapore. You will be given a news finding \
about a financial institution (the "subject"): {entity}. Classify the finding's priority as High, \
Medium, or Low using the definitions below.

## Priority levels

High (Significant or Critical)
- Significant: a material change for {singapore}, whether a threat or an opportunity. Examples: \
an incident, an enforcement action, a loss or failure at {singapore}, a binding decision that \
changes what {singapore} must do, a major deal involving {singapore}, or a market shock \
{singapore} is exposed to.
- Critical: severe, urgent or systemic for {singapore} or for Singapore's financial system, and \
needs immediate attention.

Medium (Notable)
- Worth knowing: a moderate change for the subject. Examples: new guidance, a consultation, a \
results update, or a larger event that reaches the subject or Singapore only indirectly.
- Also use Medium for a material event at the subject's head office or another branch that could \
plausibly reach {singapore} but has not yet been shown to.

Low (Minor or Trivial)
- Minor: small or expected. Examples: routine statistics, speeches, FAQs, procedural notices, \
product and promotional news, or a foreign event with little route to Singapore.
- Trivial: routine, promotional, or barely about the subject, with no consequence for it or for \
Singapore.

## Rules
1. High is reserved for findings that affect {singapore} or Singapore's financial system. A \
serious event elsewhere in the group is High only if the finding shows it directly affects \
{singapore} (e.g. its capital, liquidity, operations, staff, customers, or licence).
2. If no Singapore link is stated or clearly implied, do not assume one.
3. Judge only from the finding provided. Do not use outside knowledge.

## Output
Respond with JSON only, no other text:
{{"priority": "High" | "Medium" | "Low", "reason": "<one sentence, citing the Singapore link if \
High>"}}"""
SINGAPORE_SYSTEM = "Singapore's financial system"
# The priority call thinks at this effort on Sonnet 5.5.
PRIORITY_EFFORT: Literal["medium"] = "medium"


def priority_system(
    entity: str,
    singapore: str,
    template: str = PRIORITY_PROMPT,
    anchor: str = "",
    local_anchor: str = "",
) -> str:
    """PRIORITY_PROMPT, or another priority version's template, for one subject: its name, its
    Singapore phrase and, for a template with {anchor} or {local_anchor}, that anchor phrase
    (pipeline/subjects.py), which must then be given. Where the phrase is Singapore's financial
    system itself, "the phrase or (for) Singapore's financial system" names it once."""
    phrases = {"anchor": anchor, "local_anchor": local_anchor}
    fields = {"entity": entity, "singapore": singapore} | {k: v for k, v in phrases.items() if v}
    text = template.format(**fields)
    if singapore == SINGAPORE_SYSTEM:
        for joiner in (" or for ", " or "):
            text = text.replace(f"{SINGAPORE_SYSTEM}{joiner}{SINGAPORE_SYSTEM}", SINGAPORE_SYSTEM)
    return text


def _link_types_text() -> str:
    """The 22 types in the deck's three groups, each with its meaning."""
    lines: list[str] = []
    group = ""
    for t, lt in LINK_TYPES.items():
        if lt.group != group:
            group = lt.group
            lines.append(f"{group}:")
        lines.append(f"- {t}: {lt.meaning}")
    return "\n".join(lines)


# The activated relationship types (deck of 16 Nov 2026, pages 9 and 10; ticket 05 of
# link-types-and-inferred-impact): the link_types call (pipeline/developments.py) reads a
# development and its direct entities at Medium or High, and picks for each entity the types its
# consequences could travel through. Page 9's selection rule and ten examples and page 10's rule
# are the deck's words, the second person for page 10's "The LLM"; the types are LINK_TYPES (pages
# 1 to 4); the framing (the entities given with their levels, a type counting at either end of a
# link, what travelling through a relationship means, when the structural types apply, no broader
# type beside a specific one, each example listing every type that applies) is ours, added for the
# cases' rule that a type the example's row does not name must not be picked. The sentence on an
# entity that is itself a jurisdiction, regulator or exchange is ours too (23 Nov 2026, the eval's
# OUR_CASES): such an entity activates a structural type only for a measure, condition or
# disruption of its own that bears on the entities in it as a class. No JSON shape: the
# answer's fields are described in its schema (ticket 09).
LINK_TYPES_PROMPT = f"""\
You are a risk analyst at the Monetary Authority of Singapore. You will be given a news finding \
and the entities it directly affects, each with its direct priority (High or Medium) and the \
reason for it. For each entity, select the relationship types the finding could activate: the \
types of relationship through which the consequences the finding describes could travel from \
that entity to other entities. A type counts whichever end of the relationship the entity is \
at: the entity may provide the service or receive it, hold the stake or be held.

## Relationship types
{_link_types_text()}

## Selecting relationship types
Select relationship types based on the consequences described in the finding, not merely the \
finding's subject or every relationship connected to the direct entity. For example: an engine \
defect can activate material_supplier_to; a maintenance failure can activate \
provides_business_service_to; a KrisFlyer outage can activate provides_customer_benefits_with; \
an engine problem should not activate airline-bank rewards relationships.
You do not judge every possible indirect entity. Include a relationship type only where the \
finding describes consequences that could plausibly travel through that pathway. Do not include \
a type merely because the relationship exists.
An entity for which the finding describes no such consequence gets no types.
A consequence travels through a relationship when the entity at its other end would bear it \
because of that relationship: a service, payment, data, product or supply it relies on fails, or \
a loan, investment, stake, policy or joint arrangement it holds loses value. The structural \
types (regulated_by, operates_in, exposed_to, member_of, participates_in, listed_on, \
belongs_to_sector) apply only when the finding describes a consequence for the regulator, \
jurisdiction, market, organisation, scheme, exchange or sector at the other end; that the entity \
is regulated, operates somewhere or is listed is not such a consequence, and nor is a regulator's \
investigation of it or penalty on it. Where the entity is itself a jurisdiction, regulator or \
exchange, a structural type applies only when the finding is a measure, condition or disruption \
of that jurisdiction, regulator or exchange that bears on the entities operating in it, regulated \
by it or listed on it as a class, such as a rule or policy that binds them, an economic, monetary \
or market-wide shock, or an outage; not when the finding is about one firm, including the \
regulator's action against that firm, nor when it is a local event that merely happened there. \
Work from the \
consequences the finding states, not from ones you expect to follow from them. A failure of one \
service is one pathway and takes one type, the most specific that fits it, even where that \
service could also be described as technology or as a business service.

## Examples
Each example lists every type that applies to its finding; a type it does not list does not \
apply.
Finding: applicable relationship types
- Singtel enterprise-connectivity outage: provides_connectivity_to
- Singtel consumer-advertising fine: holds_stake_in, invests_in (not provides_connectivity_to)
- MoneySmart bank-application data breach: shares_or_processes_data_with
- MoneySmart misleading product comparison: distributes_products_for
- Payment-processor outage: processes_transactions_for
- Isolated engine defect affecting a specific engine model: material_supplier_to; potentially \
provides_business_service_to if maintenance is implicated
- Fleet-wide engine defect causing extensive grounding: material_supplier_to; potentially \
provides_business_service_to, insures, holds_stake_in, invests_in and lends_to, where the stated \
consequences support them
- Aircraft-maintenance failure: provides_business_service_to
- KrisFlyer redemption outage: provides_customer_benefits_with
- General flight disruption unrelated to KrisFlyer: not provides_customer_benefits_with

{THINK_FIRST}"""
# The link_types call thinks at this effort on Sonnet 5.5. High follows the priority pick
# (version 8); the spec has the user pick between medium and high from step 7's sweep.
LINK_TYPES_EFFORT: Literal["high"] = "high"


# Slide 7's definition of each level, in the same order, as Help shows them.
PRIORITY_DEFINITIONS: list[str] = [
    "Minor: small or expected, such as routine statistics, speeches, FAQs, procedural notices, "
    "product and promotional news, or a foreign event with little route to Singapore. Trivial: "
    "routine, promotional, or barely about the subject, with no consequence for it or for "
    "Singapore.",
    "Worth knowing: a moderate change for the subject, such as new guidance, a consultation, a "
    "results update, or a larger event that reaches the subject or Singapore only indirectly; "
    "or a material event at the subject's head office or another branch that could plausibly "
    "reach the Singapore branch but has not yet been shown to.",
    "Significant: a material change for the subject's Singapore branch, whether a threat or an "
    "opportunity. Critical: severe, urgent or systemic for the Singapore branch or for "
    "Singapore's financial system, and needs immediate attention.",
]
# Needs attention is High; Watch and "material" start at Medium.
NEEDS_ATTENTION_LEVEL = 2
MATERIAL_LEVEL = 1

# Inferred impact (pipeline/impact.py; deck of 16 Nov 2026, page 11; ticket 06 of
# link-types-and-inferred-impact): the level a development passes through one link, of a type it
# activates for a named entity, to a neighbour, by the entity's direct level and the link's
# strength in that direction; None passes nothing. High by Strong gives High, with no further check
# (page 12, P8). A Non-propagating direction (page 6) passes nothing.
INDIRECT_TABLE: dict[int, dict[Strength, int | None]] = {
    2: {"Strong": 2, "Moderate": 1, "Weak": 0, "Non-propagating": None},
    1: {"Strong": 1, "Moderate": 0, "Weak": None, "Non-propagating": None},
    0: {"Strong": 0, "Moderate": None, "Weak": None, "Non-propagating": None},
}
# Whether a Low direct item passes, as page 11 draws it (Low to a Strong link as Low). Off by
# default (P7); the run prints the row counts under both settings for the pick.
INDIRECT_FROM_LOW = False
# Whether a named entity passes only developments adverse for it, the gate of the 10 Nov rules.
# On: the product owner decided it (P6, 23 Nov 2026); the run prints the row counts under both.
INDIRECT_ADVERSE_ONLY = True
# Hubs: the kinds whose news is about a class of entities, not one firm. The product owner decided
# (23 Nov 2026) that "Could affect" does not carry class-wide country and regulator news: a hub
# passes nothing down a MEMBER_TYPES link to what operates in it, is regulated by it, is listed on
# it or is a member of it (itself or through a region that covers it), nor to an entity whose
# home it is. Its other links pass as any entity's do. Market infrastructures and payment systems
# are not hubs: an exchange's outage still reaches what is listed on it.
HUB_KINDS: frozenset[EntityKind] = frozenset(
    {"country", "region", "regulator", "central_bank", "agency", "international_body"}
)
# The link types by which an entity belongs to a hub (decided 23 Nov 2026, with HUB_KINDS).
MEMBER_TYPES: frozenset[RelType] = frozenset(
    {"operates_in", "regulated_by", "listed_on", "member_of", "participates_in"}
)


def display_key(
    level: int, adverse: bool, last_published: date, development_id: str
) -> tuple[int, bool, int, str]:
    """The order a subject's developments are shown and briefed in: High, Medium, Low, adverse
    first within High and Medium, most recent first within each. `level` and `adverse` are the
    development's for the subjects in view. The feed (server/queries.py) and the briefs
    (pipeline/summaries.py) both sort by it."""
    return (-level, level > 0 and not adverse, -last_published.toordinal(), development_id)


# A source reports on a jurisdiction when its screen scores it at least this: 1.0 when the screen
# lists it, a probability in older stores.
JURISDICTION_THRESHOLD = 0.5

# Run 1 is computed as of T0 minus this many days, Run 2 as of T0.
RUN_SPACING_DAYS = 7

# Concurrent model calls. On the API (settings.claude_via_api, the default), the API's rate limits
# are the cap; Claude on the login is capped to stay within the subscription allowance.
CLAUDE_CONCURRENCY = 4
CLAUDE_API_CONCURRENCY = 16
