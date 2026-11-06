# Glossary

One language for the pipeline, wherever a reader meets it: the README and
these docs, the command line, the code's comments, docstrings and messages,
the SQL files, and the comments and docstrings of the tests.

`TestGlossary` (`tests/test_glossary.py`) reads all of that text and fails if
a retired variant comes back. Each term below lists the variants it retires.

| Term | Meaning | Retired |
|---|---|---|
| **batch** | One file as the pipeline read and landed it. Its id is the first 16 hex digits of the file's SHA-256, so the same bytes are always the same batch. | dataset, data set, data load |
| **batch record** | What a batch says about its file: its name, SHA-256 and size, its sheets and their visibility, its creator, its times, the application that saved it, how it was calculated, and the targets of its links to other workbooks. Facts about the file, never findings. | |
| **break** | What a control or reconcile reports: typed, with a stable id, a severity, a location, a message made from a fixed template, and its evidence. | discrepancy, mismatch, reconciling item, exception item |
| **control** | One of the five examinations of a workbook: structure, calculation state, formula integrity, totals, and hidden content and links. | |
| **check** | What `main.py check` does: lands a workbook and a ledger in a temporary warehouse, runs the five controls and reconcile, prints each batch record and every break, and promotes nothing. | |
| **reconcile** | To set what was read against what landed (source against raw), and the workbook's Actual block against the ledger. | |
| **acknowledge** | For a named person to mark one break of a pair accepted, dismissed or investigated. | sign off, sign-off, approve a break |
| **acknowledgement** | The row an acknowledgement appends: the pair, the break, the person, the action, an optional note and the time. A repeat appends; nothing is overwritten. | |
| **pair** | A plan batch with the ledger batch it was reconciled against: what is acknowledged and promoted. | |
| **promote** | To record, under a named person, that a pair is the current one; the facts are rebuilt from it. Refused while any of its breaks has no acknowledgement. | go live, make live, publish a batch, release a batch |
| **promotion** | The row a promotion appends: the pair, the person who promoted it and the time, and the person and time of its last acknowledgement if it had any. The latest promotion is current until the next. | |
| **plan** | The planning workbook's figures: its Actual, Budget and Forecast blocks, which `fact_plan` holds. The file itself is the planning workbook. | plan file, planning file |
| **Actual** | The version of what happened: the workbook's Actual block, January to June as typed in, and the ledger's figures, which `fact_actual_erp` holds apart from the plan. | |
| **Budget** | The version set before the year: twelve months, calculated by formulas from its drivers. | AOP, annual operating plan |
| **Forecast** | The version updated during the year: twelve months, calculated by formulas from its own drivers. | outlook, latest estimate |
| **version** | Actual, Budget or Forecast: the block a figure comes from. A generic export calls it a scenario (`docs/export_contract.md`). | |
| **entity** | A company of the group, by its code on Lists: ES, AT or DE. | business unit, subsidiary, legal entity, company code |
| **account** | A code on Lists, with its name, parent, section, polarity, unit, ledger sign and kind. | GL account, GL code, nominal code, nominal account |
| **line item** | A row of a plan block. Its label is matched to an account's name on Lists. | |
| **period** | A month, as text of the form 2026-06. FY2026 heads the total column; it is not a period of the facts. | time period, fiscal period, posting period |
| **ledger** | The ledger extract: one CSV line per entity, period and account code, in ledger sign. | ERP actuals, ERP extract, ERP file, general ledger, trial balance |
| **ledger sign** | The sign as the ledger holds it: debit positive, so revenue is negative. Lists gives each account's. | |
| **presentation sign** | The sign a reader sees: revenue and costs both positive. `dim_account` holds the factor, 1 or -1, that turns ledger sign into it. | reporting sign, display sign, natural sign |
| **star schema** | The warehouse's dimensions (`dim_version`, `dim_time`, `dim_entity`, `dim_account`) and facts (`fact_plan`, `fact_actual_erp`), built from the promoted pair only. | data mart |

## Notes

- "Finding" stays in use for what a reviewer reports on the project itself
  (a verdict's findings), and "facts about the file are never findings"
  keeps its plain meaning. What the pipeline reports is a break.
- The ledger file in `data/` keeps its name, `erp_trial_balance.csv`, and the
  fact table keeps the brief's name, `fact_actual_erp`; in prose they are the
  ledger extract and the ledger's figures.
