import { createFileRoute } from "@tanstack/react-router";

import { placePage } from "@/lib/links";

// An entity's page and its tabs, which render under this route. A topic's id here goes to
// /topics, and a group's to the Entities list filtered to it.
export const Route = createFileRoute("/entities/$id")({
  beforeLoad: ({ context: { queryClient }, params, location }) => placePage(queryClient, "entities", params.id, location),
});
