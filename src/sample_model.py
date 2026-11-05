# =============================================================================
# src/sample_model.py: the independent calculation of the sample workbook
# =============================================================================
"""
Calder Foods Group, FY2026: every figure of the sample planning workbook,
computed in pure Python with Decimal from the drivers. The build
(scripts/build_sample.py) writes the workbook with formulas, has LibreOffice
recalculate it, and compares every cached value with the figures here, so this
module never reads or writes a workbook and imports no spreadsheet library.
The web layer never imports it (tests/test_sample_model.py checks both).

The company. Three entities (ES, AT, DE), EUR only, fiscal year equal to the
calendar year, as of 30 June 2026. Each plan sheet has three blocks:

- Budget and Forecast: twelve months calculated from the drivers;
- Actual: the six closed months, typed as the preparer loaded them from the
  ledger. The June forecast is six months of actuals and six of reforecast,
  so the Actual figures are the Forecast's first six months.

A line's FY2026 total is the sum of its months, except Headcount: a stock,
whose total is its closing value (December for Budget and Forecast, June for
Actual), never a sum. Money is whole euros: every amount a formula rounds is
rounded half away from zero, as the workbook's ROUND does, and an exact half
is refused, so the two can never round a tie differently.

The planted faults D1 to D5 and the tie-out breaks T1 to T3 (brief 4.3) are
defined here with their anchors and amounts, and block() computes what the
workbook's formulas give with any set of them in place.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

COMPANY = "Calder Foods Group"
CURRENCY = "EUR"
FISCAL_YEAR = "FY2026"
AS_OF = "2026-06-30"
PERIODS = tuple(f"2026-{month:02d}" for month in range(1, 13))
CLOSED_PERIODS = PERIODS[:6]
FY = FISCAL_YEAR                         # the header of every block's total column
VERSIONS = ("Actual", "Budget", "Forecast")
HIGHER, LOWER = "Favourable when higher", "Favourable when lower"
ZERO = Decimal(0)


@dataclass(frozen=True)
class Entity:
    code: str
    name: str
    region: str


ENTITIES = (
    Entity("ES", "Calder Foods Spain", "Southern Europe"),
    Entity("AT", "Calder Foods Austria", "Central Europe"),
    Entity("DE", "Calder Foods Germany", "Central Europe"),
)


@dataclass(frozen=True)
class Line:
    """A line item of the plan blocks, as the Lists tab records it."""
    code: str
    name: str
    parent: str
    section: str
    polarity: str
    unit: str
    ledger_sign: str          # Credit or Debit for a ledger account, empty otherwise
    kind: str                 # Flow or Stock


def _account(code, name, parent, polarity, ledger_sign):
    return Line(code, name, parent, "Profit and loss", polarity, "EUR", ledger_sign, "Flow")


# In the order of the rows of every block.
LINES = (
    _account("4000", "Product sales", "Revenue", HIGHER, "Credit"),
    _account("4100", "Co-packing income", "Revenue", HIGHER, "Credit"),
    _account("5000", "Materials", "Cost of sales", LOWER, "Debit"),
    _account("5100", "Packaging", "Cost of sales", LOWER, "Debit"),
    _account("5200", "Freight out", "Cost of sales", LOWER, "Debit"),
    Line("GP", "Gross profit", "", "Subtotal", HIGHER, "EUR", "", "Flow"),
    _account("6000", "Salaries", "Operating expenses", LOWER, "Debit"),
    _account("6100", "Marketing", "Operating expenses", LOWER, "Debit"),
    _account("6200", "Rent and facilities", "Operating expenses", LOWER, "Debit"),
    _account("6300", "IT and software", "Operating expenses", LOWER, "Debit"),
    Line("EBIT", "EBIT", "", "Subtotal", HIGHER, "EUR", "", "Flow"),
    Line("VOL", "Volume", "", "Statistics", HIGHER, "units", "", "Flow"),
    Line("HC", "Headcount", "", "Statistics", LOWER, "FTE", "", "Stock"),
)
LEDGER_LINES = tuple(line for line in LINES if line.ledger_sign)
SUBTOTALS = ("Gross profit", "EBIT")
INPUT_LINES = tuple(line.name for line in LINES if line.name not in SUBTOTALS)
REVENUE = tuple(line.name for line in LINES if line.parent == "Revenue")
COST_OF_SALES = tuple(line.name for line in LINES if line.parent == "Cost of sales")
OPERATING_EXPENSES = tuple(line.name for line in LINES if line.parent == "Operating expenses")


@dataclass(frozen=True)
class Driver:
    name: str
    unit: str


DRIVERS = (
    Driver("Volume", "units"),
    Driver("Price", "EUR per unit"),
    Driver("Unit cost", "EUR per unit"),
    Driver("Packaging per unit", "EUR per unit"),
    Driver("Freight per unit", "EUR per unit"),
    Driver("Co-packing fees", "EUR"),
    Driver("Headcount", "FTE"),
    Driver("Cost per head", "EUR per FTE"),
    Driver("Marketing share", "share of revenue"),
    Driver("Rent", "EUR"),
    Driver("IT services", "EUR"),
)

# -- The drivers ----------------------------------------------------------------
# Volume is the entity's base volume times the month's seasonal factor (and,
# in the Forecast, a factor for how the year is going), to the nearest 100
# units. Every other driver holds its value until the month it changes.

SEASON = tuple(Decimal(f) for f in ("0.90", "0.86", "0.97", "1.00", "1.05", "1.09",
                                     "1.14", "1.08", "1.02", "0.98", "1.03", "1.16"))
BASE_VOLUME = {"ES": Decimal(480000), "AT": Decimal(190000), "DE": Decimal(710000)}
FORECAST_VOLUME = {
    "ES": ("0.97", "1.01", "0.95", "0.98", "1.02", "0.96") + ("0.97",) * 6,
    "AT": ("1.03", "1.05", "1.01", "1.04", "1.06", "1.02") + ("1.04",) * 6,
    "DE": ("0.94", "0.92", "0.96", "0.95", "0.93", "0.95") + ("0.95",) * 6,
}

STEPS = {
    ("Budget", "ES"): {
        "Price": {"2026-01": "3.45", "2026-04": "3.52"}, "Unit cost": "1.62",
        "Packaging per unit": "0.31", "Freight per unit": "0.22", "Co-packing fees": "42000",
        "Headcount": {"2026-01": "64", "2026-07": "67"}, "Cost per head": "3850",
        "Marketing share": "0.045", "Rent": "58000", "IT services": "21400"},
    ("Budget", "AT"): {
        "Price": {"2026-01": "4.10", "2026-04": "4.18"}, "Unit cost": "1.88",
        "Packaging per unit": "0.36", "Freight per unit": "0.27", "Co-packing fees": "15500",
        "Headcount": {"2026-01": "31.5", "2026-04": "33.5"}, "Cost per head": "5120",
        "Marketing share": "0.052", "Rent": "27500", "IT services": "11900"},
    ("Budget", "DE"): {
        "Price": {"2026-01": "3.85", "2026-04": "3.93"}, "Unit cost": "1.79",
        "Packaging per unit": "0.34", "Freight per unit": "0.19", "Co-packing fees": "61000",
        "Headcount": {"2026-01": "104", "2026-09": "108"}, "Cost per head": "5480",
        "Marketing share": "0.038", "Rent": "86000", "IT services": "33800"},
    # The June forecast: the first six months are what happened, the last six
    # the reforecast. Input costs rose, price increases landed late, hiring
    # slowed, and DE lost part of a co-packing contract from July.
    ("Forecast", "ES"): {
        "Price": {"2026-01": "3.45", "2026-04": "3.50", "2026-07": "3.55"},
        "Unit cost": {"2026-01": "1.62", "2026-03": "1.69"}, "Packaging per unit": "0.31",
        "Freight per unit": {"2026-01": "0.22", "2026-07": "0.24"}, "Co-packing fees": "42000",
        "Headcount": {"2026-01": "63.5", "2026-03": "64", "2026-09": "66"},
        "Cost per head": "3850", "Marketing share": {"2026-01": "0.045", "2026-07": "0.052"},
        "Rent": "58000", "IT services": "21400"},
    ("Forecast", "AT"): {
        "Price": {"2026-01": "4.10", "2026-04": "4.20"},
        "Unit cost": {"2026-01": "1.88", "2026-04": "1.95"}, "Packaging per unit": "0.36",
        "Freight per unit": "0.27", "Co-packing fees": "15500",
        "Headcount": {"2026-01": "31.5", "2026-05": "32.5"}, "Cost per head": "5120",
        "Marketing share": "0.052", "Rent": "27500",
        "IT services": {"2026-01": "11900", "2026-03": "12600"}},
    ("Forecast", "DE"): {
        "Price": {"2026-01": "3.85", "2026-04": "3.89"},
        "Unit cost": {"2026-01": "1.79", "2026-02": "1.87"},
        "Packaging per unit": {"2026-01": "0.34", "2026-05": "0.36"}, "Freight per unit": "0.19",
        "Co-packing fees": {"2026-01": "61000", "2026-07": "48000"},
        "Headcount": {"2026-01": "104", "2026-10": "106"}, "Cost per head": "5480",
        "Marketing share": {"2026-01": "0.038", "2026-07": "0.034"}, "Rent": "86000",
        "IT services": "33800"},
}


def _series(changes) -> tuple[Decimal, ...]:
    """Twelve monthly values from a flat value or from the months a value changes."""
    if isinstance(changes, str):
        changes = {PERIODS[0]: changes}
    values, current = [], None
    for period in PERIODS:
        if period in changes:
            current = Decimal(changes[period])
        values.append(current)
    return tuple(values)


def _volumes(version: str, entity: str) -> tuple[Decimal, ...]:
    factors = FORECAST_VOLUME[entity] if version == "Forecast" else ("1",) * 12
    hundred = Decimal(100)
    return tuple((BASE_VOLUME[entity] * season * Decimal(factor) / hundred)
                 .quantize(Decimal(1), rounding=ROUND_HALF_UP) * hundred
                 for season, factor in zip(SEASON, factors))


def driver_values(version: str, entity: str) -> dict[str, tuple[Decimal, ...]]:
    """The twelve monthly values of every driver of one version and entity, as on Assumptions."""
    if version not in ("Budget", "Forecast"):
        raise ValueError(f"only Budget and Forecast have drivers, not {version}")
    steps = STEPS[(version, entity)]
    found = {name: _series(changes) for name, changes in steps.items()}
    found["Volume"] = _volumes(version, entity)
    return {driver.name: found[driver.name] for driver in DRIVERS}


def round_whole(amount: Decimal) -> Decimal:
    """ROUND(amount, 0) as the workbook computes it: half away from zero; an exact half is refused."""
    if abs(amount) % 1 == Decimal("0.5"):
        raise ValueError(f"a rounding tie at {amount}: change a driver, so that the workbook "
                         "and this model cannot round it differently")
    return amount.quantize(Decimal(1), rounding=ROUND_HALF_UP)


# -- The planted faults and the tie-out breaks (brief 4.3) ----------------------

@dataclass(frozen=True)
class Plant:
    id: str
    fault: str
    sheet: str
    block: str = ""
    line: str = ""
    period: str = ""

    @property
    def entity(self) -> str:
        return self.sheet.removeprefix("Plan_") if self.sheet.startswith("Plan_") else ""


D1 = Plant("D1", "A typed number inside a formula row", "Plan_AT", "Budget", "Marketing",
           "2026-03")
D1_TYPED = Decimal(45000)
D2 = Plant("D2", "A total whose range stops one month short", "Plan_DE", "Budget", "Salaries", FY)
D2_LAST = "2026-11"
D3 = Plant("D3", "A hidden row holding a manual override", "Plan_DE", period="2026-10")
D3_LABEL = "Manual override: Forecast marketing"
D3_VALUE = Decimal(95000)
D4 = Plant("D4", "An embedded constant inside a formula", "Plan_ES", "Forecast", "Product sales",
           "2026-09")
D4_FACTOR = Decimal("1.02")
D5 = Plant("D5", "A hidden sheet holding numbers nothing refers to", "Scratch")
PLANTS = {plant.id: plant for plant in (D1, D2, D3, D4, D5)}


@dataclass(frozen=True)
class TieOut:
    id: str
    fault: str
    entity: str
    period: str
    workbook_account: str = ""    # the account whose workbook figure differs (T1, T2)
    ledger_account: str = ""      # for T2, the account the ledger holds the amount in
    amount: Decimal = ZERO


T1 = TieOut("T1", "A late journal: the ledger has an amount the workbook's Actual block lacks",
            "DE", "2026-06", "6100", amount=Decimal(7350))
T2 = TieOut("T2", "A reclassification: one amount in one account in the workbook and another "
            "in the ledger", "ES", "2026-04", "6300", "6200", Decimal(9800))
T3 = TieOut("T3", "A missing entity-month: the Actual block has no figures, the ledger does",
            "AT", "2026-05")
TIEOUTS = {tieout.id: tieout for tieout in (T1, T2, T3)}
_NAME = {line.code: line.name for line in LINES}


def _here(plant: Plant, plants, entity: str, version: str, line: str, key: str) -> bool:
    return (plant.id in plants and (plant.entity, plant.block, plant.line, plant.period)
            == (entity, version, line, key))


# -- The figures ------------------------------------------------------------------

def _totals(cells: dict, closing: str) -> None:
    for name, months in cells.items():
        if name == "Headcount":
            months[FY] = months[closing] or ZERO
        else:
            months[FY] = sum((v for p, v in months.items() if p in PERIODS and v is not None),
                             ZERO)


def _plan_block(entity: str, version: str, plants) -> dict:
    d = driver_values(version, entity)
    cells = {line.name: {} for line in LINES}
    for i, period in enumerate(PERIODS):
        volume, headcount = d["Volume"][i], d["Headcount"][i]
        sales = volume * d["Price"][i]
        if _here(D4, plants, entity, version, "Product sales", period):
            sales = sales * D4_FACTOR
        sales = round_whole(sales)
        copacking = round_whole(d["Co-packing fees"][i])
        materials = round_whole(volume * d["Unit cost"][i])
        packaging = round_whole(volume * d["Packaging per unit"][i])
        freight = round_whole(volume * d["Freight per unit"][i])
        gross = (sales + copacking) - (materials + packaging + freight)
        salaries = round_whole(headcount * d["Cost per head"][i])
        marketing = round_whole((sales + copacking) * d["Marketing share"][i])
        if _here(D1, plants, entity, version, "Marketing", period):
            marketing = D1_TYPED
        rent = round_whole(d["Rent"][i])
        it = round_whole(d["IT services"][i])
        ebit = gross - (salaries + marketing + rent + it)
        figures = (sales, copacking, materials, packaging, freight, gross, salaries, marketing,
                   rent, it, ebit, volume, headcount)
        for line, value in zip(LINES, figures):
            cells[line.name][period] = value
    _totals(cells, closing=PERIODS[-1])
    if _here(D2, plants, entity, version, "Salaries", FY):
        last = PERIODS.index(D2_LAST)
        cells["Salaries"][FY] = sum((cells["Salaries"][p] for p in PERIODS[:last + 1]), ZERO)
    return cells


def _actual_block(entity: str, tieouts: bool) -> dict:
    forecast = _plan_block(entity, "Forecast", frozenset())
    cells = {line.name: dict.fromkeys(PERIODS) for line in LINES}
    for name in INPUT_LINES:
        for period in CLOSED_PERIODS:
            cells[name][period] = forecast[name][period]
    if tieouts:
        if entity == T1.entity:
            cells[_NAME[T1.workbook_account]][T1.period] -= T1.amount
        if entity == T2.entity:
            cells[_NAME[T2.workbook_account]][T2.period] += T2.amount
            cells[_NAME[T2.ledger_account]][T2.period] -= T2.amount
        if entity == T3.entity:
            for name in INPUT_LINES:
                cells[name][T3.period] = None

    def total(names, period):
        return sum((cells[n][period] or ZERO for n in names), ZERO)

    for period in CLOSED_PERIODS:
        gross = total(REVENUE, period) - total(COST_OF_SALES, period)
        cells["Gross profit"][period] = gross
        cells["EBIT"][period] = gross - total(OPERATING_EXPENSES, period)
    _totals(cells, closing=CLOSED_PERIODS[-1])
    return cells


def block(entity: str, version: str, plants=frozenset(), tieouts: bool = False) -> dict:
    """Every cell of one plan block as the workbook's formulas compute it.

    line name -> period or FY2026 -> Decimal, or None for an empty cell.
    plants: the planted faults in the workbook (any of D1 to D5); tieouts:
    whether T1 to T3 are applied to the Actual block.
    """
    plants = frozenset(plants)
    if unknown := plants - set(PLANTS):
        raise ValueError(f"unknown planted faults: {sorted(unknown)}")
    if version == "Actual":
        return _actual_block(entity, tieouts)
    return _plan_block(entity, version, plants)


def group_summary(entities, plants=frozenset(), tieouts: bool = False) -> dict:
    """The Control tab's group summary: (Revenue, Gross profit or EBIT, version) -> FY total."""
    summary = {}
    for version in VERSIONS:
        blocks = [block(code, version, plants, tieouts) for code in entities]
        summary[("Revenue", version)] = sum((b[n][FY] for b in blocks for n in REVENUE), ZERO)
        for name in SUBTOTALS:
            summary[(name, version)] = sum((b[name][FY] for b in blocks), ZERO)
    return summary


def ledger_lines() -> list[tuple[str, str, str, Decimal]]:
    """The ledger extract: (entity, period, account code, amount in ledger sign, debit positive).

    It is the clean Actual, before T1 to T3: those are applied to the
    workbook, so the ledger is the independent source the workbook must tie to.
    """
    lines = []
    for entity in ENTITIES:
        actual = block(entity.code, "Actual")
        for period in CLOSED_PERIODS:
            for line in LEDGER_LINES:
                amount = actual[line.name][period]
                lines.append((entity.code, period, line.code,
                              -amount if line.ledger_sign == "Credit" else amount))
    return lines
