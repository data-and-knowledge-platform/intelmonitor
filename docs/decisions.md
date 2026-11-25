# Decision log

This file records the decisions that shaped IntelMonitor, why each was made, and whether it still stands.
[spec.md](spec.md) describes what the system does now; this file explains why it is that way, including ideas that were tried and dropped.
Entries are grouped by area and run oldest first within a group; a later entry can supersede an earlier one, and the status line says so.

Terms follow [GLOSSARY.md](../GLOSSARY.md).
"The team" is the wider project team whose decks set the rating and link rules and who review the labels; "the owner" is the person who builds the system and made most of the calls below.
A "run" is one weekly pipeline pass, and "the store" is the SQLite database a run writes.

## Scope and limits

- **One user, no sign-in.**
  Since 26 Sep 2026 the proof of concept has no login, roles, teams or sharing, until end users show the app is useful.
  Anything kept per viewer (the Personal watchlist, Recently viewed) lives only in the browser; the server stores nothing per viewer.
- **Public sources only.**
  Ingest reads public news, official releases and public data APIs.
  Internal MAS documents and the FI-to-vendor list are out, since their classification decides whether they may be sent to a model provider.
- **News alone must carry the product.**
  Social media was dropped as a source on 4 Nov 2026, and nothing depends on it.
- **No human-labelled gold set.**
  Since 5 Oct 2026 a Claude Opus model builds reference labels and judges outputs in place of human labelling, and every result must say so.
- **Paid per run, with no spend cap.**
  Every model call is billed per token on the Anthropic API; a full weekly run measured about $40 of Claude plus about $5 of Exa search ([pipeline-costs.md](pipeline-costs.md)).
- **Links come only from sourced collection.**
  No model invents a link while a run is computing; an event reaches another entity only through a stored link that has a source, a quote and a date.
- **Priority is judged from the text.**
  The model rates a development from its sources alone, with no outside knowledge of an entity's dependencies.
- **The site is read-only.**
  The deployed site holds no API keys and has no endpoint that writes; only the weekly runner calls paid APIs.

## Sources and ingest

- **24 Sep 2026.** Ingest searches news with Exa and Tavily, limited to listed news domains, and reads official feeds.
  The proof of concept runs on one machine on public data, so internal news feeds were replaced by web search over reputable domains.
  Status: Tavily removed on 4 Nov 2026; its only use by then was one of three gap searches for thinly covered subjects, and the other two, on Exa, stay.
- **27 Sep 2026.** Measured data series come into scope: MAS statistics APIs, FRED, CoinGecko and DefiLlama, with detectors that turn a series event into a development.
  Until then every number in the app was a count of documents or a model's tag; series add numbers computed by code, years of history, and signals that can arrive before any article.
  Status: stands. The series, detectors and the Data tab still run, and the runner holds the FRED, CoinGecko and MAS keys; only their use in standing questions went with those questions on 4 Nov 2026.
- **7 to 8 Oct 2026.** Social media joins as a second source after news: X posts, Reddit and HardwareZone threads, with a development reported only by posts capped at Low until news or an official source confirms it.
  The product had to work on news alone; social posts were wanted as early warning (outage complaints before the news) and as reaction.
  Status: superseded on 4 Nov 2026.
- **18 Oct 2026.** A development shows only if two distinct outlets report it, or one reputable outlet does, or any official or data source does.
  Users in the testing sessions distrusted single-source items but accepted one report from a reputable outlet.
  The reputable list is matched on parent domain and lives in one place that every feed and count uses.
- **4 Nov 2026.** Social media is dropped entirely (X, Reddit, HardwareZone, X trends, X post counts), and so is SerpApi; Google Trends comes only from its keyless RSS feed.
  The deployment build has no X API or SerpApi access.
  Stores made earlier drop their social rows when opened; the full list is in [removed-features-2026-11-04.md](removed-features-2026-11-04.md).
- **4 Nov 2026.** theedgemalaysia.com is dropped, and markets.ft.com press releases count as an ordinary outlet, not as the reputable ft.com.
  Exa dated Edge archive pages from past years as current, and they took about a fifth of all search slots for very few kept documents.
  The FT pages are company announcements, not FT reporting.
