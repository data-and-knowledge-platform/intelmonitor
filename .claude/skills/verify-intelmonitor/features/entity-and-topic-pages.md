# Entity and topic pages

An entity page (`/entities/<id>`) or topic page (`/topics/<id>`) is one subject's view as of the latest run.
The head shows the logo or icon, the name and one line: an entity's kind and its peers as links (`Bank · Peers: OCBC, UOB`, with `N more` when there are more than five), or a topic's scope; on its right, `Add to watchlist` ([watchlist](watchlist.md)).
Opening any tab, once the subject loads, records the page in Recently viewed.
Under the head, underlined tabs: Overview, Developments, Related (when something reaches the subject through its links this week) and Data (subjects with listed series).
The Overview holds the summary band full width (the week's brief, `region "This week"`, with the top 3 Key data figures, the next date and `Open Data` on its right), then the subject's top 5 developments this month and the Related card, each full width.
The Developments tab holds the full feed with its filters, under High, Medium and Low headings.
The Related tab (`/entities/<id>/related`, components/monitor/Related.tsx) holds `Could affect <name>`: what reaches the subject through its links this week.
It shows only when something does; otherwise the tab is absent and its URL lands on the Overview.
Under 900px the band's figures go under the brief, side by side, and the Related card's list goes under its graph.
Groups have no page: a group's link and its old URLs (`/monitors/<group>`, `/entities/<group>` and their tabs) land on the Entities list filtered to it (`/entities?group=<group>`).
The old `/monitors/<id>` URLs and their tabs land on the same tab at `/entities/<id>` or `/topics/<id>`, and a page under the other list moves to its own.
There is no Ask about, What changed, Follow or Mark reviewed.
The Data tab is in [data.md](data.md); the Developments tab's feed is in [developments](developments.md).

## Sub-features

- `head` `heading [level=1]` with the subject name; the line under it with peer links and `button "N more"`; `button "Add to watchlist"`, then `On watchlist` (`On Personal watchlist` in a function view) with `Remove from watchlist` on hover once the pointer has left after a click.
- `tabs` `navigation "Monitor views"` with `link "Overview"`, `link "Developments"`, `link "Related"` and `link "Data"`; the open tab has `aria-current="page"`.
- `brief` `region "This week"` (`section.ov-band`, components/monitor/overview/SummaryBand.tsx), its heading visually hidden: the direction (`Unchanged from last week` and the reason), then the state at 22px (one sentence on where the subject stands; on a quiet week it says there is nothing new to worry about and gives the standing position), then up to two concerns, each its bold label (`Property loans.`, absent on briefs written before labels) and a plain sentence (a risk or an opening and why it matters; an indirect one names its route, such as `through its Hong Kong loans`). No development links or titles, no counts line, no Related line. With no brief: `No summary for <name> this week.`
- `band-side` (`.ov-band-side`, the band's right column): the first 3 of the API's Key data pick (`key_data.flagged` then `key_data.rows`), each its name over the latest value and its change (`.kf[data-series-id]`), then `link "Open Data"` (only with figures), then `Next: <date>, <label>`, the soonest of the brief's and the next 90 days' dates, the label a link to its development with the development's title as its tooltip. The column is absent when there are neither figures nor dates, and the brief takes the band.
- Key data and Data tab changes are coloured by the series' bad direction: green when good, red when bad (a rise in an NPL or loan-to-deposit ratio is red).
- `related-card` `region "Related to <name>"` (components/monitor/RelatedTree.tsx), under the top 5, after the team's draft: on a grey stage, `7 days`/`30 days`/`90 days`, chips `All (<n>)` and one per entity with linked news (`<name> (<n>)`, narrowing the tree to it), the strength legend and `checkbox "Show entities with no linked news"`, unchecked by default. The tree (`data-testid=related-tree`): the subject at the left and one line per stored link to each linked entity (at most three, then `+<n> more`), coloured and weighted by its strength toward the subject (`toward` in `verify api "/subjects/<id>/related-graph?days=7"`: Strong, Moderate, Weak, Passes nothing), each a `.rt-lab` button with its words, its strength and the count of news it carried (`development_ids` of the link). Each entity box (`.rt-node`) shows its linked news, `development_ids` of the node: the window's developments that reached the subject through any of its links, each once; its name links to its page unless hidden; its `button "Open news from <name>"` opens up to four cards beside the tree (`.rt-card`: priority, category, headline opening the popup, date, `via <link>`), then `Show all <n>`. A line click narrows the open entity's cards to that link and fades the other lines; a second click clears it. The first entity with linked news is open on load. Checking the box adds every other linked entity under `No linked news in <n> days`. Empty window: `No news reached <name> through its links in the last <n> days.` `link "See all links"` to the Related tab, only when the subject has that tab. Absent for a subject with no stored links (most topics). Until step 9 of the link-types spec, a development is carried by one link only, so a pair's other lines show no count.
- `top` `region "Top <name> developments"` for an entity, `region "Top developments"` for a topic: the first 5 rows of this month's feed, unconfirmed ones left out (see [developments](developments.md)), High and adverse for the subject first, then other High, Medium adverse, other Medium and the rest, newest first within each, each with its priority badge (no headings), then `link "See all <n> developments this month"` (or `See all developments` with 5 or fewer) to the Developments tab. Empty: `No developments this month.`, then `link "See earlier developments"` to the tab on All time (`?days=any`) when older ones exist, and no link when the subject has none; error: `These developments could not be loaded.` Rows open the development popup (`?development=<id>&scope=subject:<id>`).
- `developments-tab` `/entities/<id>/developments` or `/topics/<id>/developments`: `region "Developments about <name>"` with the full feed under the column header, the period and facets in the URL (`?days=7&priority=High`). A group's URL lands on the Entities list filtered to it (`/entities?group=<id>`).
- `related-tab` `/entities/<id>/related`: the sections below, top to bottom; no Overview panels. A group's URL lands on the Entities list filtered to it; a subject with nothing related lands on its Overview.
- `could-affect` (Related tab) `region "Could affect <name>"`, meta `<n> this week` (the last 7 days of indirect impacts, adverse developments only, read from the fixed table of the direct level and the link's strength: Medium or Low, never High): headings by kind of link, `Exposure` (`exposed_to`), `Ownership and structure` (`holds_stake_in`, `operates_in`, `member_of`), `Dependency` (`provides_technology_to`, `provides_connectivity_to`, `provides_business_service_to`, `processes_transactions_for`, `distributes_products_for`, `participates_in`), `Other links`; under each, the link in words (`Singapore loans, 44.4% of total`) with its evidence line, then up to 3 developments (the indirect level, the title as a link, the link's reason, `News about <place>` on region-wide links, the date) and `button "Show N more"`. No why sentence, direction or confidence.
- `redirects` `/monitors/<id>` and its tabs on the same tab at the new URL; `/monitors/<id>/connections` on the Overview; `/monitors/<id>/trend` on Data; `/topics/<entity>/...` and `/entities/<topic>/...` on their own list; `/monitors/<group>` and `/entities/<group>/data` on `/entities?group=<group>`.

## How to get to it (user POV)

- A row's name on the Entities or Topics list ([list-pages](list-pages.md)); the sidebar's Recently viewed and Watchlist entries; home.
- ⌘K search, type the name, Enter.
- Any subject link: a development row or the development popup, the head's peer links.
- The tabs in `navigation "Monitor views"`; the band's `Open Data`; the Overview's `See all ... developments`; the Related card's `See all links`.
- Direct URLs `/entities/<id>`, `/entities/<id>/developments`, `/entities/<id>/related` and `/entities/<id>/data` (topics under `/topics`); hash links such as `/entities/<id>/related#could-affect` or `#developments` scroll to that section once it renders.

## Driving it with verify

Preconditions:

- `pnpm -C web verify doctor` is ok.
- `S` below means `agent-browser --session $VERIFY_SESSION` (default `im-verify`).
- Harness targets: `verify open page <subject>`, `page-developments <subject> [query]` (the query sets the feed's period and facets), `related <subject>`, `data <subject>`.
- Good subjects: `dbs` (brief with concerns, Could affect), `financial-regulation` (a topic: brief, the top 5 full width, no Related card or tab), `banking` (a group, which has no page). Counts change with each weekly run; read them from the API.

- **Open the page.** `pnpm -C web verify open page dbs`, `S snapshot -i`: `heading "DBS" [level=1]`, `button "Add to watchlist"` (or `On watchlist`), `link "OCBC"`, `link "UOB"`, `link "Overview"`, `link "Developments"`, `link "Related"`, `link "Data"`, `region "This week"`, `link "Open Data"`, `region "Top DBS developments"`, `region "Related to DBS"`, and no Could affect.
- **Add to watchlist.** Click it: it reads `On watchlist` and `verify state show` has `dbs` in `watchlist`; Recently viewed starts with DBS.
- **Brief.** `verify api /subjects/dbs/page` has `brief.state`, `brief.concerns[]` (`label`, `text` and the `development_ids` behind it, which the page does not show) and `upcoming`. The brief holds no development links: `agent-browser --session im-verify eval '(() => document.querySelectorAll(".ov-band-main a").length)()'` is 0. Briefs are built only from developments that pass the source gate, and mark single-outlet ones for the model to hedge (`reported by one outlet`).
- **Could affect.** Click the Related card's `link "See all links"`: `/entities/dbs/related` opens with `aria-current="page"` on Related and `region "Could affect DBS"` first, under `Exposure` and `Ownership and structure`; each row's level equals `level` in `verify api /subjects/dbs/impacts`. Click a link group's `Show N more`; it goes and the rows grow. Click a title: the popup opens over the tab; Esc returns to `/entities/dbs/related` at the same scroll.
- **Top developments.** Its rows are the first 5 of `verify api "/developments?subject_id=dbs&days=30"`. Click `See all <n> developments this month`: `/entities/dbs/developments` opens with `aria-current="page"` on Developments and `n` rows in the same order; `n` leaves out unconfirmed developments, and `Show unconfirmed` on the tab adds them.
- **Tabs.** Click `link "Related"`, `link "Developments"`, `link "Data"` in turn: each URL changes and `aria-current="page"` follows; `link "Overview"` returns, and Back steps through them.
- **Empty Related.** `/topics/financial-regulation/related` lands on `/topics/financial-regulation` with no Related tab.
- **Old URLs.** `/monitors/dbs/related` lands on `/entities/dbs/related`, `/monitors/dbs/trend#indicators` on `/entities/dbs/data#indicators`, `/monitors/financial-regulation` on `/topics/financial-regulation`, `/topics/dbs/developments?days=7` on `/entities/dbs/developments?days=7`.
- **Filtered tab and group.** Open `verify open page-developments dbs "days=7&priority=High"`: `This week` is pressed, `Priority: High` shows and only High rows remain. `/monitors/singapore-banks`, `/entities/singapore-banks/developments` and `/entities/singapore-banks/data` land on `/entities?group=singapore-banks`, listing DBS, OCBC and UOB.
- **Error.** `/entities/no-such-subject` shows `This page could not be loaded.` and is not recorded in Recently viewed.
- **Proof.** `pnpm -C web verify shot <run>/<step>` per state, plus the `verify api` readback.

## Gotchas

- The page scrolls inside `main.main`, not the window: `S eval '(() => document.querySelector("main.main").scrollTo(0, 800))()'`, or scroll to a section: `document.getElementById("could-affect")`.
- `agent-browser snapshot` does not print `aria-current`. Read it with `S get attr @<ref> aria-current` or from the DOM.
- The app is light mode only; `agent-browser set media dark` changes nothing.
- The app shell keeps a 1024px minimum width; at 1024 the band's figures go under the brief, the top 5 and the Related card stack, and nothing scrolls sideways.
- Data tab internals are in data.md.
