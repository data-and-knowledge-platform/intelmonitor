// Writes index.html and summary.json into the run directory: each walkthrough step with its
// status, duration, error, screenshot and the browser state (`im.state.v1`) after it, plus the
// run's videos (the test's own context as video.webm, each other context as video-<name>.webm),
// trace, requests.json, the pipeline checks' JSON and api-check.json, linked from the page.
import type { Reporter, TestCase, TestResult, TestStep } from "@playwright/test/reporter";
import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

import { runDir, screenshotName, STEPS, STORAGE_FILE, type StepStorage } from "./steps";

type Status = "passed" | "failed" | "not run";

interface Row {
  title: string;
  status: Status;
  ms: number;
  error: string | null;
  screenshot: string | null;
  /** The parsed `im.state.v1` after the step; null when unset or the step never ran. */
  storage: unknown;
}

// Files the walkthrough writes beside the report, linked when present.
const FILES = ["requests.json", "series-fetch.json", "detector.json", "past-run.json", "api-check.json"];

const escape = (text: string) =>
  text.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c] ?? c);

// Playwright error messages carry ANSI colour codes.
// eslint-disable-next-line no-control-regex
const plain = (text: string) => text.replace(/\u001b\[[0-9;]*m/g, "");

export default class WalkthroughReporter implements Reporter {
  private result: TestResult | null = null;
  private test: TestCase | null = null;

  onTestEnd(test: TestCase, result: TestResult) {
    this.test = test;
    this.result = result;
  }

  onEnd() {
    const dir = runDir();
    mkdirSync(dir, { recursive: true });
    const result = this.result;
    const steps = new Map<string, TestStep>(
      (result?.steps ?? []).filter((s) => s.category === "test.step").map((s) => [s.title, s]),
    );
    const storageFile = path.join(dir, STORAGE_FILE);
    const storage: StepStorage = existsSync(storageFile) ? JSON.parse(readFileSync(storageFile, "utf8")) : {};
    const rows: Row[] = STEPS.map((title, i) => {
      const step = steps.get(title);
      const shot = screenshotName(i);
      return {
        title,
        status: !step ? "not run" : step.error ? "failed" : "passed",
        ms: step?.duration ?? 0,
        error: step?.error?.message ? plain(step.error.message) : null,
        screenshot: existsSync(path.join(dir, shot)) ? shot : null,
        storage: storage[title] ?? null,
      };
    });

    const copied = (name: string, file: string) => {
      const from = result?.attachments.find((a) => a.name === name)?.path;
      if (!from || !existsSync(from)) return null;
      copyFileSync(from, path.join(dir, file));
      return file;
    };
    const video = copied("video", "video.webm");
    const trace = copied("trace", "trace.zip");
    const videos = [video, ...readdirSync(dir).filter((f) => /^video-.+\.webm$/.test(f)).sort()].filter((f) => f !== null);
    const files = FILES.filter((f) => existsSync(path.join(dir, f)));

    const passed = result?.status === "passed" && rows.every((r) => r.status === "passed");
    // The walkthrough does not fail a step whose screenshot could not be taken, so the report
    // flags it: the proof has a hole there.
    const unshot = rows.filter((r) => r.status === "passed" && !r.screenshot).map((r) => r.title);
    const outside = result?.errors
      .map((e) => plain(e.message ?? ""))
      .filter((m) => m && !rows.some((r) => r.error === m));
    writeFileSync(path.join(dir, "index.html"), page({ rows, passed, unshot, videos, trace, files, result, test: this.test, outside: outside ?? [] }));
    writeFileSync(
      path.join(dir, "summary.json"),
      JSON.stringify({ passed, status: result?.status ?? "not run", unshot, steps: rows, videos, files }, null, 2),
    );
    if (unshot.length) console.log(`\nPassed with no screenshot: ${unshot.join("; ")}`);
    console.log(`\nE2E artifacts: ${dir}/index.html`);
  }
}

function page({
  rows,
  passed,
  unshot,
  videos,
  trace,
  files,
  result,
  test,
  outside,
}: {
  rows: Row[];
  passed: boolean;
  /** Titles of passed steps with no screenshot. */
  unshot: string[];
  videos: string[];
  trace: string | null;
  files: string[];
  result: TestResult | null;
  test: TestCase | null;
  outside: string[];
}) {
  const started = result?.startTime.toISOString() ?? "";
  const seconds = Math.round((result?.duration ?? 0) / 1000);
  const items = rows
    .map(
      (r, i) => `
    <section class="step ${r.status.replace(" ", "-")}">
      <div class="text">
        <div class="head">
          <span class="n">${i + 1}</span>
          <h2>${escape(r.title)}</h2>
        </div>
        <p><span class="status">${r.status}</span>${r.status === "not run" ? "" : `, ${(r.ms / 1000).toFixed(1)} s`}</p>
        ${r.error ? `<pre>${escape(r.error)}</pre>` : ""}
        ${unshot.includes(r.title) ? `<p class="flag">Passed, but no screenshot was saved.</p>` : ""}
        ${r.status === "not run" ? "" : `<p class="state">im.state.v1</p><pre class="storage">${escape(JSON.stringify(r.storage))}</pre>`}
      </div>
      ${r.screenshot ? `<a href="${r.screenshot}"><img src="${r.screenshot}" alt="Step ${i + 1} screenshot" loading="lazy"></a>` : "<span></span>"}
    </section>`,
    )
    .join("");
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IntelMonitor E2E</title>
<style>
  :root { --ink: #1c1c1e; --ink2: #6e6e73; --line: #e4e4ea; --pass: #1f7a3a; --fail: #c62828; --bg: #f5f5f7; }
  body { margin: 0; background: var(--bg); color: var(--ink); font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif; }
  main { max-width: 1100px; margin: 0 auto; padding: 24px 16px 48px; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .meta { color: var(--ink2); margin: 0 0 16px; }
  .verdict { font-weight: 600; color: ${passed ? "var(--pass)" : "var(--fail)"}; }
  .files a { margin-right: 16px; }
  video { width: 100%; border: 1px solid var(--line); border-radius: 8px; background: #000; }
  .rec { font-size: 15px; margin: 24px 0 8px; }
  .step { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 16px; background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; margin: 0 0 12px; }
  .head { display: flex; align-items: baseline; gap: 10px; }
  .head h2 { font-size: 15px; margin: 0; }
  .step p { margin: 6px 0 0 22px; color: var(--ink2); }
  .n { color: var(--ink2); font-variant-numeric: tabular-nums; width: 12px; }
  .status { font-weight: 600; }
  .passed .status { color: var(--pass); }
  .failed .status { color: var(--fail); }
  .not-run .status { color: var(--ink2); }
  .step p.flag { color: var(--fail); font-weight: 600; }
  img { display: block; width: 100%; border: 1px solid var(--line); border-radius: 6px; }
  @media (max-width: 640px) { .step { grid-template-columns: 1fr; } }
  pre { white-space: pre-wrap; background: #fff5f5; border-radius: 6px; padding: 8px; font-size: 12px; overflow-x: auto; }
  .step .state { margin-top: 10px; font-size: 12px; }
  pre.storage { background: var(--bg); margin: 4px 0 0 22px; }
</style>
</head>
<body>
<main>
  <h1>IntelMonitor E2E</h1>
  <p class="meta"><span class="verdict">${passed ? "Passed" : "Failed"}</span>, ${rows.filter((r) => r.status === "passed").length} of ${rows.length} steps passed${unshot.length ? `, <span class="verdict" style="color: var(--fail)">${unshot.length} with no screenshot</span>` : ""}. ${escape(test?.title ?? "")}. Started ${escape(started)}, ${seconds} s.</p>
  <p class="files">${videos.length ? videos.map((v) => `<a href="${v}">${escape(v)}</a>`).join("") : "No video"}${trace ? `<a href="${trace}">Trace</a> <span class="meta">open with <code>pnpm exec playwright show-trace trace.zip</code></span>` : "No trace"}</p>
  <p class="files">${files.map((f) => `<a href="${f}">${f}</a>`).join("")}</p>
  ${outside.map((m) => `<pre>${escape(m)}</pre>`).join("")}
  ${items}
  ${videos.map((v) => `<h2 class="rec">Recording: ${escape(v)}</h2><video src="${v}" controls muted preload="metadata"></video>`).join("")}
</main>
</body>
</html>
`;
}
