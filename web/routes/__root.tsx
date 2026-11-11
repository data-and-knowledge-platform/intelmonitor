import type { QueryClient } from "@tanstack/react-query";
import { createRootRouteWithContext, Outlet } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { DevelopmentPopup } from "@/components/developments/DevelopmentPopup";
import { HelpDialog } from "@/components/shell/ProfileDialogs";
import { Sidebar } from "@/components/shell/Sidebar";
import { TopBar } from "@/components/shell/TopBar";
import { typingIn } from "@/lib/dom";

// The app shell around every page: sidebar, top bar, the development popup and Help, which `?`
// opens anywhere.
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
  const [help, setHelp] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "?" && !typingIn(e.target) && !document.querySelector("dialog[open]")) {
        e.preventDefault();
        setHelp(true);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  return (
    <div className="app">
      <Sidebar />
      <main className="main" data-scroll-restoration-id="main">
        <TopBar />
        <Outlet />
      </main>
      <DevelopmentPopup />
      <HelpDialog open={help} onClose={() => setHelp(false)} />
    </div>
  );
}
