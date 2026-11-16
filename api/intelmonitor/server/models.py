"""API response and request models. These are the source of truth for the OpenAPI schema, which
`pnpm gen:api` in web/ turns into TypeScript types."""

from datetime import date, datetime
from enum import IntEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field

from intelmonitor.pipeline.extract import Tone
from intelmonitor.pipeline.graph import Theme
from intelmonitor.pipeline.params import Priority
from intelmonitor.pipeline.subjects import all_subjects
from intelmonitor.relationships import (
    EndpointKind,
    Evidence,
    FigureMeasure,
    FigureUnit,
    HeadlineMeasure,
    HeadlineUnit,
    RelType,
    Strength,
)
from intelmonitor.seed import (
    BadDirection,
    EntityKind,
    Frequency,
    SeriesScope,
    SourceName,
    SubjectType,
    TopicIcon,
    Watchlist,
)
from intelmonitor.store.series import Observation, Revision

Stage = Literal["signal", "consultation", "proposed", "enacted", "enforced"]
Collection = Literal["dms", "official", "news", "data"]


def _known_subject(value: str) -> str:
    if value not in all_subjects():
        raise ValueError(f"unknown subject id {value!r}")
    return value


def _known_entity(value: str) -> str:
    if _known_subject(value) and all_subjects()[value].type != "entity":
        raise ValueError(f"{value!r} is not an entity")
    return value


# A seeded subject id, such as "ocbc" or "singapore-banks".
SubjectId = Annotated[str, AfterValidator(_known_subject)]
EntityId = Annotated[str, AfterValidator(_known_entity)]


class SubjectRef(BaseModel):
    id: str
    name: str
    type: SubjectType
    kind: EntityKind | None
    logo: str | None = Field(
        default=None, description="Entity icon path in the web app, if one exists"
    )
    logo_full: str | None = Field(
        default=None, description="Full entity logo path in the web app, if one exists"
    )
    icon: TopicIcon | None = Field(
        default=None, description="Topic glyph from the web icon library"
    )
    displayed: bool = Field(
        description="False for a hidden entity, which has no page: its name shows as text"
    )


class FunctionRef(BaseModel):
    """One of MAS's five functions, such as Financial Supervision."""

    id: str
    name: str


class FunctionDetail(FunctionRef):
    """A function with the topics it owns and its view's fixed watchlist."""

    topics: list[str] = Field(description="Ids of the topics it owns, in seed order")
    watchlist: Watchlist = Field(
        description="The function view's preset: entity and topic ids, MAS always among them"
    )


class SubjectSummary(SubjectRef):
    aliases: list[str]
    members: list[str] = Field(description="Member entity ids, for a group; else empty")
    function: FunctionRef | None = Field(description="The function that owns a topic; else null")


class SubjectLinks(BaseModel):
    topics: list[SubjectRef]
    groups: list[SubjectRef]
    entities: list[SubjectRef]


class SubjectDetail(SubjectSummary):
    description: str
    domains: list[str]
    links: SubjectLinks


class RunInfo(BaseModel):
    """One execution of a run. A recomputed run appears once per execution."""

    execution_id: int
    id: str = Field(description="The run it computed, named by its as-of date")
    as_of: date
    previous_run_id: str | None
    status: Literal["running", "complete", "failed"]
    trigger: Literal["cli", "manual"] = Field(
        description="cli: `im run` or `im weekly`. manual: Run now, since removed"
    )
    how: str = Field(description='"Command line", or "Run now, <display name>"')
    sources: int = Field(description="Distinct ingest sources among its documents")
    documents: int
    developments: int
    new_count: int
    updated_count: int
    needs_attention_count: int = Field(description="New or updated and needing attention")
    result: str | None = Field(
        description='Against the previous run: "4 new, 2 need attention since 17 Sep", '
        '"No new developments since 17 Sep" or "First run, N developments"; null unless complete'
    )
    started_at: datetime
    finished_at: datetime | None
    duration_seconds: float | None


class RunRef(BaseModel):
    id: str
    as_of: date
    previous_run_id: str | None


class LatestRun(RunRef):
    """The latest complete run, for the top bar's "Week to" date."""

    latest_news: date | None = Field(
        description="The latest last-published date among its developments"
    )


