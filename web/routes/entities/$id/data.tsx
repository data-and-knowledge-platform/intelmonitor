import { createFileRoute } from "@tanstack/react-router";

import { DataView } from "@/components/monitor/TrendView";
import { monitorPageOptions, trendOptions } from "@/lib/api/queries";

// An entity's Data tab: its indicators, then its series over the 12 months to the latest run. The
// page loads beside the trend, as the indicators wait for its run date.
export const Route = createFileRoute("/entities/$id/data")({
  loader: ({ context: { queryClient }, params }) => {
    void queryClient.prefetchQuery(monitorPageOptions(params.id));
    void queryClient.prefetchQuery(trendOptions(params.id));
  },
  component: DataPage,
});

function DataPage() {
  const { id } = Route.useParams();
  return <DataView key={id} id={id} />;
}
