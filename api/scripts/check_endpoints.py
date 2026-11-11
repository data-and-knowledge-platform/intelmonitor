"""Hit every API endpoint against the running server for a sample of subjects and assert each
returns data. Writes artifacts/api-check/<timestamp>/results.json.

Usage (from api/): uv run python scripts/check_endpoints.py [--base https://intelmonitor.api.localhost]
It only reads: every call is a GET.
"""

import argparse
import json
import ssl
import sys
import time
from collections.abc import Callable
from datetime import datetime
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
    need(len(subjects_only["nodes"]) > 1, f"{sid}: subject-only graph")
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
    args = parser.parse_args()
    c = Check(args.base)
    need(c.get("/health")["status"] == "ok", "health")
    subjects = c.get("/subjects")
    need(len(subjects) == len(all_subjects()), "every seeded subject")

    steps: list[tuple[str, Callable[[], object]]] = [
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

    out = ARTIFACTS_DIR / "api-check" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    paths = sorted({f"{r['method']} {r['path']}" for r in c.results})
    (out / "results.json").write_text(
        json.dumps({"base": args.base, "steps": outcomes, "calls": c.results}, indent=2)
    )
    failed = [o for o in outcomes if not o["ok"]]
    passed = len(outcomes) - len(failed)
    print(
        f"\n{passed}/{len(outcomes)} steps passed, {len(c.results)} calls ({len(paths)} distinct)"
    )
    print(f"results in {out / 'results.json'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
