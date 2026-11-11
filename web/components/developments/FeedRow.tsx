// One development in a feed: the status column (priority, then New or Updated from the latest run,
// and Unconfirmed when the feed lists unconfirmed ones), the headline with a visible "View sources"
// cue under it, and the date. Who it impacts, the sources and the key points are in the popup. The
// whole row opens the development's popup.
import { Link } from "@tanstack/react-router";

import type { Schema } from "@/lib/api/client";
import { shortDate } from "@/lib/format";
import { ChevronIcon } from "@/components/ui/icons";

import { ChangeMark, dateSpan, PriorityBadge, UnconfirmedMark } from "./labels";
import { developmentLink, type ViewScope } from "./link";

/** The column names above a list of FeedRows, on the same grid; the rows carry their own labels, so screen readers skip it. */
export function FeedHeader() {
  return (
    <div className="frow-head" aria-hidden="true">
      <span>Priority</span>
      <span>Finding</span>
      <span>Date</span>
    </div>
  );
}

export function FeedRow({ development: d, scope }: { development: Schema<"DevelopmentSummary">; scope: ViewScope }) {
  return (
    <li className="frow">
      <span className="fstat">
        <PriorityBadge priority={d.priority} />
        <ChangeMark change={d.change} />
        {d.unconfirmed && <UnconfirmedMark />}
      </span>
      <span className="fmain">
        <Link {...developmentLink(d.id, scope)} className="ftitle" title={d.title}>
          {d.headline}
        </Link>
        <span className="fopen" aria-hidden="true">
          View sources
          <ChevronIcon />
        </span>
      </span>
      <span className="fend">
        <span className="num">{dateSpan(d.first_published, d.last_published, shortDate)}</span>
      </span>
    </li>
  );
}
