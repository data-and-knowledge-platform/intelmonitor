"""Subjects as the pipeline sees them: one flat list with descriptions for Claude.

Topics and entities are monitored: screening asks about them, developments are tagged to them and
priority scores them. Groups are peer sets of entities (list page filters, peer comparison,
search queries); nothing is screened for, tagged to or scored for a group
(phase1-decisions.md, S3)."""

from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

from intelmonitor.pipeline.params import SINGAPORE_SYSTEM
from intelmonitor.seed import Entity, EntityKind, SubjectsSeed, SubjectType, load_subjects

_KIND_LABEL: dict[EntityKind, str] = {
    "bank": "bank",
    "central_bank": "central bank and financial regulator",
    "regulator": "financial regulator",
    "agency": "government agency",
    "market_infrastructure": "exchange and market infrastructure operator",
    "payment_system": "payment system",
    "tech_provider": "technology and service provider to financial institutions",
    "asset_manager": "asset manager",
    "investor": "investment company or sovereign wealth fund",
    "insurer": "insurer",
    "payment_institution": "payments and fintech company",
    "stablecoin_issuer": "stablecoin issuer",
    "digital_asset_firm": (
        "crypto exchange, broker or custodian (digital payment token service provider)"
    ),
    "international_body": "international organisation or standard-setting body",
    "country": "country or territory",
    "region": "region or group of countries",
    "sector": "sector",
}


# Kinds that are news scopes rather than organisations.
_SCOPE_KINDS: tuple[EntityKind, ...] = ("country", "region", "sector")


@dataclass(frozen=True)
class Subject:
    id: str
    name: str
    type: SubjectType
    kind: EntityKind | None
    description: str  # what counts as being about this subject; groups are never asked about
    members: tuple[str, ...]  # entity ids, for groups
    function: str | None = None  # the id of the MAS function that owns a topic
    # False for a hidden entity: no list row, page, search hit or watchlist entry (seed.py).
    displayed: bool = True


@lru_cache
def all_subjects() -> dict[str, Subject]:
    seed = load_subjects()
    names = {e.id: e.name for e in seed.entities}
    owner = {t: f.id for f in seed.functions for t in f.topics}
    subjects: dict[str, Subject] = {}
    for t in seed.topics:
        about = f"the topic {t.name}: {t.covers}"
        subjects[t.id] = Subject(t.id, t.name, "topic", None, about, (), function=owner[t.id])
    for g in seed.groups:
        members = ", ".join(names[m] for m in g.members)
        subjects[g.id] = Subject(
            g.id,
            g.name,
            "group",
            None,
            f"the group {g.name} ({members})",
            tuple(g.members),
        )
    for e in seed.entities:
        aliases = f", also {', '.join(e.aliases)}" if e.aliases else ""
        label = _KIND_LABEL[e.kind]
        # Countries, regions and sectors are news scopes rather than organisations, so they say
        # what belongs to them.
        if e.kind in ("country", "region"):
            about = (
                f"the {label} {e.name}{aliases}: its economy, markets (such as property, credit "
                "and currency), financial system, economic and financial policy, and political or "
                "security events with financial effects (not merely a company based there)"
            )
        elif e.kind == "sector":
            about = (
                f"the {label} {e.name}{aliases}, in any country: its market, prices, activity, "
                "defaults and financing, and policy aimed at it (not merely one company's "
                "routine news)"
            )
        else:
            about = f"{e.name}{aliases}, the {label}"
        if e.covers:
            # Country, region and sector descriptions already carry a colon.
            about += f"; {e.covers}" if e.kind in _SCOPE_KINDS else f": {e.covers}"
        subjects[e.id] = Subject(e.id, e.name, "entity", e.kind, about, (), displayed=e.displayed)
    return subjects


@lru_cache
def monitored_subjects() -> dict[str, Subject]:
    """Topics and entities, in seed order: what screening asks about and developments carry."""
    return {i: s for i, s in all_subjects().items() if s.type != "group"}


# What the priority prompt calls "the subject's Singapore branch", per entity: the Singapore unit,
# services or system an event must reach to be High for it.
SINGAPORE_HOME = "the subject itself, a Singapore institution, its overseas units included"
SINGAPORE_BRANCH = "the subject's Singapore branch or subsidiary"
SINGAPORE_SERVICES = "the subject's services to financial institutions in Singapore"
_SERVICE_KINDS: tuple[EntityKind, ...] = (
    "tech_provider",
    "payment_system",
    "market_infrastructure",
)
_SYSTEM_KINDS: tuple[EntityKind, ...] = (
    "country",
    "region",
    "sector",
    "central_bank",
    "regulator",
    "agency",
    "international_body",
)


