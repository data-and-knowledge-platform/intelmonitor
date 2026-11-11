// Top bar: the search field and "Week to <the latest run's date> · updated weekly".
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import type { Schema } from "@/lib/api/client";
import { searchOptions, useLatestRun, useSearch } from "@/lib/api/queries";
import { shortDate, TYPE_LABEL } from "@/lib/format";
import { groupLink, subjectLink } from "@/lib/links";
import { SearchIcon, XIcon } from "@/components/ui/icons";
import { SubjectIcon } from "@/components/ui/SubjectIcon";
import { developmentLink } from "@/components/developments/link";

export function TopBar() {
  const latest = useLatestRun().data;
  return (
    <header className="topbar">
      <Search />
      {latest && (
        <span className="updated" title="The week the latest run covers">
          Week to <span className="num">{shortDate(latest.as_of)}</span> · updated weekly
        </span>
      )}
    </header>
  );
}

/** Rows per result group. */
const GROUP_HITS = 5;

type Results = Schema<"SearchResults">;

/** One row in the results: a subject opens its page, a development or a document the development's popup. */
type Hit =
  | { kind: "subject"; key: string; hit: Schema<"SearchHit"> }
  | { kind: "development"; key: string; development: Schema<"DevelopmentSummary"> }
  | { kind: "document"; key: string; document: Schema<"DocumentHit"> };

const GROUPS: { kind: Hit["kind"]; label: string }[] = [
  { kind: "subject", label: "Entities and topics" },
  { kind: "development", label: "Developments" },
  { kind: "document", label: "Documents" },
];

/** The results as one list in display order: monitors, then developments, then documents. */
function flatten(results: Results | undefined): Hit[] {
  if (!results) return [];
  return [
    ...results.subjects.slice(0, GROUP_HITS).map((hit): Hit => ({ kind: "subject", key: `s-${hit.subject.id}`, hit })),
    ...results.developments
      .slice(0, GROUP_HITS)
      .map((development): Hit => ({ kind: "development", key: `d-${development.id}`, development })),
    ...results.documents.slice(0, GROUP_HITS).map((document): Hit => ({ kind: "document", key: `f-${document.id}`, document })),
  ];
}

/** ⌘K focuses the field; typing lists matching monitors, developments and documents, arrows move,
 *  Enter opens the highlighted one (a monitor's page, or a development's popup), Esc clears. */
function Search() {
  const input = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const client = useQueryClient();
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const search = useSearch(text);
  const blank = !text.trim();
  const hits = blank ? [] : flatten(search.data);

  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        input.current?.focus();
        input.current?.select();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const go = (hit: Hit) => {
    if (hit.kind === "subject") {
      const s = hit.hit.subject;
      void navigate(s.type === "group" ? groupLink(s.id) : subjectLink(s));
    }
    else void navigate(developmentLink(hit.kind === "development" ? hit.development.id : hit.document.development_id));
    setText("");
    setOpen(false);
    // A development's popup takes focus and returns it to the field on close; a page change leaves it.
    if (hit.kind === "subject") input.current?.blur();
  };

  const onKeyDown = async (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!hits.length) return;
      const step = e.key === "ArrowDown" ? 1 : -1;
      setActive((i) => (i + step + hits.length) % hits.length);
    } else if (e.key === "Enter" && !blank) {
      e.preventDefault();
      // Shown results may be for an earlier keystroke; then Enter takes the best current match.
      const stale = search.isPlaceholderData || !search.data;
      const hit = stale ? flatten(await client.fetchQuery(searchOptions(text)))[0] : hits[active];
      if (hit) go(hit);
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      if (text) setText("");
      else input.current?.blur();
    }
  };

  const showList = open && !blank && (hits.length > 0 || search.isFetched);

  return (
    <div className="search-wrap">
      <label className="field search">
        <SearchIcon />
        <input
          ref={input}
          type="search"
          role="combobox"
          aria-label="Search"
          aria-expanded={showList}
          aria-controls="search-results"
          aria-autocomplete="list"
          aria-activedescendant={showList && hits[active] ? `search-hit-${hits[active].key}` : undefined}
          placeholder="Search entities, topics and developments"
          autoComplete="off"
          spellCheck={false}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setActive(0);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setOpen(false)}
          onKeyDown={(e) => void onKeyDown(e)}
        />
        {text ? (
          <button
            type="button"
            className="btn-icon clear"
            aria-label="Clear search"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => {
              setText("");
              input.current?.focus();
            }}
          >
            <XIcon />
          </button>
        ) : (
          <span className="kbd" aria-hidden="true">
            ⌘K
          </span>
        )}
      </label>
      {showList && (
        <div id="search-results" role="listbox" aria-label="Search results" className="search-list">
          {GROUPS.map((g) => {
            const rows = hits.filter((h) => h.kind === g.kind);
            return (
              rows.length > 0 && (
                <div key={g.kind} role="group" aria-labelledby={`search-group-${g.kind}`}>
                  <div id={`search-group-${g.kind}`} className="pop-group">
                    {g.label}
                  </div>
                  {rows.map((h) => {
                    const i = hits.indexOf(h);
                    return (
                      <div
                        key={h.key}
                        id={`search-hit-${h.key}`}
                        role="option"
                        aria-selected={i === active}
                        className={`search-hit ${h.kind}`}
                        onMouseDown={(e) => e.preventDefault()}
                        onMouseEnter={() => setActive(i)}
                        onClick={() => go(h)}
                      >
                        <HitRow hit={h} />
                      </div>
                    );
                  })}
                </div>
              )
            );
          })}
          {hits.length === 0 && <p className="empty">Nothing matches “{text.trim()}”.</p>}
        </div>
      )}
    </div>
  );
}

function HitRow({ hit }: { hit: Hit }) {
  switch (hit.kind) {
    case "subject": {
      const { subject, matched } = hit.hit;
      return (
        <>
          <SubjectIcon subject={subject} />
          <b>{subject.name}</b>
          {matched !== subject.name && <span className="alias">{matched}</span>}
          <span className="type">{TYPE_LABEL[subject.type]}</span>
        </>
      );
    }
    case "development":
      return (
        <>
          <b title={hit.development.title}>{hit.development.headline}</b>
          <span className="type">{shortDate(hit.development.last_published)}</span>
        </>
      );
    case "document":
      return (
        <>
          <b title={hit.document.title}>{hit.document.title}</b>
          <span className="alias">{hit.document.snippet}</span>
          <span className="type">{hit.document.domain}</span>
        </>
      );
  }
}
