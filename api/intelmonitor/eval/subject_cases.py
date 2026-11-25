"""Cases for the development's subjects, chosen in the headline call (spec step 8a of
link-types-and-inferred-impact, decided 23 Nov 2026), written before the prompt.

A development's candidate subjects are the union of its findings' actor and affected tags
(`link_subjects`). The headline call keeps those the development is about and the rest become
mentions. It fails two ways: it keeps a subject that only a side finding concerns (the MAS hold of
14 Oct 2026 carried Artificial Intelligence & Quantum Computing from two findings on MAS's growth
outlook, and the topic took MAS's High), or it drops a subject the event is about because few
findings name it, or the headline does not. Three cases are developments of the 8 Nov 2026 run, in
their findings' own wording and tags; the fourth is made up, one finding whose four tags all
belong. Each names the subjects that must stay and those that must go; the other candidates are
free. In the MAS hold, Foreign Exchange & Reserves is free: four findings tag it for the Singapore
dollar's move, but the topic excludes central-bank policy decisions, which Central Banking &
Monetary Policy covers.

The cases run through the stage's own call (`write_headline`), one Claude call each, in their own
store, eval-runs/.../db/subjects.db, so the app's store is never touched and a repeat run replays
the recorded calls: `uv run python -m intelmonitor.eval subjects`. Writes subject_cases.json in
the eval directory."""

import json
from datetime import date

from pydantic import BaseModel, Field

from intelmonitor.eval.common import EVAL_DIR
from intelmonitor.pipeline.developments import (
    Development,
    RunFinding,
    link_subjects,
    write_headline,
)
from intelmonitor.pipeline.extract import FindingRole
from intelmonitor.pipeline.inference import Inference
from intelmonitor.store.db import connect

STORE = EVAL_DIR / "db" / "subjects.db"

Role = str  # actor, affected, commentator or mentioned


class CaseFinding(BaseModel):
    domain: str
    published: date
    statement: str
    roles: dict[str, Role]


class SubjectCase(BaseModel):
    id: str
    development: str = Field(description="Its id in the 8 Nov 2026 run, or how it was made")
    article: str = Field(description="The lead source's opening, as the call reads it")
    findings: list[CaseFinding]
    keep: list[str] = Field(description="Subjects the call must keep")
    drop: list[str] = Field(description="Subjects the call must leave out")


def _f(domain: str, published: str, statement: str, **roles: Role) -> CaseFinding:
    return CaseFinding(
        domain=domain,
        published=date.fromisoformat(published),
        statement=statement,
        roles={k.replace("_", "-"): v for k, v in roles.items()},
    )


