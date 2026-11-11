# Features removed, 4 Nov 2026

What left the product on 4 Nov 2026, and why, so any of it can be brought back later.
Items under "Decided, not yet removed" leave when the plan in progress is built; move each to "Removed" as it goes.

## Removed

### Social media sources (commit b003453)

The deployment build has no X API access, so social media is no longer a source.

- X, Reddit and forum (HardwareZone) collectors, including posts from subjects' own X accounts.
- X trends.
- X post-count series.
- `api/seed/known-incidents.yaml`.
- The `--social-only` and `--incidents` ingest flags.
- The social collection in the source gate, the priority caps, the API models and the web sources panel, which is now one list.
- Stores made earlier drop their social documents, X posts, X trends and X series, with every row that refers to them, when opened.

### SerpApi Google Trends (commit b003453)

The deployment build has no SerpApi access.

- SerpApi trending searches.
- SerpApi interest over time.
- Trends now come from the keyless Google Trends RSS feed only.

### The 10 POC topics (uncommitted)

Replaced by MAS's 30 topics (`MAS Topics 30 - Sheet1.csv`), which are for all MAS staff.

- Banking regulation, IT outages, Cyber threats, Third-party and cloud risk, Technology risk regulation, Singapore equities market, Asset management, Fixed income and FX, Insurance and risk financing, Infrastructure and transition finance.
- No replacement for the angles the 30 do not name: technology risk regulation (TRM, DORA, CII codes) and third-party concentration risk.
- Role-specific topic picks: the TCRD and FMD presets no longer preselect any topic.

### Jev (uncommitted, spec step 3)

Jev (TypeSafe System One through OpenRouter) is no longer used; Claude takes over its pipeline stages.

- `pipeline/jev.py`, `Inference.jev`, the `typesafe-sdk` dependency and the OpenRouter settings (`OPENROUTER_API_KEY`, `TYPESAFE_BASE_URL`, `TYPESAFE_DEFAULT_MODEL`).
- Screen, same_event, theme and custom_match now run on Claude Haiku 5.5, one structured call each, with thinking off.
- The route stage, which sent Jev's uncertain screening band to Claude, and its thresholds `ROUTE_LOW` and `ROUTE_HIGH`.
- `SAME_EVENT_THRESHOLD`, `PRIORITY_SURE`, `JEV_CONCURRENCY` and the `priority_check` stage.

### Priority rubric and caps (uncommitted, spec step 3)

Slide 7 of the team's deck of 10 Nov 2026 replaces the priority rubric.

- `PRIORITY_RUBRIC` and `PRIORITY_GUIDE`: priority is one Claude Sonnet 5.5 call per development and entity, with a reason.
- Each institution's standing Singapore link in the priority prompt (`pipeline/singapore.py`, `Development.singapore`).
- The caps "promotional news is Low" (`PROMOTIONAL_MAX_LEVEL`) and "market moves are never High" (`MARKET_MAX_LEVEL`).
- Promotional findings and findings that only report a share price or index move (`price_move`) are kept in `findings` but no longer join a development.

### Claude's indirect impact judgement (uncommitted, spec step 3a)

Slide 8 of the team's deck of 10 Nov 2026 replaces it with a fixed table of the direct level by the link's strength; the impact step makes no model call.

- The `impact` Claude call, its prompt, the source passages it was shown, its verdicts and stats.
- Each indirect impact's why sentence, direction (could hurt, could help, mixed) and confidence; the page and the popup show the level and the link's reason instead.
- Two-hop routes, such as a bank reached through a payment system that depends on a failed vendor (`FIRST_HOPS`, `PLACE_FIRST_HOPS`, `paths_to`, `candidates`).
- The route rules: exposures needing a 5% share, not to the lender's home country and only for economic, market, policy or security news; no route through `regulated_by` or `listed_on`, and none into a regulator or central bank (`usable`, `EXPOSURE_MIN_WEIGHT`, `EXPOSURE_CATEGORIES`, `NO_IMPACT`, `NO_IMPACT_TARGETS`).
  The link ratings and the adverse gate take their place.
- Impacts from developments that are not adverse for the entity they are about.
- `development_impacts` loses `rank`, `direction`, `route`, `why`, `confidence`, `relationship_ids`, `weight`, `evidence`, `finding_ids` and `passages`; stores made earlier recreate the table empty when opened.

### Tavily and The Edge Malaysia (uncommitted, spec step 4)

- Tavily search, used only in the gap search for thinly covered subjects; the gap search keeps its two Exa searches.
  The 40 stored Tavily documents stay.
- theedgemalaysia.com as a news source; its search results were mostly old archive pages.
  Its page-date reader went with it.
- markets.ft.com company press releases no longer count as reputable ft.com; they are an ordinary outlet.

### Overlapping weekly windows (uncommitted, spec step 5)

- The 10-day weekly ingest window that overlapped the previous week by 3 days; a week is now exactly Monday to Sunday (UTC), with T0 the Sunday, and `im weekly` ingests and computes each week in turn.
- Documents published before the corpus start (`corpus_start`, 1 Oct 2026 after the rebuild) stay stored but no run reads them.

### The web app's side of the plan's removals (uncommitted, spec step 6)

The web app no longer reads or writes any of the features listed under "Decided, not yet removed" except group pages, which went in step 10 (below); their API endpoints, pipeline hooks and stored rows go in step 7.

