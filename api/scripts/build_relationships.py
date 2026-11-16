"""Build api/seed/relationships.yaml from the collected relationship files.

Reads api/seed/collected/bank-exposures.yaml, tech-dependencies.yaml, entity-links.yaml and every
links-*.yaml (one per sector: structural links, dependencies and participations), puts
every link in one schema, folds each unit of an entity into its parent (collected/folds.yaml),
keeps a link only when both ends are entities of api/seed/subjects.yaml, derives each entity's
belongs_to_sector links from the seed's groups, writes each link's one-line reason, merges each
link's strengths from api/seed/collected/transmission.yaml, and prints a build report that lists
every collected row not built, with its reason. The collected rows stay in their files. The
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
    Evidence,
    Figure,
    FigureMeasure,
    FigureUnit,
    Node,
    Relationship,
    RelType,
    Service,
    Transmission,
    load_nodes,
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


class Fold(Row):
    unit: str
    parent: str
    name: str
    evidence: str


class NotCarried(Row):
    id: str
    reason: str


class FoldsFile(Row):
    """collected/folds.yaml: units of entities, each read as its parent."""

    folds: list[Fold]
    not_carried: list[NotCarried]


def collected_row(r: Relationship) -> str:
    """A link as the collected row it was built from, "from type to", before any folding."""
    return (r.rating_from or r.id).replace(":", " ")


class Build:
    """Relationships and the collected rows not built, collected file by file."""

    def __init__(self, seed: SubjectsSeed, folds: FoldsFile) -> None:
        self.seed = seed
        self.subjects = {e.id: e.name for e in seed.entities}  # entity id -> name
        self.groups = {g.id: g.name for g in seed.groups}  # belongs_to_sector ends at a group
        self.fold = {f.unit: f.parent for f in folds.folds}
        self.not_carried = {n.id: n.reason for n in folds.not_carried}
        entities = {e.id: e for e in seed.entities}
        for f in folds.folds:
            assert f.unit not in entities, f"fold {f.unit} is an entity"
            parent = entities.get(f.parent)
            assert parent is not None, f"fold {f.unit}: parent {f.parent} is not an entity"
            assert f.name in [parent.name, *parent.aliases], (
                f"fold {f.unit}: {f.name} is not a name or alias of {f.parent} in subjects.yaml"
            )
        self.rels: dict[str, Relationship] = {}
        self.dropped: list[tuple[str, str]] = []  # (row, reason)
        self.merged: list[str] = []  # ids that took a second row from the same source
        self.partner_rows = 0  # collected partner_of rows read

    def drop(self, row: str, reason: str) -> None:
        self.dropped.append((row, reason))

    def add(self, rel: Relationship) -> None:
        """Add a relationship from a collected row, its unit ends folded into their parents. Not
        built, with the reason: a row folds.yaml does not carry, a row whose ends fold into one
        entity, a row with an end that is not an entity (a seed group is one only for
        belongs_to_sector), and a repeat. Of two rows for one link where either was folded, the
        row with the newer source is kept (on a tie the unfolded row, else the first), and a
        folded link records in `rating_from` the id it had before folding. A second unfolded row
        from the same source is merged into the first (details joined, quotes joined with " | "),
        any other unfolded repeat dropped."""
        row = f"{rel.from_} {rel.type} {rel.to}"
        if rel.id in self.not_carried:
            self.drop(row, f"not carried (folds.yaml): {self.not_carried[rel.id]}")
            return
        src, dst = self.fold.get(rel.from_, rel.from_), self.fold.get(rel.to, rel.to)
        if (src, dst) != (rel.from_, rel.to):
            if src == dst:
                self.drop(row, "folds into a link from its parent to itself")
                return
            src, dst = ends(src, rel.type, dst)
            update = {"id": rel_id(src, rel.type, dst), "from_": src, "to": dst}
            rel = rel.model_copy(update=update | {"rating_from": rel.id})
            row += f" (folded: {rel.id})"
        if rel.from_ == rel.to:
            self.drop(row, "links an entity to itself")
            return
        outside = [] if rel.from_ in self.subjects else [rel.from_]
        sector = rel.type == "belongs_to_sector" and rel.to in self.groups
        if rel.to not in self.subjects and not sector:
            outside.append(rel.to)
        if outside:
            self.drop(f"{row}: {', '.join(outside)}", "an end is not an entity")
            return
        first = self.rels.get(rel.id)
        if first is None:
            self.rels[rel.id] = rel
        elif first.rating_from or rel.rating_from:
            dated, before = rel.as_of or rel.last_confirmed, first.as_of or first.last_confirmed
            unfolded = first.rating_from is not None and rel.rating_from is None
            newer = dated > before or (dated == before and unfolded)
            kept, lost = (rel, first) if newer else (first, rel)
            self.rels[rel.id] = kept
            self.drop(
                f"{collected_row(lost)} (as {rel.id}; kept: {collected_row(kept)})",
                "folds onto a link already there; the row with the newer source is kept",
            )
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


def with_reasons(build: Build) -> list[Relationship]:
    """Every relationship with its reason written."""
    nodes = load_nodes({r.to for r in build.rels.values()})
    return [r.model_copy(update={"reason": reason(r, nodes[r.to])}) for r in build.rels.values()]


def stale(rels: list[Relationship]) -> list[str]:
    """Links whose model rating was made from another input than the link's current prompt. A
    link carrying a folded unit's rating (`rating_from`) is listed apart, not here."""
    names = rating.ends()
    ratings = load_ratings()
    return sorted(
        r.id
        for r in rels
        if r.rating_from is None
        and (e := ratings.get(r.id)) is not None
        and e.rated_by == "model"
        and e.input_hash != rating.input_hash(rating.user_prompt(r, names))
    )


