"""Hit every API endpoint against the running server for a sample of subjects and assert each
returns data. Writes artifacts/api-check/<timestamp>/results.json, or the file `--out` names.

Usage (from api/):
    uv run python scripts/check_endpoints.py [--base <api url>] [--out <file>]
The base defaults to https://intelmonitor.api.localhost.
It only reads: every call is a GET, and it calls no model. The e2e walkthrough runs it as its last
step and keeps the results as api-check.json.
"""

import argparse
import json
import ssl
import sys
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from intelmonitor.config import ARTIFACTS_DIR
from intelmonitor.pipeline.subjects import all_subjects

SAMPLE = [
    "financial-regulation",
    "cyber-resilience",
    "dbs",
    "mas",
    "microsoft-365",
    "singapore",
]
# What an entity or topic page carries, and nothing else.
PAGE_FIELDS = {"subject", "run", "brief", "upcoming", "trend", "impacts"}
# Seed facts the catalogue and the list pages must show.
SEED_COUNTS = {"entity": 89, "topic": 30, "group": 17}
FUNCTION_COUNT = 5
PRESET_ENTITY = "mas"
# What a catalogue entry carries; the count fields /subjects once had stay out.
SUMMARY_FIELDS = {"id", "name", "type", "kind", "logo", "logo_full", "icon"} | {
    "aliases",
    "members",
    "function",
}
LISTS = {"entities": "entity", "topics": "topic"}
PERIODS = ("week", "4w", "3m")
PERIOD_DAYS = {"4w": 28, "3m": 91}
LEVELS = ("high", "medium", "low")
# Operations other than GET the API may serve, as "METHOD /path": why. The API holds no viewer
# state, so there are none; step 14's store publishing would be named here if it ever became an
# endpoint rather than `im publish`.
WRITE_EXCEPTIONS: dict[str, str] = {}

type Json = Any  # response bodies are checked field by field below


class Check:
    def __init__(self, base: str) -> None:
        ca = Path.home() / ".portless" / "ca.pem"
        verify = ssl.create_default_context(cafile=str(ca)) if ca.exists() else True
        self.http = httpx.Client(base_url=base, verify=verify, timeout=600)
        self.results: list[dict[str, object]] = []

    def call(self, method: str, path: str, expect: int = 200, **kwargs: Any) -> Json:
        started = time.monotonic()
        r = self.http.request(method, path, **kwargs)
        ms = int((time.monotonic() - started) * 1000)
        if r.status_code != expect:
            raise AssertionError(f"{method} {path}: {r.status_code} {r.text[:300]}")
        self.results.append({"method": method, "path": path, "status": r.status_code, "ms": ms})
        return r.json() if r.content and "json" in r.headers.get("content-type", "") else r.text

    def get(self, path: str, **kwargs: Any) -> Json:
        return self.call("GET", path, **kwargs)


def need(condition: object, what: str) -> None:
    if not condition:
        raise AssertionError(what)


def check_subject(c: Check, sid: str) -> dict[str, int]:
    detail = c.get(f"/subjects/{sid}")
    kind = detail["type"]
    links = detail["links"]
    need(links["topics"] or links["groups"] or links["entities"], f"{sid}: no links")

    page = c.get(f"/subjects/{sid}/page")
    need(set(page) == PAGE_FIELDS, f"{sid}: page fields {sorted(page)}")
    need(page["brief"], f"{sid}: no brief")
    if kind == "topic":
        need(page["upcoming"], f"{sid}: no upcoming dates")

    hits = c.get("/search", params={"q": detail["name"]})["subjects"]
    need(hits and hits[0]["subject"]["id"] == sid, f"{sid}: search by name")
    if detail["aliases"]:
        hits = c.get("/search", params={"q": detail["aliases"][0]})["subjects"]
        need(any(h["subject"]["id"] == sid for h in hits), f"{sid}: search by alias")

    one = c.get("/graph", params={"center": sid, "hops": 1})
    two = c.get("/graph", params={"center": sid, "hops": 2})
    need(len(one["nodes"]) > 1 and one["edges"], f"{sid}: graph at 1 hop")
    need(len(two["nodes"]) >= len(one["nodes"]), f"{sid}: graph at 2 hops")
    need(any(n["has_new"] for n in two["nodes"]), f"{sid}: no new developments in graph")
    subjects_only = c.get(
        "/graph",
        params={"center": sid, "hops": 2, "node_type": ["topic", "group", "entity"]},
    )
    need(all(n["type"] != "development" for n in subjects_only["nodes"]), "node type filter")
    # A topic links to no subject, so without developments its graph is the topic alone.
    need(len(subjects_only["nodes"]) > (kind != "topic"), f"{sid}: subject-only graph")
    affects = c.get("/graph", params={"center": sid, "link_type": "AFFECTS", "window_days": 14})
    need(all(e["type"] == "AFFECTS" for e in affects["edges"]), "link type filter")

    devs = c.get("/developments", params={"subject_id": sid, "limit": 5})["developments"]
    need(devs, f"{sid}: no developments")
    check_evidence(c, devs[0]["id"])
    changed = c.get("/developments", params={"subject_id": sid, "changed": "run"})
    need(all(d["change"] for d in changed["developments"]), f"{sid}: changed=run")

    indicators = c.get(f"/subjects/{sid}/indicators")["indicators"]
    need(all(i["state"] != "no_data" for i in indicators), f"{sid}: an indicator has no data")
    if indicators:
        first = indicators[0]["series"]["id"]
        need(c.get(f"/series/{first}")["observations"], f"{sid}: {first} has no observations")
    return {"developments": len(devs), "new_or_updated": len(changed["developments"])}


