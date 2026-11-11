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

## Decided, not yet removed

From the topics, watchlists and views plan (local map in `wayfind/topics-watchlists-views/`).

- User-customisable dashboards: the dashboard pages, widgets, the widget catalogue, the AI dashboard generator, the new-dashboard picker and the starter dashboards.
- Roles, profiles (TCRD, FMD and the user's own), onboarding and the tour; the Personal view and the function views replace them.
- Following subjects; watchlists replace it.
- "Since your last review", last-visit tracking and read marks; "New" now means the latest weekly run.
- The cross-subject developments feed (`/developments`); developments are shown per entity and per topic.
- Notes.
- Custom questions.
- Analyst feedback: removing a tag from a development, marking a development not relevant, and feedback on answers. These can return once there is sign-in.
- Visit logging and usage counts per role.
- "Run now" in the web app; runs are to be scheduled weekly on the server.
- Group pages; groups remain only as headings that organise entities.
- Standing questions and their answers (focus areas), with the answers stage and the answer drawer.
- The Ask page and the "Ask about" button.
- Dismissing and flagging developments, the last per-viewer state on the server.
- Tavily search, used only in the gap search for thinly covered subjects; the gap search keeps its two Exa searches.
- theedgemalaysia.com as a news source; its search results were mostly old archive pages.

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
| All | | | 4153 | 2026-10-09 | 2026-11-04 |
