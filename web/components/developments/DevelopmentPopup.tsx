// A development in a popup over the page it was opened from, after the team's design of 23 Nov 2026.
// The header holds its labels for the subjects in view, the headline and the close button; below, in
// one scroll, the Summary (its key points, or the full title when it has none), Affected entities
// (a card per entity beside the Connection tree) and the Sources.
//
// The popup lives in the URL (`?development=<id>&scope=...`, built by link.ts), so a shared link and
// browser Back work. Close, Esc and a backdrop click go back to the entry it was opened over, or drop
// the parameters when the link was loaded fresh. An old `?evidence=<id>` link opens the popup in its place.
import { useLocation, useNavigate, useRouter, useSearch } from "@tanstack/react-router";
import { useEffect, useRef } from "react";

import type { Schema } from "@/lib/api/client";
import { useDevelopment } from "@/lib/api/queries";
import { capitalize, longDate } from "@/lib/format";
import { Dotted } from "@/components/monitor/parts";
import { PriorityInfo } from "@/components/shell/SidebarDialogs";
import { CloseButton, Modal } from "@/components/ui/Modal";

import { AffectedEntities } from "./Impacts";
import { AdverseTone, dateSpan, names, PriorityBadge, sourceCountText, UnconfirmedMark } from "./labels";
import { closedDevelopmentLink, developmentLink, parseScope, scopeHere, type ViewScope } from "./link";
import { Sources } from "./Sources";

/** Rendered once in the app shell; shows the development the URL names. */
export function DevelopmentPopup() {
  const search = useSearch({ from: "__root__" });
  const location = useLocation();
  const navigate = useNavigate();
  const router = useRouter();
  const id = search.development;
  const scope = parseScope(search.scope) ?? scopeHere(location);
  const opener = useRef<HTMLElement | null>(null);

  const close = () => {
    if (location.state.developmentOpened) router.history.back();
    else void navigate({ ...closedDevelopmentLink(), replace: true });
  };

  // `?evidence=<id>` was the development drawer; it opens the popup now.
  const legacy = search.evidence;
  useEffect(() => {
    if (legacy) void navigate({ ...developmentLink(legacy), state: { developmentOpened: false }, replace: true });
  }, [legacy, navigate]);

  // Focus goes back to what opened the popup. The native dialog would, but its content unmounts
  // first, and a Back that rerenders the list underneath can replace the opener.
  const open = id !== undefined;
  useEffect(() => {
    if (!open) return;
    const was = document.activeElement;
    opener.current = was instanceof HTMLElement && !was.closest("dialog") ? was : null;
    return () => {
      const target = opener.current;
      if (target?.isConnected) requestAnimationFrame(() => target.focus({ preventScroll: true }));
    };
  }, [open]);

  return (
    <Modal open={open} onClose={close} labelledBy="dp-title" label="Development" className="sheet dp-pop">
      {id && <DevelopmentView key={id} id={id} scope={scope} onClose={close} />}
    </Modal>
  );
}

function DevelopmentView({ id, scope, onClose }: { id: string; scope: ViewScope; onClose: () => void }) {
  const query = scope.subject ? { subject_id: [scope.subject] } : undefined;
  const development = useDevelopment(id, query);
  const heading = useRef<HTMLHeadingElement>(null);
  const d = development.data;

  // The title takes focus once loaded, after the dialog has opened.
  const loaded = d !== undefined;
  useEffect(() => {
    if (!loaded) return;
    const frame = requestAnimationFrame(() => heading.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [loaded]);

  return (
    <>
      <div className="dp-top">
        {d ? (
          <Head development={d} scope={scope} headingRef={heading} />
        ) : (
          <p className="dp-head dp-wait">
            {development.isError ? "This development could not be loaded. It may have left the latest update." : "Loading…"}
          </p>
        )}
        <CloseButton onClick={onClose} label="Close development" />
      </div>
      {d && (
        <div className="dp-body">
          <Summary development={d} />
          <AffectedEntities direct={d.direct_subjects} impacts={d.impacts} />
          <Sources sources={d.sources} title={d.title} unconfirmed={d.unconfirmed} />
        </div>
      )}
    </>
  );
}

function Head({
  development: d,
  scope,
  headingRef,
}: {
  development: Schema<"DevelopmentDetail">;
  scope: ViewScope;
  headingRef: React.Ref<HTMLHeadingElement>;
}) {
  // Say whose priority it is when the subjects disagree.
  const differ = new Set(d.direct_subjects.map((s) => s.priority)).size > 1;
  const viewed = d.direct_subjects.find((s) => s.subject.id === scope.subject)?.subject.name;
  const whose = d.priority_for.length > 0 ? names(d.priority_for.map((s) => s.name)) : differ && viewed ? viewed : null;
  return (
    <header className="dp-head">
      <p className="dp-labels">
        <span className="dp-prio">
          <PriorityBadge priority={d.priority} />
          <PriorityInfo />
        </span>
        <span>
          <Dotted
            parts={[
              d.unconfirmed && <UnconfirmedMark key="u" />,
              whose && `For ${whose}`,
              <AdverseTone key="a" adverse={d.adverse} tone={d.direction} />,
              // Brings the sources into view where the columns stack, without a history entry.
              <a key="s" href="#sources" className="dp-jump" onClick={jumpToSources}>
                {sourceCountText(d)}
              </a>,
              capitalize(d.category),
              <span key="d" className="num">
                {dateSpan(d.first_published, d.last_published, longDate)}
              </span>,
            ]}
          />
        </span>
      </p>
      <h2 id="dp-title" tabIndex={-1} ref={headingRef}>
        {d.headline}
      </h2>
    </header>
  );
}

/** The key points, or the full title when none were written and it says more than the headline. */
function Summary({ development: d }: { development: Schema<"DevelopmentDetail"> }) {
  if (d.points.length === 0 && d.headline === d.title) return null;
  return (
    <section className="dp-summary" aria-labelledby="dp-summary">
      <h3 id="dp-summary">Summary</h3>
      {d.points.length > 0 ? (
        <ul className="dp-points">
          {d.points.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      ) : (
        <p className="dp-points">{d.title}</p>
      )}
    </section>
  );
}

function jumpToSources(e: React.MouseEvent<HTMLAnchorElement>) {
  e.preventDefault();
  const sources = document.getElementById("sources");
  sources?.scrollIntoView({ block: "start" });
  sources?.querySelector<HTMLElement>("[role=tab][aria-selected=true]")?.focus({ preventScroll: true });
}
