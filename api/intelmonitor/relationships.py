"""The relationship store: standing links between subjects and endpoints, for indirect impact.

api/seed/relationships.yaml (built by scripts/build_relationships.py) holds every relationship
and an endpoint entry for each id that is not an entity subject or seed group.
sync_relationships() copies it into the store at the start of a pipeline run; reaches() reads it
back for the impact step, shared() for GET /shared, linked_paths() for a place's linked
institutions, links_from() for the briefs, and links_of() for the Related graph.

A link reads from its `from` to its `to` (DBS exposed_to Hong Kong, FAST depends_on BCS, NETS
holds_stake_in BCS). partner_of is stored once per pair, ids in alphabetical order.
belongs_to_sector links an entity to a seed group (its parent category), so seed group ids are
valid link ends; it carries no impact and the Related and Shared sections leave it out.

Every other link carries `transmission`, its strength in each direction, rated once by
scripts/rate_relationships.py into api/seed/collected/transmission.yaml and merged in by the
build. Pipeline runs read the ratings and call no model for them.
"""

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from intelmonitor.config import SEED_DIR
from intelmonitor.seed import EntityKind, load_subjects

RELATIONSHIPS_PATH = SEED_DIR / "relationships.yaml"
TRANSMISSION_PATH = SEED_DIR / "collected" / "transmission.yaml"

# The team's 10 one-way types (deck of 10 Nov 2026, slide 4; ticket 17).
RelType = Literal[
    "holds_stake_in",
    "regulated_by",
    "operates_in",
    "exposed_to",
    "depends_on",
    "member_of",
    "participates_in",
    "partner_of",
    "listed_on",
    "belongs_to_sector",
]
REL_TYPES: tuple[RelType, ...] = RelType.__args__
Evidence = Literal["public", "reported", "assumed"]
EVIDENCE_GRADES: tuple[Evidence, ...] = Evidence.__args__  # strongest first
EndpointKind = Literal[
    "bank",
    "insurer",
    "broker",
    "investor",
    "person",
    "association",
    "central_bank",
    "regulator",
    "market_infrastructure",
    "payment_system",
    "payment_institution",
    "tech_provider",
    "stablecoin_issuer",
    "digital_asset_firm",
    "blockchain",
    "country",
    "region",
    "sector",
    "group",
]
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


Strength = Literal["Strong", "Moderate", "Weak"]


class Rating(_Model):
    """How strongly a material adverse event at one end reaches the other, and why."""

    strength: Strength
    reason: str


class Transmission(_Model):
    """A link's strength in each direction (ticket 18): from_to is how strongly an event at its
    `from` reaches its `to`, to_from the reverse (news about Hong Kong reaching DBS)."""

    from_to: Rating
    to_from: Rating


class RatingEntry(_Model):
    """One link's ratings as transmission.yaml holds them. rate_relationships.py writes `model`
    entries and rates a link again only when input_hash changes; it never touches a `hand`
    entry, which is how the team corrects a rating."""

    id: str
    from_to: Rating
    to_from: Rating
    model: str | None = None  # the model that answered; null for a hand entry
    date: date
    input_hash: str | None = None  # of the model and both prompts; null for a hand entry
    rated_by: Literal["model", "hand"]


class RatingsFile(_Model):
    ratings: list[RatingEntry]


def load_ratings(path: Path = TRANSMISSION_PATH) -> dict[str, RatingEntry]:
    """transmission.yaml by link id; empty when the file does not exist yet."""
    if not path.exists():
        return {}
    return {e.id: e for e in RatingsFile.model_validate(yaml.safe_load(path.read_text())).ratings}


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
    # Strength in each direction, from transmission.yaml; every rateable link has one.
    transmission: Transmission | None = None


def rateable(r: Relationship) -> bool:
    """Whether a link is rated: every type but belongs_to_sector, which carries no impact."""
    return r.type != "belongs_to_sector"


