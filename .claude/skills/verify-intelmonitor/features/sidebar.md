# Sidebar

The left navigation (`navigation "Main"`, `web/components/shell/Sidebar.tsx`) opens with the `IntelMonitor` brand link, then three sections, and in its foot two text links over the view switcher.
Recently viewed lists the last 5 entity or topic pages opened in this browser, newest first, shared by every view.
Watchlist lists the current view's watchlist: entities, a hairline, then topics, each alphabetical.
All links to the Entities and Topics list pages with their counts.
Help and Data sources open their dialogs; the view switcher at the bottom left picks the Personal view or one of the five function views.
What the sidebar shows lives in this browser only ([watchlist](watchlist.md)); long names wrap and nothing is cut off.

## Sub-features

- `recent`: `region "Recently viewed"`, up to 5 links, newest first, no repeats; `Empty` before any page is opened. A list page, a failed page and the popup are not recorded.
- `watchlist`: `region "Watchlist"`, entities, `hr.navrule`, then topics, each alphabetical; `Empty` when the view's list is empty. In the Personal view each entry has `button "Remove <name> from watchlist"`, shown on hover (always on touch screens). In a function view the heading carries a lock and its name reads `Watchlist, fixed`, with no remove buttons.
- `all`: `region "All"` with `link "Entities 89"` and `link "Topics 30"`, to `/entities` and `/topics`.
- `foot-links`: `button "Help"` and `button "Data sources"` on one 44 px row at metadata size, each with a 44 px hit area, opening its dialog ([help](help.md)). Neither dialog fetches until it opens.
- `view-switcher`: `button "View: <view>"` opens `menu "Views"` upward: `menuitemradio "Personal"`, a separator, then the five functions from `GET /functions`, the current one checked. Arrow keys, Home and End move, Enter picks, Esc and Tab close.
- `brand`: `link "IntelMonitor"` goes home ([watchlist](watchlist.md), `home`).

## How to get to it (user POV)

- Present on every page of the shell.
- Each entry opens its page; All opens a list page; the brand link goes home.

## Driving it with verify

Preconditions:

- `pnpm -C web verify doctor` is ok.
- `pnpm -C web verify state clear`, then `pnpm -C web verify open home`, then drive with `ab`.

- **First visit.** After `state clear` and `open home`, `ab snapshot -i` shows `link "MAS"` under `Recently viewed` and under `Watchlist`, `link "Entities 89"`, `link "Topics 30"`, `button "Help"`, `button "Data sources"` and `button "View: Personal"`. `verify state show` reads `{"recent":["mas"]}`.
- **Recently viewed.** Open six pages in turn (`verify open page uob`, `page hsbc`, ...), then `page dbs`: the section lists DBS and the four pages before it, newest first. `verify open page no-such-entity` shows `This page could not be loaded.` and leaves the list as it was.
- **Remove an entry.** `ab hover` the entry, then `ab click` `button "Remove <name> from watchlist"`: the entry leaves and `verify state show` drops its id from `watchlist`.
- **Switch view.** Click `button "View: Personal"`, then `menuitemradio "Financial Supervision"`: the button reads `View: Financial Supervision` and the Watchlist shows the preset from `verify api /functions`, locked and without remove buttons.
- **All.** Click `link "Entities 89"`: `/entities` opens ([list-pages](list-pages.md)).

## Gotchas

- The x is hidden until hover, so `snapshot -i` lists it but a screenshot shows it only on hover.
- The sidebar reads storage once per page load; after editing `im.state.v1` by hand, reload (or use `verify state set`, which ends on the Entities list).
- Recently viewed is the same in every view; only the Watchlist follows the view.