DevelopmentChangeKind = Literal["new", "updated"]


class SourceTypeCount(BaseModel):
    collection: Collection
    count: int


class DevelopmentSummary(BaseModel):
    """A development as a scope sees it: priority, adverse and tone are over the subjects in
    scope that it is tagged to (over all its subjects when unscoped)."""

    id: str
    title: str = Field(description="Its lead finding's full statement")
    headline: str = Field(description="A short news headline; the title when none was written")
    points: list[str] = Field(description="2 or 3 key points; empty when none were written")
    category: str
    stage: Stage | None
    event_date: date | None
    first_published: date
    last_published: date
    materiality_level: int = Field(ge=0, le=2)
    materiality_label: str
    needs_attention: bool
    priority: Priority = Field(description="High, Medium or Low for the subjects in scope")
    priority_for: list[SubjectRef] = Field(
        description="The subjects in scope at that priority, when others in scope rank lower; "
        "empty when they all agree"
    )
    adverse: bool
    direction: Tone = Field(description="Its tone: the most telling over the subjects in scope")
    subjects: list[SubjectRef] = Field(
        description="What it happened to or who did it, highest priority first"
    )
    actors: list[str] = Field(description="Ids of the subjects that did it, among `subjects`")
    also_affects: list[SubjectRef] = Field(
        description="Tracked subjects it reaches indirectly through stored relationships"
    )
    source_count: int
    source_types: list[SourceTypeCount] = Field(description="Sources per collection")
    outlet_count: int = Field(
        description="Distinct news outlets among its sources, one per parent domain"
    )
    unconfirmed: bool = Field(
        description="Its sources do not pass the source gate (pipeline/corroboration.py), so "
        "lists leave it out unless asked"
    )
    collections: list[Collection] = Field(description="Where its sources come from")
    jurisdictions: list[str] = Field(
        description="Names of the jurisdictions its sources report on, alphabetical"
    )
    change: DevelopmentChangeKind | None = Field(description="Change since the previous run")
    new_source_count: int


class FacetValue(BaseModel):
    value: str = Field(description="What the filter parameter takes")
    label: str
    count: int = Field(description="Matches with this value and every other facet's filter")


class DevelopmentFacets(BaseModel):
    """Each facet's values with counts. A value shows when something matches it or it is
    selected."""

    priority: list[FacetValue] = Field(description="High, Medium, Low")
    adverse: list[FacetValue] = Field(description="'true' (adverse) and 'false'")
    tone: list[FacetValue] = Field(description="Negative, neutral, positive")
    category: list[FacetValue] = Field(description="Most matches first")
    materiality: list[FacetValue] = Field(description="Most material first; values are levels")
    jurisdiction: list[FacetValue] = Field(description="Most matches first; values are codes")
    date: list[FacetValue] = Field(
        description="Today, this week and this month (1, 7 and 30 days to the run date), then 'any'"
    )
    source: list[FacetValue] = Field(description="Collections: official, research, data, news")


class DevelopmentList(BaseModel):
    """A scope's developments in the latest run that match the filters, with facet counts over
    the listed ones."""

    in_scope: int = Field(description="Every development in scope")
    matching: int = Field(description="Developments in scope matching the filters")
    unconfirmed: int = Field(
        description="Unconfirmed developments the list would show with `unconfirmed`, whether "
        "it is on or not"
    )
    developments: list[DevelopmentSummary] = Field(
        description="By priority, High first, adverse first within each priority above Low; "
        "most recent first within each"
    )
    facets: DevelopmentFacets


# The Entities and Topics list pages (GET /lists/{list}).
ListName = Literal["entities", "topics"]
Period = Literal["week", "4w", "3m"]


class PriorityCounts(BaseModel):
    """Developments in the period, each counted at this subject's own priority for it."""

    high: int
    medium: int
    low: int


class TopDevelopment(BaseModel):
    """The subject's first development in the period in display order."""

    id: str = Field(description="Opens the development popup")
    headline: str
    priority: Priority = Field(
        description="For this subject; also the row's top priority and its band"
    )
    topic: SubjectRef | None = Field(
        description="Its highest-priority topic, then the highest screen score; null on Topics "
        "rows or when it has no topic"
    )


