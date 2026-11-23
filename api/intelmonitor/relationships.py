"""The relationship store: standing links between entities, for indirect impact.

api/seed/relationships.yaml (built by scripts/build_relationships.py) holds every relationship;
each joins two entities of api/seed/subjects.yaml, or an entity and a seed group.
sync_relationships() copies it into the store at the start of a pipeline run; reaches() reads it
back for the impact step, links_from() and links_into() for the briefs, and links_of() for the
Related graph.

A link is of one of the team's 22 types in three groups (LINK_TYPES), and a pair of entities may
hold several links of different types. A link reads from its `from` to its `to` (DBS exposed_to
Hong Kong, BCS processes_transactions_for FAST, NETS holds_stake_in BCS). The SYMMETRIC types are
stored once per pair, ids in alphabetical order. A link into an entity of kind region (South and
Southeast Asia) covers the region's member countries.
belongs_to_sector links an entity to a seed group (its parent category), so seed group ids are
valid link ends; it carries no impact and the Related graph leaves it out.

Every link carries `transmission`, its strength in each direction and the prompt version that
rated it, rated once by scripts/rate_relationships.py into api/seed/collected/transmission.yaml and
merged in by the build; belongs_to_sector is rated too, as the deck asks, though it carries
nothing. Pipeline runs read the ratings and call no model for them.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from intelmonitor.config import SEED_DIR
from intelmonitor.seed import EntityKind, load_subjects

RELATIONSHIPS_PATH = SEED_DIR / "relationships.yaml"
TRANSMISSION_PATH = SEED_DIR / "collected" / "transmission.yaml"

# The team's 22 types (deck of 16 Nov 2026, pages 1 to 4), in the deck's order; LINK_TYPES gives
# each its group, meaning and example.
RelType = Literal[
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
    "belongs_to_sector",
]
REL_TYPES: tuple[RelType, ...] = RelType.__args__
LinkGroup = Literal[
    "Ownership and financial", "Operational and commercial", "Structural and institutional"
]


@dataclass(frozen=True)
class LinkType:
    """A link type as the deck defines it: its group, its meaning and the deck's example."""

    group: LinkGroup
    meaning: str
    example: str