def with_ratings(rels: list[Relationship]) -> tuple[list[Relationship], list[str], list[str]]:
    """Every relationship with its strengths from transmission.yaml (belongs_to_sector has
    none), the rateable ids with no entry, and the entries for links that no longer exist. A link
    takes its own entry when transmission.yaml holds one, so a link that was already between two
    entities keeps its strengths when a folded unit's newer row replaces its row. A folded link
    with no entry of its own takes the rating of the unit's link it was (`rating_from`), an
    interim until scripts/rate_relationships.py rates it under its own id; once it has its own,
    `rating_from` is cleared."""
    ratings = load_ratings()
    out: list[Relationship] = []
    for r in rels:
        key = r.rating_from if r.id not in ratings and r.rating_from in ratings else r.id
        update: dict[str, object] = {"rating_from": key if key != r.id else None}
        if rateable(r) and key in ratings:
            update["transmission"] = Transmission(
                from_to=ratings[key].from_to, to_from=ratings[key].to_from
            )
        out.append(r.model_copy(update=update))
    unrated = [r.id for r in out if rateable(r) and r.transmission is None]
    used = {r.rating_from or r.id for r in out if rateable(r)}
    return out, sorted(unrated), sorted(ratings.keys() - used)


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
#   rating_from   a link folded from a unit's row (api/seed/collected/folds.yaml) whose
#                 transmission is, for now, the rating of that unit's link, under this id
#   Keys left out are null, false or empty.
# Both ends of every link are entities in api/seed/subjects.yaml, apart from belongs_to_sector,
# which ends at a seed group; a link into a region entity covers its member countries.
"""


class NoAliasDumper(yaml.SafeDumper):
    """Writes repeated values (such as the shared last_confirmed date) out in full."""

    def ignore_aliases(self, data: object) -> bool:
        return True


def write(rels: list[Relationship]) -> None:
    order = sorted(rels, key=lambda r: (REL_TYPES.index(r.type), r.from_, r.to))
    doc = {"relationships": [r.model_dump(by_alias=True, exclude_defaults=True) for r in order]}
    body = yaml.dump(doc, Dumper=NoAliasDumper, sort_keys=False, allow_unicode=True, width=100)
    OUT.write_text(HEADER + "\n" + body)


def report(build: Build, rels: list[Relationship]) -> None:
    grades: tuple[Evidence, ...] = Evidence.__args__
    counts = Counter((r.type, r.evidence) for r in rels)
    sectors = sum(r.type == "belongs_to_sector" for r in rels)
    folded = [r for r in build.rels.values() if r.rating_from is not None]  # before with_ratings
    print(
        f"\n{len(rels)} relationships: {len(rels) - sectors} between two entities, of which "
        f"{len(folded)} folded from a unit's row; {sectors} belongs_to_sector"
    )
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
    print(f"\nmerged a second row from the same source into: {', '.join(build.merged)}")
    print(f"\n{len(build.dropped)} collected rows not built")
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
    build = Build(load_subjects(), FoldsFile.model_validate(read("folds.yaml")))
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
    rels, unrated, orphans = with_ratings(with_reasons(build))
    report(build, rels)
    rated = sum(r.transmission is not None for r in rels)
    carried = sorted((r.id, r.rating_from) for r in rels if r.rating_from is not None)
    print(
        f"\nstrengths from {TRANSMISSION_PATH.name}: {rated} links rated, "
        f"{len(unrated)} rateable links unrated, {len(orphans)} entries for links that no "
        "longer exist"
    )
    print(
        f"  {len(carried)} folded links carry the rating of the unit's link they were "
        "(rating_from), until scripts/rate_relationships.py rates them under their own id:"
    )
    print(*(f"    {i} <- {f}" for i, f in carried), sep="\n")
    for i in orphans:
        print(f"  no such link: {i}")
    if unrated:
        print(
            f"  {len(unrated)} unrated (load_relationships refuses them): run "
            "scripts/rate_relationships.py, then build again"
        )
    outdated = stale([r for r in rels if rateable(r)])
    print(f"  {len(outdated)} stale ratings (input changed since the link was rated)")
    if outdated:
        print(*(f"    {i}" for i in outdated), sep="\n")
        print("  run scripts/rate_relationships.py to rate them again, then build again")
    problems = [f"no source: {i}" for i in unsourced(rels)]
    problems += [f"stored both ways: {i}" for i in both_ways(rels)]
    if problems:
        print("\n".join(problems), "\nnot written", sep="\n")
        sys.exit(1)
    write(rels)
    print(f"\nwrote {OUT}: every link joins two entities, or an entity and a seed group")


if __name__ == "__main__":
    main()