class Endpoint(_Model):
    """A relationship end that is not an entity subject; regions list the places they cover."""

    id: str
    name: str
    kind: EndpointKind
    members: list[str] = []
    detail: str | None = None


class RelationshipsFile(_Model):
    relationships: list[Relationship]
    endpoints: list[Endpoint]


def problems(data: RelationshipsFile, subject_ids: set[str]) -> list[str]:
    """Why the file does not fit the subject seed: ids that are neither an entity subject, a seed
    group nor an endpoint, and endpoint entries for ids that are now subjects or groups. Empty
    when it fits. `subject_ids` holds the entity and group ids."""
    endpoint_ids = {e.id for e in data.endpoints}
    used = {r.from_ for r in data.relationships} | {r.to for r in data.relationships}
    used |= {m for e in data.endpoints for m in e.members}
    unknown = sorted(used - subject_ids - endpoint_ids)
    both = sorted(endpoint_ids & subject_ids)
    return [f"neither a subject nor an endpoint: {i}" for i in unknown] + [
        f"both a subject and an endpoint: {i}" for i in both
    ]


def link_ends() -> set[str]:
    """Seed ids a link may end at without an endpoint entry: entities and groups."""
    seed = load_subjects()
    return {e.id for e in seed.entities} | {g.id for g in seed.groups}


def load_relationships(path: Path = RELATIONSHIPS_PATH) -> RelationshipsFile:
    """Read relationships.yaml and check it against the entities and groups in subjects.yaml,
    and that every rateable link has its strengths. Raises ValueError naming every unknown id
    (the fix is to rerun scripts/build_relationships.py) or every unrated link (rerun
    scripts/rate_relationships.py, then the build)."""
    data = RelationshipsFile.model_validate(yaml.safe_load(path.read_text()))
    found = problems(data, link_ends())
    if found:
        raise ValueError(
            f"{path.name} does not fit the subject seed (rerun scripts/build_relationships.py): "
            + "; ".join(found)
        )
    unrated = [r.id for r in data.relationships if rateable(r) and r.transmission is None]
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
CREATE TABLE IF NOT EXISTS relationship_endpoints (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    members TEXT NOT NULL,  -- JSON list; regions only
    detail TEXT
);
"""


def sync_relationships(
    conn: sqlite3.Connection, path: Path = RELATIONSHIPS_PATH
) -> RelationshipsFile:
    """Replace the store's relationships and endpoints with relationships.yaml, in one
    transaction. Raises before touching the store if the file does not fit the subject seed.
    Commits any transaction the caller has open, so call it before a run's writes."""
    data = load_relationships(path)
    store_relationships(conn, data)
    return data


def store_relationships(conn: sqlite3.Connection, data: RelationshipsFile) -> None:
    """Replace the store's relationships and endpoints with `data`, unchecked, in one
    transaction; sync_relationships checks the file first, the impact cases pass made-up links."""
    conn.executescript(SCHEMA)
    with conn:
        conn.execute("DELETE FROM relationships")
        conn.execute("DELETE FROM relationship_endpoints")
        conn.executemany(
            "INSERT INTO relationships (id, from_id, to_id, type, weight, evidence, data) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    r.id,
                    r.from_,
                    r.to,
                    r.type,
                    r.weight,
                    r.evidence,
                    r.model_dump_json(by_alias=True),
                )
                for r in data.relationships
            ],
        )
        conn.executemany(
            "INSERT INTO relationship_endpoints (id, name, kind, members, detail) "
            "VALUES (?, ?, ?, ?, ?)",
            [(e.id, e.name, e.kind, json.dumps(e.members), e.detail) for e in data.endpoints],
        )


class Node(_Model):
    """A subject or endpoint on a path, named for the route a reader sees."""

    id: str
    name: str
    kind: EntityKind | EndpointKind
    subject: bool  # a tracked entity subject; otherwise an endpoint


