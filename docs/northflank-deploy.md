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
| Addon `stores` | MinIO | (`STORE_KEEP` + 1) × store size, plus margin | 8 stores of 550 MB is 4.4 GB now, plus the store from before the latest rerun, which is never pruned; grow the disk or lower `STORE_KEEP` as the store grows. |

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
  The runner never creates a bucket, so a mistyped name fails at its first read.
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

The runner and the site both start from the store `latest.json` names.
Either the runner starts a new store in the empty bucket and pays for every week ("First start" under "From the web UI"), with no tunnel once the bucket exists, or an existing store is uploaded by hand:

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
- Command: custom, `/app/api/.venv/bin/im weekly --publish`, with `--start-from` or `--rerun` added for a first start or a rerun ("From the web UI").
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

## From the web UI

Everything here is done in the `runner` job's settings and with its Run button, with no CLI, shell or tunnel.
`im weekly --help` describes the options.
Concurrency Forbid does not cover runs started by hand, so wait for a run to end before starting another; a run that overlaps another fails at its publish instead of overwriting the other's week.

### Which weeks run

There is no end date to give.
A week is Monday to Sunday UTC, dated its Sunday.
The runner runs every complete week from the corpus start (or after the store's latest run) up to the last Sunday before the day it runs, oldest first.
Run on 25 Nov 2026 from 1 Oct 2026, that is eight weeks: 4 Oct to 22 Nov.
The first week is short when the corpus start is not a Monday: from 1 Oct it covers 1 to 4 Oct.
`--max-weeks` limits one run, 4 unless set; the weeks left over are still due at the next run, and the log says "weeks still due".
There is no way to stop at an earlier week; a later run always catches up to the present.

### Before a first start or a rerun

Each of these weeks is paid again in model and search calls, so check before pressing Run.

- The date in `--start-from`: a date that makes more than 26 weeks due is refused, but a wrong date within that still queues every week since it, up to `--max-weeks` a run. Read the "weeks due" line at the top of the first log.
- The priority prompt: every week uses the prompt in the deployed code. Switch a new version on in code before the run, not between weeks.
- The runner's plan and disk (see Sizes), its secret groups, and outbound access to the Anthropic and Exa APIs.
- The series keys: without them a week is still computed and published, but the run ends as failed.
- Time: how many weeks fit in the 4-hour stop is not measured. Expect to press Run several times for eight weeks.
- Cost: the six weeks to 8 Nov cost $223 with 89 entities (measured, `pipeline-costs.md`); with 142 entities and nothing recorded to replay it will be more.

### First start

1. The bucket must exist (step 3 under "Steps" creates it) and hold no published store; the runner checks both and refuses otherwise, before any paid call.
2. Set the command to `/app/api/.venv/bin/im weekly --publish --start-from 2026-10-01 --max-weeks 8` and press Run.
3. With nothing published, the runner starts a new store with 1 Oct 2026 as its corpus start and runs the complete weeks from it, oldest first, publishing after each.
4. The site starts once the first week is published.
5. Once the log shows a line "published stores/...", remove `--start-from 2026-10-01` from the command.
   If the run ended before any such line, the bucket is still empty: press Run again with the same command.
6. If the log ends with "weeks still due", press Run again.

Without `--start-from`, the runner refuses an empty bucket, so a wrong bucket name cannot start a paid rebuild.
With `--start-from`, the runner refuses a bucket that already has a published store, so a command that still has it fails at the next run, the weekly cron included, instead of starting again.
The date must be a past date, and its first week must have ended.
A date that makes more than 26 weeks due is refused as a likely typo; raise `MAX_START_WEEKS` in `api/intelmonitor/cli.py` to start further back.

### A weekly run by hand

Press Run.
The runner runs the complete weeks after the published store's latest run, at most `--max-weeks`, and exits 0 when none is due.

### Rerun every week

1. Add `--rerun <label>` and `--max-weeks 8` to the command, for example `--rerun 2026-11-25`, and press Run.
2. If the pulled store does not record that label, the runner deletes its runs as `im clear-runs` does and records the label.
   Documents, findings, recorded model calls, series and the run log stay.
   Every week from the corpus start is then due again.
3. The label is recorded in the runner's local copy of the store, in the same transaction as the delete, and reaches the bucket only inside the first week published after it.
   If the run stops before that publish (an error, a crash, the 4-hour stop), the bucket is unchanged and the next run deletes and starts again.
4. Once a rerun week is published, runs with the same label delete nothing and carry on with the weeks still due.
   Press Run until the log no longer says "weeks still due"; the 4-hour stop also ends a run early.
5. When the rerun is done, remove `--rerun` from the command.

A new label starts a new rerun.
A label is 1 to 64 letters, digits, dots, dashes or underscores, starting with a letter or digit; anything else is refused before the bucket is touched.
Each publish of a rerun week also writes `reruns/<label>` in the bucket.
If a run with `--rerun <label>` pulls a store without that label while `reruns/<label>` exists, it refuses with exit 2 before any paid call: the store is from before the rerun, as after a rollback.
Remove `--rerun` to carry on from that store, or give a new label to rerun it again.
Rarely, a run that dies in the instant between writing `reruns/<label>` and repointing `latest.json` leaves the same refusal although no rerun week reached the site; give a new label then too.
Model calls whose input has not changed replay for free; Exa searches and series are fetched again, as the runner keeps no HTTP cache.

### What the site shows meanwhile

Until the first rerun week is published, the site shows the store from before the rerun.
After that it shows only the weeks rerun so far, and gains one with each publish.
The site picks up each publish within 10 minutes.

### Roll back

Every run's log names the store it pulled, and the run that starts a rerun logs "the store published before the rerun, `stores/<file>`, is kept".
Pruning keeps that store, the one `latest.json` names and the `STORE_KEEP` newest; a rollback can only go to one of these.

1. Remove `--rerun` from the command if it is there; a run that keeps it after a rollback to the store from before the rerun is refused.
2. Set the command to `/app/api/.venv/bin/im publish --key stores/<file>` and press Run.
   It repoints `latest.json` at that file, and the site serves it within 10 minutes.
   A weekly run still in progress then fails at its next publish instead of overwriting the rollback.
3. Set the command back to `/app/api/.venv/bin/im weekly --publish`.

If the Run dialog offers a per-run command override (not confirmed), use it for step 2 instead of editing the command.

## Rollback

`uv run im publish --key stores/<file>` through the tunnel repoints `latest.json` at an older store; the site picks it up within 10 minutes.
Without the CLI, see "Roll back" under "From the web UI".

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
| Published store | `stores/intelmonitor-2026-11-08-d0346b1f.db` (89 entities, run 2026-11-08) | the store from the rerun (`--rerun`) |
| Failure alerts | none | step 7 |
| Region | US Central | Singapore, if offered |

Already set on `runner`: the command, retry limit 0, time limit 15300 s, run on source change Never.
