// The Entities and Topics list pages (/entities, /topics), from Mock C: a facet column (search,
// priority, group or function, period) beside a table banded High, Medium and Low by each row's top
// priority. The server cuts the period and ranks the rows (GET /lists/{list}); search, the filters
// and the facet counts run here over the loaded rows. Period and filters live in the URL (search.ts).
// Each row's bookmark adds it to or removes it from the Personal watchlist, whatever the view.
import { Link, useLocation, useNavigate } from "@tanstack/react-router";
import { Bookmark } from "lucide-react";
import { useEffect, useRef } from "react";

import type { Schema } from "@/lib/api/client";
import { useFunctions, useSubjectList, type ListName } from "@/lib/api/queries";
import { addDays, shortDate } from "@/lib/format";
import { subjectLink } from "@/lib/links";
import { PERSONAL, usePersonalWatchlist, useView } from "@/lib/viewerState";
import { developmentLink } from "@/components/developments/link";
import { dateSpan, PRIORITIES } from "@/components/developments/labels";

import { PERIODS, type ListSearch } from "./search";

type Row = Schema<"ListRow">;
type Priority = (typeof PRIORITIES)[number];
type Option = { id: string; name: string };

// The filter a facet count leaves out: each facet counts rows matching every other filter.
type Skip = "priority" | "facet" | null;

