import { createFileRoute } from "@tanstack/react-router";

import { fromMonitorUrl } from "@/lib/links";

// The old page URLs, `/monitors/<id>` and its tabs (with the old Trend and Connections), land on
// the same tab of `/entities/<id>` or `/topics/<id>`.
export const Route = createFileRoute("/monitors/$")({
  beforeLoad: ({ context: { queryClient }, params, location }) => fromMonitorUrl(queryClient, params._splat ?? "", location),
});
