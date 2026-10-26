# X API plan: closing the gaps and adding volume, history and authors

This plan extends item X of `phase1-plan.md` (sections 6 and 8).
It covers how IntelMonitor reads X: what the current use misses, and which X API capabilities to add.
Conventions are those of `phase1-plan.md` section 1: checks from `api/` and `web/`, end-to-end proof through `verify-intelmonitor`, static mocks before any UI change, one sentence per line.
Steps marked **GATE** stop for the user.

## 1. Where we are

Each ingest makes one recent search per target in `TARGETS` (24 with an X query) and one trends call for Singapore (`ingest/sources/social.py`, `ingest/sources/trends.py`).
Posts are stored as `social` documents with engagement counts in `social_posts`.
Spend is capped at `x_spend_cap_usd` (3 USD, now 4) per ingest, and every response is cached in `data/raw/x/`.

What the cached responses for the 8 and 16 October ingests show:
- Singapore targets return almost nothing in a quiet week: DBS 3 and 4 posts, OCBC 1 and 4, UOB 2 and 1, PayNow 0 and 2, NETS 0 and 0, SGX 1 and 2, HKEX 1 and 1. Their caps of 20 or 30 never bind; the queries are not the problem.
- Global targets fill their cap every time: AWS, Azure, Microsoft 365, Google Cloud, Visa, Mastercard, CrowdStrike, Equinix, Circle, Coinbase, OKX. The 20 posts are X's relevancy pick out of many, and some have nothing to do with an outage (a post ranking IT certifications matched the Google Cloud query).
- About 300 posts are read per ingest, roughly 1.50 USD.

## 2. Gaps

| # | Gap | Effect |
|---|---|---|
| G1 | No post volume | An outage is a jump in how many people post, which 20 sampled posts cannot show. For the Singapore targets, the count is the signal |
| G2 | Nothing older than 7 days | A replayed ingest skips X; there is no X record of past incidents for baselines or eval gold |
| G3 | No author data | `author_id` only: no handle in the interface, no way to tell @dbsbank or a government account from anyone else |
| G4 | Official accounts are search terms | Status and corporate posts compete with complaints for the same relevancy slots, and an official post still reads Unverified |
| G5 | Global targets are noisy samples | Their capped posts are a small, partly off-topic slice of worldwide chatter, at most of the spend |
| G6 | Links in posts are discarded | A post sharing a news article is reaction, not early warning, and we cannot tell the two apart |
| G7 | Lead time not built | `phase1-plan.md` section 8 promises "posts appeared 40 minutes before the first news report"; nothing computes it |
| G8 | X trends for Singapore only | Google trends covers SG, MY, ID, TH and HK; X covers SG |

## 3. Capabilities to add, and to leave out

Add:
- **Counts** (`/2/tweets/counts/recent` and `/2/tweets/counts/all`): post counts per minute, hour or day for any search query, 0.005 or 0.010 USD per request, no posts read. One full-archive request covers 31 days.
- **Full-archive search** (`/2/tweets/search/all`): any window since 2006, up to 500 posts a page, 1,024-character queries, same price per post.
- **Author expansion** (`expansions=author_id`, `user.fields`): handle, `verified_type` (blue, business, government), `affiliation`, follower counts.
- **`from:` search** for official accounts.
- **Post fields** `entities` (expanded URLs) and `referenced_tweets`.
- **Trends** for more WOEIDs, 0.010 USD each.
- **News search** (`/2/news/search`), as a trial only: Grok-written stories with entities, tickers and the IDs of the posts behind them, up to 720 hours back. Price not on the pricing page.

Leave out for this POC:
- Filtered stream and Activity API webhooks: ingest runs in batches with no scheduler (`phase1-plan.md` section 2); revisit with live alerts.
- Community Notes: write-only (AI note writer pilot); notes on a post cannot be read.
- Lists: the handful of official handles fits in code.
- Engagement refresh by post lookup: little value without scheduled runs.

## 4. Work items

