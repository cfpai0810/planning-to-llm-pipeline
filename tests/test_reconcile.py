"""Reconcile (src/reconcile.py), part 1d, brief 6.2.

Every expectation is written by hand below and never read from the manifest,
so a bug in the build cannot make the files and the expectation agree.

- Source against raw: what the extractor read (its row counts and control
  totals, by version and period) against what landed, read back from the
  warehouse.
- The workbook's Actual block against the ledger, by entity, account and
  period, in whole euros and presentation sign. The sample gives exactly T1
  (an amount difference), T2 (one possible reclassification) and T3 (one
  missing entity-month); the clean workbook, whose Actual block 1b made equal
  to the ledger, gives none.
- Each kind of break is also shown on extracted rows changed in memory.

Breaks are the controls' Break type, with the prefix RC.
"""

import dataclasses
import hashlib
import json
import re
from decimal import Decimal

import pytest

from tests.extractions import CLEAN, ROOT, SAMPLE, extraction, ledger

SOURCE = ROOT / "src" / "reconcile.py"
CONTROLS_DOC = ROOT / "docs" / "controls.md"

# The severity of each kind of reconcile break, by the controls' rules: high
# when a figure as read is wrong or cannot be read or placed, medium when a
# figure may be wrong or a member is left out.
SEVERITY = {
    "source_against_raw": "high", "missing_entity_month": "high",
    "possible_reclassification": "medium", "amount_difference": "high",
    "duplicate_key": "high", "unknown_entity": "medium", "unknown_account": "medium",
    "row_error": "high",
}

# -- The sample against the ledger, by hand ---------------------------------------------
# (kind, severity, sheet, block, entity, account, period, address, message)
T1 = ("amount_difference", "high", "Plan_DE", "Actual_DE", "DE", "6100", "2026-06", "H14",
      'The Actual figure of "Marketing" for "DE" in 2026-06 is 103,645 in the workbook and '
      '110,995 in the ledger, in presentation sign and whole euros; the difference is -7,350.')
T2 = ("possible_reclassification", "medium", "Plan_ES", "Actual_ES", "ES", "6200,6300",
      "2026-04", "F15,F16",
      'For "ES" in 2026-04, "Rent and facilities" is 48,200 in the workbook and 58,000 in the '
      'ledger (difference -9,800), and "IT and software" is 31,200 in the workbook and 21,400 '
      'in the ledger (difference 9,800): the two differences are equal and opposite in ledger '
      'sign, a possible reclassification.')
T3 = ("missing_entity_month", "high", "Plan_AT", "Actual_AT", "AT", None, "2026-05", "G7:G16",
      'The workbook\'s Actual block holds no figure for "AT" in 2026-05, and the ledger holds '
      'figures for 9 accounts, netting to -104,632 in ledger sign: the entity-month is missing '
      'from the workbook, one break for all its accounts.')


def located(b) -> tuple:
    return (b.kind, b.severity, b.sheet, b.block, b.entity, b.account, b.period, b.address,
            b.message)


def rc_id(*anchors) -> str:
    text = json.dumps(["reconcile", *anchors], ensure_ascii=False, separators=(",", ":"))
    return "RC-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


@pytest.fixture(scope="module")
def reconcile():
    from src import reconcile
    return reconcile


@pytest.fixture(scope="module")
def sample():
    return extraction(SAMPLE)


@pytest.fixture(scope="module")
def clean():
    return extraction(CLEAN)


@pytest.fixture(scope="module")
def lines():
    return ledger()


@pytest.fixture
def built(built_warehouse):
    from src import warehouse
    with warehouse.connect(built_warehouse.path) as connection:
        yield connection


def compare(reconcile, plan, lines_, cells=None, ledger_lines=None):
    """Reconcile in memory: the plan's cells and the ledger's lines, changed or not."""
    pair = reconcile.Pair(plan.batch.batch_id, lines_.batch.batch_id)
    return reconcile.compare(pair, plan.cells if cells is None else cells,
                             lines_.lines if ledger_lines is None else ledger_lines, plan.lists)


