"""The independent oracle of part 2b (brief 4.3): every metric, every question of the grid.

    python -m tests.oracle --write        # writes tests/generated/oracle_expected.csv

Written before any compiler, and apart from the pipeline: it imports the standard library,
pandas, openpyxl and src.sample_model only, never the registry, the warehouse, the
extractor, the star schema, a compiler or the SQL files (tests/test_boundaries.py checks
it). The eleven metrics are written out here by hand from the reviewer's table, not read
from the registry; tests/test_oracle.py checks the two lists name the same metrics.

The frames. A pandas DataFrame of every figure of the plan, one row per version, entity,
line and month:
- the clean pair: from src/sample_model.py, the pure-Python Decimal figures that prove the
  workbook, with its Lists and entities;
- the sample pair: the committed sample workbook's cached values, read with openpyxl at
  fixed addresses (allowed in this file and nowhere else): the Lists tab's accounts in
  A4:H16 and entities in A19:C21, and each plan tab's Actual, Budget and Forecast lines in
  rows 7 to 19, 23 to 35 and 39 to 51, months in columns C to N.
The ledger is not read: "Actual" is the workbook's Actual block (rule 1).

The rules (brief 4.2), with the coordinator's leans where the brief left a choice:
- Periods: a month, a quarter, the year to date through June, or FY2026.
- Held. An entity-month is held for a version when the entity has at least one figure for
  that version and month among the lines the registry reads: the leaf accounts, VOL and
  HC (the subtotal rows are never read). The held range runs from the first to the last
  month any permitted entity holds for the version. A gap is an entity-month inside the
  held range that the entity does not hold; a month after the last held month is not held,
  not a gap. A window with no held month is the outcome no_data, never a zero.
- Flows sum over the held entity-months of the window. A difference is the first metric
  less the others. A ratio is the ratio of the sums, never an average of ratios: the
  exact quotient, quantised to 12 places with ROUND_HALF_UP (ties away from zero), held
  as a fraction (0.32 is 32.0%); a zero denominator gives "not meaningful".
- A stock (headcount) takes its closing value: the last month of the window that the
  version holds, across the permitted entities, summed across them; an entity with no
  figure in that month is a gap and is never filled from an earlier month. Grouped by
  month or quarter, each month or quarter closes on its own; grouped by entity or region,
  the window's closing month is used for every row.
- Grouping: a grouped row with no held month is left out of the rows (its gap is listed),
  and a missing row is never a zero. Rows come in list order for entities, regions and
  accounts, and in time order for months and quarters.
- Comparisons match by entity-month (the lean, question 5): both sides sum only the
  entity-months both versions hold, so a gap is left out of both, and months_held is the
  months both hold. A stock compares closing values at the last matched month. With a
  month or quarter grouping only matched months appear.
- Variance is base less comparator. The label follows the metric's polarity:
  favourable, adverse, or no variance at zero; headcount is ungraded and has no label.
  The variance percentage is the variance over the absolute comparator, quantised as a
  ratio is; "not meaningful" when the comparator is zero. A percentage ratio's variance
  is the difference of the two 12-place ratios times 100, in percentage points, with no
  variance percentage. Revenue per unit is a ratio in euros per unit, not a percentage:
  its variance is the plain difference of the two quotients, with no variance percentage
  (a question on the stop page).
- Rounding happens only where a figure is formatted (percent, euros, per_unit); every
  figure in the file is exact.

The file. One row per answer row, with its question and its coverage; a question with no
data is one row with the outcome no_data. Figures are exact decimals; the marker for "not
meaningful" is the literal text not meaningful.
"""

from __future__ import annotations

import argparse
import csv
import functools
import io
import sys
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path
from typing import Optional, Union

import pandas
from openpyxl import load_workbook

from src import sample_model as model

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "planning_model.xlsx"
CLEAN = ROOT / "data" / "planning_model_clean.xlsx"
EXPECTED = ROOT / "tests" / "generated" / "oracle_expected.csv"

