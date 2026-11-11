import { createFileRoute } from "@tanstack/react-router";

import { RelatedTab } from "@/components/monitor/Related";

// An entity's Related tab: Could affect, linked institutions and Shared with. An entity with
// nothing related lands on its Overview from the tab itself.
export const Route = createFileRoute("/entities/$id/related")({
  component: RelatedPage,
});

function RelatedPage() {
  const { id } = Route.useParams();
  return <RelatedTab key={id} id={id} />;
}
