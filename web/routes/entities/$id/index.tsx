import { createFileRoute } from "@tanstack/react-router";

import { MonitorView } from "@/components/monitor/MonitorView";
import { monitorPageOptions } from "@/lib/api/queries";

// An entity's Overview, or a group's member list.
export const Route = createFileRoute("/entities/$id/")({
  loader: ({ context, params }) => void context.queryClient.prefetchQuery(monitorPageOptions(params.id)),
  component: OverviewPage,
});

function OverviewPage() {
  const { id } = Route.useParams();
  return <MonitorView key={id} id={id} />;
}
