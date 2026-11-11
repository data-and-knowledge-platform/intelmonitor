import { createFileRoute, redirect } from "@tanstack/react-router";

import { scopeParam } from "@/components/developments/link";
import { subjectPageOf } from "@/lib/links";

// The old address of a development's own page, `/developments/<id>?subject=<id>`. It opens the
// development's popup over the subject's page, or over the home page without a subject.
export const Route = createFileRoute("/developments_/$id")({
  validateSearch: (search): { subject?: string } => ({
    subject: typeof search.subject === "string" ? search.subject : undefined,
  }),
  beforeLoad: async ({ context: { queryClient }, params, search: { subject } }) => {
    const popup = { development: params.id, scope: subject ? scopeParam({ subject }) : undefined };
    if (subject) throw redirect({ ...(await subjectPageOf(queryClient, subject)), search: popup, replace: true });
    throw redirect({ to: "/", search: popup, replace: true });
  },
});
