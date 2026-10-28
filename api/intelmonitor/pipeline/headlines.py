"""Step 6b: a short headline and 2 or 3 key points for each development, for busy readers who
scan the feed instead of opening sources. The title (the lead finding's statement) stays as it
is for merging, priority, impact and briefs; the headline and points are for display only.

Claude writes them from the development's stored findings and the text of its lead source (most
developments have one finding, a single sentence, too little for points that add anything). The
prompt depends only on those, so a development whose findings did not change since an earlier run
reuses the recorded reply. A development made only of detections gets them from code: the measured
sentence cut at its value ("UOB share price down 5% on the week") and the sentence itself as the
point."""

import asyncio
import json
import re
import sqlite3
from dataclasses import dataclass

from pydantic import BaseModel, Field

from intelmonitor.pipeline.inference import ClaudeRefused, Inference, StepFailed

# The findings shown to Claude, primary sources first: a big merged development repeats itself.
MAX_FINDINGS = 8
# The lead source's text shown, from its start: enough for the facts of a news story.
ARTICLE_MAX_CHARS = 12_000
_COLLECTION_RANK = {"dms": 0, "official": 1, "data": 2, "news": 3, "social": 4}


class Headline(BaseModel):
    headline: str = Field(description="A news headline of 6 to 12 words, no full stop")
    points: list[str] = Field(
        description="2 or 3 key points, each one sentence of at most 20 words"
    )


HEADLINE_SYSTEM = """You are a senior news editor at a financial wire service. You write headlines \
that sum up a story in a few words and catch the reader's eye with its key words, and short key \
points that give the facts a professional reader needs.

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
<output>
{"headline": "Mastercard outage declines card payments across Australia", "points": ["A \
scheduled system update caused the failure, Mastercard said.", "Commonwealth Bank customers were \
among those hit; payments failed for several hours.", "The issue was resolved the same day."]}
</output>
</example>
<example>
<findings>
<finding source="mas.gov.sg" published="2026-09-29">On 29 September 2026 MAS appointed a third \
batch of five asset managers under the EQDP, placing S$1.45 billion to invest in Singapore \
equities.</finding>
</findings>
<output>
{"headline": "MAS hands S$1.45 billion to five more EQDP fund managers", "points": ["This is \
the third batch appointed under the Equity Market Development Programme.", "The funds go into \
Singapore-listed equities."]}
</output>
</example>
<example>
<findings>
<finding source="reuters.com" published="2026-08-03">US regulators fined UBS $125 million on 3 \
August 2026 for Bank Secrecy Act violations, with UBS Financial Services admitting willful \
violations, the largest civil fine against a broker-dealer under the law.</finding>
<finding source="finra.org" published="2026-08-03">FINRA and FinCEN fined UBS Financial Services \
for failing to report suspicious transactions over several years.</finding>
</findings>
<output>
{"headline": "UBS fined $125 million for US anti-money laundering failures", "points": ["UBS \
Financial Services admitted willful Bank Secrecy Act violations.", "FINRA and FinCEN said it \
failed to report suspicious transactions over several years.", "It is the largest civil fine \
against a broker-dealer under the law."]}
</output>
</example>
<example>
<findings>
<finding source="thestar.com.my" published="2026-10-14">On 14 October 2026, Bank Negara Malaysia \
and the Securities Commission warned the public about deepfake videos impersonating their \
officials to promote investment schemes.</finding>
</findings>
<output>
{"headline": "Malaysian regulators warn of deepfake videos posing as officials", "points": ["Bank \
Negara Malaysia and the Securities Commission issued the joint warning.", "The fake videos use \
officials' likeness to promote investment schemes."]}
</output>
</example>
</examples>"""

HEADLINE_INSTRUCTIONS = """Write the headline and key points for the development these findings \
report. The findings are statements from different sources on the same event; the article is \
the lead source's text, which may also cover other news. Write about the event in the findings, \
taking details from the article where it gives them.

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

Keep the tone of a wire report: factual and neutral, the strongest word the facts support and \
no stronger."""


@dataclass(frozen=True)
class StoredFinding:
    statement: str
    domain: str
    published: str
    collection: str
    detector: bool
    document_id: str

    @property
    def rank(self) -> tuple[int, str]:
        return (_COLLECTION_RANK.get(self.collection, 5), self.published)


