"""The source gate: which developments are shown and which are unconfirmed.

A development is shown when any of these holds, and unconfirmed otherwise:
- MIN_OUTLETS or more distinct news outlets report it, counting an outlet by its parent domain
  (www.ft.com and ft.com are one outlet), except SEPARATE_OUTLETS, which count on their own
  (markets.ft.com press releases are an ordinary outlet, not ft.com);
- a reputable outlet (REPUTABLE_DOMAINS, matched on parent domain) reports it;
- an official, research or data source backs it (the entity's own site, careers and docs pages
  included, is stored as official).
This module owns the rule. The API (server/queries.py), the briefs (pipeline/summaries.py) and
the run log (pipeline/runlog.py) call it; none re-implements it. Unconfirmed developments stay in
the store and the API can list them on request.
"""

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from intelmonitor.ingest.sources.search import REPUTABLE_DOMAINS, SEPARATE_OUTLETS

MIN_OUTLETS = 2
# Collections that back a development on their own: an official body, a researcher or a series.
BACKING_COLLECTIONS = {"official", "dms", "data"}
# Second-level labels under a country code that are part of the suffix: businesstimes.com.sg is
# its own outlet, not part of com.sg.
_SUFFIX_LABELS = {"com", "co", "net", "org", "gov", "edu", "ac", "go", "or", "ne"}


def outlet(domain: str) -> str:
    """The parent domain a source's domain belongs to: www.ft.com is ft.com,
    www.businesstimes.com.sg is businesstimes.com.sg. A domain under SEPARATE_OUTLETS is that
    outlet: markets.ft.com stays markets.ft.com."""
    domain = domain.lower().removeprefix("www.")
    for separate in SEPARATE_OUTLETS:
        if domain == separate or domain.endswith("." + separate):
            return separate
    labels = domain.split(".")
    keep = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SUFFIX_LABELS else 2
    return ".".join(labels[-keep:])


_REPUTABLE = {outlet(d) for d in REPUTABLE_DOMAINS}


@dataclass(frozen=True)
class Corroboration:
    """What backs a development: its distinct news outlets, whether one is reputable, and its
    official, research and data sources."""

    outlets: int
    reputable: bool
    backing: int

    @property
    def shown(self) -> bool:
        return self.outlets >= MIN_OUTLETS or self.reputable or self.backing > 0

    @property
    def single_outlet(self) -> bool:
        """Backed by one news outlet alone, shown only because it is reputable: the briefs hedge
        what rests on it."""
        return self.outlets == 1 and self.backing == 0


def corroboration(sources: Iterable[tuple[str, str]]) -> Corroboration:
    """A development's corroboration from its sources, as (collection, domain) pairs, one per
    document."""
    outlets: set[str] = set()
    backing = 0
    for collection, domain in sources:
        if collection == "news":
            outlets.add(outlet(domain))
        elif collection in BACKING_COLLECTIONS:
            backing += 1
    return Corroboration(
        outlets=len(outlets),
        reputable=not outlets.isdisjoint(_REPUTABLE),
        backing=backing,
    )


def corroborations(conn: sqlite3.Connection, run_id: str) -> dict[str, Corroboration]:
    """The corroboration of each of the run's developments, by id."""
    sources: dict[str, dict[str, tuple[str, str]]] = {}
    for r in conn.execute(
        "SELECT s.development_id, s.document_id, d.collection, d.domain "
        "FROM development_sources s JOIN source_documents d ON d.id = s.document_id "
        "WHERE s.run_id = ?",
        (run_id,),
    ):
        sources.setdefault(r[0], {})[r[1]] = (r[2], r[3])
    return {dev: corroboration(docs.values()) for dev, docs in sources.items()}


def shown_developments(conn: sqlite3.Connection, run_id: str) -> set[str]:
    """Ids of the run's developments that pass the gate."""
    return {dev for dev, c in corroborations(conn, run_id).items() if c.shown}