Status on 26 October 2026: X1, X2, X3 (storage only), X4, X5, X6 and X9 are built in the ingest.
X0 was not run: authors are assumed to cost 0.010 USD each, so they are expanded for the Singapore-facing targets only, and the official handles are looked up once.
X counts start on 1 September 2026, not 31 days back, so October has a baseline.
The 40 new top-50 entities added 13 X targets (Grab, GXS, Trust Bank, MariBank, Singlife, Income, Standard Chartered, Maybank, Citibank, HSBC, Wise, Stripe, Crypto.com); `--social-only --t0` backfills cover the weeks of October.
The cap is now 4 USD per ingest, so per week: only DBS, OCBC, UOB, Grab, PayNow, Singtel and the cyber topic still search on X among the Singapore-facing targets (15 to 20 posts), official accounts read at most 2 pages, and the other Singapore-facing targets are watched through post counts alone (`max_posts` 0).
Still open: the Sources handle and badge (X3 step 3, mock first), X7 and X8.

### Wave 1

**X0. Confirm billing.** GATE.
Depends on: nothing.
Steps:
1. Make one call each, by hand: recent search with `expansions=author_id&user.fields=username,verified_type`, counts recent, counts all, `/2/news/search?query=DBS`.
2. Read the charges in the developer console.
3. Record in this file: whether expanded authors are billed as user reads (0.010 USD each), the counts price, and the news search price and whether the account has access.
Done when: the three prices are written here. X3 and X8 wait on it.

**X1. Post volume per target as series, with a spike detector.** Closes G1.
Depends on: nothing.
Owns: new `api/intelmonitor/ingest/series/x_counts.py`, its registration in `api/intelmonitor/ingest/series/collect.py`, `x.posts.*` entries in `api/seed/series.yaml`, one rule in `api/seed/detectors.yaml`.
Steps:
1. A series adapter with source `{kind: x, target: <subject id>}`. It takes the query from that subject's `SocialTarget` in `TARGETS`, so search and counts never drift apart, and sends `/2/tweets/counts/all` with `granularity=day` over the 31 days ending at the fetch date. One request per target gives the day and its own baseline.
2. One series per X target, `x.posts.<subject>`: daily, unit posts, `bad_direction: up`, primary subject the target, source URL the X search for the query.
3. A detector `x-posts-spike`: kind `move`, window `previous`, `sigmas: 3`, direction up, `when: {at_least: 20}` so a quiet target going from 1 post to 4 never fires. Materiality notable.
4. Charge counts requests to `XSpend` like trends calls, and log them.
Done when:
- `im series fetch` stores 31 days for every X target at about 0.24 USD, and a second run the same day costs nothing.
- Each Singapore bank's Data tab shows its post volume chart (checked with `verify-intelmonitor`).
- Replaying the fetch over the 21 August 2026 DBS disruption (`phase1-plan.md` section 9) fires `x-posts-spike` for DBS on that day.

**X2. Full-archive search for past T0.** Closes G2.
Depends on: nothing.
Owns: `x_search` in `api/intelmonitor/ingest/sources/social.py`, the `--social-only` and `--t0` check in `api/intelmonitor/cli.py`.
Steps:
1. When T0 is more than 7 days back, send the same query to `/2/tweets/search/all` with `start_time` and `end_time` set to the ingest window, instead of skipping. The spend cap and cache keys stay as they are.
2. Let `im ingest --social-only --t0 <date>` run, so one subject's X history can be backfilled without a full ingest.
3. Write `api/seed/known-incidents.yaml` (dates, subjects, one source each) for the incidents we already know, starting with the 21 August 2026 DBS disruption, and backfill each.
Done when: `im ingest --social-only --t0 2026-08-21` stores DBS posts from that day, and the DBS development for it shows them under Social.

### Wave 2

**X3. Authors.** Closes G3.
Depends on: X0.
Owns: `x_search`, `SocialMeta` and `store_social_meta` in `social.py`; new `social_posts` columns in `api/intelmonitor/store/db.py` (through `_add_columns`); the X post row in `web/components/developments/Sources.tsx`.
Steps:
1. Add `expansions=author_id` and `user.fields=username,name,verified_type,affiliation,public_metrics`.
2. Store `author_username`, `author_name`, `author_verified_type` and `author_followers`.
3. Show `@handle` and a government or business badge on each X post in Sources. Mock first.
If X0 shows each expanded author costs 0.010 USD, expand authors only for the Singapore targets, where posts are few; the global targets would add up to 2 USD per ingest.
Done when: Sources shows handles for new X posts, and the ingest log's spend estimate matches the console.

