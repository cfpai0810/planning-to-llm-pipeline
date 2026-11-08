# Controls

The controls that check a planning workbook before anything built from it can
be used. This page starts with the member-name allow-list, which the extractor
applies as it reads (`src/names.py`), then states the five spreadsheet
controls that check what it read (`src/controls.py`), reconcile, which ties
what landed to what was read and the workbook to the ledger
(`src/reconcile.py`), and the promotion gate (`src/promotion.py`).

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
- a block against its anchors, a block that cannot be read, or a block whose
  version is not admitted: `anchor` and the block's name, so a name that
  disagrees, a block that cannot be read and a version not admitted are one
  break; a name of a block's form that is not used, where the tab's block is
  not found either: the missing block's anchors, so the two are one break;
  the accounts a block lacks: `accounts` and the block; an extra line of an
  account the block already holds: `cell`, the block, its label and the
  label header, as a line not on Lists is; a block without units: `units`
  and the block;
- part 2a2: blocks that share a cell: `overlap`, the tab and the blocks' names
  sorted, so `["structure","overlap","Plan_ES",["Budget_ES","Forecast_ES"]]`,
  and a name's disagreement with its label in the group takes the same
  anchors; a blank figure: `blank`, the block, the account and the first month
  of the run; a blank month: `blank_month`, the block and the first month of
  the run; a "Line item" cell not read: `header`, the tab and the cell;
- a tab's Entity cell: `entity cell` and the tab; its Currency cell:
  `currency` and the tab; a tab whose Entity or Currency label is not found:
  `heading` and the tab; money accounts with more than one unit: `units` and
  `Lists`;
- a hidden row or column: `row` or `column`, the tab and its number or letter;
  a run of neighbouring hidden rows or columns: the same, with the range
  (`53:55`, `C:E`) in place of the number or letter; a link to another
  workbook: `link` and its number in the workbook.

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
A value read from the Currency cell, a version label, the Entity cell or a
block's version that fails the allow-list is described by the allow-list's
reasons instead, for example "a value that has characters outside the allowed
set (shown encoded in the evidence)", and the evidence shows it as
`json.dumps` with `ensure_ascii` writes it, so a stray space, a tab or a
zero-width character shows as an escape. The evidence is bounded at 60
encoded characters, never cutting an escape in two, and then gives the
value's length: `"EUR\u200b"`, or a long value's first part followed by
"(100 characters in all)".
Figures in a message are as the file holds them, with thousands separators;
the evidence holds them as plain decimals.

**Severity** follows fixed rules, by kind:

- **high**: a figure as read is wrong, or cannot be read, or cannot be placed:
  a missing tab or header, a duplicate or missing period, a formula with no
  cached value, an error value, a total that does not tie, a block that cannot
  be read or whose defined name disagrees with the sheet, blocks that share a
  cell, a tab whose Entity or Currency cell cannot be found, a currency that
  is not the accounts' unit;
- **medium**: a figure may be wrong, or a member is left out, or something is
  out of sight: a typed or embedded constant, a member not on Lists or failing
  the allow-list, a line with no unit or a block without units, merged cells,
  accounts not in the block, an account twice in a block, a block whose
  version is not admitted, a blank figure or a blank month in a Budget or
  Forecast block, hidden content, a link to another workbook;
