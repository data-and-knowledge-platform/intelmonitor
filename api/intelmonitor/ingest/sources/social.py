"""Social posts for the `social` collection: X recent search, and Reddit and forum threads found
through Tavily and Exa.

Only subjects where posts tend to come before news are searched (TARGETS): the banks, payment
systems, technology providers, exchanges, stablecoin issuers and crypto exchanges, MAS, and the IT
outage and cyber topics with targeted queries. Each ingest runs one Tavily search and one Exa
search per target, and one X search for each target with an X query.

X bills each post read (X_POST_USD), so every X search is capped at its target's `max_posts` and
the ingest stops searching X once the next search could pass the spend cap. Responses are cached
like every other source, so a rerun of the same ingest reads nothing from X. X recent search only
reaches back 7 days; forum threads come from the whole ingest window.

Every social document carries an accurate timestamp: X gives each post's creation time, an X post
found by Tavily is dated from its id, a HardwareZone thread from its first page, and a Reddit
thread from Exa's date. Reddit refuses page fetches and Tavily gives no dates, so Tavily's Reddit
hits are dropped, as the news searches drop what they cannot date.
"""

import json
import re
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from urllib.parse import urlencode

from pydantic import BaseModel, Field

from intelmonitor.ingest.candidates import normalize, subject_matchers
from intelmonitor.ingest.http import CachedHttp, FetchError, JSONValue
from intelmonitor.ingest.models import RawDocument, Window
from intelmonitor.ingest.parse import canonical_url, domain_of
from intelmonitor.ingest.parse import document_id as _document_id

X_POST_USD = 0.005  # pay-per-use price of one post read
X_TRENDS_USD = 0.010  # and of one trends call
X_COUNTS_USD = 0.010  # and of one counts request
# One author, expanded on a post or looked up: assumed billed as a user read, until the console
# confirms it (x-api-plan.md, X0).
X_USER_USD = 0.010
SOCIAL_DOMAINS = ["reddit.com", "forums.hardwarezone.com.sg"]
FORUM_RESULTS = 10
MIN_POST_CHARS = 40
_TEXT_MAX_CHARS = 20000
# Problem words that turn a name into an early-warning search.
# Phrases rather than "down", which mostly finds share prices and "doubling down".
_DOWN = '(outage OR "is down" OR "went down" OR "are down" OR disruption OR "not working"'
# Bank app names are specific enough for a plain "down"; market chatter is left out.
_TROUBLE = (
    '(down OR outage OR disruption OR "not working" OR glitch OR scam OR fraud OR hacked OR breach)'
    ' -stock -stocks -shares -"share price" -dividend'
)
_OUTAGE = f"{_DOWN} OR degraded)"


@dataclass(frozen=True)
class SocialTarget:
    """A subject searched on social platforms: its X query (search operators, before the shared
    filters), its forum query (plain words for Tavily and Exa), the most X posts to read, the
    subject's own X accounts and whether it is Singapore-facing.

    Official accounts are searched apart from the keyword query (official_search), so status and
    corporate posts do not compete with complaints for the same relevancy slots. Singapore-facing
    targets return few posts, so their authors are expanded; the global targets fill their cap
    with worldwide chatter and show outages through post volume (ingest/series/x_counts.py), so
    they read fewer posts and no authors. Most Singapore-facing targets read none and are watched
    through post counts alone, which keeps a week's X spend under x_spend_cap_usd."""

    subject_id: str
    x_query: str | None  # None: forums only, where X finds mostly noise
    forum_query: str
    max_posts: int = 10  # 0: post counts only, no search
    official: tuple[str, ...] = ()  # X handles, without the @
    local: bool = False

    @property
    def search_query(self) -> str | None:
        """The query sent to X: the target's query and the shared filters. Searches and post
        counts (ingest/series/x_counts.py) both send it."""
        return f"{self.x_query} {_X_FILTERS}" if self.x_query else None