export function ListPage({ list, search }: { list: ListName; search: ListSearch }) {
  const navigate = useNavigate();
  const entities = list === "entities";
  const period = search.period ?? "week";
  const data = useSubjectList(list, period);
  const functions = useFunctions().data;
  const personal = usePersonalWatchlist();
  const onLabel = useView().view.id === PERSONAL ? "On watchlist" : "On Personal watchlist";

  // The group filter on Entities, the function filter on Topics.
  const facetKey = entities ? "group" : "function";
  const facetIds = (r: Row) => (entities ? r.groups.map((g) => g.id) : r.function ? [r.function.id] : []);

  const set = (patch: ListSearch, replace = false) =>
    void navigate({ to: ".", search: (prev) => ({ ...prev, ...patch }), replace, resetScroll: false });

  if (data.isError) return <p className="page-message">This list could not be loaded.</p>;
  const res = data.data;
  if (!res) return null;

  // Every group an entity is in, by name; the five functions in seed order.
  const facetOptions: Option[] = entities
    ? [...new Map(res.rows.flatMap((r) => r.groups).map((g) => [g.id, g])).values()].sort((a, b) => a.name.localeCompare(b.name))
    : (functions ?? []);
  // An id in the URL that matches no option filters nothing (kept while the functions load).
  const facetValue = search[facetKey];
  const facetFilter = !entities && !functions ? facetValue : facetOptions.find((o) => o.id === facetValue)?.id;

  const q = search.q?.trim().toLowerCase() ?? "";
  const matches = (r: Row, skip: Skip = null) =>
    (!q || r.name.toLowerCase().includes(q)) &&
    (skip === "priority" || !search.priority || r.top?.priority === search.priority) &&
    (skip === "facet" || !facetFilter || facetIds(r).includes(facetFilter));
  const rows = res.rows.filter((r) => matches(r));

  // Bands keep the server's ranking inside each; rows with nothing in the period come last.
  const bands: { key: Priority | "none"; label: string; rows: Row[] }[] = [
    ...PRIORITIES.map((p) => ({ key: p, label: p, rows: rows.filter((r) => r.top?.priority === p) })),
    { key: "none" as const, label: "No developments", rows: rows.filter((r) => !r.top) },
  ].filter((b) => b.rows.length > 0);

  const total = res.rows.length;
  const span = dateSpan(addDays(res.since, 1), res.run.as_of, shortDate);
  const cols = 9;

  return (
    <>
      <div className="lp-head">
        <h1>{entities ? "Entities" : "Topics"}</h1>
        <span className="lp-count" data-testid="list-count">
          {rows.length === total ? total : `${rows.length} of ${total}`}
        </span>
        <span className="lp-since" title="Developments last published in this period">
          {span}
        </span>
      </div>
      <div className="lp">
        <aside className="lp-facets" aria-label="Filters">
          <SearchField value={search.q ?? ""} onChange={(v) => set({ q: v.trim() ? v : undefined }, true)} />
          <Facet
            title="Priority"
            all={res.rows.filter((r) => matches(r, "priority")).length}
            options={PRIORITIES.map((p) => ({
              id: p,
              name: p,
              count: res.rows.filter((r) => matches(r, "priority") && r.top?.priority === p).length,
              dot: p,
            }))}
            selected={search.priority}
            onSelect={(id) => set({ priority: PRIORITIES.find((p) => p === id) })}
          />
          <Facet
            title={entities ? "Group" : "Function"}
            scroll={entities}
            all={res.rows.filter((r) => matches(r, "facet")).length}
            options={facetOptions.map((o) => ({
              ...o,
              count: res.rows.filter((r) => matches(r, "facet") && facetIds(r).includes(o.id)).length,
            }))}
            selected={facetFilter}
            onSelect={(id) => set({ [facetKey]: id })}
          />
          <div className="lp-facet" role="group" aria-label="Period">
            <h2>Period</h2>
            {PERIODS.map((p) => (
              <button
                key={p.value}
                type="button"
                className="lp-opt"
                aria-pressed={period === p.value}
                onClick={() => set({ period: p.value === "week" ? undefined : p.value })}
              >
                <span className="lp-opt-name">{p.label}</span>
              </button>
            ))}
          </div>
        </aside>
        <div className="lp-table-wrap">
          <table className="lp-table" aria-busy={data.isPlaceholderData}>
            <caption className="sr-only">{entities ? "Entities" : "Topics"} by top priority</caption>
            <thead>
              <tr>
                <th scope="col">{entities ? "Entity" : "Topic"}</th>
                <th scope="col">{entities ? "Group" : "Function"}</th>
                <th scope="col" className="r">
                  High
                </th>
                <th scope="col" className="r">
                  <abbr title="Medium">Med</abbr>
                </th>
                <th scope="col" className="r">
                  Low
                </th>
                <th scope="col">Top development</th>
                <th scope="col" className="r">
                  New
                </th>
                <th scope="col" className="r">
                  Latest
                </th>
                <th scope="col" className="lp-watch">
                  <span className="sr-only">Watchlist</span>
                </th>
              </tr>
            </thead>
            {bands.map((b) => (
              <tbody key={b.key} data-band={b.key}>
                <tr className="lp-band">
                  <th colSpan={cols} scope="rowgroup">
                    {b.key !== "none" && <span className={`lp-dot ${b.key}`} aria-hidden="true" />}
                    {b.label} <span className="lp-band-n">{b.rows.length}</span>
                  </th>
                </tr>
                {b.rows.map((r) => (
                  <ListRow
                    key={r.id}
                    row={r}
                    entities={entities}
                    watch={{
                      on: personal.has(r.id),
                      onLabel,
                      toggle: () => (personal.has(r.id) ? personal.remove(r.id) : personal.add(r.id)),
                    }}
                  />
                ))}
              </tbody>
            ))}
            {bands.length === 0 && (
              <tbody>
                <tr>
                  <td colSpan={cols} className="lp-empty">
                    No matches
                  </td>
                </tr>
              </tbody>
            )}
          </table>
        </div>
      </div>
    </>
  );
}

/** A row's bookmark: whether the subject is on the Personal watchlist, and how it reads when on. */
type Watch = { on: boolean; onLabel: string; toggle: () => void };

/** One row: name, first group or function, the period's counts, the top development (its popup
 *  shows the row's priority for it) with its topic on Entities, New, the latest date and the
 *  bookmark. */
