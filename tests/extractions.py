"""One extraction, and one run of the controls, per committed workbook per test session.

The control tests of part 1d (tests/test_pattern.py and tests/test_controls.py)
read the same committed workbooks many times. Extracting a workbook takes a
few tenths of a second, so each is extracted once per session and kept here,
never once per test. Workbooks a test builds for itself (a copy edited inside
the zip, or rows changed in memory) are not cached.
"""

from __future__ import annotations

import functools
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FIXTURES = ROOT / "tests" / "fixtures"
MUTATIONS = FIXTURES / "mutations"
SAMPLE = DATA / "planning_model.xlsx"
CLEAN = DATA / "planning_model_clean.xlsx"


@functools.cache
def extraction(path: Path):
    from src.extract import extract_workbook
    return extract_workbook(path)


@functools.cache
def report(path: Path):
    from src.controls import run_controls
    return run_controls(extraction(path))


def breaks(path: Path) -> tuple:
    return report(path).breaks()