NOT_MEANINGFUL = "not meaningful"
PLACES = 12
VERSIONS = ("Actual", "Budget", "Forecast")
MONTHS = tuple(f"2026-{m:02d}" for m in range(1, 13))
QUARTERS = {f"Q{q}": MONTHS[3 * q - 3:3 * q] for q in range(1, 5)}
PERIODS = (tuple(("month", m) for m in MONTHS) + tuple(("quarter", q) for q in QUARTERS)
           + (("ytd", "2026-06"), ("fy", "FY2026")))
COMPARISONS = {"none": None, "budget": "Budget", "forecast": "Forecast"}
ENTITY_SETS = ("group", "ES", "AT", "DE")
COLUMNS = ("version", "entity", "region", "account", "name", "parent", "section", "kind",
           "unit", "period", "amount")
HEADER = ("pair", "metric", "version", "comparison", "entities", "period", "group_by", "group",
          "outcome", "value", "comparator", "variance", "variance_pct", "label", "months_asked",
          "months_held", "gaps")

# -- The eleven metrics, by hand from the reviewer's table (brief 4.2) --------------------
METRICS = ("revenue", "cost_of_sales", "gross_profit", "operating_expenses", "ebit",
           "gross_margin_pct", "ebit_margin_pct", "marketing_pct_of_revenue", "volume",
           "headcount", "revenue_per_unit")
BY_PARENT = {"revenue": "Revenue", "cost_of_sales": "Cost of sales",
             "operating_expenses": "Operating expenses"}
BY_ACCOUNT = {"volume": "VOL", "headcount": "HC"}
STOCKS = ("headcount",)
DIFFERENCES = {"gross_profit": ("revenue", "cost_of_sales"),
               "ebit": ("gross_profit", "operating_expenses")}
# A ratio's operands: a metric, or ("account", code) for the inline sum of one account.
RATIOS = {"gross_margin_pct": ("gross_profit", "revenue"),
          "ebit_margin_pct": ("ebit", "revenue"),
          "marketing_pct_of_revenue": (("account", "6100"), "revenue"),
          "revenue_per_unit": ("revenue", "volume")}
PERCENTAGES = ("gross_margin_pct", "ebit_margin_pct", "marketing_pct_of_revenue")
HIGHER, LOWER, UNGRADED = "higher", "lower", "ungraded"
POLARITY = {"revenue": HIGHER, "cost_of_sales": LOWER, "gross_profit": HIGHER,
            "operating_expenses": LOWER, "ebit": HIGHER, "gross_margin_pct": HIGHER,
            "ebit_margin_pct": HIGHER, "marketing_pct_of_revenue": LOWER, "volume": HIGHER,
            "headcount": UNGRADED, "revenue_per_unit": HIGHER}
GROUPINGS = ("none", "entity", "region", "account", "month", "quarter")

Figure = Union[Decimal, str]          # a Decimal, or NOT_MEANINGFUL


# == The frames ===============================================================================

def _amount(value) -> Decimal:
    """A cell's figure as an exact Decimal through its text; a whole number has no places."""
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    return amount.quantize(Decimal(1)) if amount == amount.to_integral_value() else amount


def _frame(rows: list) -> pandas.DataFrame:
    frame = pandas.DataFrame(rows, columns=list(COLUMNS))
    frame["amount"] = pandas.Series([r[-1] for r in rows], dtype=object)
    return frame


@functools.cache
def _clean_rows() -> tuple:
    rows = []
    for version in VERSIONS:
        for entity in model.ENTITIES:
            block = model.block(entity.code, version)
            for line in model.LINES:
                for period in MONTHS:
                    amount = block[line.name][period]
                    if amount is not None:
                        rows.append((version, entity.code, entity.region, line.code, line.name,
                                     line.parent, line.section, line.kind, line.unit, period,
                                     _amount(amount)))
    return tuple(rows)


