// "Who it impacts" in the development popup: the subjects it is about first, then the tracked
// entities it reaches through one stored link (indirect impact by the fixed table), each with its
// route, level, the link's reason and its evidence (grade, size, age, source). "Connections" under
// it backs one claim per route with the graph, the claims written here from the links, never by a
// model.
import type { Schema } from "@/lib/api/client";
import { longDate } from "@/lib/format";
import { SubjectLink } from "@/components/shell/SubjectLink";
import { Snippet, type SnippetPath } from "@/components/graph/Snippet";

import { AdverseTone, names, PriorityBadge } from "./labels";

type Impact = Schema<"IndirectImpact">;
type Direct = Schema<"DirectSubject">;
type Link = Schema<"RelationshipLink">;

/** The color of an indirect level, shown as a plain word. */
export const LEVEL_CLASS: Record<Impact["level"], string> = {
  High: "c-attn",
  Medium: "c-watch",
  Low: "muted",
};

export const EVIDENCE: Record<Link["evidence"], { word: string; hint: string }> = {
  public: { word: "Public disclosure", hint: "Published by the institution or system itself." },
  reported: { word: "Press report", hint: "Reported by the press, not confirmed by the institution." },
  assumed: { word: "Assumed", hint: "Not confirmed by a source." },
};

// A source older than this gets its age flagged.
const OLD_YEARS = 3;

export function WhoItImpacts({ direct, impacts }: { direct: Direct[]; impacts: Impact[] }) {
  return (
    <section className="dp-sec" aria-labelledby="who">
      <h3 id="who">Who it impacts</h3>
      {direct.length > 0 && (
        <div className="who-group">
          <h4>This development is about</h4>
          <DirectRows direct={direct} />
        </div>
      )}
      {impacts.length > 0 && (
        <div className="who-group">
          <h4>Could also be affected, through their links</h4>
          {impacts.map((i, n) => (
            <ImpactRow key={`${i.subject.id}-${n}`} impact={i} />
          ))}
        </div>
      )}
      {direct.length === 0 && impacts.length === 0 && <p className="muted">No tracked entity or topic is linked to this news.</p>}
    </section>
  );
}

/** One row per subject, or one row listing them all when they share their labels. */
function DirectRows({ direct }: { direct: Direct[] }) {
  const same = direct.every(
    (d) => d.priority === direct[0].priority && d.adverse === direct[0].adverse && d.tone === direct[0].tone && d.role === direct[0].role,
  );
  const groups = same ? [direct] : direct.map((d) => [d]);
  return groups.map((g) => (
    <p key={g[0].subject.id} className="who-direct">
      <span className="who-names">
        {g.map((d, i) => (
          <span key={d.subject.id}>
            {i > 0 && (i === g.length - 1 ? " and " : ", ")}
            <SubjectLink subject={d.subject} />
          </span>
        ))}
      </span>
      <PriorityBadge priority={g[0].priority} />
      <span className="meta">
        <AdverseTone adverse={g[0].adverse} tone={g[0].tone} />
        {g[0].role === "actor" && " · Took the action"}
      </span>
    </p>
  ));
}

function ImpactRow({ impact: i }: { impact: Impact }) {
  return (
    <div className="imp">
      <p className="imp-h">
        <SubjectLink subject={i.subject} /> <span className="imp-route">{i.route}</span>
        {" · "}
        <span className={`imp-dir ${LEVEL_CLASS[i.level]}`}>{i.level}</span>
      </p>
      <p className="imp-why">{reasonFrom(i)}</p>
      <p className="meta imp-ev">
        <LinkEvidence link={i.link} />
      </p>
    </div>
  );
}

/** S$210,852m, S$45.3b, US$62,317m, 38.98m tokens: one decimal under 100. */
function money(v: number, unit: NonNullable<Link["unit"]>) {
  const n = v.toLocaleString("en-GB", { maximumFractionDigits: unit === "SGD b" || v < 100 ? 1 : 0 });
  if (unit === "m tokens") return `${n}m tokens`;
  return `${unit.startsWith("USD") ? "US$" : "S$"}${n}${unit.slice(-1)}`;
}

const MEASURE: Record<NonNullable<Link["measure"]>, string> = {
  gross_customer_loans: "of loans",
  loans_by_currency: "of loans in that currency",
  rwa: "of risk-weighted assets",
  reserve_assets: "of reserves",
  repo_principal: "in repo",
  revenue: "of revenue",
  distribution_costs: "of distribution costs",
  token_supply: "on that chain",
  assets_on_platform: "of customer assets",
  stablecoin_holdings: "held",
  card_volume: "annualised card volume",
};

/** Whole years from a date to today. */
const yearsSince = (iso: string) => Math.floor((Date.now() - Date.parse(iso)) / (365.25 * 86_400_000));