def _singapore_phrase(entity: Entity | None) -> str:
    """The Singapore phrase for a subject: Singapore's financial system for a country, region,
    sector, central bank, regulator (MAS included), government agency or international body, and
    for a topic (None); else the whole institution
    when its home is Singapore; else a tech provider, payment system or market infrastructure's
    services to financial institutions in Singapore; any other institution's Singapore branch or
    subsidiary."""
    if entity is None or entity.kind in _SYSTEM_KINDS:
        return SINGAPORE_SYSTEM
    if entity.home == "singapore":
        return SINGAPORE_HOME
    if entity.kind in _SERVICE_KINDS:
        return SINGAPORE_SERVICES
    return SINGAPORE_BRANCH


# Kinds whose High is anchored on their operations in Singapore (the team's subject guide of
# 23 Nov 2026, its Singapore branch only); every other entity is anchored on itself.
_SINGAPORE_OPERATIONS_KINDS: tuple[EntityKind, ...] = (
    "bank",
    "insurer",
    "asset_manager",
    "payment_institution",
    "digital_asset_firm",
    "stablecoin_issuer",
)


def _anchor_phrase(entity: Entity | None) -> str:
    """What a finding must materially change to be High for a subject, by the team's subject
    guide: MAS or Singapore's financial system for a topic (None); a country's or region's
    financial system; a sector itself; a financial institution in Singapore; else the entity
    itself."""
    if entity is None:
        return f"MAS or {SINGAPORE_SYSTEM}"
    if entity.kind == "country":
        return f"{entity.name}'{'' if entity.name.endswith('s') else 's'} financial system"
    if entity.kind == "region":
        return f"the financial systems of {entity.name}"
    if entity.kind == "sector":
        return f"the {entity.name[0].lower()}{entity.name[1:]} sector"
    if entity.kind in _SINGAPORE_OPERATIONS_KINDS:
        return f"{entity.name} in Singapore"
    return entity.name


def _local_anchor_phrase(entity: Entity | None) -> str:
    """The anchor phrase (`_anchor_phrase`), except that a tech provider, payment system or market
    infrastructure based in Singapore, such as Singtel, is anchored on itself in Singapore, as a
    financial institution is, so its deals and events elsewhere are not High on their size
    alone."""
    if entity is not None and entity.kind in _SERVICE_KINDS and entity.home == "singapore":
        return f"{entity.name} in Singapore"
    return _anchor_phrase(entity)


@dataclass(frozen=True)
class PrioritySubject:
    """A topic or entity as the priority prompt names it. A frozen store keeps its subjects as
    they were rated (trial.py), so prompts rebuilt on it do not follow later seed edits."""

    id: str
    name: str
    type: SubjectType
    singapore: str  # its Singapore phrase
    # Its anchor phrases, which a template's {anchor} (`_anchor_phrase`) and {local_anchor}
    # (`_local_anchor_phrase`) take. Empty in a seed frozen before anchors, which
    # `seed_as_rated` fills from the working tree's seed.
    anchor: str = ""
    local_anchor: str = ""


def priority_subjects_of(seed: SubjectsSeed) -> dict[str, PrioritySubject]:
    """The seed's topics and entities, in seed order, with their names, Singapore phrases and
    anchor phrases."""
    topics = [
        PrioritySubject(
            t.id,
            t.name,
            "topic",
            _singapore_phrase(None),
            _anchor_phrase(None),
            _local_anchor_phrase(None),
        )
        for t in seed.topics
    ]
    entities = [
        PrioritySubject(
            e.id,
            e.name,
            "entity",
            _singapore_phrase(e),
            _anchor_phrase(e),
            _local_anchor_phrase(e),
        )
        for e in seed.entities
    ]
    return {s.id: s for s in [*topics, *entities]}


@lru_cache
def priority_subjects() -> dict[str, PrioritySubject]:
    """The working tree seed's priority subjects, which the pipeline rates with."""
    return priority_subjects_of(load_subjects())


def resolve_groups(subject_ids: Iterable[str]) -> list[str]:
    """Monitored subject ids for some subject ids, in order without repeats: a group stands for
    its members, and ids no longer in the seed drop out."""
    subjects = all_subjects()
    out: dict[str, None] = {}
    for i in subject_ids:
        if (s := subjects.get(i)) is not None:
            out.update(dict.fromkeys(s.members if s.type == "group" else (i,)))
    return list(out)


def scope(subject: Subject) -> set[str]:
    """Subject ids whose developments count for `subject`: itself, plus members for a group."""
    return {subject.id, *subject.members}