def clean_frame() -> pandas.DataFrame:
    """The clean pair's figures, from src/sample_model.py (no planted fault, no tie-out break)."""
    return _frame(list(_clean_rows()))


# The sample workbook's fixed addresses (this file only).
LISTS_ACCOUNTS = range(4, 17)          # A4:H16: code, name, parent, section, polarity, unit,
LISTS_ENTITIES = range(19, 22)         # ledger sign, kind; A19:C21: code, name, region
FIRST_LINE = {"Actual": 7, "Budget": 23, "Forecast": 39}
FIRST_MONTH_COLUMN = 3                 # C is 2026-01, N is 2026-12


def _text(value) -> str:
    return "" if value is None else str(value)


@functools.cache
def _sample_rows(path: Path = SAMPLE) -> tuple:
    wb = load_workbook(path, data_only=True, read_only=False)
    lists = wb["Lists"]
    lines = [tuple(_text(lists.cell(r, c).value) for c in range(1, 9)) for r in LISTS_ACCOUNTS]
    entities = [tuple(_text(lists.cell(r, c).value) for c in range(1, 4))
                for r in LISTS_ENTITIES]
    rows = []
    for version in VERSIONS:
        for code, _, region in entities:
            ws = wb[f"Plan_{code}"]
            for i, (account, name, parent, section, _, unit, _, kind) in enumerate(lines):
                row = FIRST_LINE[version] + i
                for j, period in enumerate(MONTHS):
                    value = ws.cell(row, FIRST_MONTH_COLUMN + j).value
                    if value is not None:
                        rows.append((version, code, region, account, name, parent, section,
                                     kind, unit, period, _amount(value)))
    return tuple(rows)


def sample_frame() -> pandas.DataFrame:
    """The sample pair's figures: the committed workbook's cached values at fixed addresses."""
    return _frame(list(_sample_rows()))


def frame_from(rows) -> pandas.DataFrame:
    """A small frame built by hand: (version, entity, region, account, parent, period, amount)."""
    return _frame([(v, e, r, a, a, p, "Profit and loss", "Flow", "EUR", period, Decimal(amount))
                   for v, e, r, a, p, period, amount in rows])


# == The data the questions are asked of ========================================================

@dataclass
class Data:
    entities: tuple                   # in list order
    region: dict                      # entity -> region
    accounts: dict                    # parent -> its accounts, in list order
    held: dict                        # version -> the (entity, month) pairs it holds
    figures: dict                     # (version, entity, month, key) -> Decimal
    coverage: dict                    # a cache of each question's coverage


def prepare(frame: pandas.DataFrame) -> Data:
    """The frame's figures by version, entity, month and parent or account, and what is held."""
    entities = tuple(frame["entity"].drop_duplicates())
    region = dict(frame.drop_duplicates("entity")[["entity", "region"]].itertuples(index=False))
    read = frame[frame["section"] != "Subtotal"]          # the leaf accounts, VOL and HC
    accounts: dict = {}
    for account, parent in read.drop_duplicates("account")[["account", "parent"]].itertuples(
            index=False):
        if parent:
            accounts.setdefault(parent, []).append(account)
    held = {v: set() for v in VERSIONS}
    for version, entity, period in read[["version", "entity", "period"]].drop_duplicates(
            ).itertuples(index=False):
        held[version].add((entity, period))
    figures = {}
    by_parent = read[read["parent"] != ""].groupby(["version", "entity", "period", "parent"],
                                                   sort=False)["amount"].sum()
    for (version, entity, period, parent), amount in by_parent.items():
        figures[(version, entity, period, ("parent", parent))] = amount
    by_account = read.groupby(["version", "entity", "period", "account"], sort=False)[
        "amount"].sum()
    for (version, entity, period, account), amount in by_account.items():
        figures[(version, entity, period, ("account", account))] = amount
    return Data(entities, region, {p: tuple(a) for p, a in accounts.items()},
                {v: frozenset(s) for v, s in held.items()}, figures, {})


