---
name: verify-intelmonitor
description: "Drive the running IntelMonitor web app (Vite and TanStack Router pages over the FastAPI store) the way an analyst does, and keep proof. Use to verify any UI or API change, reproduce a reported bug, check a feature still works, or take screenshots and recordings of the Entities and Topics list pages, entity and topic pages and their tabs, the Personal and function views with their watchlists and Recently viewed, development feeds and the development popup, the Data tab and graph views."
---

# Verify IntelMonitor

`pnpm -C web verify <command>` is the harness (`web/verify/control.ts`).
It health checks the dev servers, resolves a feature to its page with real ids, and keeps evidence.
The page itself is driven with `agent-browser --session im-verify`, which the harness opens at the e2e viewport (1440x900).
`pnpm -C web verify help` lists every command.
The app keeps no viewer state on the server: the watchlist, Recently viewed and the view live in the browser, in `localStorage["im.state.v1"]`, which `verify state` reads and writes.

## Launch

The app is two Portless dev servers over one SQLite store (`data/intelmonitor.db`, or `$INTELMONITOR_DATA_DIR/intelmonitor.db` when that is set for the API and the harness).
Set the same `INTELMONITOR_DATA_DIR` for `verify check`, `pnpm -C web e2e` and anything else that opens the store, so they read the store the API serves.

```bash
pnpm -C web verify doctor   # first, and again whenever something looks off
pnpm -C web verify up       # starts only what is missing: proxy, API, web
```

Ready means doctor prints `"ok": true`.
The user usually has the servers running already; `up` then starts nothing.
There is one store and one pair of servers, so no isolated second instance exists.
Two agents driving at once each set `VERIFY_SESSION=<name>` for their own browser; the web app only reads the API and each browser session has its own storage, so drives do not disturb each other.

## Doctor

`doctor` is read-only and prints one JSON object of checks, each with a `fix` command when it fails:

- `proxy`, `api`, `web`: Portless routes exist and both servers answer.
- `api-types`: the live API schema equals `web/lib/api/openapi.json`. When it differs, run `pnpm -C web gen:api` before trusting any UI result.
- `run`: the latest run in `/runs` (the weekly runs are scheduled, so there is no running state to report); warns when there is none.
- `store`, `agent-browser`, `git`: the store exists, the driver is installed, HEAD and dirty count for the proof log.

## Drive

1. Match the change to a file in [`features/`](features/README.md) and read it. It lists every entry point, stable handles and gotchas.
2. Set the browser state the recipe starts from: `pnpm -C web verify state show`, `state clear` (a first visit) or `state set '{"watchlist":["mas","dbs"],"view":"personal"}'`.
   Clear and set end on the Entities list, which records nothing in Recently viewed, so the page holds exactly the written state.
3. `pnpm -C web verify open <target> [args]` to reach the starting page.
   Targets: `home`, `page <subject>`, `page-developments <subject> [query]`, `related <subject>`, `data <subject>`, `graph <subject>`, `entities [query]`, `topics [query]`, `development <development-id> [subject]`.
   `page` resolves to `/entities/<id>` or `/topics/<id>`; a list query sets its period and filters (`period=4w&group=banking`); a development opens over its subject's page, else over the Entities list.
   A subject is an id or a name; a name that matches several items fails and lists their ids.
   Opening an entity or topic page records it in Recently viewed, as a visit does.
4. Find ids from the API when a recipe needs one: `pnpm -C web verify api /subjects`, `/functions`, `/lists/entities?period=week`, `/developments?subject_id=dbs&limit=5`, `/runs`.
5. Drive with `agent-browser --session im-verify snapshot -i`, then `click @eN`, `fill @eN "text"`, `press "Meta+k"`, `wait --load networkidle`.
   Act on refs from a fresh snapshot. `agent-browser find role ... --name` misses links, textboxes and checkboxes here and can hang.
   Some names repeat (developments facet buttons and column sorts share names), so check the ref's context in the snapshot.
   Popovers, dialogs and sidebar groups can be missing from `snapshot -i`; take a full `snapshot` when something you see is not listed.
   `eval` shares one global scope across calls, so wrap each script as `(() => { ... })()`.
6. After the user action, read the side effect back: `verify state show` for the watchlist, view and Recently viewed, `verify api ...` for what the page shows, or a reload; not just the pixels.

Pipeline behavior with no UI entry point has scripted checks: `pnpm -C web verify check series|past-run|detector`, each printing JSON.
The full regression walkthrough is `pnpm -C web e2e` (Playwright, 19 steps, writes `artifacts/e2e/<timestamp>/index.html`); see [pipeline-checks](features/pipeline-checks.md).
It is one test, so steps cannot run alone; it calls no model and writes nothing to the store: it logs every API request its pages make to `requests.json` and fails on any that is not a GET or that reaches a removed endpoint, and it ends with `check_endpoints.py`.

## Evidence

```bash
pnpm -C web verify shot <run>/<step>          # artifacts/verify/<run>/<step>.png + .aria.txt
pnpm -C web verify record start <run>/<name>  # .webm; record stop when the end state is stable
```

Proof bar:

- Drive the production user path: visible controls, keyboard shortcuts, links. Opening a URL is how to reach a starting page, not proof that its entry point works.
- Show the trigger and the stable end state in the same recording, or a shot before and after.
- Verify state: a reload keeps it, and the API agrees with what the page shows.
- Cover every entry point, and the empty, error, cancel and persistence paths the change can affect, per the feature file.
- Name a path you could not reach and why.

## Store and cleanup

Nothing a viewer does in the web app writes to the store, so a UI drive needs no snapshot and no clean-up beyond `verify state clear`.
Store snapshots are for pipeline work such as a rebuild or a migration.

```bash
pnpm -C web verify store save <label>     # SQLite backup, safe while serving
pnpm -C web verify store restore <file>   # puts it back; keeps a copy of what it replaced
pnpm -C web verify cleanup                # closes the harness browser
pnpm -C web verify down                   # stops only servers that `up` started
```

Snapshots live in `data/verify-snapshots/`, evidence in `artifacts/verify/`; cleanup removes neither.
Never kill servers by name; the user's servers are not ours.
If the browser stops answering or lands on `about:blank`, run `verify cleanup`, then `verify open` again; do not keep driving a wedged session.

## Keeping the map honest

A change that adds, moves or renames a user-visible control updates its feature file in the same commit.
When a recipe here or in `features/` turns out wrong, fix it as part of the task.