- Dashboard pages, widgets, the AI dashboard generator (and its `@ag-ui` packages), the new-dashboard picker and the sidebar's Dashboards list; `/dashboards/<id>` lands on the home page.
- Onboarding (`/setup`, which lands on the home page), the profile menu and role switch, the tour and visit logging.
- The run chip, Run now, the alerts bell and the Following dialog in the top bar.
- Notes, custom questions, analyst feedback (Mark wrong, tag removal, Restore after Not relevant), and the answer drawer (`?answer=`) for standing questions.
- The parked What changed section and every read mark: opening a development no longer marks it read, and New and Updated always show, from the latest run.
- The `/developments` feed page (it lands on the home page), Show all, Show dismissed and Restore to feed.
- "N High since your last visit" in the sidebar, and "since your last visit" in Help.
- Home opens MAS's page until the watchlists land.
- Data sources has no entry until the sidebar gives it one; Help keeps its `?` shortcut.

### The Ask page and the "Ask about" button (uncommitted, spec step 6)

- `/ask` (it lands on the home page), its mocked answers, and the "Ask about <name>" button in an entity or topic page's head.

### The API, pipeline and store side of the plan's removals (uncommitted, spec step 7)

The API, the pipeline and the store no longer carry any of the features the web app dropped in step 6, nor these, which were decided in the same plan:

- User-customisable dashboards: the dashboard endpoints, widgets, the widget catalogue, `/widgets/data`, the dashboard overview, the starter dashboards (`api/seed/dashboards.yaml`) and the Generate agent (`/agent/generate`, `api/intelmonitor/agent/`, `AGENT_ENABLED`, the `ag-ui-protocol` dependency).
- Roles, profiles (TCRD, FMD and the user's own), onboarding suggestions and the tour; the Personal view and the function views replace them.
- Following subjects; watchlists replace it.
- "Since your last review", last-visit tracking and read marks; "New" means the latest weekly run, from its stored run changes.
- The cross-subject developments feed: `GET /developments` needs a `subject_id` and no longer takes `dashboard_id`, `since`, `changed=review` or `dismissed`.
- Notes.
- Custom questions, with the on-demand answer path (`pipeline/custom.py`).
- Analyst feedback: removing a tag from a development (and the pipeline leaving removed tags off later runs), marking a development not relevant, and feedback on answers. These can return once there is sign-in.
- Visit logging and usage counts per role; the counts are kept below.
- "Run now" and `/runs/status`; `GET /runs` and `/runs/latest` stay, and the 15 Run now rows in the run log keep their `manual` trigger.
- Standing questions and their answers (focus areas): `api/seed/questions.yaml`, the answers stage, the series facts given to answers, `/subjects/{id}/facts`, `/subjects/{id}/answers/{question}/evidence`, the standing questions' states in the Data tab payload and the alert threshold a question could set on a series.
- The phase A eval of standing answers (`eval` steps v0, v1, v2, gold, judge, score and rescore), including the UOB exposure question that ran through the custom question path; its results stay in `eval-runs/phase-a-2026-10-05/`.
- Dismissing and flagging developments, the last per-viewer state on the server.
- The Data tab's chart marks High and Medium developments by priority alone, with no Watch answer and no dismissed filter.
- Entity and topic page payloads no longer carry What changed, the topic stage track, group insights, notes or custom question slots, and `/subjects` no longer counts new, High, recent or needs-attention developments.
- Stores made earlier drop the tables `dashboards`, `profiles`, `visits`, `follows`, `analyst_actions`, `feedback`, `custom_question_answers`, `custom_questions`, `development_triage`, `alert_reads`, `question_answers` and `run_facts`, the run changes of kind `question_state`, and the meta keys `active_profile`, `profile`, `profiles_seeded`, `dashboards_seeded`, `follows_seeded` and `tour_seen_at`, when opened.

### Group pages (uncommitted, spec step 10)

Groups remain only to organise entities.

- The group member list (`GroupMembers`) that a group's URL showed.
- A group's name, its graph node and a search hit for it open the Entities list filtered to it (`/entities?group=<id>`), and so do old group URLs under `/entities`, `/topics` and `/monitors`.

### The sidebar's subject tree (uncommitted, spec step 11)

Recently viewed, the watchlist and the view switcher replace it; the Entities and Topics list pages list every subject.

- The sidebar's Entities section, with the sidebar groups as collapsible headings, and its Topics section.
- The collapsed sections kept in `localStorage["im.sidebar.closed"]`, which the app now removes once on load.

## Decided, not yet removed

From the topics, watchlists and views plan (local map in `wayfind/topics-watchlists-views/`).
Nothing is left.

## Visits per role, local store

Recorded on 11 Nov 2026 from `data/intelmonitor-before-feature-removal-2026-11-11.db`, before the `visits` table is dropped.
Days with a visit use the query in the `api/intelmonitor/server/profiles.py` docstring: `SELECT role, count(DISTINCT date) FROM visits GROUP BY role`.
These are loads of the local app, not of the deployed site.
The deployed store's counts are recorded when it is backed up, before it is first touched.

| Role | Profile | Days with a visit | Visits | First | Last |
|---|---|---|---|---|---|
| `development_staff` | FMD | 8 | 1085 | 2026-10-12 | 2026-11-04 |
| `regulatory_supervisor` | Regulatory supervisor | 5 | 2128 | 2026-10-09 | 2026-10-21 |
| `tech_cyber_supervisor` | TCRD | 7 | 940 | 2026-10-12 | 2026-11-04 |
| All | | 13 | 4153 | 2026-10-09 | 2026-11-04 |
