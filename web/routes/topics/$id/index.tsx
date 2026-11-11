import { createFileRoute } from "@tanstack/react-router";

import { MonitorView } from "@/components/monitor/MonitorView";
import { monitorPageOptions } from "@/lib/api/queries";

// A topic's Overview.
export const Route = createFileRoute("/topics/$id/")({
  loader: ({ context, params }) => void context.queryClient.prefetchQuery(monitorPageOptions(params.id)),
  component: OverviewPage,
});

function OverviewPage() {
  const { id } = Route.useParams();
  return <MonitorView key={id} id={id} />;
}
