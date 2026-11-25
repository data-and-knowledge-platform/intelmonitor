"""Inferred impact by the team's fixed table and the activated relationship types (deck of 16 Nov
2026, pages 11 and 12; ticket 06 of link-types-and-inferred-impact), with no model call.

For each development and each entity named on it (development_subjects), every link one hop away
whose type the development activates for that entity (development_subjects.link_types, the
link_types stage) is a path to the entity at its other end, unless that entity is named too. Its
level comes from INDIRECT_TABLE (pipeline/params.py): the named entity's direct level by the
link's strength in the direction from it. High gives High, Medium, Low for a Strong, Moderate,
Weak link; Medium gives Medium, Low, nothing; Low passes nothing unless INDIRECT_FROM_LOW; a
Non-propagating direction passes nothing. An entity whose types were not asked passes nothing.
With INDIRECT_ADVERSE_ONLY (on, P6), only entities the development is adverse for pass
(passes_adverse_rule), and the link_types stage does not ask a development that none of its
entities would pass. A hub
(HUB_KINDS: a country, region, regulator, central bank, agency or international body) passes
nothing down a MEMBER_TYPES link to the entity that operates in it, is regulated by it, is listed
on it or is a member of it, itself or through a region that covers it, nor to an entity whose
home it is; its other links pass, and a firm's own news still reaches its regulator and country
(decided 23 Nov 2026). Never a second hop; a link into a region entity counts for each member
country.

Every path is kept. Paths are never added together; each entity reached has one best path: the
highest level, then the stronger link, the larger share, the link id, the source id.

The rows go to development_impacts, apart from development_subjects, so an inferred impact is
never shown as news about the entity itself. The wording helpers below write links in plain
words for the Overview's Related graph, the briefs, the routes the API serves and the build's
reasons.
"""

import json
import sqlite3
from dataclasses import dataclass, replace
from functools import cache
from typing import NamedTuple

from intelmonitor.pipeline.params import (
    HUB_KINDS,
    INDIRECT_ADVERSE_ONLY,
    INDIRECT_FROM_LOW,
    INDIRECT_TABLE,
    MATERIAL_LEVEL,
    MEMBER_TYPES,
    PRIORITY_LABELS,
)
from intelmonitor.relationships import (
    LOAN_MEASURES,
    HeadlineMeasure,
    Node,
    Reach,
    Relationship,
    RelType,
    Service,
    Strength,
    owns,
    reaches,
)
from intelmonitor.seed import Entity, load_subjects

SERVICE_WORDS: dict[Service, str] = {
    "banking": "banking services",
    "blockchain": "blockchain infrastructure",
    "cloud": "cloud services",
    "core_banking": "core banking systems",
    "custody": "custody services",
    "data_centre": "data centre services",
    "distribution": "distribution",
    "market_infrastructure": "market infrastructure",
    "payments": "payments",
    "print_mail": "print and mail services",
    "reserve_management": "reserve management",
    "security": "security services",
    "telco": "telecoms",
}

# A link type in plain words from its `from` end, before the name at its `to` end: "Singtel
# provides connectivity to DBS". exposed_to reads from its measure instead (EXPOSURE_WORDS).
LINK_WORDS: dict[RelType, str] = {
    "holds_stake_in": "holds a stake in",
    "lends_to": "lends to",
    "invests_in": "invests in",
    "funds": "funds",
    "insures": "insures",
    "provides_technology_to": "provides technology to",
    "provides_connectivity_to": "provides connectivity to",
    "provides_business_service_to": "provides business services to",
    "processes_transactions_for": "processes transactions for",
    "shares_or_processes_data_with": "shares data with",
    "distributes_products_for": "distributes products for",
    "provides_customer_benefits_with": "offers customer benefits with",
    "jointly_offers_product_with": "jointly offers a product with",
    "material_customer_of": "is a major customer of",
    "material_supplier_to": "is a major supplier to",
    "regulated_by": "is regulated by",
    "operates_in": "operates in",
    "exposed_to": "lends to",
    "member_of": "is a member of",
    "participates_in": "takes part in",
    "listed_on": "is listed on",
    "belongs_to_sector": "belongs to",
}


@dataclass(frozen=True)
class ExposureWords:
    """An exposure measure in plain words. In a sentence, the subject `verb`s a share of its
    `basis` `prep` the target: Circle "holds" 84.9% of its "stablecoin reserves" "in" the Circle
    Reserve Fund. Graph labels read `head` from the from end, `inbound` from the to end, and
    `short` after a share ("84.9% of reserves")."""

    verb: str
    basis: str
    prep: str
    head: str
    inbound: str
    short: str


