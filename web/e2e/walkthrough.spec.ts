// The walkthrough against the running dev servers: an entity page and its tabs, a development's
// popup and the old links that open it, search, Help and the narrow window, then two scripted
// pipeline checks. It reads what the pipeline produced through the API to decide what to click and
// what to expect. The web app only reads the API: the walkthrough logs every API request the page
// makes to requests.json and fails on any that is not a GET or that reaches a removed endpoint.
import { expect, test, type Locator, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

import { api, API_URL } from "../lib/api/client";
import { shortDate } from "../lib/format";

import { runDir, screenshotName, STEPS } from "./steps";

/** One pass of `im check series`: requests that reached a source, were cached, or had no cache. */
interface FetchReplay {
  added: number;
  revised: number;
  failures: Record<string, string>;
  network: number;
  cached: number;
  missed: number;
}

/** What `im check series` prints (api/intelmonitor/checks.py). */
interface SeriesCheck {
  backfill: FetchReplay;
  fetch: FetchReplay;
  catalogue: number;
  series: number;
  observations: number;
  per_100: { series_id: string; unit: string; period: string; value: number | null }[];
  compared: number;
  differences: string[];
}

/** What `im check detector` prints (api/intelmonitor/checks.py). */
interface DetectorCheck {
  as_of: string;
  detections: string[];
  developments: { title: string; materiality_label: string; materiality_rule: string | null; needs_attention: boolean; subjects: string[]; sources: number }[];
}

const ROOT = path.resolve(import.meta.dirname, "../..");

/** Runs `uv run im check <name>` in api/ and returns what it prints. */
const imCheck = (name: string) => execFileSync("uv", ["run", "im", "check", name], { cwd: path.join(ROOT, "api"), encoding: "utf8" });

/** Shows a check's JSON as the step's page, so its screenshot records the result. */
const showJson = (page: Page, value: unknown) =>
  page.setContent(`<pre style="font: 13px/1.5 ui-monospace, monospace; white-space: pre-wrap">${JSON.stringify(value, null, 2)}</pre>`);

async function unwrap<T>(request: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await request;
  if (error !== undefined || data === undefined) {
    throw new Error(`${response.url} answered ${response.status}: ${JSON.stringify(error)}`);
  }
  return data;
}

const PRIORITIES = ["High", "Medium", "Low"];

/** The feed rows' priority badges, top to bottom. */
const badges = (feed: Locator) => feed.locator(".frow .fstat .badge:not(.b-new)").allTextContents();

type Listed = { priority: string }[];

/** The feed's column header, then the shown rows' priority badges, High first, as the API lists them. */
async function expectOrdered(feed: Locator, shown: Listed) {
  await expect(feed.locator(".frow-head")).toHaveText("PriorityFindingDate");
  await expect(feed.locator(".frow .fstat .badge:not(.b-new)")).toHaveText(shown.map((d) => d.priority));
}

/** A URL with a development's popup open over a page whose path matches `page`. */
const popupUrl = (page: string, id: string) => new RegExp(`${page}\\?(.*&)?development=${id}(&|$)`);

/** One API request the page made, as requests.json lists it. */
interface Logged {
  method: string;
  path: string;
  status: number;
}

// Endpoints of removed features (spec step 6); the page must not call them.
const REMOVED_PATHS = [
  /^\/dashboards/,
  /^\/widgets/,
  /^\/agent/,
  /^\/profiles?(\/|$)/,
  /^\/onboarding/,
  /^\/visits/,
  /^\/follows/,
  /^\/notes/,
  /^\/custom-questions/,
  /^\/feedback/,
  /^\/reviews/,
  /^\/what-changed/,
  /^\/alerts/,
  /^\/runs\/(now|status)/,
  /^\/subjects\/[^/]+\/(answers|facts)/,
  /^\/developments\/[^/]+\/triage/,
];
// Query parameters of removed features on GET /developments.
const REMOVED_PARAMS = ["dashboard_id", "dismissed", "since", "changed"];

test("IntelMonitor walkthrough", async ({ page }) => {
  test.setTimeout(15 * 60_000);
  const dir = runDir();
  mkdirSync(path.join(dir, "steps"), { recursive: true });
  const pageErrors: string[] = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));
  const requests: Logged[] = [];
  page.on("response", (r) => {
    const url = new URL(r.url());
    if (url.origin === new URL(API_URL).origin) requests.push({ method: r.request().method(), path: `${url.pathname}${url.search}`, status: r.status() });
  });

  let index = 0;
  const step = async (title: (typeof STEPS)[number], body: () => Promise<void>) => {
    const i = index++;
    expect(STEPS[i], "steps run in the order of STEPS").toBe(title);
    await test.step(title, async () => {
      try {
        await body();
      } finally {
        await page.screenshot({ path: path.join(dir, screenshotName(i)), animations: "disabled" }).catch(() => undefined);
      }
    });
  };

  const latest = await unwrap(api.GET("/runs/latest"));
  expect(latest.latest_news, "the latest run covers some news").toBeTruthy();
  const subjects = await unwrap(api.GET("/subjects"));
  const month = await unwrap(api.GET("/developments", { params: { query: { subject_id: ["dbs"], days: 30 } } }));
  expect(month.developments.length, "DBS has developments this month").toBeGreaterThan(0);

  // Set by the steps that reach them, read by the later ones.
  let feedUrl = "";

  try {
    await step(STEPS[0], async () => {
      await page.goto("/entities/dbs");
      await expect(page.getByRole("heading", { level: 1, name: "DBS" })).toBeVisible();
      await expect(page.locator(".topbar .updated")).toHaveText(`Week to ${shortDate(latest.as_of)} · updated weekly`);
      for (const region of ["This week", "Top DBS developments", "Related to DBS"]) {
        await expect(page.getByRole("region", { name: region }), `the ${region} block`).toBeVisible();
      }
      await expect(page.getByRole("link", { name: "Open Data" })).toBeVisible();

      // None of the removed controls anywhere: Ask, dashboards, the profile menu, the run chip, alerts, triage.
      for (const name of ["Add widget", "Edit", "Run now", "Mark all reviewed", "Mark reviewed", "Follow", "Generate", "New dashboard", "Add a focus area"]) {
        await expect(page.getByRole("button", { name, exact: true }), `no ${name} button`).toHaveCount(0);
      }
      await expect(page.getByRole("button", { name: /^(Alerts|Show runs|Monitoring)/ })).toHaveCount(0);
      await expect(page.getByRole("link", { name: /^Ask/ }), "no Ask page or Ask about").toHaveCount(0);
      const nav = page.getByRole("navigation", { name: "Main" });
      await expect(nav.getByText("Dashboards", { exact: true }), "no dashboards in the sidebar").toHaveCount(0);
      await expect(page.locator("[data-dashboard-id], [data-widget-id], .profile, dialog.tour")).toHaveCount(0);
      await expect(nav.locator(".count.attn"), "no High since your last visit").toHaveCount(0);
      await expect(page.getByRole("link", { name: /^(Connections|Trend)$/ })).toHaveCount(0);

      // The sidebar keeps Entities, with the groups as headings, and Topics.
      const banks = subjects.find((g) => g.id === "banking");
      if (!banks) throw new Error("the Banking group is missing");
      await expect(nav.getByRole("button", { name: `Banking ${banks.members.length}`, exact: true })).toHaveAttribute("aria-expanded", "true");
      await expect(nav.locator("[aria-current=page]")).toHaveText("DBS");

      // Could affect and Shared with are on the Related tab, which the Related card's See all links opens.
      for (const region of ["Could affect DBS", "Shared with"]) {
        await expect(page.getByRole("region", { name: region }), `no ${region} on the Overview`).toHaveCount(0);
      }
      const tabs = page.getByRole("navigation", { name: "Monitor views" });
      await page.getByRole("region", { name: "Related to DBS" }).getByRole("link", { name: "See all links" }).click();
      await expect(page).toHaveURL(/\/entities\/dbs\/related$/);
      await expect(tabs.getByRole("link", { name: "Related" })).toHaveAttribute("aria-current", "page");
      await expect(page.locator("main .mon-sec h2")).toHaveText(["Could affect DBS", "Shared with"]);
      await expect(page.getByRole("region", { name: "Could affect DBS" }).locator(".ca-row")).not.toHaveCount(0);
      await tabs.getByRole("link", { name: "Overview" }).click();
      await expect(page).toHaveURL(/\/entities\/dbs$/);

      // The Overview shows this month's top 5 with their priority and New or Updated.
      const top = page.getByRole("region", { name: "Top DBS developments" });
      const shown = month.developments.slice(0, 5);
      await expect(top.locator(".frow")).toHaveCount(shown.length);
      await expect(top.locator(".frow .ftitle").first()).toHaveText(shown[0].headline);
      await expect(top.locator(".frow .b-new")).toHaveCount(shown.filter((d) => d.change === "new").length);
      await expectOrdered(top, shown);

      await tabs.getByRole("link", { name: "Data" }).click();
      await expect(page).toHaveURL(/\/entities\/dbs\/data$/);
      await expect(page.locator('section[aria-labelledby="why"] .mon-sec-head .meta')).toHaveText(/^Week to \d+ \w{3}$/);
      // The chrome is in plain words; note bodies are the model's text and are not checked here.
      await expect(page.locator("main .tr-legend").first()).toHaveText("HighMedium");
      await expect(page.locator('section[aria-labelledby="runs"]'), "no focus areas by run").toHaveCount(0);
      const indicatorBadges = await page.locator('section[aria-labelledby="indicators"] .badge').allTextContents();
      expect(indicatorBadges.filter((b) => b !== "No data" && b !== "Stale"), "no Watch or Stable on series").toEqual([]);
      await expect(page.locator("main")).not.toContainText("Run of");
      // Cited developments carry their priority for DBS, as its feed shows them.
      const cited = (await unwrap(api.GET("/subjects/{subject_id}/trend", { params: { path: { subject_id: "dbs" } } }))).runs.at(-1)?.note?.developments ?? [];
      const feed = await unwrap(api.GET("/developments", { params: { query: { subject_id: ["dbs"], days: null } } }));
      const inFeed = new Map(feed.developments.map((d) => [d.id, d.priority]));
      await expect(page.locator(".tr-cited .badge")).toHaveText(cited.map((c) => inFeed.get(c.development.id) ?? c.development.priority));

      // A topic links to nothing: no Related tab, and its URL lands on the Overview.
      await page.goto("/topics/financial-regulation/related");
      await expect(page).toHaveURL(/\/topics\/financial-regulation$/);
      await expect(tabs.getByRole("link").first()).toHaveText("Overview");
      await expect(tabs.getByRole("link", { name: "Related" })).toHaveCount(0);
    });

    await step(STEPS[1], async () => {
      await page.goto("/entities/dbs");
      const top = page.getByRole("region", { name: "Top DBS developments" });
      await top.getByRole("link", { name: /^See all/ }).click();
      await expect(page).toHaveURL(/\/entities\/dbs\/developments$/);
      const tabs = page.getByRole("navigation", { name: "Monitor views" });
      await expect(tabs.getByRole("link", { name: "Developments" })).toHaveAttribute("aria-current", "page");
      const feed = page.getByRole("region", { name: "Developments about DBS" });
      await expect(feed.locator(".frow")).toHaveCount(month.developments.length);
      await expectOrdered(feed, month.developments);
      await expect(feed.getByRole("button", { name: /^Adverse event/ })).toBeVisible();
      const ranks = (await badges(feed)).map((b) => PRIORITIES.indexOf(b));
      expect(ranks, "highest priority first").toEqual([...ranks].sort((a, b) => a - b));

      // New and Updated show on every row the latest run added or updated, read or not.
      await expect(feed.locator(".frow .b-new")).toHaveCount(month.developments.filter((d) => d.change === "new").length);
      await expect(feed.locator(".frow .upd")).toHaveCount(month.developments.filter((d) => d.change === "updated").length);
      await expect(feed.locator(".feed-foot")).toHaveText(`New: added in the run of ${shortDate(latest.as_of)}. Updated: new reports in that run.`);
      await expect(feed.getByRole("button", { name: /^(Show|Hide) dismissed/ })).toHaveCount(0);
      await expect(feed.getByRole("link", { name: /^Show all/ })).toHaveCount(0);

      // This week, then its highest priority only: the URL holds both, a reload keeps them, every row
      // has that priority.
      const week = await unwrap(api.GET("/developments", { params: { query: { subject_id: ["dbs"], days: 7 } } }));
      const [level] = week.developments.map((d) => d.priority).sort((a, b) => PRIORITIES.indexOf(a) - PRIORITIES.indexOf(b));
      if (!level) throw new Error("DBS has no developments this week");
      await feed.getByRole("group", { name: "Period" }).getByRole("button", { name: /^This week/ }).click();
      await expect(page).toHaveURL(/days=7/);
      await feed.getByRole("button", { name: /^Priority/ }).click();
      await page.getByRole("dialog", { name: "Priority" }).getByRole("checkbox", { name: new RegExp(`^${level}`) }).check();
      await expect(page).toHaveURL(new RegExp(`priority=${level}`));
      await page.keyboard.press("Escape");
      await page.reload();
      await expect(feed.getByRole("group", { name: "Period" }).getByRole("button", { name: /^This week/ })).toHaveAttribute("aria-pressed", "true");
      await expect(feed.getByRole("button", { name: `Priority: ${level}` })).toBeVisible();
      const only = await unwrap(api.GET("/developments", { params: { query: { subject_id: ["dbs"], days: 7, priority: [level] } } }));
      await expect(feed.locator(".frow")).toHaveCount(only.developments.length);
      expect(new Set(await badges(feed)), `only ${level} rows`).toEqual(new Set([level]));
    });

    await step(STEPS[2], async () => {
      await page.goto("/entities/dbs/developments");
      feedUrl = page.url();
      const feed = page.getByRole("region", { name: "Developments about DBS" });
      const d = month.developments[0];
      const link = feed.getByRole("link", { name: d.headline, exact: true });
      await link.click();
      await expect(page).toHaveURL(popupUrl("/entities/dbs/developments", d.id));
      const popup = page.getByRole("dialog", { name: d.headline });
      await expect(popup.getByRole("heading", { level: 2, name: d.headline })).toBeFocused();
      await expect(popup.getByRole("button", { name: "Close development" })).toBeVisible();
      await expect(popup.getByRole("button", { name: /^Back/ })).toHaveCount(0);
      await expect(popup.getByRole("heading", { name: "Who it impacts" })).toBeVisible();
      await expect(popup.getByRole("heading", { name: "Connections" })).toBeVisible();
      await expect(popup.getByRole("button", { name: /^(Dismiss|Not relevant|Restore to feed)$/ })).toHaveCount(0);
      for (const word of ["materiality", "Tagged to", "tagged by", "Needs attention", "Dismissed", "Marked not relevant"]) {
        await expect(popup, `no "${word}"`).not.toContainText(word, { ignoreCase: true });
      }
      // 80% of the screen, within its bounds.
      const box = await popup.boundingBox();
      const view = page.viewportSize();
      expect(box && view && Math.round(box.width) === Math.round(Math.min(1400, Math.max(900, view.width * 0.8))), "80% wide").toBe(true);

      await page.keyboard.press("Escape");
      await expect(page).toHaveURL(feedUrl);
      await expect(popup).toHaveCount(0);
      await expect(link, "focus returns to the row").toBeFocused();

      // A development's old page address opens the popup over the subject's page, or over MAS's page
      // without a subject; close drops it.
      await page.goto(`/developments/${d.id}?subject=dbs`);
      await expect(page).toHaveURL(popupUrl("/entities/dbs", d.id));
      await expect(popup).toBeVisible();
      await popup.getByRole("button", { name: "Close development" }).click();
      await expect(page).toHaveURL(/\/entities\/dbs$/);
      await page.goto(`/developments/${d.id}`);
      await expect(page).toHaveURL(popupUrl("/entities/mas", d.id));
      await expect(popup).toBeVisible();
      await popup.getByRole("button", { name: "Close development" }).click();
      await expect(page).toHaveURL(/\/entities\/mas$/);

      // Old page URLs land on the same tab at the new ones, renamed tabs included, and a page under
      // the other list moves to its own.
      for (const [old, now] of [
        ["/monitors/dbs/related", "/entities/dbs/related"],
        ["/monitors/dbs/trend#indicators", "/entities/dbs/data#indicators"],
        ["/monitors/financial-regulation", "/topics/financial-regulation"],
        ["/topics/dbs/developments?days=7", "/entities/dbs/developments?days=7"],
      ]) {
        await page.goto(old);
        await expect(page, `${old} lands on ${now}`).toHaveURL(new URL(now, page.url()).href);
      }
      await expect(page.getByRole("heading", { level: 1, name: "DBS" })).toBeVisible();

      // Home and the removed pages' old links land on MAS's page.
      for (const old of ["/", "/setup", "/ask?about=dbs", "/dashboards/anything", "/developments?dashboard=anything"]) {
        await page.goto(old);
        await expect(page, `${old} lands on MAS`).toHaveURL(/\/entities\/mas$/);
        await expect(page.getByRole("heading", { level: 1, name: "MAS" })).toBeVisible();
      }
      await page.goto(feedUrl);
    });

    await step(STEPS[3], async () => {
      const feed = page.getByRole("region", { name: "Developments about DBS" });
      const title = (await feed.locator(".frow .ftitle").first().textContent()) ?? "";
      await feed.getByRole("link", { name: title, exact: true }).click();
      const popup = page.getByRole("dialog", { name: title });
      await expect(popup).toBeVisible();
      const id = new URL(page.url()).searchParams.get("development") ?? "";
      const d = await unwrap(api.GET("/developments/{development_id}", { params: { path: { development_id: id } } }));
      // The key points sit under the headline, as the API gives them; the full title only when there are none.
      if (d.points.length > 0) await expect(popup.locator("ul.dp-points li")).toHaveText(d.points);
      else if (d.headline !== d.title) await expect(popup.locator(".dp-points")).toHaveText(d.title);
      await popup.getByRole("button", { name: "Close development" }).click();
      await expect(page).toHaveURL(feedUrl);
      await expect(feed.getByRole("link", { name: title, exact: true }), "back in the feed").toBeVisible();
    });

    await step(STEPS[4], async () => {
      const search = page.getByRole("combobox", { name: "Search" });
      // A pointer resting where the results open would take the highlight on hover.
      await page.mouse.move(700, 880);
      await page.keyboard.press("ControlOrMeta+k");
      await expect(search).toBeFocused();
      await search.fill("ocbc");
      await expect(page.getByRole("option", { name: "OCBC Entity" })).toHaveAttribute("aria-selected", "true");
      await page.keyboard.press("Enter");
      await expect(page).toHaveURL(/\/entities\/ocbc$/);

      // A word from a DBS headline, so the latest run surely has a development that matches it.
      const word = month.developments[0].headline.split(/[^A-Za-z]+/).find((w) => w.length >= 6) ?? "bank";
      const results = await unwrap(api.GET("/search", { params: { query: { q: word, limit: 5 } } }));
      const hit = results.developments[0];
      expect(hit, `a development matches ${word}`).toBeTruthy();
      await page.keyboard.press("ControlOrMeta+k");
      await search.fill(word);
      await page.locator("#search-results .search-hit.development").filter({ hasText: hit.headline }).click();
      await expect(page).toHaveURL(popupUrl("/entities/ocbc", hit.id));
      await expect(page.getByRole("dialog", { name: hit.headline }).getByRole("heading", { level: 2, name: hit.headline })).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(page).toHaveURL(/\/entities\/ocbc$/);
    });

    await step(STEPS[5], async () => {
      await page.evaluate(() => (document.activeElement instanceof HTMLElement ? document.activeElement.blur() : undefined));
      await page.keyboard.press("?");
      const help = page.getByRole("dialog").filter({ has: page.getByRole("heading", { name: "Help" }) });
      await expect(help).toBeVisible();
      const labels = help.getByRole("region", { name: "Labels on a development" });
      for (const word of ["High", "Medium", "Low", "Adverse event", "New", "Updated"]) await expect(labels).toContainText(word);
      await expect(labels, "New is the latest run, not the last visit").not.toContainText("last visit");
      await page.keyboard.press("Escape");
      await expect(help).toHaveCount(0);
    });

    await step(STEPS[6], async () => {
      await page.setViewportSize({ width: 1024, height: 800 });
      await page.goto("/entities/dbs/developments");
      await expect(page.getByRole("heading", { level: 1, name: "DBS" })).toBeVisible();
      await expect(page.locator(".frow").first()).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth), "no sideways scroll").toBe(1024);
      // Titles a user must read to decide what to open show in full: no clamp, no ellipsis, no hidden overflow.
      const cut = await page.evaluate(() =>
        [...document.querySelectorAll<HTMLElement>(".ftitle, .navitem > span:first-of-type, h1")]
          .filter((el) => el.clientHeight > 0 && (el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1))
          .map((el) => el.textContent),
      );
      expect(cut).toEqual([]);
    });

    await step(STEPS[7], async () => {
      // `im check series` backfills every series into a scratch store from data/raw/ alone, then
      // fetches again as the daily fetch does. A request with no cached response fails instead of
      // reaching the source, so a clean pass proves the fetch path runs offline end to end.
      await page.setViewportSize({ width: 1440, height: 900 });
      const check: SeriesCheck = JSON.parse(imCheck("series"));
      for (const [name, pass] of [["backfill", check.backfill], ["fetch", check.fetch]] as const) {
        expect(pass.network, `the ${name} reaches no source`).toBe(0);
        expect(pass.missed, `every ${name} request is cached`).toBe(0);
        expect(pass.cached, `the ${name} made requests`).toBeGreaterThan(0);
        expect(pass.failures, `no series fails in the ${name}`).toEqual({});
      }
      expect(check.series, "every series but the hand-collected one has history").toBeGreaterThanOrEqual(check.catalogue - 1);
      expect([check.fetch.added, check.fetch.revised], "the same responses again add nothing").toEqual([0, 0]);
      // Quotes per 100 units are stored per unit, the values the app serves.
      for (const v of check.per_100) {
        const served = await unwrap(
          api.GET("/series/{series_id}", { params: { path: { series_id: v.series_id }, query: { from: v.period, to: v.period } } }),
        );
        expect([v.unit, v.value], `${v.series_id} on ${v.period}`).toEqual([served.series.unit, served.observations[0]?.value]);
      }
      expect(check.compared, "replayed observations were compared with the store").toBeGreaterThan(1000);
      expect(check.differences).toEqual([]);
      writeFileSync(path.join(dir, "series-fetch.json"), JSON.stringify(check, null, 2));
      await showJson(page, check);
    });

    await step(STEPS[8], async () => {
      // `im check detector` feeds fixture loan-to-deposit values to a scratch store; crossing 90%
      // fires the rule into one development that reaches High through its rule.
      const check: DetectorCheck = JSON.parse(imCheck("detector"));
      expect(check.detections).toHaveLength(1);
      expect(check.detections[0]).toMatch(/rose above 90\.0%/);
      expect(check.developments).toHaveLength(1);
      const [d] = check.developments;
      expect([d.title, d.materiality_rule, d.needs_attention, d.sources]).toEqual([check.detections[0], "ltd-above-90", true, 1]);
      writeFileSync(path.join(dir, "detector.json"), JSON.stringify(check, null, 2));
      await showJson(page, check);
    });
  } finally {
    writeFileSync(path.join(dir, "requests.json"), JSON.stringify(requests, null, 2));
  }
  expect(pageErrors, "no uncaught errors in the page").toEqual([]);
  // The page only reads: every API request is a GET (CORS preflights aside), none reaches a removed
  // endpoint or carries a removed feature's parameter.
  expect(requests.filter((r) => r.method !== "GET" && r.method !== "OPTIONS"), "every API request is a GET").toEqual([]);
  const removed = requests.filter((r) => {
    const url = new URL(r.path, API_URL);
    return REMOVED_PATHS.some((p) => p.test(url.pathname)) || REMOVED_PARAMS.some((k) => url.searchParams.has(k));
  });
  expect(removed, "no request to a removed endpoint").toEqual([]);
});
