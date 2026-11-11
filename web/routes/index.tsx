import { createFileRoute, redirect } from "@tanstack/react-router";

import { functionsOptions, subjectsOptions } from "@/lib/api/queries";
import { subjectLink } from "@/lib/links";
import { currentWatchlist, inPersonalView } from "@/lib/viewerState";

// Home opens the alphabetically first entity on the current view's watchlist, else its first topic,
// else the Entities list; a first visit's watchlist is MAS. It keeps the query, so a development
// popup opened at `/` stays open. If the subjects cannot load, it opens MAS's page.
export const Route = createFileRoute("/")({
  beforeLoad: async ({ context: { queryClient }, search }) => {
    const subjects = await queryClient.ensureQueryData(subjectsOptions).catch(() => null);
    if (!subjects) throw redirect({ to: "/entities/$id", params: { id: "mas" }, search, replace: true });
    const functions = inPersonalView() ? [] : await queryClient.ensureQueryData(functionsOptions).catch(() => []);
    const { entities, topics } = currentWatchlist(subjects, functions);
    const first = entities[0] ?? topics[0];
    if (first) throw redirect({ ...subjectLink(first), search, replace: true });
    throw redirect({ to: "/entities", search, replace: true });
  },
});
