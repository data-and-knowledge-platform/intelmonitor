import { createFileRoute, redirect } from "@tanstack/react-router";

// Dashboards are gone; old links land on the home page.
export const Route = createFileRoute("/dashboards/$id")({
  beforeLoad: () => {
    throw redirect({ to: "/", replace: true });
  },
});
