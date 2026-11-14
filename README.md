# Planning-to-Warehouse-to-LLM Pipeline

**Phase 2, not yet usable.** What exists: the sample workbook and its ledger
extract, the extractor, the spreadsheet controls, reconcile and promotion,
the metric registry, and the governed layer that answers a structured
question: sessions, roles, validation, the compiler and the guard. No model
is called yet.

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

## What it will be

A governed question-and-answer pipeline over planning data. A planning model built in a spreadsheet will be extracted, checked, landed in a warehouse, tied out against a second source, and then queried in plain English. The model will never write SQL and never calculate: it will turn a question into a structured query specification, and Python will validate that specification against finance-owned metric definitions, compile the query, run it under the user’s access role and return the figures with their provenance. The aim is to show that the path from a planning model to a language model can be governed.

## Test suite

<!-- test-count: written by tests/conftest.py after a full, passing run, from generated/test_counts.json; never typed -->
The suite has **2466 tests**, counted by its last full, passing run (`generated/test_counts.json`).
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
