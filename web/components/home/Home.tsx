// Home (/): the current view's watchlist at a glance. The scope (its entities, topics and their
// groups as sectors), then New developments: one row per watchlist entity and topic with the week's
// developments by priority, the top High headline, the topics they cover, a status and the period,
// in the Entities and Topics lists' ranking (GET /lists/{list}, period "week"). Explore links to the
// two lists.
import { Link } from "@tanstack/react-router";
import { Building2, ChevronRight, Layers } from "lucide-react";

import type { Schema } from "@/lib/api/client";
import { useSubjectList } from "@/lib/api/queries";
import { addDays, longDate, plural } from "@/lib/format";
import { groupLink, subjectLink } from "@/lib/links";
import { useWatchlist } from "@/lib/viewerState";
import { developmentLink } from "@/components/developments/link";
import { PRIORITIES } from "@/components/developments/labels";

type Row = Schema<"ListRow">;
type Ref = Schema<"SubjectRef">;

/** Topics shown on a row before "N more". */
const ROW_TOPICS = 3;

export function Home() {
  const { entities, topics, editable, loaded } = useWatchlist();
  const entityList = useSubjectList("entities", "week");
  const topicList = useSubjectList("topics", "week");

  // Watchlist rows in the lists' ranking: entities, then topics.
  const ids = new Set([...entities, ...topics].map((s) => s.id));
  const rows = [...(entityList.data?.rows ?? []), ...(topicList.data?.rows ?? [])].filter((r) => ids.has(r.id));
  // A sector is a group one of the watchlist's entities is in.
  const sectors = [...new Map(rows.flatMap((r) => r.groups).map((g) => [g.id, g])).values()].sort((a, b) =>
    a.name.localeCompare(b.name),
  );
  const run = entityList.data;
  const period = run ? `${longDate(addDays(run.since, 1))} to ${longDate(run.run.as_of)}` : "";

  return (
    <div className="home">
      <header className="home-head">
        <h1>Entity Intel Monitor Dashboard</h1>
        <p>
          What happened this week at the entities and topics you monitor, rated High, Medium or Low for Singapore, and which
          linked entities it could reach.
        </p>
      </header>

      <section className="home-sec" aria-labelledby="home-scope">
        <h2 id="home-scope">Your monitoring scope</h2>
        <div className="home-tiles">
          <Tile n={entities.length} label="Entities" />
          <Tile n={topics.length} label="Topics" />
          <Tile n={sectors.length} label="Sectors" />
        </div>
        <h3>On your watchlist</h3>
        {loaded && ids.size === 0 ? (
          <p className="home-empty">Your watchlist is empty.</p>
        ) : (
          <div className="home-chips">
            {[...entities, ...topics].map((s) => (
              <Link key={s.id} {...subjectLink(s)} className="chip">
                {s.name}
              </Link>
            ))}
            {sectors.map((g) => (
              <Link key={g.id} {...groupLink(g.id)} className="chip chip-sector" title="Sector: opens its entities">
                {g.name}
              </Link>
            ))}
          </div>
        )}
        <Link to="/entities" className="home-more">
          {editable ? "Manage your watchlist" : "Browse entities"} <span aria-hidden="true">→</span>
        </Link>
      </section>

      <section className="home-sec" aria-labelledby="home-devs">
        <h2 id="home-devs">New developments</h2>
        <p className="home-lede">Developments at your watchlist&apos;s entities and topics this week, most High first.</p>
        {entityList.isError || topicList.isError ? (
          <p className="home-empty">This week&apos;s developments could not be loaded.</p>
        ) : (
          <table className="home-table">
            <thead>
              <tr>
                <th scope="col">Watchlist</th>
                <th scope="col">Developments</th>
                <th scope="col">Status</th>
                <th scope="col">Period</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <DevelopmentsRow key={r.id} row={r} period={period} />
              ))}
              {loaded && run && rows.length === 0 && (
                <tr>
                  <td colSpan={4} className="home-empty">
                    Nothing on your watchlist yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </section>

      <section className="home-sec" aria-labelledby="home-explore">
        <h2 id="home-explore">Explore the dashboard</h2>
        <p className="home-lede">Find developments at other organisations, infrastructure and networks.</p>
        <div className="home-explore">
          <Explore to="/entities" icon={<Building2 size={20} aria-hidden="true" />} title="Entities">
            Every monitored organisation, system, country and sector, ranked by this week&apos;s developments.
          </Explore>
          <Explore to="/topics" icon={<Layers size={20} aria-hidden="true" />} title="Topics">
            MAS&apos;s 30 themes across industries, infrastructure and networks, and what could reach your entities.
          </Explore>
        </div>
      </section>
    </div>
  );
}

function Tile({ n, label }: { n: number; label: string }) {
  return (
    <div className="home-tile">
      <span className="home-tile-n">{n}</span>
      <span>{label}</span>
    </div>
  );
}

const COUNT_KEY = { High: "high", Medium: "medium", Low: "low" } as const;

/** One watchlist subject's week: its name, the developments by priority as a bar, the top High
 *  headline (opening its popup), the topics they cover, Needs attention when any is High, and the period. */
function DevelopmentsRow({ row: r, period }: { row: Row; period: string }) {
  const counts = PRIORITIES.map((p) => ({ p, n: r.counts[COUNT_KEY[p]] }));
  const total = counts.reduce((sum, c) => sum + c.n, 0);
  const attention = r.counts.high > 0;
  return (
    <tr className={attention ? "attn" : undefined}>
      <td>
        <Link {...subjectLink(r)} className="chip">
          {r.name}
        </Link>
      </td>
      <td className="home-dev">
        <span className="home-total">{plural(total, "development")}</span>
        {total > 0 && (
          <>
            <span className="home-bar" aria-hidden="true">
              {counts.map((c) => c.n > 0 && <span key={c.p} className={c.p} style={{ flexGrow: c.n }} />)}
            </span>
            <span className="home-counts">
              {counts.map((c) => (
                <span key={c.p}>
                  <span className={`lp-dot ${c.p}`} aria-hidden="true" />
                  {c.p} <b>{c.n}</b>
                </span>
              ))}
            </span>
          </>
        )}
        {r.top?.priority === "High" && (
          <Link {...developmentLink(r.top.id, { subject: r.id })} className="home-headline">
            {r.top.headline}
          </Link>
        )}
        {r.topics.length > 0 && <Topics topics={r.topics} />}
      </td>
      <td>
        {attention ? <span className="badge b-attn">Needs attention</span> : <span className="badge b-stable">Nothing of note</span>}
      </td>
      <td className="home-period">{period}</td>
    </tr>
  );
}

function Topics({ topics }: { topics: Ref[] }) {
  const more = topics.length - ROW_TOPICS;
  return (
    <span className="home-topics">
      <span className="home-topics-label">Topics covered</span>
      {topics.slice(0, ROW_TOPICS).map((t) => (
        <Link key={t.id} {...subjectLink(t)} className="chip chip-sm">
          {t.name}
        </Link>
      ))}
      {more > 0 && <span className="home-topics-more">{more} more</span>}
    </span>
  );
}

function Explore({ to, icon, title, children }: { to: "/entities" | "/topics"; icon: React.ReactNode; title: string; children: string }) {
  return (
    <Link to={to} className="home-card">
      <span className="home-card-icon">{icon}</span>
      <span className="home-card-text">
        <span className="home-card-title">{title}</span>
        <span>{children}</span>
      </span>
      <ChevronRight size={18} aria-hidden="true" />
    </Link>
  );
}
