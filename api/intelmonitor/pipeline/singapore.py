"""How each tracked institution links to Singapore's financial system, for priority scoring
(pipeline/developments.py). Code decides it from the seed so Jev does not have to know that ez-link
runs on Azure:

- A Singapore financial institution is an entity whose home is Singapore, other than a tech
  provider (subjects.yaml).
- Any other institution is strongly linked when relationships.yaml records a Singapore presence
  (licensed by MAS, operating in Singapore, owning a Singapore institution) or a Singapore
  institution owning it, depending on it for a service, or taking part in it as a payment system.
  A loan exposure (exposed_to) is never a strong link, and assumed links do not count.
- Countries and sectors are places, not institutions: they get no link.
"""

from collections import defaultdict
from dataclasses import dataclass
from functools import cache
from typing import Literal

from intelmonitor.relationships import Relationship, load_relationships
from intelmonitor.seed import EntityKind, load_subjects

LinkKind = Literal["singapore_fi", "linked", "none"]

SINGAPORE = "singapore"
NOT_INSTITUTIONS: tuple[EntityKind, ...] = ("country", "sector")
# An ownership stake at least this large is a strong link; a passive holding is not.
MIN_STAKE = 0.2
# Names listed per fact before "and N more".
MAX_NAMES = 4


@dataclass(frozen=True)
class SingaporeLink:
    kind: LinkKind
    facts: tuple[str, ...] = ()

    def describe(self) -> str:
        """The link in the words Jev reads."""
        if self.kind == "singapore_fi":
            return "a financial institution in Singapore"
        if self.kind == "linked":
            return "strongly linked to Singapore: " + "; ".join(self.facts)
        return "no strong link to Singapore"


def _names(names: list[str]) -> str:
    shown = sorted(set(names))
    more = len(shown) - MAX_NAMES
    head = shown[:MAX_NAMES]
    if more > 0:
        return f"{', '.join(head)} and {more} more"
    return head[0] if len(head) == 1 else f"{', '.join(head[:-1])} and {head[-1]}"


@cache
def singapore_links() -> dict[str, SingaporeLink]:
    """Each institution entity's link to Singapore, by id."""
    seed = load_subjects()
    data = load_relationships()
    names = {e.id: e.name for e in data.endpoints} | {e.id: e.name for e in seed.entities}
    institutions = {e.id: e for e in seed.entities if e.kind not in NOT_INSTITUTIONS}
    singapore_fi = {
        i for i, e in institutions.items() if e.home == SINGAPORE and e.kind != "tech_provider"
    }
    rels = [r for r in data.relationships if r.evidence != "assumed"]
    # Ids with a Singapore presence of their own: a Singapore institution, or licensed or
    # operating there (endpoints such as a group's Singapore unit included).
    present = singapore_fi | {
        r.from_
        for r in rels
        if (r.type == "supervised_by" and r.to == "mas")
        or (r.type == "operates_in" and r.to == SINGAPORE)
    }

    def stake(r: Relationship) -> bool:
        return r.type in ("owns", "subsidiary_of") or (
            r.type == "shareholder_of" and (r.weight or 0) >= MIN_STAKE
        )

    facts: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for r in rels:
        a, b = r.from_, r.to
        if r.type == "supervised_by" and b == "mas":
            facts[a]["licensed by MAS"].append("")
        elif r.type == "operates_in" and b == SINGAPORE:
            facts[a]["operates in Singapore"].append("")
        elif stake(r):
            # owns and shareholder_of run owner to owned; subsidiary_of runs owned to owner.
            owner, owned = (b, a) if r.type == "subsidiary_of" else (a, b)
            if owned in present:
                facts[owner]["owns {}"].append(names.get(owned, owned))
            if owner in singapore_fi:
                facts[owned]["owned by {}"].append(names.get(owner, owner))
        elif r.type == "depends_on" and a in present:
            facts[b]["relied on by {}"].append(names.get(a, a))
        elif r.type == "participates_in":
            if a in singapore_fi:
                facts[b]["used by {}"].append(names.get(a, a))
            if b in singapore_fi:
                facts[a]["takes part in {}"].append(names.get(b, b))

    links: dict[str, SingaporeLink] = {}
    for i in institutions:
        if i in singapore_fi:
            links[i] = SingaporeLink("singapore_fi")
        elif found := facts.get(i):
            links[i] = SingaporeLink(
                "linked",
                tuple(f.format(_names(n)) if "{}" in f else f for f, n in found.items()),
            )
        else:
            links[i] = SingaporeLink("none")
    return links


NO_LINK = SingaporeLink("none")