_LOANS = ExposureWords("has", "loans", "in", "lends to", "lent to by", "loans")
EXPOSURE_WORDS: dict[HeadlineMeasure, ExposureWords] = {
    "gross_customer_loans": _LOANS,
    "loans_by_currency": _LOANS,
    "rwa": _LOANS,
    "reserve_assets": ExposureWords(
        "holds", "stablecoin reserves", "in", "holds reserves in", "holds reserves of", "reserves"
    ),
    "repo_principal": ExposureWords(
        "has", "reserve fund", "in repo with", "repo with", "repo counterparty to", "reserve fund"
    ),
    "revenue": ExposureWords(
        "earns", "revenue", "from", "earns revenue from", "a revenue source of", "revenue"
    ),
    "distribution_costs": ExposureWords(
        "pays",
        "distribution costs",
        "to",
        "pays distribution costs to",
        "paid distribution costs by",
        "distribution costs",
    ),
    "token_supply": ExposureWords(
        "issues", "token supply", "on", "issues tokens on", "carries tokens of", "token supply"
    ),
    "assets_on_platform": ExposureWords(
        "holds",
        "customer assets",
        "in",
        "holds customer assets in",
        "held for customers by",
        "customer assets",
    ),
    "stablecoin_holdings": ExposureWords(
        "holds",
        "stablecoins",
        "issued by",
        "holds stablecoins of",
        "stablecoins held by",
        "stablecoins",
    ),
    "card_volume": ExposureWords(
        "runs", "card volume", "on", "runs card volume on", "carries card volume of", "card volume"
    ),
}


def _exposure_words(r: Relationship) -> ExposureWords:
    return EXPOSURE_WORDS[r.measure] if r.measure else _LOANS


def _place(node: Node) -> str:
    """A place or sector as it reads inside a sentence: a sector's first letter in lower case,
    unless it starts with an acronym (US Treasury securities)."""
    name = node.name
    if node.kind != "sector" or name[:2].isupper():
        return name
    return name[0].lower() + name[1:]


def _share(weight: float) -> str:
    """12.2%, or 100% for a whole number."""
    return f"{weight * 100:.1f}".removesuffix(".0") + "%"


# A link type in plain words from its `to` end, before the name at its `from` end: "DBS gets its
# connectivity from Singtel". A SYMMETRIC type reads the same from either end.
INBOUND_WORDS: dict[RelType, str] = {
    "holds_stake_in": "held by",
    "lends_to": "borrows from",
    "invests_in": "gets investment from",
    "funds": "gets funding from",
    "insures": "gets insurance from",
    "provides_technology_to": "gets its technology from",
    "provides_connectivity_to": "gets its connectivity from",
    "provides_business_service_to": "gets its business services from",
    "processes_transactions_for": "gets its transaction processing from",
    "shares_or_processes_data_with": "gets data from",
    "distributes_products_for": "sells through",
    "provides_customer_benefits_with": "offers customer benefits with",
    "jointly_offers_product_with": "jointly offers a product with",
    "material_customer_of": "earns much of its revenue from",
    "material_supplier_to": "gets key supplies from",
    "regulated_by": "regulates",
    "operates_in": "hosts",
    "exposed_to": "lent to by",
    "member_of": "includes",
    "participates_in": "is joined by",
    "listed_on": "lists",
    "belongs_to_sector": "includes",
}


def type_words(r: Relationship) -> tuple[str, str]:
    """The link's type in plain words from its `from` end and from its `to` end, where its
    service names what is provided: ("provides cloud services to", "gets its cloud services
    from"), ("processes payments for", "gets its payment processing from"); else LINK_WORDS and
    INBOUND_WORDS."""
    if r.service and r.type in ("provides_technology_to", "provides_business_service_to"):
        what = SERVICE_WORDS[r.service]
        return f"provides {what} to", f"gets its {what} from"
    if r.service == "payments" and r.type == "processes_transactions_for":
        return "processes payments for", "gets its payment processing from"
    return LINK_WORDS[r.type], INBOUND_WORDS[r.type]


