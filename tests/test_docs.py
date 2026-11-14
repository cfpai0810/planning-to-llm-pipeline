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
    def test_what_the_connection_stops_and_the_controls_for_reads_are_said_plainly(self):
        # Changed on purpose in 3a (the reviewer's verdict of 11 November, F23): the brief's
        # "the read-only connection ... is the control" was imprecise. The connection stops
        # writes, files and settings, not reads; for reads the compiler and the guard are the
        # controls.
        text = flat(GOVERNED)
        first = flat_text(GOVERNED.read_text(encoding="utf-8").split("\n\n")[2])
        assert first.startswith("Said plainly: ") and READS in first
        for phrase in (READS, "the guard, a second line behind the compiler's fixed templates",
                       "The allow-list of names narrows the injection route and does not close "
                       "it", "v1 does not query the ledger",
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


# -- Commit 3a: the reviewer's verdict of 11 November ----------------------------------------------

# The reviewer's sentence (F23), as the doc's first paragraph and the guard's docstring say it.
READS = ("the read-only connection, with external access off, stops writes, files and "
         "settings; it does not stop reads; for reads the controls are the compiler, which "
         "writes only fixed templates with canonical members, and the guard")
GUARD = ROOT / "src" / "guard.py"
SPEC = ROOT / "src" / "spec.py"


def flat_text(text: str) -> str:
    return " ".join(text.split())


def docstring(path: Path) -> str:
    import ast
    return flat_text(ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))))


def section(path: Path, heading: str) -> str:
    """The flat text of one section of a Markdown file, from its heading to the next."""
    text = path.read_text(encoding="utf-8")
    level = heading.split(" ", 1)[0]
    body = text.split(f"\n{heading}\n", 1)[1]
    return flat_text(re.split(rf"\n#{{1,{len(level)}}} ", body, maxsplit=1)[0])


class TestThe3aDocs:
    def test_no_passage_calls_the_connection_the_control(self):
        # F23: the sentence came from the brief and stood in three places.
        places = [README, *sorted((ROOT / "docs").glob("*.md")), ROOT / "main.py",
                  *sorted((ROOT / "src").glob("*.py"))]
        found = [p.name for p in places
                 if re.search(r"is the control(?![\w'])", flat(p))]
        assert found == []

    def test_the_guards_docstring_says_what_the_guard_is_behind(self):
        text = docstring(GUARD)
        assert text.startswith("The guard (the reviewer's Phase 2 brief, 6.4): a second line "
                               "behind the compiler's fixed templates.")
        assert READS[0].upper() + READS[1:] in text

    def test_the_condition_under_which_a_star_only_file_is_required(self):
        # D2: accepted for v1, with the condition written where the reads are described.
        text = section(GOVERNED, "### What the read-only connection does not stop")
        for phrase in ("a star-only file, one per session, becomes a requirement, not an "
                       "option, before any of these:",
                       "real company data in the template;",
                       "any feature in which text written by a person or a model reaches the "
                       "engine as SQL;",
                       "any setting in which a visitor can reach the file other than through "
                       "`session.answer`",
                       "`PRAGMA database_list` on the connection shows the path of the "
                       "session's folder"):
            assert phrase in text, phrase
        # The paragraph on reads stays.
        assert ("For reads the compiler (which writes only the queries below) and the guard "
                "(which refuses any other relation and any unfiltered `fact_plan`) are the "
                "controls.") in text

    def test_the_limit_on_telling_that_a_code_exists_is_closed(self):
        # D3: the limit kept as the brief designed it is closed, and the doc says the rule.
        text = section(GOVERNED, "## Roles")
        assert "A limit, kept as the brief designed it" not in text
        assert "can tell that a code exists" not in text
        for phrase in ("matched against its own entity and its own regions alone",
                       "Any other value, real or made up",
                       "is `entity_not_permitted`: one and the same outcome, so an entity "
                       "controller cannot tell whether a code exists",
                       "A group controller sees every entity, and a name that is no member is "
                       "`unknown_member`",
                       "an unknown one is `unknown_member` for every role"):
            assert phrase in text, phrase

    def test_a_role_of_the_wrong_shape_is_a_programming_fault(self):
        # F21.
        text = section(GOVERNED, "## Roles")
        for phrase in ("never from a visitor's words", "`RoleError`",
                       "`validate`, `compile`, `session.answer`, `session.run`, `members_for` "
                       "and `provenance`"):
            assert phrase in text, phrase

    def test_a_hand_built_specification_is_read_by_the_same_type_rules(self):
        # F24.
        text = section(GOVERNED, "## The specification and its validation")
        assert ("a specification built by hand is first read by the same type rules as "
                "`from_json`: a field of the wrong type is `specification_invalid`") in text

    def test_the_validation_docstring_states_the_order_and_the_rules(self):
        text = docstring(SPEC)
        for phrase in ("a role that is not a Role of the two shapes raises RoleError",
                       "the types, by the rules from_json enforces (specification_invalid)",
                       "real or made up, is entity_not_permitted",
                       "for a group controller a name that is no member is unknown_member"):
            assert phrase in text, phrase

    def test_an_account_filter_chooses_what_is_summed(self):
        # F22, in the registry's rules and in the compiler's guarantees.
        registry_text = section(REGISTRY_DOC, "### Held, by metric")
        assert "narrows the accounts a question reads" not in registry_text
        assert ("An account filter chooses which of those accounts are summed, and nothing "
                "else: held is judged on every account the metric reads, whatever the filter, "
                "so the slices of a metric by account add up to its total and a gap is a gap "
                "with or without the filter.") in registry_text
        assert ("**An account filter chooses what is summed, nothing else.**"
                in section(GOVERNED, "## The compiler"))

    def test_the_grid_and_the_trigger_for_its_own_ci_job(self):
        # D1: the whole grid stays in the default suite and in CI until the trigger.
        text = section(GOVERNED, "## The compiler")
        for phrase in ("The whole grid runs, unsampled, in the default suite and in CI",
                       "When a grid job takes more than 15 minutes, or CI minutes become a "
                       "cost, the grid becomes its own CI job per Python, and the rest of the "
                       "suite its own"):
            assert phrase in text, phrase

    def test_the_readme_gives_the_inner_loop_in_one_line(self):
        # D1.
        readme = README.read_text(encoding="utf-8")
        suite = readme.split("\n## Test suite\n", 1)[1].split("\n## ", 1)[0]
        lines = [line for line in suite.splitlines() if "--ignore=tests/test_grid.py" in line]
        assert lines == ["The inner loop is `python -m pytest --ignore=tests/test_grid.py`; a "
                         "phase gate runs the full suite."]

    def test_a_stale_folder_is_judged_by_its_age_alone(self):
        # F26: stated as a limit in 3a; changed on purpose in 3b, where the root became
        # required (the reviewer's Phase 3 brief, 4.7 item 4).
        text = section(GOVERNED, "## Sessions and promotion")
        assert "A limit: a folder left behind is judged by its age alone" not in text
        assert "Until that is changed" not in text
        for phrase in ("A pool's root is required, with no default (F26)",
                       "each deployment, the command line's and the app's, gives a root of "
                       "its own",
                       "a pool removes stale folders only directly under its own root, never "
                       "under another",
                       "A folder left behind is judged by its age alone, whether or not "
                       "another process still holds it",
                       "two processes given one root are not coordinated"):
            assert phrase in text, phrase