# == The arithmetic ==============================================================================

def quotient(numerator: Figure, denominator: Figure) -> Figure:
    """The exact quotient quantised to 12 places, ties away from zero; a zero denominator,
    or an operand that is itself not meaningful, gives not meaningful."""
    if NOT_MEANINGFUL in (numerator, denominator) or denominator == 0:
        return NOT_MEANINGFUL
    exact = Fraction(numerator) / Fraction(denominator) * 10 ** PLACES
    whole, rest = divmod(abs(exact.numerator), exact.denominator)
    whole += 2 * rest >= exact.denominator
    return Decimal(whole if exact >= 0 else -whole).scaleb(-PLACES)


def _sum(data: Data, version: str, cells, key) -> Decimal:
    return sum((data.figures.get((version, e, p, key), Decimal(0)) for e, p in cells),
               Decimal(0))


def value(data: Data, metric, version: str, cells, account: Optional[str] = None) -> Figure:
    """A metric (or ("account", code), a ratio's inline operand) over held entity-months."""
    if isinstance(metric, tuple):
        return _sum(data, version, cells, metric)
    if metric in BY_PARENT:
        key = ("account", account) if account else ("parent", BY_PARENT[metric])
        return _sum(data, version, cells, key)
    if metric in STOCKS:
        last = max(p for _, p in cells)
        return _sum(data, version, [(e, p) for e, p in cells if p == last],
                    ("account", BY_ACCOUNT[metric]))
    if metric in BY_ACCOUNT:
        return _sum(data, version, cells, ("account", BY_ACCOUNT[metric]))
    if metric in DIFFERENCES:
        first, *others = DIFFERENCES[metric]
        return value(data, first, version, cells) - sum(
            (value(data, o, version, cells) for o in others), Decimal(0))
    numerator, denominator = RATIOS[metric]
    return quotient(value(data, numerator, version, cells),
                    value(data, denominator, version, cells))


def variance(metric: str, base: Figure, comparator: Figure) -> tuple:
    """(variance, variance percentage, label) of base against comparator."""
    if NOT_MEANINGFUL in (base, comparator):
        return NOT_MEANINGFUL, None if metric in RATIOS else NOT_MEANINGFUL, ""
    difference = base - comparator
    if metric in PERCENTAGES:
        difference, percentage = difference * 100, None
    elif metric in RATIOS:
        percentage = None
    else:
        percentage = quotient(difference, abs(comparator))
    if POLARITY[metric] == UNGRADED:
        label = ""
    elif difference == 0:
        label = "no variance"
    else:
        label = "favourable" if (difference > 0) == (POLARITY[metric] == HIGHER) else "adverse"
    return difference, percentage, label


# == A question and its answer ===================================================================

@dataclass(frozen=True)
class Row:
    group: str
    value: Figure
    comparator: Optional[Figure] = None
    variance: Optional[Figure] = None
    variance_pct: Optional[Figure] = None
    label: str = ""


@dataclass(frozen=True)
class Answer:
    outcome: str                      # ok or no_data
    rows: tuple
    months_asked: tuple
    months_held: tuple                # with a comparison: the months both versions hold
    gaps: tuple                       # (entity, month) inside the held range, not held
    closing: Optional[str]            # a stock's closing month, for the whole window


def months_of(period: tuple) -> tuple:
    kind, which = period
    if kind == "month":
        return (which,)
    if kind == "quarter":
        return QUARTERS[which]
    if kind == "ytd":
        return MONTHS[:MONTHS.index(which) + 1]
    return MONTHS


