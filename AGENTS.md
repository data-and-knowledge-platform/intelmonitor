# IntelMonitor: notes for agents

Read `docs/spec.md` for what is built and what is left, `docs/decisions.md` for why, and `GLOSSARY.md` for the terms.
`README.md` has setup and the commands.

## Rules for this repo

- **Paid commands need a go-ahead.** Anything that calls a model or Exa costs money (the README lists them). Say what it will cost, from `docs/pipeline-costs.md`, and wait for the owner before running it.
- **The stores are not in git and cost hundreds of dollars to rebuild.** Copy `data/intelmonitor.db` to a dated backup before any command that changes stored runs, such as `im clear-runs` or `im weekly`.
- **Opening a store with new code can migrate it.** `im serve` reloads on code changes, so a schema change reaches the live store as soon as it is saved. Try schema changes on a copy, with `INTELMONITOR_DATA_DIR` pointing at it.
- **The baseline store is frozen.** Open `data/intelmonitor-baseline-89-entities-2026-11-16.db` only through a command's `--store` option, which runs no migration.
- **A prompt edit makes a new version.** Changing a stage's template, schema, model or effort changes its version id, and recorded calls stop replaying. Try a change as a candidate in `api/intelmonitor/trial.py` first. Do not edit `PRIORITY_PROMPT` until the owner says to switch.
- **The team's deck sets the rules.** Prompts quote it word for word where they can, and code comments mark which wording is ours. Keep that marking when editing a prompt.
- **Report measured figures as measured and estimates as estimates.**
- **Older documents are history.** `prd.md`, `phase1-plan.md`, `phase1-decisions.md` and `docs/archive/` describe earlier plans. `docs/spec.md` wins.

## Before a commit

- `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` in `api/`.
- `uv run python -m intelmonitor.eval impact` in `api/`.
- `pnpm typecheck` and `pnpm lint` in `web/`.
- After an API model change, `pnpm gen:api` in `web/` and commit the generated files.
- Commit messages follow the log: a conventional prefix, then a sentence saying what now holds.
