"""The command line (main.py), part 1d, brief 6.5.

python main.py check <workbook> <ledger> lands both files into a temporary
warehouse, which is deleted afterwards; prints each batch record's summary,
external link targets shown as text, as data; runs the controls and
reconcile and prints the breaks as a plain table with their count; and never
promotes. A file that is missing or cannot be read gives one plain line, no
traceback, and a non-zero exit.

The check is run here as a child process, with the environment
subprocess_env() gives it (no key, and a temporary folder of the test's own,
so the test can see it is left empty), on small sources: a test never builds
from the committed workbook a second time. What it prints for the committed
sample and ledger is read from the session's own start-up build, through the
same function the command uses.

Part 1e (the reviewer's verdict, item 7a): a file that is not a workbook is
told in plain words, and nothing the command prints names a Python class.
"""

import json
import re
import subprocess
import sys

import pytest

from tests.conftest import subprocess_env
from tests.extractions import FIXTURES, ROOT, extraction

MAIN = ROOT / "main.py"
SMALL_BOOK = FIXTURES / "extra_tab.xlsx"
SMALL_LEDGER = FIXTURES / "readers" / "ledger_bom.csv"
SAMPLE_BREAKS = ("FI-facc450f1682", "TO-417d669f6dd1", "HC-b04c76a19ed4", "FI-64e07c883fff",
                 "HC-d72689858228")
COUNT = re.compile(r"^(\d+) breaks?: (\d+) from the controls, (\d+) from reconcile\.$")


def run(*args, temp):
    """main.py in a child process, its temporary files in temp."""
    return subprocess.run([sys.executable, str(MAIN), *map(str, args)], capture_output=True,
                          text=True, encoding="utf-8", cwd=str(ROOT), timeout=180,
                          env=subprocess_env(TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp)))


def break_rows(lines) -> list:
    return [l for l in lines if l[:3] in ("FI-", "TO-", "HC-", "ST-", "CS-", "RC-")]


@pytest.fixture(scope="module")
def lines(built_warehouse):
    """What check prints for the committed sample and ledger, from the session's build."""
    import main
    return main.report_lines(built_warehouse)


@pytest.fixture(scope="module")
def checked(tmp_path_factory):
    """The command run once in a child process, on the small base and a one-line ledger."""
    temp = tmp_path_factory.mktemp("check_temp")
    return run("check", SMALL_BOOK, SMALL_LEDGER, temp=temp), temp


class TestWhatItPrintsForTheSample:
    """The report for the committed sample and ledger, from the session's start-up build."""

    def test_each_batch_record(self, lines):
        sample = extraction(ROOT / "data" / "planning_model.xlsx").batch
        for line in ("Workbook: planning_model.xlsx",
                     f"  batch id: {sample.batch_id}",
                     f"  SHA-256: {sample.file_sha256}",
                     '  sheets: "Control" (visible), "Assumptions" (visible), "Plan_ES" '
                     '(visible), "Plan_AT" (visible), "Plan_DE" (visible), "Lists" (visible), '
                     '"Scratch" (hidden)',
                     f"  application: {json.dumps(sample.application)}",
                     "  calculation: recalculated by the sample build",
                     "  external links: none",
                     "Ledger: erp_trial_balance.csv",
                     "  lines: 162"):
            assert line in lines, line

    def test_the_breaks_as_a_table_and_their_count(self, lines):
        header = next(l for l in lines if l.startswith("id "))
        assert header.split() == ["id", "control", "kind", "severity", "where", "message"]
        rows = break_rows(lines)
        assert len(rows) == 8
        for break_id in SAMPLE_BREAKS:
            assert any(r.startswith(break_id) for r in rows), break_id
        assert sum(r.startswith("RC-") for r in rows) == 3
        assert "8 breaks: 5 from the controls, 3 from reconcile." in lines
        assert "Nothing was promoted: the check never promotes." in lines


