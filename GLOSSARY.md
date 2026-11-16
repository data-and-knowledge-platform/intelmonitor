# IntelMonitor

A monitoring site for MAS staff: news and data on financial institutions and themes, condensed into developments with their sources.

## Language

**Subject**:
Anything the site monitors: a topic or an entity.
_Avoid_: Monitor

**Entity**:
An institution, system, country or sector the site monitors, such as DBS or PayNow.
_Avoid_: Company, institution (as the general term)

**Topic**:
One of MAS's 30 themes the site monitors, such as Banking & Lending, defined by MAS's descriptor.
_Avoid_: Theme, category

**Group**:
A named set of entities, such as Singapore banks, used only to organise entities. A group has no page and nothing is tagged to it.
_Avoid_: Peer set, watchlist

**Function**:
One of the five MAS functions (Financial Supervision, FinTech & Technology, Markets & Financial Sector Development, Economic Policy, Cross-cutting Functions), each owning a set of topics.
_Avoid_: Role, profile, department

**Entity page** / **Topic page**:
The page for one entity or one topic.
_Avoid_: Monitor page, dashboard

**Watchlist**:
The entities and topics a view keeps at hand in the sidebar.
_Avoid_: Pins, follows, favourites, bookmarks

**View**:
Which watchlist the site shows: the Personal view, which the viewer edits and which lives in their browser, or one of the five function views, each a fixed preset.
_Avoid_: Profile, role, dashboard, mode

**Development**:
One event, merged from the findings of every document that reports it.
_Avoid_: Story, article, item, finding (for the merged event)

**Finding**:
One statement a single document makes about a subject; developments are built from findings.
_Avoid_: Development (for the single-document statement)

**Priority**:
How much a development matters to one of its subjects, judged only from what its sources show for Singapore (the entity's Singapore unit or services, or Singapore's financial system): High, Medium or Low, with a one-sentence reason. A topic takes the highest priority among the development's entities.
_Avoid_: Risk rating, materiality, severity

**Link**:
A stored relationship between two entities, or between an entity and an endpoint (an institution, place or system the site does not monitor), of one of ten types such as holds_stake_in or depends_on, with its source and a one-line reason.
_Avoid_: Edge (outside the graph view), connection

**Link strength**:
How strongly a material adverse event at one end of a link reaches the other end: Strong, Moderate or Weak, rated once for each direction.
_Avoid_: Weight (the stake or exposure share a link stores), transmission (in the product)

**Direct impact**:
A development's priority for an entity it is about.
_Avoid_: Primary impact

**Indirect impact**:
The level an adverse development passes through one link to an entity it is not about, read from a fixed table of the direct level and the link's strength in that direction: Medium or Low, never High.
_Avoid_: Knock-on effect, contagion, second-order impact

**Prompt version**:
One stage's prompt as the model received it, that is its template, output schema, model and effort, numbered from 1 per stage in the order first used; every stored label names the version that made it.
_Avoid_: Prompt revision, prompt id (for the number)

**Baseline**:
The frozen copy of the store taken on 16 Nov 2026, before any seed or prompt change (six runs, 89 entities), on which every priority version is tried so that two versions differ only by prompt.
It carries the seed its runs were rated with (each subject's name and Singapore phrase), which every prompt rebuilt on it is filled from, and it is opened without migrations.
_Avoid_: Snapshot, backup (for this copy)
