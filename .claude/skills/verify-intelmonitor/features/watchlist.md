# Watchlist and views

A view decides which watchlist the sidebar shows.
The Personal view's watchlist is the viewer's own: it starts as MAS, changes through Add to watchlist on a page head and the bookmark on a list row, and an emptied one stays empty.
Each of the five function views (Financial Supervision, FinTech & Technology, Markets & Financial Sector Development, Economic Policy, Cross-cutting Functions) shows a fixed preset from `GET /functions`, with MAS in every one.
Add to watchlist always changes the Personal watchlist, whatever the view.
Home (`/`) opens the alphabetically first entity on the current view's watchlist, else its first topic, else the Entities list; a first visit lands on MAS.
All of it lives in this browser only, in one key, `localStorage["im.state.v1"]` (`web/lib/viewerState.ts`), with the fields `watchlist`, `recent` and `view`, each written only once the viewer acts; nothing reaches the server.

## Sub-features

- `first-visit`: empty storage, `/` lands on `/entities/mas` in the Personal view with MAS on the watchlist and in Recently viewed; storage then holds `{"recent":["mas"]}` only.
- `home`: `/` follows the current watchlist (first entity by name, else first topic, else `/entities`), and keeps its query, so `/?development=<id>` opens the popup over that page.
- `add-head`: an entity or topic page's head has `button "Add to watchlist"`; once on, it reads `On watchlist` (in a function view, `On Personal watchlist`) and `Remove from watchlist` on hover, and a click removes it.
- `add-row`: each list row has `button "Add <name> to watchlist"` (tooltip `Add to watchlist`), then `button "Remove <name> from watchlist"` (tooltip `On watchlist`, or `On Personal watchlist` in a function view), `aria-pressed` following.
- `remove`: the sidebar's x on a Personal entry ([sidebar](sidebar.md)); removing MAS keeps it out after a reload.
- `views`: the switcher lists Personal and the five functions; a function view's watchlist is the preset, entities then topics, locked; the view is remembered across visits.
- `state`: `im.state.v1` per field: `watchlist` (Personal ids; missing reads as MAS), `recent` (newest first, at most 5) and `view` (`personal` or a function id). A field of the wrong type reads as its default and is overwritten on its next write; ids that left the seed and group ids are hidden and dropped on the field's next write; a view that names no function reads as Personal. The retired `im.sidebar.closed` key is removed on load.
- `tabs`: a change in one tab shows in every other tab of the same browser without a reload (the `storage` event).
- `blocked`: when `localStorage.setItem` throws, changes still work in the tab from memory, the first watchlist change shows one notice, `This browser blocks storage, so watchlist changes last only until this tab closes.`, and a reload starts from the defaults.

## How to get to it (user POV)

- The head of any entity or topic page, and the bookmark column of the Entities and Topics lists.
- The sidebar's Watchlist entries and the view switcher at its bottom left.
- `/` and the `IntelMonitor` brand link.

## Driving it with verify

Preconditions:

- `pnpm -C web verify doctor` is ok.
- Start from a known state: `pnpm -C web verify state clear` (a first visit) or `pnpm -C web verify state set '{"watchlist":["mas","dbs"],"view":"personal"}'`; both end on the Entities list.

- **First visit.** `verify state clear`, `verify open home`: the URL is `/entities/mas`, `button "View: Personal"`, Watchlist `MAS`. `verify state show` reads `{"recent":["mas"]}`.
- **Add from a head.** `verify open page dbs`, click `button "Add to watchlist"`: it reads `On watchlist`; `ab hover` it to read `Remove from watchlist`. The sidebar Watchlist reads DBS, MAS and `verify state show` has `"watchlist":["mas","dbs"]`.
- **Add from a row.** `verify open entities`, click `button "Add OCBC to watchlist"`: the button becomes `Remove OCBC from watchlist` with `aria-pressed=true`; on `verify open topics`, `Add Banking & Lending to watchlist` adds a topic under the hairline.
- **Home.** With `watchlist` `["mas","dbs"]`, `verify open home` lands on `/entities/dbs`; with `[]`, on `/entities`.
- **Function view.** Pick `Financial Supervision` in the switcher: the Watchlist is the preset from `verify api /functions`, locked, and home opens DBS. `verify open page sgx`, click `Add to watchlist`: it reads `On Personal watchlist` and the preset does not change; switching back to Personal shows SGX on the Personal watchlist. A reload keeps the view.
- **Two tabs.** `ab tab new <url>` opens a second tab in the same session; an add, a removal or a view change in one shows in the other without a reload. `ab tab t1` returns.
- **Bad stored state.** `verify state set '{"watchlist":["cyber-threats","banking","dbs"],"recent":42,"view":"no-such-view"}'`: the Watchlist shows DBS only, Recently viewed is empty (on the Entities list, which records nothing), the view is Personal. Adding OCBC stores `"watchlist":["dbs","ocbc"]`.
- **Blocked storage.** A separate session with an init script whose `Storage.prototype.setItem` throws for `localStorage`: `agent-browser --session im-blocked --init-script <file> open <url>`. The first watchlist change shows the notice once; a reload starts from the defaults.

## Gotchas

- The app reads storage once per load; edit it with `verify state set`, which ends on the Entities list, or reload after an `ab eval`.
- Opening an entity or topic page, through `verify open` too, records it in Recently viewed; start from `verify open entities` when a recipe needs `recent` untouched.
- Groups have no page and are never on a watchlist.
- The notice shows once per tab; after it, further changes that cannot be saved stay silent.
- An init script runs on every load, including a new page's `about:blank`, which has no storage; guard it before touching `sessionStorage` or `localStorage`.
