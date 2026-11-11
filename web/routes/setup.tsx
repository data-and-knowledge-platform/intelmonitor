import { createFileRoute, redirect } from "@tanstack/react-router";

// Onboarding is gone; old links land on the home page.
export const Route = createFileRoute("/setup")({
  beforeLoad: () => {
    throw redirect({ to: "/", replace: true });
  },
});
