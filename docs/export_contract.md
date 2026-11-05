# The export contract

A plan from any planning tool can enter the pipeline as a long table: one CSV
line per figure. Any tool that can write this table can feed the pipeline,
so no page here maps one tool's export; the generic terms below say what each
column means in the words most planning tools use.

## The long table

The first line is exactly this header:

```
version,entity,account,period,amount,unit
```

| Column | What it holds | Rule |
|---|---|---|
| `version` | The version of the figure: Actual, Budget or Forecast | Required. A member name, checked by the allow-list |
| `entity` | The entity's code on Lists, such as ES | Required. A code not on the lists the reader is given is flagged |
| `account` | The account's code on Lists, such as 4000 | Required. A code not on the lists the reader is given is flagged |
| `period` | The month, as text of the form 2026-06 | Required. Anything else is a row error |
| `amount` | The figure, a plain decimal: an optional minus sign, digits, and an optional point followed by digits | Anything else is a row error that keeps the text: never coerced |
| `unit` | The unit, such as EUR, units or FTE | Optional |

The file:

- is UTF-8 text; a leading byte order mark is accepted;
- holds the header exactly as above, and then one line per figure; blank
  lines are skipped;
- holds amounts as plain decimals: a thousands separator, brackets, a
  currency sign, an exponent, spaces or words make a row error, with the text
  kept and no amount;
- may hold the same version, entity, account and period on several lines:
  every one is kept and flagged as a duplicate.

When a line has several faults, its row error is the first of: a wrong
number of fields (`fields`), an empty version, entity or account
(`version_missing`, `entity_missing`, `account_missing`), a period not of the
form 2026-06 (`period_not_yyyy_mm`), and an amount that is not a plain
decimal (`amount_missing`, `amount_thousands_separator`, `amount_brackets`,
`amount_not_plain`).

## What happens to it

`src/long_format.py` reads the file. Each line becomes a plan row, as a
workbook cell does, but with no sheet, address, block or formula; the rows
land in the same raw table as a workbook's cells. The source has no formulas,
so its batch record says "not applicable: the source has no formulas", and:

- the calculation-state and formula-integrity controls report "not run: the
  source has no formulas", never a pass;
- the totals control reports "not run: the source has no total cells", and
  the hidden-content control "not run: the source has no sheets";
- the structure control runs: it reports member names that fail the
  allow-list, by line, and entity and account codes that are not on the
  lists the reader was given.

In this phase the command line's check takes a planning workbook; the long
table's reader exists and is tested, and its rows land as a workbook's do.

## The generic terms

| Generic term | Column | What it means here |
|---|---|---|
| scenario | `version` | Actual, Budget or Forecast |
| dimension member | `entity` | An entity on Lists, by its code; an account is a member of the other dimension |
| line item | `account` | An account on Lists, by its code |
| period | `period` | A month of the fiscal year, which equals the calendar year |
