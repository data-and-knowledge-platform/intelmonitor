# Pipeline costs

What each pipeline stage costs on the Anthropic API once Jev is gone, for one weekly run and for the six-week seed.
Written 4 Nov 2026 for [Pipeline cost breakdown](../wayfind/topics-watchlists-views/issues/13-pipeline-costs.md).
Updated 11 Nov 2026 for the team's deck (spec steps 2a, 3 and 3a): indirect impact is a fixed table with no model call, priority is one Sonnet call per development and entity with no high check, links are rated once, and the seed has six weeks.

Measured on 11 Nov 2026 by the October rebuild: the totals below are what the six runs cost, and they replace the estimates, which were about four times too low because the 30 topics bring in about 1,560 documents a week, not 425.
The sections after "Measured, October rebuild" keep the estimate and its method as they were written.

Noted 16 Nov 2026: trials of link strength judged per development cost $17.56 (measured); the work is not merged, and its code and cost record are on the branch `park/per-development-strength`.

Noted 17 Nov 2026: the theme stage no longer runs.
It labelled each development with a theme for the graph view, which was removed on 16 Nov 2026 (spec step 3b of link-types-and-inferred-impact).
Its rows below are what it cost while it ran ($0.64 of the October rebuild, measured), and a weekly run now costs that much less.

## Totals

| | Documents | Claude Haiku 5.5 | Claude Sonnet 5.5 | Total | Basis |
|---|---|---|---|---|---|
| One weekly run | about 1,560 | $1.70 | $38 | about $40 | measured, $38 to $42 over five full weeks |
| Seed (6 weekly runs, 4 Oct to 8 Nov) | 9,277 | $9.42 | $214.07 | $223.49 | measured |
| Link rating (once) | | | $2.49 | $2.49 | measured |

Sonnet stages are about 96% of the bill.
Exa ingest is about $5 a week on top (about 700 searches at $0.007), so about $29 for the rebuild.

## Measured, October rebuild

Runs dated 4, 11, 18 and 25 Oct and 1 and 8 Nov 2026, computed on 11 Nov 2026; figures are sums over `inference_records` for the six runs.
The first run covers four days (1 to 4 Oct) and cost $25.32; the five full weeks cost $38.76, $38.88, $41.55, $40.99 and $37.99.
The corpus holds 9,277 documents published 1 Oct to 8 Nov; 79% were screened relevant and went to extract; the latest run holds 10,224 developments, 92 of them High.

| Stage | Model | Calls | Input tokens | Output tokens | Cost |
|---|---|---|---|---|---|
| extract | Sonnet 5.5 | 7,299 | 37.0M | 6.26M | $96.87 |
| headline | Sonnet 5.5 | 11,038 | 41.9M | 1.63M | $69.44 |
| priority | Sonnet 5.5 | 15,212 | 18.2M | 1.25M | $23.00 |
| brief | Sonnet 5.5 | 929 | 7.8M | 0.77M | $21.86 |
| same_event | Haiku 5.5 | 76,245 | 49.3M | 0.69M | $5.28 |
| screen | Haiku 5.5 | 9,280 | 168.4M | 0.70M | $3.50 |
| note | Sonnet 5.5 | 150 | 0.6M | 0.16M | $2.80 |
| theme | Haiku 5.5 | 10,430 | 5.8M | 0.12M | $0.64 |
| screen, refusals answered on Sonnet | Sonnet 5.5 | 2 | 0.04M | 0.00M | $0.09 |
| **Total** | | 130,585 | | | **$223.49** |

A document costs about $0.024, most of it extract and headline: $0.013 to extract a relevant document and $0.006 for each development's headline.

## Estimate of 4 Nov 2026

The rest of this document is the estimate made before the rebuild, kept for its method and call shapes.
It assumed about 425 documents a week and gave about $9 a weekly run and about $54 for the six-week seed.
Jev's stages on Haiku were estimated at about $0.38 a week; the Sonnet route stage they replace cost about $1.70 a week.
Exa ingest costs are not included in the estimate.

## Prices

From the claude-api skill (cached 6 Oct 2026), per million tokens:

| Model | Input | Output | Cache read | Cache write (5 min) |
|---|---|---|---|---|
| Claude Haiku 5.5 (`claude-haiku-5-5`) | $0.10 | $0.50 | $0.01 | $0.125 |
| Claude Sonnet 5.5 (`claude-sonnet-5-5`) | $2.00 | $10.00 | $0.10 | $2.50 |

Haiku's prices hold for prompts up to 100k tokens; every pipeline call is far below that.
The Message Batches API takes 50% off every token, cache reads and writes included.
No stage uses Claude Opus 5.5.

## One weekly run

About 425 new documents.

