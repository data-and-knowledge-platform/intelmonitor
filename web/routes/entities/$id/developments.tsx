import { createFileRoute } from "@tanstack/react-router";

import { DevelopmentsTab } from "@/components/monitor/SubjectDevelopments";

// An entity's Developments tab: its full feed, the period and filters in the URL.
export const Route = createFileRoute("/entities/$id/developments")({
  component: DevelopmentsPage,
});

function DevelopmentsPage() {
  const { id } = Route.useParams();
  return <DevelopmentsTab key={id} id={id} />;
}
