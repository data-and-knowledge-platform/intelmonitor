"""Indirect impacts and direct subjects, read from the latest run's development_impacts and the
relationship store (pipeline/impact.py, relationships.py)."""

import sqlite3
from datetime import timedelta

from intelmonitor.pipeline.impact import relationship_label, route_text
from intelmonitor.pipeline.params import PRIORITY_LABELS
from intelmonitor.pipeline.subjects import all_subjects
from intelmonitor.relationships import SCHEMA, Relationship, load_nodes
from intelmonitor.server.models import (
    DirectSubject,
    IndirectImpact,
    IndirectLevel,
    RelationshipFigure,
    RelationshipLink,
    SubjectImpact,
)
from intelmonitor.server.queries import Queries, subject_ref

# The period a monitor page shows indirect impacts for, as the entity brief covers a week.
IMPACT_PERIOD_DAYS = 7


def display_names(conn: sqlite3.Connection) -> dict[str, str]:
    """Display names of every subject, by id. A store the pipeline has not synced yet gets the
    relationships table empty, so it serves no links instead of failing."""
    conn.executescript(SCHEMA)
    return {i: s.name for i, s in all_subjects().items()}


def relationship_link(r: Relationship, names: dict[str, str]) -> RelationshipLink:
    return RelationshipLink(
        id=r.id,
        type=r.type,
        label=relationship_label(r),
        from_id=r.from_,
        from_name=names.get(r.from_, r.from_),
        to_id=r.to,
        to_name=names.get(r.to, r.to),
        weight=r.weight,
        measure=r.measure,
        value=r.value,
        unit=r.unit,
        evidence=r.evidence,
        chart_reading=r.chart_reading,
        as_of=r.as_of,
        last_confirmed=r.last_confirmed,
        source_url=r.source_url,
        source_title=r.source_title,
        page=r.page,
        quote=r.quote,
        detail=r.detail,
        reason=r.reason,
        from_to=r.transmission.from_to.strength if r.transmission else None,
        to_from=r.transmission.to_from.strength if r.transmission else None,
        figures=[RelationshipFigure.model_validate(f.model_dump()) for f in r.figures],
    )


# development_impacts.level as the API names it; the table never gives High.
INDIRECT_LEVELS: dict[int, IndirectLevel] = {1: "Medium", 0: "Low"}


def _impacts(q: Queries, where: str, args: tuple[object, ...]) -> list[IndirectImpact]:
    """The latest run's impacts matching `where` (on development_impacts as `i`), by development,
    Medium before Low, stronger links first. An impact whose link left the store since the run,
    or whose entity or source left the seed, is skipped."""
    subjects = all_subjects()
    rows = [
        r
        for r in q.conn.execute(
            "SELECT i.development_id, i.subject_id, i.source_id, i.region, i.strength, i.level, "
            "r.data FROM development_impacts i JOIN relationships r ON r.id = i.relationship_id "
            f"WHERE i.run_id = ? AND {where} ORDER BY i.development_id, i.level DESC, "
            "CASE i.strength WHEN 'Strong' THEN 0 WHEN 'Moderate' THEN 1 ELSE 2 END, i.subject_id",
            (q.run.id, *args),
        ).fetchall()
        if r["subject_id"] in subjects and r["source_id"] in subjects
    ]
    if not rows:
        return []
    links = [Relationship.model_validate_json(r["data"]) for r in rows]
    names = display_names(q.conn)
    ends = {i for link in links for i in (link.from_, link.to)}
    nodes = load_nodes(ends | {r["source_id"] for r in rows})
    return [
        IndirectImpact(
            development_id=r["development_id"],
            subject=subject_ref(r["subject_id"]),
            level=INDIRECT_LEVELS[r["level"]],
            strength=r["strength"],
            source=subject_ref(r["source_id"]),
            route=route_text(link, r["source_id"], r["subject_id"], nodes),
            region=names.get(r["region"], r["region"]) if r["region"] else None,
            link=relationship_link(link, names),
        )
        for r, link in zip(rows, links, strict=True)
    ]


def development_impacts(q: Queries, development_id: str) -> list[IndirectImpact]:
    """The entities a development reaches indirectly, Medium before Low."""
    return _impacts(q, "i.development_id = ?", (development_id,))


def subject_impacts(q: Queries, subject_id: str, days: int | None) -> list[SubjectImpact]:
    """Developments that reach the subject indirectly, last published within `days` of the run
    date (any time in the run when None), in display order."""
    since = q.run.as_of - timedelta(days=days) if days else None
    found: list[SubjectImpact] = []
    for i in _impacts(q, "i.subject_id = ?", (subject_id,)):
        dev = q.devs.by_id.get(i.development_id)
        if dev is not None and (since is None or dev.last_published > since):
            found.append(SubjectImpact(**i.model_dump(), development=dev))
    order = list(q.devs.by_id)
    return sorted(found, key=lambda s: order.index(s.development_id))


def direct_subjects(q: Queries, development_id: str) -> list[DirectSubject]:
    """What the development happened to or who did it, highest priority first."""
    subjects = all_subjects()
    return [
        DirectSubject(
            subject=subject_ref(r["subject_id"]),
            role=r["role"],
            priority=PRIORITY_LABELS[int(r["priority_level"])],
            adverse=bool(r["adverse"]),
            tone=r["tone"],
            reason=r["priority_reason"],
        )
        for r in q.conn.execute(
            "SELECT subject_id, role, priority_level, adverse, tone, priority_reason "
            "FROM development_subjects "
            "WHERE run_id = ? AND development_id = ? "
            "ORDER BY priority_level DESC, priority_score DESC, subject_id",
            (q.run.id, development_id),
        )
        if r["subject_id"] in subjects
    ]
