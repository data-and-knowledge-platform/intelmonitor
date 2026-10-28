"""Pipeline tuning: routing thresholds, budgets and concurrency, all in one place."""

from datetime import date
from typing import Literal

# Confidence routing for Jev nouls (relevance and subject tags): at or above HIGH is a clear yes,
# at or below LOW a clear no, and the band in between goes to Claude.
ROUTE_LOW = 0.3
ROUTE_HIGH = 0.7

# Jev's context on OpenRouter is 32k tokens. Documents longer than this state budget are screened
# on their title plus leading chunks (about 4 characters per token).
SCREEN_STATE_TOKENS = 24_000
CHARS_PER_TOKEN = 4

# Claude reads the full text for extraction, capped for the very longest PDFs.
EXTRACT_MAX_CHARS = 200_000

# Same-event candidates: findings sharing a subject, event dates within MERGE_WINDOW_DAYS, cosine
# similarity at or above MERGE_MIN_SIMILARITY, at most MERGE_MAX_NEIGHBOURS per finding.
# Jev decides each pair; SAME_EVENT_THRESHOLD or more merges them.
MERGE_WINDOW_DAYS = 10
MERGE_MIN_SIMILARITY = 0.6
MERGE_MAX_NEIGHBOURS = 5
SAME_EVENT_THRESHOLD = 0.5
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

# Priority labels, indexed by level 0 to 2. Jev scores a development's priority for each subject it
# happened to or that did it, against PRIORITY_RUBRIC; the development's level is its highest.
Priority = Literal["Low", "Medium", "High"]
PRIORITY_LABELS: list[Priority] = ["Low", "Medium", "High"]
# What each level means, in the same order: Jev scores against these, and Help shows them. They
# follow the supervisors' guideline: where it happened (pipeline/singapore.py says how each
# institution links to Singapore) and whether it is an incident or a development. Adverse is a
# separate flag.
PRIORITY_RUBRIC: list[str] = [
    "no effect on financial institutions in Singapore: news about a foreign institution or third "
    "party with no strong link to Singapore, Singapore news outside the financial landscape, or "
    "routine, promotional or barely about this subject",
    "a development at a financial institution in Singapore (results, strategy, deals, "
    "appointments, a new rule, guidance or consultation that applies to it), or a development "
    "at a foreign institution or third party strongly linked to Singapore",
    "an incident at a financial institution in Singapore, or at a foreign institution or third "
    "party strongly linked to Singapore: an outage, cyberattack or data breach, fraud, "
    "enforcement action or penalty, a large loss, a failure or a rescue; or a development at a "
    "financial institution in Singapore serious enough to change its position",
]
# Needs attention is High; Watch, the impact step and "material" start at Medium.
NEEDS_ATTENTION_LEVEL = 2
MATERIAL_LEVEL = 1
# Below this probability on its top label, Jev is unsure and Claude decides instead.
PRIORITY_SURE = 0.6


def display_key(
    level: int, adverse: bool, last_published: date, development_id: str
) -> tuple[int, bool, int, str]:
    """The order a subject's developments are shown and briefed in: High, Medium, Low, adverse
    first within High and Medium, most recent first within each. `level` and `adverse` are the
    development's for the subjects in view. The feed (server/queries.py) and the briefs
    (pipeline/summaries.py) both sort by it."""
    return (-level, level > 0 and not adverse, -last_published.toordinal(), development_id)


# Promotional and product news is Low.
PROMOTIONAL_MAX_LEVEL = 0
# A development only social media reports is unverified, and a single post is not a signal
# (prd.md section 7): Low until a news, official or data source joins it.
UNVERIFIED_MAX_LEVEL = 0
# A source reports on a jurisdiction when Jev scores it at least this likely.
JURISDICTION_THRESHOLD = 0.5
# Market moves (prices, indices, flows) are never High, and Low unless a Singapore financial
# institution or Singapore market is involved (pipeline/singapore.py).
MARKET_MAX_LEVEL = 1

# Run 1 is computed as of T0 minus this many days, Run 2 as of T0.
RUN_SPACING_DAYS = 7

# Concurrent model calls. Claude is capped to stay within the subscription allowance.
JEV_CONCURRENCY = 8
CLAUDE_CONCURRENCY = 4
