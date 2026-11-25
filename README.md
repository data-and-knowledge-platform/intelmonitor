# IntelMonitor

A proof of concept for the Monetary Authority of Singapore.
Each week it reads public news and regulatory documents, groups them into developments, rates each one High, Medium or Low for the entities and topics it is about, and infers which linked entities it could affect.

- `api/`: the pipeline, the store and the API. Python 3.12, FastAPI, SQLite, managed with uv.
- `web/`: the app. Vite, React and TanStack Router, managed with pnpm.
- `api/seed/`: entities, topics, links and their ratings.
- `docs/`: what is built, why, prompts, costs and deployment.

## Read first

| File | What it holds |
|---|---|
| [docs/spec.md](docs/spec.md) | What the system does now and what work is left. |
| [docs/decisions.md](docs/decisions.md) | The decisions behind it, with dates and reasons. |
| [GLOSSARY.md](GLOSSARY.md) | The project's terms. |
| [docs/prompts/priority-versions.md](docs/prompts/priority-versions.md) | The priority prompt's versions and results. |
| [docs/pipeline-costs.md](docs/pipeline-costs.md) | Measured cost per stage and per run. |
| [docs/deploy.md](docs/deploy.md), [docs/northflank.md](docs/northflank.md) | The deployed site, the runner and the bucket. |
| [docs/northflank-deploy.md](docs/northflank-deploy.md) | The steps to deploy on Northflank, and what is left on the test deployment. |
| [PRODUCT.md](PRODUCT.md), [DESIGN.md](DESIGN.md) | Who the app is for and how it should look. |

`prd.md`, `phase1-plan.md`, `phase1-decisions.md` and `docs/archive/` are history.
Where they differ from `docs/spec.md`, the spec is right.

## Set up

Needs Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22 with pnpm 11, and [Portless](https://github.com/vercel-labs/portless) for the local addresses.

```sh
cp .env.example .env        # then fill in the keys
cd api && uv sync
cd ../web && pnpm install
```

Keys, in `.env`:

- `ANTHROPIC_API_KEY` and `EXA_API_KEY` are required for a run.
- `FRED_API_KEY`, `COINGECKO_API_KEY` and the `MAS_*` keys feed the measured series. Without them a run still completes, but `im weekly` reports the missing series and exits 1.
- The `STORE_*` settings are for the deployed site and runner only.

The store is not in git.
Copy `data/intelmonitor.db` from the machine that has it, or pull the published one with `uv run im pull`, or build one from nothing with `uv run im weekly`, which pays for every model call and search.

## Run locally

```sh
scripts/proxy-start.sh                                # once per boot
cd api && portless intelmonitor.api uv run im serve   # https://intelmonitor.api.localhost
cd web && portless intelmonitor.web pnpm dev          # https://intelmonitor.web.localhost
```

`uv run im --help` lists the pipeline commands.
The ones that call a model or Exa cost money: `im weekly`, `im run`, `im ingest`, `im trial`, `im summarize`, the `link-types` and `subjects` evals, `scripts/collect_links.py` and `scripts/rate_relationships.py`.

## Check

```sh
cd api && uv run ruff check . && uv run pyright
uv run python -m intelmonitor.eval impact     # the inferred impact rules, no model call
cd ../web && pnpm typecheck && pnpm lint
pnpm e2e                                      # needs both servers and a store
```
