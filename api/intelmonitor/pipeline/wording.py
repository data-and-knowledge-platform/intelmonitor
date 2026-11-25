"""Numbers in words, written by code: series values in their units, dates and periods, peg sides
and which way an FX change moved the SGD. Key data (key_data.py) and detections (detectors.py)
share them, so a brief and a detected development state a value the same way.

`answer_problems` checks text Claude wrote (briefs) against those rules: numbers it
was not given, currency pair names and rates said to go up or down."""

import re
from datetime import date

from intelmonitor.seed import Deviation, Frequency, SeriesDef

_FX_UNIT = re.compile(r"SGD per ([A-Z]{3})")
_SCALED_UNIT = re.compile(r"([A-Z]{3}) (m|bn)")


def num(v: float, decimals: int) -> str:
    return f"{v:,.{decimals}f}"


def value(s: SeriesDef, v: float) -> str:
    """A value in the series' unit: 70.3%, USD 73.21bn, SGD 1,234m, HKD 4,512bn, 0.3128 SGD per
    MYR."""
    match s.unit:
        case "%":
            return f"{v:.{s.decimals}f}%"
        case "index":
            return num(v, s.decimals)
        case "SGD m":
            return f"SGD {v:,.0f}m"
        case _ if m := _SCALED_UNIT.fullmatch(s.unit):  # as published: HKD 4,512bn
            return f"{m.group(1)} {num(v, s.decimals)}{m.group(2)}"
        case "USD" | "SGD" if abs(v) >= 1e9:
            return f"{s.unit} {v / 1e9:,.2f}bn"
        case "USD" | "SGD" if abs(v) >= 1e6:
            return f"{s.unit} {v / 1e6:,.1f}m"
        case "USD" | "SGD":
            return f"{s.unit} {num(v, s.decimals)}"
        case _:
            return f"{num(v, s.decimals)} {s.unit}"


def day(d: date) -> str:
    return f"{d.day} {d:%b %Y}"


def period(d: date, frequency: Frequency) -> str:
    match frequency:
        case "daily":
            return day(d)
        case "monthly":
            return f"{d:%b %Y}"
        case "quarterly":
            return f"Q{(d.month - 1) // 3 + 1} {d.year}"
        case "yearly":
            return str(d.year)


def fx_currency(s: SeriesDef) -> str | None:
    """For an FX series (SGD per unit of a currency), that currency's code."""
    quoted = _FX_UNIT.fullmatch(s.unit)
    return quoted.group(1) if quoted else None


def fx_move(s: SeriesDef, change: float) -> str | None:
    """For an FX series, a change in percent told only by the SGD's side: "SGD weakened 0.23%
    against the USD". A rate going up or down is easy to read backwards, so FX changes never say
    it. The size is the change in SGD per unit, as the indicators give it."""
    currency = fx_currency(s)
    if currency is None:
        return None
    if round(change, 2) == 0:
        return f"SGD unchanged against the {currency}"
    moved = "weakened" if change > 0 else "strengthened"
    return f"SGD {moved} {abs(change):.2f}% against the {currency}"


def peg_side(s: SeriesDef, source: Deviation, by_id: dict[str, SeriesDef], v: float) -> str:
    """A deviation value as the price and its side of the target: 0.9987 USD (0.13% below)."""
    target = by_id[source.input]
    price = source.target * (1 + v / 100)
    rounded = round(abs(v), s.decimals)
    side = (
        "at the peg"
        if rounded == 0
        else f"{rounded:.{s.decimals}f}% {'below' if v < 0 else 'above'}"
    )
    return f"{num(price, target.decimals)} {target.unit} ({side})"


# Numbers a brief could invent: decimals and percentages. Whole numbers ("30 days") pass.
_NUMBER = re.compile(r"\d[\d,]*\.\d+%?|\d[\d,]*%")
_PER_CENT = re.compile(r"\s*(?:per ?cent|percent)\b", re.IGNORECASE)


