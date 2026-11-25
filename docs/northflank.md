# Northflank

How the three resources in [deploy.md](deploy.md) map onto Northflank, what must change, and what to ask the platform team.
Checked against Northflank's public docs on 23 Nov 2026.
The government version may differ from them anywhere; items marked **unconfirmed** are not in the docs.

## 1. Mapping

| Railway | Northflank |
|---|---|
| Each service builds from `Dockerfile` | One build service builds the image; `site` and `runner` both deploy from it with CD on, so both run the same code; the site brings its pulled copy of the store to its own schema before serving it |
| `scripts/deploy.sh` (`railway up` to both) | A push to the tracked branch (CI on the build service, CD on both workloads) |
| `site` service | Deployment service `site` |
| `runner` cron service | Cron job `runner` |
| Bucket `stores` (Tigris) | MinIO addon, or the cloud account's S3 under BYOC |
| Service variables, sealed | Secret groups restricted to one workload each |
| Crash email to the account owner | Notification integration on job run failure |
| `railway cron run --service runner` | Run job in the UI, `northflank start job run`, or `POST .../jobs/{jobId}/runs` |

### Build service `intelmonitor`

- Source: the repo, branch `main`, CI on.
- Build type Dockerfile, Dockerfile path `/Dockerfile`, build context `/`, no target stage (the final stage is the Python image).
- Builds run on Northflank's build infrastructure outside the project unless the cluster is BYOC with builds on it.
- The build pulls `node:22-slim` (Docker Hub), `ghcr.io/astral-sh/uv:python3.12-bookworm-slim`, pnpm through corepack, npm packages and PyPI wheels.

### Deployment service `site`