class ListRow(SubjectRef):
    groups: list[SubjectRef] = Field(
        description="An entity's groups in seed order, the first one shown; empty for topics"
    )
    function: FunctionRef | None = Field(description="The function that owns a topic; else null")
    counts: PriorityCounts
    top: TopDevelopment | None = Field(description="Null when nothing falls in the period")
    new: int = Field(description="Shown developments new in the latest run, whatever the period")
    latest: date | None = Field(
        description="The latest last-published date among all its shown developments in the run"
    )


class SubjectList(BaseModel):
    """Every entity or every topic with its figures for a period, from the latest run's shown
    developments it is tagged to. Indirect impact never counts."""

    run: RunRef
    period: Period
    since: date = Field(
        description="The period's exclusive start: a development is in it when last published "
        "after this date"
    )
    rows: list[ListRow] = Field(description="Ranked by High count, Medium count, then name")


class UpcomingItem(BaseModel):
    date: date
    label: str
    development_id: str
    development_title: str
    subjects: list[SubjectRef]


BriefDirection = Literal["worse", "better", "unchanged", "mixed"]


class BriefConcern(BaseModel):
    label: str | None = Field(
        description="2 to 4 words naming its topic, shown bold before the text; None for a brief "
        "written before labels"
    )
    text: str = Field(description="A risk or an opening for the subject and why it matters")
    development_ids: list[str] = Field(
        description="The developments, open matters and indirect impacts behind it, kept for "
        "checks; the page does not list them"
    )


class BriefDate(BaseModel):
    date: date
    label: str
    development_id: str


class EntityBrief(BaseModel):
    """The brief at the top of a subject's page (pipeline/summaries.py): written by Claude from
    the last 7 days of developments, indirect impacts, series events and upcoming dates and the
    subject's standing position (Key data, main links, open matters), checked and counted by
    code."""

    state: str = Field(description="One sentence on the subject's situation now")
    direction: BriefDirection = Field(
        description="Against the previous brief; unchanged when the model's direction disagreed "
        "with the evidence and was flagged"
    )
    direction_reason: str = Field(description="Empty when the direction was flagged")
    direction_flagged: bool = Field(
        description="The model's direction disagreed with code's signals after a retry"
    )
    concerns: list[BriefConcern] = Field(
        description="At most 2, most serious first; none on a quiet week unless a matter High "
        "and adverse to the subject is open, which a concern then cites. A brief written before "
        "concerns gives its points' claims"
    )
    coming_up: list[BriefDate]
    developments: int = Field(description="Developments with a source in the last 7 days")
    high: int = Field(description="Of those, High for the subject")
    adverse: int = Field(description="Of those, adverse to the subject")
    latest_news: date | None = Field(description="The last source of its latest development")
    model: str = Field(description="none when code wrote it, with no evidence to brief on")


class RelationshipFigure(BaseModel):
    """A further figure behind an exposure, such as loans by currency, risk-weighted assets or
    NPLs, with its own source."""

    measure: FigureMeasure
    segment: str = Field(description="The row label in the source table")
    value: float
    unit: FigureUnit
    share: float | None
    period: date
    chart_reading: bool
    source_url: str
    source_title: str
    page: int | None
    quote: str


class RelationshipLink(BaseModel):
    """A stored relationship as evidence: its type, ends, size, grade and source."""

    id: str
    type: RelType
    label: str = Field(description='The type in plain words: "depends on, for cloud services"')
    from_id: str
    from_name: str
    to_id: str
    to_name: str
    weight: float | None = Field(
        description="Exposures: the headline figure's share of its total (a bank's loans, an "
        "issuer's reserves, a firm's revenue). Ownership: the share held. Else null; an exposure "
        "with no weight discloses no share"
    )
    measure: HeadlineMeasure | None
    value: float | None
    unit: HeadlineUnit | None
    evidence: Evidence
    chart_reading: bool = Field(description="The figure was read off a chart")
    as_of: date | None = Field(description="When the link is known to hold")
    last_confirmed: date
    source_url: str | None
    source_title: str | None
    page: int | None
    quote: str | None = Field(description="Verbatim from the source")
    detail: str
    reason: str = Field(
        description='One line from the stored facts: "has 12.2% of its loans in Hong Kong, 30 Jun '
        '2026"'
    )
    from_to: Strength | None = Field(
        description="How strongly a material adverse event at `from` reaches `to`; null for a "
        "link that is not rated (belongs_to_sector)"
    )
    to_from: Strength | None = Field(description="The same, from `to` to `from`")
    figures: list[RelationshipFigure]


