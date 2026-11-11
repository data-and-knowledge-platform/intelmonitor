import { createFileRoute, redirect } from "@tanstack/react-router";

// The cross-subject developments page is gone; old links land on the home page.
export const Route = createFileRoute("/developments")({
  beforeLoad: () => {
    throw redirect({ to: "/", replace: true });
  },
});