- Deploy from build service `intelmonitor`, CD on.
- Docker runtime mode: default configuration (the image's `CMD /app/deploy/start.sh`).
- Port 8000, HTTP, public (or VPC only, see the open questions).
  Northflank does not document injecting `PORT` (**unconfirmed**); `start.sh` falls back to 8000, so set nothing or set `PORT=8000`.
- Health checks, all HTTP `GET /api/health` on port 8000:

  | Probe | Initial delay | Interval | Timeout | Max failures |
  |---|---|---|---|---|
  | Startup | 5 s | 10 s | 5 s | 60 |
  | Readiness | 0 s | 10 s | 5 s | 3 |
  | Liveness | 0 s | 30 s | 5 s | 3 |

  uvicorn runs the lifespan, which downloads the ~500 MB store, before it binds the port, so every probe is refused until the download ends.
  The startup probe allows about 10 minutes, as the 300 s Railway healthcheck did with margin; readiness and liveness wait for it.
  Whether the government version caps max failures below 60 is **unconfirmed**.
- Ephemeral storage 5 GB.
  The default is 1 GB per container, and a container that writes past its limit may be evicted.
  At a swap the site holds the previous store, the current one and the next one's `.part` file, about 1.5 GB.
- No volume.
- Instances: 1.
  Each replica downloads its own copy and polls on its own clock, so with more than one, replicas can serve different runs for up to 10 minutes after a publish.
- Secret group `store-read` only; no API keys.

### Cron job `runner`

- Deploy from build service `intelmonitor`, CD on.
- Run on image change: Never, so a deploy does not start a paid run.
- Docker runtime mode: custom command `/app/api/.venv/bin/im weekly --publish` (exec form, no shell needed).
- Schedule `0 0,6 * * 1` with time zone `UTC` (the API's `timeZone` field, IANA name).
  The docs do not say what time zone applies when none is set (**unconfirmed**).
  This must be UTC: the code treats a week as Monday to Sunday UTC, and a schedule read as Singapore time would fire at 16:00 and 22:00 UTC on Sunday, when the week is not complete, so the week would wait for the next Monday.
- Concurrency policy: Forbid.
  Northflank's docs say the policy does not apply to runs started by hand, so a manual run during a scheduled one runs alongside it.
- Skip missed runs: off, so a late start still runs; a firing with nothing due exits 0.
- Retry limit (`backoffLimit`): 0.
  The 06:00 firing is the retry; an immediate retry after a 4-hour stop would repeat the spend.
- Time limit (`activeDeadlineSeconds`): 15300 (4 h 15 min).
  The in-process 4-hour stop fires first and logs why; the time limit is the backstop if the process hangs where Python cannot run the timer.
- Ephemeral storage 5 GB: the pulled store, its WAL, the `VACUUM INTO` copy made at each publish, a week of HTTP cache under `/data/raw` and the 67 MB embedding model under `/data/models`.
- No ports, no health checks.
- Secret groups `store-write` and `runner-keys`.
- `INTELMONITOR_DATA_DIR=/data` and `INTELMONITOR_MODELS_DIR=/app/models` come from the image; Railway also sets the first on the runner, which is redundant.

### Bucket

Option A, the MinIO addon:

- Addon `stores`, MinIO, not publicly accessible, TLS on (the default).
- Create the bucket with `mc mb <alias>/stores`.
- `STORE_S3_ENDPOINT`: the addon's internal `MINIO_ENDPOINT`, linked into the secret groups with that alias.
- `STORE_S3_REGION=us-east-1` (MinIO's default region).
- `STORE_BUCKET=stores`.
- Addressing: Northflank's MinIO guide says the addon only supports path-style requests.
  boto3 1.44.5 (the locked version) already sends path-style requests whenever `endpoint_url` is set, checked locally, so no code change is needed.
- Multipart upload (boto3 splits files over 8 MB) and user metadata (`x-amz-meta-sha256`, `x-amz-meta-run-id`) are standard S3 features MinIO implements; Northflank's docs do not mention either.
- Read-only keys for the site: the addon exposes one admin key pair.
  Make a second MinIO user with a policy that allows `s3:GetObject` and `s3:ListBucket` on `stores` only (`mc admin user add`, `mc admin policy create`, `mc admin policy attach`).
  The docs show `mc admin config set` on the addon, so admin access exists, but they do not cover users or policies (**unconfirmed**).
  Without `s3:ListBucket`, a missing `latest.json` returns 403 instead of 404; the site fails to start either way before the first publish.
- If the internal TLS certificate is not publicly trusted (**unconfirmed**), set `AWS_CA_BUNDLE` to the CA file on both workloads; botocore reads it, no code change.
- Backups: the addon supports backup schedules; the store files are immutable and `latest.json` is one small object.

Option B, the cloud account's S3 (BYOC on AWS):

- Leave `STORE_S3_ENDPOINT` empty, so boto3 uses AWS's endpoint with virtual-host addressing.
- Set `STORE_S3_REGION` to the bucket's region.
- Leave both key variables empty and use workload identity: Northflank injects `AWS_ROLE_ARN` and `AWS_WEB_IDENTITY_TOKEN_FILE`, and `Bucket.from_settings` passes `None` for empty keys, so boto3's default chain picks them up with no code change.
- Two roles: the site's with `s3:GetObject` and `s3:ListBucket` on the bucket, the runner's with `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` and `s3:ListBucket`.

### Secret groups

All of type secret, runtime scope, with Restrict secret group on.

| Group | Restricted to | Variables |
|---|---|---|
| `store-read` | `site` | `STORE_BUCKET`, `STORE_S3_ENDPOINT`, `STORE_S3_REGION`, `STORE_S3_ACCESS_KEY_ID`, `STORE_S3_SECRET_ACCESS_KEY` (read-only user) |
| `store-write` | `runner` | The same names with the admin or read-write keys |
| `runner-keys` | `runner` | `ANTHROPIC_API_KEY`, `CLAUDE_VIA_API=1`, `EXA_API_KEY`, `EXA_API_KEY_2`, `FRED_API_KEY`, `COINGECKO_API_KEY`, `MAS_EXCHANGE_RATES_API_KEY`, `MAS_BANK_ASSET_QUALITY_API_KEY`, `MAS_BANK_LIQUIDITY_API_KEY`, `MAS_BANK_EXPOSURE_API_KEY`, `MAS_SORA_KEY` |

The two store groups use the same names, so each must stay restricted to its one workload.
Updating a group through the API or CLI replaces all its variables, even with PATCH; edit in the UI or send the full set.
RBAC can let a role see a secret group's keys but not its values.

### Notifications

- One integration (Slack, Teams Workflows, Discord or webhook) with job run failure, abort and terminate, and job container crash and eviction, restricted to the project.
- The docs list no email integration (**unconfirmed**), unlike Railway's crash email.

### Egress the runner needs

The site needs only the bucket.
The runner calls:

- LLM: `api.anthropic.com`.
- APIs: `api.exa.ai`, `api.stlouisfed.org`, `api.coingecko.com`, `stablecoins.llama.fi`, `query1.finance.yahoo.com`, `eservices.mas.gov.sg`, `api.hkma.gov.hk`.
- Feeds and the pages they list: `www.mas.gov.sg`, `www.federalreserve.gov`, `www.ecb.europa.eu`, `www.bankofengland.co.uk`, `www.eba.europa.eu`, `www.esma.europa.eu`, `www.fsb.org`, `www.bis.org`, `libertystreeteconomics.newyorkfed.org`, `www.thunes.com`, `trends.google.com`, `trends.google.com.sg`.
- Status pages: `status.aws.amazon.com`, `ocistatus.oraclecloud.com`, `azure.status.microsoft`, `status.cloud.google.com`, `www.cloudflarestatus.com`.
- Pages it dates itself: `asiaasset.com`.
- The embedding model at first use: `huggingface.co` and its file CDN (`Qdrant/bge-small-en-v1.5-onnx-Q`), unless the model is baked into the image (2.1).

Network policies with FQDN rules exist only on BYOC clusters; adding any egress rule turns the policy into an allow-list.
Whether FQDN rules take wildcards is **unconfirmed**.
Feed items that link off their own host would need their hosts added as well.

## 2. What must change

### 2.1 Code

None is needed on Northflank's standard cloud with open egress and either bucket option.
These become necessary under the conditions given:

1. **Claude through a cloud provider** (if `api.anthropic.com` is blocked), `api/intelmonitor/pipeline/claude.py` and `api/intelmonitor/config.py`.
   - Build the client from a setting: `AsyncAnthropicBedrockMantle` (Claude in Amazon Bedrock, Messages API) instead of `AsyncAnthropic`, with the region and AWS credentials from the environment or workload identity.
   - Bedrock model IDs carry an `anthropic.` prefix, which breaks `model == HAIKU_MODEL` in `_ask_api`, the `_PRICES` lookup (cost becomes `None`) and the defaults in `pipeline/params.py` and `ANTHROPIC_MODEL`.
     Map our names to provider IDs in one place rather than changing the names everywhere.
   - Drop `betas=["server-side-fallback-2026-07-01"]` and `fallbacks="default"` on that path: Bedrock does not support server-side fallback, and the cyber-refusal rerun would move to a client-side retry on the fallback model.
   - Structured outputs, thinking and prompt caching are supported on Bedrock; adaptive thinking on each model there is **unconfirmed**.
   - Server-side web search and web fetch are not, which affects only `api/scripts/collect_links.py`, not the weekly run.
   - Vertex AI needs the same kind of change; its feature list was not checked.
2. **Non-root containers** (if the platform enforces them), `Dockerfile`.
   The image runs as root and `/data` does not exist until the app makes it.
   Add a user, `RUN mkdir /data && chown <user> /data` and `USER <user>`.
   A read-only root filesystem would need a writable mount at `/data`, which Northflank's docs do not describe for jobs (**unconfirmed**).
3. **Egress without Hugging Face**: done.
   The image builds the embedding model into `/app/models` (`INTELMONITOR_MODELS_DIR`), and it loads with networking off.

Recommended on both platforms, before the first scheduled firing:

4. **Fail before spending**: done.
   `im weekly` makes one Exa search and one Claude Haiku call before the first due week and exits 1 if either fails (`api/intelmonitor/preflight.py`).
   Exa answers 401 or 403 for a bad key and 402 when out of credits; those stop the ingest, other Exa errors count as failed searches, and none is cached.
   `EXA_API_KEY_2` takes over when Exa refuses the first key.
5. **Fail when sources fail**: done, with one difference from the proposal.
   An official source not reached, most news searches failed, or a series key missing or refused makes `im weekly` exit 1 after computing and publishing the week, listing what was missing.
   Publishing keeps the sources that did arrive; the non-zero exit still raises the platform's alert.
6. **Guard `latest.json`**: done.
   `publish` re-reads `latest.json` before repointing it and raises `LatestMoved` if it no longer names the store the run pulled or last published.
7. **Comments that name Railway**: the `cli.py` and `config.py` comments are fixed; the `Dockerfile` header still names `scripts/deploy.sh`, which is right for Railway.
8. **`scripts/deploy.sh`** is Railway only (`railway up` from a staged copy, which includes uncommitted files).
   On Northflank, deploys come from commits on the tracked branch; keep the script for Railway, and document the push flow in deploy.md when moving.

### 2.2 Config and settings

Everything in section 1.
The settings that are easy to get wrong:

- Cron time zone `UTC`, set explicitly.
- Run on image change: Never.
- Retry limit 0, time limit 15300 s, concurrency Forbid.
- Ephemeral storage 5 GB on both workloads (default 1 GB).
- A startup probe on `site` sized for the download, not only a liveness probe, or the container is killed mid-download and restarts in a loop.
- `site` at 1 instance.
- Secret groups restricted, so the site never inherits the API keys or the write keys.

### 2.3 On Railway first

These gaps apply to both platforms, so close them where it runs now:

1. **The `site` service still builds with Railpack and has no healthcheck** (from `railway environment config`).
   With `railway.json` gone, running `scripts/deploy.sh` before step 2 of the first deploy in deploy.md would build `site` with Railpack, not the Dockerfile.
   Apply the Dockerfile builder and the `/api/health` 300 s healthcheck before any deploy.
2. **The first firing rescreens about 9,000 documents with paid calls**, because no screen call in the current seed is cached.
   Seed the screen cache, or accept and budget the cost, before setting the cron schedule.
3. **Items 4 to 6 in 2.1**: done.
4. **A write round trip on the real bucket**: done.
   On 23 Nov 2026 a 40 MB multipart upload (5 parts) with `sha256` and `run-id` metadata, a `stored()` read of the metadata, a checked download and a delete all worked on the Railway bucket, which is empty again.
   `GET latest.json` (404 NoSuchKey) and listing work with both path-style (boto3's default here) and virtual-host requests.
5. **Bake the embedding model into the image** (2.1 item 3).

## 3. Open questions for the platform team

1. Which product is the target: Northflank's managed cloud, BYOC in a government cloud account, or a forward-deployed control plane? (name)
2. Which region or cluster? (name)
3. Is outbound traffic from workloads restricted by default? (yes/no)
4. If so, can the runner get an FQDN allow-list for the hosts in "Egress the runner needs"? (yes/no)
5. Is `api.anthropic.com` reachable, and if not, which Claude endpoint is approved (Bedrock, Vertex, other)? (yes/no, then name)
6. Which object store is approved: the MinIO addon or the cloud account's S3? (name)
7. With the MinIO addon, may we create a second MinIO user with a bucket-scoped read-only policy? (yes/no)
8. Is the MinIO addon's internal TLS certificate publicly trusted? (yes/no; if no, the CA file's name)
9. With cloud S3, is workload identity available to services and jobs? (yes/no)
10. Must containers run as non-root? (yes/no)
11. Must containers run with a read-only root filesystem? (yes/no)
12. Is 5 GB of ephemeral storage per container allowed? (yes/no)
13. Can builds pull from Docker Hub, `ghcr.io`, the npm registry and PyPI, or must base images and packages come from an internal mirror? (yes/no, then name)
14. Does the cron job form or API accept the `timeZone` field? (yes/no)
15. Which channel receives job failure notifications: Slack, Teams, webhook or email? (name)
16. May the site have a public port, or must it be VPC only behind the agency's SSO? (name)
17. May third-party API keys (Anthropic, Exa, FRED, CoinGecko, MAS) be stored in Northflank secret groups, or must they come from an external secret manager? (yes/no, then name)
18. Is there a cap on the startup probe's max failures below 60? (yes/no)

## 4. Sources

- [Run an image once or on a schedule](https://northflank.com/docs/v1/application/run/run-an-image-once-or-on-a-schedule): schedule syntax, concurrency policy (not applied to manual runs), retry and time limits, run overrides.
- [Create cron job (API)](https://northflank.com/docs/v1/api/project/jobs/create-cron-job): `schedule` pattern, `timeZone`, `concurrencyPolicy`, `backoffLimit`, `activeDeadlineSeconds`, `skipMissedRuns`, `runOnSourceChange`, `ephemeralStorage.storageSize`, Dockerfile build settings.
- [Run job (API)](https://northflank.com/docs/v1/api/project/jobs/run-job): manual runs, `northflank start job run`.
- [Increase storage](https://northflank.com/docs/v1/application/scale/increase-storage): 1 GB ephemeral default, eviction past the limit.
- [Configure ephemeral storage (changelog)](https://northflank.com/changelog/configurable-ephemeral-storage-post-gis-public-my-sql-and-postgres): 1, 5, 10 and 20 GB options.
- [Configure health checks](https://northflank.com/docs/v1/application/observe/configure-health-checks): startup, readiness and liveness probes and their settings.
- [Override command or entrypoint](https://northflank.com/docs/v1/application/run/override-command-entrypoint): custom command, exec form.
- [Build with a Dockerfile](https://northflank.com/docs/v1/application/build/build-with-a-dockerfile): Dockerfile path, build context, target stage.
- [Run an image continuously](https://northflank.com/docs/v1/application/run/run-an-image-continuously) and [Configure ports](https://northflank.com/docs/v1/application/network/configure-ports): deployment services, public, private and VPC ports.
- [Deploy to a region](https://northflank.com/docs/v1/application/run/deploy-to-a-region): project regions, builds outside the project.
- [Manage secret groups](https://northflank.com/docs/v1/application/secure/manage-secret-groups): types, scope, restrictions, API replace semantics.
- [Connect database secrets to workloads](https://northflank.com/docs/v1/application/databases-and-persistence/connect-database-secrets-to-workloads): linking addon secrets with aliases.
- [Deploy MinIO on Northflank](https://northflank.com/docs/v1/application/databases-and-persistence/deploy-databases-on-northflank/deploy-minio-on-northflank): addon, `MINIO_ENDPOINT`, port 9000, `mc admin`.
- [Connect Node.js to MinIO with TLS (guide, March 2022)](https://northflank.com/guides/connect-nodejs-to-minio-with-tls-using-aws-s3): the addon supports path-style only.
- [Use a MinIO S3 bucket on Northflank (guide, April 2023)](https://northflank.com/guides/use-a-minio-s3-bucket-on-northflank): `mc mb`, bucket policies.
- [Configure workload identity](https://northflank.com/docs/v1/application/bring-your-own-cloud/configure-workload-identity): `AWS_ROLE_ARN`, `AWS_WEB_IDENTITY_TOKEN_FILE`.
- [Configure network policies](https://northflank.com/docs/v1/application/network/configure-network-policies): BYOC egress rules by IP, CIDR or FQDN.
- [Configure egress IPs](https://northflank.com/docs/v1/application/network/configure-egress-ips): static egress IPs, addons excluded.
- [Configure notification integrations](https://northflank.com/docs/v1/application/observe/configure-notification-integrations) and [Create notification integration (API)](https://northflank.com/docs/v1/api/team/integrations/create-notification-integration): Slack, Discord, Teams, webhook; job run and job container events.
- [Enterprise](https://northflank.com/docs/v1/application/enterprise): forward-deployed control plane for air-gapped and government networks.
- [Claude in Amazon Bedrock](https://platform.claude.com/docs/en/build-with-claude/claude-in-amazon-bedrock): `anthropic.` model IDs, supported features, no server-side fallback or server tools.
- [Railway storage buckets](https://docs.railway.com/storage-buckets): virtual-host URLs for new buckets.
