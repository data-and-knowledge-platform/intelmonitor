import { createFileRoute } from "@tanstack/react-router";

import { Home } from "@/components/home/Home";

// Home: the current view's watchlist and its week (components/home/Home.tsx). A development popup
// opened at `/` stays open, as the root route keeps its parameters.
export const Route = createFileRoute("/")({
  component: Home,
});
