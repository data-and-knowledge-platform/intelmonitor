# IntelMonitor: what is built and what is left

State of the repo on 25 Nov 2026, branch `feat/link-types-and-inferred-impact`.
This file says what the system does now and what work remains.
The reasons behind each choice are in [decisions.md](decisions.md).
Terms follow [GLOSSARY.md](../GLOSSARY.md).

## Purpose and scope

IntelMonitor is a proof of concept for the Monetary Authority of Singapore (MAS).
Each week it reads public news and regulatory documents, groups them into developments, and rates each development High, Medium or Low for every entity and topic it is about.
It then infers which linked entities the development could affect.
Staff read the result in a web app: list pages, a page per entity and topic, and a popup per development.

In scope:

- Public sources only: news found through Exa search, official feeds and sites of regulators and standard setters, cloud status pages, and a set of measured series.
- 142 entities and MAS's 30 topics.
- One weekly run, dated the Sunday that ends the week.

Out of scope for now:

- Social media. It was removed on 4 Nov 2026 ([removed-features-2026-11-04.md](removed-features-2026-11-04.md)).
- Internal MAS documents.
- Sign-in, roles and sharing. The app has one user, and watchlists live in the browser.
- Labels made by people. Where a reference label is needed, a Claude Opus agent reads the sample blind; this is not a human judgement.

## Subjects

- 142 entities in 19 groups: firms, regulators, a central bank (MAS), agencies, international bodies, payment systems, market infrastructures, countries and regions.
- 57 entities are displayed. They have a page, appear in lists and search, and can be put on a watchlist.
- 85 entities are hidden. They are searched, tagged, rated and linked, and pass inferred impact, but have no page and appear only as a name.
- 30 topics, from `MAS Topics 30 - Sheet1.csv`.
- The seed is `api/seed/subjects.yaml`.

## The weekly run

`im weekly` ingests each week that is due and computes its run.
The stages, in order:

| Stage | Model | What it does |
|---|---|---|
| Ingest | none | Exa news searches per entity and topic, official feeds and sites, status pages, measured series. |
| Detectors | none | Turn moves in the measured series into data documents. |
| Screen | Claude Haiku 5.5 | Relevance, category, subject tags and jurisdictions for each document. |
| Extract | Claude Sonnet 5.5 | Findings with quotes, dates, and each subject's role, adverse flag and tone. |
| Merge | Embeddings, then Haiku 5.5 | Findings that report the same event become one development. |
| Headline | Sonnet 5.5, low effort | Writes the headline and key points, and keeps only the subjects the development is about. |
| Priority | Sonnet 5.5, medium effort | High, Medium or Low for each subject of each development, with a reason. |
| Link types | Sonnet 5.5, high effort | For each entity at Medium or High, the relationship types the development could travel through. |
| Impact | none | Inferred impact on linked entities, from a fixed table. |
| Briefs | Sonnet 5.5 | Each subject's brief and notes for the week. |

Every model call is recorded in the store with its input.
A later run replays a recorded call for free when its input has not changed.
Measured costs per stage are in [pipeline-costs.md](pipeline-costs.md).

### Priority

- The live prompt is priority version 1: the team's High, Medium, Low prompt from slide 7 of their deck of 10 Nov 2026.
- Thirteen versions have been tried on a fixed sample of 300 pairs of a development and a subject.
- Version 13 is picked to replace version 1 (25 Nov 2026). It is in `api/intelmonitor/trial.py` as the candidate `local-anchored` and is not yet live.
- Switching it on means making its template `PRIORITY_PROMPT`, its schema `PriorityRating` and its effort `PRIORITY_EFFORT` in `api/intelmonitor/pipeline/params.py`.
- The versions, their results and the open points are in [prompts/priority-versions.md](prompts/priority-versions.md).

### Links

- A link joins two entities, or an entity and its sector group. It has one of 22 types in three groups: ownership and financial, operational and commercial, structural and institutional.
- The types and their meanings are from the team's deck of 16 Nov 2026 and live in `api/intelmonitor/relationships.py`.
- The build holds 1,364 links: 1,222 between two entities and 142 `belongs_to_sector`.
- Collected rows are in `api/seed/collected/`. `api/scripts/build_relationships.py` builds `api/seed/relationships.yaml` from them.
- 821 of the links were found by `api/scripts/collect_links.py` on 23 Nov 2026: one Sonnet 5.5 request per entity with web search and web fetch, then code filters and a check call per row.
- That pass covered the 57 displayed entities. The 85 hidden entities have not had a pass.
- A blind reading of 40 of the new links found 30 supported by their source.

### Link ratings

- Every link is rated in both directions as Strong, Moderate, Weak or Non-propagating.
- The rating says how readily an event at one end reaches the other.
- `api/scripts/rate_relationships.py` rates each link once with Sonnet 5.5 at high effort and writes `api/seed/collected/transmission.yaml`.
- The prompt is the transmission half of the deck's prompt, with its ten examples as typical ratings.
- All 1,364 links are rated. A second run of the script makes no call.

### Inferred impact

A development rated for entity A can reach entity B through a link between them.
No model is called; the rules are in `api/intelmonitor/pipeline/impact.py` and `params.py`.

| A's priority | Strong link | Moderate link | Weak link |
|---|---|---|---|
| High | High | Medium | Low |
| Medium | Medium | Low | none |
| Low | none | none | none |

A link is followed from A only when all of these hold:

