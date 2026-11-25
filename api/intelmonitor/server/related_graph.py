"""The Overview's Related tree (GET /subjects/{id}/related-graph): every entity linked to the
subject in the relationship store, in either direction, each with its links (a few words, the
evidence, the strength each way) and the shown developments of the window that reach the subject
through it, per link and per entity.

A development reaches the subject through a node when its indirect impact on the subject
(development_impacts, read by server/impacts.py) passes through the subject's link to that node:
the entity the development is about, or a region that covers it. Links run either way, so news
about Singtel reaches DBS through "Singtel provides connectivity to DBS" and news about DBS
reaches Singtel through the same link. A hidden entity's node keeps its developments and its ref,
whose `displayed` is false, so the web shows its name with no page link."""

from intelmonitor.pipeline.impact import short_label
from intelmonitor.relationships import SCHEMA, links_of
from intelmonitor.server.impacts import subject_impacts
from intelmonitor.server.models import (
    RelatedDays,
    RelatedGraph,
    RelatedLink,
    RelatedNode,
)
from intelmonitor.server.queries import Queries, subject_ref


def related_graph(q: Queries, subject_id: str, days: RelatedDays) -> RelatedGraph:
    """The subject's linked nodes with the developments last published within `days` of the run
    date that reach it through each, which subject_impacts already limits to shown ones."""
    q.conn.executescript(SCHEMA)  # a store the pipeline has not synced serves no links
    nodes: dict[str, RelatedNode] = {}
    for r, node in links_of(q.conn, subject_id):
        if node.kind == "group":  # belongs_to_sector, which links_of leaves out
            continue
        found = nodes.setdefault(
            node.id,
            RelatedNode(
                id=node.id,
                name=node.name,
                kind=node.kind,
                subject=subject_ref(node.id),
                links=[],
                development_ids=[],
                harmful=0,
            ),
        )
        outbound = r.from_ == subject_id
        # Toward the centre is the direction from the node: to_from when the centre is `from`.
        toward, away = None, None
        if (t := r.transmission) is not None:
            toward, away = (t.to_from, t.from_to) if outbound else (t.from_to, t.to_from)
        found.links.append(
            RelatedLink(
                id=r.id,
                type=r.type,
                outbound=outbound,
                label=short_label(r, outbound),
                detail=r.detail,
                toward=toward.strength if toward else None,
                toward_reason=toward.reason if toward else "",
                away=away.strength if away else None,
                away_reason=away.reason if away else "",
                development_ids=[],
            )
        )

    impacts = subject_impacts(q, subject_id, days) if nodes else []  # in display order
    # The developments adverse for the entity they reach the subject from: with the adverse gate
    # off (INDIRECT_ADVERSE_ONLY), good news passes the table too.
    adverse = {
        (r["development_id"], r["subject_id"])
        for r in q.conn.execute(
            "SELECT development_id, subject_id FROM development_subjects "
            "WHERE run_id = ? AND adverse = 1",
            (q.run.id,),
        )
    }
    for i in impacts:
        through = i.link.to_id if i.link.from_id == subject_id else i.link.from_id
        if (n := nodes.get(through)) is None:
            continue
        link = next((k for k in n.links if k.id == i.link.id), None)
        if link is not None and i.development_id not in link.development_ids:
            link.development_ids.append(i.development_id)
        if i.development_id not in n.development_ids:
            n.development_ids.append(i.development_id)
            n.harmful += (i.development_id, i.source.id) in adverse

    return RelatedGraph(
        run=q.run,
        center=subject_ref(subject_id),
        days=days,
        nodes=sorted(nodes.values(), key=lambda n: (-len(n.development_ids), n.name.lower())),
        developments=list({i.development_id: i.development for i in impacts}.values()),
    )
