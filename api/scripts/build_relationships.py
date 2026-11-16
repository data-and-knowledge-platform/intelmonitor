"""Build api/seed/relationships.yaml from the collected relationship files.

Reads api/seed/collected/bank-exposures.yaml, tech-dependencies.yaml, entity-links.yaml and every
links-*.yaml (one per sector: structural links, dependencies and participations), puts
every link in one schema, decides subject or endpoint from api/seed/subjects.yaml, derives each
entity's belongs_to_sector links from the seed's groups, writes each link's one-line reason, merges
each link's strengths from api/seed/collected/transmission.yaml, and prints a build report. The
output is deterministic: a second run produces no diff. Rerun after any edit to the collected files
or to the subject seed, then run scripts/rate_relationships.py and build again: a link whose input
changed keeps its old rating until it is rated again. The report lists unrated links and stale
ratings (a model rating whose input hash no longer matches the link's current prompt).

Usage (from api/): uv run python scripts/build_relationships.py
"""

import sys
from collections import Counter
from datetime import date
from typing import Literal

import rate_relationships as rating
import yaml
from pydantic import BaseModel, ConfigDict, Field

from intelmonitor.config import SEED_DIR
from intelmonitor.pipeline.impact import link_words
from intelmonitor.relationships import (
    LOAN_MEASURES,
    REL_TYPES,
    RELATIONSHIPS_PATH,
    TRANSMISSION_PATH,
    Dimension,
    Endpoint,
    EndpointKind,
    Evidence,
    Figure,
    FigureMeasure,
    FigureUnit,
    Node,
    Relationship,
    RelationshipsFile,
    RelType,
    Service,
    Transmission,
    load_ratings,
    rateable,
)
from intelmonitor.seed import SubjectsSeed, load_subjects

COLLECTED = SEED_DIR / "collected"
OUT = RELATIONSHIPS_PATH
# The bank, tech and entity-link files were checked against their sources on this date; each
# links-*.yaml file states its own.
LAST_CONFIRMED = date(2026, 10, 7)
# belongs_to_sector links come from the seed's groups, last reviewed on this date (ticket 17, Q4).
SECTORS_CONFIRMED = date(2026, 11, 11)
# The seed's first groups are the MAS top 50's parent categories; an entity sits in at most one of
# them (banking and fintech also hold banks and fintechs added later).
TOP_50_GROUPS = 8

# Ids the collected files spell differently from the subject seed.
RENAME = {
    "china": "mainland-china",
    "mainland-china-offshore": "mainland-china",
    "xfers": "straitsx",  # Xfers Pte. Ltd. trades as StraitsX
}
# Exposure buckets that never become relationship targets, with the reason.
NOT_TARGETS = {
    "us-dollar": "currency bucket, not a place",
    "rest-of-world": "residual bucket",
    "ccyb-others": "residual bucket",
    "other-asia-pacific": "residual bucket",
    "other-industries": "residual bucket",
}
# Regions a bank discloses loans for instead of countries, and the places each one covers.
REGION_MEMBERS = {
    "greater-china": ["hong-kong", "mainland-china", "taiwan", "macau"],
    "rest-of-greater-china": ["mainland-china", "taiwan", "macau"],
    "south-and-southeast-asia": ["india", "indonesia", "malaysia", "thailand", "vietnam"],
}
# Endpoints no collected file declares (or declares with the wrong kind). Used only when the id
# is not a subject, so parking a subject such as Maybank lands it here.
EXTRA_ENDPOINTS: dict[str, tuple[str, EndpointKind]] = {
    "bangkok-bank": ("Bangkok Bank", "bank"),
    "bank-of-china": ("Bank of China Singapore", "bank"),
    "cimb": ("CIMB Bank", "bank"),
    "citibank": ("Citibank", "bank"),
    "great-eastern": ("Great Eastern", "insurer"),
    "gxs-bank": ("GXS Bank", "bank"),
    "income-insurance": ("Income Insurance", "insurer"),
    "maybank": ("Maybank", "bank"),
    "temenos": ("Temenos", "tech_provider"),
    "trust-bank": ("Trust Bank", "bank"),
    "egiro": ("eGIRO", "payment_system"),
}


