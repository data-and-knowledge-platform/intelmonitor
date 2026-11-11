# Pipeline costs

What each pipeline stage costs on the Anthropic API once Jev is gone, for one weekly run and for the October seed.
Written 4 Nov 2026 for [Pipeline cost breakdown](../wayfind/topics-watchlists-views/issues/13-pipeline-costs.md).

## Totals

| | Documents | Claude Haiku 5.5 | Claude Sonnet 5.5 | Total | Range |
|---|---|---|---|---|---|
| One weekly run | about 425 | $0.45 | $8.45 | about $9 | $7 to $11 |
| October seed (5 weekly runs) | about 2,100 | $2.24 | $42.21 | about $44 | $35 to $55 |

Sonnet stages are about 95% of the bill.
Moving Jev's stages to Haiku adds about $0.45 a week and removes the Sonnet route stage, which cost about $1.70 a week.
Exa and Tavily ingest costs are not included.

## Prices

From the claude-api skill (cached 6 Oct 2026), per million tokens:

| Model | Input | Output | Cache read | Cache write (5 min) |
|---|---|---|---|---|
| Claude Haiku 5.5 (`claude-haiku-5-5`) | $0.10 | $0.50 | $0.01 | $0.125 |
| Claude Sonnet 5.5 (`claude-sonnet-5-5`) | $2.00 | $10.00 | $0.20 | $2.50 |

Haiku's prices hold for prompts up to 100k tokens; every pipeline call is far below that.
The Message Batches API takes 50% off every token, cache reads and writes included.
No stage uses Claude Opus 5.5.

## One weekly run

About 425 new documents.

| Stage | Model | Paid per | Calls | Input tokens | Output tokens | Cost |
|---|---|---|---|---|---|---|
| screen | Haiku 5.5 | document | 425 | 6.41M (5.61M cached) | 0.04M | $0.16 |
| same_event | Haiku 5.5 | finding pair | 3,436 | 1.89M | 0.05M | $0.21 |
| priority | Haiku 5.5 | new or updated development | 382 | 0.54M | 0.02M | $0.07 |
| theme | Haiku 5.5 | new or updated development | 382 | 0.10M | 0.004M | $0.01 |
| extract | Sonnet 5.5 | relevant document | 280 | 1.13M | 0.20M | $3.61 |
| headline | Sonnet 5.5 | new or updated development | 382 | 1.34M | 0.05M | $2.19 |
| impact | Sonnet 5.5 | new or updated Medium or High development | 80 | 0.31M | 0.03M | $0.82 |
| high check | Sonnet 5.5 | development Haiku rates High | 8 | 0.01M | 0.001M | $0.03 |
| brief | Sonnet 5.5 | subject, every run | 125 | 0.48M | 0.05M | $1.22 |
| note | Sonnet 5.5 | subject with listed series, every run | 55 | 0.10M | 0.04M | $0.58 |
| **Total** | | | 5,555 | 12.3M | 0.49M | **$8.90** |

## October seed

Weekly runs from 1 Oct 2026 to the week ending 4 Nov 2026: five runs over about 2,124 documents (338, 298, 509 and about 554 measured, 425 assumed for the last week).

| Stage | Model | Calls | Input tokens | Output tokens | Cost |
|---|---|---|---|---|---|
| screen | Haiku 5.5 | 2,124 | 32.1M | 0.21M | $0.79 |
| same_event | Haiku 5.5 | 17,173 | 9.44M | 0.26M | $1.07 |
| priority | Haiku 5.5 | 1,912 | 2.68M | 0.12M | $0.32 |
| theme | Haiku 5.5 | 1,912 | 0.48M | 0.02M | $0.06 |
| extract | Sonnet 5.5 | 1,402 | 5.67M | 0.97M | $18.06 |
| headline | Sonnet 5.5 | 1,912 | 6.68M | 0.26M | $10.93 |
| impact | Sonnet 5.5 | 401 | 1.56M | 0.17M | $4.09 |
| high check | Sonnet 5.5 | 38 | 0.05M | 0.006M | $0.13 |
| brief | Sonnet 5.5 | 625 | 2.41M | 0.23M | $6.09 |
| note | Sonnet 5.5 | 275 | 0.49M | 0.19M | $2.92 |
| **Total** | | 27,774 | 61.5M | 2.44M | **$44.46** |