- **low**: a person should look, but nothing is shown to be wrong: a pattern
  difference alone, a visible tab not on the expected list, and a "Line item"
  cell that heads no block read.

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
| `heading_not_found` | high | A plan tab with no cell labelled "Entity" or "Currency": one break for the tab, naming the labels not found |
| `blocks_overlap` | high | Blocks that are read and share a cell (two names on one range, or one block found by its labels over another): one break per group, naming every block and its range; none of them is built, and nothing else in them is examined |
| `anchor_disagreement` | high | A block's defined name that disagrees with the sheet: its version against the label above the block's header row, or the tab's Entity cell against the entity the tab and its names give; or a name of a block's form that is not used |
| `block_unreadable` | high | A block that cannot be read: the first row of its range has no "Line item" header or no month header |
| `version_not_admitted` | medium | A block whose version is not one of Actual, Budget, Forecast, after trimming and ignoring case: it is landed raw and not built |
| `header_not_found` | high | A column of a block with no header, or a block (Actual, Budget, Forecast) whose header is not found on the plan tab of an entity on Lists |
| `header_not_read` | low | A "Line item" cell on a plan tab that is not in the header row of a block read, and whose skip no other break explains |
| `duplicate_period` | high | One period heading two or more columns of a block; the months this leaves with no column are named in its evidence |
| `period_gap` | high | A month of the block's year with no column, when no duplicate or missing header in the block accounts for it |
| `accounts_not_in_block` | medium | Accounts not in the block: a block that lacks accounts on Lists, beyond those its lines not on Lists stand for |
| `account_not_on_lists` | medium | A line item whose label is not an account on Lists; when it pairs with a missing account, the break names that account |
| `account_twice_in_block` | medium | An account on more than one line of a block: one break for each line after the first, listing every row that carries the label and naming the account it takes the place of, when it pairs with one |
| `rejected_name` | medium | A member name that fails the allow-list, with its reason codes; the break says the member is excluded from the star schema and from every prompt |
| `block_without_units` | medium | A block whose "Unit" header is not found or is empty, or in which no line has a unit: one break naming the block |
| `no_unit` | medium | A line item with no unit, in a block where other lines have one |
| `blank_figure` | medium | In a Budget or Forecast block, an account or statistic line with no figure in months where other lines hold one: one break per line and run of months |
| `blank_month` | medium | In a Budget or Forecast block, months in which every account and statistic line is blank: one break per run of months |
| `merged_cells` | medium | Merged cells inside a plan block |
| `currency_disagreement` | high | A plan tab's Currency cell that is not the unit of the money accounts on Lists |
| `money_units_differ` | high | Money accounts on Lists that do not share one unit, so no tab's currency can be checked |