def link_words(r: Relationship, to: Node) -> str:
    """One link in plain words, without its subject: "provides connectivity to DBS", "provides
    cloud services to DBS", "has 12.2% of its loans in Hong Kong", "holds 84.9% of its stablecoin
    reserves in the Circle Reserve Fund", "holds 28.3% of DBS", "owns Citibank" for a controlling
    stake with no share."""
    if r.type == "exposed_to":
        w = _exposure_words(r)
        share = f"{_share(r.weight)} of its {w.basis}" if r.weight is not None else w.basis
        return f"{w.verb} {share} {w.prep} {_place(to)}"
    phrase = f"{type_words(r)[0]} {to.name}"
    if r.type == "holds_stake_in" and r.weight is not None:
        phrase = f"holds {_share(r.weight)} of {to.name}"
    elif owns(r):
        phrase = f"owns {to.name}"
    if r.chart_reading:
        phrase += " (read from a chart)"
    return phrase


def relationship_label(r: Relationship) -> str:
    """A stored link in plain words without its ends, as the API serves it: "lends to, 12.2% of
    loans", "earns revenue from, 24% of revenue", "provides cloud services to", "holds a stake
    in, 100%"."""
    if r.type == "exposed_to":
        w = _exposure_words(r)
        if r.weight is None:
            return f"{w.head}, share not disclosed"
        return f"{w.head}, {_share(r.weight)} of {w.short}"
    label = type_words(r)[0]
    if r.type == "holds_stake_in" and r.weight is not None:
        label += f", {_share(r.weight)}"
    return label


def short_label(r: Relationship, outbound: bool) -> str:
    """A stored link in a few words from one end, for the Related graph: from its `from` end
    when `outbound` ("holds 100%", "24.4% of loans", "provides connectivity to"), else from its
    `to` end ("100% held by", "3.1% of its loans", "gets its connectivity from")."""
    share = _share(r.weight) if r.weight is not None else None
    if r.type == "exposed_to":
        w = _exposure_words(r)
        if share:
            return f"{share} of {w.short}" if outbound else f"{share} of its {w.short}"
        return w.head if outbound else w.inbound
    if r.type == "holds_stake_in" and share:
        return f"holds {share}" if outbound else f"{share} held by"
    words = type_words(r)
    if not outbound:
        return words[1]
    return words[0].removeprefix("is ").removeprefix("a ")


def inbound_words(r: Relationship, source: Node) -> str:
    """A link read from its `to` end, naming its `from` end: "gets its connectivity from
    Singtel", "28.3% held by Temasek", "lent to by DBS, 12.2% of its loans"."""
    if r.type == "exposed_to":
        w = _exposure_words(r)
        text = f"{w.inbound} {source.name}"
        return text + (f", {_share(r.weight)} of its {w.short}" if r.weight is not None else "")
    if r.type == "holds_stake_in" and r.weight is not None:
        return f"{_share(r.weight)} held by {source.name}"
    return f"{type_words(r)[1]} {source.name}"


def route_text(r: Relationship, source: str, reached: str, nodes: dict[str, Node]) -> str:
    """The one link by which news about `source` reaches `reached`, in plain words without
    `reached`, for an indirect impact. From the link's `from` end: "via its Hong Kong loans,
    12.2% of total", "provides connectivity to DBS"; from its `to` end: "gets its connectivity
    from Singtel"; into a region that covers the source: "via its Greater China loans, 13.5% of
    total, a figure for the whole region including Taiwan". `nodes` holds both ends and the
    source (relationships.load_nodes)."""
    if reached == r.to:
        return inbound_words(r, nodes[r.from_])
    to = nodes[r.to]
    loans = r.measure is None or r.measure in LOAN_MEASURES
    if r.type == "exposed_to" and r.weight is not None and loans:
        text = f"via its {_place(to)} loans, {_share(r.weight)} of total"
        if r.chart_reading:
            text += " (read from a chart)"
    else:
        text = link_words(r, to)
    if source != r.to:
        text += f", a figure for the whole region including {nodes[source].name}"
    return text


@dataclass(frozen=True)
class Tagged:
    """An entity named on a development: the development's direct level for it (0 Low to 2
    High), whether it is adverse for it, and the relationship types the development activates for
    it (the link_types stage); None when they were not asked (a topic, an entity at Low with
    INDIRECT_FROM_LOW off, one of a development adverse for none of its entities with
    INDIRECT_ADVERSE_ONLY on, or a run from before the stage), which passes nothing."""

    level: int
    adverse: bool
    types: frozenset[RelType] | None


class Setting(NamedTuple):
    """The rules' two switches: INDIRECT_ADVERSE_ONLY and INDIRECT_FROM_LOW."""

    adverse_only: bool
    from_low: bool


