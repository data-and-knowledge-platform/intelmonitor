// Sidebar, from Mock A: brand, Recently viewed (the last 5 pages opened, newest first), the current
// view's Watchlist (entities, a hairline, then topics, each alphabetical), All (links to the Entities
// and Topics lists), and the foot: Help and Data sources links over the view switcher. Personal
// entries show an x on hover that removes them; a function view's fixed watchlist shows a lock on
// its heading and no x. State lives in this browser (lib/viewerState.ts).
import { Link } from "@tanstack/react-router";
import { Bookmark, Briefcase, Building2, Check, ChevronsUpDown, Clock, Layers, Lock, User, X } from "lucide-react";
import { Fragment, useEffect, useRef, useState, type KeyboardEvent } from "react";

import type { Schema } from "@/lib/api/client";
import { useSubjects } from "@/lib/api/queries";
import { typingIn, useDismiss } from "@/lib/dom";
import { subjectLink } from "@/lib/links";
import { PERSONAL, usePersonalWatchlist, useRecentlyViewed, useView, useWatchlist, type ViewId } from "@/lib/viewerState";
import { DataSourcesDialog, HelpDialog } from "@/components/shell/SidebarDialogs";

type Subject = Schema<"SubjectSummary">;

/** A subject's page link, with an x that removes it when `onRemove` is given. */
function Entry({ subject: s, onRemove }: { subject: Subject; onRemove?: () => void }) {
  return (
    <div className={`navrow${onRemove ? " removable" : ""}`}>
      <Link {...subjectLink(s)} className="navitem ind">
        <span>{s.name}</span>
      </Link>
      {onRemove && (
        <button
          type="button"
          className="btn-icon navrm"
          title="Remove from watchlist"
          aria-label={`Remove ${s.name} from watchlist`}
          onClick={onRemove}
        >
          <X size={14} aria-hidden="true" />
        </button>
      )}
    </div>
  );
}

export function Sidebar() {
  const subjects = useSubjects().data ?? [];
  const recent = useRecentlyViewed();
  const watchlist = useWatchlist();
  const personal = usePersonalWatchlist();
  const { entities, topics, editable, view, loaded } = watchlist;
  const remove = (s: Subject) => (editable ? () => personal.remove(s.id) : undefined);

  return (
    <nav className="side" aria-label="Main">
      <Link to="/" activeOptions={{ exact: true }} className="brand">
        IntelMonitor
      </Link>
      <div className="side-scroll">
        <section className="navsec" aria-labelledby="nav-recent">
          <h2 className="navhead" id="nav-recent">
            <Clock size={14} aria-hidden="true" />
            Recently viewed
          </h2>
          {recent?.map((s) => (
            <Entry key={s.id} subject={s} />
          ))}
          {recent?.length === 0 && <p className="navempty">Empty</p>}
        </section>
        <section className="navsec" aria-labelledby="nav-watchlist">
          {/* The label, not a hidden text span, says fixed: an out-of-flow span adds a stray space to the name. */}
          <h2 className="navhead" id="nav-watchlist" aria-label={editable ? undefined : "Watchlist, fixed"}>
            <Bookmark size={14} aria-hidden="true" />
            Watchlist
            {!editable && (
              <span className="navlock" title={view.name ? `Fixed watchlist for ${view.name}` : "Fixed watchlist"}>
                <Lock size={13} aria-hidden="true" />
              </span>
            )}
          </h2>
          {entities.map((s) => (
            <Entry key={s.id} subject={s} onRemove={remove(s)} />
          ))}
          {entities.length > 0 && topics.length > 0 && <hr className="navrule" />}
          {topics.map((s) => (
            <Entry key={s.id} subject={s} onRemove={remove(s)} />
          ))}
          {loaded && entities.length === 0 && topics.length === 0 && <p className="navempty">Empty</p>}
        </section>
        <section className="navsec" aria-labelledby="nav-all">
          <h2 className="navhead" id="nav-all">
            All
          </h2>
          <Link to="/entities" activeOptions={{ exact: true, includeSearch: false }} className="navitem">
            <Building2 size={16} aria-hidden="true" />
            <span>Entities</span>
            <span className="count">{subjects.filter((s) => s.type === "entity").length || ""}</span>
          </Link>
          <Link to="/topics" activeOptions={{ exact: true, includeSearch: false }} className="navitem">
            <Layers size={16} aria-hidden="true" />
            <span>Topics</span>
            <span className="count">{subjects.filter((s) => s.type === "topic").length || ""}</span>
          </Link>
        </section>
      </div>
      <div className="side-foot">
        <FootLinks />
        <ViewSwitcher />
      </div>
    </nav>
  );
}

