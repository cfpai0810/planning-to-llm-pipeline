# Controls

The controls that check a planning workbook before anything built from it can
be used. This page starts with the member-name allow-list, which the extractor
applies as it reads (`src/names.py`), and then states the five spreadsheet
controls that check what it read (`src/controls.py`).

## The member-name allow-list

A member name (an account, a parent, an entity or a version) may enter a
prompt only when it passes this rule:

- it is made of Unicode letters, digits, spaces and the characters
  `& . , - / ( ) %` and nothing else;
- it is at most 50 characters long;
- it has at most six words;
- it has no leading or trailing space.

A letter is a character of a Unicode letter category, so "Logística y almacén"
passes; an accent typed as a separate combining mark does not. A digit is a
decimal digit. A space is the plain space: a tab, a line break, a no-break
space or a zero-width character fails. A character is one Unicode code point,
and a word is a run of characters between spaces.

The extractor checks every member name it finds: the codes and names of the
accounts and entities on Lists, the account parents, the versions of the plan
blocks, and any line label that is not on Lists. A name that fails is recorded
with its reasons, as fixed codes (`characters`, `length`, `words`, `space`,
`empty`), and the member is landed raw in the warehouse. It is excluded from
the star schema and from every prompt, and the break that reports it says so.

### What the allow-list cannot do

The allow-list narrows the route into a prompt; it does not close it. A short
sentence in plain English, such as "Ignore the rules please", is four ordinary
words and passes any character list. What stops such a name from steering the
model sits after it, in code: names reach the model only as JSON-encoded data,
never as instructions; the model can only return a specification that matches
a fixed schema; Python checks that specification against the finance-owned
metric registry before anything runs; and a refusal uses fixed wording. The
evaluation's golden set carries that exact label, so the claim is measured,
not assumed.

## The five spreadsheet controls

`src/controls.py` runs five controls over what the extractor read: Structure,
Calculation state, Formula integrity, Totals, and Hidden content and links.
No control opens the file again, recalculates it or changes it: each reads the
extracted rows and facts and returns typed breaks. A control that cannot apply
to a source says that it did not run, with a fixed reason, and a control that
did not run never counts as a pass.

### Breaks

Every break has the same fields.

| Field | Type | Meaning |
|---|---|---|
| `break_id` | text | The stable id (below) |
| `batch_id` | text | The batch the break was found in |
| `control` | text | `structure`, `calculation_state`, `formula_integrity`, `totals` or `hidden_content` |
| `kind` | text | The first of its reasons, in the control's fixed order |
| `reasons` | list of text | Every rule of the control that caught it, in that order |
| `severity` | text | `high`, `medium` or `low` |
| `sheet`, `block`, `entity`, `account`, `period` | text or none | The location; `account` is the code on Lists |
| `address` | text or none | The cell, the range, or the row or column (`53:53`, `E:E`), where there is one |
| `message` | text | One fixed template per reason, filled with values |
| `evidence` | pairs of text | What a reviewer needs to check it: both patterns, both totals, the formula, the cells |

**The id** is the control's prefix (`ST`, `CS`, `FI`, `TO` or `HC`), a
hyphen, and the first 12 hex digits of the SHA-256 of a JSON list of the
control and the anchors. The anchors are what the extractor reads by, and a
fixed address only where nothing else names the thing:

- a cell of a plan block: `cell`, the block, the line's label and the column's
  header, so D1 is `["formula_integrity","cell","Budget_AT","Marketing","2026-03"]`;
- a cell outside every block: `cell`, the tab and the address;
- a tab: `tab` and its name; a block whose header is not found: `block`, the
  tab and the version; a block's header cell: `header`, the block and the
  column's place in the block; a block's periods: `periods`, the block, and
  the duplicated header if there is one; merged cells: `merged`, the block and
  the range;
- a name on Lists or in a long-format source: `name`, the member and the name;
  a code in a long-format source: `code`, the member and the code;
- a hidden row or column: `row` or `column`, the tab and its number or letter;
  a link to another workbook: `link` and its number in the workbook.

The same break in two files has the same id; the batch id says which file it
came from. Should two breaks of one control ever share their anchors, the
second takes a count after them, so the ids within a batch stay unique.

**One break per cell per control.** A cell caught by two rules of the same
control is one break that lists both reasons. D4, the embedded `*1.02`, is one
break with the reasons `embedded_constant` and `pattern`. Different controls
report the same cell separately.

