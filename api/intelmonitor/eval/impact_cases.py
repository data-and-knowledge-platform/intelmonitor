"""Cases for inferred impact by the fixed table and the activated types (pipeline/impact.py; spec
step 8 of link-types-and-inferred-impact, ticket 06 Q10), written before the code. Each case
builds a small relationship store in memory from made-up links between real seed ids, names
entities on one development at a level, adverse or not, with the relationship types the
development activates for each, and checks every path the rules find: the entity reached, its
level, source, link, strength and region, and which path is its best. No model call; the cases
never read relationships.yaml or a stored run, so ratings changed by hand cannot move them.

Every way the rules could fail, and the cases that catch it:
- a cell of the table is wrong, High by Strong does not give High, or a Non-propagating direction
  passes something: `cell-*`, one per direct level, strength and INDIRECT_FROM_LOW setting, and
  `table`;
- a link whose type the development does not activate for the named entity passes something
  (`not-activated`), or of two links between a pair the stronger one counts though only the
  other is activated (`two-links-only-activated`);
- an entity whose types were not asked passes something (`not-asked`), or one asked whose
  answer was no type passes everything (`asked-none`);
- the gate reads the types of every entity named on the development, not those of the entity
  the link starts from (`types-per-entity`);
- a link is read in the wrong direction: Singtel's event reaches DBS through "DBS is a major
  customer of Singtel" at `to_from`, DBS's event reaches Singtel at `from_to` (`direction-*`);
- several valid paths to one entity: a path is dropped, paths are added together or raise each
  other, or the best is not one path chosen by level, then strength, then share (a link with no
  share loses to one with a share), then link id, then source id (`three-paths`,
  `two-medium-paths`, `several-links-*`, `several-sources-*`: one case for each tie-break, each
  with the later keys pointing the other way; two members of a region reaching one lender through
  its one region figure differ only by source id);
- an entity named on the development gets a row (`named-no-row`);
- the adverse switch: with INDIRECT_ADVERSE_ONLY off a development not adverse for the named
  entity passes at its level, with it on it passes nothing, and one entity's flag never leaks to
  another (`adverse-*`);
- the Low switch: a Low direct item passes nothing with INDIRECT_FROM_LOW off, and only Strong to
  Low with it on (`cell-*`, `low-*`);
- anything reaches two hops (`two-hops`);
- a region figure misses its member country, covers a place outside the region, carries an event
  out of the lender towards the region's members (it reaches the region entity itself, as any
  link's far end), or loses to a weaker direct link (`region-*`; Greater China is a region entity
  whose members are Hong Kong, Mainland China and Taiwan);
- belongs_to_sector carries something, or a group gets a row (`no-carry-*`);
- a topic passes something (`topic-*`);
- the run step reads the wrong level, adverse flag or types from development_subjects, stores
  the wrong rows, a wrong best flag or no reason, touches another run's rows, counts the four
  switch settings wrongly, miscounts the entities at Low whose types were asked (with none, the
  Low row's counts equal the counts without it and the run says it cannot count them), or sets
  or clears the run's impacts_recomputed mark wrongly, or miscounts the entities at Medium or
  High with their types and with none asked, by which `im impacts` tells a run from before the
  link_types stage (`store`);
- the link_types stage asks entities at Low with INDIRECT_FROM_LOW off, or does not ask them
  with it on, so the Low row passes nothing end to end; asks a topic or a development that names
  no entity; or, with the switch off, sends any prompt other than the Medium and High entities'
  (`stage`, with a stub in place of the model).

`uv run python -m intelmonitor.eval impact` runs them and writes impact_cases.json in the eval
directory."""

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from claude_agent_sdk import EffortLevel
from pydantic import BaseModel

from intelmonitor.eval.common import EVAL_DIR
from intelmonitor.pipeline.developments import (
    Development,
    DevelopmentSubject,
    DirectEntity,
    RunFinding,
    activate_link_types,
    link_types_prompt,
)
from intelmonitor.pipeline.impact import (
    Reached,
    Setting,
    Tagged,
    indirect_level,
    reached,
    store_impacts,
)
from intelmonitor.pipeline.inference import Inference
from intelmonitor.pipeline.params import PRIORITY_LABELS, Priority
from intelmonitor.pipeline.subjects import monitored_subjects
from intelmonitor.relationships import (
    SCHEMA,
    Rating,
    Relationship,
    RelationshipsFile,
    RelType,
    Strength,
    Transmission,
    store_relationships,
)
from intelmonitor.seed import load_subjects
from intelmonitor.store.db import SCHEMA as STORE_SCHEMA

HIGH, MEDIUM, LOW = 2, 1, 0

# The deck's table (16 Nov 2026, page 11) as decided in ticket 06 Q1: direct level, then strength,
# to the inferred level, None for nothing. The Low row is the one INDIRECT_FROM_LOW switches on.
TABLE: dict[int, dict[Strength, int | None]] = {
    HIGH: {"Strong": HIGH, "Moderate": MEDIUM, "Weak": LOW, "Non-propagating": None},
    MEDIUM: {"Strong": MEDIUM, "Moderate": LOW, "Weak": None, "Non-propagating": None},
    LOW: {"Strong": LOW, "Moderate": None, "Weak": None, "Non-propagating": None},
}