IndirectLevel = Literal["Medium", "Low"]


class IndirectImpact(BaseModel):
    """An entity a development reaches through one stored link, at the level the fixed table
    gives (pipeline/impact.py): the development is adverse for `source` at a direct level, and
    the link's strength from the source's end turns that into `level`."""

    development_id: str
    subject: SubjectRef = Field(description="The entity reached")
    level: IndirectLevel
    strength: Strength = Field(description="The link's strength from the source's end")
    source: SubjectRef = Field(description="The entity the development is about")
    route: str = Field(
        description='The link in plain words from the entity reached: "via its Hong Kong loans, '
        '12.2% of total", "depended on by DBS for telecoms"'
    )
    region: str | None = Field(
        description="The region's name when the link's figure covers a whole region that "
        "includes the source (Greater China)"
    )
    link: RelationshipLink


class SubjectImpact(IndirectImpact):
    """An indirect impact on a subject, with the development that causes it."""

    development: DevelopmentSummary


class DirectSubject(BaseModel):
    """A subject the development happened to (affected) or that did it (actor)."""

    subject: SubjectRef
    role: Literal["affected", "actor"]
    priority: Priority
    adverse: bool
    tone: Tone


class SharedEndpoint(BaseModel):
    """A subject or endpoint both entities link to, with each one's links to it."""

    id: str
    name: str
    kind: EntityKind | EndpointKind
    subject: bool = Field(description="A tracked subject; otherwise a relationship endpoint")
    displayed: bool = Field(description="A tracked subject with a page; false for a hidden entity")
    a_links: list[RelationshipLink]
    b_links: list[RelationshipLink]


class SharedRelationships(BaseModel):
    a: SubjectRef
    b: SubjectRef
    endpoints: list[SharedEndpoint] = Field(description="By name")


class LinkedRoute(BaseModel):
    """How an institution links to the subject: one stored link, or two through a company it
    holds a stake in, written in plain words by code."""

    text: str = Field(
        description='"Has 12.2% of its loans in Hong Kong"; "Holds 100% of DBS Bank (Hong Kong), '
        'which operates in Hong Kong"; region-level routes end ", a figure for the whole region"'
    )
    region: str | None = Field(description="The region's name when the route ends in a region")
    links: list[RelationshipLink] = Field(description="From the institution outward")


class LinkedInstitution(BaseModel):
    """A bank or other financial institution linked to a country, sector, supplier or payment
    system in the relationship store (GET /subjects/{id}/linked)."""

    id: str
    name: str
    kind: EntityKind | EndpointKind
    subject: SubjectRef | None = Field(description="None when the institution is not tracked")
    region: bool = Field(description="Its largest figure covers a whole region, not the subject")
    routes: list[LinkedRoute] = Field(
        description="Direct links first, then through a company it holds a stake in, then "
        "region-level"
    )
    impacts: list[SubjectImpact] = Field(
        description="Developments in the last 7 days that reach it through these routes"
    )


class MonitorPage(BaseModel):
    subject: SubjectDetail
    run: RunRef
    upcoming: list[UpcomingItem]
    trend: bool = Field(description="The subject has measured series for a Trend view")
    brief: EntityBrief | None = Field(description="None until the run has one for the subject")
    impacts: list[SubjectImpact] = Field(
        description="Developments in the last 7 days that reach the subject indirectly"
    )


class Span(BaseModel):
    start: int
    end: int


class Quote(Span):
    text: str


class Passage(BaseModel):
    """An excerpt of the source around a finding's quotes; highlights are offsets into `text`."""

    text: str
    highlights: list[Span]
    clipped_start: bool = Field(description="The document continues before `text`")
    clipped_end: bool = Field(description="The document continues after `text`")


