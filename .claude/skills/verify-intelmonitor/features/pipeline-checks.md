# Pipeline checks

Pipeline behavior with no UI entry point is proven by three scripted checks, `pnpm -C web verify check series|past-run|detector`, which wrap `uv run im check <name>` and print one JSON object.
Each works in a temporary scratch store that is deleted afterwards, makes no network request and no paid model call, and leaves the store as it was.
They read the store `INTELMONITOR_DATA_DIR` names (else `data/`), so set it as for the API.
`api/scripts/check_endpoints.py` reads every endpoint for a sample of subjects, checks the catalogue and the lists, and that the API serves only GET operations; it calls no model.
`pnpm -C web e2e` is the 19-step regression walkthrough, ending with the three checks and `check_endpoints.py`.
No user can start a run from the web app; the weekly runs are computed by `im weekly`.
Screen, same_event and theme run on Claude Haiku 5.5 and priority on Claude Sonnet 5.5 (`docs/pipeline-costs.md`); indirect impact is a fixed table with no model call, and there is no high check (`priority_check`) or Jev.

## Sub-features

- `check-series`: a backfill and a daily fetch replayed from cached responses in `data/raw/` reach no source, the second fetch adds nothing, and the values match the store.
- `check-past-run`: the run before the latest (the latest itself when the store holds one run) recomputed in a copy of the store, replaying only recorded model calls, uses nothing known after its date and equals the stored run, indirect impacts included.
- `check-detector`: fixture loan-to-deposit values crossing 90% fire the rule into one High development.
- `check-endpoints`: `uv run python scripts/check_endpoints.py [--base <api url>] [--out <file>]` from `api/`, writing `artifacts/api-check/<timestamp>/results.json` by default.
- `e2e`: `pnpm -C web e2e`, the Playwright walkthrough with an HTML report.

## How to get to it (user POV)

- The checks have no UI; a developer runs them in a terminal.

## Driving it with verify

Preconditions:

- `pnpm -C web verify doctor` is ok. The checks need `uv` and the API's environment, not the servers; `check_endpoints.py` and e2e need both servers.
- The store holds the six weekly runs from 4 Oct to 8 Nov, with `previous_run_id` 1 Nov on the latest (`pnpm -C web verify api /runs/latest`).

- **Series fetch replay.** `pnpm -C web verify check series` (a few seconds). Assert: `backfill.network` 0, `backfill.missed` 0, `backfill.cached` > 0, `backfill.failures` `{}`, and the same four for `fetch`; `backfill.added` equals `observations`; `series` at least `catalogue` minus one (the hand-collected series has no fetch); `fetch.added` and `fetch.revised` both 0; each `per_100` value (`fx.sgd.myr`, `fx.sgd.thb` on 2026-09-18) equal to `pnpm -C web verify api "/series/<id>?from=2026-09-18&to=2026-09-18"` `observations[0].value` with the same `unit`; `compared` > 1000; `differences` `[]`.
- **Past run.** `pnpm -C web verify check past-run` (about 3 minutes on the six-run store). Assert: `as_of` equals the latest run's `previous_run_id` (`2026-11-01`); `later_documents` > 0; every log line that counts calls reads ` 0 calls, <n> cached)`; the last `log` line contains `] complete: `; `late` is `{sources: 0, indicators: 0}`; `differences` `[]`; `developments` and `indicators` > 0.
- **Detector on fixtures.** `pnpm -C web verify check detector` (about 2 s). Assert: `detections` is one line, `Loan-to-deposit ratio, all currencies rose above 90.0% to 90.4% for Jul 2026, ...`; `developments` is one entry with that title, `materiality_label` `High`, `materiality_rule` `ltd-above-90`, `needs_attention` true, `subjects` `["singapore"]`, `sources` 1.
- **Endpoints.** From `api/`, `uv run python scripts/check_endpoints.py`: every step prints `ok` (`read only`, `catalogue`, `lists`, one per sample subject, `runs and data sources`) and it exits 0.
- **Full sweep.** `INTELMONITOR_DATA_DIR=<the API's> pnpm -C web e2e` (about 4 minutes). Playwright runs the one walkthrough test (19 steps, serial, 1440x900) and writes `artifacts/e2e/<timestamp>/` with `index.html` and `summary.json` (each step's status, error, screenshot and the `im.state.v1` after it), `steps/NN.png`, `video.webm` for the test's own browser context and `video-<name>.webm` for each other one (`two-tabs-1`, `two-tabs-2`, `stored-state`, `blocked-storage`), `trace.zip`, `requests.json`, `series-fetch.json`, `detector.json`, `past-run.json` and `api-check.json`.
- **Proof.** Save each check's JSON (`pnpm -C web verify check detector > artifacts/verify/<run>/detector.json`) and quote the asserted fields; for e2e, link the run's `index.html`.

## Gotchas

- `pnpm -C web e2e` writes nothing to the store and needs no backup: the pages only read, and the run fails on any API request that is not a GET or that reaches a removed endpoint (`requests.json` lists every request with its browser context). Each run starts from empty browser storage. It is one test, so steps cannot be run alone with `-g`.
- Run e2e and the checks with the `INTELMONITOR_DATA_DIR` the API serves; otherwise `past-run` and `series` read another store than the pages show, and `past-run`'s date will not match.
- `past-run` replays recorded calls only: a prompt or input changed since the run was computed raises `UnrecordedCall` instead of calling a model. Recompute the run with `im run` (paid) or leave the change out.
- `/runs` lists one row per execution, so the same `as_of` can repeat; match on `as_of` and `status: complete`.
- `series` compares against the store's observations up to 28 Sep. A store rebuilt or restored from another snapshot can make `differences` non-empty without a code bug; check `pnpm -C web verify doctor`'s `store` modified time first.
- The checks print only JSON on success. A Python traceback means the check itself failed (for example a missing cache in `data/raw/` shows as `missed` > 0 or an exception), not the harness.