def replace_lines(lines_, changes: dict, added=()):
    """The ledger's lines with some amounts changed in memory, keyed by (entity, period, account)."""
    out = []
    for line in lines_.lines:
        key = (line.entity, line.period, line.account)
        if key in changes:
            if changes[key] is None:
                continue
            amount = Decimal(changes[key])
            line = dataclasses.replace(line, amount=amount, amount_text=format(amount, "f"))
        out.append(line)
    return tuple(out) + tuple(added)


# -- The rules ---------------------------------------------------------------------------

class TestTheRules:
    def test_the_kinds_and_their_severity(self, reconcile):
        assert reconcile.SEVERITY == SEVERITY
        assert reconcile.KINDS == tuple(SEVERITY)
        assert reconcile.RECONCILE == "reconcile" and reconcile.ID_PREFIX == "RC"

    def test_the_break_is_the_controls_break(self, reconcile):
        from src.controls import Break
        assert reconcile.Break is Break

    def test_the_severity_grades_are_in_the_docs_with_their_rule(self):
        doc = " ".join(CONTROLS_DOC.read_text(encoding="utf-8").split())
        for kind, severity in SEVERITY.items():
            assert re.search(rf"\| `{kind}` \| {severity} \|", doc), kind
        assert "## Reconcile" in CONTROLS_DOC.read_text(encoding="utf-8")


# -- The sample and the clean workbook against the ledger -------------------------------

class TestTheProofs:
    def test_the_sample_gives_exactly_t1_t2_and_t3(self, reconcile, built, sample, lines):
        result = reconcile.reconcile(built, sample, lines)
        assert result.control == "reconcile" and result.status == "run"
        assert sorted(located(b) for b in result.breaks) == sorted([T1, T2, T3])

    def test_t1_t2_and_t3_have_their_ids_and_batches(self, reconcile, built, sample, lines):
        found = {b.kind: b for b in reconcile.reconcile(built, sample, lines).breaks}
        assert found["amount_difference"].break_id == rc_id("amount", "DE", "6100", "2026-06")
        assert found["possible_reclassification"].break_id == rc_id(
            "reclassification", "ES", "2026-04", "6200", "6300")
        assert found["missing_entity_month"].break_id == rc_id("entity_month", "AT", "2026-05")
        assert {b.batch_id for b in found.values()} == {sample.batch.batch_id}

    def test_the_evidence_holds_both_figures(self, reconcile, built, sample, lines):
        found = {b.kind: dict(b.evidence) for b in reconcile.reconcile(built, sample, lines).breaks}
        assert found["amount_difference"] == {
            "workbook": "103645", "ledger in presentation sign": "110995",
            "ledger as landed": "110995.00", "ledger sign": "Debit", "difference": "-7350",
            "ledger line": "161"}
        reclass = found["possible_reclassification"]
        assert reclass["differences in ledger sign"] == "6200: -9800; 6300: 9800"
        assert found["missing_entity_month"]["ledger figures, in presentation sign"] == (
            "4000: 888300; 4100: 15500; 5000: 412425; 5100: 76140; 5200: 57105; "
            "6000: 166400; 6100: 46998; 6200: 27500; 6300: 12600")

    def test_the_clean_workbook_gives_none(self, reconcile, warehouse_copy, clean, lines):
        # 1b made the clean workbook's Actual block equal to the ledger.
        from src import warehouse
        with warehouse.connect(warehouse_copy) as con:
            warehouse.land(con, clean)
            result = reconcile.reconcile(con, clean, lines)
        assert result.status == "run" and result.breaks == ()

    def test_the_warehouse_path_and_the_memory_path_agree(self, reconcile, built, sample, lines):
        from_warehouse = reconcile.reconcile(built, sample, lines).breaks
        in_memory = compare(reconcile, sample, lines)
        assert sorted(map(located, from_warehouse)) == sorted(map(located, in_memory))
        assert compare(reconcile, extraction(CLEAN), lines) == ()


# -- Each kind on rows changed in memory --------------------------------------------------

