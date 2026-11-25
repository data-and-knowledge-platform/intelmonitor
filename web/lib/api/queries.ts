// TanStack Query hooks over the typed API client. Components read API data only through these.
import { keepPreviousData, useQuery, queryOptions } from "@tanstack/react-query";

import { api, type Schema } from "./client";
import type { operations } from "./schema";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(`API request failed with status ${status}`);
  }
}

/** Resolves an openapi-fetch call to its data, throwing ApiError on a non-2xx response. */
async function unwrap<T>(
  request: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await request;
  if (error !== undefined || data === undefined) throw new ApiError(response.status, error);
  return data;
}

export const keys = {
  subjects: ["subjects"],
  subject: (id: string) => ["subject", id],
  functions: ["functions"],
  list: (name: ListName, period: ListPeriod) => ["list", name, period],
  latestRun: ["latest-run"],
  dataSources: ["data-sources"],
  materialityLevels: ["materiality-levels"],
  development: (id: string, scope?: DevelopmentScope) => ["development", id, scope],
  developments: (query: DevelopmentsQuery) => ["developments", query],
  monitorPage: (id: string) => ["monitor-page", id],
  relatedGraph: (id: string, days: RelatedDays) => ["related-graph", id, days],
  trend: (id: string) => ["trend", id],
  indicators: (id: string, asOf?: string) => ["indicators", id, asOf],
  series: (id: string, range: SeriesRange) => ["series", id, range],
  search: (text: string) => ["search", text],
} as const;

/** Query parameters of GET /developments: a scope (some subjects) and filters. */
export type DevelopmentsQuery = NonNullable<operations["list_developments"]["parameters"]["query"]>;

/** The subjects in view in a development popup, for its priority. */
export type DevelopmentScope = NonNullable<operations["get_development"]["parameters"]["query"]>;

/** Query parameters of GET /series/{series_id}: `from` and `to` period dates, both optional. */
export type SeriesRange = NonNullable<operations["get_series"]["parameters"]["query"]>;

/** The Related graph's window in days: 7 (the brief's week), 30 or 90. */
export type RelatedDays = Schema<"RelatedDays">;

export const subjectsOptions = queryOptions({ queryKey: keys.subjects, queryFn: () => unwrap(api.GET("/subjects")) });

export const useSubjects = () => useQuery(subjectsOptions);

/** MAS's five functions in seed order, each with its topics and its view's fixed watchlist. */
export const functionsOptions = queryOptions({ queryKey: keys.functions, queryFn: () => unwrap(api.GET("/functions")) });

export const useFunctions = () => useQuery(functionsOptions);

/** Which list page: GET /lists/entities or /lists/topics. */
export type ListName = operations["get_list"]["parameters"]["path"]["list_name"];

/** A list page's period: this week (since the previous run), the last 4 weeks or 3 months. */
export type ListPeriod = Schema<"SubjectList">["period"];

/**
 * Every entity or every topic with its figures for the period, ranked High, Medium, then name.
 * Keeps the same list's previous period on screen while another period loads.
 */
export const useSubjectList = (name: ListName, period: ListPeriod) =>
  useQuery({
    queryKey: keys.list(name, period),
    queryFn: () =>
      unwrap(api.GET("/lists/{list_name}", { params: { path: { list_name: name }, query: { period } } })),
    placeholderData: (previous, previousQuery) => (previousQuery?.queryKey[1] === name ? previous : undefined),
  });

export const useSubject = (id: string) =>
  useQuery({
    queryKey: keys.subject(id),
    queryFn: () => unwrap(api.GET("/subjects/{subject_id}", { params: { path: { subject_id: id } } })),
  });

/** The latest run, with the date of the latest news it covers for the top bar's "Updated". */
export const useLatestRun = () =>
  useQuery({ queryKey: keys.latestRun, queryFn: () => unwrap(api.GET("/runs/latest")) });

export const useDataSources = () =>
  useQuery({ queryKey: keys.dataSources, queryFn: () => unwrap(api.GET("/data-sources")) });

