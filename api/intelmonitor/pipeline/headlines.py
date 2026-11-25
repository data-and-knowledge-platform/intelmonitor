"""The headline call: one Sonnet call per development, after the merge and before priority, writes
a short headline and 2 or 3 key points for busy readers who scan the feed instead of opening
sources, and says which of the development's candidate subjects it is about. The title (the lead
finding's statement) stays as it is for merging, priority, impact and briefs; the headline and
points are for display only.

A development's candidates are the union of its findings' actor and affected tags
(`link_subjects`), so one side finding can bring a subject the event is not about: the MAS hold of
14 Oct 2026 carried Artificial Intelligence & Quantum Computing from two findings on MAS's growth
outlook. The call keeps the subjects the development is about; `Development.keep_subjects` makes
the others mentions, which are never rated (spec step 8a of link-types-and-inferred-impact,
decided 23 Nov 2026).

Claude writes from the development's findings and the text of its lead source (most developments
have one finding, a single sentence, too little for points that add anything). The prompt depends
only on those and the candidates, so a development whose findings did not change since an earlier
run reuses the recorded reply. A development made only of detections gets its headline and points
from code: the measured sentence cut at its value ("UOB share price down 5% on the week") and the
sentence itself as the point; it keeps its subjects, the series the detector measured."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from claude_agent_sdk import EffortLevel
from pydantic import BaseModel, Field

from intelmonitor.pipeline.extract import FindingRole

# The findings shown to Claude, primary sources first: a big merged development repeats itself.
# Each candidate subject's first finding that tags it is shown as well, beyond these.
MAX_FINDINGS = 8
# The lead source's text shown, from its start: enough for the facts of a news story.
ARTICLE_MAX_CHARS = 12_000
# Low: the subject cases pass 3 of 3 at low and at medium (25 Nov 2026), and medium costs about
# half as much again.
HEADLINE_EFFORT: EffortLevel = "low"


class Headline(BaseModel):
    headline: str = Field(description="A news headline of 6 to 12 words, no full stop")
    points: list[str] = Field(
        description="2 or 3 key points, each one sentence of at most 20 words"
    )
    subjects: list[str] = Field(
        description="The ids, from <subjects>, of the subjects the development is about"
    )


HEADLINE_SYSTEM = """You are a senior news editor at a financial wire service. You write headlines \
that sum up a story in a few words and catch the reader's eye with its key words, and short key \
points that give the facts a professional reader needs. You also file each story under the \
institutions, countries and topics it is about, so it reaches the desks that follow them.

Your readers are supervisors at the Monetary Authority of Singapore (MAS). They scan dozens of \
developments a day between other work. The headline is often all they read, and the points decide \
whether they open the sources, so both must carry the facts that matter for supervising banks, \
insurers, payment firms and markets. They may act on what you write, so every fact must come \
from the article and findings you are given: a missing detail is better than an inferred one.

