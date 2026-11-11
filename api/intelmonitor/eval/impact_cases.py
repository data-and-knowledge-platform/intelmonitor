"""Cases for indirect impact by the fixed table (pipeline/impact.py; ticket 20, spec step 3a),
written before the code. Each case builds a small relationship store in memory from made-up links
between real seed ids, tags entities on one development at a level, adverse or not, and checks
which entities the table reaches, at what level, and through which source, link and strength. No
model call; the cases never read relationships.yaml or a stored run, so ratings changed by hand
cannot move them.

Every way the table logic could fail, and the cases that catch it:
- a cell of the table is wrong, or a High direct item gives High: `cell-*`, one per direct level,
  strength and INDIRECT_FROM_LOW setting;
- a link is read in the wrong direction: Singtel's event reaches DBS through "DBS depends on
  Singtel" at `to_from`, DBS's event reaches Singtel at `from_to` (`direction-*`);
- several links between the same pair: the strongest wins, then the larger share (a link with no
  share loses to one with a share), then the id (`several-links-*`);
- several tagged sources reach the same entity: the highest level wins, then the stronger link
  (`several-sources-*`);
- an entity tagged on the development gets an indirect row (`tagged-*`);
- a development not adverse for the tagged entity passes something, or the adverse flag of one
  tagged entity leaks to another (`not-adverse-*`);
- a Low direct item passes something by default, or more than Strong to Low when switched on
  (`cell-*`, `low-*`);
- anything reaches two hops, through an entity or an endpoint (`two-hops-*`);
- a region figure misses its member country, covers a place outside the region, carries an event
  out of the lender towards the region's members, or loses to a weaker direct link (`region-*`);
- belongs_to_sector carries something, or an endpoint or a group gets a row (`no-carry-*`);
- a topic tag passes something (`topic-*`);
- the run step reads the wrong level or adverse flag from development_subjects, stores the wrong
  row, or counts the two settings wrongly (`store`).

`uv run python -m intelmonitor.eval impact` runs them and writes impact_cases.json in the eval
directory."""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date

from intelmonitor.eval.common import EVAL_DIR
from intelmonitor.pipeline.impact import Reached, Tagged, indirect_level, reached, store_impacts
from intelmonitor.pipeline.params import PRIORITY_LABELS, Priority
from intelmonitor.relationships import (
    SCHEMA,
    Endpoint,
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

# The deck's table (slide 8) as decided in ticket 20 Q2: direct level, then strength, to the
# indirect level, None for nothing. The Low row is the one INDIRECT_FROM_LOW switches on.
TABLE: dict[int, dict[Strength, int | None]] = {
    HIGH: {"Strong": MEDIUM, "Moderate": MEDIUM, "Weak": LOW},
    MEDIUM: {"Strong": MEDIUM, "Moderate": LOW, "Weak": None},
    LOW: {"Strong": LOW, "Moderate": None, "Weak": None},
}


def link(
    from_: str,
    type_: RelType,
    to: str,
    from_to: Strength | None,
    to_from: Strength | None,
    weight: float | None = None,
) -> Relationship:
    """A made-up link; `from_to` and `to_from` None leaves it unrated, as belongs_to_sector is."""
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
                from_to=Rating(strength=from_to, reason="case"),
                to_from=Rating(strength=to_from, reason="case"),
            )
            if from_to is not None and to_from is not None
            else None,
        }
    )


GREATER_CHINA = Endpoint(
    id="greater-china",
    name="Greater China",
    kind="region",
    members=["hong-kong", "mainland-china", "taiwan", "macau"],
)
VENDOR = Endpoint(id="case-vendor", name="A vendor no page tracks", kind="tech_provider")


@dataclass(frozen=True)
class Expect:
    """The one row an entity must get."""

    level: Priority
    source: str
    link: str
    strength: Strength
    region: str | None = None


@dataclass(frozen=True)
class Case:
    id: str
    links: list[Relationship]
    tagged: dict[str, Tagged]
    expected: dict[str, Expect]
    # Every entity that gets a row must be in `expected`; `absent` names the ones the case is
    # about, for the report.
    absent: list[str] = field(default_factory=list[str])
    endpoints: list[Endpoint] = field(default_factory=list[Endpoint])
    from_low: bool = False
    reason: str = ""