def link(
    from_: str,
    type_: RelType,
    to: str,
    from_to: Strength | None,
    to_from: Strength | None,
    weight: float | None = None,
) -> Relationship:
    """A made-up link; `from_to` and `to_from` None leaves it unrated."""
    return Relationship.model_validate(
        {
            "id": f"{from_}:{type_}:{to}",
            "from": from_,
            "to": to,
            "type": type_,
            "weight": weight,
            "evidence": "public",
            "last_confirmed": date(2026, 11, 1),
            "detail": "made up for a case",
            "reason": "made up for a case",
            "transmission": Transmission(
                from_to=Rating(strength=from_to, reason="case, from_to"),
                to_from=Rating(strength=to_from, reason="case, to_from"),
            )
            if from_to is not None and to_from is not None
            else None,
        }
    )


@dataclass(frozen=True)
class Expect:
    """One path a case must find."""

    subject: str
    level: Priority
    source: str
    link: str
    strength: Strength
    best: bool = True
    region: str | None = None


@dataclass(frozen=True)
class Case:
    id: str
    links: list[Relationship]
    tagged: dict[str, Tagged]
    # Every path found must be listed, and nothing else.
    expected: list[Expect]
    setting: Setting = Setting(adverse_only=False, from_low=False)
    reason: str = ""


def adverse(level: int, *types: RelType) -> Tagged:
    """Named at `level`, adverse for it, with these types activated."""
    return Tagged(level=level, adverse=True, types=frozenset(types))


def calm(level: int, *types: RelType) -> Tagged:
    """Named at `level`, not adverse for it, with these types activated."""
    return Tagged(level=level, adverse=False, types=frozenset(types))


def unasked(level: int) -> Tagged:
    """Named at `level`, adverse, with its types not asked (a topic, or a Low entity)."""
    return Tagged(level=level, adverse=True, types=None)


DBS_SINGTEL = "dbs:material_customer_of:singtel"
SINGTEL_DBS = "singtel:provides_connectivity_to:dbs"
BENEFITS = "dbs:provides_customer_benefits_with:singtel"
LOW_ON = Setting(adverse_only=False, from_low=True)
ADVERSE_ON = Setting(adverse_only=True, from_low=False)


def cells() -> list[Case]:
    """One case per cell: Singtel's event at each level, activating material_customer_of, reaches
    DBS through "DBS is a major customer of Singtel" rated at each strength from Singtel's end,
    under both Low settings."""
    found: list[Case] = []
    for from_low in (False, True):
        for direct, row in TABLE.items():
            for strength, level in row.items():
                if direct == LOW and not from_low:
                    level = None
                expected = (
                    [Expect("dbs", PRIORITY_LABELS[level], "singtel", DBS_SINGTEL, strength)]
                    if level is not None
                    else []
                )
                name = strength.lower().replace("-", "")
                found.append(
                    Case(
                        id=f"cell-{PRIORITY_LABELS[direct].lower()}-{name}"
                        + ("-from-low" if from_low else ""),
                        links=[link("dbs", "material_customer_of", "singtel", "Weak", strength)],
                        tagged={"singtel": adverse(direct, "material_customer_of")},
                        expected=expected,
                        setting=Setting(adverse_only=False, from_low=from_low),
                        reason=f"{PRIORITY_LABELS[direct]} by {strength} gives "
                        f"{PRIORITY_LABELS[level] if level is not None else 'nothing'}",
                    )
                )
    return found