# Banks, payment systems and fintechs, insurers, technology providers, exchanges, digital asset
# firms, MAS and two topics. GIRO, MEPS+, BCS and the core banking and print vendors are left out:
# the public does not post about them.
TARGETS = [
    SocialTarget(
        "dbs",
        f'(digibank OR POSB OR PayLah OR "DBS bank" OR "DBS app") {_TROUBLE}',
        "DBS digibank PayLah down outage",
        20,
        ("dbsbank",),
        local=True,
    ),
    SocialTarget(
        "ocbc",
        f'(OCBC OR "OCBC app") {_TROUBLE}',
        "OCBC app down outage",
        20,
        ("OCBCBank",),
        local=True,
    ),
    SocialTarget(
        "uob",
        f'(UOB OR "UOB TMRW") {_TROUBLE}',
        "UOB TMRW app down outage",
        20,
        ("UOB_Group",),
        local=True,
    ),
    SocialTarget(
        "standard-chartered",
        f'("Standard Chartered" OR StanChart OR "SC Mobile") (Singapore OR SG) {_TROUBLE}',
        "Standard Chartered Singapore app down outage",
        0,
        ("StanChart",),
        local=True,
    ),
    SocialTarget(
        "maybank",
        f'(Maybank OR Maybank2u OR "MAE app") {_TROUBLE}',
        "Maybank app down outage",
        0,
        local=True,
    ),
    SocialTarget(
        "citigroup",
        f'(Citibank OR "Citi app" OR "Citi Mobile") {_TROUBLE}',
        "Citibank app down outage",
    ),
    SocialTarget(
        "hsbc", f'("HSBC app" OR "HSBC Singapore" OR HSBC) {_TROUBLE}', "HSBC app down outage"
    ),
    SocialTarget(
        "gxs-bank",
        f'("GXS Bank" OR "GXS app" OR "GXS FlexiLoan") {_TROUBLE}',
        "GXS Bank app down",
        0,
        local=True,
    ),
    SocialTarget(
        "trust-bank",
        f'("Trust Bank" OR "Trust app" OR "Trust card") (Singapore OR FairPrice OR Linkpoint) '
        f"{_TROUBLE}",
        "Trust Bank Singapore app down",
        0,
        local=True,
    ),
    SocialTarget(
        "seamoney",
        f'(MariBank OR "Mari Bank" OR ShopeePay OR SPayLater) {_TROUBLE}',
        "MariBank ShopeePay down",
        0,
        local=True,
    ),
    SocialTarget(
        "grab",
        f'(GrabPay OR "Grab app" OR GrabFood OR "Grab ride") {_TROUBLE}',
        "Grab app GrabPay down outage",
        15,
        ("Grab",),
        local=True,
    ),
    SocialTarget(
        "singlife",
        f'(Singlife OR "Singlife app") {_TROUBLE}',
        "Singlife app down",
        0,
        local=True,
    ),
    SocialTarget(
        "income-insurance",
        f'("Income Insurance" OR "NTUC Income") {_TROUBLE}',
        "Income Insurance NTUC Income app down",
        0,
        local=True,
    ),
    SocialTarget(
        "mas",
        '"Monetary Authority of Singapore"',
        "Monetary Authority of Singapore MAS",
        0,
        ("MAS_sg",),
        local=True,
    ),
    SocialTarget(
        "sgx",
        '(SGX OR "Singapore Exchange") (halt OR halted OR outage OR glitch OR suspended OR '
        "disruption OR delay)",
        "SGX trading halt outage glitch",
        0,
        ("SGXGroup",),
        local=True,
    ),
    SocialTarget(
        "hkex",
        '(HKEX OR "Hong Kong stock exchange") (halt OR halted OR outage OR glitch OR suspended '
        "OR disruption)",
        "HKEX trading halt outage glitch",
        0,
        ("HKEXGroup",),
        local=True,
    ),
    SocialTarget(
        "paynow",
        'PayNow (down OR outage OR "not working" OR delay OR disruption OR scam)',
        "PayNow down not working",
        15,
        local=True,
    ),
    SocialTarget(
        "fast",
        '("FAST transfer" OR "FAST payment" OR "FAST transfers") (down OR delay OR outage OR '
        '"not working" OR stuck)',
        "FAST transfer delay down Singapore",
        0,
        local=True,
    ),
    SocialTarget(
        "nets",
        '("NETS payment" OR "NETS terminal" OR "NETS QR" OR "NETS card" OR NETSPay) '
        '(down OR outage OR "not working" OR disruption)',
        "NETS payment terminal down",
        0,
        local=True,
    ),
    SocialTarget(
        "swift",
        '("SWIFT network" OR "SWIFT payments" OR "SWIFT messaging") '
        "(outage OR down OR disruption OR delay OR cyberattack)",
        "SWIFT network outage disruption",
    ),
    SocialTarget(
        "visa",
        '("Visa card" OR "Visa payments") (down OR outage OR declined OR "not working" OR '
        "disruption)",
        "Visa card payments down outage",
        official=("Visa",),
    ),
    SocialTarget(
        "mastercard",
        'Mastercard (down OR outage OR declined OR "not working" OR disruption)',
        "Mastercard payments down outage",
        official=("Mastercard",),
    ),
    SocialTarget(
        "wise",
        '("Wise app" OR "Wise account" OR "Wise card" OR "Wise transfer" OR TransferWise) '
        f"{_OUTAGE}",
        "Wise app transfer down outage",
        official=("Wise",),
    ),
    SocialTarget(
        "stripe",
        f'(Stripe OR "Stripe payments" OR "Stripe API") {_OUTAGE}',
        "Stripe outage",
        official=("stripestatus",),
    ),
    SocialTarget(
        "aws",
        f'(AWS OR "Amazon Web Services") {_OUTAGE}',
        "AWS outage Singapore",
        official=("AWSSupport",),
    ),
    SocialTarget(
        "microsoft-azure", f"Azure {_OUTAGE}", "Microsoft Azure outage", official=("AzureSupport",)
    ),
    SocialTarget(
        "microsoft-365",
        f'("Microsoft 365" OR "Office 365" OR M365) {_OUTAGE}',
        "Microsoft 365 Outlook Teams outage",
        official=("MSFT365Status",),
    ),
    SocialTarget(
        "google-cloud",
        f'("Google Cloud" OR GCP) {_OUTAGE}',
        "Google Cloud outage",
    ),
    SocialTarget(
        "oracle-cloud", f'("Oracle Cloud" OR "Oracle OCI") {_OUTAGE}', "Oracle Cloud outage"
    ),
    SocialTarget(
        "crowdstrike",
        'CrowdStrike (outage OR crash OR "blue screen" OR BSOD OR down OR breach)',
        "CrowdStrike outage crash",
        official=("CrowdStrike",),
    ),
    SocialTarget(
        "singtel",
        'Singtel (down OR outage OR disruption OR "no signal" OR "not working" OR breach)',
        "Singtel outage down no signal",
        15,
        ("Singtel",),
        local=True,
    ),
    SocialTarget(
        "equinix", "Equinix (outage OR down OR power OR fire OR disruption)", "Equinix outage"
    ),
    SocialTarget(
        "circle",
        "USDC (depeg OR depegged OR frozen OR freeze OR outage OR hack OR exploit "
        'OR "not working" OR redemption)',
        "USDC depeg freeze Circle",
        official=("circle",),
    ),
    SocialTarget(
        "straitsx",
        '(XSGD OR XUSD OR StraitsX) (depeg OR frozen OR down OR outage OR "not working" OR '
        "scam OR hack)",
        "StraitsX XSGD down outage",
        0,
        ("StraitsX",),
        local=True,
    ),
    SocialTarget(
        "okx",
        f'(OKX OR "OKX SG" OR "OKX Pay") {_TROUBLE}',
        "OKX Singapore down outage withdrawals",
        official=("okx",),
    ),
    SocialTarget(
        "coinbase",
        f"Coinbase {_TROUBLE}",
        "Coinbase down outage withdrawals",
        official=("CoinbaseSupport",),
    ),
    SocialTarget(
        "crypto-com",
        f'("Crypto.com" OR "Crypto com app") {_TROUBLE}',
        "Crypto.com app down outage withdrawals",
        official=("cryptocom",),
    ),
    SocialTarget(
        "tech-outages",
        # On X, bank app names find crypto news and online sellers; the bank targets cover X.
        None,
        "Singapore bank app down outage",
    ),
    SocialTarget(
        "cyber-threats",
        '(ransomware OR "data breach" OR cyberattack OR hacked) ("Singapore bank" OR DBS OR OCBC '
        'OR UOB OR SGX OR Singtel OR "Monetary Authority of Singapore" OR CSA)',
        "Singapore bank cyberattack data breach",
        15,
        local=True,
    ),
]