class SeriesInfo(BaseModel):
    id: str
    name: str
    short_name: str = Field(description="The row name under its group heading")
    group: str | None = Field(description="The heading its rows sit under on monitor pages")
    scope: SeriesScope | None = Field(
        description="system: the banking system's figures; market: market-wide"
    )
    listed: bool = Field(description="False: an input to a derived series, off the Indicators")
    unit: str = Field(description='"%" for ratios and rates, else e.g. "USD" or "SGD per MYR"')
    frequency: Frequency
    year_to_date: bool = Field(
        description="A running total since January: only its 1y change, against the same period "
        "a year earlier, is given"
    )
    decimals: int
    bad_direction: BadDirection = Field(description="Which way is worse, if either")
    baseline: float | None = Field(
        description="A level its line is read against: 0 for a distance from a peg"
    )
    source: SourceName
    source_label: str = Field(description='Where its values come from: "MAS I.18", "FRED DFF"')
    providers: list[str] = Field(
        description='The APIs its values come from, following derived inputs: ["CoinGecko", "MAS"]'
    )
    source_url: str


class LinePoint(BaseModel):
    period: date
    value: float
    preliminary: bool


class SeriesChart(BaseModel):
    """A cited series as the evidence drawer draws it: its values over a span ending on the run
    date, as known by the end of that day, with the period its sentence cites highlighted."""

    series: SeriesInfo
    start: date = Field(
        description="12 months before the run date for daily series, 24 months for monthly, "
        "the stored history's start for quarterly"
    )
    end: date = Field(description="The run date")
    line: list[LinePoint] = Field(description="Observations from start to end, by period")
    cited_from: date | None = Field(description="First period the sentence cites")
    cited_to: date | None = Field(description="Last period the sentence cites")
    cited: Observation | None = Field(description="The value the sentence states")
    revisions: list[Revision] = Field(
        description="Revisions of the cited period made by the run date, in the order made"
    )


class EvidenceFinding(BaseModel):
    id: str
    statement: str
    event_date: date | None
    quotes: list[Quote] = Field(description="Offsets into the full document text")
    passage: Passage


class EvidenceSource(BaseModel):
    document_id: str
    title: str
    url: str
    domain: str
    source: str
    collection: Collection
    doc_type: str
    classification: str
    published_at: datetime
    new_in_run: bool = Field(description="Added to the development since the previous run")
    findings: list[EvidenceFinding]
    series: SeriesChart | None = Field(
        description="A detector's document: the series it measured, the event highlighted"
    )


class SubjectConfidence(BaseModel):
    subject: SubjectRef
    confidence: float
    source: str


class DevelopmentDetail(DevelopmentSummary):
    run: RunRef
    materiality_confidence: float
    materiality_rule: str | None = Field(
        description='The detector whose rule set the level, by name ("Fed funds rate change"); '
        "null when the model rated it"
    )
    subject_confidence: list[SubjectConfidence]
    sources: list[EvidenceSource] = Field(
        description="A detection first, then the tagged institutions' own publications, then DMS, "
        "official and news documents; earliest first within each"
    )
    direct_subjects: list[DirectSubject] = Field(
        description="What it happened to or who did it, highest priority first"
    )
    impacts: list[IndirectImpact] = Field(
        description="Entities it reaches indirectly: Medium before Low, stronger links first"
    )


class DocumentDetail(BaseModel):
    id: str
    title: str
    url: str
    domain: str
    source: str
    collection: Collection
    doc_type: str
    classification: str
    published_at: datetime
    text: str
    quotes: list[Quote] = Field(description="Every verified quote in the document, by offset")


NodeType = Literal["topic", "group", "entity", "endpoint", "development"]
# The relationship store's types in capitals; BELONGS_TO_SECTOR links an entity to its seed
# group. AFFECTS: a development to what it happened to or who did it. IMPACTS: a development to an
# entity it reaches indirectly.
LinkType = Literal[
    "HOLDS_STAKE_IN",
    "REGULATED_BY",
    "OPERATES_IN",
    "EXPOSED_TO",
    "DEPENDS_ON",
    "MEMBER_OF",
    "PARTICIPATES_IN",
    "PARTNER_OF",
    "LISTED_ON",
    "BELONGS_TO_SECTOR",
    "AFFECTS",
    "IMPACTS",
]