/** Materiality levels for Help, most material first, with the latest run's counts. */
export const useMaterialityLevels = () =>
  useQuery({ queryKey: keys.materialityLevels, queryFn: () => unwrap(api.GET("/materiality-levels")) });

/** One development with its sources and who it impacts; its priority is for the subjects in `scope`. */
export const useDevelopment = (id: string | null, scope?: DevelopmentScope) =>
  useQuery({
    queryKey: keys.development(id ?? "", scope),
    queryFn: () =>
      unwrap(
        api.GET("/developments/{development_id}", {
          params: { path: { development_id: id ?? "" }, query: scope },
        }),
      ),
    enabled: id !== null,
  });

/**
 * The latest run's developments in display order for a scope, narrowed by facet filters, with each
 * facet's counts. Keeps the previous list while a changed filter refetches.
 */
export const useDevelopments = (query: DevelopmentsQuery, enabled = true) =>
  useQuery({
    queryKey: keys.developments(query),
    queryFn: () => unwrap(api.GET("/developments", { params: { query } })),
    placeholderData: keepPreviousData,
    enabled,
  });

export const monitorPageOptions = (id: string) =>
  queryOptions({
    queryKey: keys.monitorPage(id),
    queryFn: () =>
      unwrap(api.GET("/subjects/{subject_id}/page", { params: { path: { subject_id: id } } })),
  });

export const useMonitorPage = (id: string) => useQuery(monitorPageOptions(id));

/**
 * Everything linked to a subject in the relationship store, with the developments of the last
 * `days` that reach it through each node. Keeps the previous graph while the window refetches.
 */
export const useRelatedGraph = (id: string, days: RelatedDays) =>
  useQuery({
    queryKey: keys.relatedGraph(id, days),
    queryFn: () =>
      unwrap(
        api.GET("/subjects/{subject_id}/related-graph", {
          params: { path: { subject_id: id }, query: { days } },
        }),
      ),
    placeholderData: keepPreviousData,
  });

/**
 * A subject's Trend view: its series over the 12 months to the latest run with its marked
 * developments, and the weeks of the runs in that span.
 */
export const trendOptions = (id: string) =>
  queryOptions({
    queryKey: keys.trend(id),
    queryFn: () =>
      unwrap(api.GET("/subjects/{subject_id}/trend", { params: { path: { subject_id: id } } })),
  });

export const useTrend = (id: string) => useQuery(trendOptions(id));

/**
 * A subject's measured series, primary then context, each with its latest value, changes over
 * 1w, 1m, 3m and 1y, percentile in its history and state. `asOf` (YYYY-MM-DD) reads only what
 * was available by then; the default is today. `enabled: false` leaves it idle.
 */
export const useIndicators = (id: string, asOf?: string, enabled = true) =>
  useQuery({
    queryKey: keys.indicators(id, asOf),
    enabled,
    queryFn: () =>
      unwrap(
        api.GET("/subjects/{subject_id}/indicators", {
          params: { path: { subject_id: id }, query: { as_of: asOf } },
        }),
      ),
  });

/** One series' observations and revisions, with periods in `range`. */
export const useSeries = (id: string, range: SeriesRange = {}) =>
  useQuery({
    queryKey: keys.series(id, range),
    queryFn: () =>
      unwrap(api.GET("/series/{series_id}", { params: { path: { series_id: id }, query: range } })),
  });

/**
 * Subjects whose name or an alias matches `text`, and the latest run's developments and source
 * documents whose text does, each group best first.
 */
export const searchOptions = (text: string) =>
  queryOptions({
    queryKey: keys.search(text.trim().toLowerCase()),
    queryFn: () => unwrap(api.GET("/search", { params: { query: { q: text.trim() } } })),
    staleTime: Infinity,
  });

/** Live search results while typing; idle for blank text. */
export const useSearch = (text: string) =>
  useQuery({ ...searchOptions(text), enabled: text.trim().length > 0, placeholderData: keepPreviousData });