def check_evidence(c: Check, dev_id: str) -> None:
    dev = c.get(f"/developments/{dev_id}")
    need(dev["sources"], f"{dev_id}: no sources")
    for src in dev["sources"]:
        for f in src["findings"]:
            need(f["quotes"], f"{dev_id}: finding without quotes")
            p = f["passage"]
            for q, h in zip(f["quotes"], p["highlights"], strict=True):
                need(p["text"][h["start"] : h["end"]] == q["text"], f"{dev_id}: highlight")
    doc = c.get(f"/documents/{dev['sources'][0]['document_id']}")
    need(doc["quotes"], "document: no quotes")
    for q in doc["quotes"]:
        need(doc["text"][q["start"] : q["end"]] == q["text"], "document: quote offsets")


def check_catalogue(c: Check) -> str:
    """/subjects holds the seed's subjects with a topic's function and no count fields, and
    /functions the five functions, owning every topic once, with presets of seed ids and MAS."""
    subjects = c.get("/subjects")
    by_type = {t: [s for s in subjects if s["type"] == t] for t in SEED_COUNTS}
    for t, n in SEED_COUNTS.items():
        need(len(by_type[t]) == n, f"{n} {t} subjects, got {len(by_type[t])}")
    for s in subjects:
        need(set(s) == SUMMARY_FIELDS, f"{s['id']}: catalogue fields {sorted(s)}")
        need((s["function"] is not None) == (s["type"] == "topic"), f"{s['id']}: function")
    functions = c.get("/functions")
    need(len(functions) == FUNCTION_COUNT, f"{FUNCTION_COUNT} functions")
    names = {f["id"]: f["name"] for f in functions}
    owner = {t: f["id"] for f in functions for t in f["topics"]}
    need(sum(len(f["topics"]) for f in functions) == len(owner), "a topic in two functions")
    topics = {s["id"]: s for s in by_type["topic"]}
    need(set(owner) == set(topics), "functions own exactly the topics")
    for tid, t in topics.items():
        need(t["function"] == {"id": owner[tid], "name": names[owner[tid]]}, f"{tid}: function")
    entities = {s["id"] for s in by_type["entity"]}
    for f in functions:
        w = f["watchlist"]
        need(PRESET_ENTITY in w["entities"], f"{f['id']}: preset without {PRESET_ENTITY}")
        need(set(w["entities"]) <= entities, f"{f['id']}: preset names a non-entity")
        need(set(w["topics"]) <= set(topics), f"{f['id']}: preset names a non-topic")
    detail = c.get(f"/subjects/{by_type['topic'][0]['id']}")
    need(detail["function"] == by_type["topic"][0]["function"], "detail carries the function")
    return f"{len(subjects)} subjects, {len(functions)} functions"