- **4 Nov 2026.** Fifteen official sources are added and stored as official, so each backs a development alone: FATF, US Treasury, EBA, ESMA, FCA, Bank of England, IADI, NY Fed Liberty Street, SGX announcements, the Singapore Police, MOT, DBS, Standard Chartered, Thunes and ADT.
  They come from MAS's example articles for the 30 topics; stored as news, a FATF statement would need a second outlet to show.
  The 14 trade and aggregator outlets in the same examples are not added, since they are not reputable and mostly repeat wire copy.
- **4 Nov 2026.** A week is exactly Monday to Sunday UTC, dated the Sunday, and the corpus starts on 1 Oct 2026.
  The earlier 10-day window overlapped the previous week by 3 days, and with 5 results a query the overlap spent search slots on documents already stored.
  Documents published before the corpus start stay stored, but no run reads them.
- **25 Nov 2026.** pymnts.com joins the news domains.
  The team's entity list of 16 Nov named PYMNTS, which is a news site, so it was left out as an entity.

## Entities and topics

- **8 Oct 2026.** Groups, such as Singapore banks, only organise entities: no screening question, nothing tagged to them, and no group page.
  The focus is topics and entities.
  Status: stands; a group link opens the Entities list filtered to that group.
- **8 Oct 2026.** The subject list is settled before an ingest and changed rarely.
  Screening's prompt lists every subject, so any change to the list re-screens the whole corpus.
  Status: stands as a constraint.
- **26 Oct and 11 Nov 2026.** The entity set becomes MAS's top 50 with countries, payment systems and cloud providers in the background (26 Oct); then all 89 entities get pages and list rows, with no split into displayed and hidden (11 Nov).
  The owner read the team's deck as putting entities ahead of topics, not 50 entities ahead of the other 39.
  Status: superseded on 16 Nov 2026.
- **4 Nov 2026.** MAS's 30 topics replace the 10 proof-of-concept topics, for all staff, each in exactly one of MAS's five functions.
  MAS supplied the list as its staff-wide taxonomy, so no role preselects topics any more.
  The old topics' technology-risk angles (technology risk regulation, third-party concentration) have no replacement topic.
- **4 Nov 2026.** Topic tagging stays multi-label, one yes or no per topic, and list pages show one topic per development: the one with the highest priority, then the highest screening score.
  MAS's descriptors read as mutually exclusive, but in a sample 6 of 40 relevant documents really spanned two topics, and this needs no pipeline change.
- **4 Nov 2026.** The cybersecurity topic's descriptor gains one sentence of our own: breaches and outages at technology vendors and cloud providers count even when no institution is named.
  The descriptor already names third-party incidents, the old topics covered vendors, and the technology risk supervisors' weekly bulletin needs them.
  Status: stands; MAS is still to be told about the added sentence.
- **4 Nov 2026.** A new entity joins only if it is in MAS's top 50 or linked to one; the United States and the Federal Reserve join, and ECB, Bank of Japan, Binance and Gemini stay out.
  Status: superseded on 16 Nov 2026, when the team's list brought those in.
- **16 Nov 2026.** Fifty entities join from the team's list, and a displayed flag splits them: 57 displayed and 85 hidden of 142, the three regions included.
  A hidden entity is searched, tagged, rated and linked, and passes inferred impact, but has no page, list row, search hit or watchlist place; a route that names it shows the name as plain text.
  Without the split the Entities list would hold AMD, the WTO and the IMF beside the banks MAS supervises; 37 existing entities became hidden, among them every country (Singapore included), the payment systems, Microsoft Azure and Singtel.
- **16 Nov 2026.** Foreign banks are entered as the group with its home country, and Google, Amazon and Amaze are not added (Google Cloud, AWS and Nium stand as they are).
  The priority prompt already reads a foreign institution through its Singapore branch or subsidiary.

## Developments

- **24 Sep 2026.** Documents are screened, findings are extracted with verified quotes, and reports of one event merge into one development whose every claim links to its sources.
  The product's purpose is answers, not feeds, with evidence one click away.
