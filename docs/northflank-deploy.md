# Deploying on Northflank

The steps and settings to run IntelMonitor on Northflank, in order.
[deploy.md](deploy.md) explains what the site, the runner and the bucket do; [northflank.md](northflank.md) gives the reasons for each setting, what changes on the government version, and the questions for the platform team.
The test deployment in project `default` (set up 23 Nov 2026) has steps 1 to 4 done; section "Test deployment: what is left" lists what is still off there.

## Sizes

| Workload | Plan | Ephemeral disk | Why |
|---|---|---|---|
| `site` | `nf-compute-50` (0.5 vCPU, 1 GB) | 5 GB | On `nf-compute-20` (512 MB) it sits at 85% of memory and reaches 100% under the endpoint check. During a swap it holds two stores; the store is 536 MB now and about 1.7 GB after 13 weekly runs with 142 entities. |
| `runner` | `nf-compute-100-2` (1 vCPU, 2 GB) | 5 GB | A one-day, one-entity test peaked at 483 MB. The merge alone peaked at 652 MB on a store's first run, measured before embedding went to one statement per batch (`EMBED_BATCH = 1`). A full week on a full store is not measured yet. The disk holds the store, its publish copy, a week of HTTP cache and the embedding model. |
| Build | `nf-compute-400-16` | 16 GB | What the test deployment uses. |
| Addon `stores` | MinIO | `STORE_KEEP` × store size, plus margin | 8 stores of 550 MB is 4.4 GB now; grow the disk or lower `STORE_KEEP` as the store grows. |

The free plan allows at most 0.2 vCPU, 512 MB and 2 GB of disk per container, so the runner needs a paid plan for a real week.

## Steps

### 1. Project

Create the project in the region closest to the users (Singapore if offered).
The test project `default` is in US Central.

### 2. Build and site: combined service

One combined service builds the image and runs the site; the runner job deploys from its build, so both run the same commit.

- Source: the repo, branch `main`.
- Build: Dockerfile `/Dockerfile`, context `/`, BuildKit, no target stage.
- Command: the default (`/app/deploy/start.sh`); no override.
- Port: `http`, 8000, HTTP, public.
- Instances: 1.
- Health checks, all HTTP `GET /api/health` on port 8000:

  | Probe | Initial delay | Interval | Timeout | Max failures |
  |---|---|---|---|---|
  | Startup | 5 s | 10 s | 3 s | 60 |
  | Liveness | 5 s | 30 s | 5 s | 3 |

  The startup probe allows 10 minutes, as the site downloads the store before it opens the port.
  Without it the container is killed mid-download and restarts in a loop.
- No volume.
- Secret group `bucket` only; the site holds no API keys.

The site will not start until step 5 has published a store.

### 3. Bucket: MinIO addon

- Addon `stores`, MinIO, TLS on, not publicly accessible.
- Create the bucket `stores` in it (through the tunnel in step 5, with `mc mb` or boto3 `create_bucket`).
- The addon's internal certificate is trusted by the image, so no CA file is needed.

### 4. Secret groups

Both of type secret, runtime scope, with Restrict secret group on.

| Group | Restricted to | Variables |
|---|---|---|
| `bucket` | the site service and the `runner` job | `STORE_BUCKET=stores`, `STORE_S3_REGION=us-east-1`, `STORE_KEEP=8`, and linked from the addon: `MINIO_ENDPOINT` as `STORE_S3_ENDPOINT`, `ACCESS_KEY` as `STORE_S3_ACCESS_KEY_ID`, `SECRET_KEY` as `STORE_S3_SECRET_ACCESS_KEY` |
| `runner-keys` | the `runner` job | `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `CLAUDE_VIA_API=1`, `EXA_API_KEY`, `EXA_API_KEY_2`, `FRED_API_KEY`, `COINGECKO_API_KEY`, `MAS_API_BASE_URL`, `MAS_EXCHANGE_RATES_API_KEY`, `MAS_BANK_ASSET_QUALITY_API_KEY`, `MAS_BANK_LIQUIDITY_API_KEY`, `MAS_BANK_EXPOSURE_API_KEY`, `MAS_SORA_KEY` |

The values come from the local `.env`.
Editing a group through the API or CLI replaces all its variables, even with PATCH; edit in the UI or send the full set.
A read-only MinIO user for the site is better but not set up; [northflank.md](northflank.md) has the commands.

### 5. First publish

The runner and the site both start from the store `latest.json` names, so the first one is uploaded by hand.

1. Take the store to publish with `sqlite3 data/intelmonitor.db ".backup <copy>"`, not `cp`, and never open the live store with the app's `connect()`.
2. Open a tunnel: `northflank forward addon --projectId <project> --addonId stores --skipHostnames --portConflict random`.
   It prints the local port of the `api` endpoint (9000).
3. Upload `<copy>` with the `STORE_*` variables pointing at `https://127.0.0.1:<port>`, as `im publish` does: an immutable `stores/intelmonitor-<run date>-<hash>.db` with `sha256` and `run-id` metadata, then `latest.json`.
   The addon's certificate names its internal hostname, so certificate checks fail on the loopback address; `im publish` has no switch for that, and on 23 Nov a short boto3 script with `verify=False` for this connection only did the upload.
   Forwarding with hostnames (`sudo northflank forward addon` without `--skipHostnames`) should let `im publish` run unchanged; not tried.