class SocialMeta(BaseModel):
    """A social document's row in social_posts."""

    platform: str
    subject_id: str
    query: str
    post_id: str | None = None
    author_id: str | None = None
    likes: int | None = None
    reposts: int | None = None
    replies: int | None = None
    quotes: int | None = None
    impressions: int | None = None
    # X authors: expanded for Singapore-facing targets and official accounts only.
    author_username: str | None = None
    author_name: str | None = None
    author_verified_type: str | None = None  # blue, business, government or none
    author_followers: int | None = None
    official: bool = False  # posted by the subject's own account (official_search)
    urls: list[str] = Field(default_factory=list[str])  # links in the post, expanded
    referenced: list[str] = Field(default_factory=list[str])  # "quoted:<id>", "replied_to:<id>"


@dataclass(frozen=True)
class SocialDoc:
    doc: RawDocument
    meta: SocialMeta


@dataclass
class XSpend:
    """X reads paid this ingest (cached responses are free), against the spend cap: posts and
    authors from searches, trends calls (sources/trends.py) and counts requests
    (series/x_counts.py)."""

    cap_usd: float
    posts: int = 0
    users: int = 0
    searches: int = 0
    trend_calls: int = 0
    count_calls: int = 0
    skipped: list[str] = field(default_factory=list[str])

    @property
    def usd(self) -> float:
        return (
            self.posts * X_POST_USD
            + self.users * X_USER_USD
            + self.trend_calls * X_TRENDS_USD
            + self.count_calls * X_COUNTS_USD
        )

    def allows(self, usd: float) -> bool:
        return self.usd + usd <= self.cap_usd

    def line(self) -> str:
        return (
            f"x: {self.searches} searches, {self.trend_calls} trends calls and "
            f"{self.count_calls} counts requests sent, {self.posts} posts and {self.users} "
            f"authors read, estimated {self.usd:.3f} USD (cap {self.cap_usd:.2f})"
            + (f"; skipped {', '.join(self.skipped)}" if self.skipped else "")
        )


