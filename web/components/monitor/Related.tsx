// An entity's or topic's Related tab: what reaches the subject through its links ("Could affect").
// The tab shows only when the page has such rows.
import { Navigate } from "@tanstack/react-router";

import type { Schema } from "@/lib/api/client";
import { useMonitorPage } from "@/lib/api/queries";
import { useHashScroll } from "@/lib/dom";
import { subjectLink } from "@/lib/links";

import { MonitorHead } from "./MonitorView";
import { CouldAffect } from "./overview/CouldAffect";

type Page = Schema<"MonitorPage">;

/** Whether a page has a Related tab: a development reaches its subject through a link. */
export const hasRelated = (p: Page) => p.impacts.length > 0;

export function RelatedTab({ id }: { id: string }) {
  const page = useMonitorPage(id);
  useHashScroll();
  const p = page.data;
  if (page.isError) return <p className="page-message">This page could not be loaded.</p>;
  if (!p) return null;
  return <Related page={p} />;
}

function Related({ page: p }: { page: Page }) {
  // Nothing reaches the subject: an old or typed URL lands on the Overview.
  if (!hasRelated(p)) return <Navigate {...subjectLink(p.subject)} replace />;
  return (
    <>
      <MonitorHead page={p} view="related" />
      <div className="ov">
        <div className="panel ov-card">
          <CouldAffect subject={p.subject} impacts={p.impacts} />
        </div>
      </div>
    </>
  );
}