The site and the runner bring a pulled store to their own schema, so a store from older code does not need migrating first.

### 6. Runner: cron job

A job deployed from the combined service's build.

- Type: cron.
- Schedule `0 0,6 * * 1`, time zone `UTC`, set explicitly: Monday 08:00 Singapore time, with a retry at 14:00.
  Read as Singapore time, it would fire on Sunday UTC, before the week is complete.
- Concurrency policy: Forbid.
  It does not apply to runs started by hand.
- Skip missed runs: off.
- Run on source change: Never, so a push does not start a paid run.
- Retry limit (`backoffLimit`): 0.
- Time limit (`activeDeadlineSeconds`): 15300 (4 h 15 min); the runner stops itself at 4 hours.
- Command: custom, `/app/api/.venv/bin/im weekly --publish`.
- Plan and disk: see Sizes.
- Secret groups `bucket` and `runner-keys`.

A firing with no week due exits 0 and spends nothing.
A firing with a week due spends on Claude and Exa; with both Exa keys or the Claude key refused, it exits 1 in about 10 seconds before spending.

### 7. Failure alerts

Add a notification integration (Slack, Teams, Discord or webhook) for job run failure, abort and terminate, and for job container crash and eviction.
The runner exits 1 when a week is missing a whole source, after still publishing it, so the alert is the only sign.

### 8. Check

- `uv run python scripts/check_endpoints.py` (from `api/`) with `--base https://<site>/api --out <file>` passes all 10 checks.
- The site's log shows `serving stores/intelmonitor-<run date>-<hash>.db`.
- `northflank start job run --projectId <project> --jobId runner` with no week due exits 0.

## Deploying code

Push to `main` of the tracked repo: the combined service builds and rolls out the site.
`scripts/deploy.sh` is for Railway only.
Check the runner job's deployed commit matches the site's before a firing that spends.

## Rollback

`uv run im publish --key stores/<file>` through the tunnel repoints `latest.json` at an older store; the site picks it up within 10 minutes.

## Test deployment: what is left

Project `default`, US Central, site `intelmontior` at `https://http--intelmontior--rpdtytzxv9bj.code.run`, read on 25 Nov 2026.

| Setting | Now | Needed |
|---|---|---|
| Site plan | `nf-compute-20` (512 MB) | `nf-compute-50` (paid) |
| Site disk | 2 GB | 5 GB (paid); 2 GB runs out at about week 8 with 142 entities |
| `bucket` restricted to | the site only | the site and `runner` |
| `runner` type | manual | cron, with the schedule and time zone above |
| `runner` concurrency | not set (manual) | Forbid |
| `runner` plan | `nf-compute-20` | `nf-compute-100-2` (paid) |
| `runner` disk | 2 GB | 5 GB |
| `runner` deployed commit | `b7c036b` | the site's commit |
| `runner` secret groups | `runner-keys` | `runner-keys` and `bucket` |
| Published store | `stores/intelmonitor-2026-11-08-d0346b1f.db` (89 entities, run 2026-11-08) | the store from the six-week rerun |
| Failure alerts | none | step 7 |
| Region | US Central | Singapore, if offered |

Already set on `runner`: the command, retry limit 0, time limit 15300 s, run on source change Never.
