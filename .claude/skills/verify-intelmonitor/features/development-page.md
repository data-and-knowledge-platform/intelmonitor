# Development popup

A development opens in a popup over the page it was opened from (80% of the screen, at least 900 px wide).
The URL carries it: `?development=<id>&scope=subject:<id>` on the current page's URL, the scope naming the subject in view whose priority it shows.
The header holds the labels line (the priority badge for the subjects in view, with `For OCBC` when the subjects disagree, `Adverse event` when it applies, the tone, the source types as a jump link to the sources, the category and the date or date span), `button "Priority definitions"` beside the badge, the headline (heading level 2, focused on open), and the close button (`Close development`). There is no Back button.
Below, in one scroll, after the team's design of 23 Nov 2026: `Summary` (its key points in a tinted box, or the full title when it has none), `Affected entities <n>`, and `Sources <n>` at the foot.
`Affected entities` shows the topics it is about as chips, then a card per entity it is about (`Main entity`, or `Took the action` for actors, its priority, the rating's reason (`reason` in `direct_subjects`), adverse and tone), then a card per indirect impact (`.aff-imp`: `Potential exposure`): the indirect impacts, each an entity one stored link away from a subject the development is adverse for, at the level the fixed table gives for that subject's direct level and the link's strength in that direction (Medium or Low, never High).
Each impact card shows the entity, its level, its route in plain words (`via its Singapore loans`), the level, the link's reason (one line built from the link, such as `has 12.2% of its loans in Hong Kong, 30 Jun 2026`) and one evidence line for the link (`Public disclosure` or `Press report` with a hover definition, the source with page as a link, the size or `Size not disclosed`, `Figures as of` or `Source dated` with `over N years old` when it is three years or more, and `Link last checked`).
There is no why sentence, direction (could hurt, could help) or confidence; link strength is stored but not shown.
Beside the cards, when it has indirect impacts, `Connection` draws a tree: `This news`, `about` each entity whose links carry it, and under each, as branches off one line, the entities reached, each branch labelled with its link (`44.4% of loans`, `cloud services`, `takes part`). Without indirect impacts the cards take the full width.
The sources are cards two a row: news, official and research documents with their outlet, title, highlighted excerpts (four lines each) and date, and a detector's series on a full row.
Close, Esc and a backdrop click return to the page underneath with its URL, filters and scroll, and focus returns to the link that opened it. Opening it changes nothing on the server.
Under 1024 px the popup fills the screen less 16 px and the cards, the tree and the sources stack in one column (the app shell itself has a 1024 px minimum).

## Sub-features

- `dp-open`: every development link (feed rows, top developments, Upcoming dates, Could affect, ⌘K results, the Data tab's indicator rows and note cites) opens the popup over the current page with that page's scope.
- `dp-close`: the close button, Esc and a backdrop click go back to the history entry underneath (same URL and scroll); browser Back does the same and Forward reopens it; on a fresh load of a popup URL, closing drops the parameters and stays on the page.
- `dp-focus`: the title has focus on open, Tab and Shift+Tab wrap inside the popup, and focus returns to the opener on close (the search field for a ⌘K result).
- `dp-priority`: the priority shown is for the subjects in view, not the highest over every subject.
- `dp-who`: entity cards with their priority and reason, then indirect impact cards with route, level, link reason and evidence, as `verify api /developments/<id>` lists them in `impacts` (`level`, `route`, `subject`, `link.reason`).
- `dp-graph`: `Connection`, the tree from the news through the entities it is about to the entities reached; absent without indirect impacts.
- `dp-sources`: source cards two a row with highlighted excerpts, at the foot.
- `dp-legacy`: `/developments/<id>?subject=<id>` redirects to that subject's page (`/entities/<id>` or `/topics/<id>`) with `?development=<id>&scope=subject:<id>`, and `/developments/<id>` alone to `/` with the popup open, over Home ([home](home.md)); an old `?evidence=<id>` link on any page opens the popup there.
- `dp-error`: an unknown id shows `This development could not be loaded. It may have left the latest update.` with the close button.

## How to get to it (user POV)

- A top development's headline on the Entities or Topics list, over the list with the row's priority (see [list-pages](list-pages.md)).
- A feed row on a page's Developments tab or its Overview's top 5 (see [developments](developments.md)); on the Overview also the summary band's `Next:` date and a Related card list row (the brief itself links no developments); on the Related tab a `Could affect` title (see [entity-and-topic-pages](entity-and-topic-pages.md)).
- The Data tab's `View the development` rows and note cites, ⌘K development and document results.

## Driving it with verify

Preconditions:

- `ab` means `agent-browser --session im-verify`; `@ref` is the ref `ab snapshot -i` prints.
- `pnpm -C web verify open development <id> [subject]` opens the popup over that subject's page, or over the Entities list.
- Find a development with indirect impacts: `pnpm -C web verify api "/developments?subject_id=<id>&days=30"` and pick a row whose `also_affects` is not empty.

- **Open from the feed.** On a Developments tab, scroll down and click a row's title link. The URL gains `development=<id>&scope=subject%3A<id>`, `dialog "<headline>"` shows with heading level 2 focused and `Close development`; no Back, Dismiss, Not relevant or Restore button.
- **Size.** `ab eval '(() => document.querySelector("dialog.dp-pop").getBoundingClientRect().width)()'` is 80% of the window (1152 at 1440, 900 at 1024).
- **Priority for the scope.** Compare `pnpm -C web verify api "/developments/<id>?subject_id=<a>"` and `?subject_id=<b>` for a development whose `direct_subjects` disagree; the popup opened from each page shows that subject's priority and `For <name>`.
- **Affected entities.** `ab snapshot` shows heading `Affected entities`, the topic chips and a card per entity. `ab get text ".aff-imp .imp-route"` and `".aff-imp .imp-dir"` equal `route` and `level` of each `impacts` row of `verify api "/developments/<id>?subject_id=<id>"`, in order; each impact card shows the link's reason and its evidence line.
- **Connection.** `ab snapshot -c` shows heading `Connection`; `ab get count ".cx-branches li"` equals the number of impact cards.
- **Sources.** `ab get text ".source blockquote mark"` equals the API's highlight text.
- **Close.** Note `main.main`'s `scrollTop`, open a row, press Escape: the feed's URL comes back exactly, `scrollTop` is unchanged and the row's link has focus. Reopen and click the close button, then the backdrop: same. Reopen and use `ab back`: same; `ab forward` reopens it. Open a popup URL fresh and click the close button: the parameters go and the page stays.
- **Legacy links.** `ab open <web>/developments/<id>?subject=dbs`: the URL becomes `/entities/dbs?development=<id>&scope=subject%3Adbs` with the popup open; `/developments/<id>` becomes `/?development=<id>`. `/entities/dbs?evidence=<id>` becomes `/entities/dbs?development=<id>&scope=subject%3Adbs`.
- **Proof.** `pnpm -C web verify record start <run>/<name>` before the first click, `record stop` at the end state.

## Gotchas

- The passage and `<mark>` are not in `snapshot -i` text; read them with `ab get text`.
- A title link that wraps onto two lines can have its centre in the gap between them, so `ab click` on it lands on the list item; focus it and press Enter instead.
- `scope` is URL-encoded in the address bar (`scope=subject%3Adbs`).
