// An entity's or topic's page (/entities/<id> or /topics/<id>): the head with its tabs, Overview
// (here), Developments (SubjectDevelopments.tsx's DevelopmentsTab), Related (Related.tsx) and Data
// (TrendView's DataView), each rendering MonitorHead with its tab. The Overview holds the summary
// band (the week's brief beside the top Key data figures and the next date), then the subject's
// top 5 developments and the Related card, each full width; a subject with no stored links (most
// topics) has no Related card. Groups have no page: their URL lands on the Entities list filtered
// to them (lib/links.ts placePage).
import { Link } from "@tanstack/react-router";
import { Bookmark, BookmarkPlus } from "lucide-react";
import { useEffect, useState } from "react";

import type { Schema } from "@/lib/api/client";
import { usePeers } from "@/lib/api/peers";
import { useIndicators, useMonitorPage, useRelatedGraph } from "@/lib/api/queries";
import { useHashScroll } from "@/lib/dom";
import { SubjectLink } from "@/components/shell/SubjectLink";
import { SubjectIcon } from "@/components/ui/SubjectIcon";
import { subjectLink } from "@/lib/links";
import { PERSONAL, recordView, usePersonalWatchlist, useView } from "@/lib/viewerState";

import { SummaryBand } from "./overview/SummaryBand";
import { useRelated } from "./Related";
import { RelatedGraph } from "./RelatedGraph";
import { TopDevelopments } from "./SubjectDevelopments";

type Page = Schema<"MonitorPage">;
type Subject = Schema<"SubjectDetail">;

export function MonitorView({ id }: { id: string }) {
  const page = useMonitorPage(id);
  useHashScroll();
  const p = page.data;
  if (page.isError) return <p className="page-message">This page could not be loaded.</p>;
  if (!p) return null;
  return <Overview page={p} />;
}

function Overview({ page: p }: { page: Page }) {
  // The card's own first query: every stored link shows in any window, so 7 days tells whether any
  // exist. The card stays out until it answers, so it never shows and then goes.
  const graph = useRelatedGraph(p.subject.id, 7).data;
  const related = useRelated(p);
  return (
    <>
      <MonitorHead page={p} view="overview" />
      <div className="ov">
        <SummaryBand page={p} />
        <div className="ov-grid">
          <div className="panel ov-card">
            <TopDevelopments subject={p.subject} />
          </div>
          {graph && graph.nodes.length > 0 && <RelatedGraph subject={p.subject} relatedTab={related?.show ?? false} />}
        </div>
      </div>
    </>
  );
}

export type MonitorTab = "overview" | "developments" | "related" | "data";

/**
 * The page head: logo, name and one line on what it is, and Add to watchlist, over the tabs.
 * Related shows when something links to the subject, Data when it has listed series. Every tab
 * renders it once its subject has loaded, so it records the page in Recently viewed.
 */
export function MonitorHead({ page: p, view }: { page: Page; view: MonitorTab }) {
  const id = p.subject.id;
  useEffect(() => recordView(id), [id]);
  const indicators = useIndicators(p.subject.id, p.run.as_of);
  const related = useRelated(p);
  const hasData = p.trend || (indicators.data?.indicators.some((i) => i.series.listed) ?? false);
  const current = (tab: MonitorTab) => (view === tab ? ("page" as const) : undefined);
  return (
    <div className="mon-head">
      <div className="mon-head-top">
        <div className="mon-head-main">
          {p.subject.logo_full && (
            // Local logo files need no image optimization; the name below carries the meaning.
            <img className={`mon-logo${p.subject.kind === "country" ? " flag" : ""}`} src={p.subject.logo_full} alt="" />
          )}
          <h1>
            {!p.subject.logo_full && <SubjectIcon subject={p.subject} size={28} />}
            {p.subject.name}
          </h1>
          <p>
            <Summary subject={p.subject} />
          </p>
        </div>
        <WatchButton id={id} />
      </div>
      <nav className="mon-nav" aria-label="Monitor views">
        <Link {...subjectLink(p.subject)} activeOptions={{ exact: true }} aria-current={current("overview")}>
          Overview
        </Link>
        <Link {...subjectLink(p.subject, "developments")} aria-current={current("developments")}>
          Developments
        </Link>
        {(view === "related" || related?.show) && (
          <Link {...subjectLink(p.subject, "related")} aria-current={current("related")}>
            Related
          </Link>
        )}
        {hasData && (
          <Link {...subjectLink(p.subject, "data")} aria-current={current("data")}>
            Data
          </Link>
        )}
      </nav>
    </div>
  );
}

/**
 * Adds the subject to the Personal watchlist, whatever the view. Once on it, the button reads "On
 * watchlist" (in a function view, "On Personal watchlist") and "Remove from watchlist" on hover.
 * After a click the hover swap waits until the pointer has left once, so a second click in the
 * same spot does not undo the first. All three labels share one grid cell, so the button keeps the
 * widest one's width.
 */
function WatchButton({ id }: { id: string }) {
  const personal = usePersonalWatchlist();
  const { view } = useView();
  const [held, setHeld] = useState(false);
  const on = personal.has(id);
  return (
    <button
      type="button"
      className={`btn watch-btn${on ? " on" : ""}${held ? " held" : ""}`}
      aria-pressed={on}
      onClick={() => {
        if (on) personal.remove(id);
        else personal.add(id);
        setHeld(true);
      }}
      onMouseLeave={() => setHeld(false)}
    >
      {on ? <Bookmark size={16} fill="currentColor" aria-hidden="true" /> : <BookmarkPlus size={16} aria-hidden="true" />}
      <span className="watch-labels">
        <span className="watch-add">Add to watchlist</span>
        <span className="watch-on">{view.id === PERSONAL ? "On watchlist" : "On Personal watchlist"}</span>
        <span className="watch-off">Remove from watchlist</span>
      </span>
    </button>
  );
}

const KIND_LABEL: Record<NonNullable<Subject["kind"]>, string> = {
  bank: "Bank",
  central_bank: "Central bank",
  regulator: "Regulator",
  agency: "Agency",
  market_infrastructure: "Market infrastructure",
  payment_system: "Payment system",
  tech_provider: "Technology provider",
  asset_manager: "Asset manager",
  investor: "Investor",
  insurer: "Insurer",
  payment_institution: "Payments and fintech",
  stablecoin_issuer: "Stablecoin issuer",
  digital_asset_firm: "Digital asset firm",
  country: "Country or territory",
  sector: "Sector",
};

// Peers named in the head before "N more".
const PEERS_SHOWN = 4;

/** One line under the title: an entity's kind and its peers as links, or a topic's scope. */
function Summary({ subject: s }: { subject: Subject }) {
  const peers = usePeers(s) ?? [];
  const [all, setAll] = useState(false);
  if (s.type !== "entity") return s.description;
  const shown = all || peers.length <= PEERS_SHOWN + 1 ? peers : peers.slice(0, PEERS_SHOWN);
  return (
    <>
      {s.kind ? KIND_LABEL[s.kind] : "Entity"}
      {peers.length > 0 && " · Peers: "}
      {shown.map((peer, i) => (
        <span key={peer.id}>
          {i > 0 && ", "}
          <SubjectLink subject={peer} />
        </span>
      ))}
      {shown.length < peers.length && (
        <>
          {" and "}
          <button type="button" className="link" onClick={() => setAll(true)}>
            {peers.length - shown.length} more
          </button>
        </>
      )}
    </>
  );
}
