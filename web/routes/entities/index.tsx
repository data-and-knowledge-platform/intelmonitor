import { createFileRoute, stripSearchParams } from "@tanstack/react-router";

import { ListPage } from "@/components/lists/ListPage";
import { listSearch } from "@/components/lists/search";

// The Entities list: every entity ranked by its developments in the period, with the period and
// filters in the URL.
export const Route = createFileRoute("/entities/")({
  validateSearch: listSearch("entities"),
  // Links and navigations here drop the other list's filter.
  search: { middlewares: [stripSearchParams(["function"])] },
  component: EntitiesPage,
});

function EntitiesPage() {
  return <ListPage list="entities" search={Route.useSearch()} />;
}