**Each block against its anchors.** For each block read through a defined
name (`<version>_<entity>`), the control compares what the name says with
what the sheet states: the version label above the block's header row, and
the tab's Entity cell. All three are found by their labels, as the extractor
finds everything else, never at fixed cells. They are compared after
trimming and ignoring case (below). The Entity cell is the cell to
the right of the first cell that reads "Entity" (B2 in the sample, D5 in the
`shifted` fixture). The version label is found through the block's header
row: the first row of the name's range that holds a "Line item" cell, and
the cell above that cell, wherever the name happens to start (A21 for the ES
Budget block, C40 for the shifted AT Forecast block). Any disagreement is one
high break naming the name, what it says and what the sheet states
(`name_wrong_version`, `version_label_changed`). The Entity cell is one
cell of its tab, so an Entity cell that disagrees is one break for the tab,
listing every defined name on it (`entity_changed`: Plan_ES's B2 reads AT).
A name of a block's form that the extractor does not read a block through (it
refers to another tab, to more than one range or to none, or belongs to
another tab) is one high break naming the name and where it points
(`name_other_tab`: Budget_AT on Plan_ES's Budget rows); where the tab's own
block is not found either, that is one fault, and one break with both
reasons.

The extractor never falls back to reading a block by its labels to repair a
bad name. A block read through a name that disagrees with the sheet lands as
the name reads it, and its high break holds promotion until a named person
acknowledges it (`name_wrong_version`: the Forecast name on the Budget rows
lands the Budget figures as Forecast, and gives one break). A name that cannot
be read through at all is not repaired either: it is reported, and the tab's
block is read by its own labels where they are found, as it always was.

**No cell is read twice.** A block found by its labels whose range overlaps a
block read through a defined name is not read (part 2a). The extractor
records it beside the blocks it read (`blocks_not_read`). So when a block's
version label is changed (`version_label_changed`: Plan B above the ES Budget
header), the rows are read once, through the name Budget_ES; until 2a they
were also read by their labels as a block called Plan B. The one high break,
the name against the label, holds promotion, and once it is acknowledged and
the pair promoted the star schema holds three versions and the ES Budget rows
once. That rule stays: it is the normal case.

**No cell may belong to two blocks (part 2a2).** Blocks the extractor reads
that still land a cell in common, two defined names on one range or one block
found by its labels running over another, are one high break per group of
overlapping blocks, `blocks_overlap`. The overlap is judged on the cells each
block lands, so a block that lands no cell overlaps nothing (a name whose range
has no header row lands none, and is the block that cannot be read). A name
the extractor does not read a block through takes no part. The message says
why: no cell may belong to two blocks, so none of them is built. The blocks of
the group land raw, every cell flagged (`overlap` in `raw_plan_cell`), and
none of them is built: `fact_plan` leaves those cells out. The break names every block
of the group and its range, the shared cells, any defined name in the group
whose version disagrees with its label (folded in, so a name and a label that
disagree on one range are still one break), and any header the misdirected
name leaves unread. Nothing else in those blocks is examined by the
structure, formula-integrity or totals controls: the break stands for them.
`names_same_range` (the ES Forecast name on the ES Budget rows of the clean
workbook) gives that one break, and the promoted star holds no ES Budget and
no ES Forecast row, and every other block's rows as the clean pair's;
`name_wrong_version` is the same fault on the small base and changed from an
`anchor_disagreement` to this break. Until 2a2 both names were read, and
after the break was acknowledged the ES Forecast rows held the Budget
figures. `names_partly_overlap` (the Forecast name from A30 to A51) lands no
cell, so it is one `block_unreadable`, whose message adds that its range
overlaps the ES Budget block and that nothing is built for ES Forecast. Two
blocks found by their labels, one over the other (the clean workbook with the
names Budget_ES and Forecast_ES deleted and a note typed in A36, so the found
Budget block runs down over the found Forecast block), are one break, where
until 2a2 they gave 19.

**A block that cannot be read.** A block whose first row has no "Line item"
header or no month header cannot be read: one high break, with the reason.
The control examines nothing else in that block (no header problem, no line
with no unit, no accounts not in the block, no label or name in it), so no
other break stands in for it and there is no flood of one break per line.
When the same block also disagrees with its anchor, that is one fault: one
break with both reasons, as D4 carries two. `name_shifted` and
`name_shifted_up`, the Budget name for ES one row down and one row up, each
give exactly one break, where before 1e they gave 12 and 28. Since part 2a2
the break also names in its evidence the header its name leaves unread (A22
in `name_shifted`), adds a sentence when its range overlaps blocks that are
read, and, for an Actual block, says that reconcile leaves the entity out of
the ledger tie-out (Reconcile, below).

**Every block holds every account on Lists.** A line whose label is not on
Lists is already one break, so lines not on Lists pair with missing accounts
one to one within a block: the k-th line not on Lists, in row order, pairs
with the k-th missing account, in Lists order. So n such lines excuse at most
n missing accounts, and each such line's break names the account it
displaced, in its message and its evidence (its id, kind and severity are as
before). An account on more than one line pairs the same way (part 2a): of
the lines that carry its label, the first, in row order, is the account's
line, and each later one is an extra, one `account_twice_in_block` break
each; the lines that cannot be placed, those not on Lists and the extras
together, in row order, take the missing accounts in Lists order. An extra
with nothing to pair is still the break, and says it repeats the first line
(`duplicate_account`: the ES Budget Materials label overwritten by Salaries
gives one break naming rows 25 and 29 and Materials, where at 1e it gave 25). Every missing account left unpaired is reported in one medium
break, accounts not in the block, naming the block and listing those
accounts alone (by name in the message, by code and name in the evidence).
So `unknown_account` (Volume relabelled) keeps its one break, which now names
Volume; `block_short` (the Budget name two rows short) gives the accounts
break; and a block with one line relabelled and two rows cut gives both, the
relabelled line naming one account and the accounts break the other two.

**The currency.** Each plan tab's Currency cell, found by its label like the
Entity cell (B3 in the sample), must equal the unit of the money accounts on
Lists: every account outside the Statistics section, EUR in the sample. If it
does not, after trimming and ignoring case, that is one high break naming the
tab, the cell, the tab's value and the accounts' unit (`currency_changed`:
Plan_AT's B3 reads USD). If the money
accounts do not share one unit, that is one high break on Lists and no tab's
currency is checked. A plan tab with no cell labelled "Entity" or "Currency"
is one high break saying which label is missing.

**One fault, one break (part 2a).** A block with a line-level structure break
(a line not on Lists, an account missing from the block, an account twice)
gives that break alone: the totals control does not examine the block's
subtotals that the fault touches, neither their months nor their FY cells,
because a subtotal recomputed from Lists would differ only because a line
cannot be placed. The structure break says so in a second sentence naming the
subtotals not checked, in Lists order ("The subtotals of block "Forecast_ES"
are not checked: "Gross profit", "EBIT"."). The leaf lines' FY totals in that
block, and every other subtotal, are checked as before.
`renamed_feeds_subtotal` (Salaries relabelled Staff costs in the ES Forecast
block) gives one break, where at 1e it gave 13.

**The narrower subtotal rule (part 2a2).** A missing or doubled account stops
the totals control only on the subtotals that the account feeds, worked out
from Lists as the totals control works out each Subtotal (the accounts with a
ledger sign above it), never from a fixed table: in the sample, a revenue or
cost-of-sales account feeds Gross profit and EBIT, an operating expense feeds
EBIT alone, and a statistic feeds none, so the break about a missing
statistic carries no second sentence. A doubled account stops the subtotals
of the extra line's account and of the account it takes the place of,
together. A label that is not on Lists still stops every subtotal of its
block, because its account is not known. So `block_short` (Volume and
Headcount missing) keeps its one break and loses the sentence, and its
subtotals are checked; `block_short_wrong_ebit` (the same with the March EBIT
formula leaving out IT and software) gives three breaks, as the reviewer's
ruling of 8 November expects: the accounts break, the subtotal break on the
March EBIT, and the low pattern break on the same formula; none on Gross
profit.

**A block without units.** A block whose "Unit" header is not found, or in
which no line has a unit, is one medium break, `block_without_units`, one
break naming the block and the column, with no `no_unit` break for its lines
(`unit_column_empty`: the ES Budget Unit column emptied gives one break, where
at 1e it gave 13). A block with some units missing keeps one `no_unit` break
per line (`no_units`). Since part 2a2 a "Unit" header cell left empty is the
same one break and no `header_not_found`: the empty header cell immediately
right of the label column, every cell below which, in the block, holds text
and no figure or formula, is read as the Unit column's (`unit_header_empty`:
the DE Budget block's B22 emptied gives one break, where it gave two). An
empty header cell anywhere else keeps its own `header_not_found` break
(`missing_header`).

**Three versions.** `dim_version` holds Actual, Budget and Forecast and
nothing else, fixed in its SQL file as `dim_time` is, whatever the promoted
batch holds. A block whose version, from its defined name or its label, is
not one of them after trimming and ignoring case is landed raw and not built:
`fact_plan` joins on `dim_version`, so its rows never reach the star schema,
and the structure control reports it as one medium break,
`version_not_admitted` (`extra_version_block`: a block labelled Plan B below
the Forecast block, with no defined name). A version that is one of the three
in another case, or with spaces around it, takes the version's own name, so
it is built. Since part 2a2 that break is the block's only structure break:
nothing else in a block landed and not built is examined (`landed_block_short`:
a Scenario block with one month and five lines gives one break, where it gave
three, with a period gap and accounts not in the block). A version that also
fails the allow-list (`version_label_padded`: " Plan B ", a space each side)
is that one break too, its message describing the value by the allow-list's
reason and its evidence showing it encoded with the reason codes, and no
`rejected_name` besides.

**Blank figures (part 2a2).** In a Budget or Forecast block that is built, a
figure cell is blank when it holds no value and no formula: absent from the
sheet, or empty text. A formula with no cached value, an error value, text
and an explicit 0 are not blank: an explicit 0 is not blank, it is a figure,
and the first three are the calculation-state control's. Only the lines whose
label is an account on Lists that is not a Subtotal are looked at (the leaf
accounts and the statistics), only the month columns, in order, and never the
FY column or a subtotal. A month in which every such line is blank is one
medium `blank_month` break per block and run of such months; any other blank
cell is one medium `blank_figure` break per line and run of months, a run
ending at a month that holds something or at a blank month. Each names the
block, the account, the months as a run ("2026-04 to 2026-06", or one month)
and the first cell, and says that nothing is built for them. A line of an
account the block holds twice is that break's, and is not looked at. Actual
blocks are left to the ledger tie-out, which finds an empty month, so the
sample's AT May stays one reconcile break. The fixtures `blank_cell_forecast`,
`blank_run_budget` and `blank_month_budget` give one break each; until 2a2 they
gave none, and the star simply lacked the rows.

**A "Line item" cell not read (part 2a2).** Every "Line item" cell on a plan
tab is the header row of a block the extractor reads, or one low
`header_not_read` break naming the cell and why it heads no block: the cell
above it is empty, it is in the first row, its label names a version the tab
already reads from another block, or the block it would head overlaps a block
read through a defined name. Two cases are silent. A cell in the header row
of a block that is read, named or found, built or not, is that block's header
(a named block's header row is the first row of its range holding "Line
item", wherever the name starts). And a cell skipped because its label names
a version the tab reads through a defined name whose block has a high break
of its own (it cannot be read, its label disagrees, or it shares a cell) is
named by that break, in its message or its evidence, so one fault stays one
break. `stray_line_item` (Line item typed in A4 of Plan_ES) and
`header_only_block` (a header row in row 60 with nothing above or under it)
give one low break each, and their stars are the clean pair's.

**Case and spaces.** The Currency cell against the unit of the money
accounts, a version label against its defined name's version, and the Entity
cell against the tab's entity are compared with Python's `str.strip()` and
`casefold()` on both sides, so "eur", "EUR " and "budget" are no break
(`currency_lowercase`, `currency_trailing_space`, `version_label_lowercase`).
Python's strip removes more than the plain space: tabs, line breaks and the
no-break space too. It keeps a zero-width character, so a value holding one
still differs. Any other difference stays one high break. The message shows
both values when the sheet's value passes the allow-list; otherwise it
describes the value by the allow-list's reasons and the evidence shows it
encoded (Breaks, above).

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
Control tab's summary and check cells are not read. In a block with a
line-level structure break the subtotals the fault touches are not checked,
and the structure break says so (One fault, one break, and the narrower
subtotal rule, above). A block that shares a cell with another is not
examined.

| Kind | Severity | What it detects |
|---|---|---|
| `fy_total` | high | An FY cell that does not equal its months, or a stock's last month with a figure |
| `subtotal` | high | A Subtotal that does not equal its accounts, recomputed from Lists |

### Hidden content and links

The row hidden in D3 lies outside every block, so the extractor never reads
it; this control is the one that sees it. A link's target is recorded in
the break's evidence, as text, with the cells whose formulas use it. It
appears nowhere else: not in the message, the location or the id.

A run of neighbouring hidden rows (or columns) on one tab is one break, with
the range in its address and its evidence (`53:55`, `C:E`), so a file with
thousands of hidden rows in a run gives one break, not thousands. It is high
when any row or column of the run crosses a plan block. A single hidden row or
column is the break it always was: D3 keeps its id, message and severity.
Rows and columns hidden on a hidden tab are not reported apart: the tab's own
break is enough.

| Kind | Severity | What it detects |
|---|---|---|
| `hidden_sheet` | medium | A hidden tab |
| `hidden_row` | medium | A hidden row, or a run of neighbouring hidden rows; high when it crosses a plan block |
| `hidden_column` | medium | A hidden column, or a run of neighbouring hidden columns; high when it crosses a plan block |
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

### What the controls do not see

The controls find a fault by a difference: a cell unlike its row, a total
that does not add up, an anchor that disagrees, a figure that is not the
ledger's. A fault that leaves no difference passes them. These are known:

- **A whole row wrong in the same way in all twelve months.** A row that
  fetches the wrong driver in every month (Rent and facilities reading the IT
  services rate, say) holds one pattern across the row, and every total is
  computed from it, so nothing differs. The `wrong_driver_row` fixture is
  exactly this, and `test_known_blind_spot_consistent_wrong_driver` asserts
  that the whole start-up build against the committed ledger gives no break,
  so a control that learns to see it must change that test on purpose.
- **A changed input on Assumptions.** A driver someone changed is a decision,
  not an error. The controls do not judge whether a driver is right.
- **A figure wrong in the workbook and in the ledger by the same amount.**
  Reconcile compares the two sources, and two equal wrong figures agree.
- **Anything outside the blocks.** Only the plan blocks are read as figures:
  a note, a scratch calculation or the Control tab's own totals are not
  checked against them. Hidden rows and tabs are reported, but not what they
  hold.
- **Budget and Forecast against a second source.** Reconcile reads the
  Actual block only, so a Budget or Forecast figure is never tied to anything
  outside the workbook.
- **A unit that is present but wrong.** A line with no unit is a break; a
  line whose unit differs from its account's unit on Lists is not compared.
- **Versions swapped whole.** If two blocks are swapped together with their
  version labels and their defined names, every anchor agrees.
- **A blank Volume or Headcount figure in an Actual block.** The blank checks
  look at Budget and Forecast only, and leave Actual to the ledger tie-out,
  but the ledger holds the nine leaf accounts and never Volume or Headcount.
  So a statistic left blank in an Actual block passes the gate unseen, though
  the per-metric held rule of the query layer (part 2d) will still show it as
  a gap in an answer about volume, headcount or revenue per unit.

## Reconcile

`src/reconcile.py` makes two comparisons. Each gives breaks of the same type
as the controls', with the control `reconcile`.

**Source against raw.** For each batch, by version and period, the row count
and the amount sum that the reader computed in Python before landing are set
against the rows that landed, read back from the warehouse, and against the
control totals landed with them. They must be equal; a difference is a break.

**The workbook's Actual block against the ledger.** For a pair (a plan batch
and the ledger batch it is reconciled against), by entity, account and
period:

- in whole euros: each figure is rounded to the euro, half away from zero,
  before it is compared;
- in presentation sign: the ledger's amount is turned into presentation sign
  through its account's ledger sign on Lists (a Credit account's amount times
  -1, a Debit account's as it is), so revenue is positive;
- only the accounts with a ledger sign are compared: the statistical lines
  and the subtotals are not in a ledger;
- members are judged against the Lists of the pair's plan batch, which land
  beside it (`raw_list_account`, `raw_list_entity`), not against the flags
  the ledger was read with.

The rules:

- A late journal, or any other difference in one entity, account and month,
  is an amount difference: one break.
- Two differences in one entity and month, in two accounts, that are equal
  and opposite in ledger sign, are one "possible reclassification" break. A
  reclassification moves one amount from one account to another, a debit and
  an equal credit, so it is judged in ledger sign; for two accounts with the
  same ledger sign that is also equal and opposite as presented. Pairs are
  found in account order, each account in at most one pair.
- An entity-month that one side holds figures for and the other holds none
  is one break, not one per account.
- A key that the ledger holds more than once is one break, and is not
  compared.
- A ledger entity or account code that is not on Lists is one break per
  code, listing its lines; the lines are not compared and stay out of the
  star schema.
- A ledger line with a row error (a thousands separator, brackets, a missing
  field) is one break per line, because its figure cannot be read. Neither
  the line nor its key is compared, and the line stays out of the star
  schema.
- A workbook cell that holds something but no figure (no cached value, an
  error value, text), or a key the workbook holds twice, is not compared: the
  calculation-state and structure controls report those.
- An entity whose Actual block is not built or not read (its plan tab or its
  Actual block is not found, the block cannot be read, or it shares a cell
  with another block) is left out of the comparison whole (part 2a2, the
  reviewer's ruling of 8 November): the structure break that explains the
  block says that reconcile leaves the Actual figures of that entity out of
  the ledger tie-out, so one fault is one break and not a missing entity-month
  for every month. In a built Actual block, an account the block lacks, or
  holds on two lines, is left out for that entity, and the structure break
  about it names the accounts left out (`actual_account_displaced`: one
  break, where it gave seven). A built block with an empty month is compared
  as before: the sample's AT May is still one missing entity-month.

The clean workbook's Actual block was made equal to the ledger when the
sample was built, so the clean workbook against the same ledger gives no
break. The sample against the ledger gives exactly T1 (an amount
difference), T2 (one possible reclassification) and T3 (one missing
entity-month), and nothing else.

**The id** is `RC`, a hyphen, and the first 12 hex digits of the SHA-256 of
a JSON list of `reconcile` and the anchors, as for the controls:
`source`, the source kind, the version and the period; `amount`, the entity,
the account and the period; `reclassification`, the entity, the period and
the two accounts; `entity_month`, the entity and the period; `ledger_key`,
the entity, the period and the account; `unknown`, the member and the code;
`ledger_line` and its number. A break of the comparison carries the plan
batch's id; a break of the ledger's own lines carries the ledger batch's.

**Messages** are fixed templates filled with values, with both figures and
the difference; names are shown as data, as in the controls' messages.

**Severity** follows the controls' rules: high when a figure as read is
wrong, or cannot be read or placed; medium when a figure may be wrong or a
member is left out.

| Kind | Severity | The rule behind the grade |
|---|---|---|
| `source_against_raw` | high | What landed is not what was read: every figure built on it is in doubt |
| `missing_entity_month` | high | A whole entity-month of figures is absent on one side |
| `possible_reclassification` | medium | The entity-month ties in total; two figures may sit in the wrong accounts |
| `amount_difference` | high | A figure as read disagrees with the other source |
| `duplicate_key` | high | A ledger figure cannot be placed: two lines claim one key |
| `unknown_entity` | medium | A member is left out |
| `unknown_account` | medium | A member is left out |
| `row_error` | high | A ledger figure cannot be read |

**Facts about the file are never findings.** The batch record's times, the
application that saved the file, its creator and its external link targets
are never compared with anything: reconcile never compares them, and they
never reach the star schema.

## Promotion

`src/promotion.py` is the promotion gate. What is promoted is a pair: a plan
batch with the ledger batch it was reconciled against. The pair's breaks are
the plan batch's control breaks and the pair's reconcile breaks, the
ledger's own row breaks included; the start-up build stores them in the
warehouse.

- **Acknowledge.** A named person marks each break accepted, dismissed or
  investigated, with no default: the action must be chosen. The person is
  required, trimmed, and at most 60 characters; the note is optional and at
  most 500 characters. The time comes from an injected clock. A repeat
  acknowledgement appends a row; nothing is ever overwritten.
- **Acknowledgements belong to the pair.** A break's id is the same in every
  file by design, so an acknowledgement given for one pair never counts for
  the same break of another.
- **Promote.** A named person promotes, whatever the break count: the name
  follows an acknowledger's rules (required, trimmed, at most 60 characters,
  the same refusals) and is checked before the breaks. The pair is promoted
  only once every one of its breaks has an acknowledgement; until then
  promotion is refused, with the count of breaks still waiting. The promotion
  records `promoted_by` and `promoted_at`, and the person and time of the
  pair's last acknowledgement (`last_acknowledged_by`,
  `last_acknowledged_at`), which are empty for a pair with no break.
- **Promote rebuilds the facts.** `promote` records the promotion and
  rebuilds the star schema from the promoted pair in one transaction. If the
  rebuild fails, the transaction is rolled back: there is no new promotion
  row, the previous promotion stays current, and the previous facts stay as
  they were. DuckDB rolls back a `CREATE OR REPLACE TABLE` with the rest of
  its transaction; a test makes one statement of the rebuild fail and
  compares every star table row for row.
- **The current pair.** The latest promotion is the current one: the
  previous promotion stays current until the next. The star schema's facts
  are built from the current pair only, `fact_plan` from its plan batch and
  `fact_actual_erp` from its ledger batch. At a fresh start nothing is
  promoted, so the facts are empty until someone acknowledges every break and
  a named person promotes.

The breaks, the acknowledgements and the promotions are append-only tables
in the warehouse (`gate_review`, `gate_break`, `gate_acknowledgement`,
`gate_promotion`); a test scans the code for any statement that would change
them. Acknowledgements are lost when the app restarts, because the warehouse
is rebuilt at start. The audit trail of Phase 3 is the lasting record.

## The Control tab

The workbook's own check cells are not read: the controls compute what they
compare from the extracted cells, and the sample has no check cells.