def _coverage(data: Data, version: str, comparison: str, entities: str, period: tuple):
    key = (version, comparison, entities, period)
    if key not in data.coverage:
        permitted = data.entities if entities == "group" else (entities,)
        window = months_of(period)
        held = {(e, p) for e, p in data.held[version] if e in permitted}
        other = COMPARISONS[comparison]
        if other is not None:
            held &= data.held[other]
        cells = frozenset((e, p) for e, p in held if p in window)
        gaps = ()
        if held:
            first, last = min(p for _, p in held), max(p for _, p in held)
            gaps = tuple((e, p) for p in window if first <= p <= last for e in permitted
                         if (e, p) not in held)
        data.coverage[key] = (permitted, window, cells,
                              tuple(sorted({p for _, p in cells})), gaps)
    return data.coverage[key]


def _groups(data: Data, metric: str, group_by: str, permitted: tuple, window: tuple, cells):
    """(group, its cells, its account) for each row the grouping asks for, in order."""
    if group_by == "none":
        return [("", cells, None)]
    if group_by == "entity":
        return [(e, {c for c in cells if c[0] == e}, None) for e in permitted]
    if group_by == "region":
        regions = dict.fromkeys(data.region[e] for e in permitted)
        return [(r, {c for c in cells if data.region[c[0]] == r}, None) for r in regions]
    if group_by == "account":
        return [(a, cells, a) for a in data.accounts[BY_PARENT[metric]]]
    if group_by == "month":
        return [(m, {c for c in cells if c[1] == m}, None) for m in window]
    return [(q, {c for c in cells if c[1] in months}, None) for q, months in QUARTERS.items()
            if set(months) & set(window)]


def ask(data: Data, metric: str, version: str, entities: str, period: tuple, group_by: str,
        comparison: str) -> Answer:
    """One question of the grid, answered by the rules above."""
    permitted, window, cells, months_held, gaps = _coverage(data, version, comparison, entities,
                                                            period)
    if not cells:
        return Answer("no_data", (), window, (), gaps, None)
    closing = max(months_held) if metric in STOCKS else None
    other = COMPARISONS[comparison]
    rows = []
    for group, group_cells, account in _groups(data, metric, group_by, permitted, window,
                                               cells):
        if metric in STOCKS and group_by in ("entity", "region"):
            group_cells = {c for c in group_cells if c[1] == closing}
        if not group_cells:
            continue                  # a grouped row with no held month: left out
        base = value(data, metric, version, group_cells, account)
        if other is None:
            rows.append(Row(group, base))
            continue
        against = value(data, metric, other, group_cells, account)
        rows.append(Row(group, base, against, *variance(metric, base, against)))
    return Answer("ok", tuple(rows), window, months_held, gaps, closing)


# == The two proofs ================================================================================

@dataclass(frozen=True)
class Proof:
    compared: int                     # subtotal figures compared, held entity-months only
    mismatches: tuple                 # (version, entity, month, line, oracle, workbook)
    not_held: int                     # entity-months a version does not hold
    not_held_nonzero: tuple           # of those, any whose workbook subtotal is not empty or 0


SUBTOTAL_ROWS = {"Gross profit": 5, "EBIT": 10}        # offsets from a block's first line


def subtotal_proof(pair: str) -> Proof:
    """The oracle's Gross profit and EBIT against the workbook's own subtotal cells."""
    data = prepare(clean_frame() if pair == "clean" else sample_frame())
    wb = load_workbook(CLEAN if pair == "clean" else SAMPLE, data_only=True)
    compared, mismatches, not_held, nonzero = 0, [], 0, []
    for version in VERSIONS:
        for entity in data.entities:
            ws = wb[f"Plan_{entity}"]
            for j, period in enumerate(MONTHS):
                cells = {}
                for line, offset in SUBTOTAL_ROWS.items():
                    raw = ws.cell(FIRST_LINE[version] + offset, FIRST_MONTH_COLUMN + j).value
                    cells[line] = None if raw is None else _amount(raw)
                if (entity, period) not in data.held[version]:
                    not_held += 1
                    if any(v not in (None, 0) for v in cells.values()):
                        nonzero.append((version, entity, period, cells))
                    continue
                for line, metric in (("Gross profit", "gross_profit"), ("EBIT", "ebit")):
                    mine = value(data, metric, version, {(entity, period)})
                    compared += 1
                    if mine != cells[line]:
                        mismatches.append((version, entity, period, line, mine, cells[line]))
    return Proof(compared, tuple(mismatches), not_held, tuple(nonzero))


