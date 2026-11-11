# IntelMonitor feature map

Behavior-level inventory of what an analyst can do in IntelMonitor.
Agents use it to decide what to drive and what counts as proof; humans use it as the regression checklist.
Open the one file that matches the change, not the whole map.

## Baseline preconditions

- `pnpm -C web verify doctor` prints `"ok": true`, with `api-types` passing.
- The store holds the weekly runs up to 8 Oct 2026 (`verify api /runs/latest`).
- Home opens MAS's page; there is no onboarding, profile or dashboard.
- One store serves everyone, and the web app only reads it.
- Browser: `ab` in these files means `agent-browser --session im-verify` (or `--session $VERIFY_SESSION`), after `verify open ...`. Handles are ARIA roles and names as `ab snapshot -i` prints them.

## Driving conventions

- Reach the starting page with `verify open`, then act through visible controls and shortcuts.
- The development popup and feed filters live in the URL (`?development=<id>&scope=subject:<id>`, `?days=`, facets). A reload must keep them.
- Wait on visible end states (`agent-browser wait --text ...` or `wait --load networkidle`), not fixed sleeps.
- Read what the page shows back through `verify api <path>`.

## Proof and skip reporting

- Capture the action and the end state: `verify shot` before and after, or `verify record`.
- A state is proven by a second view: the API, a reload, or another page showing the same state.
- Name every entry point you skipped and why.

## Feature entry contract

Each file has an H1, one paragraph of user-visible behavior, then exactly these H2s in order:

1. `Sub-features`: short IDs, one line each.
2. `How to get to it (user POV)`: every entry point a user has.
3. `Driving it with verify`: `Preconditions:`, then bullets pairing each user action with the exact commands and the observable result.
4. `Gotchas`: traps that waste or invalidate a run.

Keep implementation details out. Name only user paths, stable handles, required state, commands and observable proof.

## Full sweep

Walk top to bottom for a broad regression, or run `pnpm -C web e2e`, which covers the main paths of this map in 9 steps.

## Shell

- [sidebar](sidebar.md): Entities with groups as headings, and Topics.
- [top-bar](top-bar.md): ⌘K search and `Week to <date>`.
- [help](help.md): `?` help with the labels and shortcuts.

## Monitors

- [monitor-page](monitor-page.md): A topic's or entity's page: Overview (the summary band with the brief, Key data figures and the next date, the top 5, the Related card), Developments, Related (Could affect, linked institutions, Shared with) and Data; groups list their members.
- [data](data.md): Data tab for subjects with series: indicators, weekly notes, the series chart, the facts behind each note.
- [graph](graph.md): Graph view, reachable by URL only: hops, filters, node click.

## Developments

- [developments](developments.md): Development feeds on a monitor page's Developments tab and its Overview: column header, period, facets, rows, New and Updated from the latest run.
- [development-page](development-page.md): The development popup over any page: who it impacts, connections, sources, close, old links.

## Pipeline

- [pipeline-checks](pipeline-checks.md): Scripted `im check` runs for series, past-date runs and the loan-to-deposit detector.