def lead(findings: list[StoredFinding]) -> StoredFinding:
    """The finding whose source's text goes in the prompt: primary sources first, then earliest."""
    return min(findings, key=lambda f: f.rank)


def headline_prompt(findings: list[StoredFinding], article: str) -> str:
    """The lead source's text first, as long inputs do best above the instructions, then the
    findings, primary sources first, without repeats, then the instructions."""
    shown = list({f.statement: f for f in sorted(findings, key=lambda f: f.rank)}.values())
    lines = "\n".join(
        f'<finding source="{f.domain}" published="{f.published}">{f.statement}</finding>'
        for f in shown[:MAX_FINDINGS]
    )
    source = lead(findings)
    return (
        f'<article source="{source.domain}" published="{source.published}">\n'
        f"{article[:ARTICLE_MAX_CHARS]}\n</article>\n\n"
        f"<findings>\n{lines}\n</findings>\n\n{HEADLINE_INSTRUCTIONS}"
    )


def detection_headline(statement: str) -> Headline:
    """A detector's sentence names the move before its values: "UOB share price down 5.01% on the
    week to SGD 41.13 ..., from ..." (pipeline/detectors.py). The headline is that part."""
    head = re.split(r" to |, from ", statement, maxsplit=1)[0].rstrip(",. ")
    head = re.sub(r"\d+\.\d+(?=%)", lambda m: f"{float(m[0]):.1f}".removesuffix(".0"), head)
    return Headline(headline=head, points=[statement])


def run_findings(conn: sqlite3.Connection, run_id: str) -> dict[str, list[StoredFinding]]:
    """Each development's findings in the run, by development id."""
    out: dict[str, list[StoredFinding]] = {}
    for r in conn.execute(
        "SELECT s.development_id, f.statement, d.domain, substr(d.published_at, 1, 10) AS day, "
        "d.collection, d.source = 'detector' AS detector, s.document_id "
        "FROM development_sources s "
        "JOIN findings f ON f.id = s.finding_id JOIN source_documents d ON d.id = s.document_id "
        "WHERE s.run_id = ?",
        (run_id,),
    ):
        out.setdefault(r["development_id"], []).append(
            StoredFinding(
                r["statement"],
                r["domain"],
                r["day"],
                r["collection"],
                bool(r["detector"]),
                r["document_id"],
            )
        )
    return out


@dataclass
class HeadlineStats:
    written: int = 0
    from_code: int = 0
    refused: int = 0


async def write_headlines(inf: Inference, conn: sqlite3.Connection, run_id: str) -> HeadlineStats:
    """Headline and points for every development in the run, stored on its row. A refused
    development keeps none, and the app shows its title instead."""
    stats = HeadlineStats()
    failures: list[str] = []

    async def one(dev_id: str, findings: list[StoredFinding]) -> tuple[str, Headline | None]:
        if all(f.detector for f in findings):
            stats.from_code += 1
            return dev_id, detection_headline(lead(findings).statement)
        article = conn.execute(
            "SELECT text FROM source_documents WHERE id = ?", (lead(findings).document_id,)
        ).fetchone()["text"]
        try:
            h = await inf.claude(
                "headline",
                f"dev:{dev_id}:{len(findings)}",
                headline_prompt(findings, article or ""),
                Headline,
                HEADLINE_SYSTEM,
                effort="low",
            )
        except ClaudeRefused:
            stats.refused += 1
            return dev_id, None
        except Exception as error:
            failures.append(f"{dev_id}: {type(error).__name__}: {error}")
            return dev_id, None
        stats.written += 1
        return dev_id, h

    results = await asyncio.gather(*(one(i, f) for i, f in run_findings(conn, run_id).items()))
    if failures:
        raise StepFailed("headline", failures)
    conn.executemany(
        "UPDATE developments SET headline = ?, points = ? WHERE run_id = ? AND id = ?",
        [
            (h.headline.strip(), json.dumps([p.strip() for p in h.points]), run_id, dev_id)
            if h
            else (None, None, run_id, dev_id)
            for dev_id, h in results
        ],
    )
    conn.commit()
    return stats
