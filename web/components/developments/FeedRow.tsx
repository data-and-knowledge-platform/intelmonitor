// One development in a feed as a card, after the team's Citibank entity page draft: priority with
// its definitions button, the category, New or Updated from the latest run and Unconfirmed when the
// feed lists unconfirmed ones; the headline; one line of detail (its first key point, else its full
// title); and the date beside the source count and its leading outlet. The whole card opens the
// development's popup.
import { Link } from "@tanstack/react-router";

import type { Schema } from "@/lib/api/client";
import { capitalize, plural, shortDate } from "@/lib/format";
import { PriorityInfo } from "@/components/shell/SidebarDialogs";

import { ChangeMark, dateSpan, PriorityBadge, UnconfirmedMark } from "./labels";
import { developmentLink, type ViewScope } from "./link";

export function FeedRow({ development: d, scope }: { development: Schema<"DevelopmentSummary">; scope: ViewScope }) {
  const detail = d.points[0] ?? (d.title !== d.headline ? d.title : null);
  const [lead, ...others] = d.outlets;
  return (
    <li className="frow">
      <span className="fstat">
        <span className="dp-prio">
          <PriorityBadge priority={d.priority} />
          <PriorityInfo />
        </span>
        <span className="chip chip-sm">{capitalize(d.category)}</span>
        <ChangeMark change={d.change} />
        {d.unconfirmed && <UnconfirmedMark />}
      </span>
      <Link {...developmentLink(d.id, scope)} className="ftitle" title={d.title}>
        {d.headline}
      </Link>
      {detail && <span className="fdetail">{detail}</span>}
      <span className="fend">
        <span className="num">{dateSpan(d.first_published, d.last_published, shortDate)}</span>
        <span className="fsrc">
          {plural(d.source_count, "source")}
          {lead && (
            <span className="fsrc-chip" title={d.outlets.join(", ")}>
              <span className="fsrc-name">{lead}</span>
              {others.length > 0 && <span>+{others.length}</span>}
            </span>
          )}
        </span>
      </span>
    </li>
  );
}