class TestCheck:
    """The command itself, in a child process, on the small base and a one-line ledger."""

    def test_it_runs_and_exits_zero(self, checked):
        done, _ = checked
        assert done.returncode == 0, done.stderr
        assert done.stderr == ""

    def test_it_prints_both_batch_records(self, checked):
        lines = checked[0].stdout.splitlines()
        assert "Workbook: extra_tab.xlsx" in lines and "Ledger: ledger_bom.csv" in lines
        assert "  lines: 1" in lines

    def test_it_prints_the_breaks_as_a_table_and_counts_them(self, checked):
        lines = checked[0].stdout.splitlines()
        header = next(l for l in lines if l.startswith("id "))
        assert header.split() == ["id", "control", "kind", "severity", "where", "message"]
        (count,) = [COUNT.match(l) for l in lines if COUNT.match(l)]
        total, controls, reconciled = map(int, count.groups())
        rows = break_rows(lines)
        assert len(rows) == total == controls + reconciled
        assert controls == 1 and any(r.startswith("ST-") for r in rows)      # the Notes tab
        assert reconciled == sum(r.startswith("RC-") for r in rows) > 0
        assert lines[-1] == "Nothing was promoted: the check never promotes."

    def test_the_temporary_warehouse_is_deleted(self, checked):
        _, temp = checked
        assert list(temp.iterdir()) == []

    def test_it_never_promotes(self):
        text = MAIN.read_text(encoding="utf-8")
        assert "promote(" not in text and "acknowledge(" not in text


class TestFilesItCannotRead:
    def test_a_missing_file_is_one_plain_line(self, tmp_path):
        done = run("check", tmp_path / "absent.xlsx", "data/erp_trial_balance.csv",
                   temp=tmp_path)
        assert done.returncode == 1
        assert (done.stdout + done.stderr).strip().splitlines() == [
            "check: absent.xlsx: there is no such file."]

    def test_an_unreadable_workbook_is_one_plain_line(self, tmp_path):
        bad = tmp_path / "bad.xlsx"
        bad.write_text("not a workbook", encoding="utf-8")
        done = run("check", bad, "data/erp_trial_balance.csv", temp=tmp_path)
        assert done.returncode == 1
        said = (done.stdout + done.stderr).strip().splitlines()
        assert len(said) == 1 and said[0].startswith("check: bad.xlsx: ")
        assert "Traceback" not in done.stderr

    def test_an_unreadable_ledger_is_one_plain_line(self, tmp_path):
        bad = tmp_path / "ledger.csv"
        bad.write_text("company,month,code,value\nES,2026-01,4000,1\n", encoding="utf-8")
        done = run("check", SMALL_BOOK, bad, temp=tmp_path)
        assert done.returncode == 1
        assert (done.stdout + done.stderr).strip().splitlines() == [
            "check: ledger.csv: the first line must be exactly entity,period,account,amount"]
        assert [p.name for p in tmp_path.iterdir()] == ["ledger.csv"]


class TestTheCommandLine:
    def test_no_arguments_prints_the_usage(self, capsys):
        import main
        assert main.main([]) == 0
        out = capsys.readouterr().out
        assert "python main.py check <workbook> <ledger>" in out
        assert "Nothing answers a question yet." in out

    def test_an_unknown_command_is_a_usage_error(self, capsys):
        import main
        assert main.main(["answer"]) == 2
        assert "python main.py check <workbook> <ledger>" in capsys.readouterr().out

    def test_check_needs_two_files(self, capsys):
        import main
        assert main.main(["check", "data/planning_model.xlsx"]) == 2

    def test_external_link_targets_are_shown_as_data(self):
        import main
        batch = extraction(FIXTURES / "external_link.xlsx").batch
        lines = main.batch_summary(batch)
        assert '  external links: ["calder_plan_fy2025.xlsx"]' in lines


# -- Part 1e (item 7a): a bad file in plain words ----------------------------------------------
# Nothing a person reads names a Python class: a list of the names that could
# leak, and a pattern for any CamelCase word ending in Error, Exception or File.

KNOWN_CLASSES = ("BadZipFile", "BadZipfile", "LargeZipFile", "OSError", "IOError",
                 "PermissionError", "FileNotFoundError", "IsADirectoryError", "ValueError",
                 "KeyError", "TypeError", "IndexError", "AttributeError", "UnicodeDecodeError",
                 "ParseError", "SourceError", "WarehouseError", "InvalidFileException",
                 "Traceback", "zipfile", "openpyxl", "duckdb", "Exception")
