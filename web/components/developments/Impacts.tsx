// "Affected entities" in the development popup: a card for each subject it is about (its priority
// and the rating's reason) and for each tracked entity it reaches through one stored link (indirect
// impact by the fixed table: the level, the link's reason and its evidence), beside "Connection",
// which draws the routes from the news through the entities it is about to the ones it reaches.
// The words on a route are written here from the links, never by a model.
import { Link as RouterLink } from "@tanstack/react-router";

import type { Schema } from "@/lib/api/client";
import { longDate } from "@/lib/format";
import { subjectLink } from "@/lib/links";
import { SubjectLink } from "@/components/shell/SubjectLink";

import { AdverseTone, PriorityBadge } from "./labels";

type Impact = Schema<"IndirectImpact">;
type Direct = Schema<"DirectSubject">;
type Link = Schema<"RelationshipLink">;

/** The color of an indirect level, shown as a plain word. */
export const LEVEL_CLASS: Record<Impact["level"], string> = { Medium: "c-watch", Low: "muted" };

export const EVIDENCE: Record<Link["evidence"], { word: string; hint: string }> = {
  public: { word: "Public disclosure", hint: "Published by the institution or system itself." },
  reported: { word: "Press report", hint: "Reported by the press, not confirmed by the institution." },
  assumed: { word: "Assumed", hint: "Not confirmed by a source." },
};

// A source older than this gets its age flagged.
const OLD_YEARS = 3;

export function AffectedEntities({ direct, impacts }: { direct: Direct[]; impacts: Impact[] }) {
  // A topic takes its priority, and its reason, from an entity, so topics are chips, not cards.
  const entities = direct.filter((d) => d.subject.type === "entity");
  const topics = direct.filter((d) => d.subject.type === "topic");
  const total = entities.length + impacts.length;
  return (
    <section className="dp-sec" aria-labelledby="affected">
      <h3 id="affected">
        Affected entities <span className="dp-n">{total}</span>
      </h3>
      {topics.length > 0 && (
        <p className="dp-topics">
          {topics.map((t) => (
            <RouterLink key={t.subject.id} {...subjectLink(t.subject)} className="chip chip-sm">
              {t.subject.name}
            </RouterLink>
          ))}
        </p>
      )}
      {total === 0 ? (
        <p className="muted">No tracked entity is linked to this news.</p>
      ) : (
        <div className={impacts.length > 0 ? "aff aff-graph" : "aff"}>
          <div className="aff-cards">
            {entities.map((d) => (
              <DirectCard key={d.subject.id} direct={d} />
            ))}
            {impacts.map((i, n) => (
              <ImpactCard key={`${i.subject.id}-${n}`} impact={i} />
            ))}
          </div>
          {impacts.length > 0 && <Connection direct={entities} impacts={impacts} />}
        </div>
      )}
    </section>
  );
}

/** A subject it is about: its priority, adverse flag and tone, and why it got that priority. */
function DirectCard({ direct: d }: { direct: Direct }) {
  return (
    <article className="aff-card">
      <p className="aff-h">
        <SubjectLink subject={d.subject} />
        <span className="chip chip-sm">{d.role === "actor" ? "Took the action" : "Main entity"}</span>
        <PriorityBadge priority={d.priority} />
      </p>
      {d.reason && <p>{d.reason}</p>}
      <p className="meta">
        <AdverseTone adverse={d.adverse} tone={d.tone} />
      </p>
    </article>
  );
}

/** An entity it reaches through a link: the level passed, the link's reason and its evidence. */
function ImpactCard({ impact: i }: { impact: Impact }) {
  return (
    <article className="aff-card aff-imp">
      <p className="aff-h">
        <SubjectLink subject={i.subject} />
        <span className="chip chip-sm">Potential exposure</span>
        <span className={`imp-dir ${LEVEL_CLASS[i.level]}`}>{i.level}</span>
      </p>
      <p>{reasonFrom(i)}</p>
      <p className="meta">
        <span className="imp-route">{i.route}</span>
        {" · "}
        <LinkEvidence link={i.link} />
      </p>
    </article>
  );
}

/**
 * The routes as a tree: "This news", about each entity whose links carry it on (or the region a
 * link starts from), and under each, as branches off one line, the entities reached, every branch
 * labelled with its link.
 */
function Connection({ direct, impacts }: { direct: Direct[]; impacts: Impact[] }) {
  const heads = new Map<string, { name: string; reached: Impact[] }>();
  for (const i of impacts) {
    const end = farEnd(i);
    const head = heads.get(end.id) ?? { name: end.name, reached: [] };
    head.reached.push(i);
    heads.set(end.id, head);
  }
  // Heads in the order the news names its subjects, then any other far end.
  const order = new Map(direct.map((d, n) => [d.subject.id, n]));
  const columns = [...heads.entries()].sort(([a], [b]) => (order.get(a) ?? order.size) - (order.get(b) ?? order.size));
  return (
    <section className="cx" aria-labelledby="connection">
      <h4 id="connection">Connection</h4>
      <p className="meta">Drawn from the stored links between entities.</p>
      <div className="cx-tree">
        <span className="cx-node cx-news">This news</span>
        <span className="cx-edge">about</span>
        <ul className="cx-cols">
          {columns.map(([id, head]) => (
            <li key={id} className="cx-col">
              <span className="cx-node cx-ent">{head.name}</span>
              <ul className="cx-branches">
                {head.reached.map((i, n) => (
                  <li key={`${i.subject.id}-${n}`}>
                    <span className="cx-label">{edgeLabel(i.link)}</span>
                    <span className="cx-node cx-ent">{i.subject.name}</span>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      </div>
    </section>
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

const pct = (w: number) => `${(w * 100).toFixed(1).replace(/\.0$/, "")}%`;

/** A link as an edge label, read from the news toward the entity. */
function edgeLabel(l: Link): string {
  switch (l.type) {
    case "exposed_to":
      // The label ends with the share: "lends to, 12.2% of loans", "holds reserves in, 84.9% of reserves".
      if (l.weight !== null) return l.label.split(", ").at(-1) ?? "";
      return l.label.startsWith("lends") ? "lends there" : l.label.split(",")[0];
    case "provides_technology_to":
    case "provides_business_service_to":
      // What is provided, from "provides cloud services to": the word reads either way along the edge.
      return l.label.replace(/^provides (.+) to$/, "$1");
    case "provides_connectivity_to":
      return "connectivity";
    case "processes_transactions_for":
      // "payments" or "transactions", from "processes payments for".
      return l.label.replace(/^processes (.+) for$/, "$1");
    case "distributes_products_for":
      return "distribution";
    case "participates_in":
      return "takes part";
    case "holds_stake_in":
      // With no published share the stake may be controlling or minor, so the word stays neutral.
      return l.weight === null ? "stake" : `${pct(l.weight)} owned`;
    default:
      return l.label.split(",")[0];
  }
}