# The deck's table, word for word, which the prompts read. A link reads from `from` to `to`:
# provider to customer (provides_*, processes_transactions_for, material_supplier_to), customer to
# provider (material_customer_of), lender, investor, funder or insurer to the other party,
# distributor to product owner, holder of the data to the entity it goes to.
LINK_TYPES: dict[RelType, LinkType] = {
    "holds_stake_in": LinkType(
        "Ownership and financial", "Equity ownership", "Temasek holds a material stake in DBS."
    ),
    "lends_to": LinkType(
        "Ownership and financial",
        "Material lending or credit exposure",
        "Bank A provides significant loans to Property Developer B.",
    ),
    "invests_in": LinkType(
        "Ownership and financial",
        "Material investment other than lending",
        "Fund A holds bonds issued by Company B.",
    ),
    "funds": LinkType(
        "Ownership and financial",
        "Material funding or liquidity provision",
        "Parent A provides significant funding to Subsidiary B.",
    ),
    "insures": LinkType(
        "Ownership and financial",
        "Material insurance coverage",
        "Insurer A provides business-interruption insurance to Company B.",
    ),
    "provides_technology_to": LinkType(
        "Operational and commercial",
        "Cloud, software or technology infrastructure",
        "AWS provides production cloud services to Bank A.",
    ),
    "provides_connectivity_to": LinkType(
        "Operational and commercial",
        "Telecom or network connectivity",
        "Singtel provides enterprise connectivity to DBS.",
    ),
    "provides_business_service_to": LinkType(
        "Operational and commercial",
        "Material outsourced or professional service not covered elsewhere",
        "An engineering provider performs material aircraft-engine maintenance for an airline.",
    ),
    "processes_transactions_for": LinkType(
        "Operational and commercial",
        "Payment, clearing or transaction processing",
        "Processor A processes card transactions for Bank B.",
    ),
    "shares_or_processes_data_with": LinkType(
        "Operational and commercial",
        "Receives, shares or processes relevant data",
        "MoneySmart collects and transmits bank-product application data.",
    ),
    "distributes_products_for": LinkType(
        "Operational and commercial",
        "Distributes, compares or refers customers to another entity's products",
        "MoneySmart refers customers to Bank A's cards.",
    ),
    "provides_customer_benefits_with": LinkType(
        "Operational and commercial",
        "Joint customer benefits, loyalty or redemption arrangements",
        "Bank cardholders earn or redeem KrisFlyer miles with SIA.",
    ),
    "jointly_offers_product_with": LinkType(
        "Operational and commercial",
        "Entities jointly operate, brand or provide a product",
        "A bank and fintech jointly offer a digital-payment product.",
    ),
    "material_customer_of": LinkType(
        "Operational and commercial",
        "Customer is materially important to the provider",
        "Company A represents a significant share of Vendor B's revenue.",
    ),
    "material_supplier_to": LinkType(
        "Operational and commercial",
        "Material supply of goods, equipment or non-technology inputs",
        "An engine manufacturer supplies the engine model used across a material share of an "
        "airline's fleet.",
    ),
    "regulated_by": LinkType(
        "Structural and institutional",
        "Licensed, supervised or regulated by",
        "Bank A is regulated by MAS.",
    ),
    "operates_in": LinkType(
        "Structural and institutional",
        "Material operations in a jurisdiction",
        "Bank A has significant operations in India.",
    ),
    "exposed_to": LinkType(
        "Structural and institutional",
        "Material economic, country or market exposure",
        "Bank A has significant credit exposure to China.",
    ),
    "member_of": LinkType(
        "Structural and institutional",
        "Formal membership of an organisation or network",
        "Bank A is a member of ABS.",
    ),
    "participates_in": LinkType(
        "Structural and institutional",
        "Participation in a scheme, programme or initiative",
        "Bank A participates in Project Guardian.",
    ),
    "listed_on": LinkType(
        "Structural and institutional",
        "Securities listed on an exchange",
        "Company A is listed on SGX.",
    ),
    "belongs_to_sector": LinkType(
        "Structural and institutional",
        "Membership of a monitored aggregate sector",
        "DBS belongs to the Singapore Banking Sector.",
    ),
}
assert tuple(LINK_TYPES) == REL_TYPES, "LINK_TYPES must list the 22 types in RelType's order"
# Types with no direction: stored once per pair, ids in alphabetical order.
SYMMETRIC: tuple[RelType, ...] = ("provides_customer_benefits_with", "jointly_offers_product_with")
Evidence = Literal["public", "reported", "assumed"]
EVIDENCE_GRADES: tuple[Evidence, ...] = Evidence.__args__  # strongest first
# The headline figure of an exposure; further figures add NPLs and ratios. A bank's exposure is
# measured in loans (the first three); any other entity's in what its share is of: stablecoin
# reserves, its reserve fund's repo book, revenue, distribution costs, token supply by chain or
# customer assets, or, with no share, stablecoins held and card volume.
LoanMeasure = Literal["gross_customer_loans", "loans_by_currency", "rwa"]
LOAN_MEASURES: tuple[LoanMeasure, ...] = LoanMeasure.__args__
HeadlineMeasure = Literal[
    LoanMeasure,
    "reserve_assets",
    "repo_principal",
    "revenue",
    "distribution_costs",
    "token_supply",
    "assets_on_platform",
    "stablecoin_holdings",
    "card_volume",
]
FigureMeasure = Literal[HeadlineMeasure, "npl", "npl_ratio", "npa_coverage"]
Dimension = Literal[
    "geography",
    "industry",
    "currency",
    "specific",
    "ccyb_country",
    "reserve_composition",
    "revenue",
    "counterparty",
]
MoneyUnit = Literal["SGD m", "SGD b", "USD m"]
HeadlineUnit = Literal[MoneyUnit, "m tokens"]
FigureUnit = Literal[HeadlineUnit, "%", "% of loans", "% of gross loans"]
Service = Literal[
    "banking",
    "blockchain",
    "cloud",
    "core_banking",
    "custody",
    "data_centre",
    "distribution",
    "market_infrastructure",
    "payments",
    "print_mail",
    "reserve_management",
    "security",
    "telco",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Figure(_Model):
    """A further figure for the same bank and target as its exposure relationship."""

    measure: FigureMeasure
    dimension: Dimension
    segment: str
    value: float
    unit: FigureUnit
    share: float | None = None  # share of the total in the same source table
    period: date
    chart_reading: bool = False
    source_url: str
    source_title: str
    page: int | None = None
    quote: str
    note: str | None = None


# The deck's four values (16 Nov 2026, page 6), strongest first: a Non-propagating direction
# passes nothing.
Strength = Literal["Strong", "Moderate", "Weak", "Non-propagating"]


class Rating(_Model):
    """How strongly a material adverse event at one end reaches the other, and why."""

    strength: Strength
    reason: str


class Transmission(_Model):
    """A link's strength in each direction (ticket 18): from_to is how strongly an event at its
    `from` reaches its `to`, to_from the reverse (news about Hong Kong reaching DBS).
    `prompt_version` is the id of the link rating prompt version that rated it (the `prompts`
    of transmission.yaml); null for a hand entry."""

    from_to: Rating
    to_from: Rating
    prompt_version: str | None = None


class RatingEntry(_Model):
    """One link's ratings as transmission.yaml holds them. rate_relationships.py writes `model`
    entries and rates a link again only when input_hash changes; it never touches a `hand`
    entry, which is how the team corrects a rating."""

    id: str
    from_to: Rating
    to_from: Rating
    model: str | None = None  # the model that answered; null for a hand entry
    date: date
    # of the prompt version and the link's user prompt; null for a hand entry
    input_hash: str | None = None
    prompt_version: str | None = None  # the id of the version that rated it; null for a hand entry
    rated_by: Literal["model", "hand"]


class PromptVersion(_Model):
    """A version of the link rating prompt, as transmission.yaml lists it: its id (the first 12
    hex characters of the SHA-256 of the stage, template, schema, model and effort, as
    pipeline/prompts.py makes it), its number counting up from 1, the date it was first used, a
    note, its model and effort, and its template's text."""

    id: str
    number: int
    date: date
    note: str
    model: str
    effort: str
    text: str


class RatingsFile(_Model):
    prompts: list[PromptVersion] = []
    ratings: list[RatingEntry]


def load_ratings_file(path: Path = TRANSMISSION_PATH) -> RatingsFile:
    """transmission.yaml; empty when the file does not exist yet."""
    if not path.exists():
        return RatingsFile(ratings=[])
    return RatingsFile.model_validate(yaml.safe_load(path.read_text()))


def load_ratings(path: Path = TRANSMISSION_PATH) -> dict[str, RatingEntry]:
    """transmission.yaml's ratings by link id; empty when the file does not exist yet."""
    return {e.id: e for e in load_ratings_file(path).ratings}


class Relationship(_Model):
    """One link, as relationships.yaml holds it. Keys at their default are left out of the YAML."""

    model_config = ConfigDict(validate_by_name=True)

    id: str  # "from:type:to"
    from_: str = Field(validation_alias="from", serialization_alias="from")
    to: str
    type: RelType
    # exposed_to: the headline figure's share of its total (a bank's gross customer loans, an
    # issuer's reserves, a firm's revenue); holds_stake_in: the stake
    weight: float | None = None
    measure: HeadlineMeasure | None = None
    value: float | None = None
    unit: HeadlineUnit | None = None
    evidence: Evidence
    chart_reading: bool = False
    as_of: date | None = None
    last_confirmed: date
    source_url: str | None = None
    source_title: str | None = None
    page: int | None = None
    quote: str | None = None
    detail: str
    # One line built by the build script from the stored facts: the plain-words phrase with the
    # share and its date, else `detail`; a controlling stake with no share says so.
    reason: str
    # holds_stake_in with no published share where the holder controls the held (a former owns or
    # subsidiary_of row), from the collected row's flag; `owns` reads it.
    controlling: bool = False
    service: Service | None = None
    figures: list[Figure] = []
    # Strength in each direction and the prompt version that rated it, from transmission.yaml;
    # every link has one.
    transmission: Transmission | None = None


class RelationshipsFile(_Model):
    relationships: list[Relationship]


def problems(data: RelationshipsFile, entity_ids: set[str], group_ids: set[str]) -> list[str]:
    """Why the file does not fit the subject seed: links with an end that is not an entity, other
    than a belongs_to_sector link to a seed group. Empty when it fits."""
    found: list[str] = []
    for r in data.relationships:
        ends = entity_ids | group_ids if r.type == "belongs_to_sector" else entity_ids
        if r.from_ not in entity_ids:
            found.append(f"{r.id}: {r.from_} is not an entity")
        if r.to not in ends:
            found.append(
                f"{r.id}: {r.to} is not an entity" + (" or group" if ends != entity_ids else "")
            )
    return found


def load_relationships(path: Path = RELATIONSHIPS_PATH) -> RelationshipsFile:
    """Read relationships.yaml and check it against the entities and groups in subjects.yaml,
    and that every link has its strengths. Raises ValueError naming every link end that
    is not in the seed (the fix is to rerun scripts/build_relationships.py) or every unrated link
    (rerun scripts/rate_relationships.py, then the build)."""
    data = RelationshipsFile.model_validate(yaml.safe_load(path.read_text()))
    seed = load_subjects()
    found = problems(data, {e.id for e in seed.entities}, {g.id for g in seed.groups})
    if found:
        raise ValueError(
            f"{path.name} does not fit the subject seed (rerun scripts/build_relationships.py): "
            + "; ".join(found)
        )
    unrated = [r.id for r in data.relationships if r.transmission is None]
    if unrated:
        raise ValueError(
            f"{path.name} has {len(unrated)} links with no strength rating (run "
            "scripts/rate_relationships.py, then scripts/build_relationships.py): "
            + "; ".join(unrated)
        )
    return data


# The store's copy of relationships.yaml, replaced whole by sync_relationships(). `data` holds
# the full relationship (figures, source, quote) as JSON; the other columns are for queries.
SCHEMA = """
CREATE TABLE IF NOT EXISTS relationships (
    id TEXT PRIMARY KEY,
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    type TEXT NOT NULL,
    weight REAL,
    evidence TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS relationships_from ON relationships (from_id);
CREATE INDEX IF NOT EXISTS relationships_to ON relationships (to_id);
"""


def sync_relationships(
    conn: sqlite3.Connection, path: Path = RELATIONSHIPS_PATH
) -> RelationshipsFile:
    """Replace the store's relationships with relationships.yaml, in one transaction. Raises
    before touching the store if the file does not fit the subject seed. Commits any transaction
    the caller has open, so call it before a run's writes."""
    data = load_relationships(path)
    store_relationships(conn, data)
    return data


def store_relationships(conn: sqlite3.Connection, data: RelationshipsFile) -> None:
    """Replace the store's relationships with `data`, unchecked, in one transaction;
    sync_relationships checks the file first, the impact cases pass made-up links."""
    conn.executescript(SCHEMA)
    with conn:
        insert_relationships(conn, data)


def insert_relationships(conn: sqlite3.Connection, data: RelationshipsFile) -> None:
    """Replace the rows of the relationships table with `data`, inside the caller's transaction."""
    conn.execute("DELETE FROM relationships")
    conn.executemany(
        "INSERT INTO relationships (id, from_id, to_id, type, weight, evidence, data) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (r.id, r.from_, r.to, r.type, r.weight, r.evidence, r.model_dump_json(by_alias=True))
            for r in data.relationships
        ],
    )


