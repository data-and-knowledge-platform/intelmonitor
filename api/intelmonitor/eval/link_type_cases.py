"""Cases for the activated relationship types (spec step 7 of link-types-and-inferred-impact,
ticket 05 Q7), written before the prompt from the ten examples of the deck of 16 Nov 2026, page 9.
Each example is a made-up development with one direct entity, its level and priority reason, the
types the link_types call must pick for that entity and the types it must not. Where the deck
says "potentially" a type, the case neither requires nor forbids it.

The engine, maintenance and KrisFlyer examples are about Singapore Airlines and SIA Engineering,
which are not seed entities; the stage reads an entity's name from the call, so a case may name
any entity.

The cases run through the stage's own call (`select_link_types`), one Claude call each, in their
own store, eval-runs/.../db/link_types.db, so the app's store is never touched and a repeat run
replays the recorded calls: `uv run python -m intelmonitor.eval link-types [medium|high]`, at the
stage's effort by default. Writes link_type_cases.json in the eval directory."""

import json
from datetime import date

from claude_agent_sdk import EffortLevel
from pydantic import BaseModel, Field

from intelmonitor.eval.common import EVAL_DIR
from intelmonitor.pipeline.developments import (
    Development,
    DirectEntity,
    RunFinding,
    select_link_types,
)
from intelmonitor.pipeline.extract import FindingRole
from intelmonitor.pipeline.inference import Inference
from intelmonitor.relationships import RelType
from intelmonitor.store.db import connect

STORE = EVAL_DIR / "db" / "link_types.db"


class LinkTypeCase(BaseModel):
    id: str
    example: str = Field(description="The deck's finding, page 9")
    entity: DirectEntity
    title: str
    statement: str
    quote: str
    must: list[RelType] = Field(description="Types the call must pick for the entity")
    must_not: list[RelType] = Field(description="Types the call must not pick for the entity")


SINGTEL = ("singtel", "Singtel")
MONEYSMART = ("moneysmart", "MoneySmart")
SIA = ("singapore-airlines", "Singapore Airlines")


def _entity(who: tuple[str, str], level: int, reason: str) -> DirectEntity:
    return DirectEntity(id=who[0], name=who[1], level=level, reason=reason)