**X4. Official accounts.** Closes G4.
Depends on: X3.
Owns: `SocialTarget` and a new `official_search` in `social.py`; the unverified rule in `api/intelmonitor/pipeline/developments.py` if D-X1 is adopted.
Steps:
1. Add `official: list[str]` handles to each `SocialTarget` (for example @dbsbank, @OCBCBank, @MAS_sg, @AWSSupport, @MSFT365Status, @Visa), and remove them from the keyword queries.
2. One search per ingest: `from:a OR from:b ...` with no trouble words, split across requests to stay under 512 characters, `max_results=100`. Official accounts post rarely, so this costs well under 0.50 USD.
3. Mark these posts `official` in `social_posts`.
Done when: an ingest stores the week's posts from the official handles, each tagged to its subject.

**X5. Global targets: fewer sampled posts.** Closes G5.
Depends on: X1, X4.
Owns: `max_posts` in `TARGETS`.
Steps:
1. Lower `max_posts` to 10 for the global targets. Their outages now show through X1's volume spike and X4's status posts, so the sample only needs to give the development some example posts.
2. Compare one replayed ingest before and after: no development about a global target loses its only social source.
Done when: X reads per ingest fall by about a third and the comparison holds.

**X6. Links in posts.** Closes G6, and feeds X7.
Depends on: nothing.
Owns: `x_search` and `social_posts` in `social.py` and `db.py`.
Steps:
1. Add `entities,referenced_tweets` to `tweet.fields`.
2. Store each post's expanded URLs as JSON, and a `cites_document_id` when one canonicalises to a stored document.
Done when: posts sharing a stored news article point to it.

**X9. X trends for the Google geos.** Closes G8.
Depends on: nothing.
Owns: `x_trends` in `trends.py`.
Steps: one trends call each for HK, MY, ID and TH WOEIDs, 0.04 USD per ingest, stored with their geo.
Done when: the trends table has X rows for all five geos for the next T0.

### Wave 3

**X7. Lead time.** Closes G7.
Depends on: X6.
Owns: a lead-time field on the development API model, its line in the development popup.
Steps:
1. For each development with both social and news sources: the earliest X post that does not cite a news document (X6), against the earliest news or official source. Positive lead time only.
2. Mocks of where the line sits in the popup. GATE for the pick.
3. Implement the picked mock.
Done when: the 21 August DBS development shows how far X posts came before the first news report, or that they did not.

**X8. News search trial.** GATE on X0.
Depends on: X0.
Owns: a scratch script only.
Steps:
1. For the 10 most active subjects, call `/2/news/search` with `max_age_hours=720` and `news.fields=name,summary,contexts,cluster_posts_results,updated_at`.
2. Compare the stories with our developments for the same subjects: which ones we miss, which ones are noise, how early they appear.
3. Report and stop. If it is worth adding, it becomes a source in a later plan, labelled as AI-written like the social label.
Done when: the comparison is reported.

## 5. Spend per ingest

| | Now | After |
|---|---|---|
| Recent search posts | about 300, 1.50 USD | about 200, 1.00 USD (X5) |
| Official accounts | 0 | under 0.50 USD |
| Counts | 0 | 0.24 USD (`im series fetch`) |
| Trends | 0.01 USD | 0.05 USD |
| Authors | 0 | 0 to 0.50 USD, depending on X0 |
| Total | about 1.50 USD | about 2 to 2.30 USD, inside the 3 USD cap |

Backfills under X2 are one-off: about 0.10 to 0.50 USD per incident and subject.

## 6. Decisions

- **D-X1. Official posts verify a development.** A post from a target's own official account counts as an official source, so the development is no longer Unverified. Adopted 26 October 2026: these posts are stored in the `official` collection.
- **D-X2. A volume spike is a development of its own,** like any detector firing, held at notable. It merges with news about the same subject and day. Adopted 26 October 2026.
- **D-X3. Daily counts only.** Hourly counts would show onset to the hour, but the series store is daily and runs are not scheduled. Revisit with scheduled runs.
