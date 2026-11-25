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
Both services build from the repo's `Dockerfile`; `scripts/deploy.sh` uploads the same files to both, so the two run the same code. The store's schema is that of the runner that published it, so the site brings its pulled copy to its own schema before serving it.
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

### Test deployment on Northflank's free plan

Running since 23 Nov 2026, from the private repo `wongkang01/intelmonitor`, branch `main`, in project `default` (region US Central).
Deploying is a push to that branch: the service builds the `Dockerfile` and rolls out, so `scripts/deploy.sh` is not used there.

| Resource | Settings |
|---|---|
| Service `intelmontior` (combined: build and deploy) | Plan `nf-compute-20` (0.2 vCPU, 512 MB), 2 GB temporary disk, port `http` 8000 public at `https://http--intelmontior--rpdtytzxv9bj.code.run`, startup probe `/api/health` every 10 s for up to 10 minutes, liveness probe every 30 s. |
| Addon `stores` | MinIO 2025.10.15, 6 GB, TLS, private; bucket `stores` inside it. |
| Secret group `bucket` | `STORE_BUCKET=stores`, `STORE_S3_REGION=us-east-1`, `STORE_KEEP=8`, and the addon's `MINIO_ENDPOINT`, `ACCESS_KEY`, `SECRET_KEY` as `STORE_S3_ENDPOINT`, `STORE_S3_ACCESS_KEY_ID`, `STORE_S3_SECRET_ACCESS_KEY`; restricted to the service. |

The free plan's limits per container are 0.2 vCPU, 512 MB and 2 GB of temporary disk; larger plans and disks are refused.
The site sits at about 85% of its memory and reaches 100% under the endpoint check, so 1 GB (`nf-compute-50`, a paid plan) is the safer size.
The internal MinIO endpoint's TLS certificate is trusted by the image, so no CA file is needed.

The runner's development merge, measured in the image on 1 CPU over the 16,392 findings of run 2026-11-08, peaks at 470 MB once the store holds their embeddings and at 652 MB on the first run, which embeds them all (6.5 minutes).
Those figures predate two changes: the merge now holds one copy of the stored vectors instead of two while loading them, and embeds one statement per call so that a statement's vector does not depend on what was embedded with it.
The pair scoring is bounded: it scores 512 findings at a time against only those within the merge window, so its tables depend on how many findings a few weeks hold, not on the corpus.
What still grows is the one copy of the stored vectors, about 1.5 KB a finding, held through the merge.
On synthetic data at 3,000 findings a week, measured with Python's tracemalloc and not in the image, loading the vectors peaked at 32 MB for 18,000 findings and 254 MB for 144,000 (61 MB and 489 MB before the change), and scoring the pairs at 141 MB and 395 MB including those vectors.
The rest of a run on a full store is not measured yet.

The first store was published by hand through `northflank forward addon --skipHostnames`, with certificate checks off for that loopback connection only, because the certificate names the internal hostname.
Since 23 Nov it serves a copy of the local store taken after the links were rated (1,364 links, latest run 2026-11-08), migrated by the deployed code before publishing, since the site opens stores read-only and cannot migrate them.