# == Formatting, at the point a figure is shown (rule 8) ===========================================

def percent(figure: Figure) -> str:
    """A ratio held as a fraction, as a percentage to one place, half away from zero."""
    if figure == NOT_MEANINGFUL:
        return NOT_MEANINGFUL
    return f"{(figure * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"


def points(figure: Figure) -> str:
    """A percentage ratio's variance, in percentage points to one place."""
    if figure == NOT_MEANINGFUL:
        return NOT_MEANINGFUL
    return f"{figure.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)} pp"


def euros(figure: Figure) -> str:
    """Money in whole euros, exact (the sample is whole euros)."""
    if figure == NOT_MEANINGFUL:
        return NOT_MEANINGFUL
    return f"{figure.quantize(Decimal(1), rounding=ROUND_HALF_UP):,}"


def per_unit(figure: Figure) -> str:
    """Revenue per unit, to two places, half away from zero."""
    if figure == NOT_MEANINGFUL:
        return NOT_MEANINGFUL
    return f"{figure.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}"


# == The grid and the file ===========================================================================

def _text_of(figure: Optional[Figure]) -> str:
    if figure is None:
        return ""
    return figure if isinstance(figure, str) else format(figure, "f")


def _months_text(months: tuple) -> str:
    """Months as runs: 2026-01..2026-04 2026-06."""
    runs: list = []
    for m in months:
        if runs and MONTHS.index(m) == MONTHS.index(runs[-1][-1]) + 1:
            runs[-1].append(m)
        else:
            runs.append([m])
    return " ".join(r[0] if len(r) == 1 else f"{r[0]}..{r[-1]}" for r in runs)


def _period_text(period: tuple) -> str:
    kind, which = period
    return {"month": which, "quarter": which, "ytd": f"YTD {which}", "fy": which}[kind]


def questions():
    """Every question of the grid, in order: (metric, version, comparison, entities, period,
    grouping). A comparison is never the question's own version."""
    for metric in METRICS:
        for version in VERSIONS:
            for comparison, other in COMPARISONS.items():
                if other == version:
                    continue
                for entities in ENTITY_SETS:
                    for period in PERIODS:
                        for group_by in GROUPINGS:
                            if group_by == "account" and metric not in BY_PARENT:
                                continue
                            yield metric, version, comparison, entities, period, group_by


def grid_rows(pair: str, data: Data):
    for metric, version, comparison, entities, period, group_by in questions():
        a = ask(data, metric, version, entities, period, group_by, comparison)
        question = (pair, metric, version, comparison, entities, _period_text(period), group_by)
        coverage = (_months_text(a.months_asked), _months_text(a.months_held),
                    "; ".join(f"{e} {p}" for e, p in a.gaps))
        if a.outcome == "no_data":
            yield question + ("", "no_data", "", "", "", "", "") + coverage
            continue
        for r in a.rows:
            yield question + (r.group, "ok", _text_of(r.value), _text_of(r.comparator),
                              _text_of(r.variance), _text_of(r.variance_pct), r.label) + coverage


@functools.cache
def render() -> str:
    """The whole grid, on the clean pair and on the sample pair, as the committed CSV's text."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(HEADER)
    for pair, frame in (("clean", clean_frame()), ("sample", sample_frame())):
        writer.writerows(grid_rows(pair, prepare(frame)))
    return out.getvalue()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Write the oracle's expected figures.")
    parser.add_argument("--write", action="store_true", help=f"write {EXPECTED.name}")
    parser.add_argument("--out", default=str(EXPECTED))
    args = parser.parse_args(argv)
    if not args.write:
        parser.print_help()
        return 0
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render().encode("utf-8"))
    print(f"wrote {path} ({path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