<examples>
<example>
<findings>
<finding source="mastercard.com" published="2026-08-15">On 15 August 2026 a global Mastercard \
issue, which Mastercard attributed to a scheduled system update, caused card payments to be \
declined for Australian customers, including at Commonwealth Bank, before being resolved.</finding>
<finding source="abc.net.au" published="2026-08-15">Card payments failed for several hours \
across Australia on 15 August 2026 as Mastercard's network suffered an outage.</finding>
</findings>
<subjects>
<subject id="mastercard" findings="2 of 2">Mastercard, the payment system</subject>
<subject id="australia" findings="2 of 2">the country Australia</subject>
<subject id="payments-fintech" findings="1 of 2">the topic Payments & Financial Technology</subject>
</subjects>
<output>
{"headline": "Mastercard outage declines card payments across Australia", "points": ["A \
scheduled system update caused the failure, Mastercard said.", "Commonwealth Bank customers were \
among those hit; payments failed for several hours.", "The issue was resolved the same day."], \
"subjects": ["mastercard", "australia", "payments-fintech"]}
</output>
</example>
<example>
<findings>
<finding source="mas.gov.sg" published="2026-09-29">On 29 September 2026 MAS appointed a third \
batch of five asset managers under the EQDP, placing S$1.45 billion to invest in Singapore \
equities.</finding>
</findings>
<subjects>
<subject id="mas" findings="1 of 1">MAS, the central bank and financial regulator</subject>
<subject id="capital-markets-equities" findings="1 of 1">the topic Capital Markets & \
Equities</subject>
<subject id="asset-wealth-management" findings="1 of 1">the topic Asset & Wealth \
Management</subject>
</subjects>
<output>
{"headline": "MAS hands S$1.45 billion to five more EQDP fund managers", "points": ["This is \
the third batch appointed under the Equity Market Development Programme.", "The funds go into \
Singapore-listed equities."], "subjects": ["mas", "capital-markets-equities", \
"asset-wealth-management"]}
</output>
</example>
<example>
<findings>
<finding source="reuters.com" published="2026-08-03">US regulators fined UBS $125 million on 3 \
August 2026 for Bank Secrecy Act violations, with UBS Financial Services admitting willful \
violations, the largest civil fine against a broker-dealer under the law.</finding>
<finding source="finra.org" published="2026-08-03">FINRA and FinCEN fined UBS Financial Services \
for failing to report suspicious transactions over several years.</finding>
<finding source="ft.com" published="2026-08-03">UBS shares closed 0.4% lower in Zurich after the \
fine was announced.</finding>
</findings>
<subjects>
<subject id="ubs" findings="3 of 3">UBS, the bank</subject>
<subject id="united-states" findings="2 of 3">the country United States</subject>
<subject id="aml-sanctions" findings="2 of 3">the topic Anti-Money Laundering & \
Sanctions</subject>
<subject id="capital-markets-equities" findings="1 of 3">the topic Capital Markets & \
Equities</subject>
</subjects>
<output>
{"headline": "UBS fined $125 million for US anti-money laundering failures", "points": ["UBS \
Financial Services admitted willful Bank Secrecy Act violations.", "FINRA and FinCEN said it \
failed to report suspicious transactions over several years.", "It is the largest civil fine \
against a broker-dealer under the law."], "subjects": ["ubs", "united-states", "aml-sanctions"]}
</output>
</example>
<example>
<findings>
<finding source="thestar.com.my" published="2026-10-14">On 14 October 2026, Bank Negara Malaysia \
and the Securities Commission warned the public about deepfake videos impersonating their \
officials to promote investment schemes.</finding>
</findings>
<subjects>
<subject id="malaysia" findings="1 of 1">the country Malaysia</subject>
<subject id="scams-fraud" findings="1 of 1">the topic Financial Crime & Scams</subject>
<subject id="ai-quantum" findings="1 of 1">the topic Artificial Intelligence & Quantum \
Computing</subject>
</subjects>
<output>
{"headline": "Malaysian regulators warn of deepfake videos posing as officials", "points": ["Bank \
Negara Malaysia and the Securities Commission issued the joint warning.", "The fake videos use \
officials' likeness to promote investment schemes."], "subjects": ["malaysia", "scams-fraud", \
"ai-quantum"]}
</output>
</example>
</examples>"""

HEADLINE_INSTRUCTIONS = """Write the headline and key points for the development these findings \
report, and choose its subjects. The findings are statements from different sources on the same \
event; the article is the lead source's text, which may also cover other news. Write about the \
event in the findings, taking details from the article where it gives them.

headline:
- 6 to 12 words in present tense and active voice, like a wire headline: who did what, or what \
happened to whom.
- Lead with the key words a supervisor scans for: the institution, market or regulator, and the \
action (outage, fine, breach, acquisition, consultation, rate cut).
- Include the one figure that matters most, such as an amount, a percentage or a duration, when \
the article or findings give one.
- Use the names Singapore readers know (DBS, MAS, SGX, HKMA) and keep amounts in their own \
currency.
- Leave out the date and the source; the dashboard shows both. End without a full stop.

points:
- 2 or 3 points, most important first; 2 when the article and findings hold little more than \
the headline.
- Each is one plain sentence of at most 20 words that adds a fact the headline does not already \
give: who \
is affected and how many, figures, cause, status (proposed, in force, resolved, ongoing), \
effective dates and deadlines, and what the regulator or institution does next.
- When sources disagree on a figure, give the official source's figure or say reports differ.

