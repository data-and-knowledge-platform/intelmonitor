import { createFileRoute } from "@tanstack/react-router";

import { RelatedTab } from "@/components/monitor/Related";

// A topic's Related tab. Topics rarely have stored links, and a topic with nothing related lands
// on its Overview from the tab itself.
export const Route = createFileRoute("/topics/$id/related")({
  component: RelatedPage,
});

function RelatedPage() {
  const { id } = Route.useParams();
  return <RelatedTab key={id} id={id} />;
}