def check_lists(c: Check) -> str:
    """Both lists for each period: every entity or topic once, ranked High, Medium, then name;
    `since` from the previous run; each top development at the row's highest level; counts
    equal to the developments feed's priority facet for the sample; bad names refused."""
    latest = c.get("/runs/latest")
    as_of = date.fromisoformat(latest["as_of"])
    # Run ids are their as-of dates; a first run, with no previous one, looks back 7 days.
    previous = latest["previous_run_id"]
    week = date.fromisoformat(previous) if previous else as_of - timedelta(days=7)
    expected = {"week": week} | {p: as_of - timedelta(days=d) for p, d in PERIOD_DAYS.items()}
    sizes: list[str] = []
    for name, kind in LISTS.items():
        by_period: dict[str, dict[str, Json]] = {}
        for period in PERIODS:
            body = c.get(f"/lists/{name}", params={"period": period})
            rows = body["rows"]
            label = f"{name} {period}"
            need(body["period"] == period and body["run"]["id"] == latest["id"], f"{label}: run")
            need(body["since"] == expected[period].isoformat(), f"{label}: since {body['since']}")
            need(len(rows) == SEED_COUNTS[kind], f"{label}: {len(rows)} rows")
            need(len({r["id"] for r in rows}) == len(rows), f"{label}: a repeated row")
            need(all(r["type"] == kind for r in rows), f"{label}: a row of another type")
            ranked = sorted(
                rows,
                key=lambda r: (-r["counts"]["high"], -r["counts"]["medium"], r["name"].casefold()),
            )
            need([r["id"] for r in ranked] == [r["id"] for r in rows], f"{label}: ranking")
            for r in rows:
                counts = r["counts"]
                highest = next((lv for lv in LEVELS if counts[lv]), None)
                top = r["top"]
                need(
                    (top["priority"].lower() if top else None) == highest,
                    f"{label} {r['id']}: top priority",
                )
                if kind == "topic":
                    need(not r["groups"] and r["function"], f"{label} {r['id']}: function")
                    need(top is None or top["topic"] is None, f"{label} {r['id']}: top topic")
                else:
                    need(r["groups"] and r["function"] is None, f"{label} {r['id']}: groups")
                    topic = top and top["topic"]
                    need(not topic or topic["type"] == "topic", f"{label} {r['id']}: top topic")
            by_period[period] = {r["id"]: r for r in rows}
            sizes.append(f"{label} {sum(1 for r in rows if r['top'])}")
        for sid, r in by_period["week"].items():
            for period in PERIODS[1:]:
                other = by_period[period][sid]
                need((other["new"], other["latest"]) == (r["new"], r["latest"]), f"{sid}: new")
            wider = by_period["3m"][sid]["counts"]
            narrower = by_period["4w"][sid]["counts"]
            need(all(wider[lv] >= narrower[lv] for lv in LEVELS), f"{sid}: 3m below 4w")
        for sid in (s for s in SAMPLE if s in by_period["week"]):
            for period in PERIODS:
                days = (as_of - expected[period]).days
                feed = c.get("/developments", params={"subject_id": sid, "days": days, "limit": 1})
                facet = {v["value"].lower(): v["count"] for v in feed["facets"]["priority"]}
                counts = by_period[period][sid]["counts"]
                need(
                    all(counts[lv] == facet.get(lv, 0) for lv in LEVELS),
                    f"{sid} {period}: counts {counts} against the feed's {facet}",
                )
    c.get("/lists/groups", expect=422)
    c.get("/lists/entities", params={"period": "1y"}, expect=422)
    return "rows with a top development: " + ", ".join(sizes)


def check_read_only(c: Check) -> str:
    """The OpenAPI schema lists only GET operations, besides the named exceptions, so no endpoint
    can hold viewer state."""
    schema = c.get("/openapi.json")
    operations = [
        f"{method.upper()} {path}"
        for path, item in schema["paths"].items()
        for method in item
        if method in {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
    ]
    writes = [o for o in operations if not o.startswith("GET ") and o not in WRITE_EXCEPTIONS]
    need(not writes, f"operations other than GET: {writes}")
    return f"{len(operations)} operations, all GET"


def check_runs_and_sources(c: Check) -> str:
    runs = c.get("/runs")
    complete = [r for r in runs if r["status"] == "complete"]
    need(complete, "a complete run")
    need(all(r["documents"] and r["developments"] and r["result"] for r in complete), "counts")
    latest = c.get("/runs/latest")
    need(latest["id"] == max(r["id"] for r in complete), "latest run")
    sources = c.get("/data-sources")
    need(sources["total_documents"] and sources["sources"], "data sources")
    return f"{len(complete)} complete executions, latest run {latest['id']}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="https://intelmonitor.api.localhost")
    parser.add_argument("--out", type=Path, help="results file, else under artifacts/api-check/")
    args = parser.parse_args()
    c = Check(args.base)
    need(c.get("/health")["status"] == "ok", "health")
    subjects = c.get("/subjects")
    need(len(subjects) == len(all_subjects()), "every seeded subject")

    steps: list[tuple[str, Callable[[], object]]] = [
        ("read only", lambda: check_read_only(c)),
        ("catalogue", lambda: check_catalogue(c)),
        ("lists", lambda: check_lists(c)),
        *((f"subject {sid}", lambda sid=sid: check_subject(c, sid)) for sid in SAMPLE),
        ("runs and data sources", lambda: check_runs_and_sources(c)),
    ]
    outcomes: list[dict[str, object]] = []
    for name, step in steps:
        try:
            detail = step()
            outcomes.append({"step": name, "ok": True, "detail": detail})
            print(f"ok    {name}" + (f"  {detail}" if detail else ""))
        except (AssertionError, KeyError, IndexError, TypeError, httpx.HTTPError) as error:
            outcomes.append(
                {"step": name, "ok": False, "error": f"{type(error).__name__}: {error}"}
            )
            print(f"FAIL  {name}: {type(error).__name__}: {error}")

    out: Path = args.out or (
        ARTIFACTS_DIR / "api-check" / datetime.now().strftime("%Y%m%d-%H%M%S") / "results.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    paths = sorted({f"{r['method']} {r['path']}" for r in c.results})
    out.write_text(json.dumps({"base": args.base, "steps": outcomes, "calls": c.results}, indent=2))
    failed = [o for o in outcomes if not o["ok"]]
    passed = len(outcomes) - len(failed)
    print(
        f"\n{passed}/{len(outcomes)} steps passed, {len(c.results)} calls ({len(paths)} distinct)"
    )
    print(f"results in {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