def adverse(level: int) -> Tagged:
    return Tagged(level=level, adverse=True)


def calm(level: int) -> Tagged:
    return Tagged(level=level, adverse=False)


DBS_SINGTEL = "dbs:depends_on:singtel"


def cells() -> list[Case]:
    """One case per cell: Singtel's adverse event at each level reaches DBS through "DBS depends on
    Singtel" rated at each strength from Singtel's end, under both settings."""
    found: list[Case] = []
    for from_low in (False, True):
        for direct, row in TABLE.items():
            for strength, level in row.items():
                if direct == LOW and not from_low:
                    level = None
                expected = (
                    {"dbs": Expect(PRIORITY_LABELS[level], "singtel", DBS_SINGTEL, strength)}
                    if level is not None
                    else {}
                )
                found.append(
                    Case(
                        id=f"cell-{PRIORITY_LABELS[direct].lower()}-{strength.lower()}"
                        + ("-from-low" if from_low else ""),
                        links=[link("dbs", "depends_on", "singtel", "Weak", strength)],
                        tagged={"singtel": adverse(direct)},
                        expected=expected,
                        absent=[] if expected else ["dbs"],
                        from_low=from_low,
                        reason=f"{PRIORITY_LABELS[direct]} by {strength} gives "
                        f"{PRIORITY_LABELS[level] if level is not None else 'nothing'}",
                    )
                )
    return found


