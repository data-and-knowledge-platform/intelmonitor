// What each stored relationship type reads as in the graph view's Links filter, in the API's
// relationship type order.
import type { Schema } from "@/lib/api/client";

export type RelType = Schema<"RelatedLink">["type"];

/** Whether a graph node opens something: linked parties (endpoints) have no page, so their nodes
 *  take no click and their In view rows are not links. */
export const opensPage = (n: Schema<"GraphNode">) => n.type !== "endpoint";

export const LINK_LABEL: Record<RelType, string> = {
  holds_stake_in: "Holds a stake in",
  regulated_by: "Regulated by",
  operates_in: "Operates in",
  exposed_to: "Exposed to",
  depends_on: "Depends on",
  member_of: "Member of",
  participates_in: "Takes part in",
  partner_of: "Partner of",
  listed_on: "Listed on",
  belongs_to_sector: "Belongs to sector",
};