class ImpactPath(_Model):
    """How `subject` links to `target` on the target's page (linked_paths): one relationship, or
    two through `nodes[1]`.
    `relationships` and `nodes` run from the subject outward, so relationships[i] links nodes[i]
    to nodes[i + 1]. When the last node is a region that covers the target (UOB's Greater China
    loans for Taiwan news), `region` names it and the path is region-level."""

    subject: str
    target: str
    relationships: list[Relationship]
    nodes: list[Node]
    region: str | None = None
    weight: float | None = None  # the exposed_to link's share, if the path has one
    evidence: Evidence  # the weakest grade along the path
    chart_reading: bool = False

    @property
    def relationship_ids(self) -> list[str]:
        return [r.id for r in self.relationships]


def load_nodes(conn: sqlite3.Connection, ids: set[str]) -> dict[str, Node]:
    """Each id as a named node: endpoints from the store, entities and groups from the seed."""
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, name, kind FROM relationship_endpoints WHERE id IN ({marks})", sorted(ids)
    ).fetchall()
    nodes = {
        str(r["id"]): Node(id=r["id"], name=r["name"], kind=r["kind"], subject=False) for r in rows
    }
    seed = load_subjects()
    entities = {e.id: e for e in seed.entities}
    groups = {g.id: g for g in seed.groups}
    for i in ids - nodes.keys():
        # sync checked every id is an entity, a seed group or an endpoint
        if i in groups:
            nodes[i] = Node(id=i, name=groups[i].name, kind="group", subject=False)
        else:
            e = entities[i]
            nodes[i] = Node(id=i, name=e.name, kind=e.kind, subject=True)
    return nodes


def _regions(conn: sqlite3.Connection, target_id: str) -> list[str]:
    """The region endpoints and region entities whose members include the target."""
    rows = conn.execute(
        "SELECT e.id FROM relationship_endpoints e, json_each(e.members) m "
        "WHERE e.kind = 'region' AND m.value = ? ORDER BY e.id",
        (target_id,),
    ).fetchall()
    entities = [e.id for e in load_subjects().entities if target_id in e.members]
    return sorted([str(r["id"]) for r in rows] + entities)


def _path(target_id: str, rels: list[Relationship], nodes: dict[str, Node]) -> ImpactPath:
    end = rels[-1].to
    exposure = next((r for r in rels if r.type == "exposed_to"), None)
    return ImpactPath(
        subject=rels[0].from_,
        target=target_id,
        relationships=rels,
        nodes=[nodes[rels[0].from_], *(nodes[r.to] for r in rels)],
        region=None if end == target_id else end,
        weight=exposure.weight if exposure else None,
        evidence=EVIDENCE_GRADES[max(EVIDENCE_GRADES.index(r.evidence) for r in rels)],
        chart_reading=any(r.chart_reading for r in rels),
    )


# How an institution links to a place, sector, supplier or payment system on that subject's page:
# one of LINKED_FIRST into it, or holding a stake in a company that has one of LINKED_SECOND into
# it.
LINKED_FIRST: tuple[RelType, ...] = (
    "exposed_to",
    "depends_on",
    "participates_in",
    "holds_stake_in",
    "operates_in",
)
LINKED_SECOND: tuple[RelType, ...] = ("operates_in", "exposed_to", "depends_on", "participates_in")

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