CASES: list[Case] = [
    *cells(),
    Case(
        id="direction-to-from",
        links=[link("dbs", "depends_on", "singtel", "Weak", "Strong")],
        tagged={"singtel": adverse(HIGH)},
        expected={"dbs": Expect("Medium", "singtel", DBS_SINGTEL, "Strong")},
        reason="Singtel's event reaches DBS through DBS's dependency, read at to_from",
    ),
    Case(
        id="direction-from-to",
        links=[link("dbs", "depends_on", "singtel", "Weak", "Strong")],
        tagged={"dbs": adverse(HIGH)},
        expected={"singtel": Expect("Low", "dbs", DBS_SINGTEL, "Weak")},
        reason="DBS's event reaches Singtel at from_to, Weak, so High gives Low",
    ),
    Case(
        id="direction-from-to-medium",
        links=[link("dbs", "depends_on", "singtel", "Weak", "Strong")],
        tagged={"dbs": adverse(MEDIUM)},
        expected={},
        absent=["singtel"],
        reason="from_to is Weak, and Medium by Weak gives nothing; to_from's Strong is not read",
    ),
    Case(
        id="several-links-strongest",
        links=[
            link("dbs", "depends_on", "singtel", "Weak", "Weak"),
            link("dbs", "partner_of", "singtel", "Weak", "Moderate"),
        ],
        tagged={"singtel": adverse(HIGH)},
        expected={"dbs": Expect("Medium", "singtel", "dbs:partner_of:singtel", "Moderate")},
        reason="The Moderate link beats the Weak one, which would give only Low",
    ),
    Case(
        id="several-links-larger-share",
        links=[
            link("dbs", "depends_on", "hong-kong", "Weak", "Strong"),
            link("dbs", "exposed_to", "hong-kong", "Weak", "Strong", weight=0.05),
            link("dbs", "holds_stake_in", "hong-kong", "Weak", "Strong", weight=0.122),
            link("dbs", "operates_in", "hong-kong", "Weak", "Strong"),
        ],
        tagged={"hong-kong": adverse(HIGH)},
        expected={"dbs": Expect("Medium", "hong-kong", "dbs:holds_stake_in:hong-kong", "Strong")},
        reason="Equal strength: the 12.2% share beats 5% and the links with no share, whose "
        "ids come first",
    ),
    Case(
        id="several-links-id",
        links=[
            link("dbs", "partner_of", "singtel", "Weak", "Moderate"),
            link("dbs", "depends_on", "singtel", "Weak", "Moderate"),
        ],
        tagged={"singtel": adverse(HIGH)},
        expected={"dbs": Expect("Medium", "singtel", DBS_SINGTEL, "Moderate")},
        reason="Equal strength, no share: the first id wins",
    ),
    Case(
        id="several-sources-highest-level",
        links=[
            link("dbs", "depends_on", "singtel", "Weak", "Weak"),
            link("dbs", "exposed_to", "hong-kong", "Weak", "Strong", weight=0.122),
        ],
        tagged={"singtel": adverse(HIGH), "hong-kong": adverse(MEDIUM)},
        expected={"dbs": Expect("Medium", "hong-kong", "dbs:exposed_to:hong-kong", "Strong")},
        reason="High by Weak gives Low; Medium by Strong gives Medium, which wins",
    ),
    Case(
        id="several-sources-level-before-strength",
        links=[
            link("dbs", "depends_on", "singtel", "Weak", "Moderate"),
            link("dbs", "exposed_to", "hong-kong", "Weak", "Strong", weight=0.122),
        ],
        tagged={"singtel": adverse(HIGH), "hong-kong": adverse(LOW)},
        expected={"dbs": Expect("Medium", "singtel", DBS_SINGTEL, "Moderate")},
        from_low=True,
        reason="Low by Strong gives Low; High by Moderate gives Medium, which wins over the "
        "stronger link",
    ),
    Case(
        id="several-sources-stronger-link",
        links=[
            link("dbs", "depends_on", "singtel", "Weak", "Moderate"),
            link("dbs", "exposed_to", "hong-kong", "Weak", "Strong", weight=0.122),
        ],
        tagged={"singtel": adverse(HIGH), "hong-kong": adverse(MEDIUM)},
        expected={"dbs": Expect("Medium", "hong-kong", "dbs:exposed_to:hong-kong", "Strong")},
        reason="Both give Medium: the Strong link beats the Moderate one",
    ),
    Case(
        id="tagged-no-row",
        links=[
            link("dbs", "depends_on", "singtel", "Strong", "Strong"),
            link("ocbc", "depends_on", "singtel", "Weak", "Strong"),
        ],
        tagged={"singtel": adverse(HIGH), "dbs": adverse(HIGH)},
        expected={"ocbc": Expect("Medium", "singtel", "ocbc:depends_on:singtel", "Strong")},
        absent=["dbs", "singtel"],
        reason="DBS and Singtel are both tagged, so neither gets a row from the other",
    ),
    Case(
        id="not-adverse-high",
        links=[link("dbs", "depends_on", "singtel", "Strong", "Strong")],
        tagged={"singtel": calm(HIGH)},
        expected={},
        absent=["dbs"],
        reason="A High development not adverse for Singtel passes nothing",
    ),
    Case(
        id="not-adverse-per-entity",
        links=[
            link("uob", "depends_on", "singtel", "Weak", "Strong"),
            link("ocbc", "holds_stake_in", "great-eastern", "Strong", "Weak", weight=0.88),
        ],
        tagged={"singtel": adverse(MEDIUM), "ocbc": calm(HIGH)},
        expected={"uob": Expect("Medium", "singtel", "uob:depends_on:singtel", "Strong")},
        absent=["great-eastern"],
        reason="Adverse for Singtel only: its neighbour gets a row, OCBC's does not",
    ),
    Case(
        id="low-switch-moderate",
        links=[link("dbs", "depends_on", "singtel", "Weak", "Moderate")],
        tagged={"singtel": adverse(LOW)},
        expected={},
        absent=["dbs"],
        from_low=True,
        reason="With the switch on, Low reaches only a Strong link",
    ),
    Case(
        id="two-hops-entity",
        links=[
            link("dbs", "depends_on", "singtel", "Weak", "Strong"),
            link("ocbc", "depends_on", "dbs", "Weak", "Strong"),
        ],
        tagged={"singtel": adverse(HIGH)},
        expected={"dbs": Expect("Medium", "singtel", DBS_SINGTEL, "Strong")},
        absent=["ocbc"],
        reason="Singtel reaches DBS; nothing goes on from DBS to OCBC",
    ),
    Case(
        id="two-hops-endpoint",
        links=[
            link("dbs", "depends_on", VENDOR.id, "Weak", "Strong"),
            link(VENDOR.id, "depends_on", "singtel", "Weak", "Strong"),
        ],
        endpoints=[VENDOR],
        tagged={"singtel": adverse(HIGH)},
        expected={},
        absent=["dbs", VENDOR.id],
        reason="An endpoint gets no row and passes nothing on",
    ),
    Case(
        id="region-member",
        links=[link("uob", "exposed_to", "greater-china", "Weak", "Moderate", weight=0.135)],
        endpoints=[GREATER_CHINA],
        tagged={"taiwan": adverse(HIGH)},
        expected={
            "uob": Expect(
                "Medium", "taiwan", "uob:exposed_to:greater-china", "Moderate", "greater-china"
            )
        },
        reason="UOB's Greater China figure covers Taiwan, read at to_from",
    ),
    Case(
        id="region-not-member",
        links=[link("uob", "exposed_to", "greater-china", "Weak", "Strong", weight=0.135)],
        endpoints=[GREATER_CHINA],
        tagged={"singapore": adverse(HIGH)},
        expected={},
        absent=["uob"],
        reason="Singapore is not in Greater China",
    ),
    Case(
        id="region-to-from-only",
        links=[link("uob", "exposed_to", "greater-china", "Strong", "Strong", weight=0.135)],
        endpoints=[GREATER_CHINA],
        tagged={"uob": adverse(HIGH)},
        expected={},
        absent=["taiwan", "hong-kong", "mainland-china", "greater-china"],
        reason="UOB's own event does not reach the region's members through its figure",
    ),
    Case(
        id="region-beats-weaker-direct",
        links=[
            link("dbs", "exposed_to", "hong-kong", "Weak", "Weak", weight=0.122),
            link("dbs", "exposed_to", "greater-china", "Weak", "Strong", weight=0.2),
        ],
        endpoints=[GREATER_CHINA],
        tagged={"hong-kong": adverse(HIGH)},
        expected={
            "dbs": Expect(
                "Medium", "hong-kong", "dbs:exposed_to:greater-china", "Strong", "greater-china"
            )
        },
        reason="The direct link is Weak (Low); the region figure is Strong (Medium) and wins",
    ),
    Case(
        id="no-carry-belongs-to-sector",
        links=[
            link("dbs", "belongs_to_sector", "ocbc", None, None),
            link("dbs", "belongs_to_sector", "singapore-banks", None, None),
        ],
        tagged={"dbs": adverse(HIGH), "ocbc": adverse(HIGH)},
        expected={},
        absent=["ocbc", "dbs", "singapore-banks"],
        reason="belongs_to_sector carries nothing, even between two entities",
    ),
    Case(
        id="no-carry-endpoint",
        links=[link("dbs", "depends_on", VENDOR.id, "Strong", "Strong")],
        endpoints=[VENDOR],
        tagged={"dbs": adverse(HIGH)},
        expected={},
        absent=[VENDOR.id],
        reason="An endpoint is not a seed entity and gets no row",
    ),
    Case(
        id="topic-passes-nothing",
        links=[link("dbs", "depends_on", "singtel", "Strong", "Strong")],
        tagged={"banking-lending": adverse(HIGH)},
        expected={},
        absent=["dbs", "singtel"],
        reason="A topic has no links",
    ),
]


