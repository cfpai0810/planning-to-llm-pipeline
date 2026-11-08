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
        # Part 2e: query may promote, as the person named; check never does.
        import inspect

        import main
        text = inspect.getsource(main.check) + inspect.getsource(main.report_lines)
        assert "promote(" not in text and "acknowledge" not in text


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
        # Part 2e: a question can be answered from a specification; no model is called.
        assert "No model is called yet." in out and "Nothing answers a question yet." not in out

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


# -- Part 2e: python main.py query ------------------------------------------------------------------
# The command builds the template in a temporary folder, opens a session, may acknowledge every
# break and promote the pair as named people, asks the specification and prints the outcome
# sentence or the answer, and removes the folder, the read-only template file included (Trap
# 11). Every refusal is one plain line with a non-zero exit, and nothing names a Python class.

QUESTION = {"metric": "revenue", "version": "Budget", "dimensions": [], "filters": [],
            "period": {"kind": "fy", "value": "FY2026"}, "comparison": "none", "sort": None,
            "limit": 10}
AT_QUESTION = {"metric": "ebit", "version": "Actual", "dimensions": ["entity"], "filters": [],
               "period": {"kind": "ytd", "value": "2026-06"}, "comparison": "budget",
               "sort": None, "limit": 10}


def spec_file(folder, data=QUESTION, name="question.json"):
    path = folder / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def said(done) -> list:
    return (done.stdout + done.stderr).strip().splitlines()