class Row(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Market(Row):
    id: str
    name: str
    labels: dict[str, list[str]]


class ExposureRow(Row):
    bank: str
    dimension: Dimension
    measure: Literal["gross_customer_loans", "npl", "npl_ratio", "npa_coverage", "rwa"]
    segment: str
    market: str
    value: float
    unit: FigureUnit
    share_of_total: float | None
    period: date
    source_url: str
    source_title: str
    page: int | None
    quote: str
    note: str | None = None


class Gap(Row):
    bank: str
    item: str
    detail: str


class ExposuresFile(Row):
    markets: list[Market]
    gaps: list[Gap]
    exposures: list[ExposureRow]


class NewEntity(Row):
    id: str
    name: str
    kind: str
    aliases: list[str]
    country: str | None = None


class TechRow(Row):
    from_: str = Field(alias="from")
    to: str
    type: Literal["depends_on", "participates_in", "holds_stake_in", "operates_in"]
    # holds_stake_in with no published share where the holder controls the held (a former owns or
    # subsidiary_of row): the reason says "controlling owner".
    controlling: bool = False
    detail: str
    service: Service
    evidence: Evidence
    source_url: str | None
    source_title: str | None
    published: date | None
    quote: str | None
    as_of: date | None = None  # for an undated source: the date it was read


class TechFile(Row):
    relationships: list[TechRow]
    incidents: list[dict[str, object]]  # evidence for links above, not links themselves
    new_entities: list[NewEntity]


class LinkRow(Row):
    from_: str = Field(alias="from")
    to: str
    type: Literal[
        "holds_stake_in",
        "operates_in",
        "member_of",
        "partner_of",
        "listed_on",
        "regulated_by",
    ]
    share: float | None
    controlling: bool = False  # as on TechRow
    detail: str
    evidence: Evidence
    as_of: date | None
    source_url: str
    source_title: str
    quote: str


class GroupRow(Row):
    id: str
    name: str
    kind: str
    members: list[str]
    detail: str
    source_url: str
    quote: str


class LinksFile(Row):
    relationships: list[LinkRow]
    groups: list[GroupRow]
    new_entities: list[NewEntity]


class TeamLinksFile(Row):
    """A links-*.yaml file: structural links, dependencies and participations for one sector's
    entities, checked against their sources on `collected`."""

    collected: date
    relationships: list[LinkRow]
    depends_on_participates: list[TechRow]
    groups: list[GroupRow]
    new_entities: list[NewEntity]


# Output. Keys at their default (null, false, empty list) are left out of the YAML.


def rel_id(src: str, type_: RelType, dst: str) -> str:
    return f"{src}:{type_}:{dst}"


def ends(src: str, type_: RelType, dst: str) -> tuple[str, str]:
    """A link's (from, to): partner_of has no direction, so its ids go in alphabetical order."""
    return (min(src, dst), max(src, dst)) if type_ == "partner_of" else (src, dst)


# Collected entity kinds that differ from the endpoint kinds.
KIND_ALIASES: dict[str, EndpointKind] = {
    "central-bank": "central_bank",
    "exchange": "market_infrastructure",
    "consortium": "group",
    "platform": "group",
    "taskforce": "group",
    "index": "group",
}


def endpoint_kind(kind: str) -> EndpointKind:
    if kind in KIND_ALIASES:
        return KIND_ALIASES[kind]
    kinds: tuple[EndpointKind, ...] = EndpointKind.__args__
    assert kind in kinds, f"unknown entity kind {kind}"
    return kind


class Build:
    """Relationships, endpoint declarations and dropped rows, collected file by file."""

    def __init__(self, seed: SubjectsSeed) -> None:
        self.seed = seed
        self.subjects = {e.id: e.name for e in seed.entities}  # entity subject id -> name
        self.groups = {g.id: g.name for g in seed.groups}  # seed groups are link ends too
        self.rels: dict[str, Relationship] = {}
        self.declared: dict[str, Endpoint] = {}  # first declaration wins
        self.dropped: list[tuple[str, str]] = []  # (row, reason)
        self.merged: list[str] = []  # ids that took a second row from the same source
        self.partner_rows = 0  # collected partner_of rows read

    def drop(self, row: str, reason: str) -> None:
        self.dropped.append((row, reason))

    def declare(self, endpoint: Endpoint) -> None:
        self.declared.setdefault(endpoint.id, endpoint)

    def add(self, rel: Relationship) -> None:
        """Add a relationship; a second row for the same link from the same source is merged
        into the first (details joined, quotes joined with " | "), any other repeat dropped."""
        row = f"{rel.from_} {rel.type} {rel.to}"
        if rel.from_ == rel.to:
            self.drop(row, "links an entity to itself")
            return
        first = self.rels.get(rel.id)
        if first is None:
            self.rels[rel.id] = rel
        elif first.source_url == rel.source_url and first.quote and rel.quote:
            self.merged.append(rel.id)
            self.rels[rel.id] = first.model_copy(
                update={
                    "detail": f"{first.detail}; {rel.detail}",
                    "quote": f"{first.quote} | {rel.quote}",
                }
            )
        else:
            self.drop(row, "repeats a link already taken from another source")


FIGURE_ORDER = (
    "gross_customer_loans",
    "loans_by_currency",
    "rwa",
    "npl",
    "npl_ratio",
    "npa_coverage",
)


def measure_of(row: ExposureRow) -> FigureMeasure:
    if row.measure == "gross_customer_loans" and row.dimension == "currency":
        return "loans_by_currency"
    return row.measure


def sgd_m(row: ExposureRow) -> float:
    return {"SGD m": 1.0, "SGD b": 1000.0}[row.unit] * row.value


def money(value: float, unit: str) -> str:
    if unit.startswith("SGD "):
        return f"S${value:,.0f}{unit[4:]}"
    return f"{value:g}%"


def figure(row: ExposureRow) -> Figure:
    return Figure(
        measure=measure_of(row),
        dimension=row.dimension,
        segment=row.segment,
        value=row.value,
        unit=row.unit,
        share=row.share_of_total,
        period=row.period,
        chart_reading=row.note is not None and "chart" in row.note.lower(),
        source_url=row.source_url,
        source_title=row.source_title,
        page=row.page,
        quote=row.quote,
        note=row.note,
    )


def build_exposures(build: Build, data: ExposuresFile) -> None:
    """One exposed_to relationship per bank and target. The headline figure is the bank's gross
    customer loans by geography or industry (or a named breakdown of a region), weighted by its
    total gross customer loans; without one, loans by currency, then Pillar 3 RWA, with no
    weight. Every other figure for the pair rides along in `figures`."""
    totals: dict[str, float] = {}
    for bank in sorted({r.bank for r in data.exposures}):
        loans = [
            r for r in data.exposures if r.bank == bank and r.measure == "gross_customer_loans"
        ]
        geo = sum(sgd_m(r) for r in loans if r.dimension == "geography")
        industry = sum(sgd_m(r) for r in loans if r.dimension == "industry")
        assert abs(geo - industry) < 1, f"{bank}: loans by geography and industry disagree"
        totals[bank] = geo
    industries = {r.market for r in data.exposures if r.dimension == "industry"}
    for m in data.markets:
        id_ = RENAME.get(m.id, m.id)
        if m.id in NOT_TARGETS or id_ != m.id:
            continue
        if id_ in REGION_MEMBERS:
            members = REGION_MEMBERS[id_]
            build.declare(
                Endpoint(id=id_, name=m.name.split(" (")[0], kind="region", members=members)
            )
        else:
            build.declare(
                Endpoint(id=id_, name=m.name, kind="sector" if id_ in industries else "country")
            )
    pairs: dict[tuple[str, str], list[ExposureRow]] = {}
    for row in data.exposures:
        label = f"{row.bank} {row.measure} {row.dimension} {row.market} ({row.segment})"
        if row.market in NOT_TARGETS:
            build.drop(label, NOT_TARGETS[row.market])
        else:
            pairs.setdefault((row.bank, RENAME.get(row.market, row.market)), []).append(row)
    for (bank, target), rows in sorted(pairs.items()):
        figures = sorted(
            (figure(r) for r in rows), key=lambda f: (FIGURE_ORDER.index(f.measure), f.dimension)
        )
        loans = [f for f in figures if f.measure == "gross_customer_loans"]
        main = [f for f in loans if f.dimension != "specific"] or loans
        main = main or [f for f in figures if f.measure in ("loans_by_currency", "rwa")][:1]
        if not main:
            for r in rows:
                build.drop(
                    f"{bank} {r.measure} {r.dimension} {r.market} ({r.segment})",
                    "no loan, currency or RWA figure for this bank and target",
                )
            continue
        head = main[0]
        measure, unit = head.measure, head.unit
        assert measure in ("gross_customer_loans", "loans_by_currency", "rwa")
        assert unit in ("SGD m", "SGD b")
        value = sum(f.value for f in main)
        assert all(f.unit == head.unit and f.source_url == head.source_url for f in main)
        weight = None
        name = build.subjects.get(bank, bank.upper())
        segment = " and ".join(f.segment for f in main)
        if head.measure == "gross_customer_loans":
            weight = round(value * (1000 if head.unit == "SGD b" else 1) / totals[bank], 4)
            detail = (
                f"{name} gross customer loans, {segment}: {money(value, head.unit)}, "
                f"{weight:.1%} of its total at {head.period:%d %b %Y}."
            )
        elif head.measure == "loans_by_currency":
            detail = (
                f"{name} loans in {segment}: {money(value, head.unit)} at {head.period:%d %b %Y}. "
                "Loans by currency stand in for a country figure the bank does not disclose."
            )
        else:
            detail = (
                f"{name} Pillar 3 risk-weighted assets for private sector credit in {segment}: "
                f"{money(value, head.unit)} at {head.period:%d %b %Y}. "
                "The bank discloses no loan figure for this market."
            )
        notes = sorted({f.note for f in main if f.note})
        build.add(
            Relationship(
                id=rel_id(bank, "exposed_to", target),
                from_=bank,
                to=target,
                type="exposed_to",
                weight=weight,
                measure=measure,
                value=value,
                unit=unit,
                evidence="public",
                chart_reading=any(f.chart_reading for f in main),
                as_of=head.period,
                last_confirmed=LAST_CONFIRMED,
                source_url=head.source_url,
                source_title=head.source_title,
                page=head.page,
                quote=" | ".join(f.quote for f in main),
                detail=" ".join([detail, *notes]),
                reason="",  # written by with_reasons()
                figures=[f for f in figures if f not in main],
            )
        )


def build_tech(build: Build, data: TechFile, confirmed: date = LAST_CONFIRMED) -> None:
    for e in data.new_entities:
        build.declare(Endpoint(id=RENAME.get(e.id, e.id), name=e.name, kind=endpoint_kind(e.kind)))
    for r in data.relationships:
        src, dst = RENAME.get(r.from_, r.from_), RENAME.get(r.to, r.to)
        build.add(
            Relationship(
                id=rel_id(src, r.type, dst),
                from_=src,
                to=dst,
                type=r.type,
                evidence=r.evidence,
                as_of=r.published or r.as_of,
                last_confirmed=confirmed,
                source_url=r.source_url,
                source_title=r.source_title,
                quote=r.quote,
                detail=r.detail,
                reason="",  # written by with_reasons()
                service=r.service if r.type in ("depends_on", "participates_in") else None,
                controlling=r.controlling,
            )
        )


# Titles for group sources the collected file gives only as a URL, taken from the URL itself.
GROUP_SOURCE_TITLES = {
    "mas-abs-act": "MAS and ABS establish taskforce to strengthen cyber and technology resilience",
    "cosmic": "MAS launches COSMIC platform",
    "project-guardian": "Annex A: list of participants in Project Guardian industry group (MAS)",
    "global-layer-one": (
        "MAS expands industry collaboration to scale asset tokenisation for financial services"
    ),
    "project-orchid": "MAS report on potential uses of a purpose bound digital Singapore dollar",
    "nexus-global-payments": "Bank of Thailand news, 3 April 2025",
    "sti": "FTSE Russell, Straits Times Index factsheet",
    "bloom": "MAS Launches BLOOM Initiative to Extend Settlement Capabilities",
    "okxice": "OKX joint venture files with SEC to launch tokenized trading platform (Reuters)",
    "global-dollar-network": "OKX joins Global Dollar Network (Global Dollar Network newsroom)",
}
# Group members whose membership only a secondary source reports.
REPORTED_MEMBERS = {("nexus-global-payments", "bank-indonesia")}


def build_links(build: Build, data: LinksFile, confirmed: date = LAST_CONFIRMED) -> None:
    group_ids = {g.id for g in data.groups}
    for e in data.new_entities:
        kind = "group" if e.id in group_ids else endpoint_kind(e.kind)
        build.declare(Endpoint(id=RENAME.get(e.id, e.id), name=e.name, kind=kind))
    titles = {r.source_url: r.source_title for r in reversed(data.relationships)}
    for r in data.relationships:
        src, dst = ends(RENAME.get(r.from_, r.from_), r.type, RENAME.get(r.to, r.to))
        build.partner_rows += r.type == "partner_of"
        build.add(
            Relationship(
                id=rel_id(src, r.type, dst),
                from_=src,
                to=dst,
                type=r.type,
                weight=r.share if r.type == "holds_stake_in" else None,
                evidence=r.evidence,
                as_of=r.as_of,
                last_confirmed=confirmed,
                source_url=r.source_url,
                source_title=r.source_title,
                quote=r.quote,
                detail=r.detail,
                reason="",  # written by with_reasons()
                controlling=r.controlling,
            )
        )
    for g in data.groups:
        build.declared[g.id] = Endpoint(id=g.id, name=g.name, kind="group", detail=g.detail)
        title = titles.get(g.source_url) or GROUP_SOURCE_TITLES[g.id]
        for member in g.members:
            src = RENAME.get(member, member)
            build.add(
                Relationship(
                    id=rel_id(src, "member_of", g.id),
                    from_=src,
                    to=g.id,
                    type="member_of",
                    evidence="reported" if (g.id, member) in REPORTED_MEMBERS else "public",
                    last_confirmed=confirmed,
                    source_url=g.source_url,
                    source_title=title,
                    quote=g.quote,
                    detail=g.detail,
                    reason="",  # written by with_reasons()
                )
            )


def build_sectors(build: Build) -> None:
    """belongs_to_sector, derived from the seed's groups: an entity in one of the first
    TOP_50_GROUPS groups to that group (its parent category), any other entity to each of its
    groups. Nothing is collected for it, so it has no source."""
    top = build.seed.groups[:TOP_50_GROUPS]
    parent: dict[str, str] = {}
    for g in top:
        for m in g.members:
            assert m not in parent, f"{m} sits in two of the first {TOP_50_GROUPS} groups"
            parent[m] = g.id
    names = build.subjects
    for e in build.seed.entities:
        groups = (
            [parent[e.id]]
            if e.id in parent
            else [g.id for g in build.seed.groups if e.id in g.members]
        )
        for g in groups:
            build.add(
                Relationship(
                    id=rel_id(e.id, "belongs_to_sector", g),
                    from_=e.id,
                    to=g,
                    type="belongs_to_sector",
                    evidence="public",
                    last_confirmed=SECTORS_CONFIRMED,
                    detail=f"{names[e.id]} is in the {build.groups[g]} group of the subject seed.",
                    reason="",  # written by with_reasons()
                )
            )


def endpoints_for(build: Build) -> tuple[list[Endpoint], list[str]]:
    """The endpoint entry for every id in use that is not an entity subject, and the ids that
    have neither (must be none)."""
    used = {r.from_ for r in build.rels.values()} | {r.to for r in build.rels.values()}
    for e in build.declared.values():
        if e.id in used:
            used.update(e.members)
    ids = sorted(used - build.subjects.keys() - build.groups.keys())
    return [build.declared[i] for i in ids if i in build.declared], [
        i for i in ids if i not in build.declared
    ]


def unsourced(rels: list[Relationship]) -> list[str]:
    """Relationships missing a source URL, title or quote, unless graded assumed or derived from
    the seed (belongs_to_sector)."""
    return [
        r.id
        for r in rels
        if r.evidence != "assumed"
        and r.type != "belongs_to_sector"
        and not (r.source_url and r.source_title and r.quote)
    ]


# Types where the two directions are different facts, so a pair may hold both: Circle depends on
# Coinbase for distribution, Coinbase on Circle for USDC issuance and redemption.
MUTUAL: tuple[RelType, ...] = ("depends_on",)


def both_ways(rels: list[Relationship], mutual: bool = False) -> list[str]:
    """Links whose pair is also stored the other way with the same type: of a MUTUAL type when
    `mutual`, else of any other type (must be none)."""
    ids = {r.id for r in rels}
    return sorted(
        r.id for r in rels if rel_id(r.to, r.type, r.from_) in ids and (r.type in MUTUAL) == mutual
    )


def reason(r: Relationship, to: Node) -> str:
    """The link in one line: the plain-words phrase with its share and the share's date ("has
    12.2% of its loans in Hong Kong, 30 Jun 2026"); a controlling stake with no share, "is the
    controlling owner of BCS"; else the detail sentence."""
    if r.weight is not None:
        phrase = link_words(r, to)
        return f"{phrase}, {r.as_of.day} {r.as_of:%b %Y}" if r.as_of else phrase
    if r.controlling:
        return f"is the controlling owner of {to.name}"
    return r.detail


def with_reasons(build: Build, endpoints: list[Endpoint]) -> list[Relationship]:
    """Every relationship with its reason written."""
    entities = {e.id: e for e in build.seed.entities}
    nodes = {e.id: Node(id=e.id, name=e.name, kind=e.kind, subject=False) for e in endpoints}
    nodes |= {i: Node(id=i, name=n, kind="group", subject=False) for i, n in build.groups.items()}
    nodes |= {i: Node(id=i, name=e.name, kind=e.kind, subject=True) for i, e in entities.items()}
    return [r.model_copy(update={"reason": reason(r, nodes[r.to])}) for r in build.rels.values()]


def stale(rels: list[Relationship], endpoints: list[Endpoint]) -> list[str]:
    """Links whose model rating was made from another input than the link's current prompt."""
    names = rating.ends(RelationshipsFile(relationships=rels, endpoints=endpoints))
    ratings = load_ratings()
    return sorted(
        r.id
        for r in rels
        if (e := ratings.get(r.id)) is not None
        and e.rated_by == "model"
        and e.input_hash != rating.input_hash(rating.user_prompt(r, names))
    )


def with_ratings(rels: list[Relationship]) -> tuple[list[Relationship], list[str], list[str]]:
    """Every relationship with its strengths from transmission.yaml (belongs_to_sector has
    none), the rateable ids with no entry, and the entries for links that no longer exist."""
    ratings = load_ratings()
    out = [
        r.model_copy(
            update={
                "transmission": Transmission(
                    from_to=ratings[r.id].from_to, to_from=ratings[r.id].to_from
                )
            }
        )
        if rateable(r) and r.id in ratings
        else r
        for r in rels
    ]
    unrated = [r.id for r in rels if rateable(r) and r.id not in ratings]
    ids = {r.id for r in rels if rateable(r)}
    return out, sorted(unrated), sorted(ratings.keys() - ids)


HEADER = """\
# Generated by api/scripts/build_relationships.py from api/seed/collected/. Do not edit by hand:
# edit the collected files or the script, then rerun it from api/.
#
# relationships[]: one per (from, type, to); id is "from:type:to".
#   type          holds_stake_in | regulated_by | operates_in | exposed_to | depends_on
#                 | member_of | participates_in | partner_of | listed_on | belongs_to_sector
#                 holds_stake_in runs holder to held; partner_of is stored once per pair, ids in
#                 alphabetical order; belongs_to_sector links an entity to a seed group (an
#                 entity in one of the first 8 groups to that group, its parent category, any
#                 other entity to each of its groups) and is derived from api/seed/subjects.yaml,
#                 with no source
#   weight        exposed_to: the headline figure's share of its total: a bank's gross customer
#                 loans (null when the headline is loans by currency or Pillar 3 RWA), an
#                 issuer's reserves or reserve fund, a firm's revenue, costs, token supply or
#                 customer assets (null for holdings and card volume); holds_stake_in: the
#                 stake; else null
#   measure, value, unit   exposed_to only: the headline figure (gross_customer_loans,
#                 loans_by_currency or rwa for banks; reserve_assets, repo_principal, revenue,
#                 distribution_costs, token_supply, assets_on_platform, stablecoin_holdings or
#                 card_volume for other entities), in SGD m, SGD b, USD m or m tokens
#   evidence      public | reported | assumed; chart_reading true where the figure was read off
#                 a chart
#   as_of         date the link is known to hold at: the period end for exposures, the source's
#                 publication date for tech dependencies, its as-of date for group structure;
#                 last_confirmed: date it was checked
#   source_url, source_title, page, quote (verbatim; pieces joined with " | "), detail
#   reason        one line built by code: the plain-words phrase with the share and its date,
#                 "is the controlling owner of ..." for a controlling stake with no share, else
#                 detail
#   controlling   holds_stake_in with no published share where the holder controls the held
#   service       depends_on and participates_in: cloud, data_centre, payments, custody,
#                 blockchain, distribution and the like
#   figures[]     exposed_to only: further figures for the same entity and target (loans by
#                 currency, Pillar 3 RWA, NPLs; other reserve, revenue or counterparty figures),
#                 each with its own source and quote
#   transmission  every type but belongs_to_sector: from_to and to_from, each a strength (Strong,
#                 Moderate or Weak) and a reason: how strongly a material adverse event at the
#                 first end reaches the second; from api/seed/collected/transmission.yaml
#   Keys left out are null, false or empty.
# endpoints[]: every id above that is not an entity subject or group in api/seed/subjects.yaml,
#   with name and kind; regions list the places they cover in members.
"""


class NoAliasDumper(yaml.SafeDumper):
    """Writes repeated values (such as the shared last_confirmed date) out in full."""

    def ignore_aliases(self, data: object) -> bool:
        return True


def write(rels: list[Relationship], endpoints: list[Endpoint]) -> None:
    order = sorted(rels, key=lambda r: (REL_TYPES.index(r.type), r.from_, r.to))
    doc = {
        "relationships": [r.model_dump(by_alias=True, exclude_defaults=True) for r in order],
        "endpoints": [e.model_dump(exclude_defaults=True) for e in endpoints],
    }
    body = yaml.dump(doc, Dumper=NoAliasDumper, sort_keys=False, allow_unicode=True, width=100)
    OUT.write_text(HEADER + "\n" + body)


def report(build: Build, rels: list[Relationship], endpoints: list[Endpoint]) -> None:
    grades: tuple[Evidence, ...] = Evidence.__args__
    counts = Counter((r.type, r.evidence) for r in rels)
    print(f"\n{len(rels)} relationships")
    print(f"  {'type':16}{'all':>5}" + "".join(f"{g:>10}" for g in grades))
    for t in REL_TYPES:
        row = [counts[(t, g)] for g in grades]
        print(f"  {t:16}{sum(row):>5}" + "".join(f"{n:>10}" for n in row))
    exposures = [r for r in rels if r.type == "exposed_to"]
    measures = Counter(r.measure for r in exposures)
    firms = sum(n for m, n in measures.items() if m not in LOAN_MEASURES)
    print(
        f"  exposed_to headline: {measures['gross_customer_loans']} loans with a weight, "
        f"{measures['loans_by_currency']} loans by currency, {measures['rwa']} Pillar 3 RWA, "
        f"{firms} other entities' reserves, revenue and holdings; "
        f"{sum(len(r.figures) for r in exposures)} further figures; "
        f"chart readings: {sum(r.chart_reading for r in exposures)} headline, "
        f"{sum(f.chart_reading for r in exposures for f in r.figures)} further"
    )
    partners = sum(r.type == "partner_of" for r in rels)
    print(
        f"  partner_of: {build.partner_rows} collected rows, {partners} links "
        "(one per pair, ids in alphabetical order)"
    )
    sectors = [r for r in rels if r.type == "belongs_to_sector"]
    top = {m for g in build.seed.groups[:TOP_50_GROUPS] for m in g.members}
    expected = {
        (e.id, g.id)
        for e in build.seed.entities
        for i, g in enumerate(build.seed.groups)
        if e.id in g.members and (i < TOP_50_GROUPS or e.id not in top)
    }
    found = {(r.from_, r.to) for r in sectors}
    print(
        f"  belongs_to_sector: {len(sectors)} links, {len(expected)} expected memberships "
        f"({len(top)} entities of the first {TOP_50_GROUPS} groups to their parent category, "
        f"{len(expected) - len(top)} memberships of the other {len(build.subjects) - len(top)}); "
        f"missing {sorted(expected - found) or 'none'}, extra {sorted(found - expected) or 'none'}"
    )
    print(
        f"  pairs stored both ways with one type: {', '.join(both_ways(rels)) or 'none'}; "
        f"mutual {'/'.join(MUTUAL)}, kept: {', '.join(both_ways(rels, mutual=True)) or 'none'}"
    )
    kinds = Counter(e.kind for e in endpoints)
    print(
        f"\n{len(endpoints)} endpoints: " + ", ".join(f"{k} {n}" for k, n in sorted(kinds.items()))
    )
    print(f"\nmerged a second row from the same source into: {', '.join(build.merged)}")
    print(f"\n{len(build.dropped)} collected rows dropped")
    by_reason: dict[str, list[str]] = {}
    for row, reason in build.dropped:
        by_reason.setdefault(reason, []).append(row)
    for reason, rows in sorted(by_reason.items()):
        print(f"  {reason} ({len(rows)}):")
        for row in rows:
            print(f"    {row}")


def read(name: str) -> object:
    return yaml.safe_load((COLLECTED / name).read_text())


def main() -> None:
    exposures = ExposuresFile.model_validate(read("bank-exposures.yaml"))
    tech = TechFile.model_validate(read("tech-dependencies.yaml"))
    links = LinksFile.model_validate(read("entity-links.yaml"))
    build = Build(load_subjects())
    for id_, (name, kind) in EXTRA_ENDPOINTS.items():
        build.declare(Endpoint(id=id_, name=name, kind=kind))
    build_links(build, links)  # first, so its ownership shares win over tech's repeats
    build_tech(build, tech)
    build_exposures(build, exposures)
    for path in sorted(COLLECTED.glob("links-*.yaml")):
        team = TeamLinksFile.model_validate(read(path.name))
        build_links(
            build,
            LinksFile(
                relationships=team.relationships, groups=team.groups, new_entities=team.new_entities
            ),
            team.collected,
        )
        build_tech(
            build,
            TechFile(relationships=team.depends_on_participates, incidents=[], new_entities=[]),
            team.collected,
        )
    build_sectors(build)
    endpoints, unknown = endpoints_for(build)
    if unknown:
        # Reasons name each link's `to` node, so stop before writing them.
        print(
            *(f"neither a subject nor an endpoint: {i}" for i in unknown), "\nnot written", sep="\n"
        )
        sys.exit(1)
    rels, unrated, orphans = with_ratings(with_reasons(build, endpoints))
    report(build, rels, endpoints)
    rated = sum(r.transmission is not None for r in rels)
    print(
        f"\nstrengths from {TRANSMISSION_PATH.name}: {rated} links rated, "
        f"{len(unrated)} rateable links unrated, {len(orphans)} entries for links that no "
        "longer exist"
    )
    for i in orphans:
        print(f"  no such link: {i}")
    if unrated:
        print(
            f"  {len(unrated)} unrated (load_relationships refuses them): run "
            "scripts/rate_relationships.py, then build again"
        )
    outdated = stale([r for r in rels if rateable(r)], endpoints)
    print(f"  {len(outdated)} stale ratings (input changed since the link was rated)")
    if outdated:
        print(*(f"    {i}" for i in outdated), sep="\n")
        print("  run scripts/rate_relationships.py to rate them again, then build again")
    problems = [f"no source: {i}" for i in unsourced(rels)]
    problems += [f"stored both ways: {i}" for i in both_ways(rels)]
    if problems:
        print("\n".join(problems), "\nnot written", sep="\n")
        sys.exit(1)
    write(rels, endpoints)
    print(f"\nwrote {OUT}: no unknown ids")


if __name__ == "__main__":
    main()