| Stage | Model | Paid per | Calls | Input tokens | Output tokens | Cost |
|---|---|---|---|---|---|---|
| screen | Haiku 5.5 | document | 425 | 6.41M (5.61M cached) | 0.04M | $0.16 |
| same_event | Haiku 5.5 | finding pair | 3,436 | 1.89M | 0.05M | $0.21 |
| theme | Haiku 5.5 | new or updated development | 382 | 0.10M | 0.004M | $0.01 |
| extract | Sonnet 5.5 | relevant document | 280 | 1.13M | 0.20M | $3.61 |
| headline | Sonnet 5.5 | new or updated development | 382 | 1.34M | 0.05M | $2.19 |
| priority | Sonnet 5.5 | new or updated development and entity | 460 | 0.58M | 0.04M | $1.10 |
| brief | Sonnet 5.5 | subject, every run | 125 | 0.48M | 0.05M | $1.22 |
| note | Sonnet 5.5 | subject with listed series, every run | 55 | 0.10M | 0.04M | $0.58 |
| **Total** | | | 5,545 | 12.0M | 0.47M | **$9.08** |

## Seed

Weekly runs dated 4, 11, 18 and 25 Oct and 1 and 8 Nov 2026: six runs over about 2,549 documents (338, 298, 509 and about 554 measured, 425 assumed for each of the last two weeks).
Each stage is the five-week figure of 4 Nov plus one weekly run; priority is counted afresh at 1.2 calls per development.

| Stage | Model | Calls | Input tokens | Output tokens | Cost |
|---|---|---|---|---|---|
| screen | Haiku 5.5 | 2,549 | 38.5M | 0.25M | $0.95 |
| same_event | Haiku 5.5 | 20,609 | 11.3M | 0.31M | $1.28 |
| theme | Haiku 5.5 | 2,294 | 0.58M | 0.02M | $0.07 |
| extract | Sonnet 5.5 | 1,682 | 6.80M | 1.17M | $21.67 |
| headline | Sonnet 5.5 | 2,294 | 8.02M | 0.31M | $13.12 |
| priority | Sonnet 5.5 | 2,753 | 3.46M | 0.25M | $6.58 |
| brief | Sonnet 5.5 | 750 | 2.89M | 0.28M | $7.31 |
| note | Sonnet 5.5 | 330 | 0.59M | 0.23M | $3.50 |
| **Total** | | 33,261 | 72.2M | 2.82M | **$54.48** |

The seed pays every call afresh: the new topics, prompts and models change every input hash, so nothing replays from the old store.

## Link rating, once

`scripts/rate_relationships.py` rated all 812 rateable links on Claude Sonnet 5.5 at effort medium on 11 Nov 2026 for $2.49, measured, with no failures (spec step 2a).
Pipeline runs read the ratings from `api/seed/collected/transmission.yaml` and call no model for them.
A rerun pays only for links that are new or whose input changed.

## Stages that cost nothing now

| Stage | Why |
|---|---|
| route | Goes: Haiku decides relevance and subjects itself (call shapes below). |
| priority_check, high check | Goes: priority is one Sonnet call per development and entity, with no check. |
| impact | Indirect impact is a fixed table of the direct level by the link's strength (spec step 3a); it was $0.82 a week and $4.09 for the five-week seed. |
| theme | Goes with the graph view, whose themes it labelled (16 Nov 2026); it cost $0.64 over the six runs of the October rebuild. |
| custom_match, custom_draft, custom_answer | Custom questions are removed. |
| answer | Standing questions are removed. |
| materiality | Old name of priority; nothing calls it. |
| summary | Replaced by brief; nothing calls it. |

## Per document or per run

- screen and extract are paid once per document: `Inference` records each call by its input hash, and later runs replay it.
- same_event is paid once per candidate pair.
- headline is paid for each new development, and priority for each new development and entity, and again when a development gains findings, because its input changes.
- brief and note are paid on every run, whatever arrived.

## Method

- Documents per week come from `source_documents` published dates (non-data documents, 7-day windows from 1 Oct): 338, 298, 509, and 396 in the first 5 days of the fourth week.
  September weeks held 217 to 294.
  A typical week is taken as 425, with 300 and 560 as the range.
- Rates from the store, read only:
  - 66% of October documents are relevant (`document_tags`);
  - 2.45 findings per relevant document;
  - about 5 candidate pairs per new finding (5,642 same_event calls for the 1,066 findings of run 2026-10-26);
  - 0.9 new or updated developments per new document (`run_changes`: 648 for run 2026-10-26, 218 to 285 in the September weekly runs);
  - 1.2 priority calls per development: 0.98 entity tags per development and one topic call for each of the 23% that name no entity (run 2026-10-26, old topics);
  - 117 subjects, about 125 brief calls a run with retries, about 55 notes a run.
- Sonnet unit costs are measured from `inference_records` for runs 2026-10-19 and 2026-10-26, on calls made through the Anthropic API since 26 Oct, using the recorded `cost_usd`, which counts cache reads and writes: extract $0.0129, headline $0.0057, brief $0.0097 and note $0.0106 a call.
  Earlier calls went through the Claude login, whose reported cost is not API billing, so they are left out.
