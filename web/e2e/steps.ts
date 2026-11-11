// The walkthrough's steps, in order (wayfind/topics-watchlists-views/issues/12-tests-and-docs.md):
// the first visit, both list pages, the watchlist from rows and page heads, an entity page and its
// tabs, the popup, Recently viewed, removal, function views, two tabs, bad and blocked storage,
// search, the narrow window and Help, then the scripted pipeline checks and the API sweep. The test
// runs them as test.step titles and the reporter lists them in index.html, including any that
// never ran.
export const STEPS = [
  "First visit: / opens MAS's page in the Personal view, with MAS in Watchlist and Recently viewed",
  "Entities list: rows, bands and facet counts as the API ranks them; search, group and priority narrow the rows; Last 4 weeks refetches; a reload keeps the period and filters",
  "Topics list: 30 rows in the API's order with their function; the function filter lists the five functions and leaves only that function's topics",
  "Add to watchlist from list rows: OCBC and Banking & Lending join MAS in the sidebar, entities then topics, and a reload keeps them",
  "Entity page from its row: Add to watchlist in the head, the brief, the top 5 and the Related card, no removed controls; Recently viewed reads DBS, MAS",
  "Page tabs and moved URLs: the Developments feed, its filters and New and Updated; Related; Data; old page, group and removed-page URLs land where they now live",
  "Development popup from a list headline and a page feed: focus, key points, Who it impacts as the API rates it; Esc returns; old development links open it",
  "Recently viewed: the last 5 pages opened, newest first, no repeats, and a page that fails to load is not recorded",
  "Remove from the Personal watchlist: each entry's x, MAS included; the emptied watchlist stays empty and / opens the Entities list",
  "Function views: the switcher's five presets, fixed and locked; / opens the preset's first entity; Add to watchlist changes only Personal; a reload keeps the view",
  "Two tabs: an add, a removal and a view change in one tab show in the other without a reload",
  "Stored state edge cases: old, group and bad values are ignored and the retired key goes; with storage blocked, Add to watchlist works and one notice says so",
  "⌘K search opens an entity and a development",
  "At 1024 px nothing scrolls sideways and names and feed titles show in full",
  "? and the sidebar's Help link open Help with the labels; Data sources opens from its link",
  "Series fetch from cached responses: a backfill and the daily fetch after it reach no source, the fetch adds nothing and the values match the store",
  "Detector on fixture values: the loan-to-deposit rule fires into one High development",
  "Past run replayed from recorded model calls: no call, nothing known after its date, and equal to the stored run",
  "API sweep: check_endpoints.py passes every step, and the API serves only GET operations",
] as const;

/** Where each run writes its artifacts; playwright.config.ts sets it once per run. */
export function runDir(): string {
  const dir = process.env.E2E_RUN_DIR;
  if (!dir) throw new Error("E2E_RUN_DIR is not set; run the suite with `pnpm e2e`");
  return dir;
}

/** Screenshot path for a step, relative to the run directory. */
export const screenshotName = (index: number) => `steps/${String(index + 1).padStart(2, "0")}.png`;

/** Where the walkthrough leaves each step's `im.state.v1`, for the reporter to read. */
export const STORAGE_FILE = "storage.json";

/** Each step's parsed `localStorage["im.state.v1"]` after it ran, by step title; null when unset. */
export type StepStorage = Partial<Record<(typeof STEPS)[number], unknown>>;
