import { createFileRoute, redirect } from "@tanstack/react-router";

// The Ask page is gone; old links land on the home page.
export const Route = createFileRoute("/ask")({
  beforeLoad: () => {
    throw redirect({ to: "/", replace: true });
  },
});
