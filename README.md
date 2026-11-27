# Planning-to-Warehouse-to-LLM Pipeline

**Phase 3: a question in words, on the command line, with your own key.**
What exists: the sample workbook and its ledger extract, the extractor, the
spreadsheet controls, reconcile and promotion, the metric registry, the
governed layer that answers a structured question, and the question path:
the model turns a question in words into a specification and nothing else,
and Python validates, compiles and runs it, narrates the answer and records
every question. The web pages are still stubs.

To check a planning workbook against a ledger extract:

```bash
python main.py check data/planning_model.xlsx data/erp_trial_balance.csv
```

It lands both files in a temporary warehouse, prints each file's batch record
and every break the spreadsheet controls and reconcile find, and deletes the
warehouse; it never promotes. The rules are in
[docs/controls.md](docs/controls.md), the long table another planning export
can be given in is in [docs/export_contract.md](docs/export_contract.md), and
the project's terms are in [docs/glossary.md](docs/glossary.md).

To ask a question written as a specification (a JSON file of eight fields):

```bash
python main.py query question.json --pair sample --role entity_controller --entity AT --acknowledge-all-as "A. Reviewer" --promote-as "P. Promoter"
```

It builds the warehouse of both pairs in a temporary folder, acknowledges
and promotes the pair as the people named, and prints the outcome or the
answer: the SQL, the figures, the coverage and gaps, and the provenance. The
metric definitions and the finance rules are in
[docs/registry.md](docs/registry.md); the session, the roles, the compiler
and the guard are in [docs/governed_layer.md](docs/governed_layer.md).

To ask a question in words, with your own key in the environment variable
`ANTHROPIC_API_KEY` (the command reads it from there alone and never prints
it):

```bash
python main.py ask "What was revenue by entity for the year to June, against Budget?" --pair sample --role group_controller --acknowledge-all-as "A. Reviewer" --promote-as "P. Promoter"
```

It builds the warehouse in a temporary folder, has the model write a
specification, and prints the answer or the outcome, the narration (the
narration template by default; `--narrate model` asks for the model's
wording, shown only when the checks give it no flag), the model calls and
the run id. It writes one line for the question to `audit_log/audit.jsonl`
(`--audit <folder>` names another folder), and deletes the temporary folder.

To ask a recorded specification again, with no key and no model call:

```bash
python main.py replay audit_log/audit.jsonl <run id>
```

It says whether the SQL and the result are the same. The question path, the
checks and their limits, the audit and replay are in
[docs/ask.md](docs/ask.md).

## What it will be

A governed question-and-answer pipeline over planning data. A planning model built in a spreadsheet will be extracted, checked, landed in a warehouse, tied out against a second source, and then queried in plain English. The model will never write SQL and never calculate: it will turn a question into a structured query specification, and Python will validate that specification against finance-owned metric definitions, compile the query, run it under the user’s access role and return the figures with their provenance. The aim is to show that the path from a planning model to a language model can be governed.

## Test suite

<!-- test-count: written by tests/conftest.py after a full, passing run, from generated/test_counts.json; never typed -->
The suite has **2809 tests**, counted by its last full, passing run (`generated/test_counts.json`).
<!-- /test-count -->

The suite blocks every connection that leaves this machine, including during
imports and fixtures. Connections to localhost are allowed for local stand-in
servers.

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
```

The inner loop is `python -m pytest --ignore=tests/test_grid.py`; a phase gate runs the full suite.

Running it locally on Windows, two things to know:

- A virtual environment under a deeply nested folder cannot load DuckDB's DLL, because of the 260-character path limit; keep the venv's path short.
- Git Bash's `rm -rf` cannot remove some files of a venv that uv installed; PowerShell's `Remove-Item -Recurse -Force` can.

GitHub Actions runs the whole suite on every push and pull request, on Python
3.13 and 3.14, installed from the lock files.

## Releasing

The owner reboots the app after every push to a deployed branch. The steps,
and the commands that compile the lock files, are in
[docs/releasing.md](docs/releasing.md).
