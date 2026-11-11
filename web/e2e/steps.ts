// The walkthrough's steps, in order: an entity page and its tabs, a development's popup, search,
// Help and the narrow window, then two scripted pipeline checks. The test runs them as test.step
// titles and the reporter lists them in index.html, including any that never ran.
export const STEPS = [
  "Entity page: the brief, the top 5 developments and the Related card, no removed controls; See all links opens the Related tab with Could affect and Shared with; the Data tab in plain words",
  "Developments tab: the feed under priority headings; This week and High narrow it and survive a reload; New and Updated from the latest run, with its date in the footer",
  "Development popup from a feed row: labels, who it impacts, connections, sources; Esc returns to the same feed; old page URLs land on the new ones; old links open the popup or land on MAS's page",
  "Key points under the headline in the development popup, as the API gives them",
  "⌘K search opens an entity and a development",
  "? opens Help with the labels",
  "At 1024 px nothing scrolls sideways and titles wrap in full",
  "Series fetch from cached responses: a backfill and the daily fetch after it reach no source, the fetch adds nothing and the values match the store",
  "Detector on fixture values: the loan-to-deposit rule fires into one High development",
] as const;

/** Where each run writes its artifacts; playwright.config.ts sets it once per run. */
export function runDir(): string {
  const dir = process.env.E2E_RUN_DIR;
  if (!dir) throw new Error("E2E_RUN_DIR is not set; run the suite with `pnpm e2e`");
  return dir;
}

/** Screenshot path for a step, relative to the run directory. */
export const screenshotName = (index: number) => `steps/${String(index + 1).padStart(2, "0")}.png`;