- **7 Oct 2026.** "Adverse" marks the cause of harm to an entity: an event at it, or a harmful trend or rule bearing directly on it; never commentary, forecasts, market reactions or follow-up reporting.
  The earlier rule tagged DBS's own economists' gloomy outlook as adverse for DBS.
  The adverse flag stays separate from priority, because an opportunity can be High without being adverse.
- **10 to 11 Nov 2026.** Promotional findings, and findings that only report a share price or index move or an analyst's price target, are stored but never join a development; speeches and routine statistics stay.
  The team's deck excludes promotions and stock prices at collection; the flag is per finding because documents mix substantive and promotional parts.
  Keeping the findings lets the rule be checked or undone without paying for extraction again.
  This replaced the earlier code caps "promotional news is Low" and "market moves are never High".
- **23 Nov 2026, revised 25 Nov.** The headline call, which since 28 Oct 2026 writes each development's headline and key points, also decides which candidate subjects a development is about; the others become mentions and are not rated, asked for link types or passed to inferred impact.
  A development's subjects had been every subject any of its findings tagged, so a side finding could attach a topic that then took the development's highest level (the MAS policy hold of 14 Oct carried an AI topic from 2 of its 40 findings).
  It runs at low effort: the cases pass at low and at medium, and medium costs about half as much again.

## Priority rating

- **7 Oct 2026.** Priority shows as three labels, High, Medium and Low, and is rated per development and entity, in place of five materiality levels scored once per development.
  With one score per development, a Swiss vote on UBS capital ranked first on the Singapore banks dashboard, and 20% of developments reached the top tier.
- **28 Oct 2026.** Jev, the TypeSafe classification model then used for high-volume decisions, rates the three labels directly from the supervisors' guideline, code tells it each entity's standing link to Singapore, and market moves are never High.
  Analysts had seen stock moves flagged High and a Microsoft Azure incident scored Low because the model could not know which banks depend on Azure.
  "Med-High" in the guideline means Medium or High by severity and is never a fourth label; that part stands.
  Status: the rest is superseded on 11 Nov 2026.
- **11 Nov 2026.** Priority follows the team's rubric of 10 Nov: High only when the text shows an effect on the entity's Singapore unit or on Singapore's financial system, rated by one Claude Sonnet call per development and entity with a stored one-sentence reason.
  Disagreement with the labels was the first finding of the round 1 user tests, and the rubric is written for one entity at a time.
  Code no longer supplies a Singapore link; it fills in what "Singapore" means for each kind of entity, and standing links act only through inferred impact.
  Status: this is the live prompt, version 1, until version 13 (below) is switched on.
- **11 Nov 2026.** A topic takes the highest level among the development's entities; a development that names no entity is rated for the topic against Singapore's financial system.
  The deck left topics unrated, which would have emptied topics such as AI, where most developments name no entity.
- **16 Nov 2026.** A newly picked priority version rates from the week ending 15 Nov onwards, and the six earlier weeks keep version 1 when they are run again.
  Each week then holds one rubric; the owner chose that over saving cost.
  Status: decided for version 8; not restated when version 13 was picked.
- **16 Nov 2026.** Version 8 is picked: high effort, a closing "think it through" line, and two rules put back (a finding that only reports a price move is never High; promotional news is Low).
  On a blind sample an Opus judge agreed with it on about 72% of pairs against 57% for version 1, for about $4 a week more.
  Status: superseded on 25 Nov 2026; the Home page's priority definitions were written from version 8.
- **25 Nov 2026.** Version 13, "local-anchored", is the version to carry forward: each level is judged against an anchor set by the subject, following the team's subject guide of 23 Nov.
  A topic is judged on MAS or Singapore's financial system, a country or region on its own financial system, a financial institution and a Singapore-based technology provider, payment system or market infrastructure on itself in Singapore, and any other entity on itself.
  Singtel's own large deals may be High for Singtel, and there is no rule capping deals; version 12, which judged such providers by their services to financial institutions, kept their own incidents from High and is retired.
  Status: picked, not yet live.
