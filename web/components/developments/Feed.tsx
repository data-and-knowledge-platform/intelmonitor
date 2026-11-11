// The development feed for some subjects, on a subject page's Developments tab: a period control
// (Today, This week, This month, All time), the facet filters, and the rows under one column header
// in the API's order (High adverse, other High, Medium adverse, other Medium, the rest; newest first
// in each). The period and filters live in the URL, so closing a development popup returns to the
// same list. Unconfirmed ones are left out unless "Show unconfirmed" in the filter bar is on. New
// and Updated mark what the latest run added or updated; the footer names that run's date.
import { useNavigate } from "@tanstack/react-router";
import { useCallback, type ReactNode } from "react";

import { useDevelopments, useLatestRun } from "@/lib/api/queries";
import { filtered, listQuery, PERIODS, periodOf, withoutFilters, withPeriod, type FeedScope } from "@/lib/developments";
import { shortDate } from "@/lib/format";
import { usePathname, useSearchParams } from "@/lib/router";

import { FeedHeader, FeedRow } from "./FeedRow";
import { FilterBar } from "./FilterBar";
import type { ViewScope } from "./link";

export function Feed({
  scope,
  view,
  words = false,
}: {
  scope: FeedScope;
  /** The subjects in view, whose priority each development's popup shows. */
  view: ViewScope;
  words?: boolean;
}) {
  const params = useSearchParams();
  const pathname = usePathname();
  const navigate = useNavigate();
  const list = useDevelopments(listQuery(scope, params));
  const run = useLatestRun().data;
  // Filters replace the history entry, so Back leaves the page rather than undoing a filter.
  const onParams = useCallback(
    (next: URLSearchParams) => {
      void navigate({ href: `${pathname}${next.size ? `?${next}` : ""}`, replace: true, resetScroll: false });
    },
    [navigate, pathname],
  );
  const period = periodOf(params);
  const data = list.data;
  const rows = data?.developments ?? [];
  const count = (value: string) => data?.facets.date.find((v) => v.value === value)?.count;

  return (
    <div className="feed">
      <div className="feed-controls">
        <div className="seg" role="group" aria-label="Period">
          {PERIODS.map((p) => (
            <button key={p.value} type="button" aria-pressed={p.value === period} onClick={() => onParams(withPeriod(params, p.value))}>
              {p.label}
              {count(p.value) !== undefined && <span className="seg-n">{count(p.value)}</span>}
            </button>
          ))}
        </div>
        <FilterBar params={params} facets={data?.facets} unconfirmed={data?.unconfirmed} words={words} onParams={onParams} />
      </div>
      {list.isError ? (
        <p className="mon-empty">These developments could not be loaded.</p>
      ) : !data ? null : rows.length === 0 ? (
        <Empty filteredOut={filtered(params)} period={period} count={count} onParams={onParams} params={params} />
      ) : (
        <>
          <FeedHeader />
          <ol className="frows">
            {rows.map((d) => (
              <FeedRow key={d.id} development={d} scope={view} />
            ))}
          </ol>
        </>
      )}
      {data && run && (
        <div className="feed-foot">
          <span className="meta push">
            New: added in the run of {shortDate(run.as_of)}. Updated: new reports in that run.
          </span>
        </div>
      )}
    </div>
  );
}

/**
 * A link to an app URL that carries a feed query the route does not type: a plain click navigates
 * in the app, a modified click opens a tab.
 */
export function HrefLink({ href, className, children }: { href: string; className: string; children: ReactNode }) {
  const navigate = useNavigate();
  return (
    <a
      href={href}
      className={className}
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
        e.preventDefault();
        void navigate({ href });
      }}
    >
      {children}
    </a>
  );
}

function Empty({
  filteredOut,
  period,
  count,
  params,
  onParams,
}: {
  filteredOut: boolean;
  period: string;
  /** Rows in each period, so the empty state only offers a wider one that has some. */
  count: (period: string) => number | undefined;
  params: URLSearchParams;
  onParams: (next: URLSearchParams) => void;
}) {
  // The period counts follow the filters, so this offers a wider period only when it has matches.
  const at = PERIODS.findIndex((p) => p.value === period);
  const wider = PERIODS.slice(at + 1).find((p) => (count(p.value) ?? 0) > 0);
  const widen = wider && (
    <button type="button" className="link" onClick={() => onParams(withPeriod(params, wider.value))}>
      Show {wider.label.toLowerCase()}
    </button>
  );
  if (filteredOut)
    return (
      <p className="mon-empty">
        {wider ? "No developments match these filters in this period." : "No developments match these filters."} {widen}
        {widen && " · "}
        <button type="button" className="link" onClick={() => onParams(withoutFilters(params))}>
          Clear filters
        </button>
      </p>
    );
  return (
    <p className="mon-empty">
      {wider ? "No developments in this period." : "No developments yet."} {widen}
    </p>
  );
}