class Node(_Model):
    """A link end named for the route a reader sees: an entity, or a seed group (the far end of
    belongs_to_sector)."""

    id: str
    name: str
    kind: EntityKind | Literal["group"]


def load_nodes(ids: set[str]) -> dict[str, Node]:
    """Each id, an entity or a seed group, as a named node from the seed."""
    seed = load_subjects()
    nodes = {g.id: Node(id=g.id, name=g.name, kind="group") for g in seed.groups if g.id in ids}
    nodes |= {e.id: Node(id=e.id, name=e.name, kind=e.kind) for e in seed.entities if e.id in ids}
    return nodes


def _regions(target_id: str) -> list[str]:
    """The region entities whose members include the target, by id."""
    return sorted(e.id for e in load_subjects().entities if target_id in e.members)


# A published share at least this large counts as owning the held company (the usual line for
# significant influence); a smaller one is a passive holding.
OWNING_STAKE = 0.2


def owns(r: Relationship) -> bool:
    """Whether a holds_stake_in link counts as ownership: a share of at least OWNING_STAKE, or,
    with no published share, a controlling stake."""
    if r.type != "holds_stake_in":
        return False
    if r.weight is None:
        return r.controlling
    return r.weight >= OWNING_STAKE


def links_from(
    conn: sqlite3.Connection, subject_id: str, types: tuple[RelType, ...]
) -> list[tuple[Relationship, Node]]:
    """The subject's own links of the given types, each with the node it leads to, by id."""
    marks = ",".join("?" * len(types))
    rows = conn.execute(
        f"SELECT data FROM relationships WHERE from_id = ? AND type IN ({marks}) ORDER BY id",
        (subject_id, *types),
    ).fetchall()
    rels = [Relationship.model_validate_json(r["data"]) for r in rows]
    if not rels:
        return []
    nodes = load_nodes({r.to for r in rels})
    return [(r, nodes[r.to]) for r in rels]


