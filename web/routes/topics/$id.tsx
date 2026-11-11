import { createFileRoute } from "@tanstack/react-router";

import { placePage } from "@/lib/links";

// A topic's page and its tabs, which render under this route. An entity's id here goes to
// /entities, and a group's to the Entities list filtered to it.
export const Route = createFileRoute("/topics/$id")({
  beforeLoad: ({ context: { queryClient }, params, location }) => placePage(queryClient, "topics", params.id, location),
});
