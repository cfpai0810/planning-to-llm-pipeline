# The metric registry

Every answer is computed from one finance-owned definition: a metric of the
registry, `registry/metrics.toml`. Group Finance owns it. `src/registry.py`
reads it with the standard library's `tomllib`, validates it and gives it as
frozen dataclasses; nothing there opens a database or reads a workbook.

- **A formula is structured data, never a query.** A sum names the accounts
  on Lists with a parent, or one account by its code; a difference is the
  first metric less the others; a ratio is a numerator over a denominator,
  each a metric or an inline sum (marketing % of revenue divides account 6100
  by revenue). The workbook's own Gross profit and EBIT rows are never read:
  they are recomputed from the leaf accounts, and the oracle proves the two
  agree.
- **The version lock.** `registry/registry.lock.json` holds the registry
  version and a SHA-256 of each metric's definition. A definition never
  changes without a new version and a changelog entry naming the approver and
  the date; `scripts/lock_registry.py` rewrites the lock and refuses without a
  version bump. The SHA-256 of the file's bytes is the registry hash, which
  every answer's provenance names.
- **The approver.** The approver in the changelog, E. Marsh, Group Financial
  Controller, Calder Foods Group, is a fictional holder of a role in the
  sample company.
- **What the loader refuses.** Besides any field outside its schema: the
  reserved time aggregation `average` (not implemented); a difference of
  metrics with different units; a cycle of formulas; a ratio with a stock
  operand, such as revenue over headcount, because a sum over the months
  against a closing value has no agreed meaning in v1; and a ratio of a
  ratio. An inline sum takes its kind from the kind on Lists of the accounts
  it sums: given the accounts (a session gives its promoted pair's), the
  loader refuses one that names no account, sums accounts of two kinds, or is
  a stock.

## The rules

1. **Version.** Every question names one version: Actual, Budget or Forecast.
   Actual is the workbook's Actual block; the ledger is not queried in this
   phase. It serves the tie-out, and an answer reports the reconciliation
   status through its provenance (the pair's breaks and acknowledgements).
2. **Periods.** A period is a month (2026-06), a quarter (Q1 to Q4), the year
   to date through a month, or the fiscal year, FY2026. The fiscal year is the
   calendar year, and only FY2026 exists: any other period is refused with
   `period_invalid`.
3. **Flows and stocks.** Flows sum over the months of the window that are
   held. A stock (headcount) takes its closing value and is never summed
   across months: across entities it adds up, so group headcount is the sum
   of the entities' closing headcounts.
4. **Ratios.** A ratio is the ratio of the sums, never an average of ratios:
   entity A has revenue 100 and gross profit 50 (50%), entity B revenue 900
   and gross profit 270 (30%), and the group is 320 over 1,000, which is
   32.0%, not 40%. A zero denominator is "not meaningful" (a typed marker,
   NULL in the engine), never zero and never an error.
5. **Held, coverage and gaps.** See below. A window with no held month at all
   is the outcome no_data, never a zero.
6. **Comparisons.** Only matched months are compared (below). A stock
   compares closing values at the last matched month; with a month or quarter
   grouping, only matched months appear. A prior-year comparison is always
   the outcome `prior_year_not_available` in v1: the plan holds one fiscal
   year.
7. **Variance.** Variance is the base less the comparator. Favourable or
   adverse follows the metric's polarity: a cost increase is adverse, a
   revenue increase favourable; zero is "no variance"; headcount is ungraded
   and carries no label. The variance percentage divides the variance by the
   absolute comparator, and is "not meaningful" when the comparator is zero.
   A percentage's variance is in percentage points, with no variance
   percentage.
8. **Rounding.** Every figure in an answer is exact: money is held as
   DECIMAL(38, 12), and the sample is whole euros. A ratio is computed in the
   engine as DECIMAL(38, 12): the exact quotient, rounded once to twelve
   places, half away from zero (the integer route below). Where a figure is
   shown it is rounded half away from zero, in Python, once, where it is
   shown: money in whole euros, a percentage to one decimal place, percentage
   points to one place. Revenue per unit is shown to two decimal places. The
   unrounded value is the one variance uses.

### Held, by metric

An entity-month is held for a metric in a version when the entity has a
figure for every account the metric reads: a zero is a figure, an empty cell
is not. Revenue reads 4000 and 4100; cost of sales 5000, 5100 and 5200;
operating expenses 6000 to 6300; volume VOL; headcount HC. A difference or a
ratio reads the accounts of all its parts: gross profit reads five accounts,
EBIT all nine, marketing % of revenue 6100, 4000 and 4100, revenue per unit
4000, 4100 and VOL. An account filter narrows the accounts a question reads
to those it names. Subtotal rows are never read, so they never make an
entity-month held.

The version's held range runs from the first to the last month in which any
entity in scope has any figure for the version, on a line that is not a
subtotal. A gap for a metric is an entity-month inside that range and inside
the window that is not held for the metric: it is listed in the answer, and a
missing row is never a zero. A month after the last held month is not held,
and not a gap: Actual holds January to June, so the Actual figure for the
fiscal year is the first six months, with no gap. The sample's AT May Actual
is a gap: a group Actual figure for May says so, and AT's May Actual asked
alone is no_data with the gap AT 2026-05. A blank Materials cell makes its
entity-month a gap for cost of sales, gross profit, EBIT and both margins,
and for nothing else.

### Matched months

With a comparison, months are matched by entity-month and per metric: both
sides sum only the entity-months that both versions hold for the metric, so
a gap in either version is left out of both, and the coverage lists it.
Actual against Budget for the fiscal year therefore compares January to June,
and says so.

### The closing value

A stock's closing month, for a question with no grouping or grouped by
entity or region, is one month for every row: the last month of the window
in which any entity in scope holds the stock in that version (with a
comparison, the last matched month). An entity that does not hold that month
has no closing value: it is left out of the sum and listed as a gap; it never
falls back to an earlier month. Grouped by month or quarter, each month or
quarter has its own closing month, by the same rule.

### The integer route, and rounding twice

DuckDB divides two DECIMAL(38, 12) values as a DOUBLE, so the compiler never
writes a plain division. One helper, `integer_route` in `src/compiler.py`,
gives the only fragment that divides: it scales the numerator and the
denominator by 10 to the 12 into HUGEINT, divides with rounding half away
from zero, and scales back, so a ratio and a variance percentage are the
exact quotient rounded once to twelve places. A numerator of about 8.5 times
10 to the 13 or more cannot be scaled: the engine raises an error, which an
answer gives as the outcome `engine_error`, never a wrong figure. The
sample's largest sums are about 10 to the 8.

A displayed ratio is the twelve-place value rounded once more, to one place;
it can differ from rounding the exact quotient only when that quotient lies
within 5 times 10 to the minus 13 of a display tie.

### Revenue per unit

Revenue per unit is revenue over volume, in euros per unit. Its variance is
the plain difference of the two quotients, in euros per unit, with no
variance percentage. A variance percentage for revenue per unit would be a
change of definition, made only with a new registry version.

## The metrics

The table is written from `registry/metrics.toml` by
`scripts/registry_table.py`; a test fails if it differs from what the script
writes.

<!-- metric-table: written by scripts/registry_table.py from registry/metrics.toml; never typed -->
| Metric | Label | Formula | Kind | Over time | Unit | Polarity | Grouped by | Filtered by | Compared with |
|---|---|---|---|---|---|---|---|---|---|
| `revenue` | Revenue | the accounts whose parent is Revenue | flow | sum | EUR | higher is favourable | entity, region, account, month, quarter | entity, region, account | budget, forecast |
| `cost_of_sales` | Cost of sales | the accounts whose parent is Cost of sales | flow | sum | EUR | lower is favourable | entity, region, account, month, quarter | entity, region, account | budget, forecast |
| `gross_profit` | Gross profit | `revenue` less `cost_of_sales` | flow | sum | EUR | higher is favourable | entity, region, month, quarter | entity, region | budget, forecast |
| `operating_expenses` | Operating expenses | the accounts whose parent is Operating expenses | flow | sum | EUR | lower is favourable | entity, region, account, month, quarter | entity, region, account | budget, forecast |
| `ebit` | EBIT | `gross_profit` less `operating_expenses` | flow | sum | EUR | higher is favourable | entity, region, month, quarter | entity, region | budget, forecast |
| `gross_margin_pct` | Gross margin % | `gross_profit` over `revenue` | ratio | its parts' own | % | higher is favourable | entity, region, month, quarter | entity, region | budget, forecast |
| `ebit_margin_pct` | EBIT margin % | `ebit` over `revenue` | ratio | its parts' own | % | higher is favourable | entity, region, month, quarter | entity, region | budget, forecast |
| `marketing_pct_of_revenue` | Marketing % of revenue | account 6100 over `revenue` | ratio | its parts' own | % | lower is favourable | entity, region, month, quarter | entity, region | budget, forecast |
| `volume` | Volume | account VOL | flow | sum | units | higher is favourable | entity, region, month, quarter | entity, region | budget, forecast |
| `headcount` | Headcount | account HC | stock | closing | FTE | ungraded | entity, region, month, quarter | entity, region | budget, forecast |
| `revenue_per_unit` | Revenue per unit | `revenue` over `volume` | ratio | its parts' own | EUR per unit | higher is favourable | entity, region, month, quarter | entity, region | budget, forecast |

Registry version 1.0.0, owned by Group Finance.
<!-- /metric-table -->