# Every setting, which the run counts rows under, and the one it stores.
SETTINGS: tuple[Setting, ...] = tuple(
    Setting(adverse_only, from_low) for adverse_only in (False, True) for from_low in (False, True)
)
STORED = Setting(INDIRECT_ADVERSE_ONLY, INDIRECT_FROM_LOW)


@dataclass(frozen=True)
class Reached:
    """One path by which a development reaches an entity it does not name, as development_impacts
    stores it: the named entity it comes from, the link, that link's strength from the source's
    end, the level, whether it is the entity's best path, and the path in words."""

    subject_id: str
    source_id: str
    relationship: Relationship
    region: str | None
    strength: Strength
    level: int
    best: bool
    reason: str


STRENGTHS: tuple[Strength, ...] = Strength.__args__  # strongest first


def indirect_level(direct: int, strength: Strength, *, from_low: bool) -> int | None:
    """INDIRECT_TABLE's level for a direct level and a strength; None passes nothing. A Low
    direct item passes only `from_low`."""
    if direct == 0 and not from_low:
        return None
    return INDIRECT_TABLE[direct][strength]


def passes_adverse_rule(adverse: bool, *, adverse_only: bool = INDIRECT_ADVERSE_ONLY) -> bool:
    """Whether the adverse rule lets a named entity pass: any entity with `adverse_only`
    (INDIRECT_ADVERSE_ONLY) off, else only one the development is adverse for. `reached` gates
    each entity by it, and the link_types stage asks a development only when one of the
    entities it would ask about passes it (developments.link_types_entities)."""
    return adverse or not adverse_only


def _rank(r: Reached) -> tuple[int, int, float, str, str]:
    """Sort key among paths to one entity, best first: the highest level, the stronger link, the
    larger share (none last), the link id, the source id."""
    return (
        -r.level,
        STRENGTHS.index(r.strength),
        -(r.relationship.weight or 0.0),
        r.relationship.id,
        r.source_id,
    )


@cache
def _names() -> dict[str, str]:
    return {e.id: e.name for e in load_subjects().entities}


@cache
def _entities() -> dict[str, Entity]:
    return {e.id: e for e in load_subjects().entities}


def hub_blocks(reach: Reach) -> bool:
    """Whether the hub rules (params.HUB_KINDS, decided 23 Nov 2026) stop a link from a named
    entity: the source is a hub, and either the link is of a MEMBER_TYPES type with the source at
    its `to` end or covered by the region there, so the source is what the entity reached
    operates in, is regulated by, is listed on or is a member of; or the entity reached has the
    source as its home."""
    entities = _entities()
    source = entities.get(reach.source)
    if source is None or source.kind not in HUB_KINDS:
        return False
    r = reach.relationship
    if r.type in MEMBER_TYPES and (r.to == reach.source or reach.region is not None):
        return True
    return entities[reach.reached].home == reach.source


def _reason(direct: int, reach: Reach, level: int) -> str:
    """A path in words: "High for Singtel by Strong through provides_connectivity_to gives High."
    then the link's rating reason for that direction."""
    source = _names().get(reach.source, reach.source)
    return (
        f"{PRIORITY_LABELS[direct]} for {source} by {reach.strength} through "
        f"{reach.relationship.type} gives {PRIORITY_LABELS[level]}. {reach.reason}"
    )


def reached(
    conn: sqlite3.Connection,
    tagged: dict[str, Tagged],
    *,
    from_low: bool = INDIRECT_FROM_LOW,
    adverse_only: bool = INDIRECT_ADVERSE_ONLY,
    links: dict[str, list[Reach]] | None = None,
) -> list[Reached]:
    """Every path by which one development reaches an entity it does not name, by entity, each
    entity's best path first and marked best. `tagged` holds every subject named on it, and none
    of them gets a row. A named entity passes a link only when the link's type is among its
    activated types, its direction from that entity is not Non-propagating, the table gives its
    level and strength a level (a Low level only `from_low`), with `adverse_only` the
    development is adverse for it, and the hub rules do not stop it (hub_blocks). Paths are
    never added together. `links` keeps each source's links (relationships.reaches) across calls
    on one store."""
    known = {} if links is None else links
    found: dict[str, list[Reached]] = {}
    for source, tag in tagged.items():
        if tag.types is None or not passes_adverse_rule(tag.adverse, adverse_only=adverse_only):
            continue
        if source not in known:
            known[source] = reaches(conn, source)
        for reach in known[source]:
            if reach.relationship.type not in tag.types or reach.reached in tagged:
                continue
            if hub_blocks(reach):
                continue
            level = indirect_level(tag.level, reach.strength, from_low=from_low)
            if level is None:
                continue
            found.setdefault(reach.reached, []).append(
                Reached(
                    subject_id=reach.reached,
                    source_id=source,
                    relationship=reach.relationship,
                    region=reach.region,
                    strength=reach.strength,
                    level=level,
                    best=False,
                    reason=_reason(tag.level, reach, level),
                )
            )
    paths: list[Reached] = []
    for subject in sorted(found):
        first, *rest = sorted(found[subject], key=_rank)
        paths += [replace(first, best=True), *rest]
    return paths


