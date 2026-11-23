# List pages

`/entities` and `/topics` list every entity (89) or topic (30), ranked by their developments in a period (`web/components/lists/ListPage.tsx`, from `GET /lists/{entities|topics}?period=week|4w|3m`).
A facet column on the left holds search, Priority, Group (Entities) or Function (Topics) and Period.
The table is banded High, Medium, Low and `No developments` by each row's top priority, the API's ranking (High count, then Medium, then name) kept inside each band.
Counts use direct priority only; indirect impacts never count.
Search, priority, group or function and the facet counts run in the browser over the loaded rows; the period refetches and keeps the previous rows on screen until the new ones land.
The period and filters live in the URL (`?period=4w&group=banking&priority=High&q=dbs`), so a reload, Back and a shared link keep them.
Light mode only; at 1024 px nothing scrolls sideways.

## Sub-features

- `head`: `heading "Entities"` or `"Topics"` [level=1], the row count (`[data-testid=list-count]`: `89`, or `12 of 89` when filtered) and the period's span (`.lp-since`, `2 to 8 Nov`): from the day after the API's `since` to the run's date.
- `bands`: `tbody[data-band=High|Medium|Low|none]`, each headed by its name and row count.
- `columns`: name (a link to the page), first group (Entities) or function (Topics), High, Med and Low counts for the period, the top development's headline (two lines at most; opens the popup over the list with the row's priority) with its topic under it on Entities only, New (the latest run's new developments, whatever the period), the latest date, and the bookmark ([watchlist](watchlist.md)).
- `search`: `searchbox "Search"` narrows rows by name, as you type, into `?q=`.
- `priority`: `group "Priority"`: `Any <n>`, `High <n>`, `Medium <n>`, `Low <n>` by top priority; one at a time, a second click clears.
- `group`: `group "Group"` (Entities): every group an entity is in, by name, each matching any of an entity's groups (`?group=<id>`).
- `function`: `group "Function"` (Topics): the five functions in seed order (`?function=<id>`); it does not follow the current view.
- `period`: `group "Period"`: `This week` (from the previous run's date, the default and kept out of the URL), `Last 4 weeks` (`period=4w`), `Last 3 months` (`period=3m`); no counts.
- `facet-counts`: each facet counts the rows that match every other filter.
- `empty` and `error`: `No matches` when the filters leave nothing; `This list could not be loaded.` when the API fails.
- `group-links`: a group's name in search and an old group URL open `/entities?group=<id>`.

## How to get to it (user POV)

- The sidebar's `Dashboards` section: `link "Entities 57"`, `link "Topics 30"`.
- Home's `Manage your watchlist` link and Explore cards ([home](home.md)).
- A group link anywhere ([entity-and-topic-pages](entity-and-topic-pages.md)).

## Driving it with verify

Preconditions:

- `pnpm -C web verify doctor` is ok.
- Expected rows: `pnpm -C web verify api "/lists/entities?period=week"` (`since`, `run`, `rows[]` with `counts`, `top`, `new`, `latest`, `groups` or `function`).

- **Open.** `pnpm -C web verify open entities`: 89 rows (`ab get count ".lp-table tbody tr[data-id]"`) in the API's order within each band; each row's cells equal its API row.
- **Filter.** Click `button "Banking <n>"` in `group "Group"`, then `button "High <n>"` in `group "Priority"`: the URL gains `group=banking&priority=High`, the rows are the API's rows in that group with a High top development, and the count reads `<n> of 89`.
- **Search.** `ab fill` the `searchbox "Search"` with `bank`: only rows whose name contains it remain and the URL gains `q=bank`.
- **Period.** Click `button "Last 4 weeks"`: one request to `/lists/entities?period=4w`, the URL gains `period=4w`, the rows follow; the filters stay.
- **Reload.** `ab reload`: the URL, the pressed buttons and the rows are unchanged.
- **Topics.** `pnpm -C web verify open topics`: 30 rows, a Function column, no topic under a headline; `group "Function"` lists Any and the five functions; one leaves only its topics.
- **Headline.** Click a row's headline: the popup opens over the list (`?development=<id>&scope=subject:<row id>`); Esc returns to the list with focus on the headline.
- **Bookmark.** See [watchlist](watchlist.md).
- **Narrow.** `ab set viewport 1024 800`: `document.documentElement.scrollWidth` is 1024 and names are not cut.

## Gotchas

- Headlines are clamped to two lines by design (Mock C); the popup shows them whole.
- `snapshot -i` names facet buttons with their counts (`High 3`); match on the start.
- A list page is not recorded in Recently viewed.
- Filters replace the history entry; Back leaves the page.
