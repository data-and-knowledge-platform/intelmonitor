# IntelMonitor feature map

Behavior-level inventory of what an analyst can do in IntelMonitor.
Agents use it to decide what to drive and what counts as proof; humans use it as the regression checklist.
Open the one file that matches the change, not the whole map.

## Baseline preconditions

- `pnpm -C web verify doctor` prints `"ok": true`, with `api-types` passing.
- The store holds the weekly runs from 4 Oct to 8 Nov 2026 (`verify api /runs/latest`).
- No sign-in, profile or dashboard: one store serves everyone, and the web app only reads it.
- Browser state lives in the harness browser only, in `localStorage["im.state.v1"]`: start a recipe from a known watchlist, view and Recently viewed with `verify state clear` or `verify state set '<json>'`, and read it back with `verify state show`.
- A first visit (empty storage) lands on MAS's page in the Personal view, with MAS on the watchlist.
- Browser: `ab` in these files means `agent-browser --session im-verify` (or `--session $VERIFY_SESSION`), after `verify open ...`. Handles are ARIA roles and names as `ab snapshot -i` prints them.

## Driving conventions

- Reach the starting page with `verify open`, then act through visible controls and shortcuts.
- The development popup, the list pages' period and filters and the feeds' filters live in the URL (`?development=<id>&scope=subject:<id>`, `?period=4w&group=banking&priority=High&q=`, `?days=`, facets). A reload must keep them.
- The watchlist, Recently viewed and the view live in the browser: a reload and a second tab must keep them, and `verify state show` reads them.
- Wait on visible end states (`agent-browser wait --text ...` or `wait --load networkidle`), not fixed sleeps.
- Read what the page shows back through `verify api <path>`.

## Proof and skip reporting

- Capture the action and the end state: `verify shot` before and after, or `verify record`.
- A state is proven by a second view: the API, `verify state show`, a reload, or another page showing the same state.
- Name every entry point you skipped and why.

## Feature entry contract

Each file has an H1, one paragraph of user-visible behavior, then exactly these H2s in order:

1. `Sub-features`: short IDs, one line each.
2. `How to get to it (user POV)`: every entry point a user has.
3. `Driving it with verify`: `Preconditions:`, then bullets pairing each user action with the exact commands and the observable result.
4. `Gotchas`: traps that waste or invalidate a run.

Keep implementation details out. Name only user paths, stable handles, required state, commands and observable proof.

## Full sweep

Walk top to bottom for a broad regression, or run `pnpm -C web e2e`, which covers the main paths of this map in 19 steps.

## Shell

- [sidebar](sidebar.md): Recently viewed, the view's Watchlist, the All links to the list pages, Help and Data sources, and the view switcher.
- [watchlist](watchlist.md): the Personal watchlist and the five function views: first visit, home, Add to watchlist on page heads and list rows, removal, fixed presets, `im.state.v1`, tab sync, blocked storage.
- [top-bar](top-bar.md): ⌘K search and `Week to <date>`.
- [help](help.md): Help with the labels and shortcuts, and Data sources.

## Lists and pages

- [list-pages](list-pages.md): the Entities and Topics lists: bands, columns, search, priority, group or function, period, ranking, row actions.
- [entity-and-topic-pages](entity-and-topic-pages.md): an entity's or topic's page at `/entities/<id>` or `/topics/<id>`: the head with Add to watchlist, Overview (the summary band, the top 5, the Related card), Developments, Related (Could affect) and Data; old `/monitors` URLs and group links.
- [data](data.md): Data tab for subjects with series: indicators, weekly notes, the series chart, the facts behind each note.

## Developments

- [developments](developments.md): Development feeds on a page's Developments tab and its Overview's top 5: column header, period, facets, rows, New and Updated from the latest run.
- [development-page](development-page.md): The development popup over any page: who it impacts (direct priority, indirect level, route and link reason), connections, sources, close, old links.

## Pipeline

- [pipeline-checks](pipeline-checks.md): Scripted `im check` runs for series, the past run and the loan-to-deposit detector, the e2e walkthrough and `check_endpoints.py`.
