# Data tab

An entity's or topic's Data tab shows the subject's measured series.
It opens with the Indicators section (rows a detector flagged first, under `Notable changes`), then `Trend`.
`Trend` opens on the latest week: a chart of one series covers the 12 months to the latest update, with the selected week banded and High and Medium developments as ticks along the bottom.
The rail names the charted series' source.
`Before 1 Oct` (the corpus start, `documents_from`) shows the series where no document exists, and the rail lists facts computed from its line.
Series carry no Watch or Stable status, and no focus areas appear on the tab.

## Sub-features

- `tabs` `link "Data"` in the page head's `navigation "Monitor views"`, only for subjects with listed series (see [entity-and-topic-pages](entity-and-topic-pages.md)).
- `indicators` `region "Indicators"`: one row per listed series with latest value and period, change and window, and a 12-month line whose hover shows a `tooltip`. `No data` or `Stale` is the only badge. Flagged rows come first and link to the development that carries the detection, with an accent `View the development` cue under the name.
- `weeks` `region "Trend"`, `group "Week"`: one button per weekly update, newest first (`8 Nov` ... `4 Oct`), then `Before 1 Oct`. The section meta reads `Week to 8 Nov` or `Before 1 Oct`.
- `series` 1 series: its name in bold; 2 or 3: `group "Series"` of buttons by short name; more: `combobox "Series"`. Defaults to the first series.
- `period` `group "Period"`: `12 months`, `3 months`, `1 month` for daily series; monthly series drop `1 month`; quarterly series show no group.
- `chart` `.tr-chart svg` labelled `<series>, <from> to <to>, latest <v> on <date>`; legend `High`, `Medium`; ticks `link "<date>: <n> developments, High, Medium"`.
- `facts` `region "Facts"`: the source and `link "Open source table"`; under Before, meta `Computed` and the first, last, change, low and high before `documents_from`.

## How to get to it (user POV)

- An entity or topic page's `link "Data"` in `navigation "Monitor views"`.
- The Overview summary band's `link "Open Data"`.
- Direct URL `/entities/<id>/data` or `/topics/<id>/data`. The old `/monitors/<id>/trend` and `/monitors/<id>/data` redirect there, keeping their query and hash.
- A subject without series has no Data tab; its `/data` URL shows `<Subject> has no measured series.`

## Driving it with verify

Preconditions:

- `pnpm -C web verify doctor` is ok.
- Check a subject has series with `verify api /subjects/<id>/trend` (`series` not empty). On 8 Nov: `ocbc` and `dbs` (1 series each), `mas` (2 series, a `Series` button group), `sgx` (14 series, a `Series` select; counted 16 Nov). `aws` has none. Hidden entities such as `singapore` have no page, so no Data tab.
- Read only: nothing here writes to the store.

- **Open from the tab.** `pnpm -C web verify open page ocbc`, `ab snapshot -i`, click `link "Data"`. The URL ends `/entities/ocbc/data`; `link "Data"` is `aria-current="page"`.
- **Indicators.** `ab snapshot -s 'section[aria-labelledby="indicators"]' -c`: rows under `Notable changes` first, no `Watch` or `Stable` text. Hover `.ind-line`; a `tooltip` shows a value, a date and the series name.
- **Latest week.** `ab get text 'section[aria-labelledby="trend"] .mon-sec-head .meta'` reads `Week to 8 Nov`.
- **Facts.** `ab get text 'section[aria-labelledby="facts"]'` names the source, with `link "Open source table"`.
- **Earlier week.** Click `button "1 Nov"` in `group "Week"`. The meta reads `Week to 1 Nov` and the band moves to that week.
- **Before collection.** Click `button "Before 1 Oct"`. The meta reads `Before 1 Oct` and the Facts meta `Computed`.
- **Tick opens the development.** Click a tick `link "<date>: ..."`. The URL gains `development=<id>&scope=subject%3Aocbc`. Esc returns to the Data tab at the same scroll.
- **Many series.** `verify open data sgx`: `combobox "Series"`; selecting another series changes the chart label.

## Gotchas

- The series you pick stays picked when you change weeks; only a fresh load returns to the first series.
- The window you pick also persists, and a narrower window re-anchors on each selected week.
- `Before 1 Oct` exists only while the chart's start is before `documents_from`.
- Chart ticks are links with long names; count them with `ab get count .tr-tk`.
- `verify open data <name>` tries an exact id or name, then the first substring match in `/subjects` order; use ids.
- Indicator rows are not interactive unless a detector event backs them, so most rows appear only in the full snapshot, not in `snapshot -i`.