CASES = [
    SubjectCase(
        id="mas-hold-with-ai-outlook",
        development="3732a968254f6641-1",
        article="The Monetary Authority of Singapore (MAS) kept the slope, width and centre of "
        "the S$NEER policy band unchanged on Oct 14, against expectations of a slight tightening. "
        "It narrowed its 2026 core inflation forecast to 1.5 to 2 per cent from 1.5 to 2.5 per "
        "cent. MAS said the economy should keep expanding firmly in coming quarters but moderate "
        "as the AI cycle eases from its exceptional pace.",
        findings=[
            _f(
                "sc.com",
                "2026-10-14",
                "MAS kept the slope, width and centre of its exchange rate policy band "
                "unchanged, against expectations of slight tightening, and lowered its 2026 core "
                "inflation forecast to 1.5-2.0% from 1.5-2.5%.",
                mas="actor",
                monetary_policy="affected",
                singapore="affected",
                fx_reserves="affected",
            ),
            _f(
                "businesstimes.com.sg",
                "2026-10-14",
                "MAS kept monetary policy settings unchanged on Oct 14, maintaining the S$NEER "
                "policy band's rate of appreciation, width and centre, pausing after two "
                "tightenings this year.",
                mas="actor",
                monetary_policy="affected",
                singapore="affected",
                fx_reserves="affected",
            ),
            _f(
                "businesstimes.com.sg",
                "2026-10-14",
                "MAS narrowed its 2026 core inflation forecast to 1.5 to 2 per cent, with "
                "headline around 2 per cent, and projected 2027 core and headline inflation at "
                "1.5 to 2.5 per cent.",
                mas="actor",
                economic_growth_inflation="affected",
                singapore="affected",
            ),
            _f(
                "straitstimes.com",
                "2026-10-14",
                "On Oct 14, 2026, MAS kept the rate of appreciation of the S$NEER policy band "
                "unchanged after two tightenings in 2026, saying the current band will lean "
                "against near-term imported inflation.",
                mas="actor",
                monetary_policy="affected",
                singapore="affected",
                fx_reserves="affected",
            ),
            _f(
                "wsj.com",
                "2026-10-14",
                "The Monetary Authority of Singapore unexpectedly held off on further tightening "
                "on Wednesday, Oct. 14, 2026, leaving its existing policy settings unchanged after "
                "two tightenings earlier in the year.",
                mas="actor",
                monetary_policy="affected",
                singapore="affected",
            ),
            _f(
                "channelnewsasia.com",
                "2026-10-14",
                "On 14 October 2026 MAS kept monetary policy unchanged, maintaining the rate of "
                "appreciation of the S$NEER policy band, contrary to all 10 analysts polled by "
                "Reuters who expected tightening, after tightening in April and July.",
                mas="actor",
                monetary_policy="affected",
                singapore="affected",
            ),
            _f(
                "businesstimes.com.sg",
                "2026-10-14",
                "MAS said the S$NEER has strengthened modestly within the upper half of its policy "
                "band since July, and the current band will let it continue strengthening against "
                "imported inflation.",
                fx_reserves="affected",
                mas="commentator",
                singapore="affected",
            ),
            _f(
                "businesstimes.com.sg",
                "2026-10-14",
                "MAS said Singapore's AI-related activity is projected to moderate in the latter "
                "half of 2027, though strong AI orders and capacity expansion should sustain near-"
                "term growth.",
                ai_quantum="affected",
                tech_infrastructure="affected",
                mas="commentator",
                singapore="affected",
            ),
            _f(
                "straitstimes.com",
                "2026-10-14",
                "MAS said the economy should keep expanding firmly in coming quarters but "
                "moderate as the AI cycle eases from its exceptional pace and global financial "
                "conditions tighten.",
                ai_quantum="affected",
                mas="commentator",
            ),
            _f(
                "reuters.com",
                "2026-10-14",
                "DBS, OCBC, Maybank and JPMorgan economists viewed MAS' hold as a policy pause; "
                "DBS trimmed its 2026 inflation forecasts.",
                dbs="commentator",
                ocbc="commentator",
                monetary_policy="affected",
            ),
        ],
        keep=["mas", "monetary-policy", "singapore"],
        drop=["ai-quantum", "tech-infrastructure"],
    ),
    SubjectCase(
        id="ocbc-outage-with-paynow",
        development="2a8425648cab7473-0",
        article="OCBC customers faced intermittent slowness and login problems on mobile and "
        "internet banking from about 12.31pm on Friday, affecting some payments. The bank said "
        "services were progressively restored, ATM and card services were unaffected and no "
        "customer data was compromised.",
        findings=[
            _f(
                "businesstimes.com.sg",
                "2026-10-23",
                "On 23 October 2026, OCBC customers faced intermittent slowness and login "
                "problems on mobile and internet banking from about 12.31 pm, affecting some "
                "payments; the bank said services were progressively restored and no customer "
                "data was compromised.",
                ocbc="affected",
                cyber_resilience="affected",
                banking_lending="affected",
            ),
            _f(
                "straitstimes.com",
                "2026-10-23",
                "OCBC mobile and internet banking suffered a disruption on the afternoon of Oct "
                "23, 2026, beginning around 12.15pm; the bank said services were restored.",
                ocbc="affected",
                cyber_resilience="affected",
            ),
            _f(
                "straitstimes.com",
                "2026-10-23",
                "Some social media users reported problems with ATM withdrawals and PayNow "
                "services during the OCBC disruption on 23 October 2026, although OCBC said its "
                "ATM and card services were unaffected.",
                paynow="affected",
                payments_fintech="affected",
                ocbc="affected",
            ),
            _f(
                "channelnewsasia.com",
                "2026-10-23",
                "Some OCBC customers reported being unable to use payment methods such as PayNow "
                "through the OCBC app during the disruption, according to comments on the bank's "
                "Facebook page.",
                payments_fintech="affected",
                ocbc="affected",
            ),
        ],
        keep=["ocbc", "cyber-resilience"],
        drop=[],
    ),
    SubjectCase(
        id="fairfax-idbi-few-sources",
        development="53a00c9760dc21f0-0",
        article="India's government approved the strategic restructuring of IDBI Bank and the "
        "sale of a 60.7% stake to Canada's Fairfax Financial, led by Prem Watsa, for 520 billion "
        "rupees (about US$5.43 billion), the biggest foreign takeover of an Indian lender.",
        findings=[
            _f(
                "wsj.com",
                "2026-10-14",
                "India's government approved the strategic restructuring of IDBI Bank and the "
                "sale of a 60.7% stake to Canada's Fairfax Financial, led by Prem Watsa, for 520 "
                "billion rupees (about US$5.43 billion).",
                mergers_acquisitions="affected",
                india="affected",
                banking_lending="affected",
                insurance_reinsurance="mentioned",
            ),
            _f(
                "cnbc.com",
                "2026-10-15",
                "On Oct 14, 2026 India's government announced the sale of a 60.7% stake in "
                "state-owned IDBI Bank to Canada's Fairfax Financial for over 529 billion rupees "
                "($5.5 billion), at 81 rupees per share, pending final approvals.",
                mergers_acquisitions="affected",
                banking_lending="affected",
                india="actor",
                fiscal_policy="actor",
            ),
            _f(
                "businesstimes.com.sg",
                "2026-10-19",
                "AUM Capital Market's Rajesh Agarwal said the IDBI deal is a calibrated opening "
                "of India's banking ownership framework, not an immediate liberalisation or the "
                "start of an accelerated privatisation cycle.",
                india="affected",
                financial_regulation="affected",
            ),
        ],
        keep=["india", "mergers-acquisitions", "banking-lending"],
        drop=[],
    ),
    SubjectCase(
        id="mastercard-outage-one-source",
        development="made up, after the headline prompt's first example",
        article="Card payments were declined for Australian customers on Friday, including at "
        "Commonwealth Bank, after a global Mastercard issue that the company attributed to a "
        "scheduled system update. The issue was resolved the same day.",
        findings=[
            _f(
                "mastercard.com",
                "2026-08-15",
                "On 15 August 2026 a global Mastercard issue, which Mastercard attributed to a "
                "scheduled system update, caused card payments to be declined for Australian "
                "customers before being resolved.",
                mastercard="actor",
                australia="affected",
                payments_fintech="affected",
                cyber_resilience="affected",
            ),
        ],
        keep=["mastercard", "payments-fintech"],
        drop=[],
    ),
]


