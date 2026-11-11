// A list page's URL query (`/entities?period=4w&group=banking&priority=High&q=dbs`, `/topics?function=...`),
// so reload, Back and shared links keep the period and filters. This week is the default and stays
// out of the URL.
import type { Schema } from "@/lib/api/client";
import type { ListName, ListPeriod } from "@/lib/api/queries";
import { PRIORITIES } from "@/components/developments/labels";

type Priority = Schema<"TopDevelopment">["priority"];

export type ListSearch = {
  period?: ListPeriod;
  /** Rows whose top development has this priority. */
  priority?: Priority;
  /** Entities: rows in this group, any of an entity's groups. */
  group?: string;
  /** Topics: rows owned by this function. */
  function?: string;
  /** Rows whose name contains this text, ignoring case. */
  q?: string;
};

/** The period options in the order the facet lists them. */
export const PERIODS: { value: ListPeriod; label: string }[] = [
  { value: "week", label: "This week" },
  { value: "4w", label: "Last 4 weeks" },
  { value: "3m", label: "Last 3 months" },
];

const text = (v: unknown) => (typeof v === "string" && v ? v : undefined);

/** A list route's validateSearch: unknown or empty values drop out, and so do This week and the
 *  other list's filter (group on Topics, function on Entities). */
export const listSearch =
  (list: ListName) =>
  (search: Record<string, unknown>): ListSearch => ({
    period: PERIODS.find((p) => p.value !== "week" && p.value === search.period)?.value,
    priority: PRIORITIES.find((p) => p === search.priority),
    ...(list === "entities" ? { group: text(search.group) } : { function: text(search.function) }),
    q: text(search.q),
  });
