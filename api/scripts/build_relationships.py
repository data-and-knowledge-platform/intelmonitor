"""Build api/seed/relationships.yaml from the collected relationship files.

Reads api/seed/collected/bank-exposures.yaml, tech-dependencies.yaml, entity-links.yaml and every
links-*.yaml (one per sector: structural links and service links; links-new-types.yaml holds the
rows scripts/collect_links.py found, and an empty or missing file adds nothing), puts every link
of the team's 22 types in one schema (a partner_of row, outside the types, is listed and not
built), folds each
unit of an entity into its parent (collected/folds.yaml), keeps a link only when both ends are
entities of api/seed/subjects.yaml, derives each entity's belongs_to_sector links from the seed's
groups, writes each link's one-line reason, merges each link's strengths from
api/seed/collected/transmission.yaml, and prints a build report that lists every collected row not
built, with its reason. The collected rows stay in their files. The
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
from pydantic import BaseModel, ConfigDict, Field, model_validator

from intelmonitor.config import SEED_DIR
from intelmonitor.pipeline.impact import link_words
from intelmonitor.relationships import (
    LINK_TYPES,
    LOAN_MEASURES,
    REL_TYPES,
    RELATIONSHIPS_PATH,
    SYMMETRIC,
    TRANSMISSION_PATH,
    Dimension,
    Evidence,
    Figure,
    FigureMeasure,
    FigureUnit,
    Node,
    PromptVersion,
    Relationship,
    RelType,
    Service,
    Transmission,
    load_nodes,
    load_ratings,
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


# The types whose rows carry a service: the provider (a vendor, a payment system or market
# infrastructure, a distributor) to the entity it serves.
SERVICE_TYPES: tuple[RelType, ...] = (
    "provides_technology_to",
    "provides_connectivity_to",
    "provides_business_service_to",
    "processes_transactions_for",
    "distributes_products_for",
)


class TechRow(Row):
    from_: str = Field(alias="from")
    to: str
    type: Literal[
        "provides_technology_to",
        "provides_connectivity_to",
        "provides_business_service_to",
        "processes_transactions_for",
        "distributes_products_for",
        "holds_stake_in",
        "operates_in",
    ]
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
    # Any of the 22 types but belongs_to_sector, which comes from the seed's groups. partner_of is
    # not one of the 22 types: its rows are marked so, kept in the files and not built (ticket 02
    # of link-types-and-inferred-impact, P3)
    type: Literal[RelType, "partner_of"]
    outside_types: bool = False
    share: float | None
    controlling: bool = False  # as on TechRow
    detail: str
    evidence: Evidence
    as_of: date | None
    source_url: str
    source_title: str
    quote: str
    # A row of links-new-types.yaml: the id of the link collection prompt version that found it
    # (the file's `prompts`), of the link check version that kept it (the file's `checks`), and
    # the date it was collected, which stands for the file's date.
    prompt_version: str | None = None
    check_version: str | None = None
    collected: date | None = None

    @model_validator(mode="after")
    def _outside(self) -> "LinkRow":
        assert (self.type == "partner_of") == self.outside_types, (
            f"{self.from_} {self.type} {self.to}: partner_of rows, and only they, are "
            "outside_types: true"
        )
        assert self.type != "belongs_to_sector", (
            f"{self.from_} {self.type} {self.to}: belongs_to_sector comes from the seed's groups"
        )
        return self


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
    """A links-*.yaml file: structural links and service links for one sector's entities, checked
    against their sources on `collected`. links-new-types.yaml also lists the versions of the link
    collection prompt its rows name (`prompts`, as transmission.yaml lists the rating prompt's)
    and of the link check that kept them (`checks`)."""

    collected: date
    prompts: list[PromptVersion] = []
    checks: list[PromptVersion] = []
    relationships: list[LinkRow]
    service_links: list[TechRow]
    groups: list[GroupRow]
    new_entities: list[NewEntity]


# Output. Keys at their default (null, false, empty list) are left out of the YAML.


def rel_id(src: str, type_: RelType, dst: str) -> str:
    return f"{src}:{type_}:{dst}"


def ends(src: str, type_: RelType, dst: str) -> tuple[str, str]:
    """A link's (from, to): a SYMMETRIC type has no direction, so its ids go in alphabetical
    order."""
    return (min(src, dst), max(src, dst)) if type_ in SYMMETRIC else (src, dst)


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


def collected_row(link_id: str, origin: str | None) -> str:
    """A link as the collected row it was built from, "from type to": its id before folding
    (`origin`) for a folded row."""
    return (origin or link_id).replace(":", " ")


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
        # A folded link's id -> the id of the collected row it was built from, before folding
        self.folded: dict[str, str] = {}
        self.dropped: list[tuple[str, str]] = []  # (row, reason)
        self.merged: list[str] = []  # ids that took a second row from the same source
        self.partner_rows: list[str] = []  # collected partner_of rows, outside the 22 types
        self.joint_rows = 0  # collected jointly_offers_product_with rows read

    def drop(self, row: str, reason: str) -> None:
        self.dropped.append((row, reason))

    def add(self, rel: Relationship) -> None:
        """Add a relationship from a collected row, its unit ends folded into their parents. Not
        built, with the reason: a row folds.yaml does not carry, a row whose ends fold into one
        entity, a row with an end that is not an entity (a seed group is one only for
        belongs_to_sector), and a repeat. Of two rows for one link where either was folded, the
        row with the newer source is kept (on a tie the unfolded row, else the first), and
        `folded` records the id a kept folded row had before folding. A second unfolded row from
        the same source is merged into the first (details joined, quotes joined with " | "), any
        other unfolded repeat dropped."""
        row = f"{rel.from_} {rel.type} {rel.to}"
        origin: str | None = None  # the row's id before folding, for a folded row
        if rel.id in self.not_carried:
            self.drop(row, f"not carried (folds.yaml): {self.not_carried[rel.id]}")
            return
        src, dst = self.fold.get(rel.from_, rel.from_), self.fold.get(rel.to, rel.to)
        if (src, dst) != (rel.from_, rel.to):
            if src == dst:
                self.drop(row, "folds into a link from its parent to itself")
                return
            src, dst = ends(src, rel.type, dst)
            origin = rel.id
            rel = rel.model_copy(update={"id": rel_id(src, rel.type, dst), "from_": src, "to": dst})
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
        first_origin = self.folded.get(rel.id)
        if first is None:
            self.rels[rel.id] = rel
            if origin:
                self.folded[rel.id] = origin
        elif first_origin or origin:
            dated, before = rel.as_of or rel.last_confirmed, first.as_of or first.last_confirmed
            unfolded = first_origin is not None and origin is None
            newer = dated > before or (dated == before and unfolded)
            kept, lost = (rel, origin), (first, first_origin)
            if not newer:
                kept, lost = lost, kept
            self.rels[rel.id] = kept[0]
            if kept[1]:
                self.folded[rel.id] = kept[1]
            else:
                self.folded.pop(rel.id, None)
            self.drop(
                f"{collected_row(rel.id, lost[1])} (as {rel.id}; kept: "
                f"{collected_row(rel.id, kept[1])})",
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
                service=r.service if r.type in SERVICE_TYPES else None,
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
        if r.type == "partner_of":
            row = f"{r.from_} {r.type} {r.to}"
            build.partner_rows.append(row)
            build.drop(row, "outside the 22 types (outside_types: true)")
            continue
        src, dst = ends(RENAME.get(r.from_, r.from_), r.type, RENAME.get(r.to, r.to))
        build.joint_rows += r.type == "jointly_offers_product_with"
        build.add(
            Relationship(
                id=rel_id(src, r.type, dst),
                from_=src,
                to=dst,
                type=r.type,
                weight=r.share if r.type == "holds_stake_in" else None,
                evidence=r.evidence,
                as_of=r.as_of,
                last_confirmed=r.collected or confirmed,
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


# Directed types where the two directions are different facts, so a pair may hold both: two firms
# may each provide a service to the other, or hold a stake in each other.
MUTUAL: tuple[RelType, ...] = tuple(
    t
    for t in REL_TYPES
    if LINK_TYPES[t].group != "Structural and institutional" and t not in SYMMETRIC
)


def both_ways(rels: list[Relationship], mutual: bool = False) -> list[str]:
    """Links whose pair is also stored the other way with the same type: of a MUTUAL type when
    `mutual`, else of any other type (must be none; a SYMMETRIC type is stored once per pair)."""
    ids = {r.id for r in rels}
    return sorted(
        r.id for r in rels if rel_id(r.to, r.type, r.from_) in ids and (r.type in MUTUAL) == mutual
    )


def symmetric_out_of_order(rels: list[Relationship]) -> list[str]:
    """SYMMETRIC links whose ids are not in alphabetical order (must be none)."""
    return sorted(r.id for r in rels if r.type in SYMMETRIC and r.from_ > r.to)


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
    """Links whose model rating was made from another input than the current prompt version and
    the link's user prompt."""
    names = rating.ends()
    ratings = load_ratings()
    return sorted(
        r.id
        for r in rels
        if (e := ratings.get(r.id)) is not None
        and e.rated_by == "model"
        and e.input_hash != rating.input_hash(rating.user_prompt(r, names))
    )


def with_ratings(rels: list[Relationship]) -> tuple[list[Relationship], list[str], list[str]]:
    """Every relationship with its strengths and prompt version from its own entry in
    transmission.yaml, the ids with no entry, and the entries for links that no longer exist."""
    ratings = load_ratings()
    out = [
        r.model_copy(
            update={
                "transmission": Transmission(
                    from_to=e.from_to, to_from=e.to_from, prompt_version=e.prompt_version
                )
            }
        )
        if (e := ratings.get(r.id)) is not None
        else r
        for r in rels
    ]
    unrated = sorted(r.id for r in out if r.transmission is None)
    return out, unrated, sorted(ratings.keys() - {r.id for r in rels})


HEADER = """\
# Generated by api/scripts/build_relationships.py from api/seed/collected/. Do not edit by hand:
# edit the collected files or the script, then rerun it from api/.
#
# relationships[]: one per (from, type, to); id is "from:type:to". A pair may hold several links
#                 of different types.
#   type          one of the team's 22 types (deck of 16 Nov 2026), in three groups:
#                 ownership and financial: holds_stake_in | lends_to | invests_in | funds
#                 | insures; operational and commercial: provides_technology_to
#                 | provides_connectivity_to | provides_business_service_to
#                 | processes_transactions_for | shares_or_processes_data_with
#                 | distributes_products_for | provides_customer_benefits_with
#                 | jointly_offers_product_with | material_customer_of | material_supplier_to;
#                 structural and institutional: regulated_by | operates_in | exposed_to
#                 | member_of | participates_in | listed_on | belongs_to_sector.
#                 holds_stake_in runs holder to held, a service type provider to the entity it
#                 serves (a payment system to its participant), distributes_products_for
#                 distributor to product owner; provides_customer_benefits_with and
#                 jointly_offers_product_with are stored once per pair, ids in alphabetical
#                 order; belongs_to_sector links an entity to a seed group (an
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
#   service       provides_technology_to, provides_connectivity_to,
#                 provides_business_service_to, processes_transactions_for and
#                 distributes_products_for: cloud, data_centre, payments, custody, distribution
#                 and the like
#   figures[]     exposed_to only: further figures for the same entity and target (loans by
#                 currency, Pillar 3 RWA, NPLs; other reserve, revenue or counterparty figures),
#                 each with its own source and quote
#   transmission  every link: from_to and to_from, each a strength (Strong, Moderate, Weak or
#                 Non-propagating) and a reason: how strongly a material adverse event at the
#                 first end reaches the second; prompt_version, the id of the link rating prompt
#                 version that rated it (null for a hand entry); from
#                 api/seed/collected/transmission.yaml
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
    print(
        f"\n{len(rels)} relationships: {len(rels) - sectors} between two entities, of which "
        f"{len(build.folded)} folded from a unit's row; {sectors} belongs_to_sector"
    )
    print(f"  {'type':32}{'all':>5}" + "".join(f"{g:>10}" for g in grades))
    for t in REL_TYPES:
        row = [counts[(t, g)] for g in grades]
        print(f"  {t:32}{sum(row):>5}" + "".join(f"{n:>10}" for n in row))
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
    print(
        f"  partner_of: {len(build.partner_rows)} collected rows outside the 22 types, not built: "
        + ", ".join(build.partner_rows)
    )
    for t in SYMMETRIC:
        n = sum(r.type == t for r in rels)
        rows = build.joint_rows if t == "jointly_offers_product_with" else "no"
        print(f"  {t}: {rows} collected rows, {n} links (one per pair, ids in alphabetical order)")
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
        f"of a type whose two directions are different facts, kept: "
        f"{', '.join(both_ways(rels, mutual=True)) or 'none'}; symmetric links out of "
        f"alphabetical order: {', '.join(symmetric_out_of_order(rels)) or 'none'}"
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


# The rows scripts/collect_links.py found (step 5 of link-types-and-inferred-impact).
NEW_TYPES_FILE = "links-new-types.yaml"


def report_new(build: Build, rels: list[Relationship], rows: list[LinkRow]) -> None:
    """Per type, the links the rows of NEW_TYPES_FILE hold and how many of them are in the build
    (under their own id, or folded into a parent's link that kept them)."""
    if not rows:
        return
    ids = {r.id for r in rels} | set(build.folded.values())
    links = {
        (r.type, rel_id(src, r.type, dst))
        for r in rows
        if r.type != "partner_of"
        for src, dst in [ends(RENAME.get(r.from_, r.from_), r.type, RENAME.get(r.to, r.to))]
    }
    read_n = Counter(t for t, _ in links)
    built_n = Counter(t for t, i in links if i in ids)
    print(
        f"\n{NEW_TYPES_FILE}: {len(rows)} rows read, holding {len(links)} links, "
        f"{sum(built_n.values())} of them in the build"
    )
    for t in REL_TYPES:
        if read_n[t]:
            print(f"  {t:32}{read_n[t]:>5} links{built_n[t]:>5} in the build")


def read(name: str) -> object:
    return yaml.safe_load((COLLECTED / name).read_text())


def build_collected(skip: str | None = None) -> tuple[Build, list[LinkRow]]:
    """Every collected file built into links, but the links-*.yaml file named `skip`, and the
    rows of NEW_TYPES_FILE read. scripts/collect_links.py skips NEW_TYPES_FILE to know which links
    the other files hold."""
    exposures = ExposuresFile.model_validate(read("bank-exposures.yaml"))
    new_rows: list[LinkRow] = []
    tech = TechFile.model_validate(read("tech-dependencies.yaml"))
    links = LinksFile.model_validate(read("entity-links.yaml"))
    build = Build(load_subjects(), FoldsFile.model_validate(read("folds.yaml")))
    build_links(build, links)  # first, so its ownership shares win over tech's repeats
    build_tech(build, tech)
    build_exposures(build, exposures)
    for path in sorted(COLLECTED.glob("links-*.yaml")):
        if path.name == skip or (doc := read(path.name)) is None:  # skipped, or an empty file
            continue
        team = TeamLinksFile.model_validate(doc)
        if path.name == NEW_TYPES_FILE:
            new_rows = team.relationships
        build_links(
            build,
            LinksFile(
                relationships=team.relationships, groups=team.groups, new_entities=team.new_entities
            ),
            team.collected,
        )
        build_tech(
            build,
            TechFile(relationships=team.service_links, incidents=[], new_entities=[]),
            team.collected,
        )
    return build, new_rows


def main() -> None:
    build, new_rows = build_collected()
    build_sectors(build)
    rels, unrated, orphans = with_ratings(with_reasons(build))
    report(build, rels)
    report_new(build, rels, new_rows)
    rated = sum(r.transmission is not None for r in rels)
    versions = Counter(r.transmission.prompt_version for r in rels if r.transmission)
    print(
        f"\nstrengths from {TRANSMISSION_PATH.name}: {rated} links rated, "
        f"{len(unrated)} links unrated, {len(orphans)} entries for links that no longer exist; "
        "by prompt version: "
        + ", ".join(f"{v or 'hand'} {n}" for v, n in sorted(versions.items(), key=str))
    )
    for i in orphans:
        print(f"  no such link: {i}")
    if unrated:
        print(
            f"  {len(unrated)} unrated (load_relationships refuses them): run "
            "scripts/rate_relationships.py, then build again"
        )
    outdated = stale(rels)
    print(f"  {len(outdated)} stale ratings (input changed since the link was rated)")
    if outdated:
        print(*(f"    {i}" for i in outdated), sep="\n")
        print("  run scripts/rate_relationships.py to rate them again, then build again")
    problems = [f"no source: {i}" for i in unsourced(rels)]
    problems += [f"stored both ways: {i}" for i in both_ways(rels)]
    problems += [f"symmetric link out of order: {i}" for i in symmetric_out_of_order(rels)]
    if problems:
        print("\n".join(problems), "\nnot written", sep="\n")
        sys.exit(1)
    write(rels)
    print(f"\nwrote {OUT}: every link joins two entities, or an entity and a seed group")


if __name__ == "__main__":
    main()