- **25 Nov 2026.** A per-role rule line for authorities, so that MAS's own enforcement actions and notices rate properly, is deferred as a future enhancement.
  Under version 13 they rate Medium or Low, a known gap.

## Links and inferred impact

- **7 Oct 2026.** Impact on an entity through stored relationships, in news that may never name it, is a core feature, built on real public data: loan exposures, vendor and payment-system dependencies, and group structure, each with a source.
  It is the second half of the product's case: supervisors usually know their own entity's news but miss events that reach it indirectly.
- **8 Oct 2026.** A Claude call judges each candidate route with a sentence, a direction and a confidence, under code rules (an exposure counts only from a 5% loan share and never to the lender's home country).
  Status: superseded on 11 Nov 2026 by the fixed table.
- **11 Nov 2026.** Links take the team's 10 types, and the collected files stay the only source of a link's existence and type; a model only rates them.
  A sourced link is what a supervisor can check.
  Status: the types are superseded on 16 Nov 2026; the rule that every link carries a source stands.
- **11 Nov 2026.** Each link is rated once in each direction (Strong, Moderate, Weak) on the team's prompt, the ratings are kept in a committed file, and runs call no model for them.
  The deck calls the rating one-time setup, and a file can be read, diffed and corrected; a rating marked as set by hand is never overwritten.
  The team had tested the prompt on a small dataset, so its ratings were used without waiting for a review pass.
  Status: stands, with a fourth value since 16 Nov 2026: Non-propagating, for a link that exists but should not carry an event in that direction, which passes nothing; links to a group are rated too.
- **11 Nov 2026.** Inferred impact follows the team's fixed table of direct level by link strength, with no model call, one hop, never High, and only from developments adverse for the named entity.
  The table alone gave up to 13,931 rows on the store of the time against 269 under the old judgement, and the rating measures how a material adverse event travels, so applying it to a product launch is outside what it measures.
  Status: the table and the High cap are superseded on 16 Nov 2026; the adverse gate was dropped then and restored on 23 Nov 2026.
- **11 Nov 2026, kept 16 Nov.** The table's Low row (a Low direct level through a Strong link gives Low) is switched off (`INDIRECT_FROM_LOW`).
  Low is routine or promotional by definition, and Low sources were most of the candidate rows.
  Status: open; it cannot be counted until entities at Low are asked for their link types.
- **16 Nov 2026.** Link strength judged per development, one Sonnet call per development and named entity, is parked on its own branch and not built.
  The team's activated types answer the same problem (a fine and an outage at Singtel should not reach a bank alike), and the deck rules out a model call per reached entity; the branch keeps the trial record ($17.56) and its cases.
- **16 Nov 2026.** Links take the deck's 22 types in three groups; provider types read from provider to customer, and payment-system membership becomes "the system processes transactions for the participant".
  Left as plain participation, a FAST outage would reach no bank, since the deck activates the processing type for a processor's outage.
  Of nine partner links, two became joint products and seven left the build, since the deck says not to infer a relationship from two names appearing together.
- **16 Nov 2026.** A link joins two entities, or an entity and its group; link ends that were not entities leave the build, units fold into their parents by a hand-written list, and three regions become hidden entities.
  No development is tagged to a non-entity end, so such links never carried impact; a region keeps its member list, so a bank's regional loan figure still reaches the countries in it.
  The full graph view, "Linked" and "Shared with" went with them, as their only readers.
- **16 Nov 2026.** The relationship types a development activates come from a separate Sonnet call per development with an entity at Medium or High, picking types per entity; the priority call is untouched.
  Priority and type selection then keep separate prompt histories, so editing the type guidance never re-rates a level, and it costs about $1.40 a week (estimate) instead of paying for every priority pair again.
  On 23 Nov 2026 it was set to high effort: at medium the prompt failed one of the deck's ten cases (a maintenance failure).
- **16 Nov 2026.** Inferred impact follows the deck's revised table: High by a Strong link gives High; a link is followed only if its type is activated and its direction is not Non-propagating; every path is stored with one marked best.
  A Strong rating is enough for inferred High, since the Strong definition already names the deck's tests (criticality, concentration, control, low substitutability).
  It is called "inferred impact", the team's word, because it says nobody reported the effect.
- **16 to 18 Nov 2026.** Links of the new types are collected per entity by a Sonnet call with web search and page fetch that runs until the model ends its turn, then filtered: both ends must be entities, the quote must be in the fetched page and name one end, the page must name both, and a second call must find that the passage supports the parties, type, direction, materiality and that the arrangement still stands.
  The collected files had no rows for eight of the types; on a five-entity trial, reading kept rows blind showed 14 of 30 supported before the filters and check, 25 of 30 after.
- **23 Nov 2026.** The collection pass ran for the 57 displayed entities only, kept 821 links for $92.65, and the hidden entities have not had a pass, apart from Singtel, whose trial result is kept.
  Status: open for the other 84 hidden entities.
- **23 Nov 2026.** A country, region, regulator, central bank, agency or international body passes no inferred impact through `operates_in`, `regulated_by`, `listed_on`, `member_of` or `participates_in`; an entity's home country passes nothing to it; and only developments adverse for the named entity pass.
  Under the deck's rules about 9% of 6,020 previewed rows read as useful: a regulator's rule reached every firm it regulates and a country's news reached every firm in it, whatever the rule or event touched.
  Under these rules a preview week held 447 rows, none High, and 16 of 25 sampled made sense; a firm's own news still reaches its regulator and country, a country's news still reaches a foreign lender, and a parent's news still reaches its subsidiaries.
  Status: stands, but all three rules depart from the team's deck of 16 Nov, and the team has not yet seen them.
- **25 Nov 2026.** The link types call is made only for a development that is adverse for at least one of its entities at Medium or High.
  With the adverse-only rule on, the others can pass nothing, and they were 837 of the 1,148 developments asked in the week ending 8 Nov (measured).
  An asked development still lists every entity it listed before, so recorded calls replay.
  The cost is that inferred rows with the adverse rule off can no longer be counted from a run; the run says so.

## Models, prompts and versions

- **24 Sep 2026.** Every model call goes through one recorder that caches it by a hash of its whole input (model, prompts, schema, effort) and stores tokens and cost.
  A rerun pays only for calls whose input changed, and a stored run can be replayed to check it.
- **4 Nov 2026, built 11 Nov.** Jev, the TypeSafe classification model that made the high-volume decisions since 24 Sep 2026, is no longer used: screening, same-event merges and theme labels run on Claude Haiku, one structured call each with thinking off, and priority runs on Claude Sonnet.
  Haiku's price kept those stages under $2 of a weekly run.
- **11 Nov 2026.** Claude calls go through the Anthropic API on a key, billed per token with cost recorded per call, instead of the owner's Claude login.
  A scheduled server run needs a key, and the cost of each stage needs to be visible.
- **16 Nov 2026.** Every stored label names the prompt version that made it; a version is the stage, template, schema, model and effort, and a store frozen on 16 Nov (six runs, 89 entities) is where priority versions are tried.
  The owner wants to see how the labels move with each prompt, and two versions compared on the same frozen data differ only by prompt.
  The team reads one workbook per version, a sheet per week, and a compare workbook listing only the pairs that moved.
- **16 Nov 2026.** Sonnet prompts follow Anthropic's Sonnet 5.5 prompting guide: adaptive thinking set explicitly, an optional closing "think it through" line, effort chosen by a sweep, and the refusal category recorded.
  With structured output at low and medium effort the model often answers without thinking.
  The team's wording is not edited by this tuning; any change to it is a new version shown to the team.

## Evaluation

- **5 Oct 2026.** No human labelling: Claude Opus builds the reference set ("silver gold") and judges, and Claude Sonnet answers.
  The owner had no time for labelling, and a rough answer then was worth more than a labelled one later.
  Every result must flag that the same model family built the reference and judged.
- **8 Oct 2026.** Hand-written cases fix the adverse definition and the priority caps, and a share check keeps High rare (under 10% of developments).
  High must stay rare enough to mean "read this".
- **10 Nov 2026.** The team audits the labels in Excel: every development and entity with its level and reason, the inferred rows, the links and the dropped findings.
  The team asked for it to see where the tagging can improve.
- **11 to 25 Nov 2026.** Each rule-bearing stage gets cases written before its code or prompt: the inferred impact table (run with no model call), the activated types (the deck's ten examples plus eight of ours), and the subject choice.
- **16 Nov 2026.** Priority versions are compared against an Opus judge on a blind sample of pairs where versions disagree, plus controls.
  It is a model's reference made in one pass, not a human label; the judge was stricter on High than every version.

## Deployment and runs

- **5 Oct 2026.** A shareable demo runs on Railway with the model features off, no API keys, and the store shipped inside the deploy.
  Testers needed a demo without model spend or a Claude login on the server.
  Status: superseded by the runner and bucket layout below.
- **4 Nov 2026.** The eventual home is a government version of Northflank, and the Railway setup mirrors it: a stateless site, a scheduled runner and an S3-compatible bucket, joined only by the S3 API and environment variables.
  A government deployment needs restricted egress, possibly several site copies, and no write path into the site; moving then needs no code change.
  Open questions for the platform team are in [northflank.md](northflank.md).
- **4 Nov 2026.** Runs happen weekly on the server, Monday 08:00 Singapore time with a retry at 14:00, up to four missed weeks per firing; each run publishes an immutable store file and then repoints `latest.json`, and the last 12 files are kept.
  A failed run publishes nothing, so the site keeps the last good store, and rolling back is repointing `latest.json`.
  There is no spend cap; a failure exits non-zero and the platform emails the account owner.
- **4 Nov 2026.** The stored runs are deleted and rebuilt week by week from 1 Oct 2026 on the 30 topics, with spend approved up to $250 of Claude.
  Old runs carried the old topic ids and broke the API, and nothing on the new site reads runs older than the latest two.
  Measured on 11 Nov: $223.49 for six runs, about four times the estimate, because the 30 topics bring about 1,560 documents a week, not 425.
- **16 Nov 2026.** The link types and inferred impact work comes before the first scheduled run and the deploy.
  Deploying first would publish the old rules and pay for weekly runs under them.
- **16 Nov 2026.** The six weeks are ingested and run again in full for the larger entity list, not recomputed in place.
  The screening prompt lists every entity, so every document is screened again; the owner first considered running only the new entities and chose consistency instead.
  Status: not yet run; one week runs first on a copy and its measured cost decides the rest.
- **23 Nov 2026.** The runner fails before spending if a test Exa search or Haiku call fails, publishes a week that missed a source but then exits with an error naming it, and refuses to repoint `latest.json` if another run moved it.
  These close gaps that apply on both platforms before the first scheduled firing.
  A test deployment has run on Northflank's free plan since 23 Nov 2026, serving the store of 16 Nov.

## Interface

- **24 Sep 2026.** The interface is calm and exact: neutral surfaces, colour only for status (priority, Adverse event, New), nothing that moves on its own.
  The users are analysts who check a watchlist weekly with other tools open ([PRODUCT.md](../PRODUCT.md)).
- **7 Oct 2026.** Every screen is designed for a first-time, infrequent user who has had no briefing, in plain words, with one label per question: priority, the adverse flag, and New or Updated.
  The prototype showed importance in several vocabularies at once (Needs attention, Watch, Stable, materiality names), and internal terms such as "run" and "hops" appeared on screen.
- **4 Nov 2026.** Dashboards, widgets, the AI dashboard generator, roles, profiles, onboarding, following, notes, custom questions, analyst feedback, alerts, Run now, the cross-subject feed and group pages are removed.
  With one user and no sign-in, per-viewer state on the server has no owner, and a fixed layout replaces customisation; analyst feedback can return with sign-in.
  Each item and how to bring it back is in [removed-features-2026-11-04.md](removed-features-2026-11-04.md).
- **4 Nov 2026.** Standing questions and their answers, the Ask page and the "What changed" section are removed, and pages move to `/entities/<id>` and `/topics/<id>`.
  Standing questions had not run since early October and the brief replaced them; Ask answered only one DBS and UOB question.
- **4 Nov 2026.** A Personal view with a watchlist kept in the browser, starting with MAS, and five function views with fixed preset watchlists replace roles and dashboards; "Add to watchlist" always edits the Personal one.
  There is no sign-in, so per-viewer state can live only in the browser.
  For the same reason "New" means added in the latest weekly run, the same for every viewer, and read marks and "since your last visit" go.
- **4 Nov 2026.** Entities and Topics list pages rank subjects by High developments in the period, then Medium; inferred rows never count.
  A subject's place on the list then reflects news about it, and an inferred High cannot pass for a reported one.
- **11 Nov 2026.** How link strength, inferred levels and the priority reason are shown waits for the team's Figma designs; the existing layouts only swap in the new fields.
  Status: partly overtaken on 23 Nov 2026 (below); the full display is still open.
- **23 Nov 2026.** The team's designs of 23 Nov are built over the current pages: a Home page with the watchlist at a glance and the week's developments per subject, cards defining each priority, development cards, a one-scroll popup, and a Related tree in place of the force graph.
- **25 Nov 2026.** The note on why a subject's series moved leaves the deployed version.
  It is one Sonnet call per subject with listed series on every run, about 55 calls and $0.58 a run (measured), and only the Data tab and one line of the brief read it.
  Status: decided, not yet built.
- **25 Nov 2026.** The Related tab leaves the deployed version.
  It lists the developments that could affect the subject through a link; the development popup and the Overview's Related tree show inferred impact too.
  The owner's call; no reason was recorded.
  Status: decided, not yet built.
- **25 Nov 2026.** The Data tab leaves the deployed version, because the entities added on 16 Nov have no data source.
  Only 6 of the 57 displayed entities have any measured series (DBS, OCBC, UOB, MAS and SGX have their own), 9 of the 30 topics do, and the five displayed entities added on 16 Nov have none.
  Status: decided, not yet built.
  Whether the series ingest and the detectors go with it is open: the owner expects a deployed run to need only the Anthropic and Exa keys, and today `im weekly` exits 1 when a series key is missing.

## Open decisions

- **The Low row.** Whether a Low direct level reaches a Strong link as Low; off until entities at Low get their link types and the rows can be counted.
- **Priority version 13 going live.** When it is switched on, and whether the six earlier weeks stay on version 1 as planned for version 8.
- **A rule line for authorities.** Deferred: a per-role line so MAS's own enforcement actions and notices rate properly.
- **What counts as a financial institution.** Open with the team: whether REITs, GIC and Temasek do, which sets their anchor under version 13.
- **The inferred impact rules of 23 Nov.** The team has not seen the three rules that depart from its deck (no class-wide news from countries and regulators, nothing from a home country, adverse developments only).
- **Links for hidden entities.** 84 of the 85 hidden entities (all but Singtel) have had no collection pass.
- **The rerun of the six weeks.** Not run; its one-week trial sets the cost of the rest.
  The owner intends to run the full six weeks once on the deployed runner, not on a laptop, with the one-week trial run locally first.
- **Showing strength and inferred rows.** The full display waits on the team's designs; the Home page's priority definitions still describe version 8.
- **Topic pages.** Whether topic pages show developments that name no entity was left open on 11 Nov 2026, and no later decision is recorded.
- **Questions for the team.** Whether entities such as SIA, used in the deck's examples, join; whether sectors become subjects so a sector-wide event can reach its members; whether schemes such as Project Guardian join so `participates_in` has rows; whether the four-value rating prompt was tested; what counts as a partner.
- **Telling MAS.** The added sentence on vendor breaches in the cybersecurity descriptor.
- **Questions for the platform team.** Whether the runner may reach the Anthropic API or must use a cloud provider's Claude endpoint, which bucket service is allowed, and the rest listed in [northflank.md](northflank.md).
- **Measured series and detectors.** Whether they are removed with the Data tab, so a run needs only the Anthropic and Exa keys.
- **Failure alerts.** A channel for run failures beyond the account owner's email has not been named.
