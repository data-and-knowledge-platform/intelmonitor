"""X post counts (`/2/tweets/counts/all`): posts a day matching an X target's search query, the
same query its searches send (ingest/sources/social.py), so an outage shows as a jump in volume
that a sample of 10 or 30 posts cannot show (x-api-plan.md, X1).

Counts run from HISTORY_FROM in PAGE_DAYS pages aligned on it, over complete UTC days only: every
page but the last covers past days, so it is fetched once and served from the cache after; the
last ends at the start of the current UTC day and is a new request each day. Each request reads
no posts and is charged to an XSpend against x_spend_cap_usd.
"""

from collections.abc import Mapping
from datetime import date, datetime, timedelta

from pydantic import BaseModel, Field

from intelmonitor.config import Settings
from intelmonitor.ingest.http import CachedHttp
from intelmonitor.ingest.series.models import (
    Fetched,
    KeyRejected,
    SourceError,
    Window,
    day_start,
    get_json,
)
from intelmonitor.ingest.sources.social import TARGETS, X_COUNTS_USD, XSpend
from intelmonitor.seed import XSource
from intelmonitor.store.series import Observation

NAMESPACE = "x"
URL = "https://api.x.com/2/tweets/counts/all"
HISTORY_FROM = date(2026, 9, 1)  # a month of baseline before October, the first month of runs
PAGE_DAYS = 31  # the most a day-granularity page holds


class _Bucket(BaseModel):
    start: datetime
    end: datetime
    tweet_count: int


class _Meta(BaseModel):
    next_token: str | None = None


class _Response(BaseModel):
    data: list[_Bucket] = Field(default_factory=list[_Bucket])
    meta: _Meta = Field(default_factory=_Meta)


def fetch(
    http: CachedHttp, settings: Settings, sources: Mapping[str, XSource], window: Window
) -> Fetched:
    fetched = Fetched()
    if not settings.x_bearer_token:
        fetched.fail(sources, "X_BEARER_TOKEN is not set")
        return fetched
    queries = {t.subject_id: t.search_query for t in TARGETS}
    spend = XSpend(settings.x_spend_cap_usd)
    pages: list[tuple[date, date]] = []
    start, end = max(window.floor, HISTORY_FROM), window.utc_today
    while start < end:
        pages.append((start, min(start + timedelta(days=PAGE_DAYS), end)))
        start += timedelta(days=PAGE_DAYS)

    def counts(query: str) -> list[Observation]:
        observations: list[Observation] = []
        for first, stop in pages:
            token: str | None = None
            while True:
                if not spend.allows(X_COUNTS_USD):
                    raise SourceError("X spend cap reached")
                params = {
                    "query": query,
                    "granularity": "day",
                    "start_time": f"{day_start(first):%Y-%m-%dT%H:%M:%SZ}",
                    "end_time": f"{day_start(stop):%Y-%m-%dT%H:%M:%SZ}",
                }
                if token:
                    params["next_token"] = token
                sent = http.network[NAMESPACE]
                found = get_json(
                    http,
                    NAMESPACE,
                    URL,
                    _Response,
                    params=params,
                    headers={"Authorization": f"Bearer {settings.x_bearer_token}"},
                )
                spend.count_calls += http.network[NAMESPACE] - sent
                observations += [
                    Observation(period=b.start.date(), value=b.tweet_count, available_at=b.end)
                    for b in found.data
                ]
                token = found.meta.next_token
                if not token:
                    break
        return sorted(observations, key=lambda o: o.period)

    for series_id, source in sources.items():
        query = queries.get(source.target)
        if query is None:
            fetched.fail([series_id], f"{source.target} is not searched on X")
            continue
        try:
            fetched.observations[series_id] = counts(query)
        except KeyRejected as e:
            done = fetched.observations.keys() | fetched.failures.keys()
            fetched.fail([sid for sid in sources if sid not in done], f"X counts: {e}")
            break
        except SourceError as e:
            fetched.fail([series_id], f"X counts {source.target}: {e}")
    fetched.notes.append(spend.line())
    return fetched
