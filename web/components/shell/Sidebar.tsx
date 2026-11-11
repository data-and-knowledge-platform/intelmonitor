// Sidebar: brand, Entities with their groups as headings, and Topics. A group holding the current
// page opens itself, so the current page is always marked.
import { Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import type { Schema } from "@/lib/api/client";
import { useSubjects } from "@/lib/api/queries";
import { monitorLink } from "@/lib/links";
import { usePathname } from "@/lib/router";
import { ChevronIcon } from "@/components/ui/icons";
import { SubjectIcon } from "@/components/ui/SubjectIcon";

type Subject = Schema<"SubjectSummary">;

// The sidebar sections a viewer can collapse; which are closed is remembered in this browser only.
type NavSection = "entities" | "topics";
const CLOSED_KEY = "im.sidebar.closed";

function readClosed(): Set<NavSection> {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(CLOSED_KEY) ?? "[]");
    return new Set(Array.isArray(saved) ? saved.filter((v): v is NavSection => v === "entities" || v === "topics") : []);
  } catch {
    return new Set();
  }
}

function saveClosed(closed: Set<NavSection>) {
  try {
    localStorage.setItem(CLOSED_KEY, JSON.stringify([...closed]));
  } catch {
    // Storage can be blocked; the sections then just open on each load.
  }
}

/** A section heading that opens and closes the list under it. */
function SectionToggle({ id, label, open, onToggle }: { id: string; label: string; open: boolean; onToggle: () => void }) {
  return (
    <button type="button" className="navhead navhead-btn" aria-expanded={open} aria-controls={id} onClick={onToggle}>
      <span>{label}</span>
      <ChevronIcon />
    </button>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  const subjects = useSubjects();
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [closed, setClosed] = useState<Set<NavSection>>(readClosed);
  useEffect(() => saveClosed(closed), [closed]);
  const [openedFor, setOpenedFor] = useState<string | null>(null);

  const all = subjects.data ?? [];
  const byId = new Map(all.map((s) => [s.id, s]));
  const groups = all.filter((s) => s.type === "group" && s.sidebar);
  const topics = all.filter((s) => s.type === "topic");

  // On arriving at an entity's page (any tab), open its section and group once; on a topic's page,
  // open Topics. The user can still collapse them.
  const currentId = pathname.match(/^\/monitors\/([^/]+)/)?.[1];
  const currentGroup = groups.find((g) => currentId && g.members.includes(currentId))?.id;
  const currentSection: NavSection | null = currentGroup ? "entities" : topics.some((t) => t.id === currentId) ? "topics" : null;
  if (currentSection && openedFor !== pathname) {
    setOpenedFor(pathname);
    if (currentGroup) setExpanded((prev) => new Set(prev).add(currentGroup));
    if (closed.has(currentSection))
      setClosed((prev) => {
        const next = new Set(prev);
        next.delete(currentSection);
        return next;
      });
  }

  const toggleSection = (section: NavSection) =>
    setClosed((prev) => {
      const next = new Set(prev);
      if (next.has(section)) next.delete(section);
      else next.add(section);
      return next;
    });

  const toggle = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const row = (s: Subject, sub: boolean) => (
    <Link key={s.id} {...monitorLink(s.id)} className={`navitem${sub ? " sub" : ""}`}>
      <SubjectIcon subject={s} />
      <span>{s.name}</span>
    </Link>
  );

  return (
    <nav className="side" aria-label="Main">
      <Link to="/" activeOptions={{ exact: true }} className="brand">
        IntelMonitor
      </Link>
      <div className="side-scroll">
        <SectionToggle id="nav-entities" label="Entities" open={!closed.has("entities")} onToggle={() => toggleSection("entities")} />
        <div id="nav-entities" className="navgroup" hidden={closed.has("entities")}>
          {groups.map((g) => {
            const open = expanded.has(g.id);
            return (
              <div key={g.id} className="navgroup">
                <button
                  type="button"
                  className="navitem"
                  aria-expanded={open}
                  aria-controls={`nav-${g.id}`}
                  onClick={() => toggle(g.id)}
                >
                  <ChevronIcon />
                  <span>{g.name}</span>
                  <span className="count">{g.members.length}</span>
                </button>
                <div id={`nav-${g.id}`} className="navgroup" hidden={!open}>
                  {g.members.flatMap((m) => byId.get(m) ?? []).map((s) => row(s, true))}
                </div>
              </div>
            );
          })}
        </div>
        <SectionToggle id="nav-topics" label="Topics" open={!closed.has("topics")} onToggle={() => toggleSection("topics")} />
        <div id="nav-topics" className="navgroup" hidden={closed.has("topics")}>
          {topics.map((s) => row(s, false))}
        </div>
      </div>
    </nav>
  );
}
