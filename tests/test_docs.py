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
        # Part 2e adds the registry's rules and the governed layer.
        assert sorted(p.name for p in (ROOT / "docs").glob("*.md")) == [
            "controls.md", "export_contract.md", "glossary.md", "governed_layer.md",
            "rebuilding_the_sample.md", "registry.md", "releasing.md"]


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


# -- Part 1e ---------------------------------------------------------------------------------

README = ROOT / "README.md"


class TestThe1eDocs:
    def test_promotion_names_its_promoter_and_rebuilds_in_one_transaction(self):
        text = flat(CONTROLS)
        for phrase in ("named person", "`promoted_by`", "in one transaction",
                       "the previous facts stay"):
            assert phrase in text, phrase

    def test_the_readme_states_the_two_windows_quirks_in_a_local_run_note(self):
        readme = README.read_text(encoding="utf-8")
        suite = readme.split("\n## Test suite\n", 1)[1].split("\n## ", 1)[0]
        quirks = [line for line in suite.splitlines() if line.startswith("- ")]
        assert len(quirks) == 2
        assert "260" in quirks[0] and "DuckDB" in quirks[0]
        assert "rm -rf" in quirks[1] and "Remove-Item" in quirks[1] and "uv" in quirks[1]


# -- Part 2e: docs/registry.md and docs/governed_layer.md ------------------------------------------

REGISTRY_DOC = ROOT / "docs" / "registry.md"
GOVERNED = ROOT / "docs" / "governed_layer.md"


class TestTheRegistryDoc:
    def test_the_rules_of_4_2_are_stated_in_words(self):
        text = flat(REGISTRY_DOC)
        for phrase in ("Every question names one version: Actual, Budget or Forecast",
                       "the ledger is not queried",
                       "A period is a month (2026-06), a quarter (Q1 to Q4), the year to date "
                       "through a month, or the fiscal year, FY2026",
                       "Flows sum over the months of the window",
                       "never summed across months",
                       "the ratio of the sums, never an average of ratios",
                       "32.0%, not 40%",
                       "not meaningful",
                       "no_data, never a zero",
                       "prior_year_not_available",
                       "Variance is the base less the comparator",
                       "percentage points",
                       "rounded half away from zero, in Python, once, where it is shown",
                       "Revenue per unit is shown to two decimal places"):
            assert phrase in text, phrase

    def test_the_rulings_of_8_november_are_stated(self):
        text = flat(REGISTRY_DOC)
        # Q4: held per metric; Q5: matched by entity-month; Q7: the closing rule.
        for phrase in ("held for a metric in a version when the entity has a figure for every "
                       "account the metric reads: a zero is a figure, an empty cell is not",
                       "from the first to the last month in which any entity in scope has any "
                       "figure for the version",
                       "matched by entity-month and per metric",
                       "left out of the sum and listed as a gap; it never falls back to an "
                       "earlier month",
                       "each month or quarter has its own closing month"):
            assert phrase in text, phrase

    def test_the_approver_double_rounding_and_revenue_per_unit_sentences(self):
        text = flat(REGISTRY_DOC)
        assert ("The approver in the changelog, E. Marsh, Group Financial Controller, Calder "
                "Foods Group, is a fictional holder of a role in the sample company.") in text
        assert ("A displayed ratio is the twelve-place value rounded once more, to one place; "
                "it can differ from rounding the exact quotient only when that quotient lies "
                "within 5 times 10 to the minus 13 of a display tie.") in text
        assert ("A variance percentage for revenue per unit would be a change of definition, "
                "made only with a new registry version.") in text

    def test_a_ratio_with_a_stock_operand_is_refused(self):
        assert "a ratio with a stock operand" in flat(REGISTRY_DOC)

    def test_the_metric_table_is_the_one_the_script_generates(self):
        from scripts import registry_table
        from src import registry
        assert registry_table.block_of(REGISTRY_DOC.read_text(encoding="utf-8")) == \
            registry_table.table(registry.load())

    def test_the_table_names_every_metric_once(self):
        from scripts import registry_table
        from src import registry
        loaded = registry.load()
        table = registry_table.table(loaded)
        rows = [line for line in table.splitlines() if line.startswith("| `")]
        assert [r.split("`")[1] for r in rows] == list(loaded.ids())

    def test_a_changed_registry_changes_the_table(self):
        import copy
        import tomllib

        from scripts import registry_table
        from src import registry
        data = tomllib.loads((ROOT / "registry" / "metrics.toml").read_text(encoding="utf-8"))
        changed = copy.deepcopy(data)
        changed["metric"][0]["label"] = "Sales"
        assert registry_table.table(registry.from_data(changed, sha256="0" * 64)) != \
            registry_table.table(registry.from_data(data, sha256="0" * 64))


class TestTheGovernedLayerDoc:
    def test_the_control_and_the_second_line_are_said_plainly(self):
        text = flat(GOVERNED)
        for phrase in ("the read-only connection with external access off is the control; "
                       "the guard is a second line; the allow-list of names narrows the "
                       "injection route and does not close it",
                       "v1 does not query the ledger",
                       "does not answer prior-year questions"):
            assert phrase in text, phrase

    def test_the_session_roles_compiler_and_guard_are_described(self):
        text = flat(GOVERNED)
        for phrase in ("## Sessions and promotion", "## Roles", "## The compiler",
                       "## The guard", "## Outcomes"):
            assert phrase in GOVERNED.read_text(encoding="utf-8"), phrase
        for phrase in ("one cursor per request", "entity view: AT",
                       "That entity is not available to your role.", "fact_plan",
                       "dim_entity", "exactly once", "byte for byte", "integer_route",
                       "tests/guard_corpus.txt"):
            assert phrase in text, phrase

    def test_a_temp_object_on_one_cursor_is_explained(self):
        text = flat(GOVERNED)
        assert ("The read-only connection stops every change to the file, but not an object "
                "in memory that lives as long as one cursor") in text
        assert "CREATE TEMP TABLE" in text

    def test_regions_under_an_entity_controller_are_explained(self):
        text = flat(GOVERNED)
        assert "Central Europe" in text and "Southern Europe" in text
        assert "entity_not_permitted" in text

    def test_every_outcome_is_listed_with_its_sentence(self):
        from src import outcomes
        text = flat(GOVERNED)
        for code, sentence in outcomes.SENTENCES.items():
            assert f"`{code}`" in text and sentence in text, code
