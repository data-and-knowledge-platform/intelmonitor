# Sidebar

The left navigation (`navigation "Main"`) opens with the `IntelMonitor` brand link, then `Entities` lists the sidebar groups (the MAS top 50 categories: Banking, Insurance, Asset management, Financial markets, Fintech, Digital assets, Technology, Investment) as collapsible headings with their member count; groups with `sidebar: false` in `subjects.yaml` (Singapore banks, Payment systems, countries, ...) are left out, and `Topics` lists the topics.
The `Entities` and `Topics` headings are buttons (`aria-expanded`) that collapse their whole list; which are closed is kept in localStorage `im.sidebar.closed`, and opening an entity's or topic's page opens its section again.
Long names wrap; nothing is cut off.
There are no dashboards, profile button, Ask link or High counts.

## Sub-features

- `entity-groups` expands a group heading under Entities and opens a member's page; the group holding the current entity opens itself.
- `topics` opens a topic's page.
- `sections` collapses and reopens Entities and Topics.

## How to get to it (user POV)

- Present on every page of the shell.
- `IntelMonitor` brand link goes home, which lands on MAS's page.
- `?` anywhere opens [Help](help.md).

## Driving it with verify

Preconditions:

- `pnpm -C web verify doctor` is ok.
- `pnpm -C web verify open home`, then drive with `agent-browser --session <session>`.

- **Expand a group.** Run `agent-browser snapshot -i -s nav`, then click `button "Banking 12"`. It reports `expanded=true` and member links appear, for example `link "DBS"`. The groups and members match `verify api /subjects` (`type: "group"`, `sidebar: true`, `members`).
- **Open an entity.** Click a member link. URL becomes `/monitors/<id>`, the H1 is its name, and `document.querySelector("nav [aria-current=page]")` is that link.
- **Current group opens itself.** Reload an entity page. Only the group holding it reports `expanded=true`.
- **Collapse a section.** Click `button "Topics"`. It reports `expanded=false` and the topic links hide; a reload keeps it closed until a topic's page opens.

## Gotchas

- The groups' open state is per page load; only the current entity's group reopens after a reload.
- There is no Groups section; group pages (`/monitors/<group>`) are reachable by URL and list their members.
