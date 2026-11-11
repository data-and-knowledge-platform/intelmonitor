# Development popup

A development opens in a popup over the page it was opened from (80% of the screen, at least 900 px wide).
The URL carries it: `?development=<id>&scope=subject:<id>` on the current page's URL, the scope naming the subject in view whose priority it shows.
The header holds the labels line (the priority badge for the subjects in view, with `For OCBC` when the subjects disagree, `Adverse event` when it applies, the tone, the source types as a jump link to the sources, the category and the date or date span), the headline (heading level 2, focused on open), its key points, and the close button (`Close development`). There is no Back button.
Below, two columns that each scroll inside the popup: on the left `Who it impacts` over `Connections`; on the right the sources, one list under the heading `Sources`.
`Who it impacts` lists the subjects it is about (priority, adverse, tone, `Took the action` for actors), then `Could also be affected, through their links`: each entity with its route in plain words, `Could hurt`, `Could help` or `Mixed effect`, why, and one evidence line per stored link (`Public disclosure` or `Press report` with a hover definition, the source with page as a link, the size or `Size not disclosed`, `Figures as of` or `Source dated` with `over N years old` when it is three years or more, and `Link last checked`).
`Connections` has one claim line per route and a graph snippet: `This news`, the thing it is about, any company in between, and each entity, edges labelled (`44.4% of loans`, `cloud services`, `takes part`). Without indirect impacts it says `No stored link carries this news to another tracked entity.`
The sources are news, official and research documents (and a detector's series), each with its highlighted excerpts.
Close, Esc and a backdrop click return to the page underneath with its URL, filters and scroll, and focus returns to the link that opened it. Opening it changes nothing on the server.
Under 1024 px the popup fills the screen less 16 px and the columns stack in one scroll (the app shell itself has a 1024 px minimum).

## Sub-features

- `dp-open`: every development link (feed rows, top developments, Upcoming dates, Could affect, linked institutions, ⌘K results, the Data tab's indicator rows and note cites, the graph) opens the popup over the current page with that page's scope.
- `dp-close`: the close button, Esc and a backdrop click go back to the history entry underneath (same URL and scroll); browser Back does the same and Forward reopens it; on a fresh load of a popup URL, closing drops the parameters and stays on the page.
- `dp-focus`: the title has focus on open, Tab and Shift+Tab wrap inside the popup, and focus returns to the opener on close (the search field for a ⌘K result).
- `dp-priority`: the priority shown is for the subjects in view, not the highest over every subject.
- `dp-who`: direct subjects, then indirect impacts with route, direction, why and evidence.
- `dp-graph`: `Connections`, its claim lines and the snippet; a plain line without indirect impacts.
- `dp-sources`: one list with highlighted excerpts; the `Sources` heading stays at the top as the column scrolls.
- `dp-legacy`: `/developments/<id>?subject=<id>` redirects to `/monitors/<id>?development=<id>&scope=subject:<id>`, and `/developments/<id>` alone to MAS's page with the popup open; an old `?evidence=<id>` link on any page opens the popup there.
- `dp-error`: an unknown id shows `This development could not be loaded. It may have left the latest update.` with the close button.

## How to get to it (user POV)

- A feed row on a monitor page's Developments tab or its Overview's top 5 (see [developments](developments.md)); on the Overview also the summary band's `Next:` date and a Related card list row (the brief itself links no developments); on the Related tab a `Could affect` title and a linked institution's week line (see [monitor-page](monitor-page.md)).
- The Data tab's `View the development` rows and note cites, a graph development node, ⌘K development and document results.

## Driving it with verify

Preconditions:

- `ab` means `agent-browser --session im-verify`; `@ref` is the ref `ab snapshot -i` prints.
- `pnpm -C web verify open development <id> [subject]` opens the popup over that subject's page (or over Home, which lands on MAS's page).
- Find a development with indirect impacts: `pnpm -C web verify api "/developments?subject_id=<id>&days=30"` and pick a row whose `also_affects` is not empty.

- **Open from the feed.** On a Developments tab, scroll down and click a row's title link. The URL gains `development=<id>&scope=subject%3A<id>`, `dialog "<headline>"` shows with heading level 2 focused and `Close development`; no Back, Dismiss, Not relevant or Restore button.
- **Size.** `ab eval '(() => document.querySelector("dialog.dp-pop").getBoundingClientRect().width)()'` is 80% of the window (1152 at 1440, 900 at 1024).
- **Priority for the scope.** Compare `pnpm -C web verify api "/developments/<id>?subject_id=<a>"` and `?subject_id=<b>` for a development whose `direct_subjects` disagree; the popup opened from each monitor page shows that subject's priority and `For <name>`.
- **Who it impacts.** `ab snapshot` shows heading `Who it impacts`, `This development is about`, `Could also be affected, through their links`, each entity link, route and direction word. `ab get text ".imp-ev"` shows the evidence lines.
- **Connections.** `ab snapshot -c` shows heading `Connections` and `image "<claim>."`; `ab get count ".snippet .n.ent"` equals the number of impact rows.
- **Sources.** `ab get text ".source blockquote mark"` equals the API's highlight text.
- **Close.** Note `main.main`'s `scrollTop`, open a row, press Escape: the feed's URL comes back exactly, `scrollTop` is unchanged and the row's link has focus. Reopen and click the close button, then the backdrop: same. Reopen and use `ab back`: same; `ab forward` reopens it. Open a popup URL fresh and click the close button: the parameters go and the page stays.
- **Legacy links.** `ab open <web>/developments/<id>?subject=dbs`: the URL becomes `/monitors/dbs?development=<id>&scope=subject%3Adbs` with the popup open; `/developments/<id>` becomes `/monitors/mas?development=<id>`. `/monitors/dbs?evidence=<id>` becomes `/monitors/dbs?development=<id>&scope=subject%3Adbs`.
- **Proof.** `pnpm -C web verify record start <run>/<name>` before the first click, `record stop` at the end state.

## Gotchas

- The passage and `<mark>` are not in `snapshot -i` text; read them with `ab get text`.
- A title link that wraps onto two lines can have its centre in the gap between them, so `ab click` on it lands on the list item; focus it and press Enter instead.
- `scope` is URL-encoded in the address bar (`scope=subject%3Adbs`).