CASES: list[Case] = [
    *cells(),
    Case(
        id="high-by-strong-gives-high",
        links=[link("singtel", "provides_connectivity_to", "dbs", "Strong", "Weak")],
        tagged={"singtel": adverse(HIGH, "provides_connectivity_to")},
        expected=[Expect("dbs", "High", "singtel", SINGTEL_DBS, "Strong")],
        reason="A Singtel connectivity outage at High reaches DBS through a Strong, activated "
        "link at one hop: inferred High, no further check",
    ),
    Case(
        id="non-propagating",
        links=[link("singtel", "provides_connectivity_to", "dbs", "Non-propagating", "Strong")],
        tagged={"singtel": adverse(HIGH, "provides_connectivity_to")},
        expected=[],
        reason="The direction from Singtel is Non-propagating: nothing, whatever the other "
        "direction says",
    ),
    Case(
        id="not-activated",
        links=[link("dbs", "material_customer_of", "singtel", "Strong", "Strong")],
        tagged={"singtel": adverse(HIGH, "provides_connectivity_to")},
        expected=[],
        reason="The development activates provides_connectivity_to, not material_customer_of",
    ),
    Case(
        id="two-links-only-activated",
        links=[
            link("dbs", "material_customer_of", "singtel", "Weak", "Strong"),
            link("singtel", "provides_connectivity_to", "dbs", "Weak", "Weak"),
        ],
        tagged={"singtel": adverse(HIGH, "provides_connectivity_to")},
        expected=[Expect("dbs", "Low", "singtel", SINGTEL_DBS, "Weak")],
        reason="Only the activated connectivity link counts, Weak, so Low; the Strong "
        "material_customer_of link is not activated and gives nothing",
    ),
    Case(
        id="not-asked",
        links=[link("singtel", "provides_connectivity_to", "dbs", "Strong", "Strong")],
        tagged={"singtel": unasked(HIGH)},
        expected=[],
        reason="Singtel's types were not asked: it passes nothing",
    ),
    Case(
        id="asked-none",
        links=[link("singtel", "provides_connectivity_to", "dbs", "Strong", "Strong")],
        tagged={"singtel": adverse(HIGH)},
        expected=[],
        reason="Singtel was asked and the development activates no type for it: it passes nothing",
    ),
    Case(
        id="types-per-entity",
        links=[
            link("singtel", "provides_connectivity_to", "dbs", "Strong", "Weak"),
            link("dbs", "material_customer_of", "singtel", "Weak", "Strong"),
            link("uob", "material_customer_of", "ocbc", "Strong", "Strong"),
        ],
        tagged={
            "singtel": adverse(HIGH, "provides_connectivity_to"),
            "ocbc": adverse(MEDIUM, "material_customer_of"),
        },
        expected=[
            Expect("dbs", "High", "singtel", SINGTEL_DBS, "Strong"),
            Expect("uob", "Medium", "ocbc", "uob:material_customer_of:ocbc", "Strong"),
        ],
        reason="Singtel activates only connectivity and OCBC only material_customer_of: DBS's "
        "custom of Singtel is not followed from Singtel, though OCBC activates its type",
    ),
    Case(
        id="three-paths",
        links=[
            link("dbs", "material_customer_of", "singtel", "Weak", "Strong"),
            link("singtel", "provides_connectivity_to", "dbs", "Moderate", "Weak"),
            link("dbs", "provides_customer_benefits_with", "singtel", "Weak", "Weak"),
        ],
        tagged={
            "singtel": adverse(
                HIGH,
                "material_customer_of",
                "provides_connectivity_to",
                "provides_customer_benefits_with",
            )
        },
        expected=[
            Expect("dbs", "High", "singtel", DBS_SINGTEL, "Strong"),
            Expect("dbs", "Medium", "singtel", SINGTEL_DBS, "Moderate", best=False),
            Expect("dbs", "Low", "singtel", BENEFITS, "Weak", best=False),
        ],
        reason="Three activated links reach DBS: three rows, the High one best",
    ),
    Case(
        id="two-medium-paths",
        links=[
            link("dbs", "material_customer_of", "singtel", "Weak", "Strong"),
            link("singtel", "provides_connectivity_to", "dbs", "Strong", "Weak"),
        ],
        tagged={"singtel": adverse(MEDIUM, "material_customer_of", "provides_connectivity_to")},
        expected=[
            Expect("dbs", "Medium", "singtel", DBS_SINGTEL, "Strong"),
            Expect("dbs", "Medium", "singtel", SINGTEL_DBS, "Strong", best=False),
        ],
        reason="Two Medium paths stay Medium: paths are never added together; equal level, "
        "strength and share, so the first link id is best",
    ),
    Case(
        id="direction-to-from",
        links=[link("dbs", "material_customer_of", "singtel", "Weak", "Strong")],
        tagged={"singtel": adverse(HIGH, "material_customer_of")},
        expected=[Expect("dbs", "High", "singtel", DBS_SINGTEL, "Strong")],
        reason="Singtel's event reaches DBS through DBS's custom, read at to_from",
    ),
    Case(
        id="direction-from-to",
        links=[link("dbs", "material_customer_of", "singtel", "Weak", "Strong")],
        tagged={"dbs": adverse(HIGH, "material_customer_of")},
        expected=[Expect("singtel", "Low", "dbs", DBS_SINGTEL, "Weak")],
        reason="DBS's event reaches Singtel at from_to, Weak, so High gives Low",
    ),
    Case(
        id="direction-from-to-medium",
        links=[link("dbs", "material_customer_of", "singtel", "Weak", "Strong")],
        tagged={"dbs": adverse(MEDIUM, "material_customer_of")},
        expected=[],
        reason="from_to is Weak, and Medium by Weak gives nothing; to_from's Strong is not read",
    ),
    Case(
        id="several-links-larger-share",
        links=[
            link("dbs", "material_customer_of", "hong-kong", "Weak", "Strong"),
            link("dbs", "exposed_to", "hong-kong", "Weak", "Strong", weight=0.05),
            link("dbs", "holds_stake_in", "hong-kong", "Weak", "Strong", weight=0.122),
        ],
        tagged={
            "hong-kong": adverse(MEDIUM, "material_customer_of", "exposed_to", "holds_stake_in")
        },
        expected=[
            Expect("dbs", "Medium", "hong-kong", "dbs:holds_stake_in:hong-kong", "Strong"),
            Expect("dbs", "Medium", "hong-kong", "dbs:exposed_to:hong-kong", "Strong", False),
            Expect(
                "dbs", "Medium", "hong-kong", "dbs:material_customer_of:hong-kong", "Strong", False
            ),
        ],
        reason="Equal level and strength: the 12.2% share is best, then 5%, then no share",
    ),
    Case(
        id="several-links-id",
        links=[
            link("dbs", "provides_customer_benefits_with", "singtel", "Weak", "Moderate"),
            link("dbs", "material_customer_of", "singtel", "Weak", "Moderate"),
        ],
        tagged={
            "singtel": adverse(HIGH, "material_customer_of", "provides_customer_benefits_with")
        },
        expected=[
            Expect("dbs", "Medium", "singtel", DBS_SINGTEL, "Moderate"),
            Expect("dbs", "Medium", "singtel", BENEFITS, "Moderate", best=False),
        ],
        reason="Equal level, strength and no share: the first link id is best",
    ),
    Case(
        id="several-sources-highest-level",
        links=[
            link("dbs", "material_customer_of", "singtel", "Weak", "Weak"),
            link("dbs", "exposed_to", "hong-kong", "Weak", "Strong", weight=0.122),
        ],
        tagged={
            "singtel": adverse(HIGH, "material_customer_of"),
            "hong-kong": adverse(MEDIUM, "exposed_to"),
        },
        expected=[
            Expect("dbs", "Medium", "hong-kong", "dbs:exposed_to:hong-kong", "Strong"),
            Expect("dbs", "Low", "singtel", DBS_SINGTEL, "Weak", best=False),
        ],
        reason="High by Weak gives Low; Medium by Strong gives Medium, which is best",
    ),
    Case(
        id="several-sources-level-before-strength",
        links=[
            link("dbs", "material_customer_of", "singtel", "Weak", "Moderate"),
            link("dbs", "exposed_to", "hong-kong", "Weak", "Strong", weight=0.122),
        ],
        tagged={
            "singtel": adverse(HIGH, "material_customer_of"),
            "hong-kong": adverse(LOW, "exposed_to"),
        },
        expected=[
            Expect("dbs", "Medium", "singtel", DBS_SINGTEL, "Moderate"),
            Expect("dbs", "Low", "hong-kong", "dbs:exposed_to:hong-kong", "Strong", False),
        ],
        setting=LOW_ON,
        reason="Low by Strong gives Low; High by Moderate gives Medium, which is best over the "
        "stronger link",
    ),
    Case(
        id="several-sources-strength-before-share",
        links=[
            link("dbs", "material_customer_of", "singtel", "Weak", "Strong", weight=0.05),
            link("dbs", "exposed_to", "hong-kong", "Weak", "Moderate", weight=0.3),
        ],
        tagged={
            "singtel": adverse(MEDIUM, "material_customer_of"),
            "hong-kong": adverse(HIGH, "exposed_to"),
        },
        expected=[
            Expect("dbs", "Medium", "singtel", "dbs:material_customer_of:singtel", "Strong"),
            Expect("dbs", "Medium", "hong-kong", "dbs:exposed_to:hong-kong", "Moderate", False),
        ],
        reason="Medium by Strong and High by Moderate both give Medium: the Strong link is best, "
        "though Hong Kong's has the larger share, the first link id and the first source id",
    ),
    Case(
        id="several-sources-link-id-before-source-id",
        links=[
            link("dbs", "material_customer_of", "hong-kong", "Weak", "Strong"),
            link("dbs", "distributes_products_for", "singtel", "Weak", "Strong"),
        ],
        tagged={
            "hong-kong": adverse(MEDIUM, "material_customer_of"),
            "singtel": adverse(MEDIUM, "distributes_products_for"),
        },
        expected=[
            Expect("dbs", "Medium", "singtel", "dbs:distributes_products_for:singtel", "Strong"),
            Expect(
                "dbs", "Medium", "hong-kong", "dbs:material_customer_of:hong-kong", "Strong", False
            ),
        ],
        reason="Equal level, strength and no share: Singtel's link id is first, so it is best, "
        "though Hong Kong's source id is first",
    ),
    Case(
        id="several-sources-source-id",
        links=[link("uob", "exposed_to", "greater-china", "Weak", "Strong", weight=0.135)],
        tagged={
            "taiwan": adverse(MEDIUM, "exposed_to"),
            "hong-kong": adverse(MEDIUM, "exposed_to"),
        },
        expected=[
            Expect(
                "uob",
                "Medium",
                "hong-kong",
                "uob:exposed_to:greater-china",
                "Strong",
                region="greater-china",
            ),
            Expect(
                "uob",
                "Medium",
                "taiwan",
                "uob:exposed_to:greater-china",
                "Strong",
                best=False,
                region="greater-china",
            ),
        ],
        reason="Two members of Greater China reach UOB through its one region figure: two paths "
        "on the same link, so the first source id, Hong Kong, is best",
    ),
    Case(
        id="named-no-row",
        links=[
            link("dbs", "material_customer_of", "singtel", "Strong", "Strong"),
            link("ocbc", "material_customer_of", "singtel", "Weak", "Strong"),
        ],
        tagged={
            "singtel": adverse(HIGH, "material_customer_of"),
            "dbs": adverse(HIGH, "material_customer_of"),
        },
        expected=[Expect("ocbc", "High", "singtel", "ocbc:material_customer_of:singtel", "Strong")],
        reason="DBS and Singtel are both named, so neither gets a row from the other",
    ),
    Case(
        id="adverse-off-passes",
        links=[link("singtel", "provides_connectivity_to", "dbs", "Strong", "Weak")],
        tagged={"singtel": calm(HIGH, "provides_connectivity_to")},
        expected=[Expect("dbs", "High", "singtel", SINGTEL_DBS, "Strong")],
        reason="With INDIRECT_ADVERSE_ONLY off, a High development not adverse for Singtel "
        "passes at its level",
    ),
    Case(
        id="adverse-on-blocks",
        links=[link("singtel", "provides_connectivity_to", "dbs", "Strong", "Weak")],
        tagged={"singtel": calm(HIGH, "provides_connectivity_to")},
        expected=[],
        setting=ADVERSE_ON,
        reason="With INDIRECT_ADVERSE_ONLY on, a development not adverse for Singtel passes "
        "nothing",
    ),
    Case(
        id="adverse-on-per-entity",
        links=[
            link("uob", "material_customer_of", "singtel", "Weak", "Strong"),
            link("ocbc", "holds_stake_in", "great-eastern", "Strong", "Weak", weight=0.88),
        ],
        tagged={
            "singtel": adverse(MEDIUM, "material_customer_of"),
            "ocbc": calm(HIGH, "holds_stake_in"),
        },
        expected=[Expect("uob", "Medium", "singtel", "uob:material_customer_of:singtel", "Strong")],
        setting=ADVERSE_ON,
        reason="With the switch on, adverse for Singtel only: its neighbour gets a row, OCBC's "
        "does not",
    ),
    Case(
        id="low-switch-off",
        links=[link("dbs", "material_customer_of", "singtel", "Weak", "Strong")],
        tagged={"singtel": adverse(LOW, "material_customer_of")},
        expected=[],
        reason="With INDIRECT_FROM_LOW off, a Low direct item passes nothing",
    ),
    Case(
        id="low-switch-on",
        links=[link("dbs", "material_customer_of", "singtel", "Weak", "Strong")],
        tagged={"singtel": adverse(LOW, "material_customer_of")},
        expected=[Expect("dbs", "Low", "singtel", DBS_SINGTEL, "Strong")],
        setting=LOW_ON,
        reason="With INDIRECT_FROM_LOW on, Low by Strong gives Low",
    ),
    Case(
        id="low-switch-on-moderate",
        links=[link("dbs", "material_customer_of", "singtel", "Weak", "Moderate")],
        tagged={"singtel": adverse(LOW, "material_customer_of")},
        expected=[],
        setting=LOW_ON,
        reason="With the switch on, Low reaches only a Strong link",
    ),
    Case(
        id="two-hops",
        links=[
            link("dbs", "material_customer_of", "singtel", "Weak", "Strong"),
            link("ocbc", "material_customer_of", "dbs", "Weak", "Strong"),
        ],
        tagged={"singtel": adverse(HIGH, "material_customer_of")},
        expected=[Expect("dbs", "High", "singtel", DBS_SINGTEL, "Strong")],
        reason="Singtel reaches DBS; nothing goes on from DBS to OCBC",
    ),
    Case(
        id="region-member",
        links=[link("uob", "exposed_to", "greater-china", "Weak", "Moderate", weight=0.135)],
        tagged={"taiwan": adverse(HIGH, "exposed_to")},
        expected=[
            Expect(
                "uob",
                "Medium",
                "taiwan",
                "uob:exposed_to:greater-china",
                "Moderate",
                region="greater-china",
            )
        ],
        reason="UOB's Greater China figure covers Taiwan, read at to_from",
    ),
    Case(
        id="region-not-member",
        links=[link("uob", "exposed_to", "greater-china", "Weak", "Strong", weight=0.135)],
        tagged={"singapore": adverse(HIGH, "exposed_to")},
        expected=[],
        reason="Singapore is not in Greater China",
    ),
    Case(
        id="region-to-from-only",
        links=[link("uob", "exposed_to", "greater-china", "Strong", "Strong", weight=0.135)],
        tagged={"uob": adverse(HIGH, "exposed_to")},
        expected=[Expect("greater-china", "High", "uob", "uob:exposed_to:greater-china", "Strong")],
        reason="UOB's own event reaches the region entity at from_to, but not the region's "
        "members through its figure",
    ),
    Case(
        id="region-beats-weaker-direct",
        links=[
            link("dbs", "exposed_to", "hong-kong", "Weak", "Weak", weight=0.122),
            link("dbs", "exposed_to", "greater-china", "Weak", "Strong", weight=0.2),
        ],
        tagged={"hong-kong": adverse(HIGH, "exposed_to")},
        expected=[
            Expect(
                "dbs",
                "High",
                "hong-kong",
                "dbs:exposed_to:greater-china",
                "Strong",
                region="greater-china",
            ),
            Expect("dbs", "Low", "hong-kong", "dbs:exposed_to:hong-kong", "Weak", best=False),
        ],
        reason="The direct link is Weak (Low); the region figure is Strong (High) and is best",
    ),
    Case(
        id="no-carry-belongs-to-sector",
        links=[
            link("dbs", "belongs_to_sector", "ocbc", "Strong", "Strong"),
            link("dbs", "belongs_to_sector", "singapore-banks", "Strong", "Strong"),
        ],
        tagged={"dbs": adverse(HIGH, "belongs_to_sector")},
        expected=[],
        reason="belongs_to_sector carries nothing, even activated and between two entities",
    ),
    Case(
        id="topic-passes-nothing",
        links=[link("dbs", "material_customer_of", "singtel", "Strong", "Strong")],
        tagged={"banking-lending": unasked(HIGH)},
        expected=[],
        reason="A topic is not asked and has no links",
    ),
]


