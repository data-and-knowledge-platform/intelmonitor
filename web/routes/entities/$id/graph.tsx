import { createFileRoute } from "@tanstack/react-router";

import { GraphView } from "@/components/graph/GraphView";

// The full graph view around an entity, reachable by URL only, for the TCRD session (prd.md section 12).
export const Route = createFileRoute("/entities/$id/graph")({
  component: GraphPage,
});

function GraphPage() {
  const { id } = Route.useParams();
  return <GraphView key={id} id={id} />;
}
