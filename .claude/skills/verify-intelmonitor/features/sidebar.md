# Sidebar

The left navigation (`navigation "Main"`, `web/components/shell/Sidebar.tsx`) opens with the `IntelMonitor` brand link, then three regions, and in its foot two text links over the view switcher:

- `region "Recently viewed"`: the last 5 entity or topic pages opened in this browser, newest first, shared by every view.
- `region "Watchlist"`: the current view's watchlist, entities, a hairline, then topics, each alphabetical. In the Personal view each entry has `button "Remove <name> from watchlist"`, shown on hover; a function view's heading reads `Watchlist, fixed` (a lock) and has no remove buttons.
- `region "All"`: `link "Entities 89"` and `link "Topics 30"` to the list pages.
- `button "Help"` and `button "Data sources"` on one row at metadata size, each opening its dialog (`web/components/shell/SidebarDialogs.tsx`; see [help.md](help.md)).
- `button "View: <view>"` at the bottom left opens `menu "Views"` upward: `menuitemradio "Personal"`, then the five functions from `GET /functions`.

State lives in this browser only, in `localStorage["im.state.v1"]` (`web/lib/viewerState.ts`): `watchlist`, `recent` and `view`, each written only once the viewer acts.
Long names wrap; nothing is cut off.

## How to get to it (user POV)

- Present on every page of the shell.
- `IntelMonitor` brand link goes home, which opens the first entity on the current watchlist (MAS on a first visit), else its first topic, else the Entities list.
- "Add to watchlist" in a page head and the bookmark on a list row always change the Personal watchlist.

## Driving it with verify

Preconditions:

- `pnpm -C web verify doctor` is ok.
- `pnpm -C web verify open home`, then drive with `agent-browser --session im-verify`.

- **Read the state.** `agent-browser eval '(() => localStorage.getItem("im.state.v1"))()'`. A first visit to `/` stores `{"recent":["mas"]}` only.
- **Start fresh.** `eval '(() => localStorage.clear())()'`, then `open` a page: the module reads storage once per load.
- **Remove an entry.** `hover` then `click` `button "Remove <name> from watchlist"`; the entry leaves and `watchlist` drops its id.
- **Switch view.** Click `button "View: Personal"`, then a `menuitemradio`. Arrow keys move in the menu, Enter picks, Esc closes.
- **Two tabs.** `agent-browser tab new <url>` opens a second tab in the same session; a change in one shows in the other without a reload. `tab t1` returns.
- **Blocked storage.** A separate session with an init script whose `Storage.prototype.setItem` throws: `agent-browser --session im-blocked --init-script <file> open <url>`. The first watchlist change shows one notice; a reload starts from the defaults.

## Gotchas

- Groups have no page and are never on a watchlist; a stored group id or one that left the seed is hidden, and dropped on that field's next write.
- A stored view that names no function reads as Personal.
- The x is hidden until hover (always shown on touch screens), so `snapshot -i` lists it but a screenshot shows it only on hover.