def _store(links: list[Relationship]) -> sqlite3.Connection:
    """An in-memory store with the run tables and these links."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(STORE_SCHEMA)
    conn.executescript(SCHEMA)
    store_relationships(conn, RelationshipsFile(relationships=links))
    return conn


def _expect(r: Reached) -> Expect:
    return Expect(
        r.subject_id,
        PRIORITY_LABELS[r.level],
        r.source_id,
        r.relationship.id,
        r.strength,
        r.best,
        r.region,
    )


def check(case: Case, rows: list[Reached]) -> list[str]:
    problems: list[str] = []
    entities = {e.id for e in load_subjects().entities}
    found = [_expect(r) for r in rows]
    if len(set(found)) != len(found):
        problems.append("a path is listed twice")
    for missing in sorted(set(case.expected) - set(found), key=str):
        problems.append(f"missing {missing}")
    for extra in sorted(set(found) - set(case.expected), key=str):
        problems.append(f"not expected {extra}")
    for subject in {r.subject_id for r in rows}:
        best = sum(r.best for r in rows if r.subject_id == subject)
        if best != 1:
            problems.append(f"{subject} has {best} best paths")
        if subject not in entities:
            problems.append(f"{subject} is not a seed entity")
        if subject in case.tagged:
            problems.append(f"{subject} is named and reached")
    problems += [
        f"{r.subject_id} via {r.relationship.id} has no reason" for r in rows if not r.reason
    ]
    return problems


def check_table() -> list[str]:
    """indirect_level against TABLE, every cell under both Low settings; High only from High by
    Strong."""
    problems: list[str] = []
    for from_low in (False, True):
        for direct, row in TABLE.items():
            for strength, want in row.items():
                if direct == LOW and not from_low:
                    want = None
                got = indirect_level(direct, strength, from_low=from_low)
                if got != want:
                    problems.append(f"{direct} by {strength} (from_low {from_low}): {got}, {want}")
                if got == HIGH and (direct, strength) != (HIGH, "Strong"):
                    problems.append(f"{direct} by {strength} gives High")
    return problems


# Settings, as (adverse_only, from_low), to the paths store_impacts must count under each.
STORE_COUNTS: dict[Setting, int] = {
    Setting(False, False): 5,
    Setting(False, True): 6,
    Setting(True, False): 3,
    Setting(True, True): 4,
}
STORED_AT = "2026-11-23T00:00:00+00:00"


def check_store() -> list[str]:
    """store_impacts on a run of five developments: it reads each named entity's level, adverse
    flag and activated types from development_subjects, stores every path of the default setting
    with its best flag and reason, leaves another run's rows alone, counts the four settings, and
    sets the run's impacts_recomputed mark when given one and clears it when not, and counts the
    entities at Medium or High with their types and with none asked, topics apart."""
    links = [
        link("dbs", "material_customer_of", "singtel", "Weak", "Strong"),
        link("uob", "material_customer_of", "singtel", "Weak", "Weak"),
        link("ocbc", "exposed_to", "greater-china", "Weak", "Moderate", weight=0.2),
    ]
    conn = _store(links)
    customer = json.dumps(["material_customer_of"])
    tags = [
        # dev-a: Singtel High and adverse reaches DBS (High) and UOB (Low)
        ("dev-a", "singtel", HIGH, 1, customer),
        # dev-b: Taiwan Medium and adverse reaches OCBC as Low through the region figure
        ("dev-b", "taiwan", MEDIUM, 1, json.dumps(["exposed_to"])),
        # dev-c: Singtel Low reaches DBS as Low only with INDIRECT_FROM_LOW on. Its types are as
        # the link_types stage gives them with that switch on (`stage`); with it off a Low entity
        # is not asked, so a run's Low row can only be counted where a Low entity has types.
        ("dev-c", "singtel", LOW, 1, customer),
        # dev-d: Singtel High, not adverse: DBS and UOB unless INDIRECT_ADVERSE_ONLY is on
        ("dev-d", "singtel", HIGH, 0, customer),
        # dev-e: Singtel High and adverse, its types not asked: nothing
        ("dev-e", "singtel", HIGH, 1, None),
        # dev-e's topic, High and never asked, is not an entity whose types are missing
        ("dev-e", "banking-lending", HIGH, 1, None),
    ]
    conn.executemany(
        "INSERT INTO development_subjects (run_id, development_id, subject_id, confidence, "
        "source, role, adverse, priority_level, link_types) VALUES ('case', ?, ?, 1, 'claude', "
        "'affected', ?, ?, ?)",
        [(dev, subject, flag, level, types) for dev, subject, level, flag, types in tags],
    )
    conn.executemany(
        "INSERT INTO runs (id, as_of, status, documents, started_at) "
        "VALUES (?, ?, 'complete', 0, ?)",
        [(r, "2026-11-08", STORED_AT) for r in ("case", "other")],
    )
    old = (
        "INSERT INTO development_impacts (run_id, development_id, subject_id, source_id, "
        "relationship_id, strength, level, best, reason) "
        "VALUES (?, 'dev-z', 'mas', 'dbs', 'old', 'Strong', 1, 1, 'old')"
    )
    conn.execute(old, ("case",))
    conn.execute(old, ("other",))
    stats = store_impacts(conn, "case", recomputed_at=STORED_AT)
    stored = {
        tuple(r)
        for r in conn.execute(
            "SELECT development_id, subject_id, source_id, relationship_id, region, strength, "
            "level, best FROM development_impacts WHERE run_id = 'case'"
        )
    }
    uob = "uob:material_customer_of:singtel"
    region = "ocbc:exposed_to:greater-china"
    want = {
        ("dev-a", "dbs", "singtel", DBS_SINGTEL, None, "Strong", HIGH, 1),
        ("dev-a", "uob", "singtel", uob, None, "Weak", LOW, 1),
        ("dev-b", "ocbc", "taiwan", region, "greater-china", "Moderate", LOW, 1),
        ("dev-d", "dbs", "singtel", DBS_SINGTEL, None, "Strong", HIGH, 1),
        ("dev-d", "uob", "singtel", uob, None, "Weak", LOW, 1),
    }
    problems: list[str] = []
    if stored != want:
        problems.append(f"stored {sorted(stored, key=str)}, expected {sorted(want, key=str)}")
    if conn.execute("SELECT count(*) FROM development_impacts WHERE reason = ''").fetchone()[0]:
        problems.append("a stored path has no reason")
    if (
        conn.execute("SELECT count(*) FROM development_impacts WHERE run_id = 'other'").fetchone()[
            0
        ]
        != 1
    ):
        problems.append("another run's rows were touched")
    counts = {s: len(rows) for s, rows in stats.paths.items()}
    if counts != STORE_COUNTS:
        problems.append(f"counts {counts}, expected {STORE_COUNTS}")
    if stats.low_asked != 1:
        problems.append(f"{stats.low_asked} entities at Low with types, expected 1 (dev-c)")
    if (stats.typed, stats.unasked) != (3, 1):
        problems.append(
            f"entities at Medium or High: {stats.typed} with types, {stats.unasked} not asked; "
            "expected 3 (dev-a, dev-b, dev-d) and 1 (dev-e)"
        )
    marks = dict(conn.execute("SELECT id, impacts_recomputed FROM runs").fetchall())
    if marks != {"case": STORED_AT, "other": None}:
        problems.append(f"marks {marks}, expected case set and other unset")
    store_impacts(conn, "case")
    mark = conn.execute("SELECT impacts_recomputed FROM runs WHERE id = 'case'").fetchone()[0]
    if mark is not None:
        problems.append(f"a run's own impact step left the mark {mark}")
    return problems


class StubTypes(Inference):
    """Stands in for the model in the link_types stage: keeps each call's prompt by its input ref
    and answers with no type for any entity, so an entity asked gets [] and one not asked keeps
    None."""

    def __init__(self) -> None:
        super().__init__(sqlite3.connect(":memory:"), None)
        self.prompts: dict[str, str] = {}

    async def claude[T: BaseModel](
        self,
        stage: str,
        input_ref: str,
        prompt: str,
        schema: type[T],
        system: str,
        model: str | None = None,
        effort: EffortLevel | None = None,
        template: str | None = None,
    ) -> T:
        self.prompts[input_ref] = prompt
        return schema.model_validate({"entities": []})


# The stage's developments, each subject at its level; then the entities each setting of
# INDIRECT_FROM_LOW must ask about, by development. A topic and a development naming no entity
# are never asked.
STAGE_DEVELOPMENTS: dict[str, dict[str, int]] = {
    "dev-mixed": {"singtel": HIGH, "dbs": LOW, "banking-lending": MEDIUM},
    "dev-medium": {"uob": MEDIUM},
    "dev-low": {"ocbc": LOW},
    "dev-topic": {"banking-lending": HIGH},
    "dev-none": {},
}
STAGE_ASKED: dict[bool, dict[str, list[str]]] = {
    False: {"dev-mixed": ["singtel"], "dev-medium": ["uob"]},
    True: {"dev-mixed": ["dbs", "singtel"], "dev-medium": ["uob"], "dev-low": ["ocbc"]},
}


def _development(dev_id: str, levels: dict[str, int]) -> Development:
    """A made-up development of one finding, naming these subjects at these levels."""
    finding = RunFinding(
        id=f"{dev_id}-finding",
        document_id=f"{dev_id}-document",
        statement=f"{dev_id} happened, made up for a case.",
        category="operations",
        event_date=None,
        subjects=tuple(levels),
        roles={},
        promotional=False,
        price_move=False,
        first_quote="made up for a case",
        published=date(2026, 11, 1),
        collection="news",
        domain="example.com",
        title=f"{dev_id}, a case",
        stage=None,
        rule_level=None,
        rule_id=None,
    )
    dev = Development(dev_id, [finding])
    for sid, level in levels.items():
        s = DevelopmentSubject(
            role="affected", adverse=True, tone="negative", confidence=1.0, source="claude"
        )
        s.set_priority(level, f"{PRIORITY_LABELS[level]} for a case.")
        dev.subjects[sid] = s
    return dev


def check_stage() -> list[str]:
    """activate_link_types under both settings of INDIRECT_FROM_LOW, with StubTypes for the
    model: the developments asked, the entities each prompt lists (the prompt is the one
    link_types_prompt makes for exactly those entities, so with the switch off it is the one the
    stage sent before the switch reached it, and so is its cache key), the entities given types,
    and the counts the run prints."""
    problems: list[str] = []
    names = monitored_subjects()
    for from_low, want in STAGE_ASKED.items():
        devs = [_development(d, levels) for d, levels in STAGE_DEVELOPMENTS.items()]
        inf = StubTypes()
        counts = asyncio.run(activate_link_types(inf, devs, from_low=from_low))
        where = f"INDIRECT_FROM_LOW {from_low}"
        expected_prompts = {
            f"dev:{d.id}:1": link_types_prompt(
                d,
                [
                    DirectEntity(
                        e,
                        names[e].name,
                        d.subjects[e].priority_level,
                        d.subjects[e].priority_reason,
                    )
                    for e in want[d.id]
                ],
            )
            for d in devs
            if d.id in want
        }
        if sorted(inf.prompts) != sorted(expected_prompts):
            problems.append(
                f"{where}: asked {sorted(inf.prompts)}, expected {sorted(expected_prompts)}"
            )
        problems += [
            f"{where}: {ref} sent another prompt"
            for ref, prompt in expected_prompts.items()
            if ref in inf.prompts and inf.prompts[ref] != prompt
        ]
        for d in devs:
            for sid, s in d.subjects.items():
                asked = sid in want.get(d.id, [])
                if (s.link_types is not None) != asked:
                    problems.append(
                        f"{where}: {d.id} {sid} has types {s.link_types}, "
                        f"{'asked' if asked else 'not asked'}"
                    )
        if counts != (len(want), 0):
            problems.append(f"{where}: counts {counts}, expected ({len(want)}, 0)")
    return problems


@dataclass(frozen=True)
class Result:
    id: str
    ok: bool
    problems: list[str]
    rows: list[dict[str, object]]
    reason: str


def run() -> None:
    results = [Result("table", not (p := check_table()), p, [], "indirect_level by cell")]
    for case in CASES:
        conn = _store(case.links)
        rows = reached(
            conn,
            case.tagged,
            from_low=case.setting.from_low,
            adverse_only=case.setting.adverse_only,
        )
        problems = check(case, rows)
        rows_out = [_expect(r).__dict__ for r in rows]
        results.append(Result(case.id, not problems, problems, rows_out, case.reason))
    steps = [
        ("store", check_store, "store_impacts on a made-up run"),
        ("stage", check_stage, "the link_types stage's entities asked, under both Low settings"),
    ]
    for id_, check_step, reason in steps:
        try:
            problems = check_step()
        except Exception as error:  # a step that cannot do what the case asks of it
            problems = [f"{type(error).__name__}: {error}"]
        results.append(Result(id_, not problems, problems, [], reason))
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    (EVAL_DIR / "impact_cases.json").write_text(json.dumps([r.__dict__ for r in results], indent=2))
    failed = [r for r in results if not r.ok]
    lines = [
        f"Impact cases: {len(results) - len(failed)} of {len(results)} pass, no model call",
        "",
        "| Case | Result | Paths | Problems |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {r.id} | {'pass' if r.ok else 'FAIL'} | "
        + (
            ", ".join(f"{x['subject']} {x['level']}{' best' if x['best'] else ''}" for x in r.rows)
            or "none"
        )
        + f" | {'; '.join(r.problems)} |"
        for r in results
    ]
    print("\n".join(lines))
    if failed:
        raise SystemExit(1)
