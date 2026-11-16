"""The relationship store: standing links between entities, for indirect impact.

api/seed/relationships.yaml (built by scripts/build_relationships.py) holds every relationship;
each joins two entities of api/seed/subjects.yaml, or an entity and a seed group.
sync_relationships() copies it into the store at the start of a pipeline run; reaches() reads it
back for the impact step, links_from() for the briefs, and links_of() for the Related graph.

A link reads from its `from` to its `to` (DBS exposed_to Hong Kong, FAST depends_on BCS, NETS
holds_stake_in BCS). partner_of is stored once per pair, ids in alphabetical order. A link into
an entity of kind region (South and Southeast Asia) covers the region's member countries.
belongs_to_sector links an entity to a seed group (its parent category), so seed group ids are
valid link ends; it carries no impact and the Related graph leaves it out.

Every other link carries `transmission`, its strength in each direction, rated once by
scripts/rate_relationships.py into api/seed/collected/transmission.yaml and merged in by the
build. Pipeline runs read the ratings and call no model for them.
"""

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
    # The id whose rating in transmission.yaml `transmission` is, when it is not this link's own:
    # a unit's link folded into its parent (collected/folds.yaml) carries the unit link's rating
    # until scripts/rate_relationships.py rates it under its own id.
    rating_from: str | None = None


def rateable(r: Relationship) -> bool:
    """Whether a link is rated: every type but belongs_to_sector, which carries no impact."""
    return r.type != "belongs_to_sector"


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
    and that every rateable link has its strengths. Raises ValueError naming every link end that
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
    strength in that direction. `region` names the region entity when the link goes into a region
    that covers the source (UOB's Greater China loans, for news about Taiwan)."""

    source: str
    reached: str
    relationship: Relationship
    strength: Strength
    region: str | None = None


def reaches(conn: sqlite3.Connection, source_id: str) -> list[Reach]:
    """Every seed entity one link from the entity `source_id`, for indirect impact (ticket 20):
    each link with the source at one end, read from_to when the source is its `from` and to_from
    when it is its `to`; and each link into a region entity whose members (its `members` in the
    seed) include the source, read to_from only. belongs_to_sector, unrated links, groups and the
    source itself carry or get nothing, and nothing goes a second hop. By link id."""
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
                    region=region,
                )
            )
    return found