@dataclass
class ImpactStats:
    """What store_impacts found in a run: every path under each setting, with its development's
    id; how many named entities at Low have their types asked; how many developments with types
    the adverse rule would not have asked; and how many entities at Medium or High have their
    types and have none asked, each a development and entity pair. The run stores the paths of
    STORED. The link_types stage asks entities at Low only with INDIRECT_FROM_LOW on, so with
    none at Low asked the Low row's paths equal those without it by construction, and its effect
    is not measured. It asks a development adverse for none of its entities only with
    INDIRECT_ADVERSE_ONLY off, so with none such asked the paths with the adverse rule off lack
    those developments, and its effect is not measured either. A run whose entities were never
    asked (one from before the stage) has none with types and finds no path."""

    paths: dict[Setting, list[tuple[str, Reached]]]
    low_asked: int = 0
    calm_asked: int = 0  # developments with types, adverse for none of the entities with them
    typed: int = 0  # entities at Medium or High with their types, [] included
    unasked: int = 0  # entities at Medium or High whose types were not asked

    @property
    def stored(self) -> list[tuple[str, Reached]]:
        return self.paths[STORED]


def _tags(conn: sqlite3.Connection, run_id: str) -> dict[str, dict[str, Tagged]]:
    """Each development's named subjects in the run, with their level, adverse flag and
    activated types."""
    tags: dict[str, dict[str, Tagged]] = {}
    for r in conn.execute(
        "SELECT development_id, subject_id, priority_level, adverse, link_types "
        "FROM development_subjects WHERE run_id = ? ORDER BY development_id, subject_id",
        (run_id,),
    ):
        types = None if r["link_types"] is None else frozenset[RelType](json.loads(r["link_types"]))
        tags.setdefault(r["development_id"], {})[r["subject_id"]] = Tagged(
            level=int(r["priority_level"]), adverse=bool(r["adverse"]), types=types
        )
    return tags


def store_impacts(
    conn: sqlite3.Connection, run_id: str, *, recomputed_at: str | None = None
) -> ImpactStats:
    """Replace the run's development_impacts with every path under STORED, in one transaction,
    and find every path under each setting. The same transaction sets the run's
    impacts_recomputed mark to `recomputed_at`: a time from `im impacts`, None from the run's own
    impact step."""
    known: dict[str, list[Reach]] = {}
    stats = ImpactStats(paths={s: [] for s in SETTINGS})
    entities = _names()
    for dev_id, tagged in _tags(conn, run_id).items():
        stats.low_asked += sum(t.level == 0 and t.types is not None for t in tagged.values())
        asked = [t for t in tagged.values() if t.types is not None]
        stats.calm_asked += bool(asked) and not any(
            passes_adverse_rule(t.adverse, adverse_only=True) for t in asked
        )
        material = [t for sid, t in tagged.items() if sid in entities and t.level >= MATERIAL_LEVEL]
        stats.typed += sum(t.types is not None for t in material)
        stats.unasked += sum(t.types is None for t in material)
        for s in SETTINGS:
            found = reached(
                conn, tagged, from_low=s.from_low, adverse_only=s.adverse_only, links=known
            )
            stats.paths[s] += [(dev_id, r) for r in found]
    with conn:
        conn.execute("DELETE FROM development_impacts WHERE run_id = ?", (run_id,))
        conn.executemany(
            "INSERT INTO development_impacts (run_id, development_id, subject_id, source_id, "
            "relationship_id, region, strength, level, best, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    run_id,
                    dev_id,
                    r.subject_id,
                    r.source_id,
                    r.relationship.id,
                    r.region,
                    r.strength,
                    r.level,
                    r.best,
                    r.reason,
                )
                for dev_id, r in stats.stored
            ],
        )
        conn.execute("UPDATE runs SET impacts_recomputed = ? WHERE id = ?", (recomputed_at, run_id))
    return stats