function ListRow({ row: r, entities, watch }: { row: Row; entities: boolean; watch: Watch }) {
  return (
    <tr data-id={r.id}>
      <td className="lp-name">
        <Link {...subjectLink(r)}>{r.name}</Link>
      </td>
      <td className="lp-group">{entities ? r.groups[0]?.name : r.function?.name}</td>
      <td className={`lp-n${r.counts.high ? " hi" : ""}`}>{r.counts.high}</td>
      <td className={`lp-n${r.counts.medium ? " md" : ""}`}>{r.counts.medium}</td>
      <td className="lp-n">{r.counts.low}</td>
      <td className="lp-dev">
        {r.top && (
          <>
            <Link {...developmentLink(r.top.id, { subject: r.id })} className="lp-headline">
              {r.top.headline}
            </Link>
            {entities && r.top.topic && <span className="lp-topic">{r.top.topic.name}</span>}
          </>
        )}
      </td>
      <td className={`lp-n lp-new${r.new ? " on" : ""}`}>{r.new}</td>
      <td className="lp-n lp-latest">{r.latest ? shortDate(r.latest) : ""}</td>
      <td className="lp-watch">
        <button
          type="button"
          className={`btn-icon${watch.on ? " on" : ""}`}
          title={watch.on ? watch.onLabel : "Add to watchlist"}
          aria-label={`${watch.on ? "Remove" : "Add"} ${r.name} ${watch.on ? "from" : "to"} watchlist`}
          aria-pressed={watch.on}
          onClick={watch.toggle}
        >
          <Bookmark size={16} fill={watch.on ? "currentColor" : "none"} aria-hidden="true" />
        </button>
      </td>
    </tr>
  );
}

/** A facet: Any, then each option with how many rows it would leave; the chosen one is pressed. */
function Facet({
  title,
  all,
  options,
  selected,
  onSelect,
  scroll = false,
}: {
  title: string;
  all: number;
  options: (Option & { count: number; dot?: Priority })[];
  selected: string | undefined;
  onSelect: (id: string | undefined) => void;
  scroll?: boolean;
}) {
  return (
    <div className="lp-facet" role="group" aria-label={title}>
      <h2>{title}</h2>
      <div className={scroll ? "lp-opts scroll" : "lp-opts"}>
        <button type="button" className="lp-opt" aria-pressed={!selected} onClick={() => onSelect(undefined)}>
          <span className="lp-opt-name">Any</span>
          <span className="lp-opt-n">{all}</span>
        </button>
        {options.map((o) => (
          <button
            key={o.id}
            type="button"
            className="lp-opt"
            aria-pressed={selected === o.id}
            onClick={() => onSelect(selected === o.id ? undefined : o.id)}
          >
            {o.dot && <span className={`lp-dot ${o.dot}`} aria-hidden="true" />}
            <span className="lp-opt-name" title={o.name}>
              {o.name}
            </span>
            <span className="lp-opt-n">{o.count}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * The search field. It is uncontrolled so typing never waits on the URL. It remembers the values it
 * has sent to the URL: when one comes back it is skipped, so a late echo never overwrites newer
 * typing, and any other value (Back, Forward, a link) is written into it, focused or not. The effect
 * runs on every navigation, so echoes the router folded into one render are cleared too.
 */
function SearchField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const sent = useRef<string[]>([]);
  const at = useLocation({ select: (l) => l.state.__TSR_key });
  useEffect(() => {
    const el = input.current;
    if (!el) return;
    const echo = sent.current.indexOf(value);
    if (echo >= 0) {
      sent.current = sent.current.slice(echo + 1);
      return;
    }
    sent.current = [];
    if (el.value !== value) el.value = value;
  }, [value, at]);
  return (
    <label className="lp-search">
      <span className="sr-only">Search</span>
      <input
        ref={input}
        className="input"
        type="search"
        placeholder="Search"
        defaultValue={value}
        onChange={(e) => {
          const v = e.target.value;
          // The URL drops a blank search, so it comes back as "".
          sent.current.push(v.trim() ? v : "");
          onChange(v);
        }}
      />
    </label>
  );
}