def links_into(
    conn: sqlite3.Connection, subject_id: str, types: tuple[RelType, ...]
) -> list[tuple[Relationship, Node]]:
    """The links of the given types that end at the subject, each with the node it comes from,
    by id."""
    marks = ",".join("?" * len(types))
    rows = conn.execute(
        f"SELECT data FROM relationships WHERE to_id = ? AND type IN ({marks}) ORDER BY id",
        (subject_id, *types),
    ).fetchall()
    rels = [Relationship.model_validate_json(r["data"]) for r in rows]
    if not rels:
        return []
    nodes = load_nodes({r.from_ for r in rels})
    return [(r, nodes[r.from_]) for r in rels]


def links_of(conn: sqlite3.Connection, subject_id: str) -> list[tuple[Relationship, Node]]:
    """Every link from or to the subject but belongs_to_sector, each with the node at its other
    end, by id."""
    rows = conn.execute(
        "SELECT data FROM relationships WHERE (from_id = ? OR to_id = ?) "
        "AND type != 'belongs_to_sector' ORDER BY id",
        (subject_id, subject_id),
    ).fetchall()
    rels = [Relationship.model_validate_json(r["data"]) for r in rows]
    if not rels:
        return []
    other = {r.id: r.to if r.from_ == subject_id else r.from_ for r in rels}
    nodes = load_nodes(set(other.values()))
    return [(r, nodes[other[r.id]]) for r in rels]


