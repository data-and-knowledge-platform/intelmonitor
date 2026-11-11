// Links to app pages, built with TanStack Router's typed link options.
import { linkOptions } from "@tanstack/react-router";

/** A subject's page; spread into a Link or pass to navigate. */
export const monitorLink = (subjectId: string) => linkOptions({ to: "/monitors/$id", params: { id: subjectId } });
