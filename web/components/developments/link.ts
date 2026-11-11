// Links that open a development in the popup over the current page (DevelopmentPopup.tsx). The
// popup lives in the URL, so a shared link and browser Back work: `?development=<id>` names it and
// `&scope=subject:<id>` the subject in view, whose priority it shows. Without a scope a link takes
// the current page's subject.
import { linkOptions } from "@tanstack/react-router";

export type ViewScope = { subject?: string };

/** The subject in view on a page, read from its path (`window.location` by default). */
export function scopeHere({ pathname }: { pathname: string } = window.location): ViewScope {
  const monitor = pathname.match(/^\/monitors\/([^/]+)/);
  return monitor ? { subject: decodeURIComponent(monitor[1]) } : {};
}

/** The `scope` parameter for a scope: "subject:<id>", or nothing. */
export const scopeParam = (s: ViewScope) => (s.subject ? `subject:${s.subject}` : undefined);

/** The scope a `scope` parameter names, or undefined when it names none. */
export function parseScope(param: string | undefined): ViewScope | undefined {
  const [kind, ...rest] = param?.split(":") ?? [];
  const id = rest.join(":");
  return kind === "subject" && id ? { subject: id } : undefined;
}

/**
 * Link options that open a development's popup over the current page, keeping its filters and
 * scroll; spread into a Link or pass to navigate. The history entry is marked so closing the popup
 * goes back to the entry underneath instead of adding one.
 */
export const developmentLink = (id: string, scope: ViewScope = scopeHere()) =>
  linkOptions({
    to: ".",
    search: (prev) => ({ ...prev, evidence: undefined, development: id, scope: scopeParam(scope) }),
    state: { developmentOpened: true },
    resetScroll: false,
  });

/** The current page with no development open. */
export const closedDevelopmentLink = () =>
  linkOptions({
    to: ".",
    search: (prev) => ({ ...prev, development: undefined, scope: undefined }),
    resetScroll: false,
  });