def _store(links: list[Relationship], endpoints: list[Endpoint]) -> sqlite3.Connection:
    """An in-memory store with the run tables and these links."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(STORE_SCHEMA)
    conn.executescript(SCHEMA)
    store_relationships(conn, RelationshipsFile(relationships=links, endpoints=endpoints))
    return conn


def _row(r: Reached) -> dict[str, object]:
    return {
        "subject": r.subject_id,
        "level": PRIORITY_LABELS[r.level],
        "source": r.source_id,
        "link": r.relationship.id,
        "strength": r.strength,
        "region": r.region,
    }


def check(case: Case, rows: list[Reached]) -> list[str]:
    problems: list[str] = []
    entities = {e.id for e in load_subjects().entities}
    by_subject: dict[str, list[Reached]] = {}
    for r in rows:
        by_subject.setdefault(r.subject_id, []).append(r)
    for subject, found in by_subject.items():
        if len(found) > 1:
            problems.append(f"{subject} has {len(found)} rows")
        if subject not in entities:
            problems.append(f"{subject} is not a seed entity")
        if subject not in case.expected:
            problems.append(f"{subject} reached: {_row(found[0])}")
    for subject, want in case.expected.items():
        if subject not in by_subject:
            problems.append(f"{subject} not reached")
            continue
        got = by_subject[subject][0]
        found = Expect(
            PRIORITY_LABELS[got.level], got.source_id, got.relationship.id, got.strength, got.region
        )
        if found != want:
            problems.append(f"{subject}: {found}, expected {want}")
    problems += [f"{s} is tagged and reached" for s in case.tagged if s in by_subject]
    return problems


def check_table() -> list[str]:
    """indirect_level against TABLE, every cell under both settings; never High."""
    problems: list[str] = []
    for from_low in (False, True):
        for direct, row in TABLE.items():
            for strength, want in row.items():
                if direct == LOW and not from_low:
                    want = None
                got = indirect_level(direct, strength, from_low=from_low)
                if got != want:
                    problems.append(f"{direct} by {strength} (from_low {from_low}): {got}, {want}")
                if got == HIGH:
                    problems.append(f"{direct} by {strength} gives High")
    return problems


def check_store() -> list[str]:
    """store_impacts on a run with two developments: it reads each tag's level and adverse flag
    from development_subjects, stores the default setting's rows and counts both settings."""
    links = [
        link("dbs", "depends_on", "singtel", "Weak", "Strong"),
        link("uob", "depends_on", "singtel", "Weak", "Weak"),
        link("ocbc", "exposed_to", "greater-china", "Weak", "Moderate", weight=0.2),
    ]
    conn = _store(links, [GREATER_CHINA])
    tags = [
        # dev-a: Singtel High and adverse reaches DBS (Medium) and UOB (Low)
        ("dev-a", "singtel", HIGH, 1),
        # dev-b: Taiwan Low and adverse reaches OCBC only when Low passes (it is Moderate: no)
        ("dev-b", "taiwan", LOW, 1),
        # dev-c: Singtel Low and adverse reaches DBS as Low only with the switch on
        ("dev-c", "singtel", LOW, 1),
        # dev-d: Singtel High, not adverse: nothing
        ("dev-d", "singtel", HIGH, 0),
    ]
    conn.executemany(
        "INSERT INTO development_subjects (run_id, development_id, subject_id, confidence, "
        "source, role, adverse, priority_level) VALUES ('case', ?, ?, 1, 'claude', 'affected', "
        "?, ?)",
        [(dev, subject, adverse_flag, level) for dev, subject, level, adverse_flag in tags],
    )
    stats = store_impacts(conn, "case")
    stored = {
        tuple(r)
        for r in conn.execute(
            "SELECT development_id, subject_id, source_id, relationship_id, region, strength, "
            "level FROM development_impacts WHERE run_id = 'case'"
        )
    }
    want = {
        ("dev-a", "dbs", "singtel", DBS_SINGTEL, None, "Strong", MEDIUM),
        ("dev-a", "uob", "singtel", "uob:depends_on:singtel", None, "Weak", LOW),
    }
    problems: list[str] = []
    if stored != want:
        problems.append(f"stored {sorted(stored, key=str)}, expected {sorted(want, key=str)}")
    if (stats.without_low, stats.with_low) != (2, 3):
        problems.append(f"counts {stats.without_low} without Low, {stats.with_low} with, not 2, 3")
    if stats.impacts != len(stored):
        problems.append(f"stats say {stats.impacts} rows, {len(stored)} stored")
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
        conn = _store(case.links, case.endpoints)
        rows = reached(conn, case.tagged, from_low=case.from_low)
        problems = check(case, rows)
        rows_out = [_row(r) for r in rows]
        results.append(Result(case.id, not problems, problems, rows_out, case.reason))
    problems = check_store()
    results.append(Result("store", not problems, problems, [], "store_impacts on a made-up run"))
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    (EVAL_DIR / "impact_cases.json").write_text(json.dumps([r.__dict__ for r in results], indent=2))
    failed = [r for r in results if not r.ok]
    lines = [
        f"Impact cases: {len(results) - len(failed)} of {len(results)} pass, no model call",
        "",
        "| Case | Result | Reached | Problems |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {r.id} | {'pass' if r.ok else 'FAIL'} | "
        f"{', '.join(f'{x["subject"]} {x["level"]}' for x in r.rows) or 'none'} | "
        f"{'; '.join(r.problems)} |"
        for r in results
    ]
    print("\n".join(lines))
    if failed:
        raise SystemExit(1)
