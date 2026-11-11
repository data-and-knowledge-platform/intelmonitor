"""Step 12: graph nodes and edges for a run. Subjects, relationship endpoints and developments are
nodes. Edges, each with a plain label:
- the relationship store's links, typed in capitals (DEPENDS_ON, EXPOSED_TO, ...), among them
  BELONGS_TO_SECTOR from an entity to its seed group, each with its reason and its strength in
  each direction (from_to, to_from) in its detail;
- AFFECTS from a development to the subjects it happened to or that did it (development_subjects);
- IMPACTS from a development to the entities it reaches indirectly, with the route, the level and
  the link's strength (development_impacts).
Claude Haiku tags each development with one theme, which the graph view colours and groups by."""

import asyncio
import json
import sqlite3
from typing import Literal

from pydantic import BaseModel

from intelmonitor.pipeline.diff import DevelopmentChange
from intelmonitor.pipeline.impact import relationship_label, route_text
from intelmonitor.pipeline.inference import Inference, StepFailed
from intelmonitor.pipeline.params import HAIKU_MODEL, PRIORITY_LABELS
from intelmonitor.relationships import Relationship, load_nodes
from intelmonitor.seed import load_subjects

# The graph view's fixed themes, in the order their sectors sit around the centre.
Theme = Literal["regulation", "digital-assets", "technology", "strategy", "markets", "macro"]
THEMES: dict[str, str] = {
    "regulation": "Rules, consultations, licensing, supervision, AML/CFT or enforcement action",
    "digital-assets": "Stablecoins, tokenised deposits or assets, crypto, CBDCs or blockchain",
    "technology": "AI, technology spending, cyber security, outages or digital platforms",
    "strategy": "M&A, expansion, new products, wealth management, partnerships or leadership",
    "markets": "Earnings, capital, share prices, ratings, analyst calls or market moves",
    "macro": "Monetary policy, rates, growth, FX, trade, tariffs, sanctions or conflicts",
}
THEME_SYSTEM = "What is this development mainly about? Pick one theme:\n" + "\n".join(
    f"- {k}: {v}" for k, v in THEMES.items()
)


class ThemeReply(BaseModel):
    theme: Theme


async def development_themes(
    inf: Inference, conn: sqlite3.Connection, run_id: str
) -> dict[str, str]:
    """Theme per development id, one Haiku call on each development's statement."""
    rows = conn.execute("SELECT id, title FROM developments WHERE run_id = ?", (run_id,)).fetchall()
    failures: list[str] = []

    async def tag(r: sqlite3.Row) -> tuple[str, str]:
        try:
            reply = await inf.claude(
                "theme",
                f"dev:{r['id']}",
                f"Development: {r['title']}",
                ThemeReply,
                THEME_SYSTEM,
                model=HAIKU_MODEL,
            )
        except Exception as error:
            failures.append(f"{r['id']}: {type(error).__name__}: {error}")
            return r["id"], ""
        return r["id"], reply.theme

    themes = dict(await asyncio.gather(*(tag(r) for r in rows)))
    if failures:
        raise StepFailed("theme", failures)
    return themes


type NodeRow = tuple[str, str, str, str, str]
type EdgeRow = tuple[str, str, str, str, str, float, str, str]


async def store_graph(
    inf: Inference, conn: sqlite3.Connection, run_id: str, changes: dict[str, DevelopmentChange]
) -> tuple[int, int]:
    """Rebuild the run's graph; returns (nodes, edges)."""
    themes = await development_themes(inf, conn, run_id)
    conn.execute("DELETE FROM graph_nodes WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM graph_edges WHERE run_id = ?", (run_id,))
    seed = load_subjects()
    nodes: list[NodeRow] = []
    edges: list[EdgeRow] = []

    nodes += [(run_id, t.id, "topic", t.name, json.dumps({})) for t in seed.topics]
    nodes += [(run_id, g.id, "group", g.name, json.dumps({})) for g in seed.groups]
    nodes += [(run_id, e.id, "entity", e.name, json.dumps({"kind": e.kind})) for e in seed.entities]
    for e in conn.execute("SELECT id, name, kind FROM relationship_endpoints"):
        nodes.append((run_id, e["id"], "endpoint", e["name"], json.dumps({"kind": e["kind"]})))
    links: dict[str, Relationship] = {}
    for row in conn.execute("SELECT data FROM relationships ORDER BY id"):
        r = Relationship.model_validate_json(row["data"])
        links[r.id] = r
        t = r.transmission
        detail: dict[str, object] = {
            "relationship_id": r.id,
            "evidence": r.evidence,
            "weight": r.weight,
            "reason": r.reason,
            "from_to": t.from_to.strength if t else None,
            "to_from": t.to_from.strength if t else None,
        }
        edges.append(
            (
                run_id,
                r.from_,
                r.to,
                r.type.upper(),
                "relationships",
                1.0,
                relationship_label(r),
                json.dumps(detail),
            )
        )

    for d in conn.execute("SELECT * FROM developments WHERE run_id = ?", (run_id,)):
        change = changes.get(d["id"])
        data = {
            "category": d["category"],
            "theme": themes[d["id"]],
            "materiality_level": d["materiality_level"],
            "needs_attention": bool(d["needs_attention"]),
            "last_published": d["last_published"],
            "change": change.kind if change else None,
        }
        nodes.append((run_id, f"dev:{d['id']}", "development", d["title"], json.dumps(data)))
    for s in conn.execute(
        "SELECT development_id, subject_id, confidence, source, role FROM development_subjects "
        "WHERE run_id = ?",
        (run_id,),
    ):
        label = "directly affects" if s["role"] == "affected" else "action by"
        edges.append(
            (
                run_id,
                f"dev:{s['development_id']}",
                s["subject_id"],
                "AFFECTS",
                s["source"],
                s["confidence"],
                label,
                "{}",
            )
        )
    impacts = conn.execute(
        "SELECT development_id, subject_id, source_id, relationship_id, strength, level "
        "FROM development_impacts WHERE run_id = ?",
        (run_id,),
    ).fetchall()
    ends = {e for i in impacts for e in (i["subject_id"], i["source_id"])}
    ends |= {e for i in impacts if (r := links.get(i["relationship_id"])) for e in (r.from_, r.to)}
    nodes_by_id = load_nodes(conn, ends) if impacts else {}
    for i in impacts:
        link = links.get(i["relationship_id"])
        if link is None:  # the link left the store since the impact step
            continue
        route = route_text(link, i["source_id"], i["subject_id"], nodes_by_id)
        detail = {
            "route": route,
            "level": PRIORITY_LABELS[i["level"]],
            "strength": i["strength"],
            "source_id": i["source_id"],
            "link_id": link.id,
        }
        edges.append(
            (
                run_id,
                f"dev:{i['development_id']}",
                i["subject_id"],
                "IMPACTS",
                "table",
                1.0,
                f"indirectly affects, {route}",
                json.dumps(detail),
            )
        )

    # A relationship endpoint may share an id with a seed topic or group: the subject wins.
    conn.executemany(
        "INSERT OR IGNORE INTO graph_nodes (run_id, id, type, label, data) VALUES (?, ?, ?, ?, ?)",
        nodes,
    )
    conn.executemany(
        "INSERT OR IGNORE INTO graph_edges (run_id, from_id, to_id, type, source, confidence, "
        "label, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        edges,
    )
    conn.commit()
    return len(nodes), len(edges)