class TestEachKind:
    def test_a_late_journal_is_one_amount_difference(self, reconcile, clean, lines):
        # The ledger holds 500 more salaries for DE in March than the workbook.
        changed = replace_lines(lines, {("DE", "2026-03", "6000"): "570420.00"})
        original = next(l.amount for l in lines.lines
                        if (l.entity, l.period, l.account) == ("DE", "2026-03", "6000"))
        assert original == Decimal("569920.00")
        (b,) = compare(reconcile, clean, lines, ledger_lines=changed)
        assert (b.kind, b.entity, b.account, b.period, b.severity) == (
            "amount_difference", "DE", "6000", "2026-03", "high")
        assert b.message.endswith("the difference is -500.")

    def test_a_reclassification_is_one_break_not_two(self, reconcile, clean, lines):
        # 1,000 of ES's February marketing posted to rent in the ledger.
        get = {(l.entity, l.period, l.account): l.amount for l in lines.lines}
        changed = replace_lines(lines, {
            ("ES", "2026-02", "6100"): get[("ES", "2026-02", "6100")] - 1000,
            ("ES", "2026-02", "6200"): get[("ES", "2026-02", "6200")] + 1000})
        (b,) = compare(reconcile, clean, lines, ledger_lines=changed)
        assert (b.kind, b.account, b.address, b.severity) == (
            "possible_reclassification", "6100,6200", "D14,D15", "medium")

    def test_equal_and_opposite_is_judged_in_ledger_sign(self, reconcile, clean, lines):
        # 2,000 of revenue netted against materials in the ledger: in ledger sign
        # the revenue line is 2,000 less negative and materials 2,000 less
        # positive, which is equal and opposite; in presentation sign both fall.
        get = {(l.entity, l.period, l.account): l.amount for l in lines.lines}
        changed = replace_lines(lines, {
            ("AT", "2026-01", "4000"): get[("AT", "2026-01", "4000")] + 2000,
            ("AT", "2026-01", "5000"): get[("AT", "2026-01", "5000")] - 2000})
        (b,) = compare(reconcile, clean, lines, ledger_lines=changed)
        assert (b.kind, b.account) == ("possible_reclassification", "4000,5000")

    def test_two_differences_that_do_not_cancel_are_two_breaks(self, reconcile, clean, lines):
        get = {(l.entity, l.period, l.account): l.amount for l in lines.lines}
        changed = replace_lines(lines, {
            ("ES", "2026-02", "6100"): get[("ES", "2026-02", "6100")] - 1000,
            ("ES", "2026-02", "6200"): get[("ES", "2026-02", "6200")] + 1500})
        found = compare(reconcile, clean, lines, ledger_lines=changed)
        assert sorted((b.kind, b.account) for b in found) == [
            ("amount_difference", "6100"), ("amount_difference", "6200")]

    def test_a_missing_entity_month_is_one_break(self, reconcile, clean, lines):
        # ES's February emptied in the workbook's Actual block, in memory.
        cells = tuple(dataclasses.replace(c, kind="empty", formula=None, cached=None,
                                          value_type="none", value_text=None, amount=None)
                      if (c.block, c.period) == ("Actual_ES", "2026-02") and c.kind == "constant"
                      else c for c in clean.cells)
        (b,) = compare(reconcile, clean, lines, cells=cells)
        assert (b.kind, b.entity, b.period, b.account, b.address) == (
            "missing_entity_month", "ES", "2026-02", None, "D7:D16")

    def test_an_entity_month_missing_from_the_ledger_is_one_break(self, reconcile, clean, lines):
        changed = replace_lines(lines, {(l.entity, l.period, l.account): None
                                        for l in lines.lines
                                        if (l.entity, l.period) == ("DE", "2026-02")})
        (b,) = compare(reconcile, clean, lines, ledger_lines=changed)
        assert (b.kind, b.entity, b.period) == ("missing_entity_month", "DE", "2026-02")
        assert "missing from the ledger" in b.message

    def test_a_duplicate_key_is_a_break_and_is_not_compared(self, reconcile, clean, lines):
        first = next(l for l in lines.lines if (l.entity, l.period, l.account)
                     == ("AT", "2026-03", "6200"))
        copy = dataclasses.replace(first, line_number=999, amount=Decimal("1.00"),
                                   amount_text="1.00", duplicate=True)
        changed = tuple(dataclasses.replace(l, duplicate=True) if l is first else l
                        for l in lines.lines) + (copy,)
        (b,) = compare(reconcile, clean, lines, ledger_lines=changed)
        assert (b.kind, b.entity, b.account, b.period, b.severity, b.batch_id) == (
            "duplicate_key", "AT", "6200", "2026-03", "high", lines.batch.batch_id)
        assert dict(b.evidence)["ledger lines"] == f"{first.line_number}, 999"

    @pytest.mark.parametrize("field, code, kind", [("entity", "FR", "unknown_entity"),
                                                   ("account", "9999", "unknown_account")])
    def test_an_unknown_member_is_a_break(self, reconcile, clean, lines, field, code, kind):
        extra = dataclasses.replace(lines.lines[0], line_number=900, **{field: code})
        (b,) = compare(reconcile, clean, lines, ledger_lines=lines.lines + (extra,))
        assert (b.kind, b.severity, getattr(b, field)) == (kind, "medium", code)
        assert "900" in dict(b.evidence)["ledger lines"]

    def test_unknown_is_judged_against_the_plan_batchs_lists(self, reconcile, clean, lines):
        # The ledger was read against the sample's Lists; reconcile judges it
        # against the Lists of the plan batch it is paired with.
        lists = dataclasses.replace(clean.lists, entities=tuple(
            e for e in clean.lists.entities if e.code != "DE"))
        pair = reconcile.Pair(clean.batch.batch_id, lines.batch.batch_id)
        found = reconcile.compare(pair, clean.cells, lines.lines, lists)
        assert [(b.kind, b.entity) for b in found] == [("unknown_entity", "DE")]

    def test_a_ledger_row_error_is_a_break_and_its_key_is_not_compared(self, reconcile, clean,
                                                                       lines):
        index, first = next((i, l) for i, l in enumerate(lines.lines)
                            if (l.entity, l.period, l.account) == ("ES", "2026-05", "5100"))
        broken = dataclasses.replace(first, amount=None, amount_text="1,234.00",
                                     row_error="amount_thousands_separator")
        changed = lines.lines[:index] + (broken,) + lines.lines[index + 1:]
        (b,) = compare(reconcile, clean, lines, ledger_lines=changed)
        assert (b.kind, b.severity, b.entity, b.account, b.period) == (
            "row_error", "high", "ES", "5100", "2026-05")
        assert dict(b.evidence)["amount as the file holds it"] == "1,234.00"

    def test_whole_euros(self, reconcile, clean, lines):
        get = {(l.entity, l.period, l.account): l.amount for l in lines.lines}
        under = replace_lines(lines, {("ES", "2026-01", "6300"): get[("ES", "2026-01", "6300")]
                                      + Decimal("0.49")})
        assert compare(reconcile, clean, lines, ledger_lines=under) == ()
        over = replace_lines(lines, {("ES", "2026-01", "6300"): get[("ES", "2026-01", "6300")]
                                     + Decimal("0.50")})
        (b,) = compare(reconcile, clean, lines, ledger_lines=over)
        assert b.message.endswith("the difference is -1.")


