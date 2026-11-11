"""Indirect impact by the team's fixed table (deck of 10 Nov 2026, slide 8; ticket 20), with no
model call.

For each development and each entity tagged on it (development_subjects) that the development is
adverse for, every seed entity one link away (relationships.reaches) and not itself tagged gets a
level from INDIRECT_TABLE (pipeline/params.py): the tagged entity's direct level by the link's
strength in the direction from the tagged entity to the neighbour. High gives Medium, Medium,
Low for a Strong, Moderate, Weak link; Medium gives Medium, Low, nothing; Low passes nothing
unless INDIRECT_FROM_LOW. Never High, and never a second hop; a link into a region endpoint counts
for each member country. Where several routes reach the same entity, the highest level wins, then
the stronger link, the larger share, the link id, the source id.

The rows go to development_impacts, apart from development_subjects, so an indirect impact is
never shown as news about the entity itself. The wording helpers below write links in plain
words for the graph, the briefs, the Linked section and the API.
"""

import sqlite3
from dataclasses import dataclass

from intelmonitor.pipeline.params import INDIRECT_FROM_LOW, INDIRECT_TABLE
from intelmonitor.relationships import (
    LOAN_MEASURES,
    HeadlineMeasure,
    Node,
    Relationship,
    RelType,
    Service,
    Strength,
    owns,
    reaches,
)

SERVICE_WORDS: dict[Service, str] = {
    "banking": "banking",
    "blockchain": "blockchain infrastructure",
    "cloud": "cloud services",
    "core_banking": "core banking",
    "custody": "custody",
    "data_centre": "data centre services",
    "distribution": "distribution",
    "market_infrastructure": "market infrastructure",
    "payments": "payments",
    "print_mail": "print and mail",
    "reserve_management": "reserve management",
    "security": "security services",
    "telco": "telecoms",
}

