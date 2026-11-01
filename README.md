# Planning-to-Warehouse-to-LLM Pipeline

**Phase 0, not yet usable.** This repository holds the kit that later phases
build on: the pinned stack, the test guards and the visual theme. Nothing here
answers a question yet.

## What it will be

A governed question-and-answer pipeline over planning data. A planning model built in a spreadsheet will be extracted, checked, landed in a warehouse, tied out against a second source, and then queried in plain English. The model will never write SQL and never calculate: it will turn a question into a structured query specification, and Python will validate that specification against finance-owned metric definitions, compile the query, run it under the user’s access role and return the figures with their provenance. The aim is to show that the path from a planning model to a language model can be governed.

## Test suite

<!-- test-count: written by tests/conftest.py after a full, passing run, from generated/test_counts.json; never typed -->
The suite has **58 tests**, counted by its last full, passing run (`generated/test_counts.json`).
<!-- /test-count -->

The suite blocks every connection that leaves this machine, including during
imports and fixtures. Connections to localhost are allowed for local stand-in
servers.

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
```

GitHub Actions runs the whole suite on every push and pull request, on Python
3.13 and 3.14, installed from the lock files.

## Releasing

The owner reboots the app after every push to a deployed branch. The steps,
and the commands that compile the lock files, are in
[docs/releasing.md](docs/releasing.md).