class TestSourceAgainstRaw:
    def test_what_landed_equals_what_was_read(self, reconcile, built, sample, lines):
        assert reconcile.source_against_raw(built, sample) == ()
        assert reconcile.source_against_raw(built, lines) == ()

    def test_an_amount_changed_after_landing_is_a_break(self, reconcile, built, sample):
        from src.batch import control_totals
        index = next(i for i, c in enumerate(sample.cells)
                     if (c.block, c.line, c.period) == ("Budget_ES", "Salaries", "2026-07"))
        cell = sample.cells[index]
        cells = sample.cells[:index] + (dataclasses.replace(cell, amount=cell.amount + 10),) \
            + sample.cells[index + 1:]
        changed = dataclasses.replace(sample, cells=cells,
                                      control_totals=control_totals(sample.batch.batch_id, cells))
        (b,) = reconcile.source_against_raw(built, changed)
        assert (b.kind, b.severity, b.period, b.batch_id) == (
            "source_against_raw", "high", "2026-07", sample.batch.batch_id)
        assert b.break_id == rc_id("source", "workbook", "Budget", "2026-07")
        assert "the differences are 0 rows and 10" in b.message

    def test_a_row_missing_from_the_ledger_side_is_a_break(self, reconcile, built, lines):
        from src.batch import control_totals
        kept = lines.lines[1:]
        changed = dataclasses.replace(lines, lines=kept, control_totals=control_totals(
            lines.batch.batch_id, kept, version_of=lambda line: None))
        (b,) = reconcile.source_against_raw(built, changed)
        assert (b.kind, b.period, b.entity) == ("source_against_raw", "2026-01", None)
        assert "the differences are -1 rows" in b.message