def development(case: SubjectCase) -> Development:
    """The case as a development: one finding per entry, each from its own document, news."""
    return Development(
        id=case.id,
        findings=[
            RunFinding(
                id=f"{case.id}-{i}",
                document_id=f"{case.id}-{i}",
                statement=f.statement,
                category="other",
                event_date=f.published,
                subjects=tuple(f.roles),
                roles={
                    s: FindingRole.model_validate({"role": r, "adverse": False, "tone": "neutral"})
                    for s, r in f.roles.items()
                },
                promotional=False,
                price_move=False,
                first_quote=f.statement,
                published=f.published,
                collection="news",
                domain=f.domain,
                title=f.statement,
                stage=None,
                rule_level=None,
                rule_id=None,
            )
            for i, f in enumerate(case.findings)
        ],
    )


class SubjectCaseResult(BaseModel):
    id: str
    ok: bool
    kept: list[str] | None  # None when refused
    missing: list[str]
    wrongly_kept: list[str]
    headline: str | None


async def run() -> None:
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    STORE.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(STORE)
    before = conn.execute("SELECT coalesce(max(id), 0) FROM inference_records").fetchone()[0]
    inf = Inference(conn, "eval-subjects")
    results: list[SubjectCaseResult] = []
    for case in CASES:
        dev = development(case)
        link_subjects(conn, [dev])
        text = await write_headline(inf, dev, case.article)
        if text is None:
            results.append(
                SubjectCaseResult(
                    id=case.id,
                    ok=False,
                    kept=None,
                    missing=case.keep,
                    wrongly_kept=[],
                    headline=None,
                )
            )
            continue
        dev.keep_subjects(text.subjects)
        kept = sorted(dev.subjects)
        missing = [s for s in case.keep if s not in kept]
        wrong = [s for s in case.drop if s in kept]
        results.append(
            SubjectCaseResult(
                id=case.id,
                ok=not missing and not wrong,
                kept=kept,
                missing=missing,
                wrongly_kept=wrong,
                headline=text.headline,
            )
        )
    cost = conn.execute(
        "SELECT coalesce(sum(cost_usd), 0) FROM inference_records WHERE id > ?", (before,)
    ).fetchone()[0]
    (EVAL_DIR / "subject_cases.json").write_text(
        json.dumps([r.model_dump() for r in results], indent=2)
    )
    lines = [
        f"Subject cases: {sum(r.ok for r in results)} of {len(results)} pass ({inf.calls} "
        f"Claude calls, {inf.cache_hits} replayed, ${cost:.4f} for the calls made, measured; "
        f"versions {', '.join(sorted(inf.versions))})",
        "",
        "| Case | Result | Kept | Missing | Kept, should go |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {r.id} | {'pass' if r.ok else 'FAIL'} | "
        f"{'refused' if r.kept is None else ', '.join(r.kept)} | {', '.join(r.missing)} | "
        f"{', '.join(r.wrongly_kept)} |"
        for r in results
    ]
    print("\n".join(lines))
