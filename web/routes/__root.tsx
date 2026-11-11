import type { QueryClient } from "@tanstack/react-query";
import { createRootRouteWithContext, Outlet } from "@tanstack/react-router";

import { DevelopmentPopup } from "@/components/developments/DevelopmentPopup";
import { Sidebar } from "@/components/shell/Sidebar";
import { TopBar } from "@/components/shell/TopBar";

// The app shell around every page: sidebar (with Help, which `?` opens anywhere, and Data sources),
// top bar and the development popup.
export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  // Every page can carry the development popup's parameters (components/developments/link.ts);
  // `evidence` is the popup's old name.
  validateSearch: (search): { evidence?: string; development?: string; scope?: string } => ({
    ...search,
    evidence: typeof search.evidence === "string" ? search.evidence : undefined,
    development: typeof search.development === "string" ? search.development : undefined,
    scope: typeof search.scope === "string" ? search.scope : undefined,
  }),
  component: Shell,
  notFoundComponent: () => <p className="page-message">This page could not be found.</p>,
});

function Shell() {
  return (
    <div className="app">
      <Sidebar />
      <main className="main" data-scroll-restoration-id="main">
        <TopBar />
        <Outlet />
      </main>
      <DevelopmentPopup />
    </div>
  );
}
