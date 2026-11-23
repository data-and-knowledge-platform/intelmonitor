# Deploy

The deployment has three resources: a stateless `site` service, a scheduled `runner` job, and an S3-compatible bucket between them.
Only the S3 API and environment variables connect them, so moving to another platform means recreating the three resources and copying the variables, with no code change.

## How a week reaches the site

1. Every Monday the runner starts with an empty disk, downloads the store `latest.json` names, and migrates it.
   When a week is due, a preflight makes one Exa search and one Claude Haiku call, and the runner exits 1 before any other work if either fails.
2. For each complete week since the store's last complete run (Monday to Sunday UTC, at most 4 per firing, oldest first), it ingests that week and computes its run, logged with trigger `scheduled`.
3. After each week it uploads a compact copy of the store as an immutable file `stores/intelmonitor-<run date>-<short hash>.db`, then rewrites `latest.json` (`key`, `run_id`, `sha256`, `published_at`) last, so readers never see a half-written store.
4. It keeps the `STORE_KEEP` newest store files (12 by default) and deletes older ones, never the one `latest.json` names.
5. The site downloads the file `latest.json` names when it starts, opens it read-only, and checks `latest.json` every 10 minutes; a new key is downloaded beside the old file, its sha256 checked, and swapped in without a restart.

A failed run exits non-zero and publishes nothing for that week, so the site keeps the last good store.
A week that left out a whole source (an official source not reached, most news searches failed, a series key missing or refused) is still computed and published, and the runner then exits 1 listing what was missing.
Exa searches use `EXA_API_KEY`, and `EXA_API_KEY_2` once Exa refuses the first (revoked, or out of credits); with both refused, ingest stops.
Before rewriting `latest.json`, a publish checks it still names the store the run started from, so a run that overlapped another fails instead of overwriting its week.
A firing with nothing due exits 0 and uploads nothing, so the 14:00 retry is harmless.
The runner stops itself after 4 hours.
There is no spend cap.

The code is `api/intelmonitor/store/publish.py`, `im weekly --publish`, `im publish` and `im pull` in `api/intelmonitor/cli.py`, and the lifespan in `api/intelmonitor/server/app.py`.

## Railway

Project `intelmonitor`, environment `production`.
Both services build from the repo's `Dockerfile`; `scripts/deploy.sh` uploads the same files to both, so code and schema always match.
There is no `railway.json`: Railway stops reading config files on 1 Dec 2026, so each service's settings live in the service.

| Resource | Settings |
|---|---|
| `site` | Dockerfile `Dockerfile`, default start command (`deploy/start.sh`), healthcheck `/api/health` with a 300 s timeout, restart on failure, region Singapore, public domain `intelmonitor-poc.up.railway.app`, no volume. |
| `runner` | Dockerfile `Dockerfile`, start command `/app/api/.venv/bin/im weekly --publish`, restart never, cron `0 0,6 * * 1` (Monday 08:00 Singapore time, retry at 14:00), no domain, no volume. |
| bucket `stores` | Region `sin`. Railway issues one key pair per bucket, so both services share it. |

Variables:

| Variable | `site` | `runner` |
|---|---|---|
| `STORE_BUCKET`, `STORE_S3_ENDPOINT`, `STORE_S3_REGION`, `STORE_S3_ACCESS_KEY_ID`, `STORE_S3_SECRET_ACCESS_KEY` | yes | yes |
| `INTELMONITOR_DATA_DIR=/data`, `INTELMONITOR_MODELS_DIR=/app/models` | set by the image | set by the image |
| `ANTHROPIC_API_KEY` with `CLAUDE_VIA_API=1`, `EXA_API_KEY`, `EXA_API_KEY_2`, `FRED_API_KEY`, `COINGECKO_API_KEY`, the five MAS keys | no | yes, sealed |

The bucket's values come from `railway bucket credentials --bucket stores --json`.
The site holds no API keys and has no endpoint that writes.

A failed firing shows as crashed in Railway, which emails the account owner.
`railway cron run --service runner` starts a firing off schedule, for example to catch up at once.

## By hand

With the `STORE_*` variables set in the shell or `.env`:

- `uv run im publish` uploads `data/intelmonitor.db` and repoints `latest.json`; used for the first publish.
- `uv run im publish --key stores/<file>` repoints `latest.json` at a file already in the bucket: a rollback, picked up by the site within 10 minutes.
- `uv run im pull` downloads the published store as `data/intelmonitor.db`; `--replace` overwrites a local store.

`im serve` with the variables set serves the published store read-only, as the site does; without them it uses the local store as before.

## First deploy

The runner computes paid weeks on whatever code is deployed, so this follows the rerun of the six weeks on the current schema.

1. Publish the rerun store: `uv run im publish` with the bucket variables.
2. Set the `site` settings above (Dockerfile, healthcheck `/api/health` with 300 s) and the bucket variables on `site`.
3. Run `scripts/deploy.sh`, which deploys `site` and `runner` from the same commit.
4. Check the site serves the published run, then detach and delete the old `site` volume.
5. Give the runner its API keys and set its cron schedule.
6. Optionally start one firing with `railway cron run --service runner`; with no week due it exits 0.

## Northflank

| Here | Northflank |
|---|---|
| `site` service | Deployment service from the same image, no volume, the bucket variables with read-only keys. |
| `runner` cron service | Cron job from the same image with the same start command and schedule, concurrency forbidden. |
| Railway bucket | Northflank's S3 add-on, or the cloud account's S3. |

[northflank.md](northflank.md) has the exact settings, what must change and the questions for the platform team.

To confirm with the platform team: outbound access from the runner to the Anthropic API, or a switch of its model client to the cloud provider's Claude endpoint; and which bucket service is allowed.
