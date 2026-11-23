// A development's sources at the foot of the popup, two cards a row: news, official and research
// reports, each with its outlet, title, highlighted excerpts and date, and detections as their
// series, a full row each. An unconfirmed development says above its sources what the source gate
// (api pipeline/corroboration.py) found missing.
import { useId, type ReactNode } from "react";

import type { Schema } from "@/lib/api/client";
import { longDate } from "@/lib/format";
import { Dotted } from "@/components/monitor/parts";
import { SeriesSource } from "@/components/shell/SeriesSource";

import { SOURCE_TYPE_LABEL } from "./labels";

type Source = Schema<"EvidenceSource">;

export function Sources({ sources, title, unconfirmed = false }: { sources: Source[]; title: string; unconfirmed?: boolean }) {
  const id = useId();
  return (
    <section className="dp-src dp-sec" aria-labelledby={`${id}-heading`} id="sources">
      <h3 id={`${id}-heading`}>
        Sources <span className="dp-n">{sources.length}</span>
      </h3>
      <div className="src-grid">
        {unconfirmed && (
          <p className="src-lead">
            <span className="unv">Unconfirmed:</span> not enough independent sources yet.
          </p>
        )}
        {sources.length === 0 && <p className="muted src-lead">No news or official report yet.</p>}
        {sources.map((s) => (
          <SourceBlock key={s.document_id} source={s} title={title} />
        ))}
      </div>
    </section>
  );
}

/** A source's collection and document type: "News", "Official, Media Releases". */
function kindOf(s: Source): string {
  const label = SOURCE_TYPE_LABEL[s.collection];
  return s.doc_type.toLowerCase() === s.collection ? label : `${label}, ${s.doc_type}`;
}

/** One source card with its findings; a detector's document is its series, with the event highlighted. */
function SourceBlock({ source: s, title }: { source: Source; title: string }) {
  if (s.series) {
    const sentence = s.findings.find((f) => f.statement !== title)?.statement ?? null;
    return (
      <div className="src-card src-series">
        <SeriesSource chart={s.series} known={s.published_at} sentence={sentence} />
      </div>
    );
  }
  return (
    <article className="source src-card">
      <p className="src-outlet">{s.domain}</p>
      <a href={s.url} target="_blank" rel="noreferrer">
        {s.title}
      </a>
      {s.findings.map((f) => (
        <div key={f.id} className="finding">
          {f.statement !== title && <p>{f.statement}</p>}
          <blockquote>
            <Highlighted passage={f.passage} />
          </blockquote>
        </div>
      ))}
      <p className="meta">
        <Dotted parts={[publishedText(s.published_at), kindOf(s)]} />
      </p>
    </article>
  );
}

/** Many news sources carry only a date, stored at midnight UTC; those have no time to compare. */
const dateOnly = (iso: string) => /T00:00:00(\.0+)?(Z|\+00:00)$/.test(iso);
const time = new Intl.DateTimeFormat("en-GB", { hour: "numeric", minute: "2-digit", hour12: true });

/** "15 Aug 2026, 5:37 pm" for a timestamp, "15 Aug 2026" for a date-only source. */
const publishedText = (iso: string) => (dateOnly(iso) ? longDate(iso.slice(0, 10)) : `${longDate(iso)}, ${time.format(new Date(iso))}`);

/** A passage with its quote spans wrapped in <mark>, and an ellipsis where the document goes on. */
export function Highlighted({ passage }: { passage: Schema<"Passage"> }) {
  const parts: ReactNode[] = [passage.clipped_start && "…"];
  let at = 0;
  for (const h of [...passage.highlights].sort((a, b) => a.start - b.start)) {
    if (h.start < at) continue;
    parts.push(passage.text.slice(at, h.start));
    parts.push(<mark key={h.start}>{passage.text.slice(h.start, h.end)}</mark>);
    at = h.end;
  }
  parts.push(passage.text.slice(at), passage.clipped_end && "…");
  return <>{parts}</>;
}
