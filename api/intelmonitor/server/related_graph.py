"""The Overview's Related graph (GET /subjects/{id}/related-graph): every entity linked to the
subject in the relationship store, in either direction, each with its links in a few words and
the shown developments of the window that reach the subject through it.

A development reaches the subject through a node when its indirect impact on the subject
(development_impacts, read by server/impacts.py) passes through the subject's link to that node:
the entity the development is about, or a region that covers it. Links run either way, so news
about Singtel reaches DBS through "DBS depends on Singtel" and news about DBS reaches Singtel
through the same link. A hidden entity's node keeps its developments and its ref, whose
`displayed` is false, so the web shows its name with no page link."""

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
        found.links.append(
            RelatedLink(id=r.id, type=r.type, outbound=outbound, label=short_label(r, outbound))
        )

    impacts = subject_impacts(q, subject_id, days) if nodes else []  # in display order
    for i in impacts:
        through = i.link.to_id if i.link.from_id == subject_id else i.link.from_id
        if (n := nodes.get(through)) is not None:
            n.development_ids.append(i.development_id)
            # Only a development adverse for the entity it is about passes the table.
            n.harmful += 1

    return RelatedGraph(
        run=q.run,
        center=subject_ref(subject_id),
        days=days,
        nodes=sorted(
            nodes.values(), key=lambda n: (-len(n.development_ids), -n.harmful, n.name.lower())
        ),
        developments=[i.development for i in impacts],
    )
