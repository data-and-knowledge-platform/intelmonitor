// The Overview's Related card, after the team's draft and option E of the Related tree mocks: the
// subject at the left, one line per stored link to each linked entity, coloured and weighted by how
// strongly news at that entity reaches the subject (the link's strength toward it), and named with
// its words, its strength and the news it carried. An entity's number is its linked news: the
// window's developments that reached the subject through any of its links, each counted once. A
// line click narrows the open entity's news to that link; the entity's › opens all of it as cards
// beside the tree. Chips narrow the tree to one entity. Entities with no linked news are left out
// unless asked for. Data: GET /subjects/{id}/related-graph (server/related_graph.py).
import { Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";

import type { Schema } from "@/lib/api/client";
import { useRelatedGraph, type RelatedDays } from "@/lib/api/queries";
import { capitalize, longDate } from "@/lib/format";
import { subjectLink } from "@/lib/links";
import { PriorityBadge } from "@/components/developments/labels";
import { developmentLink } from "@/components/developments/link";

import { Section } from "./parts";

type Subject = Schema<"SubjectRef">;
type Node = Schema<"RelatedNode">;
type RelLink = Schema<"RelatedLink">;
type Development = Schema<"DevelopmentSummary">;
type Strength = NonNullable<RelLink["toward"]>;

const WINDOWS: RelatedDays[] = [7, 30, 90];
const STRENGTHS: Strength[] = ["Strong", "Moderate", "Weak", "Non-propagating"];
const RANK: Record<Strength, number> = { Strong: 3, Moderate: 2, Weak: 1, "Non-propagating": 0 };
/** The class each strength draws with (related.css): colour, then width and dash in STROKE. */
const CLS: Record<Strength, string> = { Strong: "strong", Moderate: "moderate", Weak: "weak", "Non-propagating": "none" };
const STROKE: Record<Strength | "unrated", [number, string]> = {
  Strong: [4.5, ""],
  Moderate: [2.5, ""],
  Weak: [1.75, "5 4"],
  "Non-propagating": [1.25, "1 4"],
  unrated: [1.25, "1 4"],
};
const word = (s: Strength | null) => (s === null ? "unrated" : s === "Non-propagating" ? "passes nothing" : s);

// Layout, in px: the subject box, where the line labels start, the entity column, the card column.
const ROOT_W = 120;
const LAB_X = 250;
const NODE_X = 510;
const NODE_W = 236;
const CARD_X = 800;
const CARD_STEP = 120;
const LINE_GAP = 22;
const ROW_GAP = 26;
const MAX_LINES = 3;
// Cards shown before "Show all".
const CARD_CAP = 4;
const GROUP_H = 24;

const rowHeight = (n: Node) => Math.max(46, Math.min(n.links.length, MAX_LINES) * LINE_GAP + 6);
const byStrength = (n: Node) => [...n.links].sort((a, b) => (b.toward ? RANK[b.toward] : -1) - (a.toward ? RANK[a.toward] : -1));

export function RelatedTree({ subject, relatedTab }: { subject: Subject; relatedTab: boolean }) {
  const [days, setDays] = useState<RelatedDays>(7);
  const query = useRelatedGraph(subject.id, days);
  const data = query.data;

  return (
    <div className="panel ov-card rt">
      <Section
        id="related-graph"
        title={`Related to ${subject.name}`}
        meta={
          relatedTab && (
            <Link {...subjectLink(subject, "related")} className="link">
              See all links
            </Link>
          )
        }
      >
        <p className="rt-lede">News at linked entities that reached {subject.name} through a link. Each number counts that news only.</p>
        {query.isError ? (
          <p className="mon-empty">These links could not be loaded.</p>
        ) : !data ? null : (
          // Keyed by window so the picks reset when the news changes.
          <Tree key={days} subject={subject} data={data} days={days} setDays={setDays} />
        )}
      </Section>
    </div>
  );
}

function Tree({
  subject,
  data,
  days,
  setDays,
}: {
  subject: Subject;
  data: Schema<"RelatedGraph">;
  days: RelatedDays;
  setDays: (d: RelatedDays) => void;
}) {
  const linked = data.nodes.filter((n) => n.development_ids.length > 0);
  const [quiet, setQuiet] = useState(false);
  const [only, setOnly] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(linked[0]?.id ?? null);
  const [picked, setPicked] = useState<{ node: string; link: string } | null>(null);
  const [allCards, setAllCards] = useState(false);
  const byId = useMemo(() => new Map(data.developments.map((d) => [d.id, d])), [data]);

  const shown = only ? data.nodes.filter((n) => n.id === only) : [...linked, ...(quiet ? data.nodes.filter((n) => !n.development_ids.length) : [])];
  const layout = place(shown, only === null && quiet && linked.length < data.nodes.length);
  const openRow = layout.rows.find((r) => r.node.id === open);
  const pickedLink = openRow && picked?.node === openRow.node.id ? openRow.node.links.find((l) => l.id === picked.link) : undefined;
  const found = openRow ? (pickedLink ? pickedLink.development_ids : openRow.node.development_ids).flatMap((id) => byId.get(id) ?? []) : [];
  const cards = allCards ? found : found.slice(0, CARD_CAP);
  const hiddenCards = found.length - cards.length;
  const cardsTop = openRow ? Math.max(0, openRow.y - (cards.length * CARD_STEP) / 2) : 0;
  const height = Math.max(layout.height, cardsTop + Math.max(cards.length, 1) * CARD_STEP + (hiddenCards ? 40 : 8));
  const rootY = layout.rows.length ? (layout.rows[0].y + layout.rows[layout.rows.length - 1].y) / 2 : 40;

  const pick = (node: Node, l: RelLink) => {
    const same = picked?.node === node.id && picked.link === l.id;
    setPicked(same ? null : { node: node.id, link: l.id });
    setOpen(node.id);
    setAllCards(false);
  };

  return (
    <div className="rt-stage">
      <div className="rt-bar">
        <div className="seg" role="group" aria-label="Developments from">
          {WINDOWS.map((d) => (
            <button key={d} type="button" aria-pressed={days === d} onClick={() => setDays(d)}>
              {d} days
            </button>
          ))}
        </div>
        {linked.length > 0 && (
          <div className="rt-chips" role="group" aria-label="Linked entities">
            <button type="button" className="rt-chip" aria-pressed={only === null} onClick={() => setOnly(null)}>
              All ({data.developments.length})
            </button>
            {linked.map((n) => (
              <button
                key={n.id}
                type="button"
                className="rt-chip"
                aria-pressed={only === n.id}
                onClick={() => {
                  setOnly(n.id);
                  setOpen(n.id);
                  setPicked(null);
                  setAllCards(false);
                }}
              >
                {n.name} ({n.development_ids.length})
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="rt-legend">
        <span>Strength toward {subject.name}</span>
        {STRENGTHS.map((s) => (
          <span key={s} className={`rt-key ${CLS[s]}`}>
            <svg width="28" height="8" aria-hidden="true">
              <line x1="1" y1="4" x2="27" y2="4" strokeWidth={STROKE[s][0]} strokeDasharray={STROKE[s][1]} strokeLinecap="round" />
            </svg>
            {s === "Non-propagating" ? "Passes nothing" : s}
          </span>
        ))}
        <label className="rt-quiet">
          <input type="checkbox" checked={quiet} onChange={(e) => setQuiet(e.target.checked)} />
          Show entities with no linked news
        </label>
      </div>
      {shown.length === 0 ? (
        <p className="mon-empty">
          No news reached {subject.name} through its links in the last {days} days.
        </p>
      ) : (
        <div className="rt-scroll">
          <div className="rt-canvas" style={{ height }} data-testid="related-tree">
            <svg className="rt-lines" aria-hidden="true">
              {layout.rows.flatMap(({ node, y }) =>
                byStrength(node)
                  .slice(0, MAX_LINES)
                  .map((l, k, all) => {
                    const ly = lineY(y, k, all.length);
                    const [w, dash] = STROKE[l.toward ?? "unrated"];
                    const faded = picked !== null && picked.link !== l.id;
                    const cls = `rt-line ${l.toward ? CLS[l.toward] : "none"}${faded ? " faded" : ""}`;
                    return (
                      <g key={l.id} className={cls}>
                        <path d={`M${ROOT_W},${rootY} C${ROOT_W + 90},${rootY} ${ROOT_W + 70},${ly} ${LAB_X - 20},${ly} H${NODE_X - 6}`} strokeWidth={w} strokeDasharray={dash} />
                        <path d={`M${NODE_X - 11},${ly - 4} L${NODE_X - 4},${ly} L${NODE_X - 11},${ly + 4}`} strokeWidth={1.5} />
                      </g>
                    );
                  }),
              )}
              {openRow &&
                cards.map((d, i) => {
                  const cy = cardsTop + i * CARD_STEP + 46;
                  const ox = NODE_X + NODE_W;
                  return (
                    <g key={d.id} className="rt-arrow">
                      <path d={`M${ox},${openRow.y} H${ox + 22} V${cy} H${CARD_X - 6}`} />
                      <path d={`M${CARD_X - 12},${cy - 4} L${CARD_X - 5},${cy} L${CARD_X - 12},${cy + 4}`} />
                    </g>
                  );
                })}
            </svg>
            <div className="rt-root" style={{ top: rootY - 26 }}>
              {subject.name}
            </div>
            {layout.groupY !== null && (
              <div className="rt-group" style={{ top: layout.groupY }}>
                No linked news in {days} days
              </div>
            )}
            {layout.rows.map(({ node, y }) => (
              <Row key={node.id} node={node} y={y} open={open === node.id} picked={picked} onPick={pick} onToggle={() => {
                setOpen(open === node.id ? null : node.id);
                setPicked(null);
                setAllCards(false);
              }} />
            ))}
            {openRow && cards.length === 0 && (
              <p className="rt-note" style={{ top: openRow.y - 10 }}>
                {pickedLink ? `This link carried no news to ${subject.name} in the last ${days} days.` : `No news at ${openRow.node.name} reached ${subject.name} through a link in the last ${days} days.`}
              </p>
            )}
            {openRow &&
              cards.map((d, i) => <Card key={d.id} development={d} node={openRow.node} subject={subject} top={cardsTop + i * CARD_STEP} />)}
            {openRow && hiddenCards > 0 && (
              <button type="button" className="rt-all" style={{ top: cardsTop + cards.length * CARD_STEP, left: CARD_X }} onClick={() => setAllCards(true)}>
                Show all {found.length}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** Where each shown row sits: entities with linked news, then, under a heading, the rest. */
function place(nodes: Node[], grouped: boolean) {
  let y = 12;
  let groupY: number | null = null;
  const rows: { node: Node; y: number }[] = [];
  for (const node of nodes) {
    if (grouped && groupY === null && node.development_ids.length === 0) {
      groupY = y;
      y += GROUP_H;
    }
    const h = rowHeight(node);
    rows.push({ node, y: y + h / 2 });
    y += h + ROW_GAP;
  }
  return { rows, groupY, height: y };
}

const lineY = (y: number, k: number, n: number) => y + (k - (n - 1) / 2) * LINE_GAP;

/** One entity: its line labels (the lines themselves are in the SVG) and its box. */
function Row({
  node,
  y,
  open,
  picked,
  onPick,
  onToggle,
}: {
  node: Node;
  y: number;
  open: boolean;
  picked: { node: string; link: string } | null;
  onPick: (node: Node, l: RelLink) => void;
  onToggle: () => void;
}) {
  const links = byStrength(node);
  const lines = links.slice(0, MAX_LINES);
  const rest = links.length - lines.length;
  const count = node.development_ids.length;
  return (
    <>
      {lines.map((l, k) => {
        const n = l.development_ids.length;
        return (
          <button
            key={l.id}
            type="button"
            className={`rt-lab ${l.toward ? CLS[l.toward] : "none"}`}
            style={{ left: LAB_X, top: lineY(y, k, lines.length) }}
            aria-pressed={picked?.node === node.id && picked.link === l.id}
            title={`${l.detail}\n\nToward: ${word(l.toward)}. ${l.toward_reason}\nAway: ${word(l.away)}. ${l.away_reason}`}
            onClick={() => onPick(node, l)}
          >
            <span className="dot" aria-hidden="true" />
            {l.label} <span className="w">{word(l.toward)}</span>
            {n > 0 && <span className="c">{n}</span>}
          </button>
        );
      })}
      {rest > 0 && (
        <span className="rt-more" style={{ left: LAB_X, top: lineY(y, lines.length, lines.length) + 4 }}>
          +{rest} more
        </span>
      )}
      <div className={`rt-node${count ? "" : " quiet"}`} style={{ top: y - 23, left: NODE_X, width: NODE_W }}>
        <span className="nm" title={node.name}>
          {node.subject.displayed ? <Link {...subjectLink(node.subject)}>{node.name}</Link> : node.name}
        </span>
        <span className={`k${count ? "" : " z"}`} title={`${count} linked news`}>
          {count}
        </span>
        <button type="button" className="exp" aria-expanded={open} aria-label={`${open ? "Close" : "Open"} news from ${node.name}`} onClick={onToggle}>
          {open ? "‹" : "›"}
        </button>
      </div>
    </>
  );
}

/** A development the open entity's links carried: priority, category, headline (its popup) and date,
 *  and the links that carried it. */
function Card({ development: d, node, subject, top }: { development: Development; node: Node; subject: Subject; top: number }) {
  const via = node.links.filter((l) => l.development_ids.includes(d.id));
  return (
    <div className="rt-card" style={{ top, left: CARD_X }}>
      <span className="top">
        <PriorityBadge priority={d.priority} />
        <span className="chip chip-sm">{capitalize(d.category)}</span>
      </span>
      <Link {...developmentLink(d.id, { subject: subject.id })} className="rt-title" title={d.headline}>
        {d.headline}
      </Link>
      <span className="meta">
        <span>{longDate(d.last_published)}</span>
        {via.map((l) => (
          <span key={l.id} className={`via ${l.toward ? CLS[l.toward] : "none"}`}>
            via {l.label}
          </span>
        ))}
      </span>
    </div>
  );
}