def unsupported_numbers(summary: str, allowed: str) -> list[str]:
    """Decimals and percentages in `summary` that appear nowhere in `allowed` (the facts and
    development text the prompt gave), ignoring thousands separators and reading "per cent" as
    "%"."""
    plain = _PER_CENT.sub("%", allowed.replace(",", ""))
    return [n for n in dict.fromkeys(_NUMBER.findall(summary)) if n.replace(",", "") not in plain]


# Currency pair names (SGD/MYR, USD-SGD, SGDMYR), and a rate said to go up or down: after a rate
# or a counter currency ("SGD per USD up 0.23%", "against the MYR, up 0.55%", "vs MYR, up 0.03%",
# "SGD/MYR rose"), or before one with no SGD before the verb ("up 0.03% vs MYR"). Readers take a
# pair or a rate's rise either way round, so briefs tell FX moves
# only by the SGD's side, as the facts do: "SGD weakened 0.23% against the USD" or "vs the USD".
# Codes are upper case even where the rest of a pattern ignores case.
_PAIR = r"(?-i:SGD ?[/:\-–] ?[A-Z]{3}|[A-Z]{3} ?[/:\-–] ?SGD|SGD[A-Z]{3}|[A-Z]{3}SGD)"
_CODE = r"(?-i:[A-Z]{3})"
_FX_PAIR = re.compile(rf"\b{_PAIR}\b")
_MOVED = (
    r"(?:up|down|rose|fell|rising|falling|higher|lower|gained|lost|climbed|dropped|increased|"
    r"decreased|appreciated|depreciated)"
)
_COUNTER = rf"(?:{_CODE} per {_CODE}|(?:against|vs\.?|versus) (?:the )?{_CODE}|{_PAIR})"
# A word or a number, and up to three of them between the rate and the verb ("against the MYR
# on the week, up"); a gap never crosses the end of a sentence or clause.
_WORD = r"(?:\d[\d.,]*%?|[^\s.;]+?)"
_GAP = rf"(?:[\s,:()]+{_WORD}){{0,3}}?[\s,:()]+"
_FX_RATE_MOVE = re.compile(
    rf"\b{_COUNTER}{_GAP}(?:(?:was|is|has|had)\s+)?{_MOVED}\b"
    rf"|(?<!SGD )\b{_MOVED}(?:[\s,]+{_WORD}){{0,2}}?[\s,]+"
    rf"(?:against|vs\.?|versus) (?:the )?{_CODE}\b",
    re.IGNORECASE,
)


def fx_pairs(summary: str) -> list[str]:
    """Currency pair names in `summary`, which an answer on FX series may not use."""
    return list(dict.fromkeys(_FX_PAIR.findall(summary)))


def fx_rate_moves(summary: str) -> list[str]:
    """Phrases in `summary` that move a rate up or down instead of saying how the SGD moved."""
    return list(dict.fromkeys(m.group(0) for m in _FX_RATE_MOVE.finditer(summary)))


def answer_problems(summary: str, allowed: str, fx: bool) -> list[str]:
    """Why an answer is asked again: numbers found in neither the facts nor the developments
    (`allowed`), and, for a question with FX series (`fx`), currency pair names and rates said
    to go up or down."""
    problems: list[str] = []
    if nums := unsupported_numbers(summary, allowed):
        problems.append(f"numbers in neither the facts nor the developments: {', '.join(nums)}")
    if fx and (pairs := fx_pairs(summary)):
        problems.append(
            f"currency pairs {', '.join(pairs)}: say how the SGD moved against the currency, "
            'as the facts do ("SGD weakened 0.32% against the MYR")'
        )
    if fx and (moves := fx_rate_moves(summary)):
        quoted = ", ".join(f'"{m}"' for m in moves)
        problems.append(
            f"rates said to go up or down ({quoted}): say only how the SGD moved against the "
            'currency, as the facts do ("SGD weakened 0.32% against the MYR")'
        )
    return problems
