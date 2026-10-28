// The labels every development carries, one per question (prd.md section 12): priority (High,
// Medium, Low), the adverse flag with the tone, the change marker (New or Updated), the source
// counts and the Unconfirmed mark. Shared by the feed rows and the development popup.
import type { Schema } from "@/lib/api/client";
import { plural } from "@/lib/format";

type Priority = Schema<"DevelopmentSummary">["priority"];
type Tone = Schema<"DevelopmentSummary">["direction"];
type SourceType = Schema<"SourceTypeCount">;

/** The priorities, highest first: the order feeds sort and group by. */
export const PRIORITIES: Priority[] = ["High", "Medium", "Low"];

const PRIORITY_CLASS: Record<Priority, string> = { High: "b-attn", Medium: "b-watch", Low: "b-neutral" };

const PRIORITY_TITLE: Record<Priority, string> = {
  High: "High: an incident at or strongly linked to a Singapore financial institution",
  Medium: "Medium: a development at or strongly linked to a Singapore financial institution",
  Low: "Low: no effect on Singapore's financial institutions",
};

/** Priority as the one filled badge: High, Medium or Low, with what it means on hover. */
export function PriorityBadge({ priority }: { priority: Priority }) {
  return (
    <span className={`badge ${PRIORITY_CLASS[priority]}`} title={PRIORITY_TITLE[priority]}>
      {priority}
    </span>
  );
}

/**
 * What each priority means, in the words a first-time user reads: under the feed's priority
 * headings and in Help.
 */
export const PRIORITY_MEANING: Record<Priority, string> = {
  High: "Incidents at Singapore financial institutions, or at foreign institutions and third parties strongly linked to Singapore (not through loans alone), and developments serious enough to change a Singapore institution's position. Read these first.",
  Medium: "Other developments at Singapore financial institutions or at institutions strongly linked to Singapore. Market moves are Medium at most.",
  Low: "News with no effect on Singapore's financial institutions, and routine or promotional news.",
};

/** New as a badge; Updated as a plain word in the new color, so it never reads as a priority. */
export function ChangeMark({ change }: { change: Schema<"DevelopmentSummary">["change"] }) {
  if (change === "new") return <span className="badge b-new">New</span>;
  if (change === "updated") return <span className="upd">Updated</span>;
  return null;
}

export const toneWord = (tone: Tone) => `${tone.charAt(0).toUpperCase()}${tone.slice(1)} tone`;

/** "Adverse event · Negative tone", or the tone alone when the news reports no harm to the subject. */
export function AdverseTone({ adverse, tone }: { adverse: boolean; tone: Tone }) {
  return (
    <>
      {adverse && (
        <>
          <span className="adv">Adverse event</span>
          {" · "}
        </>
      )}
      {toneWord(tone)}
    </>
  );
}

export const SOURCE_TYPE_LABEL: Record<SourceType["collection"], string> = {
  news: "News",
  official: "Official",
  dms: "Research",
  data: "Data",
  social: "Social",
};

// Social posts are hidden everywhere for now: posts mostly reshare the news articles already listed.
// The api still stores them and the source gate still counts them.
export const SHOW_SOCIAL = false;

/** A development's sources as shown: social posts left out while SHOW_SOCIAL is off. */
export const shownSources = <S extends Pick<SourceType, "collection">>(sources: S[]): S[] =>
  SHOW_SOCIAL ? sources : sources.filter((s) => s.collection !== "social");

/**
 * "3 outlets · 12 posts · 1 official source": distinct news outlets, social posts, then official,
 * research and data sources, each left out when zero. These are what the source gate counts
 * (api pipeline/corroboration.py). Posts are left out while SHOW_SOCIAL is off.
 */
export function sourceCountText(d: Pick<Schema<"DevelopmentSummary">, "outlet_count" | "social_count" | "source_types">): string {
  const of = (c: SourceType["collection"]) => d.source_types.find((t) => t.collection === c)?.count ?? 0;
  const parts: [number, string][] = [
    [d.outlet_count, "outlet"],
    [SHOW_SOCIAL ? d.social_count : 0, "post"],
    [of("official"), "official source"],
    [of("dms"), "research paper"],
    [of("data"), "data source"],
  ];
  return parts
    .filter(([n]) => n > 0)
    .map(([n, word]) => plural(n, word))
    .join(" · ");
}

/** The mark an unconfirmed development carries when the feed lists it. */
export const UnconfirmedMark = () => <span className="unv">Unconfirmed</span>;

/** "DBS", "DBS and OCBC", "DBS, OCBC and UOB", or the first `max` and "2 more". */
export function names(list: string[], max = 4): string {
  const shown = list.length > max ? [...list.slice(0, max), `${list.length - max} more`] : list;
  return shown.length < 2 ? (shown[0] ?? "") : `${shown.slice(0, -1).join(", ")} and ${shown.at(-1)}`;
}

/** "22 Sep", or "17 to 20 Sep" when it was reported over several days. */
export function dateSpan(first: string, last: string, format: (iso: string) => string): string {
  if (first === last) return format(last);
  const [a, b] = [format(first), format(last)];
  const sameMonth = a.split(" ").slice(1).join(" ") === b.split(" ").slice(1).join(" ");
  return sameMonth ? `${a.split(" ")[0]} to ${b}` : `${a} to ${b}`;
}