**Messages** are fixed templates filled with values, one sentence per reason,
never free text. Names are shown as data, JSON-encoded in double quotes. A tab
name is always shown that way. A line item or other member name is shown when
it passes the allow-list, and a block name when its version and entity do; a
name that fails it never enters a message, which says "(a name that fails the
allow-list)" or names its cell instead, and the name is kept in the evidence.
Figures in a message are as the file holds them, with thousands separators;
the evidence holds them as plain decimals.

**Severity** follows fixed rules, by kind:

- **high**: a figure as read is wrong, or cannot be read, or cannot be placed:
  a missing tab or header, a duplicate or missing period, a formula with no
  cached value, an error value, a total that does not tie;
- **medium**: a figure may be wrong, or a member is left out, or something is
  out of sight: a typed or embedded constant, a member not on Lists or failing
  the allow-list, a line with no unit, merged cells, hidden content, a link to
  another workbook;
- **low**: a person should look, but nothing is shown to be wrong: a pattern
  difference alone, and a visible tab not on the expected list.

A break with several reasons takes the highest of their severities. The one
rule that is not by kind alone: a hidden row or column that crosses a plan
block is high, because it hides figures the plan is read from. The tables
below give each kind's severity; `SEVERITY` in `src/controls.py` holds the
same table.

### Structure

The expected tabs are Control, Assumptions, Lists, and a `Plan_` tab for each
entity on Lists. A hidden tab is never a structure break: it belongs to the
hidden-content control alone, so the sample's hidden Scratch tab (D5) is one
break, not two.

| Kind | Severity | What it detects |
|---|---|---|
| `missing_tab` | high | A tab on the expected list is missing |
| `entity_not_on_lists` | medium | A visible `Plan_` tab for an entity that is not on Lists |
| `tab_not_on_list` | low | Any other visible tab outside the expected list: a tab not on the expected list |
| `header_not_found` | high | A column of a block with no header, or a block (Actual, Budget, Forecast) whose header is not found on the plan tab of an entity on Lists |
| `duplicate_period` | high | One period heading two or more columns of a block; the months this leaves with no column are named in its evidence |
| `period_gap` | high | A month of the block's year with no column, when no duplicate or missing header in the block accounts for it |
| `account_not_on_lists` | medium | A line item whose label is not an account on Lists |
| `rejected_name` | medium | A member name that fails the allow-list, with its reason codes; the break says the member is excluded from the star schema and from every prompt |
| `no_unit` | medium | A line item with no unit |
| `merged_cells` | medium | Merged cells inside a plan block |

### Calculation state

The pipeline never recalculates a file, so a formula with no cached value has
no figure to read. Every formula cell in the workbook is checked, the Control
tab and hidden tabs included. The `no_cache` fixture gives one break for each
of its 372 formula cells.

| Kind | Severity | What it detects |
|---|---|---|
| `no_cached_value` | high | A formula cell with no cached value |
| `error_value` | high | A cell holding an error value, such as #DIV/0! or #REF! |

### Formula integrity

Three rules over the cells of the plan blocks; the Control tab and the other
tabs are not read.

1. **A typed constant in a formula row.** The reviewer's rule, as written: "A
   row is a formula row when, among its month cells that hold something (a
   formula or a typed value), at least half are formulas. Empty months are not
   counted, and a tie counts as a formula row. A typed constant in a formula
   row is a break, in Actual, Budget and Forecast. The FY column follows the
   same rule by column. In addition, a row whose account is a Subtotal on
   Lists is a formula row in every block whatever the counts, so a subtotal
   typed across a whole row is still found." An array formula counts as a
   formula. Actual input rows, where every month is typed, are not formula
   rows. A Subtotal's FY cell typed in an FY column that is not a formula
   column is still found, as part of its formula row.
2. **An embedded constant.** A number typed inside a formula is a break,
   except ROUND's digits argument (the whole second argument of ROUND, a sign
   allowed). Numbers are found with openpyxl's formula tokenizer, so digits
   inside a text string or a cell reference are not numbers.
3. **The pattern of a row.** Budget and Forecast month columns only. Each
   formula is translated with openpyxl's translator from its own cell to a
   fixed far-right origin on its row, column XFD (the last column a worksheet
   can have), so every relative reference moves by the same amount and none
   can fall off the left edge. Identical results form one group, and the
   largest group is the row's pattern; when two groups are equally large, the
   one whose first cell is further left. A formula outside it "differs from
   the pattern of its row": the break shows both patterns, is labelled "for
   review", and is never worded as an error or as a rate. A formula the
   translator cannot move is a group of its own. A typed constant holds no
   formula, so it is never a pattern finding: the first rule finds it (D1).

An array formula is recorded in the control's result, never read as text, and
skipped by the pattern comparison and by the search for numbers inside
formulas.

