import { createFileRoute, stripSearchParams } from "@tanstack/react-router";

import { ListPage } from "@/components/lists/ListPage";
import { listSearch } from "@/components/lists/search";

// The Topics list: every topic ranked by its developments in the period, with the period and
// filters in the URL.
export const Route = createFileRoute("/topics/")({
  validateSearch: listSearch("topics"),
  // Links and navigations here drop the other list's filter.
  search: { middlewares: [stripSearchParams(["group"])] },
  component: TopicsPage,
});

function TopicsPage() {
  return <ListPage list="topics" search={Route.useSearch()} />;
}
