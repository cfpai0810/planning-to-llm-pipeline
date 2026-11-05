"""The two CSV readers (src/ledger.py and src/long_format.py), part 1c, brief 5.2.

Both accept a leading byte order mark. An amount is a plain decimal (an
optional minus, digits, an optional point and digits); anything else is kept
as a typed row error with its text and no amount, never coerced. Rows with the
same key are all kept and all flagged. An entity or account code that is not
on the lists passed in is flagged: the caller passes the codes of the Lists
tab of the workbook the file is checked with. A wrong header refuses the file
with one plain line. The expected results are written by hand, line by line.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from src.hashing import file_sha256

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "data" / "erp_trial_balance.csv"
READERS = ROOT / "tests" / "fixtures" / "readers"
ENTITIES = ("ES", "AT", "DE")
LEDGER_ACCOUNTS = ("4000", "4100", "5000", "5100", "5200", "6000", "6100", "6200", "6300")
ALL_ACCOUNTS = LEDGER_ACCOUNTS[:5] + ("GP",) + LEDGER_ACCOUNTS[5:] + ("EBIT", "VOL", "HC")
CLOSED = tuple(f"2026-{m:02d}" for m in range(1, 7))

# line number -> (entity, period, account, amount text, amount, row error)
LEDGER_CASES = {
    2: ("ES", "2026-01", "4000", "-1490400.00", Decimal("-1490400.00"), None),
    3: ("ES", "2026-01", "6100", "63.50", Decimal("63.50"), None),
    4: ("ES", "2026-01", "6200", "1,234.00", None, "amount_thousands_separator"),
    5: ("ES", "2026-01", "6300", "(1234.00)", None, "amount_brackets"),
    6: ("AT", "2026-01", "4000", "n/a", None, "amount_not_plain"),
    7: ("AT", "2026-01", "4100", "", None, "amount_missing"),
    8: ("FR", "2026-01", "4000", "10.00", Decimal("10.00"), None),
    9: ("DE", "2026-01", "9999", "20.00", Decimal("20.00"), None),
    10: ("DE", "2026-02", "5000", "30.00", Decimal("30.00"), None),
    11: ("DE", "2026-02", "5000", "30.00", Decimal("30.00"), None),
    12: ("DE", "2026-13", "5000", "1.00", Decimal("1.00"), "period_not_yyyy_mm"),
    13: ("DE", "2026-03", "5000", None, None, "fields"),
    14: ("DE", "2026-03", "5100", "1e3", None, "amount_not_plain"),
    15: ("DE", "2026-03", "5200", "0.10", Decimal("0.10"), None),
    16: ("DE", "2026-04", "5000", None, None, "fields"),
}


@pytest.fixture(scope="module")
def ledger():
    from src import ledger
    return ledger


@pytest.fixture(scope="module")
def long_format():
    from src import long_format
    return long_format


def read(ledger, path, entities=ENTITIES, accounts=LEDGER_ACCOUNTS):
    return ledger.read_ledger(path, entities=entities, accounts=accounts)


class TestTheLedger:
    def test_the_committed_ledger(self, ledger):
        result = read(ledger, LEDGER)
        assert len(result.lines) == 3 * 6 * 9
        assert [(l.row_error, l.duplicate, l.unknown_entity, l.unknown_account)
                for l in result.lines] == [(None, False, False, False)] * 162
        first = result.lines[0]
        assert (first.line_number, first.entity, first.period, first.account) == (2, "ES",
                                                                                  "2026-01", "4000")
        assert first.amount_text == "-1445550.00" and first.amount == Decimal("-1445550.00")
        assert all(type(l.amount) is Decimal and str(l.amount) == l.amount_text
                   for l in result.lines)

    def test_the_ledgers_batch_record(self, ledger):
        batch = read(ledger, LEDGER).batch
        sha = file_sha256(LEDGER)
        assert (batch.batch_id, batch.file_sha256, batch.file_name) == (
            sha[:16], sha, "erp_trial_balance.csv")
        assert batch.source == "ledger" and batch.file_bytes == LEDGER.stat().st_size
        assert batch.has_formulas is False and batch.sheets == ()
        assert batch.calculation == "not applicable: the source has no formulas"
        assert (batch.creator, batch.application, batch.external_links) == (None, None, ())

    def test_the_ledgers_control_totals(self, ledger):
        result = read(ledger, LEDGER)
        totals = {(t.version, t.period): t for t in result.control_totals}
        assert set(totals) == {(None, p) for p in CLOSED}
        for (_, period), total in totals.items():
            amounts = [l.amount for l in result.lines if l.period == period]
            assert total.row_count == len(amounts) == 27
            assert total.amount_sum == sum(amounts, Decimal(0))

    def test_every_case_line_by_line(self, ledger):
        result = read(ledger, READERS / "ledger_cases.csv")
        got = {l.line_number: (l.entity, l.period, l.account, l.amount_text, l.amount, l.row_error)
               for l in result.lines}
        assert got == LEDGER_CASES

    def test_an_amount_is_never_coerced(self, ledger):
        for line in read(ledger, READERS / "ledger_cases.csv").lines:
            if line.row_error and line.row_error.startswith("amount"):
                assert line.amount is None and line.amount_text is not None

    def test_duplicates_are_kept_and_flagged(self, ledger):
        lines = read(ledger, READERS / "ledger_cases.csv").lines
        assert [l.line_number for l in lines if l.duplicate] == [10, 11]

    def test_unknown_codes_are_flagged(self, ledger):
        lines = read(ledger, READERS / "ledger_cases.csv").lines
        assert [l.line_number for l in lines if l.unknown_entity] == [8]
        assert [l.line_number for l in lines if l.unknown_account] == [9]

    def test_the_lists_passed_in_decide_what_is_unknown(self, ledger):
        lines = read(ledger, LEDGER, entities=("ES", "AT"), accounts=LEDGER_ACCOUNTS[:-1]).lines
        assert {l.entity for l in lines if l.unknown_entity} == {"DE"}
        assert {l.account for l in lines if l.unknown_account} == {"6300"}

    def test_a_byte_order_mark_is_accepted(self, ledger):
        result = read(ledger, READERS / "ledger_bom.csv")
        assert [(l.entity, l.amount) for l in result.lines] == [("ES", Decimal("-1490400.00"))]
        assert (READERS / "ledger_bom.csv").read_bytes().startswith(b"\xef\xbb\xbf")

    def test_crlf_line_endings_are_read_as_well(self, ledger, tmp_path):
        path = tmp_path / "crlf.csv"
        path.write_bytes(LEDGER.read_bytes().replace(b"\n", b"\r\n"))
        assert [(l.entity, l.amount) for l in read(ledger, path).lines] == \
            [(l.entity, l.amount) for l in read(ledger, LEDGER).lines]

    @pytest.mark.parametrize("text", ["", "entity,period,account\nES,2026-01,4000\n",
                                      "Entity,Period,Account,Amount\n",
                                      "period,entity,account,amount\n"],
                             ids=["empty", "three columns", "capitals", "order"])
    def test_a_wrong_header_refuses_the_file(self, ledger, tmp_path, text):
        path = tmp_path / "ledger.csv"
        path.write_text(text, encoding="utf-8", newline="\n")
        with pytest.raises(ledger.SourceError) as caught:
            read(ledger, path)
        assert caught.value.kind == "header" and "\n" not in str(caught.value)

    def test_text_that_is_not_utf8_refuses_the_file(self, ledger, tmp_path):
        path = tmp_path / "ledger.csv"
        path.write_bytes(b"entity,period,account,amount\nES,2026-01,4000,\xff\n")
        with pytest.raises(ledger.SourceError) as caught:
            read(ledger, path)
        assert caught.value.kind == "encoding"


# line number -> (version, entity, account, period, amount, unit, row error)
LONG_CASES = {
    2: ("Budget", "ES", "4000", "2026-01", Decimal("1490400"), "EUR", None),
    3: ("Budget", "ES", "4000", "2026-01", Decimal("1490400"), "EUR", None),
    4: ("Budget", "FR", "4000", "2026-01", Decimal("10"), "EUR", None),
    5: ("Budget", "ES", "9999", "2026-01", Decimal("10"), "EUR", None),
    6: ("Budget", "ES", "4100", "2026-01", None, "EUR", "amount_thousands_separator"),
    7: ("Budget", "ES", "5000", "2026-01", None, "EUR", "amount_brackets"),
    8: ("Budget", "ES", "5100", "2026-01", None, "EUR", "amount_missing"),
    9: ("Budget", "ES", "5200", "FY2026", Decimal("10"), "EUR", "period_not_yyyy_mm"),
    10: ("Budget plan; draft", "ES", "6000", "2026-01", Decimal("10"), "EUR", None),
    11: ("Budget", "ES", "6100", "2026-02", Decimal("0.1"), None, None),
    12: ("", "ES", "6200", "2026-01", Decimal("10"), "EUR", "version_missing"),
    13: ("Budget", "ES", "6300", "2026-01", None, None, "fields"),
}


def read_long(long_format, path, entities=ENTITIES, accounts=ALL_ACCOUNTS):
    return long_format.read_long_format(path, entities=entities, accounts=accounts)


class TestTheLongFormat:
    def test_the_contract(self, long_format):
        assert long_format.HEADER == ("version", "entity", "account", "period", "amount", "unit")
        result = read_long(long_format, READERS / "long_format.csv")
        assert [(c.version, c.entity, c.account, c.period, c.amount, c.unit)
                for c in result.cells] == [
            ("Budget", "ES", "4000", "2026-01", Decimal("1490400"), "EUR"),
            ("Budget", "ES", "EBIT", "2026-01", Decimal("208842"), "EUR"),
            ("Budget", "ES", "VOL", "2026-01", Decimal("432000"), "units"),
            ("Budget", "ES", "HC", "2026-01", Decimal("64"), "FTE"),
            ("Forecast", "ES", "HC", "2026-01", Decimal("63.5"), "FTE"),
            ("Budget", "AT", "6100", "2026-03", Decimal("40099"), "EUR")]
        for c in result.cells:
            assert (c.sheet, c.address, c.block, c.formula, c.cached) == (None,) * 5
            assert (c.kind, c.value_type, c.column_kind, c.row_error) == ("constant", "number",
                                                                          "month", None)
            assert c.line == c.account and c.batch_id == result.batch.batch_id
        assert [c.source_line for c in result.cells] == [2, 3, 4, 5, 6, 7]

    def test_its_batch_record_says_the_source_has_no_formulas(self, long_format):
        batch = read_long(long_format, READERS / "long_format.csv").batch
        assert batch.source == "long_format" and batch.has_formulas is False
        assert batch.calculation == "not applicable: the source has no formulas"
        assert batch.file_sha256 == file_sha256(READERS / "long_format.csv")

    def test_every_case_line_by_line(self, long_format):
        result = read_long(long_format, READERS / "long_format_cases.csv")
        got = {c.source_line: (c.version, c.entity, c.line, c.period, c.amount, c.unit,
                               c.row_error) for c in result.cells}
        assert got == LONG_CASES

    def test_errors_duplicates_and_unknown_codes(self, long_format):
        cells = read_long(long_format, READERS / "long_format_cases.csv").cells
        assert [c.source_line for c in cells if c.duplicate] == [2, 3]
        assert [c.source_line for c in cells if c.unknown_entity] == [4]
        assert [(c.source_line, c.account) for c in cells if c.unknown_account] == [(5, None)]
        for c in cells:
            if c.row_error and c.row_error.startswith("amount"):
                assert c.amount is None and c.value_type in ("text", "none")
        text = next(c for c in cells if c.source_line == 6)
        assert (text.kind, text.value_type, text.value_text) == ("constant", "text", "42,000")
        assert next(c for c in cells if c.source_line == 8).kind == "empty"

    def test_a_rejected_name_is_recorded_and_landed_raw(self, long_format):
        result = read_long(long_format, READERS / "long_format_cases.csv")
        assert [(n.member, n.name, n.reasons, n.source_line) for n in result.rejected_names] == [
            ("version", "Budget plan; draft", ("characters",), 10)]
        line_10 = next(c for c in result.cells if c.source_line == 10)
        assert line_10.version == "Budget plan; draft" and line_10.name_allowed is False
        assert all(c.name_allowed for c in result.cells if c.source_line != 10)

    def test_its_control_totals(self, long_format):
        result = read_long(long_format, READERS / "long_format.csv")
        totals = {(t.version, t.period): (t.row_count, t.amount_sum) for t in result.control_totals}
        assert totals == {("Budget", "2026-01"): (4, Decimal("2131306")),
                          ("Forecast", "2026-01"): (1, Decimal("63.5")),
                          ("Budget", "2026-03"): (1, Decimal("40099"))}

    def test_a_byte_order_mark_is_accepted(self, long_format, tmp_path):
        path = tmp_path / "long.csv"
        path.write_bytes(b"\xef\xbb\xbf" + (READERS / "long_format.csv").read_bytes())
        assert len(read_long(long_format, path).cells) == 6

    def test_a_wrong_header_refuses_the_file(self, long_format, tmp_path):
        path = tmp_path / "long.csv"
        path.write_text("entity,period,account,amount\n", encoding="utf-8", newline="\n")
        with pytest.raises(long_format.SourceError) as caught:
            read_long(long_format, path)
        assert caught.value.kind == "header"