class Reach(_Model):
    """One link by which an event at `source` reaches the seed entity `reached`, with the link's
    strength in that direction and its rating's reason. `region` names the region entity when the
    link goes into a region that covers the source (UOB's Greater China loans, for news about
    Taiwan)."""

    source: str
    reached: str
    relationship: Relationship
    strength: Strength
    reason: str
    region: str | None = None


def reaches(conn: sqlite3.Connection, source_id: str) -> list[Reach]:
    """Every seed entity one link from the entity `source_id`, for inferred impact (ticket 20):
    each link with the source at one end, read from_to when the source is its `from` and to_from
    when it is its `to`; and each link into a region entity whose members (its `members` in the
    seed) include the source, read to_from only. belongs_to_sector, unrated links (only made-up
    links in the impact cases), groups and the source itself carry or get nothing, and nothing
    goes a second hop. By link id."""
    entities = {e.id for e in load_subjects().entities}
    if source_id not in entities:
        return []
    ends = [source_id, *_regions(source_id)]
    marks = ",".join("?" * len(ends))
    rows = conn.execute(
        "SELECT data FROM relationships WHERE type != 'belongs_to_sector' "
        f"AND (from_id = ? OR to_id IN ({marks})) ORDER BY id",
        (source_id, *ends),
    ).fetchall()
    found: list[Reach] = []
    for row in rows:
        r = Relationship.model_validate_json(row["data"])
        if r.transmission is None:
            continue
        if r.from_ == source_id:
            reached, rating, region = r.to, r.transmission.from_to, None
        elif r.to == source_id:
            reached, rating, region = r.from_, r.transmission.to_from, None
        else:  # into a region that covers the source
            reached, rating, region = r.from_, r.transmission.to_from, r.to
        if reached != source_id and reached in entities:
            found.append(
                Reach(
                    source=source_id,
                    reached=reached,
                    relationship=r,
                    strength=rating.strength,
                    reason=rating.reason,
                    region=region,
                )
            )
    return found