- The Sonnet priority call is measured from the 206 development and entity pairs rated in spec step 3's check on 10 Nov: $0.00239 a call, 1,258 input and 92 output tokens on average.
- Run-level totals in `inference_records` are not used as a weekly run: each run was computed two or three times during development, and some calls screened X posts since deleted.
- Haiku stages have no records, so their tokens are estimated from the prompts each call shape would send.
  Claude's tokenizer reads about 3.1 characters a token (extract and route prompts against their documents).
  Jev's token counts are not reused: its prompt carried every question's text, and it wrote 2.4k output tokens a document.

| Haiku stage | Input a call | Output a call |
|---|---|---|
| screen | 13,200 cached (instructions and 117 subjects) plus 1,890 (document, mean 5.8k characters) | 100 |
| same_event | 550 | 15 |
| theme | 250 | 10 |

## Uncertainty

- Document volume is the largest factor: the fresh ingest uses the replacement queries and 15 new official sources, so a week may bring more or fewer than 425 documents.
  Each document costs about $0.017, almost all extract and headline.
- The 30 topics may raise relevance and subjects per finding, which raises extract and priority.
- Haiku 5.5's tokenizer is not measured; Haiku stages could be 30% higher or lower and would still cost under $1 a week.
- If Haiku runs with thinking on (adaptive at medium effort, its default), about 500 thinking tokens a call add about $1.20 a week; the call shapes turn thinking off.
- Recomputes after prompt changes pay again for every stage whose input changed.
- Rate figures come from one or two runs; they are means, not bounds.

## Prompt caching

- `pipeline/claude.py` already marks each call's system prompt for caching; the Sonnet unit costs above include it (headline saves 32%; extract and brief 14% to 15%).
  Prompts under 512 tokens do not cache, so note and route saved nothing.
- Haiku screen depends on it: its fixed part (instructions, relevance criterion, 117 subjects, categories, jurisdictions, stages) goes in the system prompt, byte for byte the same on every call.
  Cached, a document costs $0.00037; uncached, $0.0016.
- same_event prompts are below 512 tokens, so caching does not apply.
- extract puts its fixed instructions after the document in the user prompt, so about 2k tokens a call are never cached.
  Moving them into the system prompt would save about $1 a week; this is optional.
- The Batches API would halve the bill for an unattended weekly run, at the cost of polling and waits of minutes to hours per stage; not proposed now.

## Call shapes for the former Jev stages

Each is one structured Haiku call through `Inference.claude` with `model="claude-haiku-5-5"`, thinking disabled, and `output_config.format` set from a Pydantic schema, as the Sonnet stages do today.
Calls stay recorded and replayed by input hash.
Haiku 5.5 has no server-side refusal fallback, so a Haiku refusal is retried once on Sonnet 5.5.

### screen

- One call per document, as now.
- System, cached: the task, the relevance criterion, each subject's id and description, the categories, the jurisdictions and the regulatory stages.
- User: the screen state (title, source, date, text up to `SCREEN_STATE_TOKENS`); the cap stays for cost, and cuts 0.4% to 1.2% of documents.
- Output: `relevant` (bool), `subjects` (ids, most central first, empty when not relevant), `category` (enum), `stage` (enum or null, only for regulatory), `jurisdictions` (codes).
- The order of `subjects` gives each tag a score, so the list pages' tie-break on screen score keeps working.
- Routing: the route stage and `ROUTE_LOW` and `ROUTE_HIGH` go, since one Haiku call now decides what Jev and Sonnet split between them.
- Before the seed, run the Haiku screen over the 2,818 stored documents (about $1) and compare with `document_tags`, which holds the Jev and Sonnet outcomes; a Sonnet check returns only if that comparison shows a gap, aimed at that gap.

### same_event

- One call per candidate pair, as now; candidate selection by embeddings is unchanged.
- Input: the two findings as `_describe` writes them.
- Output: `same_event` (bool); `SAME_EVENT_THRESHOLD` goes.
- Batching a finding with its up to 5 candidates would cut calls about fivefold, but the stage costs $0.21 a week and per-pair records let each weekly run pay only new pairs.
- No uncertain band.

### priority

- Superseded on 11 Nov 2026 by the team's slide 7 prompt ([The direct H/M/L prompt](../wayfind/topics-watchlists-views/issues/19-direct-priority-prompt.md)): one Claude Sonnet 5.5 call per development and entity, not Haiku, with a level and a one-sentence reason.
- No high check: the `priority_check` stage, `PriorityDecision` and `PRIORITY_SURE` go.
- `priority_score` and `priority_confidence` lose Jev's meaning; the score becomes the level.

### theme

- Removed on 16 Nov 2026 with the graph view, whose themes it labelled.

### custom_match and materiality

- No calls: custom questions are removed, and materiality is the old name of priority.

After these changes `Inference.jev`, `pipeline/jev.py`, `JEV_CONCURRENCY` and the OpenRouter settings have no caller.
