# Development feeds

A scope's developments in the latest run as a feed: High and adverse first, then other High, then Medium adverse, then other Medium, then the rest, newest first within each group.
Priority and adverse are for the subjects in view, so a development adverse to OCBC only ranks as adverse on OCBC's page.
A source gate (`api/intelmonitor/pipeline/corroboration.py`) decides which developments are shown: 2 or more distinct news outlets (counted by parent domain, so `markets.ft.com` and `ft.com` are one), or 1 reputable outlet (`REPUTABLE_DOMAINS` in `ingest/sources/search.py`), or any official, research or data source (the entity's own site counts as official).
The rest are unconfirmed and left out of every list, count and total by default: feeds, facet and period counts, `See all <n>`, the Related tab and its impacts, the graph, search, and the briefs written from the next run on.
Feeds show only on entity and topic pages: a page's Developments tab (`/entities/<id>/developments` or `/topics/<id>/developments`, section `Developments about <name>`) shows one subject's feed, and its Overview the top 5 rows without headings.
The list pages show only each row's top development ([list-pages](list-pages.md)).
Rows sit in one list under a column header, `Priority`, `Finding`, `Date` (hidden from the accessibility tree and below 640 px), in that order; each row's priority badge is in the Priority column.
Each row has a status column (priority badge High, Medium or Low for the subjects in view, then New or Updated: added or updated in the latest weekly run, shown to everyone and never cleared by opening it), the full wrapping title, one line on who it impacts (`Impacts DBS · By MAS · About Banking regulation · Could also affect UOB`), a facts line (`Unconfirmed` when it is, `High for OCBC` when the subjects in view disagree, `Adverse event` when it applies, the tone, the source counts, the category), and at the right the date and a visible `View sources ›` cue.
The source counts read `3 outlets · 1 official source · 2 research papers · 1 data source`, each part left out when zero; outlets are distinct parent domains, not articles.
The whole row opens the development's popup over the list (see [development-page](development-page.md)).
Above the rows: the period control (Today, This week, This month, All time; This month is the default), the facet buttons Priority, Adverse event, Tone, Category and Source type, each a popover with All first and the values with counts, and the checkbox `Show unconfirmed <n>` while some unconfirmed developments match.
The period, facets and `unconfirmed=true` live in the URL, so a reload and closing a development popup keep them.

## Sub-features

- `feed-open`: the page's Developments tab shows the subject's feed; the Overview's `See all ... developments` opens it.
- `feed-rows`: one ordered list under the `.frow-head` column header, High adverse rows first, then High, Medium adverse, Medium and the rest, newest first in each; a priority filter leaves only its rows.
- `feed-gate`: unconfirmed developments are not listed or counted by default; `Show unconfirmed <n>` (`unconfirmed=true`) lists them among the others, each marked `Unconfirmed`, and adds them to the period and facet counts. The checkbox shows only while `n` is above 0 or it is on.
- `feed-row`: priority badge, New badge or Updated word, full title, impact line, facts line, date and `View sources ›`.
- `feed-period`: the period segments with counts; This month by default, `days=1|7|any` otherwise.
- `feed-facets`: Priority, Adverse event (single choice), Tone, Category and Source type narrow the list; the button reads `Priority`, `Priority: High` or `Priority: 2 selected`; zero-count values are hidden.
- `feed-open-row`: a row adds `development=<id>&scope=subject:<id>` to the list's URL.
- `feed-back`: closing the popup (close, Esc, backdrop or browser Back) returns to the feed with the same URL and scroll.
- `feed-empty`: `No developments in this period. Show this month` (the next wider period that has developments), `No developments yet.` when no period has any, and `No developments match these filters. Clear filters`, or `No developments match these filters in this period. Show this week · Clear filters` when a wider period has matches (the period counts follow the filters).
- `feed-new`: the foot line `New: added in the run of <date>. Updated: new reports in that run.` names the latest run's date (`as_of` of `verify api /runs/latest`).
- `feed-bad-params`: hand-edited facet values the list cannot apply are ignored.

## How to get to it (user POV)

- An entity or topic page: the `Developments` tab, or the Overview's `See all ... developments`.
- There is no cross-subject feed: `/developments` lands on home.

## Driving it with verify

Preconditions:

- `pnpm -C web verify doctor` is ok.
- `ab` below means `agent-browser --session im-verify` (or `--session $VERIFY_SESSION`). `@ref` is the ref `ab snapshot -i` prints for the named control; snapshot again before each click.
- Read current counts with `pnpm -C web verify api "/developments?subject_id=<id>&days=30"` (`matching`, `facets`).

- **Open.** `pnpm -C web verify open page-developments dbs`. Section `Developments about DBS`; group `Period` with `This month <n>` pressed; buttons `Priority`, `Adverse event`, `Tone`, `Category`, `Source type`; the rows are one ordered list under the Priority, Finding and Date header, their badges in the API's order.
- **Period.** `ab click @ref` for button `This week <n>`. URL gains `days=7`; rows equal the API's `matching` for `days=7`. `This month` drops `days`.
- **Facet.** `ab click @ref` for button `Priority`, then checkbox `High <n>`. URL gains `priority=High`, the button reads `Priority: High`, every row's badge is High. Check another value: the button reads `Priority: 2 selected`. Checkbox `All` clears it. `ab press Escape` closes the popover and focus returns to the button.
- **Adverse event.** Button `Adverse event`, radio `Yes <n>` (or `No <n>`): URL gains `adverse=true` and the button reads `Adverse event: Yes`; every row's facts line shows `Adverse event`.
- **Open a row and come back.** With filters set and the list scrolled, `ab click @ref` on a row's title link. The URL is the feed's URL plus `development=<id>&scope=subject%3A<id>`. Click `Close development`: the URL is the feed's URL exactly, filters, rows and scroll unchanged.
- **Reload keeps it.** `ab reload`: URL, button names and rows unchanged.
- **Unconfirmed.** Read `unconfirmed` from `verify api "/developments?subject_id=dbs&days=30"`, then `verify open page-developments dbs`. With `n` above 0, `ab click @ref` on checkbox `Show unconfirmed <n>`: URL gains `unconfirmed=true`, `n` more rows appear in order, each with `Unconfirmed` in its facts line, and the `This month` count grows by `n`. Open one: the popup's labels read `Unconfirmed` and its sources start with `Unconfirmed: not enough independent sources yet.` Uncheck: the URL drops the parameter and the rows go.
- **New and Updated.** Rows with `change: "new"` in the API carry the `New` badge and `change: "updated"` the word `Updated`, whether or not they were opened; the foot line names the latest run's date.
- **Empty.** With a filter that matches nothing: `No developments match these filters. Clear filters`; the button clears the facets, keeping the period. On `Today` with nothing that day: `No developments in this period. Show this week`. With `Adverse event: Yes` on `Today` when only the week has matches: `No developments match these filters in this period. Show this week · Clear filters`; `Show this week` keeps the filters.
- **Overview.** The Overview (`verify open page dbs`) shows `Top DBS developments`: the first 5 confirmed rows in the same order, with badges and no headings.
- **Bad parameters.** `pnpm -C web verify open page-developments dbs "priority=Bogus&tone=x&adverse=maybe&category=bogus&source=foo&days=abc"`: buttons read plain `Priority` and so on, and `This month` is pressed.
- **Proof.** `pnpm -C web verify shot phase1-U1/<step>`; the `.aria.txt` holds the button names and counts.

## Gotchas

- Filters replace the history entry, so Back leaves the page instead of undoing a filter. Opening a development pushes an entry, so Back from it returns to the feed.
- The popover dialog does not appear in `snapshot -i`; its checkboxes and radios do, listed right after the expanded button.