def linked_paths(conn: sqlite3.Connection, target_id: str) -> list[ImpactPath]:
    """Every node linked to `target_id` (or to a region that covers it) by one LINKED_FIRST link,
    or by owning (see `owns`) a company with a LINKED_SECOND link into it, for the target's page.
    A holds_stake_in link counts only when it is ownership. The first node may be an endpoint,
    so untracked institutions count; shareholders that only hold a stake in a
    bank (Singtel in GXS Bank) are left to the caller to drop."""
    ends = [target_id, *_regions(conn, target_id)]
    end_marks = ",".join("?" * len(ends))
    first_marks = ",".join("?" * len(LINKED_FIRST))
    second_marks = ",".join("?" * len(LINKED_SECOND))
    one = conn.execute(
        f"SELECT data FROM relationships WHERE to_id IN ({end_marks}) "
        f"AND type IN ({first_marks}) AND from_id != ?",
        [*ends, *LINKED_FIRST, target_id],
    ).fetchall()
    two = conn.execute(
        "SELECT r1.data AS first, r2.data AS second FROM relationships r1 "
        "JOIN relationships r2 ON r2.from_id = r1.to_id "
        f"WHERE r1.type = 'holds_stake_in' AND r2.type IN ({second_marks}) "
        f"AND r2.to_id IN ({end_marks}) "
        "AND r1.from_id != ?",
        [*LINKED_SECOND, *ends, target_id],
    ).fetchall()
    chains = [[Relationship.model_validate_json(r["data"])] for r in one] + [
        [
            Relationship.model_validate_json(r["first"]),
            Relationship.model_validate_json(r["second"]),
        ]
        for r in two
    ]
    chains = [c for c in chains if c[0].type != "holds_stake_in" or owns(c[0])]
    if not chains:
        return []
    nodes = load_nodes(conn, {c[0].from_ for c in chains} | {r.to for c in chains for r in c})
    return [_path(target_id, c, nodes) for c in chains]


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
    nodes = load_nodes(conn, {r.to for r in rels})
    return [(r, nodes[r.to]) for r in rels]


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
    nodes = load_nodes(conn, set(other.values()))
    return [(r, nodes[other[r.id]]) for r in rels]


class Reach(_Model):
    """One link by which an event at `source` reaches the seed entity `reached`, with the link's
    strength in that direction. `region` names the region (an endpoint, or an entity of kind
    region) when the link goes into a region that covers the source (UOB's Greater China loans,
    for news about Taiwan)."""

    source: str
    reached: str
    relationship: Relationship
    strength: Strength
    region: str | None = None


def reaches(conn: sqlite3.Connection, source_id: str) -> list[Reach]:
    """Every seed entity one link from the entity `source_id`, for indirect impact (ticket 20):
    each link with the source at one end, read from_to when the source is its `from` and to_from
    when it is its `to`; and each link into a region endpoint or region entity whose members
    include the source, read to_from only. belongs_to_sector, unrated links, endpoints, groups
    and the source itself carry or get nothing, and nothing goes a second hop. By link id."""
    entities = {e.id for e in load_subjects().entities}
    if source_id not in entities:
        return []
    ends = [source_id, *_regions(conn, source_id)]
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
                    region=region,
                )
            )
    return found


class SharedEndpoint(_Model):
    """A node linked to both subjects, with each one's links to it (either direction)."""

    node: Node
    a_links: list[Relationship]
    b_links: list[Relationship]


def shared(conn: sqlite3.Connection, a: str, b: str) -> list[SharedEndpoint]:
    """The subjects and endpoints that both `a` and `b` link to, for GET /shared, by id. Seed
    groups (belongs_to_sector) are left out."""

    def links(subject: str) -> dict[str, list[Relationship]]:
        rows = conn.execute(
            "SELECT data FROM relationships WHERE (from_id = ? OR to_id = ?) "
            "AND type != 'belongs_to_sector' ORDER BY id",
            (subject, subject),
        ).fetchall()
        out: dict[str, list[Relationship]] = {}
        for row in rows:
            r = Relationship.model_validate_json(row["data"])
            out.setdefault(r.to if r.from_ == subject else r.from_, []).append(r)
        return out

    a_links, b_links = links(a), links(b)
    common = sorted((a_links.keys() & b_links.keys()) - {a, b})
    nodes = load_nodes(conn, set(common))
    return [SharedEndpoint(node=nodes[i], a_links=a_links[i], b_links=b_links[i]) for i in common]