class TestFactsAboutTheFile:
    def test_file_facts_change_nothing(self, reconcile, built, sample, lines):
        odd = dataclasses.replace(sample.batch, created="2031-01-01T00:00:00Z",
                                  modified="1999-12-31T23:59:59Z", application="Something else",
                                  creator="Someone", last_modified_by="Someone else",
                                  external_links=("elsewhere.xlsx",))
        changed = dataclasses.replace(sample, batch=odd)
        assert reconcile.reconcile(built, changed, lines).breaks \
            == reconcile.reconcile(built, sample, lines).breaks

    def test_reconcile_never_reads_them(self):
        text = SOURCE.read_text(encoding="utf-8")
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        body = code.split('"""', 2)[-1]           # after the module docstring
        for fact in ("created", "modified", "application", "creator", "last_modified_by",
                     "external_links"):
            assert not re.search(rf"\b{fact}\b", body), fact


# -- Part 2a2: what reconcile leaves out --------------------------------------------------
# The reviewer's ruling of 8 November, item 3: an entity whose Actual block is not built or
# not read is left out of the tie-out; and (the optional account-level case, F19) so is an
# account a built Actual block lacks. The structure break that explains it says so
# (tests/test_controls.py).

class TestWhatIsLeftOut:
    def test_an_entity_left_out_gives_no_break_of_its_own(self, reconcile, clean, lines):
        # ES's whole Actual block emptied in memory: six missing entity-months, or none.
        cells = tuple(dataclasses.replace(c, kind="empty", formula=None, cached=None,
                                          value_type="none", value_text=None, amount=None)
                      if c.block == "Actual_ES" else c for c in clean.cells)
        assert len(compare(reconcile, clean, lines, cells=cells)) == 6
        pair = reconcile.Pair(clean.batch.batch_id, lines.batch.batch_id)
        assert reconcile.compare(pair, cells, lines.lines, clean.lists, left_out=("ES",)) == ()

    def test_an_account_left_out_is_not_compared_and_the_rest_is(self, reconcile, sample,
                                                                  lines):
        # The sample's T2 (ES, 2026-04, 6200 and 6300): leaving 6300 out of ES turns the
        # reclassification into one amount difference on 6200; T1 and T3 stand.
        pair = reconcile.Pair(sample.batch.batch_id, lines.batch.batch_id)
        found = reconcile.compare(pair, sample.cells, lines.lines, sample.lists,
                                  accounts_left_out=(("ES", "6300"),))
        assert [(b.kind, b.entity, b.account, b.period) for b in found] == [
            ("missing_entity_month", "AT", None, "2026-05"),
            ("amount_difference", "DE", "6100", "2026-06"),
            ("amount_difference", "ES", "6200", "2026-04")]

    def test_reconcile_reads_what_to_leave_out_from_the_plan_batch(self, reconcile):
        import inspect
        source = inspect.getsource(reconcile.reconcile)
        assert "actual_left_out(" in source
