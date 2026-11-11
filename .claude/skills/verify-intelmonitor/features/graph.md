# Graph

The graph view draws one subject's neighbourhood on a canvas and lists the same nodes beside it.
It opens at 1 hop with every node and link type over all time, and it can expand to 2 hops or filter by node type, link type and how recent developments are.
Nodes new or updated this run carry a ring on the canvas and `New:` in the list.
Clicking an entity or topic node opens its page, and a group node the Entities list filtered to that group (groups have no page); clicking a development opens its popup over the graph; closing it returns to the graph.
Each development is coloured by its theme (one Claude Haiku call per development in the run's graph step) and pulled into that theme's sector, labelled on the canvas; sectors sit clockwise from the top in the order Regulation, Digital assets, Technology and AI, Strategy, Earnings and markets, Macro and geopolitics.
The layout animates until it settles, then the camera fits the graph.
Dragging a node carries it and its neighbours; on release it floats back and the layout settles again.
No control links here: the page is reachable by URL only (`/entities/<id>/graph` or `/topics/<id>/graph`). Its head's `Open page` link goes back to the subject's page.
Relationship edges carry the link's type and, in their detail, both strengths (`from_to`, `to_from`) and its reason; IMPACTS edges carry the indirect level and the strength.

## Sub-features

- `head` `heading [level=1]` with the subject name, `link "Open page"`.
- `hops` `group "Hops"`: `button "1 hop"`, `button "2 hops"`.
- `nodes-filter` `button "Nodes: All"` opens `group "Nodes shown"` with `checkbox "Topics"`, `"Groups"`, `"Entities"`, `"Linked parties"`, `"Developments"`; the last checked box is disabled; the button summarises (`Nodes: 4 of 5`, `Nodes: Topics, Groups`). Escape or an outside click closes it.
- `links-filter` `button "Links: All"` opens `group "Links shown"` with `checkbox "Affects directly"`, `"Affects indirectly"`, then the ten link types: `"Holds a stake in"`, `"Regulated by"`, `"Operates in"`, `"Exposed to"`, `"Depends on"`, `"Member of"`, `"Takes part in"`, `"Partner of"`, `"Listed on"`, `"Belongs to sector"`.
- `window` `group "Developments from"`: `All time`, `7 days`, `14 days`, `30 days`.
- `canvas` `[data-testid=graph-canvas]`, `image "Graph of <n> nodes and <m> links around <Subject>"`, `data-layout` `running` or `settled`, `data-hovered=<node id>` under the pointer, `data-dragging=<node id>` while a node is dragged (the layout reads `running` until it cools); `button "Fit graph to view"`.
- `regions` the theme labels over the canvas, `.graph-regions [data-region=<theme>]`, one per theme in view.
- `legend` the node types in view other than themed developments, `New or updated this run`, and `<n> lower-priority developments not shown` when the API truncated.
- `in-view` `region "In view"` (`[data-testid=graph-nodes]`): `<n> nodes, <k> new`, H3s `<Type>, <h> hop(s) <count>`, with developments under their theme instead (`Digital assets, 1 hop 7`), one link per node (`[data-node=<id>]`, developments `dev:<id>`), `New: ` prefix and a `High` badge on nodes with a High development. Hovering a row highlights its node and neighbours on the canvas.
- `open` entity or topic node or row: `/entities/<id>` or `/topics/<id>`; group node or row: `/entities?group=<id>`; development node or row: the popup, `?development=<id>&scope=subject:<center>`.

## How to get to it (user POV)

- Direct URL `/entities/<id>/graph` or `/topics/<id>/graph` (`pnpm -C web verify open graph <subject>`); `/monitors/<id>/graph` redirects there.

## Driving it with verify

Preconditions:

- `pnpm -C web verify doctor` is ok.
- `S` below means `agent-browser --session $VERIFY_SESSION` (default `im-verify`).
- Node counts come from `verify api "/graph?center=<id>&hops=1"` (`nodes`, `edges`, `truncated_developments`, `has_new`); the canvas label reads the same `nodes` and `edges` counts.
  The counts below are the 8 Nov run in the rebuilt store (read 11 Nov); read today's from the API before asserting them. A topic links to no subject, so its graph is its developments alone.
- Read only: nothing here writes to the store.

- **Open.** `pnpm -C web verify open graph OCBC`, `S wait '[data-testid=graph-canvas][data-layout=settled]'`. The URL ends `/entities/ocbc/graph`; the canvas reads `Graph of 104 nodes and 320 links around OCBC`; `In view` has `Groups, 1 hop 1`, `Entities, 1 hop 27`, `Linked parties, 1 hop 35`, then the developments by theme (`Regulation, 1 hop 13`, `Technology and AI, 1 hop 7`, `Strategy, 1 hop 3`, `Earnings and markets, 1 hop 11`, `Macro and geopolitics, 1 hop 6`), and the legend `137 lower-priority developments not shown`.
- **2 hops.** `S find role button click --name "2 hops"`. The canvas goes `running` and reads `Graph of 285 nodes and 1282 links around OCBC`. The list adds `Topics, 2 hops 28`, `Groups, 2 hops 9`, `Entities, 2 hops 46`, `Linked parties, 2 hops 98`, and the legend reads `2196 lower-priority developments not shown`. On 11 Nov it had not settled after 25 s and the harness browser's renderer stayed at 100% CPU (see Gotchas); use a smaller subject for the 2-hop recipes that need `settled`.
- **Node types.** `S find role button click --name "Nodes: All"`, `S snapshot -i` (five checked checkboxes), `S uncheck @<checkbox "Developments">`, `S press Escape`. The button reads `Nodes: 4 of 5`, `S get count '[data-node^="dev:"]'` is 0, and the canvas reads `Graph of 64 nodes and 100 links around OCBC` (204 nodes and 674 links at 2 hops). Check the box again to restore.
- **Link types.** `Links: All`, uncheck `Affects directly` and `Affects indirectly`, Escape. The development nodes go, because developments join the graph only through those links.
- **Window.** Click `button "7 days"`. At 1 hop, OCBC reads `Graph of 104 nodes and 243 links` and the legend `1 lower-priority development not shown`; at 2 hops, 281 nodes and 1102 links. `All time` restores.
- **New developments highlighted.** The legend shows `New or updated this run`; new rows start `New:` (`link "New: Singapore banks"`), and `In view` counts them (`<k> new`). The count excludes the centre node, so it is one below the API's `has_new` total when the centre is new.
- **Click a subject node on the canvas.** Take a screenshot (`S screenshot <file>`) to find a node, then `S mouse move <x> <y>` and read `S eval 'document.querySelector("[data-testid=graph-canvas]").dataset.hovered ?? "none"'` until it names a node other than the centre. Then `S mouse down left` and `S mouse up left`. With OCBC at 1 hop, `singapore-banks` was at (669, 368) in one layout (coordinates vary, so measure each time): the URL became `/entities?group=singapore-banks`, the Entities list filtered to that group; an entity node opens `/entities/<id>`.
- **Click from the list.** `S click '.graph-list [data-node="banking"]'` opens `/entities?group=banking`. `S click '[data-node^="dev:"]'` opens the popup (`/entities/ocbc/graph?development=<id>&scope=subject%3Aocbc`); `S press Escape` returns to `/entities/ocbc/graph`.
- **Pointer on the canvas.** With agent-browser 0.38, `S mouse move` reaches sigma's mouse canvas but never sets `data-hovered`, so the hover, click-on-canvas and drag recipes below do not work through it (checked 5 Oct 2026). Drive them with Playwright instead, as the walkthrough's `findNode` does: a scratch script using `chromium` from `web/node_modules/@playwright/test` hovered, dragged and settled on DBS and OCBC.
- **Drag a node.** Find a node as in the click recipe, then `S mouse down left`, two or three `S mouse move` steps more than 3 px away, and read `S get attr '[data-testid=graph-canvas]' data-dragging` (the node id) and `data-layout` (`running`). `S mouse up left` leaves the URL unchanged, clears `data-dragging`, and `data-layout` returns to `settled` within a few seconds. A press that moves 3 px or less is a click.
- **Hover sync.** `S mouse move` onto a list row sets the canvas's `data-hovered` to that node's id.
- **Proof.** `pnpm -C web verify shot <run>/<step>` after each settle; the ARIA file carries the canvas label and the list.

## Gotchas

- Filter changes keep the previous graph on screen while the new one loads, and that graph still reads `settled`. Waiting for `data-layout=settled` right after a click can return before the new data lands. Wait for the canvas label to change (or `wait --load networkidle`) first, then for `settled`.
- Hops, filters and window live only in page state. Back, reload, or leaving and returning resets to 1 hop, all nodes and links, all time.
- The canvas is WebGL, so nodes have no ARIA handles. Find them by pointer and `data-hovered`, or use the `In view` links. The e2e's `findNode` spirals out from the centre the same way.
- Run agent-browser commands one at a time per session. A leftover background loop moving the mouse made a canvas click land elsewhere, and concurrent or killed commands left the daemon returning `daemon may be busy or unresponsive` with the page renderer at 100% CPU. Only killing that session's renderer and closing the session recovered it.
- Feed `agent-browser` from `</dev/null` inside `while read` loops; it reads stdin and swallows the loop's input.
- `S hover <selector>` on an `In view` row did not set `data-hovered` in one try; `S mouse move` to the row's box did.
- At 1 hop, an entity's topics are often missing because they link through its group; they appear at 2 hops.
- agent-browser's wheel does not scroll this app; dispatch a `WheelEvent` from `S eval` to test wheel handling.
- The last checked box in a filter menu is disabled; the API reads an empty filter as all.
- Themes come from the pipeline's graph step. A run built before themes existed has none: its developments keep the single development colour, no sector labels show, and the list groups them as `Developments`.
