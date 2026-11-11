// Links to app pages, built with TanStack Router's typed link options. A topic's page sits under
// /topics and an entity's under /entities. Groups have no page: a group links to the Entities list
// filtered to it.
import type { QueryClient } from "@tanstack/react-query";
import { linkOptions, redirect, type ParsedLocation } from "@tanstack/react-router";

import type { Schema } from "@/lib/api/client";
import { subjectsOptions } from "@/lib/api/queries";

/** What a page link needs of a subject. */
export type PageSubject = Pick<Schema<"SubjectRef">, "id" | "type">;

/** A page's tabs; the graph view is reachable by URL only. */
export type SubjectTab = "overview" | "developments" | "related" | "data" | "graph";

/** The top-level path a subject's page sits under. */
export type PageSection = "entities" | "topics";

const TABS: SubjectTab[] = ["overview", "developments", "related", "data", "graph"];
const isTab = (segment: string): segment is SubjectTab => TABS.some((t) => t === segment);

const PATHS = {
  entities: {
    overview: "/entities/$id",
    developments: "/entities/$id/developments",
    related: "/entities/$id/related",
    data: "/entities/$id/data",
    graph: "/entities/$id/graph",
  },
  topics: {
    overview: "/topics/$id",
    developments: "/topics/$id/developments",
    related: "/topics/$id/related",
    data: "/topics/$id/data",
    graph: "/topics/$id/graph",
  },
} as const;

export const pageSection = (s: PageSubject): PageSection => (s.type === "topic" ? "topics" : "entities");

/** A subject's page, or one of its tabs; spread into a Link or pass to navigate or redirect. */
export const subjectLink = (s: PageSubject, tab: SubjectTab = "overview") =>
  linkOptions({ to: PATHS[pageSection(s)][tab], params: { id: s.id } });

/** The Entities list filtered to a group's members. */
export const groupLink = (id: string) => linkOptions({ to: "/entities", search: { group: id } });

/** The subject a page id names, from the cached subject list; undefined when unknown or unloaded. */
async function findSubject(queryClient: QueryClient, id: string) {
  const subjects = await queryClient.ensureQueryData(subjectsOptions).catch(() => []);
  return subjects.find((s) => s.id === id);
}

/**
 * Sends a page URL where its subject belongs, keeping its query and hash: a topic under /topics,
 * an entity under /entities, and a group to the Entities list filtered to it. An unknown id stays,
 * and its page says it could not be loaded. Runs in each section's `$id` route.
 */
export async function placePage(queryClient: QueryClient, section: PageSection, id: string, location: ParsedLocation) {
  const subject = await findSubject(queryClient, id);
  if (!subject) return;
  if (subject.type === "group") throw redirect({ ...groupLink(subject.id), replace: true });
  if (pageSection(subject) === section) return;
  const segment = location.pathname.split("/")[3] ?? "";
  const tab = isTab(segment) ? segment : "overview";
  throw redirect({ ...subjectLink(subject, tab), search: location.search, hash: location.hash, replace: true });
}

// Tabs of the old `/monitors/<id>/<tab>` URLs that are gone or renamed.
const OLD_TABS = new Map<string, SubjectTab>([
  ["trend", "data"],
  ["connections", "overview"],
]);

/**
 * Sends an old `/monitors/<id>[/<tab>]` URL (`path` is what follows `/monitors/`) to the page
 * that replaced it, keeping its query and hash; `/monitors` alone lands on home.
 */
export async function fromMonitorUrl(queryClient: QueryClient, path: string, location: ParsedLocation): Promise<never> {
  const [id = "", segment = ""] = path.split("/");
  if (!id) throw redirect({ to: "/", replace: true });
  const subject = (await findSubject(queryClient, id)) ?? { id, type: "entity" };
  if (subject.type === "group") throw redirect({ ...groupLink(subject.id), replace: true });
  const tab = isTab(segment) ? segment : (OLD_TABS.get(segment) ?? "overview");
  throw redirect({ ...subjectLink(subject, tab), search: location.search, hash: location.hash, replace: true });
}

/** A subject's page under a development's popup, for the old `/developments/<id>?subject=<id>`. */
export async function subjectPageOf(queryClient: QueryClient, id: string) {
  return subjectLink((await findSubject(queryClient, id)) ?? { id, type: "entity" });
}
