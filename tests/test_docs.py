"""The docs of part 1d's second commit (brief 6.5): the export contract, and what
docs/controls.md says about reconcile and promotion.

The export contract is the long table of the long-format reader, with a
generic-terms mapping and no page for any one tool. docs/controls.md states
reconcile's rules and severity grades (their table is tests/test_reconcile.py's),
how the clean workbook was made to tie, the promotion gate, and that
acknowledgements are lost when the app restarts.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "docs" / "export_contract.md"
CONTROLS = ROOT / "docs" / "controls.md"


def flat(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def table_rows(path: Path, first_header: str) -> list:
    """The body rows of the Markdown table whose first header cell is first_header."""
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith(f"| {first_header} |"))
    rows = []
    for line in lines[start + 2:]:
        if not line.startswith("|"):
            break
        rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


class TestTheExportContract:
    def test_the_long_table_is_the_readers(self):
        from src.long_format import HEADER
        rows = table_rows(CONTRACT, "Column")
        assert tuple(r[0].strip("`") for r in rows) == HEADER == (
            "version", "entity", "account", "period", "amount", "unit")

    def test_the_rules_are_the_readers(self):
        text = flat(CONTRACT)
        for phrase in ("version,entity,account,period,amount,unit", "byte order mark", "UTF-8",
                       "plain decimal", "2026-06", "not run: the source has no formulas",
                       "row error"):
            assert phrase in text, phrase

    def test_the_generic_terms_mapping(self):
        rows = table_rows(CONTRACT, "Generic term")
        assert [r[0] for r in rows] == ["scenario", "dimension member", "line item", "period"]
        assert [r[1].strip("`") for r in rows] == ["version", "entity", "account", "period"]

    def test_no_page_for_any_one_tool(self):
        assert not (ROOT / "docs" / "mappings").exists()
        assert sorted(p.name for p in (ROOT / "docs").glob("*.md")) == [
            "controls.md", "export_contract.md", "glossary.md", "rebuilding_the_sample.md",
            "releasing.md"]


class TestControlsAddsReconcileAndPromotion:
    def test_reconcile_is_described(self):
        text = flat(CONTROLS)
        for phrase in ("## Reconcile", "Source against raw", "whole euros",
                       "presentation sign", "equal and opposite in ledger sign",
                       "one break, not one per account", "row error"):
            assert phrase in text or phrase in CONTROLS.read_text(encoding="utf-8"), phrase

    def test_the_clean_workbook_choice_is_stated(self):
        text = flat(CONTROLS)
        assert ("The clean workbook's Actual block was made equal to the ledger when the sample "
                "was built, so the clean workbook against the same ledger gives no break.") in text

    def test_promotion_and_the_restart_line(self):
        text = flat(CONTROLS)
        assert "## Promotion" in CONTROLS.read_text(encoding="utf-8")
        assert ("Acknowledgements are lost when the app restarts, because the warehouse is "
                "rebuilt at start. The audit trail of Phase 3 is the lasting record.") in text
        for phrase in ("accepted, dismissed or investigated", "no default",
                       "at most 60 characters", "at most 500 characters", "appends",
                       "the previous promotion stays current"):
            assert phrase in text, phrase

    def test_facts_about_the_file_are_never_findings_of_reconcile(self):
        text = flat(CONTROLS)
        assert re.search(r"reconcile never compares (them|the file's facts)", text)
