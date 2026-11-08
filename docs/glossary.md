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
| **version** | Actual, Budget or Forecast: the block a figure comes from. A generic export calls it a scenario (`docs/export_contract.md`). | scenario |
| **entity** | A company of the group, by its code on Lists: ES, AT or DE. | business unit, subsidiary, legal entity, company code |
| **account** | A code on Lists, with its name, parent, section, polarity, unit, ledger sign and kind. | GL account, GL code, nominal code, nominal account |
| **line item** | A row of a plan block. Its label is matched to an account's name on Lists. | |
| **period** | A month, as text of the form 2026-06. FY2026 heads the total column; it is not a period of the facts. | time period, fiscal period, posting period |
| **ledger** | The ledger extract: one CSV line per entity, period and account code, in ledger sign. | ERP actuals, ERP extract, ERP file, general ledger, trial balance |
| **ledger sign** | The sign as the ledger holds it: debit positive, so revenue is negative. Lists gives each account's. | |
| **presentation sign** | The sign a reader sees: revenue and costs both positive. `dim_account` holds the factor, 1 or -1, that turns ledger sign into it. | reporting sign, display sign, natural sign |
| **star schema** | The warehouse's dimensions (`dim_version`, `dim_time`, `dim_entity`, `dim_account`) and facts (`fact_plan`, `fact_actual_erp`), built from the promoted pair only. | data mart |
| **metric** | A figure the registry defines, by its id: a formula over accounts on Lists, a kind (flow, stock or ratio), a unit, a polarity, and the groupings, filters and comparisons it allows. Every question asks for one metric. | KPI, key performance indicator |
| **registry** | `registry/metrics.toml`: the finance-owned definitions of the metrics, with a version, a changelog naming who approved each version, and a lock. Its SHA-256 is in every answer's provenance. | |
| **specification** | A question as eight fields: metric, version, dimensions, filters, period, comparison, sort and limit. In Phase 3 a model writes it; it is never SQL. | |
| **validate** | To check a specification against the registry, the promoted pair's members and the role, in a fixed order, and give it back with canonical names, or refuse it with an outcome. | |
| **compile** | To turn a validated specification into one SELECT, from fixed templates and the registry's formulas, with the role's entities injected. | |
| **guard** | The check every compiled query passes before it runs: one SELECT of the allowed tables, with the role's filter in place. A second line behind the read-only connection. | |
| **role** | Who asks: a group controller sees every entity; an entity controller sees one, and every group figure is over it. | |
| **session** | One visitor's copy of the template, opened read-only between write cycles: its acknowledgements, promotion and answers are its own. | |
| **template** | The warehouse every session copies: both pairs landed, their breaks stored, nothing promoted, read-only on disk. | |
| **held** | An entity-month is held for a metric in a version when the entity has a figure for every account the metric reads; a zero is a figure, an empty cell is not. | |
| **coverage** | What an answer says about its months: those asked for, those held (with a comparison, those both versions hold), and its gaps. | |
| **gap** | An entity-month inside the held range and the window that is not held for the metric: listed in the answer, never filled with a zero. | |
| **matched months** | With a comparison, the entity-months both versions hold for the metric; only they are compared, on both sides. | |
| **closing value** | A stock's value in its closing month: the last month of the window, or of a month or quarter, that an entity in scope holds; entities without it are left out and listed as gaps. | |
| **ratio of sums** | A ratio computed on the sums of its numerator and its denominator over the entities and months asked for, never an average of ratios. | |
| **outcome** | What a question gets in place of figures: a code and one fixed sentence, which never echoes a name. | |
| **provenance** | What an answer names about its figures: the promoted pair's files and hashes, the promotion, the breaks and their acknowledgements, the registry's version and hash, the engine, and the role's view. | |

## Notes

- "Finding" stays in use for what a reviewer reports on the project itself
  (a verdict's findings), and "facts about the file are never findings"
  keeps its plain meaning. What the pipeline reports is a break.
- The ledger file in `data/` keeps its name, `erp_trial_balance.csv`, and the
  fact table keeps the brief's name, `fact_actual_erp`; in prose they are the
  ledger extract and the ledger's figures.
- Two retired variants stay in two places on purpose (part 2e). The export
  contract's table of generic terms maps an export's "scenario" to the
  version. The pages' theme kit, carried from P6, names a widget the "KPI
  tile"; the pages stay stubs in Phase 2, and the widget is renamed when they
  are built. The block labelled "Scenario" in the `landed_block_short`
  fixture is data, not a term.
- The governed layer's terms (metric to provenance) were added in part 2e as
  proposals, for the reviewer to approve.