def x_search_url(subject_id: str) -> str:
    """X's latest-posts search page for a target's query."""
    target = next((t for t in TARGETS if t.subject_id == subject_id), None)
    if target is None or target.search_query is None:
        raise ValueError(f"{subject_id} is not searched on X")
    return f"https://x.com/search?{urlencode({'q': target.search_query, 'f': 'live'})}"


def x_status_url(post_id: str) -> str:
    return f"https://x.com/i/status/{post_id}"


def _x_title(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= 100 else line[:99].rstrip() + "…"


_LIST_COLUMNS = {"urls", "referenced"}
# Refreshed when a known post is found again; the first subject whose search found it stays.
_REFRESHED = [
    "likes",
    "reposts",
    "replies",
    "quotes",
    "impressions",
    "author_username",
    "author_name",
    "author_verified_type",
    "author_followers",
    "fetched_at",
]


def store_social_meta(conn: sqlite3.Connection, document_id: str, meta: SocialMeta) -> None:
    """Write a stored social document's metadata, or refresh its counts and author when the
    document is already known. Does nothing when the document was not stored, such as a post
    deduped by its text. `cites_document_id` is the first link in the post that is a stored
    document, such as a news article the post shares."""
    row: dict[str, object] = meta.model_dump()
    for column in _LIST_COLUMNS:
        row[column] = json.dumps(row[column])
    cited = [_document_id(canonical_url(u)) for u in meta.urls]
    row["cites_document_id"] = next(
        (
            c
            for c in cited
            if c != document_id
            and conn.execute("SELECT 1 FROM source_documents WHERE id = ?", (c,)).fetchone()
        ),
        None,
    )
    columns = ["document_id", *row, "fetched_at"]
    values = [document_id, *row.values(), datetime.now(UTC).isoformat()]
    conn.execute(
        f"INSERT INTO social_posts ({', '.join(columns)}) "
        f"SELECT {', '.join('?' for _ in columns)} "
        "WHERE EXISTS (SELECT 1 FROM source_documents WHERE id = ?) "
        "ON CONFLICT (document_id) DO UPDATE SET "
        + ", ".join(f"{c} = coalesce(excluded.{c}, {c})" for c in _REFRESHED),
        [*values, document_id],
    )


# --- X search ------------------------------------------------------------------------------

X_SEARCH = "https://api.x.com/2/tweets/search/recent"
X_SEARCH_ALL = "https://api.x.com/2/tweets/search/all"
X_USERS = "https://api.x.com/2/users/by"
X_DAYS = 7  # how far back recent search reaches, and the span every X search covers
_X_FILTERS = "-is:retweet lang:en"
_TWEET_FIELDS = "created_at,public_metrics,author_id,lang,note_tweet,entities,referenced_tweets"
_USER_FIELDS = "username,name,verified_type,affiliation,public_metrics"
# Query length limits: recent search, and full-archive search.
_QUERY_CHARS = {X_SEARCH: 512, X_SEARCH_ALL: 1024}


class _XMetrics(BaseModel):
    retweet_count: int = 0
    reply_count: int = 0
    like_count: int = 0
    quote_count: int = 0
    impression_count: int | None = None


class _XNote(BaseModel):
    text: str


class _XUrl(BaseModel):
    expanded_url: str | None = None
    unwound_url: str | None = None


class _XEntities(BaseModel):
    urls: list[_XUrl] = Field(default_factory=list[_XUrl])


class _XReference(BaseModel):
    type: str
    id: str


class _XPost(BaseModel):
    id: str
    text: str
    created_at: datetime
    author_id: str | None = None
    public_metrics: _XMetrics | None = None
    note_tweet: _XNote | None = None  # the full text of a post over 280 characters
    entities: _XEntities | None = None
    referenced_tweets: list[_XReference] = Field(default_factory=list[_XReference])


class _XUserMetrics(BaseModel):
    followers_count: int = 0


class _XUser(BaseModel):
    id: str
    username: str
    name: str
    verified_type: str | None = None
    public_metrics: _XUserMetrics | None = None


class _XIncludes(BaseModel):
    users: list[_XUser] = Field(default_factory=list[_XUser])


class _XMeta(BaseModel):
    next_token: str | None = None


class _XResponse(BaseModel):
    data: list[_XPost] = Field(default_factory=list[_XPost])
    includes: _XIncludes = Field(default_factory=_XIncludes)
    meta: _XMeta = Field(default_factory=_XMeta)


@dataclass(frozen=True)
class _XSpan:
    """The X_DAYS days ending at T0, and the endpoint that reaches them: recent search within
    the last 7 days, full-archive search before."""

    url: str
    start: datetime
    end: datetime
    archive: bool


def _x_span(window: Window) -> _XSpan:
    end = datetime.combine(window.t0 + timedelta(days=1), time(), UTC)
    start = end - timedelta(days=X_DAYS)
    # Recent search reaches back 7 days from now, so a span starting before that needs the archive.
    archive = start < datetime.now(UTC) - timedelta(days=X_DAYS)
    return _XSpan(X_SEARCH_ALL if archive else X_SEARCH, start, end, archive)


def _x_get(
    http: CachedHttp,
    token: str,
    span: _XSpan,
    params: dict[str, str],
    window: Window,
    label: str,
) -> tuple[_XResponse, bool]:
    """One search over the span; returns the response and whether it came from the cache.

    A recent search is keyed by query and T0, so a rerun of the same ingest is served from the
    cache even on a later day; the end of T0 is sent as end_time unless it is still in the future.
    A full-archive search names its fixed span, so it needs no T0 key."""
    now = datetime.now(UTC)
    if span.archive:
        params = {
            **params,
            "start_time": f"{span.start:%Y-%m-%dT%H:%M:%SZ}",
            "end_time": f"{span.end:%Y-%m-%dT%H:%M:%SZ}",
        }
        sent_unkeyed, vary = {}, None
    else:
        recent_end = span.end < now - timedelta(minutes=1)
        sent_unkeyed = {"end_time": f"{span.end:%Y-%m-%dT%H:%M:%SZ}"} if recent_end else {}
        vary = window.t0.isoformat()
    cached_before = http.cached["x"]
    response = http.get(
        "x",
        span.url,
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        expect_json=True,
        vary=vary,
        secret_params=sent_unkeyed,
        cache_errors=False,
    )
    if not response.ok:
        raise FetchError(f"X search {label}: HTTP {response.status} {response.text()[:300]}")
    return _XResponse.model_validate_json(response.content), http.cached["x"] != cached_before


def _x_doc(
    p: _XPost,
    subject_id: str,
    query: str,
    author: _XUser | None,
    official: bool,
) -> SocialDoc:
    """A post as a document: social, or official when the subject's own account posted it
    (phase1 decision D-X1: an official account verifies a development like any official
    source)."""
    text = (p.note_tweet.text if p.note_tweet else p.text).strip()
    m = p.public_metrics or _XMetrics()
    links = [u.unwound_url or u.expanded_url for u in (p.entities.urls if p.entities else [])]
    urls = [u for u in dict.fromkeys(links) if u is not None]
    doc = RawDocument(
        source="x",
        collection="official" if official else "social",
        doc_type="X post",
        url=x_status_url(p.id),
        title=_x_title(text),
        published_at=p.created_at,
        text=text,
    )
    meta = SocialMeta(
        platform="x",
        subject_id=subject_id,
        query=query,
        post_id=p.id,
        author_id=p.author_id,
        likes=m.like_count,
        reposts=m.retweet_count,
        replies=m.reply_count,
        quotes=m.quote_count,
        impressions=m.impression_count,
        author_username=author.username if author else None,
        author_name=author.name if author else None,
        author_verified_type=author.verified_type if author else None,
        author_followers=author.public_metrics.followers_count
        if author and author.public_metrics
        else None,
        official=official,
        # Links to other posts are quotes, already in `referenced`.
        urls=[u for u in urls if not _X_STATUS.match(u)],
        referenced=[f"{r.type}:{r.id}" for r in p.referenced_tweets],
    )
    return SocialDoc(doc, meta)


def x_search(
    http: CachedHttp,
    token: str,
    target: SocialTarget,
    window: Window,
    spend: XSpend,
    log: Callable[[str], None],
) -> list[SocialDoc]:
    """Up to `target.max_posts` posts from the 7 days ending at T0, most relevant first, through
    recent search, or full-archive search for a T0 more than 7 days back. Authors are expanded
    for Singapore-facing targets."""
    if target.search_query is None or not target.max_posts:
        return []
    per_post = X_POST_USD + (X_USER_USD if target.local else 0)
    if not spend.allows(target.max_posts * per_post):
        spend.skipped.append(f"{target.subject_id} (spend cap)")
        return []
    span = _x_span(window)
    query = target.search_query
    params = {
        "query": query,
        "max_results": str(min(100, max(10, target.max_posts))),
        "sort_order": "relevancy",
        "tweet.fields": _TWEET_FIELDS,
    }
    if target.local:
        params |= {"expansions": "author_id", "user.fields": _USER_FIELDS}
    found, cached = _x_get(http, token, span, params, window, target.subject_id)
    if not cached:
        spend.posts += len(found.data)
        spend.users += len(found.includes.users)
        spend.searches += 1
    authors = {u.id: u for u in found.includes.users}
    docs = [
        _x_doc(p, target.subject_id, query, authors.get(p.author_id or ""), official=False)
        for p in found.data
        if window.contains(p.created_at)
    ]
    archive = " (archive)" if span.archive else ""
    log(f"x {target.subject_id}: {len(found.data)} posts{archive}{' (cached)' if cached else ''}")
    return docs


class _XUsers(BaseModel):
    data: list[_XUser] = Field(default_factory=list[_XUser])


OFFICIAL_PAGE_POSTS = 100
OFFICIAL_MAX_PAGES = 2  # a busy week's posts across all the accounts, at most 1.00 USD


def _official_queries(handles: list[str], limit: int) -> list[str]:
    """`(from:a OR from:b ...) -is:retweet -is:reply`, split to stay under the query limit.
    Replies are left out: support accounts mostly answer customers one by one."""
    suffix = " -is:retweet -is:reply"
    queries: list[str] = []
    batch: list[str] = []
    for h in handles:
        candidate = [*batch, f"from:{h}"]
        if batch and len(f"({' OR '.join(candidate)}){suffix}") > limit:
            queries.append(f"({' OR '.join(batch)}){suffix}")
            candidate = [f"from:{h}"]
        batch = candidate
    if batch:
        queries.append(f"({' OR '.join(batch)}){suffix}")
    return queries


def official_search(
    http: CachedHttp,
    token: str,
    targets: list[SocialTarget],
    window: Window,
    spend: XSpend,
    log: Callable[[str], None],
    fail: Callable[[str], None],
) -> list[SocialDoc]:
    """Every post by the targets' official accounts in the 7 days ending at T0, each tagged to
    its subject and stored as an official document.

    The handles are looked up once (the lookup is cached for good) for the author details and to
    map each post back to its subject; a handle X does not know is reported and skipped."""
    owner = {h.lower(): t.subject_id for t in targets for h in t.official}
    if not owner:
        return []
    handles = sorted(owner)
    cached_before = http.cached["x"]
    response = http.get(
        "x",
        X_USERS,
        params={"usernames": ",".join(handles), "user.fields": _USER_FIELDS},
        headers={"Authorization": f"Bearer {token}"},
        expect_json=True,
        cache_errors=False,
    )
    if not response.ok:
        raise FetchError(f"X users: HTTP {response.status} {response.text()[:300]}")
    users = _XUsers.model_validate_json(response.content).data
    if http.cached["x"] == cached_before:
        spend.users += len(users)
    if unknown := set(handles) - {u.username.lower() for u in users}:
        fail(f"X official accounts not found: {', '.join(sorted(unknown))}")
    by_id = {u.id: u for u in users}
    span = _x_span(window)
    docs: list[SocialDoc] = []
    known = sorted(u.username for u in users)
    for query in _official_queries(known, _QUERY_CHARS[span.url]):
        page_token: str | None = None
        for _ in range(OFFICIAL_MAX_PAGES):
            if not spend.allows(OFFICIAL_PAGE_POSTS * X_POST_USD):
                spend.skipped.append("official accounts (spend cap)")
                return docs
            params = {
                "query": query,
                "max_results": str(OFFICIAL_PAGE_POSTS),
                "sort_order": "recency",
                "tweet.fields": _TWEET_FIELDS,
            }
            if page_token:
                params["next_token"] = page_token
            try:
                found, cached = _x_get(http, token, span, params, window, "official accounts")
            except FetchError as e:
                fail(str(e))
                break
            if not cached:
                spend.posts += len(found.data)
                spend.searches += 1
            for p in found.data:
                author = by_id.get(p.author_id or "")
                if author and window.contains(p.created_at):
                    subject_id = owner[author.username.lower()]
                    docs.append(_x_doc(p, subject_id, query, author, official=True))
            log(f"x official: {len(found.data)} posts{' (cached)' if cached else ''}")
            page_token = found.meta.next_token
            if not page_token:
                break
    return docs


# --- Reddit and forums through Tavily and Exa ----------------------------------------------

_X_STATUS = re.compile(r"^https?://(?:www\.)?(?:x|twitter)\.com/[^/]+/status/(\d+)")
_HWZ_THREAD = re.compile(r"^(https://forums\.hardwarezone\.com\.sg/threads/[^/?#]+/)")
_TIME_TAG = re.compile(r'<time[^>]*datetime="([^"]+)"')
_X_EPOCH_MS = 1288834974657


def x_post_time(post_id: str) -> datetime:
    """When an X post was created, from its id (a snowflake: milliseconds in the top bits), to the
    second like X's own created_at."""
    ms = (int(post_id) >> 22) + _X_EPOCH_MS
    return datetime.fromtimestamp(ms // 1000, UTC)


def _hwz_started(http: CachedHttp, url: str) -> datetime | None:
    """A HardwareZone thread's start: the first timestamp on its first page."""
    try:
        response = http.get("pages", url)
    except FetchError:
        return None
    match = _TIME_TAG.search(response.text()) if response.ok else None
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None


_TITLE_SUFFIX = re.compile(r"( \| Page \d+)?( \| HardwareZone Forums| : r/\w+)$")


def _thread_title(title: str) -> str:
    return _TITLE_SUFFIX.sub("", " ".join(title.split()))


_PLATFORMS = {"x.com": "x", "reddit.com": "reddit", "forums.hardwarezone.com.sg": "hardwarezone"}
_DOC_TYPES = {"x": "X post", "reddit": "Reddit thread", "hardwarezone": "Forum thread"}


def _forum_doc(
    http: CachedHttp,
    source: str,
    url: str,
    title: str | None,
    text: str | None,
    search_date: datetime | None,
    target: SocialTarget,
    query: str,
) -> SocialDoc | None:
    """A dated social document from a search hit, or None when it cannot be dated.

    X posts are dated from their id, HardwareZone threads from their first page, and Reddit
    threads from the search API's date (Reddit refuses page fetches, so Tavily's undated Reddit
    hits are dropped)."""
    domain = domain_of(url)
    platform = next(
        (p for d, p in _PLATFORMS.items() if domain == d or domain.endswith("." + d)), None
    )
    text = (text or "").strip()[:_TEXT_MAX_CHARS]
    if platform is None or len(text) < MIN_POST_CHARS:
        return None
    post_id = None
    if platform == "x":
        status = _X_STATUS.match(url)
        if not status:
            return None  # a profile or search page, not a post
        post_id = status.group(1)
        url, published = x_status_url(post_id), x_post_time(post_id)
    elif platform == "hardwarezone":
        thread = _HWZ_THREAD.match(url)
        if not thread:
            return None
        url = thread.group(1)
        published = _hwz_started(http, url)
    else:
        url, published = url.split("?")[0], search_date
    if published is None:
        return None
    doc = RawDocument(
        source=source,
        collection="social",
        doc_type=_DOC_TYPES[platform],
        url=url,
        title=_x_title(text) if platform == "x" else _thread_title(title or domain),
        published_at=published,
        text=text,
    )
    return SocialDoc(
        doc,
        SocialMeta(platform=platform, subject_id=target.subject_id, query=query, post_id=post_id),
    )


TAVILY_SEARCH = "https://api.tavily.com/search"
EXA_SEARCH = "https://api.exa.ai/search"


class _TavilyHit(BaseModel):
    url: str
    title: str | None = None
    content: str | None = None
    raw_content: str | None = None


class _TavilyHits(BaseModel):
    results: list[_TavilyHit]


class _ExaHit(BaseModel):
    url: str
    title: str | None = None
    published_date: str | None = Field(default=None, alias="publishedDate")
    text: str | None = None


class _ExaHits(BaseModel):
    results: list[_ExaHit]


def forum_search(
    http: CachedHttp, tavily_key: str, exa_key: str, target: SocialTarget, window: Window
) -> list[SocialDoc]:
    """One Tavily search (Reddit, HardwareZone and X) and one Exa search (Reddit and
    HardwareZone) for a target over the ingest window. Hits must be dated inside the window and,
    for an entity, name it."""
    query = target.forum_query
    hits: list[SocialDoc | None] = []
    tavily = http.post_json(
        "tavily",
        TAVILY_SEARCH,
        {
            "query": query,
            "topic": "general",
            "search_depth": "basic",
            "max_results": FORUM_RESULTS,
            "start_date": str(window.start),
            "end_date": str(window.t0),
            "include_domains": [*SOCIAL_DOMAINS, "x.com"],
            "include_raw_content": "text",
        },
        headers={"Authorization": f"Bearer {tavily_key}"},
    )
    if tavily.ok:
        for r in _TavilyHits.model_validate_json(tavily.content).results:
            # An X page's raw content is mostly sign-in chrome; the snippet is the post.
            x_page = domain_of(r.url) == "x.com"
            text = r.content if x_page else r.raw_content or r.content
            hits.append(_forum_doc(http, "tavily", r.url, r.title, text, None, target, query))
    exa = http.post_json(
        "exa",
        EXA_SEARCH,
        {
            "query": query,
            "type": "auto",
            "numResults": FORUM_RESULTS,
            "includeDomains": list[JSONValue](SOCIAL_DOMAINS),
            "startPublishedDate": f"{window.start}T00:00:00.000Z",
            "endPublishedDate": f"{window.t0}T23:59:59.999Z",
            "contents": {"text": {"maxCharacters": _TEXT_MAX_CHARS}},
        },
        headers={"x-api-key": exa_key},
    )
    if exa.ok:
        for r in _ExaHits.model_validate_json(exa.content).results:
            found = datetime.fromisoformat(r.published_date) if r.published_date else None
            hits.append(_forum_doc(http, "exa", r.url, r.title, r.text, found, target, query))
    matcher = next((m for m in subject_matchers() if m.id == target.subject_id), None)
    # Thread pages share the thread's URL; the first copy wins.
    unique = list({h.doc.url: h for h in reversed([h for h in hits if h is not None])}.values())
    return [
        h
        for h in reversed(unique)
        if window.contains(h.doc.published_at)
        and (
            matcher is None
            or matcher.type != "entity"
            or matcher.matches(normalize(f"{h.doc.title}\n{h.doc.text}"))
        )
    ]


# --- One ingest's social search ------------------------------------------------------------

_FORUM_WORKERS = 4


def fetch_social(
    http: CachedHttp,
    x_token: str,
    tavily_key: str,
    exa_key: str,
    window: Window,
    spend: XSpend,
    subject_ids: set[str] | None = None,
    log: Callable[[str], None] = print,
    fail: Callable[[str], None] = print,
) -> list[SocialDoc]:
    """Official account posts, X posts, then forum threads, for every target (or the targets in
    `subject_ids`). X searches run one at a time so each is charged to the spend only when it
    reached X. Errors go to `fail` and skip that search."""
    targets = [t for t in TARGETS if subject_ids is None or t.subject_id in subject_ids]
    docs: list[SocialDoc] = []
    # Official accounts first, so a post their search shares with a keyword search stays official.
    if x_token:
        try:
            docs += official_search(http, x_token, targets, window, spend, log, fail)
        except FetchError as e:
            fail(str(e))
    for target in [t for t in targets if t.x_query] if x_token else []:
        try:
            docs += x_search(http, x_token, target, window, spend, log)
        except FetchError as e:
            fail(str(e))

    def forums(target: SocialTarget) -> list[SocialDoc]:
        try:
            return forum_search(http, tavily_key, exa_key, target, window)
        except FetchError as e:
            fail(f"forums {target.subject_id}: {e}")
            return []

    with ThreadPoolExecutor(_FORUM_WORKERS) as pool:
        for target, found in zip(targets, pool.map(forums, targets), strict=True):
            log(f"forums {target.subject_id}: {len(found)} threads and posts")
            docs += found
    return docs
