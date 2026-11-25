"""`uv run python -m intelmonitor.eval <step>`: cases [store path] (the gold cases for roles,
adverse and priority, on the app's store by default), adverse (the adverse flag's gold cases
through the extraction prompt, in their own store), link-types [medium|high] (the activated
relationship types' cases through the link_types call, in their own store), subjects (the
development subjects' cases through the headline call, in their own store), impact (the indirect
impact table's cases, in memory, no model call), brief [store path] [subject ...] (the judged
brief eval on a store's latest complete run, the app's by default) or brief-compare [store path]
[subject ...] (the same subjects' briefs on Opus 5.5 beside the stored ones, in a store copy).

The phase A eval of standing answers (v0, v1, v2, gold, judge, score) went with the standing
questions on 4 Nov 2026; its results stay in eval-runs/phase-a-2026-10-05/."""

import asyncio
import sys
from pathlib import Path


def main() -> None:
    from intelmonitor.eval import (
        adverse_cases,
        brief_eval,
        cases,
        impact_cases,
        link_type_cases,
        subject_cases,
    )

    step = sys.argv[1] if len(sys.argv) > 1 else ""
    if step == "adverse":
        asyncio.run(adverse_cases.run())
    elif step == "link-types":
        effort = sys.argv[2] if len(sys.argv) > 2 else None
        if effort is None or effort in ("medium", "high"):
            asyncio.run(link_type_cases.run(effort))
        else:
            raise SystemExit("link-types takes medium or high")
    elif step == "subjects":
        asyncio.run(subject_cases.run())
    elif step == "impact":
        impact_cases.run()
    elif step == "cases":
        cases.run(*(Path(p) for p in sys.argv[2:3]))
    elif step in ("brief", "brief-compare"):
        path = Path(sys.argv[2]) if len(sys.argv) > 2 else brief_eval.DB_PATH
        chosen = sys.argv[3:] or None
        asyncio.run((brief_eval.run if step == "brief" else brief_eval.compare)(path, chosen))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
