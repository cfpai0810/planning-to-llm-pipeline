# =============================================================================
# src/answer.py: what a question gets back
# =============================================================================
"""
The answer (the reviewer's Phase 2 brief, 6.2). An Answer is frozen and
holds: the validated specification; the SQL and its SHA-256; the columns
(name, kind, unit); the rows, every cell as text (a figure as the exact
Decimal the engine gave, twelve places, or the marker "not meaningful" where
the rules give it; a dimension's member; a variance label); row_count and
truncated (the query fetches one row more than the limit, to know); the
coverage (the months asked for, the months held, which with a comparison are
the months both versions hold, and the version compared with) and the gaps
(each entity-month missing inside the held range, as (entity, month)); the
outcome, None when there are figures and no_data when the window holds no
month; the result hash (the SHA-256 of the canonical JSON of the rows); and
the provenance of the session (src/session.py).

build() makes an Answer from a compiled query and the rows the engine
returned: the query's columns, then whether a row was found, the months held
and the gaps (src/compiler.py). Nothing in an Answer is rounded: a figure is
rounded once, in Python, where it is shown (part 2e, shown()), half away from
zero: money in whole euros, a percentage to one place, percentage points to
one place, revenue per unit to two places, a count of units or FTE as it is.

This module imports neither the engine nor the query parser
(tests/test_boundaries.py).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from src import outcomes
from src.outcomes import Outcome

__all__ = ["NOT_MEANINGFUL", "Answer", "Coverage", "build", "result_sha256", "shown"]

NOT_MEANINGFUL = "not meaningful"
COMPARED = {"budget": "Budget", "forecast": "Forecast"}


@dataclass(frozen=True)
class Coverage:
    months_asked: tuple
    months_held: tuple            # with a comparison, the months both versions hold
    compared_with: Optional[str]  # the version compared with, or None


@dataclass(frozen=True)
class Answer:
    spec: object                  # the validated specification (src/spec.py)
    sql: str
    sql_sha256: str
    columns: tuple                # (name, kind, unit)
    rows: tuple                   # tuples of text
    row_count: int
    truncated: bool
    coverage: Coverage
    gaps: tuple                   # (entity, month), in month order, then list order
    outcome: Optional[Outcome]    # None with figures; no_data when no month is held
    result_sha256: str
    provenance: dict


def _cell(value, column: tuple) -> str:
    name, kind, _ = column
    if kind in ("dimension", "label"):
        return "" if value is None else str(value)
    if value is None:
        return NOT_MEANINGFUL
    return format(value if isinstance(value, Decimal) else Decimal(value), "f")


def result_sha256(rows: tuple) -> str:
    """The SHA-256 of the canonical JSON of the rows."""
    text = json.dumps([list(r) for r in rows], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build(compiled, validated, fetched: list, provenance: dict) -> Answer:
    """The Answer to a validated specification from the rows its compiled query returned."""
    width = len(compiled.columns)
    first = fetched[0]
    months_held = tuple(first[width + 1] or ())
    gaps = tuple(tuple(g) for g in (first[width + 2] or ()))
    found = [row for row in fetched if row[width]]
    limit = validated.spec.limit
    rows = tuple(tuple(_cell(v, c) for v, c in zip(row[:width], compiled.columns))
                 for row in found[:limit])
    coverage = Coverage(tuple(validated.months), months_held,
                        COMPARED.get(validated.spec.comparison))
    return Answer(spec=validated.spec, sql=compiled.sql, sql_sha256=compiled.sql_sha256,
                  columns=compiled.columns, rows=rows, row_count=len(rows),
                  truncated=len(found) > limit, coverage=coverage, gaps=gaps,
                  outcome=None if found else outcomes.NO_DATA,
                  result_sha256=result_sha256(rows), provenance=provenance)


# == Showing a figure (part 2e) ===================================================================

def _rounded(value: Decimal, places: str) -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)   # half away from zero


def shown(text: str, unit: Optional[str]) -> str:
    """A figure of an Answer's rows as a person reads it, rounded once, here: money in whole
    euros, a percentage (a ratio held as a fraction) to one place, percentage points to one
    place, euros per unit to two places, and a count (units, FTE) exact, with its unit."""
    if text == NOT_MEANINGFUL:
        return text
    value = Decimal(text)
    if unit == "EUR":
        return f"{_rounded(value, '1'):,} EUR"
    if unit == "%":
        return f"{_rounded(value * 100, '0.1')}%"
    if unit == "pp":
        return f"{_rounded(value, '0.1')} pp"
    if unit == "EUR per unit":
        return f"{_rounded(value, '0.01')} EUR per unit"
    exact = value.normalize() if value != value.to_integral_value() else _rounded(value, "1")
    return f"{exact:,f}" + (f" {unit}" if unit else "")