class TestQueryRefusesInOnePlainLine:
    def test_a_missing_spec_file(self, tmp_path):
        done = run("query", tmp_path / "absent.json", "--pair", "clean", "--role",
                   "group_controller", temp=tmp_path)
        assert done.returncode == 1
        assert said(done) == ["query: absent.json: there is no such file."]

    @pytest.mark.parametrize("text", ["{not json", "[1, 2]", json.dumps({**QUESTION, "x": 1}),
                                      json.dumps({k: v for k, v in QUESTION.items()
                                                  if k != "limit"})])
    def test_a_malformed_spec_file(self, tmp_path, text):
        bad = tmp_path / "bad.json"
        bad.write_text(text, encoding="utf-8")
        done = run("query", bad, "--pair", "clean", "--role", "group_controller", temp=tmp_path)
        assert done.returncode == 1
        assert said(done) == ["query: bad.json: The question could not be read: it is not a "
                              "specification of the expected form."]
        assert no_class_name(done.stdout + done.stderr)

    def test_a_spec_file_that_is_not_text(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_bytes(b"\xff\xfe\x00{")
        done = run("query", bad, "--pair", "clean", "--role", "group_controller", temp=tmp_path)
        assert done.returncode == 1 and len(said(done)) == 1
        assert "Traceback" not in done.stderr

    def test_an_unknown_pair(self, tmp_path):
        done = run("query", spec_file(tmp_path), "--pair", "other", "--role",
                   "group_controller", temp=tmp_path)
        assert done.returncode == 2
        assert said(done) == ["query: the pair must be clean or sample."]

    def test_an_unknown_role(self, tmp_path):
        done = run("query", spec_file(tmp_path), "--pair", "clean", "--role", "auditor",
                   temp=tmp_path)
        assert done.returncode == 2
        assert said(done) == ["query: the role must be group_controller or entity_controller."]

    def test_an_entity_controller_with_no_entity(self, tmp_path):
        done = run("query", spec_file(tmp_path), "--pair", "clean", "--role",
                   "entity_controller", temp=tmp_path)
        assert done.returncode == 2
        assert said(done) == ["query: an entity controller needs --entity."]

    def test_promote_as_on_the_sample_without_acknowledge_all_as(self, tmp_path):
        done = run("query", spec_file(tmp_path), "--pair", "sample", "--role",
                   "group_controller", "--promote-as", "P. Promoter", temp=tmp_path)
        assert done.returncode == 1
        assert said(done) == ["query: the sample pair has 8 breaks, so --promote-as needs "
                              "--acknowledge-all-as as well; nothing was promoted."]
        assert [p.name for p in tmp_path.iterdir()] == ["question.json"]

    def test_with_nothing_promoted_it_says_so(self, tmp_path):
        done = run("query", spec_file(tmp_path), "--pair", "clean", "--role",
                   "group_controller", temp=tmp_path)
        assert done.returncode == 1
        assert said(done) == ["query: Nothing has been promoted yet. Review a batch first."]

    def test_a_refused_question_is_its_fixed_sentence_and_names_nothing(self, tmp_path):
        other = {**QUESTION, "filters": [{"dimension": "entity", "values": ["DE"]}]}
        done = run("query", spec_file(tmp_path, other), "--pair", "clean", "--role",
                   "entity_controller", "--entity", "AT", "--promote-as", "P. Promoter",
                   temp=tmp_path)
        assert done.returncode == 1
        assert said(done) == ["query: That entity is not available to your role."]

    def test_an_unknown_option_is_a_usage_error(self, tmp_path):
        done = run("query", spec_file(tmp_path), "--pair", "clean", "--role",
                   "group_controller", "--verbose", temp=tmp_path)
        assert done.returncode == 2 and "Traceback" not in done.stderr
        assert said(done)[0].startswith("query: ")


@pytest.fixture(scope="module")
def group_run(tmp_path_factory):
    temp = tmp_path_factory.mktemp("query_group")
    done = run("query", spec_file(temp), "--pair", "clean", "--role", "group_controller",
               "--promote-as", "P. Promoter", temp=temp)
    return done, temp


@pytest.fixture(scope="module")
def entity_run(tmp_path_factory):
    temp = tmp_path_factory.mktemp("query_entity")
    done = run("query", spec_file(temp, AT_QUESTION), "--pair", "sample", "--role",
               "entity_controller", "--entity", "AT", "--acknowledge-all-as", "A. Reviewer",
               "--promote-as", "P. Promoter", temp=temp)
    return done, temp


class TestQueryAnswers:
    def test_a_group_controller_on_the_clean_pair(self, group_run):
        done, _ = group_run
        assert done.returncode == 0, done.stderr
        lines = done.stdout.splitlines()
        assert "Question: revenue, Budget, FY2026, no comparison; group view" in lines
        assert any(l.startswith("SQL (SHA-256 ") for l in lines)
        assert any(l.startswith("WITH plan AS (SELECT version, entity, account, period, "
                                "amount FROM fact_plan WHERE entity IN ('ES', 'AT', 'DE')")
                   for l in lines)
        # The oracle's FY2026 Budget revenue for the clean pair: 65,899,660.
        assert "  value 65,899,660 EUR" in lines
        assert "Months asked: 2026-01..2026-12; months held: 2026-01..2026-12" in lines
        assert "Gaps: none" in lines
        assert "  plan: planning_model_clean.xlsx" in "\n".join(lines)
        assert "  promotion 1, promoted by P. Promoter at " in "\n".join(lines)
        assert done.stderr == ""

    def test_an_entity_controller_on_the_sample_pair(self, entity_run):
        done, _ = entity_run
        assert done.returncode == 0, done.stderr
        text = done.stdout
        lines = text.splitlines()
        assert "Question: ebit, Actual, YTD 2026-06, against Budget; entity view: AT" in lines
        # The oracle's AT figures: 370,759 against 335,679, a variance of 35,080 (10.5%),
        # favourable; AT's May Actual is a gap and left out of both sides.
        assert ("  entity AT: value 370,759 EUR; comparator 335,679 EUR; variance 35,080 EUR; "
                "variance % 10.5%; favourable") in lines
        assert "Months asked: 2026-01..2026-06; months held: 2026-01..2026-04 2026-06; " \
               "compared with Budget" in lines
        assert "Gaps: AT 2026-05" in lines
        for other in ("'ES'", "'DE'", "Calder Foods Spain", "Calder Foods Germany",
                      "Southern Europe"):
            assert other not in text, other

    def test_nothing_is_left_behind(self, group_run, entity_run):
        for done, temp in (group_run, entity_run):
            assert sorted(p.name for p in temp.iterdir()) == ["question.json"], done.stdout

    def test_the_temporary_folder_is_removed_though_the_template_is_read_only(self, tmp_path):
        import os
        import stat

        import main
        folder = tmp_path / "work"
        (folder / "inner").mkdir(parents=True)
        locked = folder / "inner" / "template.duckdb"
        locked.write_bytes(b"x")
        os.chmod(locked, stat.S_IREAD)
        main.remove_folder(folder)
        assert not folder.exists()

    def test_removing_the_folder_only_ever_adds_the_write_bit(self, tmp_path, monkeypatch):
        # Part 2e (fix): remove_folder once set every path, directories included, to 0o600,
        # which on Linux takes a directory's search bit and stops the removal. It may now only
        # add the write bit to the mode a path already has, and never leave a directory
        # without its search bit. On Windows the read-only template file needs the write bit
        # (the attribute) back before it can be deleted, so the change is seen there too.
        import os
        import stat
        from pathlib import Path

        import main
        folder = tmp_path / "work"
        (folder / "inner" / "deeper").mkdir(parents=True)
        locked = folder / "inner" / "template.duckdb"
        locked.write_bytes(b"x")
        (folder / "inner" / "deeper" / "note.txt").write_text("x", encoding="utf-8")
        os.chmod(locked, stat.S_IREAD)
        before = {p: os.stat(p).st_mode for p in [folder, *folder.rglob("*")]}
        calls = []
        real = os.chmod

        def spy(path, mode, *args, **kwargs):
            calls.append((Path(path), mode))
            return real(path, mode, *args, **kwargs)
        monkeypatch.setattr(os, "chmod", spy)
        main.remove_folder(folder)
        assert not folder.exists()
        for path, mode in calls:
            old = stat.S_IMODE(before[path])
            assert mode & old == old and mode & stat.S_IWRITE, (path, oct(old), oct(mode))
            assert not stat.S_ISDIR(before[path]) or mode & stat.S_IXUSR, path

    def test_the_usage_names_the_query_command(self, capsys):
        import main
        assert main.main([]) == 0
        out = capsys.readouterr().out
        assert ("python main.py query <spec.json> --pair clean|sample --role "
                "group_controller|entity_controller") in out


class TestFiguresAreRoundedOnceWhereShown:
    """src/answer.py's shown(): an exact figure as a person reads it, by its unit (rule 8)."""

    @pytest.mark.parametrize("text, unit, expected", [
        ("65899660.000000000000", "EUR", "65,899,660 EUR"),
        ("-1234.500000000000", "EUR", "-1,235 EUR"),
        ("0.320000000000", "%", "32.0%"),
        ("0.123450000000", "%", "12.3%"),
        ("0.123500000000", "%", "12.4%"),
        ("-0.123500000000", "%", "-12.4%"),
        ("0.699727239000", "pp", "0.7 pp"),
        ("-1.250000000000", "pp", "-1.3 pp"),
        ("3.455000000000", "EUR per unit", "3.46 EUR per unit"),
        ("201.500000000000", "FTE", "201.5 FTE"),
        ("1000.000000000000", "units", "1,000 units"),
        ("not meaningful", "%", "not meaningful"),
    ])
    def test_each_unit(self, text, unit, expected):
        from src import answer
        assert answer.shown(text, unit) == expected