subjects:
- The ids, from <subjects>, of the subjects the development is about: the institutions, places \
and markets the event happened to or that did it, and the topics the event falls under. An event \
often falls under several topics; keep each one it does.
- Judge a topic by its description in <subjects>: keep it when the event itself is within what the \
topic covers and not in what it excludes, not because a finding names its subject matter.
- Leave out only a subject that a side detail concerns and the event itself does not: an aside in \
one finding, background, another story the article also covers, or an outlook or market reaction \
mentioned in passing. It stays on the development as mentioned.
- Do not leave a subject out because few findings name it or because the headline does not.
- When unsure, keep it. Keep at least one.

Keep the tone of a wire report: factual and neutral, the strongest word the facts support and \
no stronger."""


class HeadlineFinding(Protocol):
    """What the prompt reads of a finding (pipeline/developments.py's RunFinding)."""

    @property
    def statement(self) -> str: ...
    @property
    def domain(self) -> str: ...
    @property
    def published(self) -> date: ...
    @property
    def rank(self) -> tuple[int, date, str]: ...
    @property
    def roles(self) -> Mapping[str, FindingRole]: ...


@dataclass(frozen=True)
class Candidate:
    """A subject the call may keep: its id, what it covers, and how many of the development's
    findings say the development happened to it or that it did it."""

    id: str
    description: str
    findings: int


def shown_findings[F: HeadlineFinding](
    findings: Sequence[F], subject_ids: Sequence[str]
) -> list[F]:
    """The findings the call reads, by rank, each statement once from its best-ranked finding: the
    first MAX_FINDINGS, and for each subject that none of those tags as actor or affected, its
    first finding that does, so every candidate is judged on a finding about it."""
    ranked = sorted(findings, key=lambda f: f.rank)
    unique: dict[str, F] = {}
    for f in ranked:
        unique.setdefault(f.statement, f)
    shown = list(unique.values())[:MAX_FINDINGS]

    def tags(f: F, sid: str) -> bool:
        r = f.roles.get(sid)
        return r is not None and r.role in ("actor", "affected")

    for sid in subject_ids:
        if not any(tags(f, sid) for f in shown):
            first = next((f for f in ranked if tags(f, sid)), None)
            if first is not None and first.statement not in {f.statement for f in shown}:
                shown.append(first)
    return sorted(shown, key=lambda f: f.rank)


def headline_prompt(
    findings: Sequence[HeadlineFinding], article: str, candidates: Sequence[Candidate]
) -> str:
    """The lead source's text first, as long inputs do best above the instructions, then the
    findings `shown_findings` picks, then the candidate subjects, then the instructions. The lead
    source is the first finding by rank."""
    shown = shown_findings(findings, [c.id for c in candidates])
    lines = "\n".join(
        f'<finding source="{f.domain}" published="{f.published}">{f.statement}</finding>'
        for f in shown
    )
    subjects = "\n".join(
        f'<subject id="{c.id}" findings="{c.findings} of {len(findings)}">{c.description}</subject>'
        for c in candidates
    )
    source = min(findings, key=lambda f: f.rank)
    return (
        f'<article source="{source.domain}" published="{source.published}">\n'
        f"{article[:ARTICLE_MAX_CHARS]}\n</article>\n\n"
        f"<findings>\n{lines}\n</findings>\n\n<subjects>\n{subjects}\n</subjects>\n\n"
        f"{HEADLINE_INSTRUCTIONS}"
    )


def detection_headline(statement: str) -> tuple[str, list[str]]:
    """A detector's sentence names the move before its values: "UOB share price down 5.01% on the
    week to SGD 41.13 ..., from ..." (pipeline/detectors.py). The headline is that part, and the
    sentence is the one point."""
    head = re.split(r" to |, from ", statement, maxsplit=1)[0].rstrip(",. ")
    head = re.sub(r"\d+\.\d+(?=%)", lambda m: f"{float(m[0]):.1f}".removesuffix(".0"), head)
    return head, [statement]
