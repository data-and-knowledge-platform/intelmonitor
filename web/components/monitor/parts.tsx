// Pieces shared by subject pages and the development popup.
import { Fragment, type ReactNode } from "react";

/** Meta parts joined with a middle dot. Empty parts are dropped. */
export function Dotted({ parts }: { parts: ReactNode[] }) {
  return parts
    .filter((p) => p !== null && p !== undefined && p !== false && p !== "")
    .map((p, i) => (
      <Fragment key={i}>
        {i > 0 && " · "}
        {p}
      </Fragment>
    ));
}

/** A ruled section inside a monitor panel: a 15/600 header with optional meta on the right. */
export function Section({ id, title, meta, children }: { id: string; title: string; meta?: ReactNode; children: ReactNode }) {
  return (
    <section className="mon-sec" aria-labelledby={id}>
      <div className="mon-sec-head">
        <h2 id={id}>{title}</h2>
        {meta && <span className="meta">{meta}</span>}
      </div>
      {children}
    </section>
  );
}
