"""The checks `im weekly` makes before paying for a week: one Exa search and one Claude Haiku call.
A missing, revoked or blocked key then fails the run at once, instead of an Exa key that only
makes the week look quiet or a Claude key that grinds through retries until the runner's limit."""

import tempfile
from pathlib import Path

from pydantic import BaseModel

from intelmonitor.ingest.http import CachedHttp
from intelmonitor.ingest.models import Window
from intelmonitor.ingest.sources.search import NEWS_DOMAINS, exa_search
from intelmonitor.pipeline.claude import ask_claude
from intelmonitor.pipeline.params import HAIKU_MODEL


class _Reply(BaseModel):
    ok: bool


async def preflight(window: Window) -> None:
    """Raises when Exa or Claude cannot be used. Costs one Exa search and one Haiku call."""
    with tempfile.TemporaryDirectory() as raw:  # an empty cache, so the search reaches Exa
        exa_search(
            CachedHttp(Path(raw)),
            "Monetary Authority of Singapore",
            window,
            NEWS_DOMAINS,
            1,
        )
    await ask_claude('Answer {"ok": true}.', _Reply, model=HAIKU_MODEL)
