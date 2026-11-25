# Home

Home (`/`, `web/components/home/Home.tsx`) is the current view's watchlist at a glance, after the team's design of 23 Nov 2026.
`Your monitoring scope` counts the watchlist's entities, topics and sectors (the groups its entities are in), lists them as chips, and links `Manage your watchlist` (`Browse entities` in a function view) to the Entities list.
`New developments` has a row per watchlist entity and topic that has a list row, in the lists' ranking (High, Medium, then name): the name, `<n> developments` this week, a bar and counts by priority, the top development's headline when it is High (opening its popup), `Topics covered` (an entity's topics among the week's developments, the first three then `<n> more`), `Needs attention` when any is High else `Nothing of note`, and the period.
The figures are `GET /lists/entities` and `/lists/topics` with `period=week`; topics covered is `topics` on each row.
`Explore the dashboard` links the Entities and Topics lists.
Home writes nothing to the browser and records nothing in Recently viewed.

## Sub-features

- `home-scope`: three tiles, the chips and the manage link.
- `home-rows`: `.home-table` rows matching the watchlist's list rows, counts as the API gives them, the red left edge on a row that needs attention.
- `home-empty`: an empty watchlist says `Your watchlist is empty.` and has no rows.

## How to get to it (user POV)

- `/`, the brand link and `link "Home"` in the sidebar's Dashboards.

## Driving it with verify

- `pnpm -C web verify state set '{"watchlist":["mas","dbs","banking-lending"],"view":"personal"}'`, then `pnpm -C web verify open home`.
- **Rows.** Each row's `.home-total` and `.home-counts` equal the row's `counts` in `verify api "/lists/entities?period=week"` (or `/lists/topics`).
- **Headline.** Click a row's headline: the popup opens over `/` scoped to that row.
- **Empty.** `verify state set '{"watchlist":[]}'`, `verify open home`: `Your watchlist is empty.`