class GraphNode(BaseModel):
    id: str = Field(description="Subject or endpoint id, or dev:<development id>")
    type: NodeType
    label: str
    kind: EntityKind | EndpointKind | None
    hop: int
    has_new: bool = Field(
        description="A development: new or updated this run. A subject: affected by one."
    )
    needs_attention: bool
    materiality_level: int | None
    last_published: date | None
    theme: Theme | None = Field(description="A development's theme, which the view groups by")


class GraphEdge(BaseModel):
    source: str
    target: str
    type: LinkType
    label: str = Field(description='The link in plain words: "depends on, for cloud services"')
    origin: str = Field(
        description="relationships for a stored link, claude or detector for AFFECTS (who tagged "
        "it), table for IMPACTS; seed and jev in older runs"
    )
    confidence: float
    relationship_id: str | None = Field(description="A stored relationship's id")
    route: str | None = Field(description="IMPACTS: the route in plain words")


class Graph(BaseModel):
    run: RunRef
    center: str
    hops: int
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated_developments: int = Field(description="Development nodes left out by the cap")


class SearchHit(BaseModel):
    subject: SubjectRef
    matched: str = Field(description="The name or alias that matched")


class DocumentHit(BaseModel):
    id: str
    title: str
    domain: str
    published_at: datetime
    snippet: str = Field(
        description="The best matching passage of the body as plain text, without the title, "
        "with … where clipped"
    )
    development_id: str = Field(
        description="The development whose evidence it opens: the first citing it in display order"
    )


class SearchResults(BaseModel):
    subjects: list[SearchHit] = Field(description="By name and alias, best first")
    developments: list[DevelopmentSummary] = Field(
        description="The latest run's, by title and summary, best first"
    )
    documents: list[DocumentHit] = Field(
        description="Sources of the latest run's developments, by title and text, best first"
    )


class DataSourceStats(BaseModel):
    source: str
    collection: Collection
    documents: int
    relevant_documents: int
    first_published: date
    last_published: date


class DataSources(BaseModel):
    t0: date | None
    total_documents: int
    sources: list[DataSourceStats]


class MaterialityLevel(BaseModel):
    """A materiality level for Help: what it means and how the app treats it."""

    level: int = Field(ge=0, le=2)
    name: str
    description: str
    material: bool = Field(description="Medium or High")
    needs_attention: bool
    developments: int = Field(
        description="Developments at this level in the latest run, unconfirmed ones left out"
    )


class RelatedDays(IntEnum):
    """The Related graph's windows in days: the brief's week, a month and a quarter. An enum, not
    a Literal, so a query string's "7" validates."""

    week = 7
    month = 30
    quarter = 90


class RelatedLink(BaseModel):
    """A stored link between the Related graph's centre and one of its nodes."""

    id: str
    type: RelType
    outbound: bool = Field(
        description="The centre is the link's from end: DBS holds a stake in DBS India"
    )
    label: str = Field(
        description='A few words from the centre: "holds 100%", "24.4% of loans", "depended on by"'
    )


class RelatedNode(BaseModel):
    """A subject or endpoint linked to the centre, with the developments that reach the centre
    through it."""

    id: str
    name: str
    kind: EntityKind | EndpointKind
    subject: SubjectRef | None = Field(description="None when the node is not tracked")
    links: list[RelatedLink] = Field(description="Its links to the centre, either direction")
    development_ids: list[str] = Field(
        description="Shown developments in the window that reach the centre through this node, "
        "in display order"
    )
    harmful: int = Field(
        description="How many of them are adverse for the entity they are about; every "
        "indirect impact is, so this equals the number of developments"
    )


class RelatedGraph(BaseModel):
    """Everything linked to a subject in the relationship store, for the Overview's Related
    graph (GET /subjects/{id}/related-graph)."""

    run: RunRef
    center: SubjectRef
    days: RelatedDays
    nodes: list[RelatedNode] = Field(
        description="Most developments first, then most harmful, then by name"
    )
    developments: list[DevelopmentSummary] = Field(
        description="Every development a node lists, in display order"
    )