# -- Part 3b: the reviewer's Phase 3 brief, 4.7 and 4.8 ---------------------------------------------

class TestThe3bDocs:
    def test_provenance_takes_a_role_and_its_break_counts_are_pair_wide(self):
        # F27.
        text = section(GOVERNED, "## Sessions and promotion")
        for phrase in ("Provenance takes a role, always: there is no provenance without a view.",
                       "`breaks_scope`: `pair-wide`",
                       "the counts cover every entity of the pair and show no break's location "
                       "or text",
                       "whether the pair was reviewed before promotion, which is pair-wide by "
                       "construction"):
            assert phrase in text, phrase

    def test_the_engines_message_is_kept_for_the_audit_and_never_shown(self):
        text = section(GOVERNED, "## The compiler")
        assert ("the engine's message is handed to the caller that asks for it, for the audit, "
                "and never shown: the outcome a reader sees is the one fixed `engine_error`") \
            in text

    def test_the_question_paths_outcomes_are_in_the_table(self):
        text = section(GOVERNED, "## Outcomes")
        assert ("The question path (`src/ask.py`, part 3b) adds seven: `question_invalid` "
                "before anything is sent, the four refusal reasons a model may give, "
                "`session_ended` and `audit_unavailable`.") in text

    def test_the_schema_command_is_described(self):
        text = section(GOVERNED, "## Trying it")
        for phrase in ("`python main.py schema --out <folder>`", "`schema.json`",
                       "`schema_report.txt`", "calls nothing"):
            assert phrase in text, phrase

    def test_the_first_paragraph_says_the_model_writes_the_specification(self):
        first = flat_text(GOVERNED.read_text(encoding="utf-8").split("\n\n")[1])
        assert "No model is called yet" not in first
        assert ("A model writes the specification through the question path (`src/ask.py`), "
                "and nothing else.") in first