/** "Public disclosure: 2Q26 summary, p. 20 · S$210,852m of loans · Figures as of 30 Jun 2026 · Link last checked 7 Oct 2026". */
export function LinkEvidence({ link: l }: { link: Link }) {
  const grade = EVIDENCE[l.evidence];
  const source = [l.source_title, l.page !== null && `p. ${l.page}`].filter(Boolean).join(", ");
  const size = l.value !== null && l.unit ? `${money(l.value, l.unit)} ${l.measure ? MEASURE[l.measure] : ""}`.trim() : "Size not disclosed";
  const age = l.as_of ? yearsSince(l.as_of) : 0;
  return (
    <>
      <span title={grade.hint} className="ev-grade">
        {grade.word}
      </span>
      {source && (
        <>
          {": "}
          {l.source_url ? (
            <a href={l.source_url} target="_blank" rel="noreferrer">
              {source}
            </a>
          ) : (
            source
          )}
        </>
      )}
      {" · "}
      {size}
      {l.as_of && (
        <>
          {" · "}
          {l.value !== null ? "Figures as of" : "Source dated"} {longDate(l.as_of)}
          {age >= OLD_YEARS && <span className="c-watch">, over {age} years old</span>}
        </>
      )}
      {" · "}Link last checked {longDate(l.last_confirmed)}
    </>
  );
}

/** The link's other end from the entity reached: what the news is about, or its region. */
function farEnd(i: Impact): { id: string; name: string } {
  const l = i.link;
  return l.from_id === i.subject.id ? { id: l.to_id, name: l.to_name } : { id: l.from_id, name: l.from_name };
}

/**
 * The link's reason under the entity reached. A reason built from a share starts at its verb, written from the
 * link's `from` end, so when the entity reached is the `to` end it gets that end's name: "OCBC holds 93.7% of
 * Great Eastern, ...". A detail sentence stands alone.
 */
export function reasonFrom({ subject, link: l }: Pick<Impact, "subject" | "link">): string {
  return l.to_id === subject.id && l.reason !== l.detail ? `${l.from_name} ${l.reason}` : l.reason;
}

/** Whether the entity reached is the link's `from` end, so the link reads from it ("DBS lends in Hong Kong"). */
const outward = (i: Impact) => i.link.from_id === i.subject.id;
// Links from a provider, a payment system or a distributor to the entity it serves.
const USED = new Set<Link["type"]>([
  "provides_technology_to",
  "provides_connectivity_to",
  "provides_business_service_to",
  "processes_transactions_for",
  "distributes_products_for",
]);

/** One sentence per route type and far end: "DBS, OCBC and UOB each lend over 40% of their loans in Singapore". */
export function claims(impacts: Impact[]): string[] {
  const groups = new Map<string, Impact[]>();
  for (const i of impacts) {
    const key = `${i.link.type}:${outward(i)}:${farEnd(i).id}`;
    groups.set(key, [...(groups.get(key) ?? []), i]);
  }
  return [...groups.values()].map((g) => {
    const who = names([...new Set(g.map((i) => i.subject.name))]);
    const many = g.length > 1;
    const end = farEnd(g[0]).name;
    const first = g[0].link;
    // A provider serves the entity it reaches ("DBS uses Singtel"), read from the entity's end.
    if (!outward(g[0]) && USED.has(first.type)) return `${who} ${many ? (g.length === 2 ? "both use" : "all use") : "uses"} ${end}`;
    // Any other link reads from the far end ("Temasek holds a stake in DBS", reaching DBS): only the plain wording fits.
    switch (outward(g[0]) ? first.type : null) {
      case "exposed_to": {
        // Reserves, revenue and the like: the label's head says how ("holds reserves in").
        if (!first.label.startsWith("lends")) return `${who} ${many ? "are each exposed to" : first.label.split(",")[0]} ${end}`;
        const shares = g.map((i) => i.link.weight);
        if (shares.some((w) => w === null)) return `${who} ${many ? "lend" : "lends"} in ${end}`;
        const low = Math.min(...(shares as number[]));
        if (!many) return `${who} lends ${(low * 100).toFixed(1).replace(/\.0$/, "")}% of its loans in ${end}`;
        return low >= 0.1 ? `${who} each lend over ${Math.floor(low * 10) * 10}% of their loans in ${end}` : `${who} each lend part of their loans in ${end}`;
      }
      case "participates_in":
        return `${who} ${many ? (g.length === 2 ? "both take" : "all take") : "takes"} part in ${end}`;
      default:
        return `${who} ${many ? "are" : "is"} linked to ${end}`;
    }
  });
}

/** "Connections" under the impacts: the claims, then one snippet with every route. */
export function ImpactGraph({ impacts }: { impacts: Impact[] }) {
  const lines = claims(impacts);
  const paths: SnippetPath[] = impacts.map((i) => ({ entityId: i.subject.id, entityName: i.subject.name, links: [i.link] }));
  return (
    <section className="dp-sec dp-graph" aria-labelledby="connections">
      <h3 id="connections">Connections</h3>
      {lines.length === 0 && <p className="muted">No stored link carries this news to another tracked entity.</p>}
      {lines.map((c) => (
        <p key={c} className="claim">
          {c}.
        </p>
      ))}
      {paths.length > 0 && <Snippet paths={paths} label={`${lines.join(". ")}.`} />}
    </section>
  );
}
