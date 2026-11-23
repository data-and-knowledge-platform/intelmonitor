"""Cases for the activated relationship types (spec step 7 of link-types-and-inferred-impact,
ticket 05 Q7), written before the prompt from the ten examples of the deck of 16 Nov 2026, page 9.
Each example is a made-up development with one direct entity, its level and priority reason, and
the types its row names. The row lists the types that apply, so a case requires the types it names
outright, allows those it names as "potentially", and forbids the rest of the 22: a reply that adds
a type because the entity holds that relationship fails (page 10: "Do not include a type merely
because the relationship exists"). The general flight disruption's row names no type that applies,
so its case forbids all 22.

The engine, maintenance and KrisFlyer examples are about Singapore Airlines and SIA Engineering,
which are not seed entities; the stage reads an entity's name from the call, so a case may name
any entity.

OUR_CASES are ours, not from the deck's page 9 (added 23 Nov 2026, after the step 8 preview made
hundreds of firms High through a country's operates_in or a regulator's regulated_by). Each has a
direct entity that is itself a jurisdiction, regulator or exchange, with the wording of a real
development of the 8 Nov run: one finding's statement, its source's title and quote, and the
entity's level and priority reason. A structural type is activated for such an entity only when
the finding is a measure, condition or disruption of it that bears on the firms operating in it,
regulated by it or listed on it as a class; not when the finding is about one firm, or a local
event that only happened there. These cases name what must not be picked (`must_not`) and may
require one of several types (`one_of`); the other types are free.

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
from intelmonitor.relationships import LINK_TYPES, REL_TYPES, RelType
from intelmonitor.store.db import connect

STORE = EVAL_DIR / "db" / "link_types.db"


class LinkTypeCase(BaseModel):
    id: str
    example: str | None = Field(description="The deck's finding, page 9; None for our cases")
    entity: DirectEntity
    title: str
    statement: str
    quote: str
    must: list[RelType] = Field(description="Types the call must pick for the entity")
    may: list[RelType] = Field(
        default=[], description="Types the deck says the finding potentially activates"
    )
    one_of: list[RelType] = Field(
        default=[], description="Our cases: at least one of these must be picked"
    )
    must_not: list[RelType] | None = Field(
        default=None,
        description="Our cases: the types that must not be picked; None for the deck's",
    )

    def forbidden(self) -> list[RelType]:
        """The types the call must not pick: `must_not`, or for the deck's cases every type the
        row does not name."""
        if self.must_not is not None:
            return self.must_not
        return [t for t in REL_TYPES if t not in self.must and t not in self.may]


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
        may=["provides_business_service_to"],
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
        may=["provides_business_service_to", "insures", "holds_stake_in", "invests_in", "lends_to"],
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
    ),
]

# The deck's third group: regulated_by, operates_in, exposed_to, member_of, participates_in,
# listed_on, belongs_to_sector.
STRUCTURAL: list[RelType] = [
    t for t, lt in LINK_TYPES.items() if lt.group == "Structural and institutional"
]
SINGAPORE = ("singapore", "Singapore")
MAS = ("mas", "MAS")
SGX = ("sgx", "SGX")

# Ours, not from the deck's page 9: see the module's docstring. From the 8 Nov run of the
# worktree's store, its development ids in the case ids' comments.
OUR_CASES = [
    # 657dc3f4bb3f2032-0
    LinkTypeCase(
        id="ours-regulator-revokes-one-licence",
        example=None,
        entity=_entity(
            MAS,
            2,
            "MAS revoked a Singapore-licensed Major Payment Institution's licence under the "
            "Payment Services Act 2019 for letting a third party run a cross-border remittance "
            "business under its name and for giving false or misleading information, which is a "
            "binding enforcement action directly affecting Singapore's financial system.",
        ),
        title="MAS Revokes the Major Payment Institution License of Sir Money Changer Pte Ltd",
        statement="MAS revoked Sir Money Changer Pte Ltd's Major Payment Institution License "
        "under the Payment Services Act 2019, effective 15 September 2026, after finding it let a "
        "third party run a cross-border remittance business under its name and gave MAS false or "
        "misleading information.",
        quote="The Monetary Authority of Singapore (MAS) has revoked the Major Payment "
        "Institution License of Sir Money Changer Pte. Ltd. (SMC) under the Payment Services Act "
        "2019, with effect from 15 September 2026.",
        must=[],
        must_not=STRUCTURAL,
    ),
    # a3065a895267fb9f-1
    LinkTypeCase(
        id="ours-regulator-directs-one-firm",
        example=None,
        entity=_entity(
            MAS,
            2,
            "A data breach at MoneySmart Financial, a MAS-regulated insurance broker affecting "
            "Singapore customers, has prompted MAS to direct containment, IT security, root cause "
            "analysis and remediation, so it is a significant incident directly within "
            "Singapore's financial system.",
        ),
        title="MoneySmart data breach affects Singapore and Hong Kong customers - The Business "
        "Times",
        statement="MAS is following up with MoneySmart Financial, its regulated insurance broker, "
        "over the breach, directing it to contain the breach, secure its IT environment, conduct "
        "a root cause analysis and implement remedial measures.",
        quote="In a response to queries from BT, MAS said that it was following up closely with "
        "MoneySmart Financial – the group’s MAS-regulated insurance broker, over the "
        "incident.",
        must=[],
        must_not=STRUCTURAL,
    ),
    # 5bedd77aa8abe1ff-0
    LinkTypeCase(
        id="ours-regulator-issues-guidelines",
        example=None,
        entity=_entity(
            MAS,
            2,
            "MAS issued final Guidelines on AI Risk Management setting supervisory expectations "
            "for all financial institutions in Singapore (AI governance, lifecycle controls, "
            "third-party AI), a binding-in-effect regulatory change that alters what Singapore's "
            "financial system must do from 7 October 2027.",
        ),
        title="MAS Sets Out Supervisory Expectations on Responsible AI Adoption by Financial "
        "Institutions",
        statement="MAS issued Guidelines on AI Risk Management on 7 October 2026, setting "
        "supervisory expectations for all financial institutions on AI governance, lifecycle risk "
        "controls, third-party AI and risk-proportionate application, effective 7 October 2027.",
        quote="The Monetary Authority of Singapore (MAS) today issued a set of Guidelines on "
        'Artificial Intelligence (AI) Risk Management ("Guidelines") to support responsible AI '
        "adoption in Singapore’s financial sector.",
        must=["regulated_by"],
        must_not=[],
    ),
    # 037f633f65ab6c87-1
    LinkTypeCase(
        id="ours-country-arrests",
        example=None,
        entity=_entity(
            SINGAPORE,
            2,
            "Police in Singapore arrested nine people for setting up corporate entities and bank "
            "accounts used to funnel illegal gambling proceeds through Singapore's banking "
            "system, with customer accounts frozen, which is a direct AML/financial-crime "
            "incident affecting Singapore's financial system.",
        ),
        title="GemBet issuing invoices to mask illegal gambling bets in Singapore | The Straits "
        "Times",
        statement="Police in Singapore arrested nine people in July over setting up business "
        "entities and corporate bank accounts used to facilitate illegal gambling transactions "
        "for the syndicate behind GemBet; punters said their bank accounts were frozen.",
        quote="Six of them are suspected of setting up business entities and corporate bank "
        "accounts believed to have been used to facilitate monetary transactions for illegal "
        "gambling, while three are said to have assisted the syndicate behind GemBet in "
        "obtaining these entities and bank accounts.",
        must=[],
        must_not=STRUCTURAL,
    ),
    # 21c965d40c03cd82-0
    LinkTypeCase(
        id="ours-country-company-deal",
        example=None,
        entity=_entity(
            SINGAPORE,
            2,
            "A ~S$611-626 million sponsor-to-REIT acquisition of a 51% stake in Keppel South "
            "Central, a Singapore property, with five-year income support, is a major deal within "
            "Singapore's financial system (listed REIT sector) that raises $12.1 billion pro "
            "forma portfolio value and minority-shareholder governance concerns.",
        ),
        title="Keppel REIT to acquire 51% stake in Keppel South Central for $626.2 mil; Sponsor "
        "to provide five-year income support",
        statement="Keppel REIT announced it will acquire a 51% stake in Keppel South Central "
        "from sponsor Keppel Limited for $626.2 million, with Keppel Limited providing income "
        "support of $2.23 million monthly for up to five years.",
        quote="Keppel REIT has announced that it will acquire a 51% stake in Keppel South "
        "Central from its sponsor, Keppel Limited, for a total consideration of $626.2 million.",
        must=[],
        must_not=STRUCTURAL,
    ),
    # 3732a968254f6641-1
    LinkTypeCase(
        id="ours-country-monetary-policy",
        example=None,
        entity=_entity(
            SINGAPORE,
            2,
            "MAS's surprise decision to hold the S$NEER policy band (against expected tightening) "
            "and cut its 2026 core inflation forecast is a binding monetary policy decision that "
            "materially affects Singapore's financial system, including the Singapore dollar, "
            "rates and market pricing.",
        ),
        title="MAS' surprise hold suggests core inflation is near its peak - The Business Times",
        statement="On Oct 14, 2026, MAS unexpectedly left the S$NEER policy band slope, width and "
        "centre unchanged, against the expectations of 17 of 20 economists polled by Bloomberg "
        "who had forecast a steeper slope.",
        quote="MAS left the slope of the Singapore dollar nominal effective exchange rate "
        "(S$NEER) policy band unchanged, with no change to its width or the level at which it is "
        "centred.",
        must=[],
        one_of=["operates_in", "exposed_to"],
        must_not=[],
    ),
    # 51cea549dc309f6c-0
    LinkTypeCase(
        id="ours-exchange-rule-change",
        example=None,
        entity=_entity(
            SGX,
            1,
            "SGX RegCo's new enhanced disclosure rules on remuneration, dividend and investor "
            "relations policies are new regulatory guidance that applies to listed issuers "
            "(effective for financial years from Jan 1, 2027) and only indirectly affect SGX as "
            "the exchange operator and regulator, with no direct binding change on SGX itself.",
        ),
        title="When reigniting markets, don’t overcook the broth",
        statement="On Sept 23, SGX RegCo introduced new rules requiring listed issuers to make "
        "enhanced disclosures on remuneration, dividend and investor relations policies.",
        quote="On Sept 23, Singapore Exchange Regulation (SGX RegCo) introduced new rules "
        "requiring enhanced disclosures among listed issuers on remuneration, dividend and "
        "investor relations policies.",
        must=["listed_on"],
        must_not=[],
    ),
    # 7e9a55f20446296c-0
    LinkTypeCase(
        id="ours-exchange-one-company",
        example=None,
        entity=_entity(
            SGX,
            1,
            "A large Singapore-headquartered firm (DayOne, ~US$20bn market cap) chose a "
            "Nasdaq-only IPO over a dual Nasdaq-SGX listing, a missed opportunity for SGX's Global "
            "Listing Board and its liquidity ambitions, but it is not an incident, enforcement "
            "action, or loss at SGX itself.",
        ),
        title="Why tech and data centre IPOs are facing delays now - The Business Times",
        statement="Singapore-headquartered data centre operator DayOne filed for a US Nasdaq IPO "
        "in October seeking up to US$5 billion, with H1 revenue tripling but net losses "
        "widening; an SGX listing is not ruled out.",
        quote="DayOne officially filed for a US Nasdaq IPO directly in October this year, citing "
        "plans to raise as much as US$5 billion.",
        must=[],
        must_not=STRUCTURAL,
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
    missing: list[str] = Field(description='Types of `must`, or "a or b" for an unmet `one_of`')
    forbidden: list[RelType]


def check(case: LinkTypeCase, picked: list[RelType] | None) -> LinkTypeCaseResult:
    got: set[RelType] = set(picked or [])
    missing: list[str] = [t for t in case.must if t not in got]
    if case.one_of and not got & set(case.one_of):
        missing.append(" or ".join(case.one_of))
    forbidden: list[RelType] = [t for t in case.forbidden() if t in got]
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
    for case in [*CASES, *OUR_CASES]:
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
    deck, ours = results[: len(CASES)], results[len(CASES) :]
    lines = [
        f"Link type cases, effort {effort or 'the stage default'}: the deck's "
        f"{sum(r.ok for r in deck)} of {len(deck)} and ours {sum(r.ok for r in ours)} of "
        f"{len(ours)} pass ({inf.calls} Claude calls, {inf.cache_hits} replayed, ${cost:.4f} "
        f"for the calls made, mean {output_tokens:.0f} output tokens, measured; versions "
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