# A link type in plain words, as the graph labels it: "DBS depends on AWS".
LINK_WORDS: dict[RelType, str] = {
    "holds_stake_in": "holds a stake in",
    "regulated_by": "is regulated by",
    "operates_in": "operates in",
    "exposed_to": "lends to",
    "depends_on": "depends on",
    "member_of": "is a member of",
    "participates_in": "takes part in",
    "partner_of": "is a partner of",
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


def link_words(r: Relationship, to: Node) -> str:
    """One link in plain words, without its subject: "depends on AWS for cloud services",
    "has 12.2% of its loans in Hong Kong", "holds 84.9% of its stablecoin reserves in the Circle
    Reserve Fund", "holds 28.3% of DBS", "owns Citibank" for a controlling stake with no share."""
    if r.type == "exposed_to":
        w = _exposure_words(r)
        share = f"{_share(r.weight)} of its {w.basis}" if r.weight is not None else w.basis
        return f"{w.verb} {share} {w.prep} {_place(to)}"
    phrase = f"{LINK_WORDS[r.type]} {to.name}"
    if r.type == "holds_stake_in" and r.weight is not None:
        phrase = f"holds {_share(r.weight)} of {to.name}"
    elif owns(r):
        phrase = f"owns {to.name}"
    if r.type == "depends_on" and r.service:
        phrase += f" for {SERVICE_WORDS[r.service]}"
    if r.chart_reading:
        phrase += " (read from a chart)"
    return phrase


def relationship_label(r: Relationship) -> str:
    """A stored link in plain words without its ends, as the graph labels it: "lends to, 12.2%
    of loans", "earns revenue from, 24% of revenue", "depends on, for cloud services", "holds a
    stake in, 100%"."""
    if r.type == "exposed_to":
        w = _exposure_words(r)
        if r.weight is None:
            return f"{w.head}, share not disclosed"
        return f"{w.head}, {_share(r.weight)} of {w.short}"
    label = LINK_WORDS[r.type]
    if r.type == "holds_stake_in" and r.weight is not None:
        label += f", {_share(r.weight)}"
    if r.type == "depends_on" and r.service:
        label += f", for {SERVICE_WORDS[r.service]}"
    return label


# A link type read from its `to` end, as the Related graph labels it: "AWS, depended on by DBS".
INBOUND_WORDS: dict[RelType, str] = {
    "holds_stake_in": "held by",
    "regulated_by": "regulates",
    "operates_in": "hosts",
    "exposed_to": "lent to by",
    "depends_on": "depended on by",
    "member_of": "includes",
    "participates_in": "used by",
    "partner_of": "partner of",
    "listed_on": "lists",
    "belongs_to_sector": "includes",
}


def short_label(r: Relationship, outbound: bool) -> str:
    """A stored link in a few words from one end, for the Related graph: from its `from` end
    when `outbound` ("holds 100%", "24.4% of loans", "depends on"), else from its `to` end
    ("100% held by", "3.1% of its loans", "depended on by")."""
    share = _share(r.weight) if r.weight is not None else None
    if r.type == "exposed_to":
        w = _exposure_words(r)
        if share:
            return f"{share} of {w.short}" if outbound else f"{share} of its {w.short}"
        return w.head if outbound else w.inbound
    if r.type == "holds_stake_in" and share:
        return f"holds {share}" if outbound else f"{share} held by"
    if not outbound:
        return INBOUND_WORDS[r.type]
    return LINK_WORDS[r.type].removeprefix("is ").removeprefix("a ")


def _inbound(r: Relationship, source: Node) -> str:
    """A link read from its `to` end, naming its `from` end: "depended on by DBS for telecoms",
    "28.3% held by Temasek", "lent to by DBS, 12.2% of its loans"."""
    if r.type == "exposed_to":
        w = _exposure_words(r)
        text = f"{w.inbound} {source.name}"
        return text + (f", {_share(r.weight)} of its {w.short}" if r.weight is not None else "")
    if r.type == "holds_stake_in" and r.weight is not None:
        return f"{_share(r.weight)} held by {source.name}"
    text = f"{INBOUND_WORDS[r.type]} {source.name}"
    if r.type == "depends_on" and r.service:
        text += f" for {SERVICE_WORDS[r.service]}"
    return text


def route_text(r: Relationship, source: str, reached: str, nodes: dict[str, Node]) -> str:
    """The one link by which news about `source` reaches `reached`, in plain words without
    `reached`, for an indirect impact. From the link's `from` end: "via its Hong Kong loans,
    12.2% of total", "depends on Singtel for telecoms"; from its `to` end: "depended on by DBS
    for telecoms"; into a region that covers the source: "via its Greater China loans, 13.5% of
    total, a figure for the whole region including Taiwan". `nodes` holds both ends and the
    source (relationships.load_nodes)."""
    if reached == r.to:
        return _inbound(r, nodes[r.from_])
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
    """An entity tagged on a development: the development's direct level for it (0 Low to 2
    High) and whether it is adverse for it."""

    level: int
    adverse: bool


@dataclass(frozen=True)
class Reached:
    """An entity a development reaches indirectly, as development_impacts stores it: the tagged
    entity it comes from, the link, that link's strength from the source's end, and the level."""

    subject_id: str
    source_id: str
    relationship: Relationship
    region: str | None
    strength: Strength
    level: int


STRENGTHS: tuple[Strength, ...] = Strength.__args__  # strongest first


def indirect_level(direct: int, strength: Strength, *, from_low: bool) -> int | None:
    """INDIRECT_TABLE's level for a direct level and a strength; None passes nothing. A Low
    direct item passes only `from_low`."""
    if direct == 0 and not from_low:
        return None
    return INDIRECT_TABLE[direct][strength]


def _best(r: Reached) -> tuple[int, int, float, str, str]:
    """Sort key among routes to one entity: the highest level, the stronger link, the larger
    share (none last), the link id, the source id."""
    return (
        -r.level,
        STRENGTHS.index(r.strength),
        -(r.relationship.weight or 0.0),
        r.relationship.id,
        r.source_id,
    )


def reached(
    conn: sqlite3.Connection, tagged: dict[str, Tagged], *, from_low: bool = INDIRECT_FROM_LOW
) -> list[Reached]:
    """The entities one development reaches through the table, one row each, by id. `tagged`
    holds every subject tagged on it; only those it is adverse for pass, and none of them gets a
    row."""
    best: dict[str, Reached] = {}
    for source, tag in tagged.items():
        if not tag.adverse:
            continue
        for reach in reaches(conn, source):
            level = indirect_level(tag.level, reach.strength, from_low=from_low)
            if level is None or reach.reached in tagged:
                continue
            row = Reached(
                subject_id=reach.reached,
                source_id=source,
                relationship=reach.relationship,
                region=reach.region,
                strength=reach.strength,
                level=level,
            )
            held = best.get(row.subject_id)
            if held is None or _best(row) < _best(held):
                best[row.subject_id] = row
    return [best[s] for s in sorted(best)]


@dataclass
class ImpactStats:
    developments: int = 0  # with a row under INDIRECT_FROM_LOW
    impacts: int = 0  # rows stored, under INDIRECT_FROM_LOW
    without_low: int = 0  # rows with INDIRECT_FROM_LOW off
    with_low: int = 0  # rows with it on


def store_impacts(conn: sqlite3.Connection, run_id: str) -> ImpactStats:
    """Replace the run's development_impacts with the table's rows, in one transaction, and count
    the rows under both INDIRECT_FROM_LOW settings."""
    tags: dict[str, dict[str, Tagged]] = {}
    for r in conn.execute(
        "SELECT development_id, subject_id, priority_level, adverse FROM development_subjects "
        "WHERE run_id = ? ORDER BY development_id, subject_id",
        (run_id,),
    ):
        tags.setdefault(r["development_id"], {})[r["subject_id"]] = Tagged(
            level=int(r["priority_level"]), adverse=bool(r["adverse"])
        )
    stats = ImpactStats()
    rows: list[tuple[str, Reached]] = []
    for dev_id, tagged in tags.items():
        found = {low: reached(conn, tagged, from_low=low) for low in (False, True)}
        stats.without_low += len(found[False])
        stats.with_low += len(found[True])
        rows += [(dev_id, r) for r in found[INDIRECT_FROM_LOW]]
        stats.developments += bool(found[INDIRECT_FROM_LOW])
    stats.impacts = len(rows)
    with conn:
        conn.execute("DELETE FROM development_impacts WHERE run_id = ?", (run_id,))
        conn.executemany(
            "INSERT INTO development_impacts (run_id, development_id, subject_id, source_id, "
            "relationship_id, region, strength, level) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
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
                )
                for dev_id, r in rows
            ],
        )
    return stats
