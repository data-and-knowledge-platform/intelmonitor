// Display formatting shared by pages, the shell and dialogs.
import type { Schema } from "./api/client";

/** Parses an API date (YYYY-MM-DD) as a local calendar day. */
export function day(iso: string): Date {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d);
}

// Fixed three-letter months: Intl's en-GB writes "Sept".
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const dayMonth = (d: Date) => `${d.getDate()} ${MONTHS[d.getMonth()]}`;

/** "24 Sep" for an API date. */
export const shortDate = (iso: string) => dayMonth(day(iso));

/** The API date `days` after an API date. */
export function addDays(iso: string, days: number): string {
  const d = day(iso);
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** "24 Sep 2026" for an API date. */
export const longDate = (iso: string) => `${shortDate(iso)} ${day(iso).getFullYear()}`;

export const plural = (n: number, word: string, many = `${word}s`) =>
  `${n} ${n === 1 ? word : many}`;

/** First letters of up to three words: "Wong Kang" is "WK", "DBS" is "D". */
export const initials = (name: string) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .map((w) => w[0])
    .join("")
    .slice(0, 3)
    .toUpperCase();

export const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

type SubjectType = Schema<"SubjectRef">["type"];

export const TYPE_LABEL: Record<SubjectType, string> = {
  topic: "Topic",
  group: "Group",
  entity: "Entity",
};

export const COLLECTION_LABEL: Record<Schema<"EvidenceSource">["collection"], string> = {
  dms: "DMS",
  official: "Official",
  data: "Data",
  news: "News",
};

type Series = Schema<"SeriesInfo">;
type Indicator = Schema<"SubjectIndicator">;
export type IndicatorState = Indicator["state"];

/** An indicator's data state (api/intelmonitor/pipeline/indicators.py), in words. */
export const INDICATOR_STATE_LABEL: Record<IndicatorState, string> = {
  no_data: "No data",
  stale: "Stale",
  revised: "Revised",
  preliminary: "Preliminary",
  current: "Current",
};

export const SCOPE_LABEL: Record<NonNullable<Series["scope"]>, string> = {
  system: "System-wide",
  market: "Market-wide",
};

// A price quoted in a currency (unit a currency code, such as a share's close): "S$42.50", "MYR 10.12".
const CURRENCY_PREFIX: Record<string, string> = { USD: "$", SGD: "S$", HKD: "HK$" };
const CURRENCY = /^[A-Z]{3}$/;
// An amount published in millions or billions of a currency, as hand-collected figures are: "HKD bn".
const SCALED = /^([A-Z]{3}) (m|bn)$/;

/**
 * How an amount in this unit reads as money in trillions, billions or millions: USD or SGD with no
 * decimals, and any "<currency> m" or "<currency> bn". Undefined for other units.
 */
function money(s: Series): { prefix: string; scale: number } | undefined {
  const scaled = SCALED.exec(s.unit);
  if (scaled) {
    const [, code, size] = scaled;
    return { prefix: CURRENCY_PREFIX[code] ?? `${code} `, scale: size === "m" ? 1e6 : 1e9 };
  }
  if (s.decimals !== 0) return undefined;
  return s.unit === "USD" || s.unit === "SGD" ? { prefix: CURRENCY_PREFIX[s.unit], scale: 1 } : undefined;
}
const MINUS = "−";
const signed = (x: number, text: string) => `${x > 0 ? "+" : x < 0 ? MINUS : ""}${text}`;

/**
 * A series value in its unit: "70.3%", "S$16.5M", "$183.4B", "S$42.50", "MYR 10.12", "0.3132" (SGD
 * per MYR). A distance
 * from a target (a peg, `baseline` set) is always signed, "+0.04%" above and "−0.01%" below; a
 * value that rounds to zero takes no sign.
 */
export function seriesValue(s: Series, v: number): string {
  const text = magnitude(s, Math.abs(v));
  if (!/[1-9]/.test(text)) return text;
  return s.baseline !== null ? signed(v, text) : `${v < 0 ? MINUS : ""}${text}`;
}

/** A value's size in the series' unit, unsigned. */
function magnitude(s: Series, a: number): string {
  const m = money(s);
  if (s.unit === "%") return `${a.toFixed(s.decimals)}%`;
  if (!m && CURRENCY.test(s.unit)) return `${CURRENCY_PREFIX[s.unit] ?? `${s.unit} `}${a.toFixed(s.decimals)}`;
  if (!m) return a.toLocaleString("en-US", { minimumFractionDigits: s.decimals, maximumFractionDigits: s.decimals });
  const amount = a * m.scale;
  if (amount < 1e3) return `${m.prefix}${amount.toFixed(0)}`;
  const [div, suffix] = amount >= 1e12 ? [1e12, "T"] : amount >= 1e9 ? [1e9, "B"] : amount >= 1e6 ? [1e6, "M"] : [1e3, "K"];
  return `${m.prefix}${(amount / div).toFixed(1)}${suffix}`;
}

/**
 * A move from one value to another in the series' unit: points for series in % ("+0.3 pt"),
 * else the difference and the percent ("+$2.2B (+3.0%)", "+0.0017 (+0.5%)").
 */
export function seriesChange(s: Series, from: number, to: number): string {
  const diff = to - from;
  if (s.unit === "%") {
    const size = Math.abs(diff).toFixed(s.decimals);
    return signed(Number(size) === 0 ? 0 : diff, `${size} pt`);
  }
  const amount = signed(diff, magnitude(s, Math.abs(diff)));
  if (!from) return amount;
  const pct = (diff / Math.abs(from)) * 100;
  const size = Math.abs(pct).toFixed(1);
  return `${amount} (${signed(Number(size) === 0 ? 0 : pct, `${size}%`)})`;
}

/**
 * An axis tick in the series' unit: money as "$180.0B", other values as bare numbers ("3.50"),
 * signed about a peg ("+0.50").
 */
export function seriesTick(s: Series, v: number, decimals: number): string {
  if (money(s)) return seriesValue(s, v);
  const text = Math.abs(v).toFixed(decimals);
  if (Number(text) === 0) return text;
  return s.baseline !== null ? signed(v, text) : `${v < 0 ? MINUS : ""}${text}`;
}

/** "Jun 2026 was 69.8%, revised 12 Sep" per revision, for a Revised label's tooltip. */
export const revisionsTitle = (s: Series, revisions: Schema<"Revision">[]) =>
  revisions.map((r) => `${seriesPeriod(s, r.period)} was ${seriesValue(s, r.old_value)}, revised ${shortDate(r.revised_at)}`).join("\n");

/** A period as its series is released: "24 Sep" daily, "Jul 2026" monthly, "Q2 2026" quarterly, "2025" yearly. */
export function seriesPeriod(s: Series, iso: string): string {
  const d = day(iso);
  if (s.frequency === "daily") return shortDate(iso);
  if (s.frequency === "monthly") return `${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
  if (s.frequency === "yearly") return `${d.getFullYear()}`;
  return `Q${Math.floor(d.getMonth() / 3) + 1} ${d.getFullYear()}`;
}

type ChangeWindow = Indicator["changes"][number]["window"];
const RELEASE_WINDOW: Record<Series["frequency"], ChangeWindow> = {
  daily: "1m",
  monthly: "1m",
  quarterly: "3m",
  yearly: "1y",
};

/**
 * The change window an indicator row shows: a month for daily and monthly series, else one release.
 * A year-to-date total compares with the same period a year earlier, so January never reads as a fall.
 */
export function releaseWindow(s: Series): ChangeWindow {
  return s.year_to_date ? "1y" : RELEASE_WINDOW[s.frequency];
}

/** What a release's change is against: "vs Jun", "vs Q1", "vs 2024", or "vs Aug 2025" for a year-to-date total. */
export function versus(s: Series, since: string): string {
  const period = seriesPeriod(s, since);
  return `vs ${s.year_to_date ? period : period.split(" ")[0]}`;
}

/**
 * How a change reads, for its colour: a rise is good and a fall bad, except on a series where a
 * rise is the bad direction (an NPL ratio, loan-to-deposit), which flips them. Null for no change.
 */
export function changeTone(series: Indicator["series"], change: number): ChangeTone | null {
  if (change === 0) return null;
  return change > 0 === (series.bad_direction === "up") ? "bad" : "good";
}

export type ChangeTone = "good" | "bad";

const WINDOW_TEXT: Record<ChangeWindow, string> = { "1w": "1 week", "1m": "1 month", "3m": "3 months", "1y": "1 year" };

/**
 * The change an indicator shows over `window`, by default the one `releaseWindow` picks, with its
 * window: over that span for daily series ("1 month"), against the previous release for monthly,
 * quarterly and yearly ones (`versus`). Series in % change in points ("+0.3 pt"), others in
 * percent ("+2.6%"). Null when the window has no change.
 */
export function indicatorChange(
  ind: Indicator,
  window: ChangeWindow = releaseWindow(ind.series),
): { value: string; window: string; tone: ChangeTone | null } | null {
  const s = ind.series;
  const c = ind.changes.find((x) => x.window === window);
  if (!c || c.change === null || !c.since) return null;
  const size = (ind.change_basis === "points" ? Math.abs(c.change).toFixed(s.decimals) : Math.abs(c.change).toFixed(1));
  // A change that rounds to zero takes no sign.
  const shown = Number(size) === 0 ? 0 : c.change;
  const value = signed(shown, ind.change_basis === "points" ? `${size} pt` : `${size}%`);
  const tone = changeTone(s, shown);
  if (s.frequency === "daily") return { value, window: WINDOW_TEXT[window], tone };
  return { value, window: versus(s, c.since), tone };
}

/** The same day `months` earlier, clamped to its month; a month end maps to a month end, as the API's windows do. */
export function monthsBefore(iso: string, months: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  const t = y * 12 + m - 1 - months;
  const [year, month] = [Math.floor(t / 12), (t % 12) + 1];
  const last = new Date(year, month, 0).getDate();
  const at = d === new Date(y, m, 0).getDate() ? last : Math.min(d, last);
  return `${year}-${String(month).padStart(2, "0")}-${String(at).padStart(2, "0")}`;
}

/** "Sep 2025". */
export const monthYear = (d: Date) => `${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