CASES = [
    LinkTypeCase(
        id="singtel-connectivity-outage",
        example="Singtel enterprise-connectivity outage",
        entity=_entity(
            SINGTEL,
            2,
            "An outage of Singtel's enterprise network in Singapore cut off the business "
            "customers that depend on it for connectivity.",
        ),
        title="Singtel enterprise network outage disrupts businesses for nine hours",
        statement="A fault in Singtel's enterprise network cut connectivity for about nine hours "
        "on Nov 3 to business customers in Singapore that rely on its leased lines and "
        "corporate networks, including bank branches and payment terminals.",
        quote="Enterprise customers on our leased-line and managed network services experienced "
        "a loss of connectivity from 6am to 3pm, Singtel said.",
        must=["provides_connectivity_to"],
        must_not=[
            "provides_customer_benefits_with",
            "distributes_products_for",
            "shares_or_processes_data_with",
            "lends_to",
        ],
    ),
    LinkTypeCase(
        id="singtel-advertising-fine",
        example="Singtel consumer-advertising fine",
        entity=_entity(
            SINGTEL,
            1,
            "A record fine and refunds over misleading consumer advertising will cost Singtel "
            "about S$120 million, a hit to its earnings.",
        ),
        title="Singtel fined over misleading mobile plan advertising",
        statement="The Competition and Consumer Commission of Singapore fined Singtel S$45 "
        "million on Nov 4 for advertising mobile plans as unlimited when speeds were capped, and "
        "ordered refunds that Singtel estimates at S$75 million, a charge it will take against "
        "this year's profit.",
        quote="The fine and refunds, about S$120 million in all, will be taken as a one-off "
        "charge in the second half, Singtel said.",
        must=["holds_stake_in", "invests_in"],
        must_not=[
            "provides_connectivity_to",
            "processes_transactions_for",
            "provides_customer_benefits_with",
        ],
    ),
    LinkTypeCase(
        id="moneysmart-data-breach",
        example="MoneySmart bank-application data breach",
        entity=_entity(
            MONEYSMART,
            2,
            "Applicants' bank-application data held by MoneySmart was taken in a breach in "
            "Singapore.",
        ),
        title="MoneySmart breach exposes bank card and loan applicants' data",
        statement="Hackers gained access to MoneySmart's systems and took the bank-application "
        "data of about 80,000 people in Singapore who applied for credit cards and personal "
        "loans through its site, including NRIC numbers, income documents and the banks applied "
        "to.",
        quote="The data accessed includes the information applicants submitted to be passed on "
        "to their chosen banks, MoneySmart said.",
        must=["shares_or_processes_data_with"],
        must_not=[
            "provides_connectivity_to",
            "processes_transactions_for",
            "material_supplier_to",
        ],
    ),
    LinkTypeCase(
        id="moneysmart-misleading-comparison",
        example="MoneySmart misleading product comparison",
        entity=_entity(
            MONEYSMART,
            1,
            "MoneySmart's comparisons of bank products in Singapore were found misleading and "
            "it must change how it presents them.",
        ),
        title="MoneySmart told to fix misleading credit card comparisons",
        statement="The Competition and Consumer Commission of Singapore found that MoneySmart's "
        "credit card and savings account comparison pages ranked banks' products by the "
        "referral fees the banks paid while presenting the rankings as independent, and ordered "
        "it to change them.",
        quote="Consumers were led to believe the rankings reflected the products' merits, the "
        "commission said.",
        must=["distributes_products_for"],
        must_not=[
            "shares_or_processes_data_with",
            "provides_connectivity_to",
            "processes_transactions_for",
        ],
    ),
    LinkTypeCase(
        id="payment-processor-outage",
        example="Payment-processor outage",
        entity=_entity(
            ("nets", "NETS"),
            2,
            "A NETS outage stopped card and QR payments across Singapore for six hours.",
        ),
        title="NETS outage halts card and QR payments for six hours",
        statement="A failure in NETS's transaction switch stopped NETS card, contactless and QR "
        "payments at merchants across Singapore for about six hours on Nov 5, and banks' "
        "customers could not pay with their NETS-enabled ATM cards.",
        quote="All NETS transactions failed to be processed between 11am and 5pm, NETS said.",
        must=["processes_transactions_for"],
        must_not=[
            "provides_connectivity_to",
            "distributes_products_for",
            "provides_customer_benefits_with",
        ],
    ),
    LinkTypeCase(
        id="engine-defect-isolated",
        example="Isolated engine defect affecting a specific engine model",
        entity=_entity(
            SIA,
            1,
            "A defect in one engine model grounded four of Singapore Airlines' aircraft for "
            "inspections.",
        ),
        title="SIA grounds four 787s over defect in one engine model",
        statement="Singapore Airlines took four Boeing 787-10s out of service on Nov 6 after "
        "its engine maker found a defect in turbine blades of one engine model; the rest of its "
        "fleet uses other engines and is not affected.",
        quote="The inspections apply only to aircraft fitted with the affected engine model, "
        "the airline said.",
        must=["material_supplier_to"],
        must_not=[
            "provides_customer_benefits_with",
            "lends_to",
            "processes_transactions_for",
        ],
    ),
    LinkTypeCase(
        id="engine-defect-fleet-wide",
        example="Fleet-wide engine defect causing extensive grounding",
        entity=_entity(
            SIA,
            2,
            "A fleet-wide engine defect grounded over a third of Singapore Airlines' widebody "
            "fleet, with large losses expected.",
        ),
        title="SIA grounds 40 widebody aircraft over fleet-wide engine defect",
        statement="Singapore Airlines grounded 40 widebody aircraft, over a third of its fleet, "
        "on Nov 7 after a defect was found across the engines that power them; it cancelled "
        "hundreds of flights, expects a loss for the half year, will claim on its insurance and "
        "said its engineering arm faces months of engine checks.",
        quote="We expect the groundings to last at least three months and to have a material "
        "impact on our results, the airline said.",
        must=["material_supplier_to"],
        must_not=[
            "provides_customer_benefits_with",
            "processes_transactions_for",
            "provides_connectivity_to",
        ],
    ),
    LinkTypeCase(
        id="maintenance-failure",
        example="Aircraft-maintenance failure",
        entity=_entity(
            ("sia-engineering", "SIA Engineering"),
            2,
            "Regulators found SIA Engineering skipped required checks on aircraft it maintains "
            "for airlines at Changi.",
        ),
        title="SIA Engineering missed required checks on airlines' aircraft",
        statement="The Civil Aviation Authority of Singapore found that SIA Engineering skipped "
        "required landing-gear inspections on aircraft it maintains for several airlines at "
        "Changi, and ordered the aircraft re-inspected before they fly again.",
        quote="Airlines whose aircraft were serviced in the period must have them re-inspected, "
        "the authority said.",
        must=["provides_business_service_to"],
        must_not=[
            "provides_customer_benefits_with",
            "processes_transactions_for",
            "shares_or_processes_data_with",
        ],
    ),
    LinkTypeCase(
        id="krisflyer-redemption-outage",
        example="KrisFlyer redemption outage",
        entity=_entity(
            SIA,
            1,
            "KrisFlyer members could not redeem miles for two days after a system failure.",
        ),
        title="KrisFlyer members unable to redeem miles for two days",
        statement="A system failure stopped KrisFlyer members from redeeming miles for flights "
        "and partner rewards for about 48 hours from Nov 8, Singapore Airlines said.",
        quote="Members were unable to redeem miles on any channel during the disruption, the "
        "airline said.",
        must=["provides_customer_benefits_with"],
        must_not=["material_supplier_to", "lends_to", "provides_connectivity_to"],
    ),
    LinkTypeCase(
        id="flight-disruption",
        example="General flight disruption unrelated to KrisFlyer",
        entity=_entity(
            SIA,
            1,
            "Storms and an air traffic control fault delayed and cancelled Singapore Airlines "
            "flights at Changi for a day.",
        ),
        title="Storms and air traffic fault delay dozens of SIA flights",
        statement="Thunderstorms and an air traffic control system fault at Changi Airport on "
        "Nov 9 delayed 60 Singapore Airlines flights and led it to cancel 12.",
        quote="Flights are operating normally again, the airline said on Nov 10.",
        must=[],
        must_not=["provides_customer_benefits_with", "material_supplier_to", "lends_to"],
    ),
]