| Kind | Severity | What it detects |
|---|---|---|
| `typed_constant` | medium | A typed constant in a formula row, or in a formula column (FY) |
| `embedded_constant` | medium | A number typed inside a formula, ROUND's digits argument apart |
| `pattern` | low | A formula that differs from the pattern of its row, for review |

### Totals

Every figure is the one the file holds (the cached value), and the message
shows both figures and the difference.

- **Each FY cell against its own row's months**, added up. A stock (kind Stock
  on Lists, such as Headcount) is checked against its last month with a
  figure instead.
- **The subtotals month by month** (Gross profit and EBIT), recomputed from
  the extracted cells and Lists. A Subtotal is the running total of the
  accounts above it on Lists that carry a ledger sign: a Credit account adds
  and a Debit account subtracts. So Gross profit is revenue less cost of
  sales, and EBIT is Gross profit less operating expenses.

An empty cell counts as zero. A comparison is made only when the total and
every cell it is compared with hold a figure or are empty: a formula with no
cached value, an error value or text is the calculation-state control's. The
Control tab's summary and check cells are not read.

| Kind | Severity | What it detects |
|---|---|---|
| `fy_total` | high | An FY cell that does not equal its months, or a stock's last month with a figure |
| `subtotal` | high | A Subtotal that does not equal its accounts, recomputed from Lists |

### Hidden content and links

The row hidden in D3 lies outside every block, so the extractor never reads
it; this control is the one that sees it. A link's target is recorded in
the break's evidence, as text, with the cells whose formulas use it. It
appears nowhere else: not in the message, the location or the id.

| Kind | Severity | What it detects |
|---|---|---|
| `hidden_sheet` | medium | A hidden tab |
| `hidden_row` | medium | A hidden row; high when it crosses a plan block |
| `hidden_column` | medium | A hidden column; high when it crosses a plan block |
| `external_link` | medium | A link to another workbook |

### A long-format source

A source in the export contract has no formulas, so the calculation-state and
formula-integrity controls report "not run: the source has no formulas",
never a pass. It has no total cells and no sheets, so the totals control
reports "not run: the source has no total cells" and the hidden-content
control "not run: the source has no sheets". The structure control runs: it
reports member names that fail the allow-list, by line, and entity and
account codes that are not on the lists the reader was given.

### Facts about the file

The file's created and modified times, the application that last saved it and
its creator are facts in the batch record. They are never findings, and no
control reads them. The committed workbooks were saved on 5 November 2026,
after the plan's as-of date of 30 June 2026, and that gives no break.

### The FAST rules each control is related to

The FAST Standard is a published standard for building financial models. Each
control is related to it as below. The relations are described in words, and
no rule number is given here: rule numbers are added once the current
published version of the standard has been consulted.

- Structure: related to the FAST guidance on a consistent layout (the same
  column holds the same period on every calculation sheet), on labelling every
  line with its unit, and on avoiding merged cells.
- Calculation state: no FAST rule is closely related; it checks the state of
  the file, not the design of the model.
- Formula integrity: related to the FAST rules that keep inputs apart from
  calculations, keep typed numbers out of formulas, and keep one consistent
  formula along each row of a calculation.
- Totals: related to the FAST aim that calculations can be followed and
  checked; no single rule is relied on.
- Hidden content and links: related to the FAST guidance against hiding rows,
  columns or sheets, and against links to other workbooks.

### Why a person reviews every break

Pattern checks find real faults, but they also flag sound formulas and miss
some faults. ExceLint, a research tool that flags formulas which break the
pattern of their neighbours, reported a mean precision of 64.1% and a mean
recall of 62.1% over 70 workbooks (ExceLint, OOPSLA 2018). Those are the
paper's figures, not this project's. They are the reason a pattern finding is
worded "for review" and every break is seen by a named person.

### What the controls claim, and what they do not

On the committed sample the controls find exactly the five planted defects,
D1 to D5, and nothing else, and the clean workbook gives no break; the
mutation tests show both sides of "exactly". That is the only detection claim
made here, and it is about the planted set alone: it says nothing about how
often the controls would find a fault in any other workbook.

By design they do not find a typed number in a row that is mostly typed (a
Budget line typed across all twelve months, unless it is a Subtotal), a
pattern difference in the Actual block or in the FY column, or a Control-tab
total that disagrees with the plan tabs.

These controls detect; they do not prevent. Nothing here replaces an
independent review of the model by a second person: an acknowledgement records
that a named person saw each break, not that anyone re-performed the work.

## The Control tab

The workbook's own check cells are not read: the controls compute what they
compare from the extracted cells, and the sample has no check cells.
