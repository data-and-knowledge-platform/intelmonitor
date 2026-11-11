"""Key data: the series a subject's Overview shows as Key data, which its brief states too
(pipeline/summaries.py), and each series stated in a sentence written by code. Everything is read
from what was available by the end of the date, so a run backfilled to a past date never states a
later value."""

from collections.abc import Iterable, Mapping
from datetime import date, timedelta

from pydantic import BaseModel, Field

from intelmonitor.pipeline.detectors import Detection, materiality_level
from intelmonitor.pipeline.indicators import ChangeWindow, Indicator, IndicatorChange
from intelmonitor.pipeline.wording import day, fx_move, peg_side, period, value
from intelmonitor.seed import Deviation, SeriesDef, load_series
from intelmonitor.store.series import Observation


def mapped_series(subject_id: str) -> list[SeriesDef]:
    """The series mapped to a subject, primary or context, in catalogue order."""
    return [
        s
        for s in load_series().series
        if subject_id in s.subjects.primary or subject_id in s.subjects.context
    ]


# Key data rows per role after the flagged ones.
KEY_DATA_PER_ROLE = 3
_FREQUENCIES = ("daily", "monthly", "quarterly", "yearly")


class KeyData(BaseModel):
    """The series an Overview's Key data shows, by id, in order. The brief states the same ones
    (pipeline/summaries.py)."""

    flagged: list[str] = Field(
        description="Listed series a live detector event flags, from a feed that still has data, "
        "worst event first"
    )
    rows: list[str] = Field(
        description=f"Then up to {KEY_DATA_PER_ROLE} of the subject's own (primary) series and "
        f"up to {KEY_DATA_PER_ROLE} context series, one per series group, daily ones first among "
        "the context; a series with no data yet included"
    )


def key_data(
    subject_id: str, indicators: Mapping[str, Indicator], events: Iterable[Detection]
) -> KeyData:
    """The subject's Key data from its listed series in `indicators` and the live detector events
    on them (pipeline/detectors.py `live_events`)."""
    worst: dict[str, int] = {}
    for e in events:
        worst[e.series.id] = max(worst.get(e.series.id, 0), materiality_level(e.detector))
    listed = [s for s in mapped_series(subject_id) if s.listed and s.id in indicators]
    flagged = sorted(
        (s for s in listed if s.id in worst and indicators[s.id].state not in ("no_data", "stale")),
        key=lambda s: (-worst[s.id], subject_id not in s.subjects.primary),
    )

    def one_per_group(rows: Iterable[SeriesDef]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for s in rows:
            if (s.group or s.id) not in seen and len(out) < KEY_DATA_PER_ROLE:
                seen.add(s.group or s.id)
                out.append(s.id)
        return out

    rest = [s for s in listed if s not in flagged]
    own = one_per_group(s for s in rest if subject_id in s.subjects.primary)
    context = one_per_group(
        sorted(
            (s for s in rest if subject_id not in s.subjects.primary),
            key=lambda s: _FREQUENCIES.index(s.frequency),
        )
    )
    return KeyData(flagged=[s.id for s in flagged], rows=[*own, *context])


# ---- sentences


_WINDOW_PHRASE: dict[ChangeWindow, str] = {
    "1w": "on the week",
    "1m": "on the month",
    "3m": "over 3 months",
    "1y": "on the year",
}


def _change(s: SeriesDef, ind: Indicator, c: IndicatorChange) -> str | None:
    if c.change is None or c.since is None or c.from_value is None:
        return None
    if ind.change_basis == "points":
        size = f"{abs(c.change):.{s.decimals}f} points"
        flat = round(c.change, s.decimals) == 0
    else:
        size = f"{abs(c.change):.2f}%"
        flat = round(c.change, 2) == 0
    move = fx_move(s, c.change) or (
        "unchanged" if flat else f"{'up' if c.change > 0 else 'down'} {size}"
    )
    since = period(c.since, s.frequency)
    at = "on" if s.frequency == "daily" else "for"
    return f"{move} {_WINDOW_PHRASE[c.window]} (from {value(s, c.from_value)} {at} {since})"


def _rank(s: SeriesDef, history: list[Observation], latest: Observation, start: date) -> str:
    """Where the latest value sits in the history, as counts rather than a rounded percentile."""
    lower = sum(o.value < latest.value for o in history)
    higher = sum(o.value > latest.value for o in history)
    since = f"the {len(history):,} values since {period(start, s.frequency)}"
    if lower == 0:
        return f"the lowest of {since}"
    if higher == 0:
        return f"the highest of {since}"
    return f"above {lower:,} of {since}"


def series_sentence(
    s: SeriesDef,
    by_id: dict[str, SeriesDef],
    history: list[Observation],
    ind: Indicator,
    as_of: date,
    window_days: int,
) -> str:
    """One series stated in a sentence from its observations known by the end of `as_of` and its
    indicator: the latest value, its changes, a daily series' range over the `window_days` to the
    date and where the latest sits in the history."""
    if ind.latest is None:
        return f"{s.name}: no data available by {day(as_of)}."
    latest = ind.latest
    daily = s.frequency == "daily"
    start = as_of - timedelta(days=window_days) if daily else None
    window = [o for o in history if start is not None and o.period > start]
    low = min(window, key=lambda o: o.value) if window else None
    high = max(window, key=lambda o: o.value) if window else None
    parts: list[str] = []
    if ind.state == "stale":
        parts.append(f"stale: no point since {day(latest.period)}")
    source = s.source
    changes = [c for c in ind.changes if c.change is not None]
    if isinstance(source, Deviation):
        # A peg: how far it strayed in the window, as prices, then the latest.
        if window and low and high:
            furthest = max(abs(o.value) for o in window)
            parts.append(
                f"within {furthest:.{s.decimals}f}% of the peg in the {window_days} days to "
                f"{day(as_of)}; lowest {peg_side(s, source, by_id, low.value)} on "
                f"{day(low.period)}, highest {peg_side(s, source, by_id, high.value)} on "
                f"{day(high.period)}"
            )
        parts.append(f"latest {peg_side(s, source, by_id, latest.value)} on {day(latest.period)}")
        changes = []
    else:
        at = "on" if daily else "for"
        flags: list[str] = []
        if latest.preliminary:
            flags.append("preliminary")
        if not daily:
            flags.append(f"available from {day(latest.available_at.date())}")
        flagged = f" ({', '.join(flags)})" if flags else ""
        parts.append(f"{value(s, latest.value)} {at} {period(latest.period, s.frequency)}{flagged}")
        stated = [text for c in changes if (text := _change(s, ind, c))]
        if stated:
            parts.append(", ".join(stated))
        if window and low and high and len(window) > 1:
            span = f"in the {window_days} days to {day(as_of)}"
            if value(s, low.value) == value(s, high.value):
                parts.append(f"{span} it held at {value(s, low.value)}")
            else:
                parts.append(
                    f"{span} it ranged from {value(s, low.value)} ({day(low.period)}) to "
                    f"{value(s, high.value)} ({day(high.period)})"
                )
        if ind.history_start is not None:
            parts.append(_rank(s, history, latest, ind.history_start))
    for r in ind.revisions:
        parts.append(
            f"revised: {period(r.period, s.frequency)} from {value(s, r.old_value)} to "
            f"{value(s, r.new_value)} on {day(r.revised_at.date())}"
        )
    return f"{s.name}: {'; '.join(parts)}."
