import { createFileRoute, redirect } from "@tanstack/react-router";

// Home opens MAS's page, keeping the query so a development popup opened at `/` stays open.
export const Route = createFileRoute("/")({
  beforeLoad: ({ search }) => {
    throw redirect({ to: "/monitors/$id", params: { id: "mas" }, search, replace: true });
  },
});