CLASS_NAME = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:Error|Exception|File)\b")
NOT_A_WORKBOOK = "not a workbook that can be read: it is not an .xlsx file, or the file is " \
                 "damaged or cut short"
PLAIN = {
    "text.xlsx": f"check: text.xlsx: {NOT_A_WORKBOOK}",
    "empty.xlsx": "check: empty.xlsx: not a workbook that can be read: the file is empty",
    "truncated.xlsx": f"check: truncated.xlsx: {NOT_A_WORKBOOK}",
    "doctype.xlsx": "check: doctype.xlsx: the part xl/workbook.xml declares a document type, "
                    "which a workbook never needs",
}


def no_class_name(text: str) -> bool:
    return not any(name in text for name in KNOWN_CLASSES) and not CLASS_NAME.search(text)


def bad_file(folder, name: str):
    """One of the four bad files: a text file, an empty file, a cut workbook, a DOCTYPE."""
    import zipfile
    path = folder / name
    if name == "text.xlsx":
        path.write_text("Prices for the second half are under review.\n", encoding="utf-8")
    elif name == "empty.xlsx":
        path.write_bytes(b"")
    elif name == "truncated.xlsx":
        data = SMALL_BOOK.read_bytes()
        path.write_bytes(data[:len(data) // 2])
    else:
        with zipfile.ZipFile(SMALL_BOOK) as old, zipfile.ZipFile(path, "w") as new:
            for info in old.infolist():
                data = old.read(info.filename)
                if info.filename == "xl/workbook.xml":
                    data = data.replace(b"?>", b'?><!DOCTYPE workbook [<!ENTITY x "y">]>', 1)
                new.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
    return path


class TestABadFileInPlainWords:
    def test_the_pattern_finds_a_class_name(self):
        for text in ("check: x.xlsx: not a workbook (BadZipFile)", "OSError", "a SourceError",
                     "an InvalidFileException"):
            assert not no_class_name(text), text
        assert no_class_name("check: a.xlsx: the file is empty. File names are kept.")

    @pytest.mark.parametrize("name", list(PLAIN))
    def test_check_says_it_in_plain_words(self, tmp_path, name):
        path = bad_file(tmp_path, name)
        done = run("check", path, "data/erp_trial_balance.csv", temp=tmp_path)
        said = (done.stdout + done.stderr).strip().splitlines()
        assert done.returncode == 1
        assert said == [PLAIN[name]]
        assert no_class_name(done.stdout + done.stderr)

    def test_a_damaged_part_is_told_in_plain_words(self, tmp_path):
        import zipfile

        from src.batch import SourceError
        from src.extract import extract_workbook
        path = tmp_path / "damaged.xlsx"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr("xl/workbook.xml", b"<workbook>" + b"x" * 200 + b"</workbook>")
        data = bytearray(path.read_bytes())
        at = data.index(b"xxxx")
        data[at] = ord("y")
        path.write_bytes(bytes(data))
        with pytest.raises(SourceError) as refused:
            extract_workbook(path)
        assert str(refused.value) == ("damaged.xlsx: the part xl/workbook.xml is damaged and "
                                      "cannot be read")

    def test_contents_that_are_not_a_workbook_are_told_in_plain_words(self, tmp_path):
        import zipfile

        from src.batch import SourceError
        from src.extract import extract_workbook
        path = tmp_path / "parts.xlsx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", b"<Types/>")
        with pytest.raises(SourceError) as refused:
            extract_workbook(path)
        assert str(refused.value) == ("parts.xlsx: not a workbook that can be read: its contents "
                                      "could not be read as a workbook")

    def test_a_file_that_cannot_be_opened_is_told_in_plain_words(self, monkeypatch, capsys):
        import main
        from src import startup

        def refuse(*args, **kwargs):
            raise PermissionError(13, "Access is denied")
        monkeypatch.setattr(startup, "build_warehouse", refuse)
        assert main.check(SMALL_BOOK, SMALL_LEDGER) == 1
        out = capsys.readouterr().out
        assert out.strip().splitlines() == [
            "check: the check could not finish: a file could not be read or written."]
        assert no_class_name(out)