The seed pays every call afresh: the new topics, prompts and models change every input hash, so nothing replays from the old store.

## Stages that cost nothing now

| Stage | Why |
|---|---|
| route | Goes: Haiku decides relevance and subjects itself (call shapes below). |
| priority_check | Becomes the high check. |
| custom_match, custom_draft, custom_answer | Custom questions are removed. |
| answer | Standing questions are removed. |
| materiality | Old name of priority; nothing calls it. |
| summary | Replaced by brief; nothing calls it. |

## Per document or per run

- screen and extract are paid once per document: `Inference` records each call by its input hash, and later runs replay it.
- same_event is paid once per candidate pair.
- priority, theme, headline and impact are paid for each new development, and again when a development gains findings, because its input changes.
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
  - 21% of those are Medium or High and 1% to 2% High;
  - 117 subjects, about 125 brief calls a run with retries, about 55 notes a run.
- Sonnet unit costs are measured from `inference_records` for runs 2026-10-19 and 2026-10-26, on calls made through the Anthropic API since 26 Oct, using the recorded `cost_usd`, which counts cache reads and writes: extract $0.0129, headline $0.0057, impact $0.0102, brief $0.0097, note $0.0106 and priority_check $0.0034 a call.
  Earlier calls went through the Claude login, whose reported cost is not API billing, so they are left out.
- Run-level totals in `inference_records` are not used as a weekly run: each run was computed two or three times during development, and some calls screened X posts since deleted.
- Haiku stages have no records, so their tokens are estimated from the prompts each call shape would send.
  Claude's tokenizer reads about 3.1 characters a token (extract and route prompts against their documents).
  Jev's token counts are not reused: its prompt carried every question's text, and it wrote 2.4k output tokens a document.

| Haiku stage | Input a call | Output a call |
|---|---|---|
| screen | 13,200 cached (instructions and 117 subjects) plus 1,890 (document, mean 5.8k characters) | 100 |
| same_event | 550 | 15 |
| priority | 1,400 | 60 |
| theme | 250 | 10 |

## Uncertainty

- Document volume is the largest factor: the fresh ingest uses the replacement queries and 15 new official sources, so a week may bring more or fewer than 425 documents.
  Each document costs about $0.017, almost all extract and headline.
- The 30 topics may raise relevance and subjects per finding, which raises extract, priority and impact.
- Haiku 5.5's tokenizer is not measured; Haiku stages could be 30% higher or lower and would still cost under $1 a week.
- If Haiku runs with thinking on (adaptive at medium effort, its default), about 500 thinking tokens a call add about $1.20 a week; the call shapes turn thinking off.
- Recomputes after prompt changes pay again for every stage whose input changed.
- Rate figures come from one or two runs; they are means, not bounds.

## Prompt caching

- `pipeline/claude.py` already marks each call's system prompt for caching; the Sonnet unit costs above include it (headline saves 32%; extract, impact and brief 14% to 15%).
  Prompts under 512 tokens do not cache, so note and route saved nothing.
- Haiku screen depends on it: its fixed part (instructions, relevance criterion, 117 subjects, categories, jurisdictions, stages) goes in the system prompt, byte for byte the same on every call.
  Cached, a document costs $0.00037; uncached, $0.0016.
- same_event, priority and theme prompts are below 512 tokens, so caching does not apply.
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

- One call per development, rating all its subjects at once, keyed on development id and finding count as now.
- System: `PRIORITY_SYSTEM` with the rubric and guide; user: `priority_state` and each subject with its role and whether the development is adverse to it.
- Output: the existing `PriorityDecision` schema, a list of `{subject_id, priority}` with High, Medium or Low.
- The promotional, market-move and detector rules still apply afterwards.
- `priority_score` and `priority_confidence` lose Jev's meaning; the score becomes the level.
- Routing: `PRIORITY_SURE` goes, and priority_check becomes a high check: Sonnet 5.5 confirms or lowers each subject Haiku rates High.
  High drives list-page ranking, and the check costs about $0.03 a week.

### theme

- One call per development, on its title, as now.
- Output: `theme`, one of the six graph themes.
- No uncertain band.

### custom_match and materiality

- No calls: custom questions are removed, and materiality is the old name of priority.

After these changes `Inference.jev`, `pipeline/jev.py`, `JEV_CONCURRENCY` and the OpenRouter settings have no caller.
