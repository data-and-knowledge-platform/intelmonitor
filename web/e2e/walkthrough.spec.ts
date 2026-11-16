// The walkthrough against the running dev servers, written from the draft steps in
// wayfind/topics-watchlists-views/issues/12-tests-and-docs.md. One serial test: steps 1 to 10 and
// 13 to 15 share the test's browser context, so the browser state (`localStorage["im.state.v1"]`)
// carries from step to step; steps 11 and 12 open contexts of their own, each recorded on video.
// It reads the API to decide what to click and what to expect; only seed facts (57 displayed
// entities, 30 topics, the five functions and their presets) are fixed. The web app only reads the API: every
// API request any context makes goes to requests.json, and the run fails on any that is not a GET
// or that reaches a removed endpoint, so it needs no store backup. It ends with the scripted
// pipeline checks and check_endpoints.py; run it with the INTELMONITOR_DATA_DIR the API serves.
import { expect, test, type BrowserContext, type Locator, type Page } from "@playwright/test";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";

import { api, API_URL, type Schema } from "../lib/api/client";
import { addDays, shortDate } from "../lib/format";

import { runDir, screenshotName, STEPS, STORAGE_FILE, type StepStorage } from "./steps";

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

/** What `im check past-run` prints (api/intelmonitor/checks.py). */
interface PastRunCheck {
  as_of: string;
  log: string[];
  later_documents: number;
  later_observations: number;
  later_detections: number;
  detections: number;
  developments: number;
  indicators: number;
  late: { sources: number; indicators: number };
  differences: string[];
}

/** What check_endpoints.py writes to its results file. */
interface EndpointCheck {
  steps: { step: string; ok: boolean; detail?: unknown; error?: string }[];
}

type Subject = Schema<"SubjectSummary">;
type ListRow = Schema<"ListRow">;

const ROOT = path.resolve(import.meta.dirname, "../..");
const API_DIR = path.join(ROOT, "api");
const VIEWPORT = { width: 1440, height: 900 };
const STATE_KEY = "im.state.v1";
// Seed facts the walkthrough may fix.
const ENTITY_COUNT = 57; // displayed entities; hidden ones have no list row
const TOPIC_COUNT = 30;
const PRIORITIES = ["High", "Medium", "Low"] as const;
const NOTICE = "This browser blocks storage, so watchlist changes last only until this tab closes.";

/** Runs `uv run im check <name>` in api/ and returns what it prints. */
const imCheck = (name: string) => execFileSync("uv", ["run", "im", "check", name], { cwd: API_DIR, encoding: "utf8", maxBuffer: 256 * 1024 * 1024 });

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

/** The parsed `im.state.v1` in a page's origin, or null when it is unset or unreadable. */
async function storedState(page: Page): Promise<unknown> {
  const raw = await page.evaluate((key) => localStorage.getItem(key), STATE_KEY).catch(() => null);
  if (raw === null) return null;
  const parsed: unknown = JSON.parse(raw);
  return parsed;
}

/** The feed rows' priority badges, top to bottom. */
const badges = (feed: Locator) => feed.locator(".frow .fstat .badge:not(.b-new)").allTextContents();

/** The feed's column header, then the shown rows' priority badges, High first, as the API lists them. */
async function expectOrdered(feed: Locator, shown: { priority: string }[]) {
  await expect(feed.locator(".frow-head")).toHaveText("PriorityFindingDate");
  await expect(feed.locator(".frow .fstat .badge:not(.b-new)")).toHaveText(shown.map((d) => d.priority));
}

/** A URL with a development's popup open over a page whose path is `page`. */
const popupUrl = (page: string, id: string) => new RegExp(`${page}\\?(.*&)?development=${id}(&|$)`);

const escapeRegExp = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** The sidebar and its parts, as an analyst reads them. */
const sidebar = (page: Page) => page.getByRole("navigation", { name: "Main" });
const watchlistLinks = (page: Page) => sidebar(page).getByRole("region", { name: /^Watchlist/ }).getByRole("link");
const recentLinks = (page: Page) => sidebar(page).getByRole("region", { name: "Recently viewed" }).getByRole("link");
const removeButtons = (page: Page) => sidebar(page).getByRole("button", { name: /^Remove .+ from watchlist$/ });

/** Picks a view in the sidebar's switcher. */
async function pickView(page: Page, from: string, to: string) {
  await sidebar(page).getByRole("button", { name: `View: ${from}` }).click();
  await page.getByRole("menu", { name: "Views" }).getByRole("menuitemradio", { name: to, exact: true }).click();
  await expect(sidebar(page).getByRole("button", { name: `View: ${to}` })).toBeVisible();
}

/** Removes an entry from the Personal watchlist with its x, shown on hover. */
async function removeFromSidebar(page: Page, name: string) {
  const row = sidebar(page)
    .getByRole("region", { name: /^Watchlist/ })
    .locator(".navrow")
    .filter({ has: page.getByRole("link", { name, exact: true }) });
  await row.hover();
  await row.getByRole("button", { name: `Remove ${name} from watchlist` }).click();
}

/** A list page's rows as shown: band, name, group or function, counts, headline, topic, New, latest. */
const shownRows = (page: Page) =>
  page.locator(".lp-table tbody tr[data-id]").evaluateAll((trs) =>
    trs.map((tr) => {
      const text = (selector: string) => tr.querySelector(selector)?.textContent ?? "";
      return {
        id: tr.getAttribute("data-id") ?? "",
        band: tr.closest("tbody")?.getAttribute("data-band") ?? "",
        name: text(".lp-name"),
        group: text(".lp-group"),
        counts: [3, 4, 5].map((n) => tr.querySelector(`td:nth-child(${n})`)?.textContent ?? ""),
        headline: text(".lp-headline"),
        topic: text(".lp-topic"),
        new: text(".lp-new"),
        latest: text(".lp-latest"),
      };
    }),
  );

/** The same rows from the API: banded by top priority, the API's ranking kept inside each band. */
const expectedRows = (rows: ListRow[], entities: boolean) =>
  [...PRIORITIES, null]
    .flatMap((p) => rows.filter((r) => (r.top?.priority ?? null) === p))
    .map((r) => ({
      id: r.id,
      band: r.top?.priority ?? "none",
      name: r.name,
      group: entities ? (r.groups[0]?.name ?? "") : (r.function?.name ?? ""),
      counts: [r.counts.high, r.counts.medium, r.counts.low].map(String),
      headline: r.top?.headline ?? "",
      topic: entities ? (r.top?.topic?.name ?? "") : "",
      new: String(r.new),
      latest: r.latest ? shortDate(r.latest) : "",
    }));