/** "Help" and "Data sources" as text links above the view switcher, each opening its dialog; `?` opens Help anywhere. */
function FootLinks() {
  const [open, setOpen] = useState<"help" | "sources" | null>(null);
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "?" && !typingIn(e.target) && !document.querySelector("dialog[open]")) {
        e.preventDefault();
        setOpen("help");
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  const close = () => setOpen(null);
  return (
    <div className="side-links">
      <button type="button" className="side-link" onClick={() => setOpen("help")}>
        Help
      </button>
      <button type="button" className="side-link" onClick={() => setOpen("sources")}>
        Data sources
      </button>
      <HelpDialog open={open === "help"} onClose={close} />
      <DataSourcesDialog open={open === "sources"} onClose={close} />
    </div>
  );
}

const ViewIcon = ({ id }: { id: ViewId }) =>
  id === PERSONAL ? <User size={16} aria-hidden="true" /> : <Briefcase size={16} aria-hidden="true" />;

/** The current view at the bottom left; it opens a menu upward of Personal and the five function views. */
function ViewSwitcher() {
  const { view, views, setView } = useView();
  const [open, setOpen] = useState(false);
  const { wrap, trigger } = useDismiss(open, () => setOpen(false));
  const menu = useRef<HTMLDivElement>(null);

  const items = () => [...(menu.current?.querySelectorAll<HTMLButtonElement>("[role=menuitemradio]") ?? [])];
  // Arrow keys, Home and End move between the views; Tab closes the menu.
  const onKey = (e: KeyboardEvent) => {
    const all = items();
    const at = all.findIndex((el) => el === document.activeElement);
    const to = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: all.length - 1 }[e.key];
    if (e.key === "Tab") setOpen(false);
    if (to === undefined) return;
    e.preventDefault();
    all[(to + all.length) % all.length]?.focus();
  };
  // On opening, focus goes to the current view.
  useEffect(() => {
    if (open) menu.current?.querySelector<HTMLButtonElement>("[aria-checked=true]")?.focus();
  }, [open]);

  return (
    <div className="vsw" ref={wrap}>
      {open && (
        <div className="vsw-menu" role="menu" aria-label="Views" ref={menu} onKeyDown={onKey}>
          {views.map((v, i) => (
            <Fragment key={v.id}>
              {i === 1 && <div className="vsw-sep" role="separator" />}
              <button
                type="button"
                role="menuitemradio"
                aria-checked={v.id === view.id}
                tabIndex={-1}
                onClick={() => {
                  setView(v.id);
                  setOpen(false);
                  trigger.current?.focus();
                }}
              >
                <ViewIcon id={v.id} />
                <span>{v.name}</span>
                {v.id === view.id && <Check size={16} className="vsw-check" aria-hidden="true" />}
              </button>
            </Fragment>
          ))}
        </div>
      )}
      <button
        ref={trigger}
        type="button"
        className="vsw-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <ViewIcon id={view.id} />
        <span>
          <span className="sr-only">View: </span>
          {view.name}
        </span>
        <ChevronsUpDown size={14} aria-hidden="true" />
      </button>
    </div>
  );
}