def development(case: LinkTypeCase) -> Development:
    """The case as a development of one finding from one made-up news document."""
    finding = RunFinding(
        id=f"case-{case.id}",
        document_id=f"case-{case.id}",
        statement=case.statement,
        category="incident",
        event_date=None,
        subjects=(case.entity.id,),
        roles={case.entity.id: FindingRole(role="affected", adverse=True, tone="negative")},
        promotional=False,
        price_move=False,
        first_quote=case.quote,
        published=date(2026, 11, 10),
        collection="news",
        domain="example.com",
        title=case.title,
        stage=None,
        rule_level=None,
        rule_id=None,
    )
    return Development(id=finding.id, findings=[finding])


class LinkTypeCaseResult(BaseModel):
    id: str
    ok: bool
    picked: list[RelType] | None = Field(description="None when the call was refused")
    missing: list[RelType]
    forbidden: list[RelType]


def check(case: LinkTypeCase, picked: list[RelType] | None) -> LinkTypeCaseResult:
    got: set[RelType] = set(picked or [])
    missing: list[RelType] = [t for t in case.must if t not in got]
    forbidden: list[RelType] = [t for t in case.must_not if t in got]
    ok = picked is not None and not missing and not forbidden
    return LinkTypeCaseResult(
        id=case.id, ok=ok, picked=picked, missing=missing, forbidden=forbidden
    )


async def run(effort: EffortLevel | None = None) -> None:
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect(STORE)
    before = conn.execute("SELECT coalesce(max(id), 0) FROM inference_records").fetchone()[0]
    inf = Inference(conn, "eval-link-types")
    results: list[LinkTypeCaseResult] = []
    for case in CASES:
        reply = await select_link_types(inf, development(case), [case.entity], effort=effort)
        results.append(check(case, None if reply is None else reply[case.entity.id]))
    cost, output_tokens = conn.execute(
        "SELECT coalesce(sum(cost_usd), 0), coalesce(avg(output_tokens), 0) "
        "FROM inference_records WHERE id > ?",
        (before,),
    ).fetchone()
    (EVAL_DIR / "link_type_cases.json").write_text(
        json.dumps([r.model_dump() for r in results], indent=2)
    )
    passed = sum(r.ok for r in results)
    lines = [
        f"Link type cases, effort {effort or 'the stage default'}: {passed} of {len(results)} "
        f"pass ({inf.calls} Claude calls, {inf.cache_hits} replayed, ${cost:.4f} for the calls "
        f"made, mean {output_tokens:.0f} output tokens, measured; versions "
        f"{', '.join(sorted(inf.versions))})",
        "",
        "| Case | Result | Picked | Missing | Must not, picked |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {r.id} | {'pass' if r.ok else 'FAIL'} | "
        f"{'refused' if r.picked is None else ', '.join(r.picked) or 'none'} | "
        f"{', '.join(r.missing)} | {', '.join(r.forbidden)} |"
        for r in results
    ]
    print("\n".join(lines))