/** A facet's options as shown: name and count. */
const facetOptions = (page: Page, title: string) =>
  page
    .getByRole("group", { name: title, exact: true })
    .locator(".lp-opt")
    .evaluateAll((buttons) => buttons.map((b) => [b.querySelector(".lp-opt-name")?.textContent ?? "", b.querySelector(".lp-opt-n")?.textContent ?? ""]));

/** A facet option's button: its name, then its count. */
const facetButton = (page: Page, title: string, name: string) =>
  page.getByRole("group", { name: title, exact: true }).getByRole("button", { name: new RegExp(`^${escapeRegExp(name)}( \\d+)?$`) });

/** One API request a page made, as requests.json lists it; status is null when no response came. */
interface Logged {
  context: string;
  method: string;
  path: string;
  status: number | null;
}

// Endpoints of removed features (spec steps 6 and 7); no page may call them.
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

test("IntelMonitor walkthrough", async ({ page, browser, baseURL }) => {
  test.setTimeout(45 * 60_000);
  const dir = runDir();
  mkdirSync(path.join(dir, "steps"), { recursive: true });
  const pageErrors: string[] = [];
  const requests: Logged[] = [];
  const storage: StepStorage = {};

  /** Logs a context's API requests as they are sent, so aborted ones count too, and uncaught page errors. */
  const watch = (context: BrowserContext, name: string) => {
    context.on("request", (r) => {
      const url = new URL(r.url());
      if (url.origin !== new URL(API_URL).origin) return;
      const logged: Logged = { context: name, method: r.method(), path: `${url.pathname}${url.search}`, status: null };
      requests.push(logged);
      void r.response().then(
        (response) => {
          logged.status = response?.status() ?? null;
        },
        () => undefined,
      );
    });
    context.on("weberror", (e) => pageErrors.push(`${name}: ${e.error().message}`));
  };
  watch(page.context(), "main");

  // Contexts other than the test's own: each records its pages on video, kept as video-<name>.webm.
  const open: { name: string; context: BrowserContext }[] = [];
  const openContext = async (name: string) => {
    const context = await browser.newContext({ baseURL, ignoreHTTPSErrors: true, viewport: VIEWPORT, recordVideo: { dir: path.join(dir, "videos", name), size: VIEWPORT } });
    watch(context, name);
    open.push({ name, context });
    return context;
  };
  const closeContext = async (context: BrowserContext) => {
    const at = open.findIndex((o) => o.context === context);
    if (at < 0) return;
    const [{ name }] = open.splice(at, 1);
    const videos = context.pages().flatMap((p) => p.video() ?? []);
    await context.close();
    for (const [i, video] of videos.entries()) {
      renameSync(await video.path(), path.join(dir, `video-${name}${videos.length > 1 ? `-${i + 1}` : ""}.webm`));
    }
    rmSync(path.join(dir, "videos", name), { recursive: true, force: true });
  };

  // The page a step's screenshot and browser state come from: the test's own unless a step that
  // drives another context points them there. Contexts a step opens close once both are taken.
  let at = { shot: page, state: page };
  let index = 0;
  const step = async (title: (typeof STEPS)[number], body: () => Promise<void>) => {
    const i = index++;
    expect(STEPS[i], "steps run in the order of STEPS").toBe(title);
    at = { shot: page, state: page };
    await test.step(title, async () => {
      try {
        await body();
      } finally {
        await at.shot.screenshot({ path: path.join(dir, screenshotName(i)), animations: "disabled" }).catch(() => undefined);
        storage[title] = await storedState(at.state);
        for (const { context } of [...open]) await closeContext(context);
      }
    });
  };

  const latest = await unwrap(api.GET("/runs/latest"));
  expect(latest.latest_news, "the latest run covers some news").toBeTruthy();
  const subjects = await unwrap(api.GET("/subjects"));
  const functions = await unwrap(api.GET("/functions"));
  const byId = new Map(subjects.map((s) => [s.id, s]));
  const subject = (id: string) => {
    const s = byId.get(id);
    if (!s) throw new Error(`no subject ${id} in /subjects`);
    return s;
  };
  const byName = (a: Subject, b: Subject) => a.name.localeCompare(b.name);
  const pagePath = (s: Subject) => `/${s.type === "topic" ? "topics" : "entities"}/${s.id}`;
  /** A watchlist as the sidebar shows it: entities, then topics, each alphabetical. */
  const watchlistNames = (ids: string[]) => {
    const shown = ids.map(subject).sort(byName);
    return [...shown.filter((s) => s.type === "entity"), ...shown.filter((s) => s.type === "topic")].map((s) => s.name);
  };
  /** Where `/` lands for a watchlist: its first entity, else its first topic, else the Entities list. */
  const homeOf = (ids: string[]) => {
    const shown = ids.map(subject).sort(byName);
    const first = shown.find((s) => s.type === "entity") ?? shown.find((s) => s.type === "topic");
    return first ? pagePath(first) : "/entities";
  };
  const month = await unwrap(api.GET("/developments", { params: { query: { subject_id: ["dbs"], days: 30 } } }));
  expect(month.developments.length, "DBS has developments this month").toBeGreaterThan(0);
  const supervision = functions.find((f) => f.id === "financial-supervision");
  if (!supervision) throw new Error("no Financial Supervision function in /functions");
  const preset = [...supervision.watchlist.entities, ...supervision.watchlist.topics];

  // The Personal watchlist as the steps change it, in the order they add to it.
  let personal = ["mas"];

  try {
    await step(STEPS[0], async () => {
      await page.goto("/");
      await expect(page).toHaveURL(/\/entities\/mas$/);
      await expect(page.getByRole("heading", { level: 1, name: "MAS" })).toBeVisible();
      await expect(page.locator(".topbar .updated")).toHaveText(`Week to ${shortDate(latest.as_of)} · updated weekly`);
      // No onboarding, profile menu or dashboard on a first visit.
      await expect(page.getByRole("button", { name: /^(Create dashboard|Get started|Profile)/ })).toHaveCount(0);
      await expect(page.locator(".profile, dialog.tour, [data-dashboard-id]")).toHaveCount(0);
      await expect(sidebar(page).getByRole("button", { name: "View: Personal" })).toBeVisible();
      await expect(watchlistLinks(page)).toHaveText(["MAS"]);
      await expect(recentLinks(page)).toHaveText(["MAS"]);
      expect(await storedState(page), "only Recently viewed is written").toEqual({ recent: ["mas"] });
    });

    await step(STEPS[1], async () => {
      await sidebar(page).getByRole("link", { name: `Entities ${ENTITY_COUNT}` }).click();
      await expect(page).toHaveURL(/\/entities$/);
      const week = await unwrap(api.GET("/lists/{list_name}", { params: { path: { list_name: "entities" }, query: { period: "week" } } }));
      expect(week.rows).toHaveLength(ENTITY_COUNT);
      await expect(page.getByTestId("list-count")).toHaveText(String(ENTITY_COUNT));
      // The period's span runs from the day after `since` to the run's date.
      const span = page.locator(".lp-since");
      await expect(span).toContainText(shortDate(latest.as_of));
      await expect(span).toHaveText(new RegExp(`^${shortDate(addDays(week.since, 1)).split(" ")[0]} `));
      await expect(page.locator(".lp-table tbody tr[data-id]")).toHaveCount(ENTITY_COUNT);
      expect(await shownRows(page), "rows, bands and cells as the API ranks them").toEqual(expectedRows(week.rows, true));

      // Facet counts are counts over the loaded rows.
      const count = (keep: (r: ListRow) => boolean) => String(week.rows.filter(keep).length);
      expect(await facetOptions(page, "Priority")).toEqual([
        ["Any", String(ENTITY_COUNT)],
        ...PRIORITIES.map((p) => [p, count((r) => r.top?.priority === p)]),
      ]);
      const groups = [...new Map(week.rows.flatMap((r) => r.groups).map((g) => [g.id, g])).values()].sort((a, b) => a.name.localeCompare(b.name));
      expect(await facetOptions(page, "Group")).toEqual([["Any", String(ENTITY_COUNT)], ...groups.map((g) => [g.name, count((r) => r.groups.some((x) => x.id === g.id))])]);

      // Search narrows the rows by name.
      const field = page.getByRole("searchbox", { name: "Search" });
      await field.fill("bank");
      await expect(page).toHaveURL(/[?&]q=bank/);
      expect((await shownRows(page)).map((r) => r.id)).toEqual(expectedRows(week.rows.filter((r) => r.name.toLowerCase().includes("bank")), true).map((r) => r.id));
      await field.fill("");
      await expect(page).toHaveURL(/\/entities$/);

      // DBS's first group leaves every entity in it, whichever of its groups it is; High then
      // leaves only those whose top development is High.
      const dbsRow = week.rows.find((r) => r.id === "dbs");
      const group = dbsRow?.groups[0];
      if (!group) throw new Error("DBS has no group");
      await facetButton(page, "Group", group.name).click();
      await expect(page).toHaveURL(new RegExp(`[?&]group=${group.id}`));
      const inGroup = (rows: ListRow[]) => rows.filter((r) => r.groups.some((g) => g.id === group.id));
      expect((await shownRows(page)).map((r) => r.id)).toEqual(expectedRows(inGroup(week.rows), true).map((r) => r.id));
      await expect(page.locator('.lp-table tr[data-id="dbs"]')).toBeVisible();
      await facetButton(page, "Priority", "High").click();
      await expect(page).toHaveURL(/[?&]priority=High/);
      const high = (rows: ListRow[]) => inGroup(rows).filter((r) => r.top?.priority === "High");
      expect((await shownRows(page)).map((r) => r.id)).toEqual(expectedRows(high(week.rows), true).map((r) => r.id));

      // Last 4 weeks refetches the list; the filters stay, and a reload keeps them all.
      const weeks = await unwrap(api.GET("/lists/{list_name}", { params: { path: { list_name: "entities" }, query: { period: "4w" } } }));
      const refetch = page.waitForResponse((r) => r.url().startsWith(`${API_URL}/lists/entities`) && new URL(r.url()).searchParams.get("period") === "4w");
      await page.getByRole("group", { name: "Period" }).getByRole("button", { name: "Last 4 weeks" }).click();
      await refetch;
      await expect(page).toHaveURL(/[?&]period=4w/);
      const narrowed = expectedRows(high(weeks.rows), true).map((r) => r.id);
      await expect(page.locator(".lp-table")).toHaveAttribute("aria-busy", "false");
      expect((await shownRows(page)).map((r) => r.id)).toEqual(narrowed);
      const url = page.url();
      await page.reload();
      expect(page.url()).toBe(url);
      await expect(page.getByRole("group", { name: "Period" }).getByRole("button", { name: "Last 4 weeks" })).toHaveAttribute("aria-pressed", "true");
      await expect(facetButton(page, "Group", group.name)).toHaveAttribute("aria-pressed", "true");
      await expect(facetButton(page, "Priority", "High")).toHaveAttribute("aria-pressed", "true");
      await expect(page.locator(".lp-table tbody tr[data-id]")).toHaveCount(narrowed.length);
      expect((await shownRows(page)).map((r) => r.id)).toEqual(narrowed);
      expect(await storedState(page), "a list page is not recorded").toEqual({ recent: ["mas"] });
    });

    await step(STEPS[2], async () => {
      await sidebar(page).getByRole("link", { name: `Topics ${TOPIC_COUNT}` }).click();
      await expect(page).toHaveURL(/\/topics$/);
      const week = await unwrap(api.GET("/lists/{list_name}", { params: { path: { list_name: "topics" }, query: { period: "week" } } }));
      expect(week.rows).toHaveLength(TOPIC_COUNT);
      await expect(page.locator(".lp-table tbody tr[data-id]")).toHaveCount(TOPIC_COUNT);
      expect(await shownRows(page)).toEqual(expectedRows(week.rows, false));
      await expect(page.locator(".lp-table thead th").nth(1)).toHaveText("Function");
      await expect(page.locator(".lp-topic"), "no topic under a topic's headline").toHaveCount(0);

      // The function filter lists the five functions and, unset, filters nothing.
      expect((await facetOptions(page, "Function")).map(([name]) => name)).toEqual(["Any", ...functions.map((f) => f.name)]);
      await expect(facetButton(page, "Function", "Any")).toHaveAttribute("aria-pressed", "true");
      const fn = functions[0];
      await facetButton(page, "Function", fn.name).click();
      await expect(page).toHaveURL(new RegExp(`[?&]function=${fn.id}`));
      const owned = week.rows.filter((r) => r.function?.id === fn.id);
      expect(owned.map((r) => r.id).sort()).toEqual([...fn.topics].sort());
      expect((await shownRows(page)).map((r) => r.id)).toEqual(expectedRows(owned, false).map((r) => r.id));
      await facetButton(page, "Function", "Any").click();
      await expect(page.locator(".lp-table tbody tr[data-id]")).toHaveCount(TOPIC_COUNT);
    });

    await step(STEPS[3], async () => {
      /** Bookmarks a list row: the tooltip says what it does before and after. */
      const bookmark = async (id: string) => {
        const name = subject(id).name;
        const row = page.locator(`.lp-table tr[data-id="${id}"]`);
        const add = row.getByRole("button", { name: `Add ${name} to watchlist` });
        await expect(add).toHaveAttribute("title", "Add to watchlist");
        await expect(add).toHaveAttribute("aria-pressed", "false");
        await add.click();
        const added = row.getByRole("button", { name: `Remove ${name} from watchlist` });
        await expect(added).toHaveAttribute("aria-pressed", "true");
        await expect(added).toHaveAttribute("title", "On watchlist");
        personal = [...personal, id];
      };
      await sidebar(page).getByRole("link", { name: `Entities ${ENTITY_COUNT}` }).click();
      await bookmark("ocbc");
      await sidebar(page).getByRole("link", { name: `Topics ${TOPIC_COUNT}` }).click();
      await bookmark("banking-lending");
      await expect(watchlistLinks(page)).toHaveText(watchlistNames(personal));
      await expect(sidebar(page).locator("hr.navrule"), "a hairline between entities and topics").toHaveCount(1);
      await page.reload();
      await expect(watchlistLinks(page)).toHaveText(watchlistNames(personal));
      await expect(page.locator('.lp-table tr[data-id="banking-lending"]').getByRole("button", { name: /^Remove .+ from watchlist$/ })).toBeVisible();
      expect(await storedState(page)).toEqual({ recent: ["mas"], watchlist: ["mas", "ocbc", "banking-lending"] });
    });

    await step(STEPS[4], async () => {
      await sidebar(page).getByRole("link", { name: `Entities ${ENTITY_COUNT}` }).click();
      await page.locator('.lp-table tr[data-id="dbs"] .lp-name').getByRole("link", { name: "DBS", exact: true }).click();
      await expect(page).toHaveURL(/\/entities\/dbs$/);
      await expect(page.getByRole("heading", { level: 1, name: "DBS" })).toBeVisible();

      // The head's labelled button adds DBS, then reads On watchlist and offers removal on hover,
      // but only once the pointer has left after the click. Its width holds through every label.
      const watchButton = page.locator(".mon-head .watch-btn");
      await expect(watchButton).toHaveAccessibleName("Add to watchlist");
      const width = (await watchButton.boundingBox())?.width;
      await watchButton.click();
      personal = [...personal, "dbs"];
      await expect(watchButton).toHaveAccessibleName("On watchlist");
      await expect(watchButton.locator(".watch-off")).toBeHidden();
      expect((await watchButton.boundingBox())?.width, "the button keeps its width").toBe(width);
      await page.mouse.move(700, 880);
      await expect(watchButton.locator(".watch-on")).toHaveText("On watchlist");
      await expect(watchButton.locator(".watch-off")).toBeHidden();
      await watchButton.hover();
      await expect(watchButton.locator(".watch-off")).toHaveText("Remove from watchlist");
      await expect(watchButton.locator(".watch-on")).toBeHidden();
      expect((await watchButton.boundingBox())?.width, "the button keeps its width").toBe(width);
      await page.mouse.move(700, 880);
      await expect(watchlistLinks(page)).toHaveText(watchlistNames(personal));

      for (const region of ["This week", "Top DBS developments", "Related to DBS"]) {
        await expect(page.getByRole("region", { name: region }), `the ${region} block`).toBeVisible();
      }
      const top = page.getByRole("region", { name: "Top DBS developments" });
      const shown = month.developments.slice(0, 5);
      await expect(top.locator(".frow")).toHaveCount(shown.length);
      await expect(top.locator(".frow .ftitle").first()).toHaveText(shown[0].headline);
      await expectOrdered(top, shown);

      // None of the removed controls: Ask, What changed, follows, reviews, dashboards, the run chip, alerts.
      for (const name of ["Add widget", "Edit", "Run now", "Mark all reviewed", "Mark reviewed", "Follow", "Generate", "New dashboard", "Add a focus area"]) {
        await expect(page.getByRole("button", { name, exact: true }), `no ${name} button`).toHaveCount(0);
      }
      await expect(page.getByRole("button", { name: /^(Alerts|Show runs|Monitoring)/ })).toHaveCount(0);
      await expect(page.getByRole("link", { name: /^Ask/ }), "no Ask page or Ask about").toHaveCount(0);
      await expect(page.getByRole("button", { name: /^Ask/ }), "no Ask about").toHaveCount(0);
      await expect(page.getByRole("region", { name: /^What changed/ })).toHaveCount(0);
      await expect(sidebar(page).getByText("Dashboards", { exact: true }), "no dashboards in the sidebar").toHaveCount(0);
      await expect(page.locator("[data-dashboard-id], [data-widget-id], .profile, dialog.tour")).toHaveCount(0);

      await expect(recentLinks(page)).toHaveText(["DBS", "MAS"]);
      expect(await storedState(page)).toEqual({ recent: ["dbs", "mas"], watchlist: personal });
    });

    await step(STEPS[5], async () => {
      const tabs = page.getByRole("navigation", { name: "Monitor views" });
      await tabs.getByRole("link", { name: "Developments" }).click();
      await expect(page).toHaveURL(/\/entities\/dbs\/developments$/);
      await expect(tabs.getByRole("link", { name: "Developments" })).toHaveAttribute("aria-current", "page");
      const feed = page.getByRole("region", { name: "Developments about DBS" });
      await expect(feed.locator(".frow")).toHaveCount(month.developments.length);
      await expectOrdered(feed, month.developments);
      const ranks = (await badges(feed)).map((b) => PRIORITIES.findIndex((p) => p === b));
      expect(ranks, "highest priority first").toEqual([...ranks].sort((a, b) => a - b));
      // New and Updated show on every row the latest run added or updated, from that run's changes.
      // The store needs a run after the first for the marks to be exercised at all.
      expect(month.developments.some((d) => d.change !== null), "the latest run changed some of DBS's month").toBe(true);
      await expect(feed.locator(".frow .b-new")).toHaveCount(month.developments.filter((d) => d.change === "new").length);
      await expect(feed.locator(".frow .upd")).toHaveCount(month.developments.filter((d) => d.change === "updated").length);
      await expect(feed.locator(".feed-foot")).toHaveText(`New: added in the run of ${shortDate(latest.as_of)}. Updated: new reports in that run.`);
      await expect(feed.getByRole("button", { name: /^(Show|Hide) dismissed/ })).toHaveCount(0);
      await expect(feed.getByRole("link", { name: /^Show all/ })).toHaveCount(0);

      // This week, then its highest priority: the URL holds both and a reload keeps them.
      const week = await unwrap(api.GET("/developments", { params: { query: { subject_id: ["dbs"], days: 7 } } }));
      const level = PRIORITIES.find((p) => week.developments.some((d) => d.priority === p));
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

      // Related lists Could affect.
      await tabs.getByRole("link", { name: "Related" }).click();
      await expect(page).toHaveURL(/\/entities\/dbs\/related$/);
      await expect(page.locator("main .mon-sec h2")).toHaveText(["Could affect DBS"]);
      await expect(page.getByRole("region", { name: "Could affect DBS" }).locator(".ca-row")).not.toHaveCount(0);

      // Data: the chart marks High and Medium developments, in plain words.
      await tabs.getByRole("link", { name: "Data" }).click();
      await expect(page).toHaveURL(/\/entities\/dbs\/data$/);
      await expect(page.locator("main .tr-legend").first()).toHaveText("HighMedium");
      const indicatorBadges = await page.locator('section[aria-labelledby="indicators"] .badge').allTextContents();
      expect(indicatorBadges.filter((b) => b !== "No data" && b !== "Stale"), "no Watch or Stable on series").toEqual([]);

      // Old page URLs land on the same tab at the new ones.
      for (const [old, now] of [
        ["/monitors/dbs/related", "/entities/dbs/related"],
        ["/monitors/dbs/trend#indicators", "/entities/dbs/data#indicators"],
        ["/topics/dbs/developments?days=7", "/entities/dbs/developments?days=7"],
      ]) {
        await page.goto(old);
        await expect(page, `${old} lands on ${now}`).toHaveURL(new URL(now, page.url()).href);
      }
      await expect(page.getByRole("heading", { level: 1, name: "DBS" })).toBeVisible();

      // A group's page URL opens the Entities list filtered to that group.
      const group = subjects.find((s) => s.type === "group" && s.members.includes("dbs"));
      if (!group) throw new Error("DBS is in no group");
      await page.goto(`/entities/${group.id}`);
      await expect(page).toHaveURL(new RegExp(`/entities\\?group=${group.id}$`));
      await expect(facetButton(page, "Group", group.name)).toHaveAttribute("aria-pressed", "true");

      // Removed pages land on home, the first entity on the watchlist.
      for (const old of ["/setup", "/ask?about=dbs", "/dashboards/anything", "/developments?dashboard=anything"]) {
        await page.goto(old);
        await expect(page, `${old} lands on home`).toHaveURL(new RegExp(`${homeOf(personal)}$`));
      }

      // A topic links to nothing: Overview, Developments and Data (when it has series), no Related.
      const topic = subject("financial-regulation");
      const topicPage = await unwrap(api.GET("/subjects/{subject_id}/page", { params: { path: { subject_id: topic.id } } }));
      const indicators = await unwrap(api.GET("/subjects/{subject_id}/indicators", { params: { path: { subject_id: topic.id } } }));
      const hasData = Boolean(topicPage.trend) || indicators.indicators.some((i) => i.series.listed);
      await page.goto(`/topics/${topic.id}/related`);
      await expect(page).toHaveURL(new RegExp(`/topics/${topic.id}$`));
      await expect(page.getByRole("heading", { level: 1, name: topic.name })).toBeVisible();
      await expect(tabs.getByRole("link")).toHaveText(["Overview", "Developments", ...(hasData ? ["Data"] : [])]);
      expect(await storedState(page)).toEqual({ recent: [topic.id, "dbs", "mas"], watchlist: personal });
    });

    await step(STEPS[6], async () => {
      // A High headline on the Entities list opens the popup over the list, scoped to its row.
      await page.goto("/entities");
      const headline = page.locator('.lp-table tbody[data-band="High"] .lp-headline').first();
      await expect(headline).toBeVisible();
      const listUrl = page.url();
      const atList = await storedState(page);
      const title = (await headline.textContent()) ?? "";
      await headline.click();
      const fromList = page.getByRole("dialog", { name: title });
      await expect(fromList.getByRole("heading", { level: 2, name: title })).toBeFocused();
      const listId = new URL(page.url()).searchParams.get("development") ?? "";
      await expect(page).toHaveURL(popupUrl("/entities", listId));
      await expect(fromList.getByRole("heading", { name: "Who it impacts" })).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(page).toHaveURL(listUrl);
      await expect(fromList).toHaveCount(0);
      await expect(headline, "focus returns to the headline").toBeFocused();
      expect(await storedState(page), "the popup changes no browser state").toEqual(atList);

      // A row from DBS's Developments tab that reaches other entities: its labels, key points, and
      // Who it impacts rows with the API's level and route.
      await page.goto("/entities/dbs/developments");
      const feedUrl = page.url();
      await expect(page.getByRole("heading", { level: 1, name: "DBS" })).toBeVisible();
      const before = await storedState(page);
      let detail: Schema<"DevelopmentDetail"> | undefined;
      for (const d of month.developments) {
        const found = await unwrap(api.GET("/developments/{development_id}", { params: { path: { development_id: d.id }, query: { subject_id: ["dbs"] } } }));
        if (found.impacts.length > 0) {
          detail = found;
          break;
        }
      }
      if (!detail) throw new Error("no DBS development this month reaches another entity");
      const d = detail;
      const feed = page.getByRole("region", { name: "Developments about DBS" });
      const link = feed.getByRole("link", { name: d.headline, exact: true });
      await link.focus();
      await page.keyboard.press("Enter");
      await expect(page).toHaveURL(popupUrl("/entities/dbs/developments", d.id));
      const popup = page.getByRole("dialog", { name: d.headline });
      await expect(popup.getByRole("heading", { level: 2, name: d.headline })).toBeFocused();
      await expect(popup.getByRole("button", { name: "Close development" })).toBeVisible();
      await expect(popup.getByRole("button", { name: /^(Dismiss|Not relevant|Restore to feed|Back)/ })).toHaveCount(0);
      for (const word of ["materiality", "Tagged to", "tagged by", "Needs attention", "Dismissed", "Marked not relevant", "Could hurt", "Could help", "confidence"]) {
        await expect(popup, `no "${word}"`).not.toContainText(word, { ignoreCase: true });
      }
      if (d.points.length > 0) await expect(popup.locator("ul.dp-points li")).toHaveText(d.points);
      else if (d.headline !== d.title) await expect(popup.locator(".dp-points")).toHaveText(d.title);
      await expect(popup.getByRole("heading", { name: "Who it impacts" })).toBeVisible();
      await expect(popup.getByRole("heading", { name: "Connections" })).toBeVisible();
      const rows = popup.locator(".imp");
      await expect(rows).toHaveCount(d.impacts.length);
      // A hidden entity reached has no page, so its name is text and only displayed ones link.
      await expect(rows.locator(".imp-h a")).toHaveText(d.impacts.filter((i) => i.subject.displayed).map((i) => i.subject.name));
      for (const [n, i] of d.impacts.entries()) await expect(rows.nth(n).locator(".imp-h")).toContainText(i.subject.name);
      await expect(rows.locator(".imp-route")).toHaveText(d.impacts.map((i) => i.route));
      await expect(rows.locator(".imp-dir")).toHaveText(d.impacts.map((i) => i.level));
      // 80% of the screen, within its bounds.
      const box = await popup.boundingBox();
      expect(box && Math.round(box.width) === Math.round(Math.min(1400, Math.max(900, VIEWPORT.width * 0.8))), "80% wide").toBe(true);
      await page.keyboard.press("Escape");
      await expect(page).toHaveURL(feedUrl);
      await expect(popup).toHaveCount(0);
      await expect(link, "focus returns to the row").toBeFocused();
      expect(await storedState(page), "the popup changes no browser state").toEqual(before);

      // A development's old address opens the popup over its subject's page, or over home without one.
      await page.goto(`/developments/${d.id}?subject=dbs`);
      await expect(page).toHaveURL(popupUrl("/entities/dbs", d.id));
      await expect(popup).toBeVisible();
      await popup.getByRole("button", { name: "Close development" }).click();
      await expect(page).toHaveURL(/\/entities\/dbs$/);
      await page.goto(`/developments/${d.id}`);
      await expect(page).toHaveURL(popupUrl(homeOf(personal), d.id));
      await expect(popup).toBeVisible();
      await popup.getByRole("button", { name: "Close development" }).click();
      await expect(page).toHaveURL(new RegExp(`${homeOf(personal)}$`));
    });

    await step(STEPS[7], async () => {
      const visits = ["uob", "hsbc", "hkma", "cyber-resilience", "aml-sanctions", "monetary-policy", "dbs"].map(subject);
      for (const s of visits) {
        await page.goto(pagePath(s));
        await expect(page.getByRole("heading", { level: 1, name: s.name })).toBeVisible();
        await expect(recentLinks(page).first()).toHaveText(s.name);
      }
      await page.goto("/entities/no-such-entity");
      await expect(page.getByText("This page could not be loaded.")).toBeVisible();
      const recent = [...visits].reverse().slice(0, 5);
      await expect(recentLinks(page)).toHaveText(recent.map((s) => s.name));
      expect(await storedState(page)).toMatchObject({ recent: recent.map((s) => s.id), watchlist: personal });
    });

    await step(STEPS[8], async () => {
      await page.goto("/entities/dbs");
      await expect(watchlistLinks(page)).toHaveText(watchlistNames(personal));
      for (const name of watchlistNames(personal)) {
        await removeFromSidebar(page, name);
        personal = personal.filter((id) => subject(id).name !== name);
        await expect(watchlistLinks(page)).toHaveText(watchlistNames(personal));
      }
      const watchlist = sidebar(page).getByRole("region", { name: /^Watchlist/ });
      await expect(watchlist.getByText("Empty")).toBeVisible();
      await page.reload();
      await expect(page.getByRole("heading", { level: 1, name: "DBS" })).toBeVisible();
      await expect(watchlistLinks(page), "MAS does not come back").toHaveCount(0);
      await page.goto("/");
      await expect(page).toHaveURL(/\/entities$/);
      expect(await storedState(page)).toMatchObject({ watchlist: [] });
    });

    await step(STEPS[9], async () => {
      await sidebar(page).getByRole("button", { name: "View: Personal" }).click();
      await expect(page.getByRole("menu", { name: "Views" }).getByRole("menuitemradio")).toHaveText(["Personal", ...functions.map((f) => f.name)]);
      await page.keyboard.press("Escape");
      await pickView(page, "Personal", supervision.name);

      // The preset as the API serves it, entities then topics, locked, with no x.
      await expect(sidebar(page).getByRole("region", { name: "Watchlist, fixed", exact: true })).toBeVisible();
      await expect(watchlistLinks(page)).toHaveText(watchlistNames(preset));
      await expect(removeButtons(page)).toHaveCount(0);
      expect(await storedState(page)).toMatchObject({ view: supervision.id, watchlist: [] });
      await page.goto("/");
      await expect(page).toHaveURL(new RegExp(`${homeOf(preset)}$`));

      // The Topics list's function filter does not follow the view.
      await sidebar(page).getByRole("link", { name: `Topics ${TOPIC_COUNT}` }).click();
      await expect(facetButton(page, "Function", "Any")).toHaveAttribute("aria-pressed", "true");
      await expect(page.locator(".lp-table tbody tr[data-id]")).toHaveCount(TOPIC_COUNT);

      // Add to watchlist on a page outside the preset changes the Personal watchlist only.
      const outside = subject("sgx");
      expect(preset).not.toContain(outside.id);
      await page.goto(pagePath(outside));
      const watchButton = page.locator(".mon-head .watch-btn");
      await expect(watchButton).toHaveAccessibleName("Add to watchlist");
      await watchButton.click();
      personal = [outside.id];
      await page.mouse.move(700, 880);
      await expect(watchButton.locator(".watch-on")).toHaveText("On Personal watchlist");
      await expect(watchlistLinks(page)).toHaveText(watchlistNames(preset));
      await page.reload();
      await expect(sidebar(page).getByRole("button", { name: `View: ${supervision.name}` })).toBeVisible();
      await expect(watchlistLinks(page)).toHaveText(watchlistNames(preset));
      expect(await storedState(page)).toMatchObject({ view: supervision.id, watchlist: personal });

      await pickView(page, supervision.name, "Personal");
      await expect(sidebar(page).getByRole("region", { name: "Watchlist", exact: true })).toBeVisible();
      await expect(watchlistLinks(page)).toHaveText(watchlistNames(personal));
      expect(await storedState(page)).toMatchObject({ view: "personal", watchlist: personal });
    });

    await step(STEPS[10], async () => {
      const context = await openContext("two-tabs");
      const a = await context.newPage();
      const b = await context.newPage();
      at = { shot: b, state: b };
      await a.goto("/entities/dbs");
      await expect(recentLinks(a)).toHaveText(["DBS"]);
      await b.goto("/entities/ocbc");
      // Each tab's own write (Recently viewed) has reached the other before the steps below write.
      for (const p of [a, b]) await expect(recentLinks(p)).toHaveText(["OCBC", "DBS"]);
      for (const p of [a, b]) await expect(watchlistLinks(p)).toHaveText(["MAS"]);

      // An add in one tab shows in the other without a reload, and so do a removal and a view change.
      await a.locator(".mon-head .watch-btn").click();
      await expect(watchlistLinks(b)).toHaveText(watchlistNames(["mas", "dbs"]));
      await removeFromSidebar(b, "MAS");
      await expect(watchlistLinks(a)).toHaveText(["DBS"]);
      const policy = functions.find((f) => f.id === "economic-policy");
      if (!policy) throw new Error("no Economic Policy function in /functions");
      await pickView(a, "Personal", policy.name);
      await expect(sidebar(b).getByRole("button", { name: `View: ${policy.name}` })).toBeVisible();
      await expect(watchlistLinks(b)).toHaveText(watchlistNames([...policy.watchlist.entities, ...policy.watchlist.topics]));
      expect(await storedState(b)).toMatchObject({ watchlist: ["dbs"], view: policy.id });
    });

    await step(STEPS[11], async () => {
      // A store written by older code: a topic, a group and a now hidden entity's id, a bad `recent`,
      // an unknown view and the retired sidebar key, set once before the app loads.
      const group = subjects.find((s) => s.type === "group" && s.members.includes("dbs"));
      if (!group) throw new Error("DBS is in no group");
      expect(byId.has("cyber-threats"), "cyber-threats left the seed").toBe(false);
      expect(byId.has("singtel"), "Singtel is hidden").toBe(false);
      const seeded = await openContext("stored-state");
      await seeded.addInitScript(
        ({ key, value }) => {
          // A new page starts on about:blank, which has no storage.
          if (location.protocol === "about:" || sessionStorage.getItem("e2e-seeded")) return;
          sessionStorage.setItem("e2e-seeded", "1");
          localStorage.setItem("im.sidebar.closed", JSON.stringify(["entities"]));
          localStorage.setItem(key, value);
        },
        { key: STATE_KEY, value: JSON.stringify({ watchlist: ["cyber-threats", group.id, "singtel", "dbs"], recent: 42, view: "no-such-view" }) },
      );
      const s = await seeded.newPage();
      at = { shot: s, state: s };
      await s.goto("/entities");
      await expect(watchlistLinks(s)).toHaveText(["DBS"]);
      await expect(recentLinks(s)).toHaveCount(0);
      await expect(sidebar(s).getByRole("region", { name: "Recently viewed" }).getByText("Empty")).toBeVisible();
      await expect(sidebar(s).getByRole("button", { name: "View: Personal" })).toBeVisible();
      expect(await s.evaluate(() => localStorage.getItem("im.sidebar.closed")), "the retired key goes").toBeNull();
      await s.locator('.lp-table tr[data-id="ocbc"]').getByRole("button", { name: "Add OCBC to watchlist" }).click();
      await expect(watchlistLinks(s)).toHaveText(watchlistNames(["dbs", "ocbc"]));
      expect(await storedState(s)).toMatchObject({ watchlist: ["dbs", "ocbc"] });
      await closeContext(seeded);

      // Blocked storage: localStorage.setItem throws. Changes still work in the tab, one notice
      // says they cannot be saved, and a reload starts from the defaults.
      const blocked = await openContext("blocked-storage");
      await blocked.addInitScript(() => {
        const setItem = Storage.prototype.setItem;
        Storage.prototype.setItem = function (key: string, value: string) {
          if (this === window.localStorage) throw new DOMException("Storage is blocked", "SecurityError");
          setItem.call(this, key, value);
        };
      });
      const p = await blocked.newPage();
      at = { shot: p, state: p };
      await p.goto("/entities/dbs");
      await expect(p.getByRole("heading", { level: 1, name: "DBS" })).toBeVisible();
      await expect(watchlistLinks(p)).toHaveText(["MAS"]);
      const button = p.locator(".mon-head .watch-btn");
      const notice = p.getByText(NOTICE);
      await button.click();
      await p.mouse.move(700, 880);
      await expect(button.locator(".watch-on")).toHaveText("On watchlist");
      await expect(watchlistLinks(p)).toHaveText(watchlistNames(["mas", "dbs"]));
      await expect(notice).toBeVisible();
      await expect(notice).toBeHidden({ timeout: 15_000 });
      await button.click();
      await p.mouse.move(700, 880);
      await expect(watchlistLinks(p)).toHaveText(["MAS"]);
      await button.click();
      await p.mouse.move(700, 880);
      await expect(watchlistLinks(p)).toHaveText(watchlistNames(["mas", "dbs"]));
      await p.waitForTimeout(1500);
      await expect(notice, "the notice shows once").toBeHidden();
      await p.reload();
      await expect(p.getByRole("heading", { level: 1, name: "DBS" })).toBeVisible();
      await expect(p.getByRole("region", { name: "Top DBS developments" }).locator(".frow").first(), "the page loads in full").toBeVisible();
      await expect(watchlistLinks(p)).toHaveText(["MAS"]);
      await expect(recentLinks(p)).toHaveText(["DBS"]);
      expect(await storedState(p), "nothing was saved").toBeNull();
    });

    await step(STEPS[12], async () => {
      await page.goto("/entities/dbs");
      await expect(page.getByRole("heading", { level: 1, name: "DBS" })).toBeVisible();
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

    await step(STEPS[13], async () => {
      await page.setViewportSize({ width: 1024, height: 800 });
      // Names a reader must see to decide what to open show in full: no clamp, no ellipsis, no
      // hidden overflow. List headlines keep Mock C's two-line clamp; the popup shows them whole.
      const cut = (selectors: string) =>
        page.evaluate(
          (s) =>
            [...document.querySelectorAll<HTMLElement>(s)]
              .filter((el) => el.clientHeight > 0 && (el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1))
              .map((el) => el.textContent),
          selectors,
        );
      await page.goto("/entities");
      await expect(page.locator(".lp-table tbody tr[data-id]")).toHaveCount(ENTITY_COUNT);
      expect(await page.evaluate(() => document.documentElement.scrollWidth), "no sideways scroll on the list").toBe(1024);
      expect(await cut(".lp-name a, .navitem > span:first-of-type, h1")).toEqual([]);
      await page.goto("/entities/dbs/developments");
      await expect(page.getByRole("heading", { level: 1, name: "DBS" })).toBeVisible();
      await expect(page.locator(".frow").first()).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth), "no sideways scroll on the page").toBe(1024);
      expect(await cut(".ftitle, .navitem > span:first-of-type, h1")).toEqual([]);
    });

    await step(STEPS[14], async () => {
      // Step 14 leaves the 1024 px viewport for its screenshot; the rest run at the full width.
      await page.setViewportSize(VIEWPORT);
      await page.evaluate(() => (document.activeElement instanceof HTMLElement ? document.activeElement.blur() : undefined));
      await page.keyboard.press("?");
      const help = page.getByRole("dialog").filter({ has: page.getByRole("heading", { name: "Help" }) });
      await expect(help).toBeVisible();
      const labels = help.getByRole("region", { name: "Labels on a development" });
      for (const word of ["High", "Medium", "Low", "Adverse event", "New", "Updated"]) await expect(labels).toContainText(word);
      await expect(labels, "New is the latest run, not the last visit").not.toContainText("last visit");
      await page.keyboard.press("Escape");
      await expect(help).toHaveCount(0);
      await sidebar(page).getByRole("button", { name: "Help", exact: true }).click();
      await expect(help).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(help).toHaveCount(0);
      await sidebar(page).getByRole("button", { name: "Data sources", exact: true }).click();
      const sources = page.getByRole("dialog").filter({ has: page.getByRole("heading", { name: "Data sources" }) });
      await expect(sources).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(sources).toHaveCount(0);
    });

    await step(STEPS[15], async () => {
      // `im check series` backfills every series into a scratch store from data/raw/ alone, then
      // fetches again as the daily fetch does. A request with no cached response fails instead of
      // reaching the source, so a clean pass proves the fetch path runs offline end to end.
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

    await step(STEPS[16], async () => {
      // `im check detector` feeds fixture loan-to-deposit values to a scratch store; crossing 90%
      // fires the rule into one development that reaches High through its rule.
      const check: DetectorCheck = JSON.parse(imCheck("detector"));
      expect(check.detections).toHaveLength(1);
      expect(check.detections[0]).toMatch(/rose above 90\.0%/);
      expect(check.developments).toHaveLength(1);
      const [d] = check.developments;
      expect([d.title, d.materiality_label, d.materiality_rule, d.needs_attention, d.sources]).toEqual([check.detections[0], "High", "ltd-above-90", true, 1]);
      writeFileSync(path.join(dir, "detector.json"), JSON.stringify(check, null, 2));
      await showJson(page, check);
    });

    await step(STEPS[17], async () => {
      // `im check past-run` recomputes the run before the latest in a scratch copy of the store,
      // replaying only recorded model calls: an unrecorded call fails it instead of reaching a model.
      const check: PastRunCheck = JSON.parse(imCheck("past-run"));
      expect(check.as_of, "the run before the latest").toBe(latest.previous_run_id ?? latest.id);
      const counted = check.log.filter((l) => / calls, \d+ cached\)$/.test(l));
      expect(counted.length).toBeGreaterThan(0);
      expect(counted.filter((l) => !/ 0 calls, /.test(l)), "no model call").toEqual([]);
      expect(check.log.at(-1)).toContain("] complete: ");
      expect(check.late, "nothing known after its date").toEqual({ sources: 0, indicators: 0 });
      expect(check.developments).toBeGreaterThan(0);
      if (latest.previous_run_id) expect(check.later_documents, "the store holds later documents").toBeGreaterThan(0);
      expect(check.differences, "equal to the stored run").toEqual([]);
      writeFileSync(path.join(dir, "past-run.json"), JSON.stringify(check, null, 2));
      await showJson(page, { ...check, log: check.log.slice(-8) });
    });

    await step(STEPS[18], async () => {
      // check_endpoints.py reads every endpoint for a sample of subjects, checks the catalogue and
      // the lists, and that the API serves only GET operations; it calls no model.
      const out = path.join(dir, "api-check.json");
      const run = spawnSync("uv", ["run", "python", "scripts/check_endpoints.py", "--base", API_URL, "--out", out], { cwd: API_DIR, encoding: "utf8" });
      const check: EndpointCheck = JSON.parse(readFileSync(out, "utf8"));
      expect(check.steps.map((s) => s.step)).toContain("read only");
      expect(check.steps.filter((s) => !s.ok), run.stdout).toEqual([]);
      expect(run.status, run.stderr).toBe(0);
      await showJson(page, check.steps);
    });
  } finally {
    for (const { context } of [...open]) await closeContext(context).catch(() => undefined);
    rmSync(path.join(dir, "videos"), { recursive: true, force: true });
    writeFileSync(path.join(dir, "requests.json"), JSON.stringify(requests, null, 2));
    writeFileSync(path.join(dir, STORAGE_FILE), JSON.stringify(storage, null, 2));
  }
  expect(pageErrors, "no uncaught errors in any page").toEqual([]);
  // The pages only read: every API request is a GET (CORS preflights aside), none reaches a removed
  // endpoint or carries a removed feature's parameter.
  expect(requests.filter((r) => r.method !== "GET" && r.method !== "OPTIONS"), "every API request is a GET").toEqual([]);
  const removed = requests.filter((r) => {
    const url = new URL(r.path, API_URL);
    return REMOVED_PATHS.some((p) => p.test(url.pathname)) || REMOVED_PARAMS.some((k) => url.searchParams.has(k));
  });
  expect(removed, "no request to a removed endpoint").toEqual([]);
});