- Its type is one the development activates for A (the link types stage).
- Its rating in the direction away from A is not Non-propagating.
- The development is adverse for A.
- A is not a hub passing along a membership link. A hub is a country, region, regulator, central bank, agency or international body. The membership types are `operates_in`, `regulated_by`, `listed_on`, `member_of` and `participates_in`.
- A is not B's home country.

Other points:

- One hop only.
- Every valid path is stored in `development_impacts`. One path per entity reached is marked best, and pages show only that one.
- A Low rating passing Low through a Strong link exists behind the switch `INDIRECT_FROM_LOW`, which is off.
- The last three conditions depart from the team's deck, and the team has not yet seen them.
- `im impacts --as-of <date>` or `--all` recomputes the inferred rows of stored runs with no model call.

On a partial preview of the week ending 8 Nov 2026, these rules gave 447 inferred rows from 104 developments: none High, 98 Medium and 349 Low.
A reading of 25 of them found 16 that make sense.

## Prompt versions

- A version is a stage, its template, its schema, the model and the effort. Its id is the first 12 hex characters of their SHA-256.
- The store records which version made each call and which versions each run used.
- `im prompts` lists versions. `im trial priority` and `im trial link-types` try a candidate on a frozen copy of the store without touching the live labels. `im prompts compare` writes a workbook of what moved.
- The frozen copy is the baseline store. It holds every tried version and its recorded calls. It is not in git.
- Commands that take `--store` open a store without running schema migrations. Never open the baseline any other way.

## Checks

| Check | Command | Model calls |
|---|---|---|
| Lint and types | `uv run ruff check .`, `uv run pyright` in `api/`; `pnpm typecheck`, `pnpm lint` in `web/` | none |
| Inferred impact rules, 72 cases | `uv run python -m intelmonitor.eval impact` | none |
| Activated link types, 18 cases | `uv run python -m intelmonitor.eval link-types` | paid |
| Subjects kept by the headline call, 4 cases | `uv run python -m intelmonitor.eval subjects` | paid |
| Every API endpoint | `uv run python scripts/check_endpoints.py` against a running server | none |
| Web walkthrough | `pnpm e2e` in `web/` | none |

`.claude/skills/verify-intelmonitor/` describes how to drive the app and what each page should show.

## Data that is not in git

| What | Where | Why it matters |
|---|---|---|
| The live store | `data/intelmonitor.db` | Six runs, 4 Oct to 8 Nov 2026, with every recorded model call. |
| The baseline store | `data/intelmonitor-baseline-89-entities-2026-11-16.db` | Priority versions 1 to 13 and the link type trials. Needed to rerun or compare a version. |
| The HTTP cache | `data/raw/` | Fetched pages and search results, so past weeks are not fetched again. |
| Link collection results | `data/link-collection/` | Per entity results, check verdicts and fetched pages. The script resumes from them. |
| Keys | `.env` | See `.env.example`. |

A new machine needs the live store copied in, or pulled from the bucket with `im pull`, or built from nothing with `im weekly`, which pays for every call again.

## Deployment

- The site is stateless and serves a published copy of the store. A runner computes the weekly run and publishes the store to an S3-compatible bucket.
- [deploy.md](deploy.md) covers the Railway demo and the bucket. [northflank.md](northflank.md) covers the Northflank setup that mirrors the intended government platform.
- A test site and a manual runner exist on Northflank's free plan. A real weekly run needs about 1 GB of memory, more than the free plan's 512 MB.
- No schedule is set yet.

## What is left

1. **Run the weeks again.** The stored runs were rated before the 142 entities, the 22 link types, the headline call's subject choice and the inferred impact rules above. Until a store is rerun, it shows no inferred impact under these rules, and `im check past-run` fails on every stored run. The plan: back up the store, run the week ending 8 Nov on a copy and record its cost, then `im clear-runs` and `im weekly --max-weeks 6`. Six runs cost $223.49 with 89 entities; the headline calls alone were $69.44 and none of them replays.
2. **Switch on priority version 13** for the weeks after that rerun.
3. **Decide the Low row** of the inferred impact table from the rerun's counts, then apply it with `im impacts --all`.
4. **Wording, API, web and export.** "Indirect impact" becomes "inferred impact" in the glossary, API, comments and export. An inferred row can be High. The export gains the activated types and one row per path.
5. **Tests and docs.** The walkthrough and `check_endpoints.py` move to the 57 displayed entities and the new rules. `im check past-run` replays the link types stage.
6. **Scheduled runs.** Set the weekly schedule and publish the first real store once a rerun exists.
7. **A link pass for the 85 hidden entities**, if wanted.

## Known problems

- Credit Suisse is still linked 100% to UBS as a separate entity.
- Azure and Microsoft 365 are linked to Microsoft as `holds_stake_in`.
- PayNow was rated High on OCBC's own app outage, which gives 37 inferred rows from one development.
- Some adverse flags on findings are wrong, and the adverse condition relies on them.
- A parent's own news travels down a minority stake.
- Priority version 13 rates MAS's own enforcement actions and notices Medium or Low. The fix is deferred ([prompts/priority-versions.md](prompts/priority-versions.md)).
- The Home page's priority definitions describe version 8, and the priority wording in `GLOSSARY.md` and `PRODUCT.md` describes version 1. Neither matches version 13 yet.
- MoneySmart has no logo.
- The Overview's Related card still says "See all links".
- Three commits on the local branch `feat/scheduled-runs` store embeddings and cut the merge step's memory. They are reviewed and tested locally but have not run on Northflank, and they are not merged.
