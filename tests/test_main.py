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

Part 3b (the reviewer's Phase 3 brief, 4.8): python main.py schema --out
<folder> writes the schema and the system texts, with a report, and calls
nothing.

Part 3e (the reviewer's Phase 3 brief, section 7, and BRIEF_3E): python main.py ask reads the
key first and asks a question in words; python main.py replay asks a recorded specification
again. In process, the client seam (main.client_for) is the recording fake and the template
build (main.build_template) a copy of the suite's template; in a child process, the real client
seam talks to the loopback stand-in with the key text "stand-in". No test reaches the network or
holds a real key.
"""

import json
import re
import subprocess
import sys

import pytest

from tests import fakes
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
        # Changed on purpose in 3e: the closing lines say what Phase 3 made, a question in words
        # with your own key, so the line that no model is called is gone.
        assert "No model is called yet." not in out and "Nothing answers a question yet." not in out
        assert "Phase 3: a question in words on the command line, with your own key." in out

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


# -- Part 3b: python main.py schema (the reviewer's Phase 3 brief, 4.8) ----------------------------
# The command builds the template as query does, acknowledges the sample pair's breaks and
# promotes it as "Schema dump", and writes the schema and the system texts for the owner's live
# check and the reviewer: schema.json (the canonical bytes that schema_sha256 hashes), one
# system text for the group controller and one for each of the sample's three entities, and
# schema_report.txt. It reads no key and calls nothing: it is run here with the model call and
# every way of making a client replaced by functions that stop the process.

SCHEMA_FILES = ["schema.json", "schema_report.txt", "system_entity_controller_AT.txt",
                "system_entity_controller_DE.txt", "system_entity_controller_ES.txt",
                "system_group_controller.txt"]
NO_MODEL = ("import runpy, sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "import anthropic\n"
            "from src import model_call\n"
            "from streamlit_app.lib import claude_client\n"
            "def refuse(*args, **kwargs):\n"
            "    raise SystemExit('a model call or a client was attempted')\n"
            "model_call.call_model = refuse\n"
            "claude_client.build_client = refuse\n"
            "anthropic.Anthropic.__init__ = refuse\n"
            "sys.argv = sys.argv[2:]\n"
            "runpy.run_path(sys.argv[0], run_name='__main__')\n")


@pytest.fixture(scope="module")
def schema_run(tmp_path_factory):
    temp = tmp_path_factory.mktemp("schema_temp")
    out = tmp_path_factory.mktemp("schema_out") / "dump"
    done = subprocess.run([sys.executable, "-c", NO_MODEL, str(ROOT), str(MAIN), "schema",
                           "--out", str(out)], capture_output=True, text=True,
                          encoding="utf-8", cwd=str(ROOT), timeout=300,
                          env=subprocess_env(TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp)))
    return done, temp, out


@pytest.fixture(scope="module")
def dumped(template, tmp_path_factory):
    """The sample pair promoted in a session of the test's own, as the command promotes it."""
    from src import session
    pool = session.Sessions(template, root=tmp_path_factory.mktemp("schema_session"))
    s = pool.open()
    s.acknowledge_many("sample", [(b.break_id, "Schema dump", "accepted")
                                  for b in s.breaks("sample")])
    s.promote("sample", "Schema dump")
    yield s
    pool.close_all()


class TestSchema:
    def test_it_writes_the_six_files_and_calls_nothing(self, schema_run):
        done, _, out = schema_run
        assert done.returncode == 0, done.stdout + done.stderr
        assert done.stderr == "" and "attempted" not in done.stdout
        assert sorted(p.name for p in out.iterdir()) == SCHEMA_FILES

    def test_nothing_is_left_in_the_temporary_folder(self, schema_run):
        _, temp, _ = schema_run
        assert list(temp.iterdir()) == []

    def test_schema_json_is_the_canonical_schema(self, schema_run, dumped):
        from src import prompt
        _, _, out = schema_run
        shape = prompt.schema(dumped.registry(), dumped.dimensions().versions)
        data = (out / "schema.json").read_bytes()
        assert data == prompt.canonical(shape).encode("utf-8") and not data.endswith(b"\n")
        assert prompt.walk_problems(json.loads(data)) == []

    def test_each_system_text_is_the_one_ask_sends(self, schema_run, dumped):
        from src import prompt, roles
        _, _, out = schema_run
        for name, role in (("group_controller", roles.group_controller),
                           ("entity_controller_ES", roles.entity_controller("ES")),
                           ("entity_controller_AT", roles.entity_controller("AT")),
                           ("entity_controller_DE", roles.entity_controller("DE"))):
            made = prompt.build(dumped.registry(), roles.members_for(role, dumped),
                                dumped.dimensions().versions, "Any question")
            assert (out / f"system_{name}.txt").read_bytes() == made.system.encode("utf-8")

    def test_the_report(self, schema_run):
        import hashlib

        from src import prompt
        _, _, out = schema_run
        text = (out / "schema_report.txt").read_text(encoding="utf-8")
        schema_bytes = (out / "schema.json").read_bytes()
        described = prompt.describe(json.loads(schema_bytes))
        assert "the sample pair, its breaks acknowledged and promoted as Schema dump" in text
        assert f"size: {len(schema_bytes)} bytes" in text
        assert "properties: 18" in text
        assert "nullable fields: 3 (refusal, specification, specification.sort); at most 16" \
            in text
        assert ("deepest nesting: 5 levels in a reply (the reply, specification, filters, a "
                "filter, its values)") in text
        assert f"{described['json_depth']} levels of JSON in schema.json" in text
        for path, values in described["enums"]:
            assert f"  {path}: {len(values)}: {', '.join(values)}" in text, path
        assert f"schema_sha256: {hashlib.sha256(schema_bytes).hexdigest()}" in text
        for name in SCHEMA_FILES:
            if name.startswith("system_"):
                digest = hashlib.sha256((out / name).read_bytes()).hexdigest()
                assert f"  {name}: {digest}" in text, name
        assert text.endswith("\n") and b"\r" not in (out / "schema_report.txt").read_bytes()

    def test_it_says_where_each_file_went(self, schema_run):
        done, _, out = schema_run
        lines = done.stdout.splitlines()
        assert lines[0] == f"schema: wrote 6 files to {out}"
        assert lines[-1] == "No key was read and no model was called."

    def test_the_usage_names_the_schema_command(self, capsys):
        import main
        assert main.main([]) == 0
        assert "python main.py schema --out <folder>" in capsys.readouterr().out

    @pytest.mark.parametrize("argv", [["schema"], ["schema", "--out"],
                                      ["schema", "--folder", "x"],
                                      ["schema", "--out", "x", "--out", "y"]])
    def test_a_wrong_option_is_one_plain_line(self, capsys, argv):
        import main
        assert main.main(argv) == 2
        out = capsys.readouterr().out.strip().splitlines()
        assert out == ["schema: give --out and a folder; run python main.py for the usage."]

    def test_an_out_that_is_a_file_is_refused_before_anything_is_built(self, tmp_path, capsys,
                                                                      monkeypatch):
        import main
        from src import startup
        monkeypatch.setattr(startup, "build_template", lambda *a, **k: pytest.fail("built"))
        taken = tmp_path / "taken.txt"
        taken.write_text("x", encoding="utf-8")
        assert main.main(["schema", "--out", str(taken)]) == 1
        assert capsys.readouterr().out.strip().splitlines() == [
            "schema: taken.txt: that is a file, not a folder."]


# -- Part 3e: python main.py ask and replay -----------------------------------------------------
# ask reads the key first, builds the template in a temporary folder of its own, asks through
# src/ask.py with the client from main.client_for and an audit.AuditLog, prints the result and
# removes the folder. replay reads an audit file, verifies it, replays one line's specification
# on a fresh template and prints the state. In process, main.client_for is replaced by the
# recording fake and main.build_template by a copy of the suite's template; the child-process
# tests use the real client seam against the loopback stand-in, with the key text "stand-in".

STAND_IN = "stand-in"
# A made-up key text anyone would recognise; the recording fake never sends it anywhere.
KEY_TEXT = "stand-in-3e-KEY-TEXT-r4Wq9Lm"
BY_ENTITY = {"metric": "revenue", "version": "Actual", "dimensions": ["entity"], "filters": [],
             "period": {"kind": "ytd", "value": "2026-06"}, "comparison": "budget",
             "sort": None, "limit": 100}
ASKED = "How did revenue compare with budget by entity?"
WORDS = "Actual revenue for Calder Foods Austria was 3,983,040 EUR."
AT_WORDS = "Actual revenue for Calder Foods Austria was 3,983,040 EUR."


def model_reply(specification=BY_ENTITY, refusal=None) -> str:
    return json.dumps({"refusal": refusal, "specification": specification,
                       "asks_for_a_cause": False})


@pytest.fixture
def made(template, monkeypatch, tmp_path):
    """The command's template build replaced by a copy of the suite's template (the session
    copies it anyway), and every temporary folder the command makes for itself (p7_ask_ or
    p7_replay_) recorded; they are made in the test's own folder, never in the system's."""
    import shutil
    import tempfile
    from pathlib import Path

    import main
    from src.startup import Template

    def copied(folder):
        (folder / "template").mkdir(parents=True)
        path = folder / "template" / template.path.name
        shutil.copyfile(template.path, path)
        return Template(path, template.pairs, template.breaks, template.steps)
    monkeypatch.setattr(main, "build_template", copied)
    folders = []
    real = tempfile.mkdtemp
    (tmp_path / "temp").mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "temp"))

    def recording(*args, **kwargs):
        folder = real(*args, **kwargs)
        if str(kwargs.get("prefix", "")).startswith(("p7_ask_", "p7_replay_")):
            folders.append(Path(folder))
        return folder
    monkeypatch.setattr(tempfile, "mkdtemp", recording)
    return folders


def asking(capsys, monkeypatch, audit_folder, *argv, replies=None, client=None, key=STAND_IN,
           question=ASKED, role=("--role", "group_controller"), pair="sample"):
    """main.main(["ask", ...]) in process: (exit code, stdout, stderr, the client)."""
    import main
    fake = client if client is not None else fakes.FakeClient(
        replies=list(replies if replies is not None else [fakes.Reply(model_reply())]))
    monkeypatch.setattr(main, "client_for", lambda k: fake)
    if key is None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    else:
        monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    promote = (("--acknowledge-all-as", "A. Reviewer", "--promote-as", "P. Promoter")
               if pair == "sample" else ("--promote-as", "P. Promoter"))
    code = main.main(["ask", question, "--pair", pair, *role, *promote, *argv,
                      "--audit", str(audit_folder)])
    out = capsys.readouterr()
    return code, out.out, out.err, fake


class TestAskTheKeyFirst:
    @pytest.mark.parametrize("key", [None, "", "  \t "], ids=["unset", "empty", "white space"])
    def test_no_key_is_one_plain_line_and_nothing_is_built_or_sent(self, capsys, monkeypatch,
                                                                    tmp_path, made, key):
        import main

        def never(k):
            raise AssertionError("a client was made with no key")
        monkeypatch.setattr(main, "client_for", never)
        if key is None:
            monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        else:
            monkeypatch.setenv("ANTHROPIC_API_KEY", key)
        code = main.main(["ask", ASKED, "--pair", "sample", "--role", "group_controller",
                          "--audit", str(tmp_path / "audit")])
        out = capsys.readouterr()
        assert (code, out.out, out.err) == (1, main.NO_KEY + "\n", "")
        assert made == [] and not (tmp_path / "audit").exists()

    def test_a_key_is_not_judged_by_its_look(self, capsys, monkeypatch, tmp_path, made):
        # "stand-in" looks like no real key: only the API can say whether a key works.
        code, out, _, fake = asking(capsys, monkeypatch, tmp_path / "audit")
        assert code == 0 and len(fake.calls) == 1


class TestAskAnswers:
    def test_an_answer_with_the_template(self, capsys, monkeypatch, tmp_path, made):
        from src import audit
        code, out, err, fake = asking(capsys, monkeypatch, tmp_path / "audit")
        assert (code, err) == (0, "")
        lines = out.splitlines()
        assert lines[0] == f"Question asked: {json.dumps(ASKED)}"
        assert lines[1] == ("Specification: " + json.dumps(BY_ENTITY, sort_keys=True,
                                                           separators=(",", ":")))
        order = [next(i for i, l in enumerate(lines) if l.startswith(start)) for start in (
            "Question asked: ", "Specification: ", "SQL (SHA-256 ", "Figures (3 rows):",
            "Provenance:", "Narration, the template:", "Call: specification", "Run id: ",
            "Audit line: ")]
        assert order == sorted(order)
        assert ("  entity AT: value 3,983,040 EUR; comparator 3,864,048 EUR; variance "
                "118,992 EUR; variance % 3.1%; favourable") in lines
        import config
        assert (f"Call: specification, ok, served by {config.MODEL}, stop reason end_turn, "
                "tokens 12 in and 3 out") in lines
        assert "req_fake" not in out
        (record,) = audit.read(tmp_path / "audit" / "audit.jsonl")
        assert lines[-2] == f"Run id: {record['run_id']}"
        assert lines[-1] == f"Audit line: {(tmp_path / 'audit' / 'audit.jsonl').resolve()}"
        assert len(fake.calls) == 1
        assert [f.name.startswith("p7_ask_") and not f.exists() for f in made] == [True]

    def test_an_answer_with_the_models_wording(self, capsys, monkeypatch, tmp_path, made):
        code, out, _, fake = asking(capsys, monkeypatch, tmp_path / "audit", "--narrate",
                                    "model", replies=[fakes.Reply(model_reply()),
                                                      fakes.Reply(WORDS)])
        lines = out.splitlines()
        assert code == 0 and len(fake.calls) == 2
        assert lines[lines.index("Narration, the model's wording:") + 1] == "  " + WORDS
        assert [l.split(",")[0] for l in lines if l.startswith("Call: ")] == [
            "Call: specification", "Call: narration"]

    def test_withheld_wording_prints_the_flags_and_never_the_models_text(self, capsys,
                                                                       monkeypatch, tmp_path,
                                                                       made):
        from src import audit
        wrong = "Actual revenue for Calder Foods Austria was 1,234 EUR. Zebra crossing."
        code, out, _, _ = asking(capsys, monkeypatch, tmp_path / "audit", "--narrate", "model",
                                 replies=[fakes.Reply(model_reply()), fakes.Reply(wrong)])
        assert code == 0
        assert "Narration, the template:" in out and "Zebra" not in out
        assert ("  numeric: 1,234 EUR: '1,234 EUR' matches no figure for Actual revenue for "
                "Calder Foods Austria.") in out.splitlines()
        (record,) = audit.read(tmp_path / "audit" / "audit.jsonl")
        assert record["narration"]["model_text"] == wrong

    def test_the_answer_is_printed_by_querys_own_function(self, capsys, monkeypatch, tmp_path,
                                                          made):
        import main
        monkeypatch.setattr(main, "answer_lines", lambda result, role: ["ANSWER LINES"])
        code, out, _, _ = asking(capsys, monkeypatch, tmp_path / "audit")
        assert code == 0 and "ANSWER LINES" in out.splitlines()


class TestAskOutcomesAndFailures:
    def test_a_refusal_is_its_sentence_with_no_sql(self, capsys, monkeypatch, tmp_path, made):
        from src import outcomes
        code, out, _, fake = asking(capsys, monkeypatch, tmp_path / "audit", replies=[
            fakes.Reply(model_reply(None, {"reason": "out_of_scope"}))])
        assert code == 1 and len(fake.calls) == 1
        assert f"Outcome: {outcomes.outcome('out_of_scope').sentence}" in out.splitlines()
        assert "SQL" not in out and "Specification" not in out
        assert [f.exists() for f in made] == [False]

    def test_a_validation_refusal(self, capsys, monkeypatch, tmp_path, made):
        from src import outcomes
        asked_for_spain = {**BY_ENTITY, "filters": [{"dimension": "entity", "values": ["ES"]}]}
        code, out, _, _ = asking(capsys, monkeypatch, tmp_path / "audit", replies=[
            fakes.Reply(model_reply(asked_for_spain))],
            role=("--role", "entity_controller", "--entity", "AT"))
        assert code == 1
        assert f"Outcome: {outcomes.outcome('entity_not_permitted').sentence}" in out
        assert "Spain" not in out.replace(json.dumps(ASKED), "")

    @pytest.mark.parametrize("question", ["", "Revenue \x1b[31m in June?", "x" * 501],
                             ids=["empty", "an escape sequence", "501 characters"])
    def test_a_question_that_fails_the_checks_is_never_sent(self, capsys, monkeypatch,
                                                             tmp_path, made, question):
        from src import outcomes
        code, out, _, fake = asking(capsys, monkeypatch, tmp_path / "audit", question=question)
        assert code == 1 and fake.calls == []
        assert out.splitlines()[0] == f"Question asked: {json.dumps(question)}"
        assert "\x1b" not in out
        assert f"Outcome: {outcomes.QUESTION_INVALID.sentence}" in out

    def test_an_api_failure_is_its_line(self, capsys, monkeypatch, tmp_path, made):
        from src import errors
        error = fakes.status_error(fakes.anthropic.AuthenticationError, 401)
        code, out, _, fake = asking(capsys, monkeypatch, tmp_path / "audit", replies=[error])
        lines = out.splitlines()
        assert code == 1 and len(fake.calls) == 1
        assert f"Failure: {errors.line('key_rejected')}" in lines
        assert any(l.startswith("Call: specification, key_rejected, served by none") for l in
                   lines)
        assert [f.exists() for f in made] == [False]

    def test_a_fault_is_one_plain_line_and_the_folder_still_goes(self, capsys, monkeypatch,
                                                                 tmp_path, made):
        code, out, err, _ = asking(capsys, monkeypatch, tmp_path / "audit", replies=[
            RuntimeError("a fault in this code")])
        assert (code, out, err) == (1, "ask: the question could not be answered.\n", "")
        assert [f.exists() for f in made] == [False]

    def test_an_audit_folder_that_is_a_file_is_audit_unavailable_with_no_figure(
            self, capsys, monkeypatch, tmp_path, made):
        from src import outcomes
        blocked = tmp_path / "audit"
        blocked.write_bytes(b"a file, not a folder")
        code, out, _, _ = asking(capsys, monkeypatch, blocked)
        assert code == 1
        assert f"Outcome: {outcomes.AUDIT_UNAVAILABLE.sentence}" in out.splitlines()
        assert "Audit line: none was written." in out.splitlines()
        for figure in ("3,983,040", "9,865,590", "Narration", "SQL"):
            assert figure not in out, figure
        assert [f.exists() for f in made] == [False]

    def test_promote_as_on_the_sample_without_acknowledge_all_as(self, capsys, monkeypatch,
                                                                 tmp_path, made):
        import main
        fake = fakes.FakeClient(replies=[])
        monkeypatch.setattr(main, "client_for", lambda k: fake)
        monkeypatch.setenv("ANTHROPIC_API_KEY", STAND_IN)
        code = main.main(["ask", ASKED, "--pair", "sample", "--role", "group_controller",
                          "--promote-as", "P. Promoter", "--audit", str(tmp_path / "audit")])
        assert code == 1 and fake.calls == [] and "nothing was sent" in capsys.readouterr().out

    @pytest.mark.parametrize("argv", [
        [], ["--pair", "sample"], [ASKED], [ASKED, "--pair", "elsewhere", "--role",
                                            "group_controller"],
        [ASKED, "--pair", "sample", "--role", "chief"],
        [ASKED, "--pair", "sample", "--role", "entity_controller"],
        [ASKED, "--pair", "sample", "--role", "group_controller", "--entity", "AT"],
        [ASKED, "--pair", "sample", "--role", "group_controller", "--narrate", "poetry"],
        [ASKED, "--pair", "sample", "--role", "group_controller", "--verbose", "yes"],
        [ASKED, "--pair", "sample", "--pair", "clean", "--role", "group_controller"]],
        ids=["nothing", "no question", "no pair", "an unknown pair", "an unknown role",
             "no entity", "an entity for the group", "an unknown narration",
             "an unknown option", "a pair twice"])
    def test_a_wrong_option_is_one_plain_line_and_exit_2(self, capsys, monkeypatch, made, argv):
        import main
        monkeypatch.setattr(main, "client_for", lambda k: fakes.FakeClient(replies=[]))
        monkeypatch.setenv("ANTHROPIC_API_KEY", STAND_IN)
        assert main.main(["ask", *argv]) == 2
        out = capsys.readouterr().out.splitlines()
        assert len(out) == 1 and out[0].startswith("ask: ") and made == []


class TestAskPrintsSafely:
    def test_a_console_in_cp1252_never_stops_a_print(self, monkeypatch, tmp_path, made):
        # A redirected stdout on Windows writes the locale's code page: the minus sign and an
        # emoji cannot be written in it, and are written as backslash escapes instead.
        import io
        import sys

        import main
        words = WORDS + " In words: " + chr(0x2212) + " " + chr(0x1F600) + "."
        fake = fakes.FakeClient(replies=[fakes.Reply(model_reply()), fakes.Reply(words)])
        monkeypatch.setattr(main, "client_for", lambda k: fake)
        monkeypatch.setenv("ANTHROPIC_API_KEY", STAND_IN)
        raw = io.BytesIO()
        console = io.TextIOWrapper(raw, encoding="cp1252", errors="strict", newline="\n")
        monkeypatch.setattr(sys, "stdout", console)
        code = main.main(["ask", ASKED, "--pair", "sample", "--role", "group_controller",
                          "--acknowledge-all-as", "A. Reviewer", "--promote-as", "P. Promoter",
                          "--narrate", "model", "--audit", str(tmp_path / "audit")])
        console.flush()
        written = raw.getvalue()
        assert code == 0
        assert b"In words: \\u2212 \\U0001f600." in written


class TestAskTheKeySearch:
    def test_no_key_and_no_header_in_what_ask_prints_or_writes(self, capsys, monkeypatch,
                                                               tmp_path, made):
        from src import narration
        error = fakes.status_error(fakes.anthropic.AuthenticationError, 401,
                                   f"invalid x-api-key {KEY_TEXT}; Authorization: Bearer x")
        said = []
        for replies, extra, folder in (
                ([fakes.Reply(model_reply())], (), "audit"),
                ([fakes.Reply(model_reply()), fakes.Reply(WORDS)], ("--narrate", "model"),
                 "audit"),
                ([fakes.Reply(model_reply(None, {"reason": "out_of_scope"}))], (), "audit"),
                ([error], (), "audit"),
                ([fakes.Reply(model_reply())], (), "blocked")):
            if folder == "blocked":
                (tmp_path / "blocked").write_bytes(b"a file")
            client = fakes.FakeClient(replies=list(replies), api_key=KEY_TEXT)
            code, out, err, _ = asking(capsys, monkeypatch, tmp_path / folder, *extra,
                                       client=client, key=KEY_TEXT)
            said.append(out + err)
        assert narration.NOTICE_WITHHELD not in said[1]
        everything = ("".join(said).encode("utf-8")
                      + (tmp_path / "audit" / "audit.jsonl").read_bytes()).lower()
        assert (tmp_path / "audit" / "audit.jsonl").read_bytes().count(b"\n") == 4
        for text in (KEY_TEXT.lower(), "x-api-key", "authorization", "bearer"):
            assert text.encode("ascii") not in everything, text


class TestAskCallCounts:
    def test_one_specification_call_and_a_narration_call_only_when_asked(self, capsys,
                                                                         monkeypatch, tmp_path,
                                                                         made):
        _, _, _, template = asking(capsys, monkeypatch, tmp_path / "audit")
        _, _, _, model = asking(capsys, monkeypatch, tmp_path / "audit", "--narrate", "model",
                                replies=[fakes.Reply(model_reply()), fakes.Reply(WORDS)])
        _, _, _, refused = asking(capsys, monkeypatch, tmp_path / "audit", "--narrate", "model",
                                  replies=[fakes.Reply(model_reply(None,
                                                                   {"reason": "out_of_scope"}))])
        _, _, _, invalid = asking(capsys, monkeypatch, tmp_path / "audit", question="")
        assert [len(c.calls) for c in (template, model, refused, invalid)] == [1, 2, 1, 0]
        assert "format" not in model.calls[1]["output_config"]


# -- replay ----------------------------------------------------------------------------------------

@pytest.fixture
def recorded(capsys, monkeypatch, tmp_path, made):
    """Six answers recorded by ask in one audit file: both pairs, under the group controller
    and the ES and AT controllers. [(pair, role, run id)] and the file."""
    from src import audit
    found = []
    for pair in ("clean", "sample"):
        for role in (("--role", "group_controller"),
                     ("--role", "entity_controller", "--entity", "ES"),
                     ("--role", "entity_controller", "--entity", "AT")):
            code, _, _, _ = asking(capsys, monkeypatch, tmp_path / "audit", pair=pair,
                                   role=role)
            assert code == 0
            found.append((pair, role[-1], audit.read(tmp_path / "audit" / "audit.jsonl")[-1][
                "run_id"]))
    made.clear()
    return found, tmp_path / "audit" / "audit.jsonl"


def replaying(capsys, monkeypatch, path, run_id):
    """main.main(["replay", ...]) with no key and a client seam that fails if it is ever
    used: (exit code, stdout lines)."""
    import main

    def never(key):
        raise AssertionError("replay made a client")
    monkeypatch.setattr(main, "client_for", never)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    code = main.main(["replay", str(path), run_id])
    return code, capsys.readouterr().out.splitlines()


def rewritten(path, folder, change):
    """A copy of an audit file whose first line's record is changed by change and written
    again with its own hash, so the copy still verifies."""
    from src import audit
    records = [{k: v for k, v in r.items() if k != "record_sha256"} for r in audit.read(path)]
    change(records[0])
    copy = folder / "changed.jsonl"
    copy.write_bytes(b"".join(audit.line_of(r) for r in records))
    return copy, records[0]["run_id"]


class TestReplay:
    def test_every_recorded_answer_replays_to_same_on_both_pairs_and_three_roles(
            self, capsys, monkeypatch, recorded, made):
        found, path = recorded
        before = path.read_bytes()
        for pair, role, run_id in found:
            code, lines = replaying(capsys, monkeypatch, path, run_id)
            assert code == 0, lines
            assert lines[0] == f"Replay of run {run_id}: line " \
                               f"{[r[2] for r in found].index(run_id) + 1} of {path.resolve()}"
            assert lines[1] == f"Pair: {pair}, its breaks acknowledged and the pair promoted " \
                               'as "Replay"'
            assert lines[2] == "State: same" and lines[3].startswith("  recorded: outcome ")
            assert lines[3][len("  recorded: "):] == lines[4][len("  replayed: "):]
            assert lines[-1] == "No key was read and no model was called."
        assert path.read_bytes() == before
        assert len(made) == 6 and not [f for f in made if f.exists()]
        assert {(p, r) for p, r, _ in found} == {(p, r) for p in ("clean", "sample")
                                                  for r in ("group_controller", "ES", "AT")}

    def test_a_record_with_no_specification_is_not_replayable_before_anything_is_built(
            self, capsys, monkeypatch, tmp_path, made):
        from src import audit
        asking(capsys, monkeypatch, tmp_path / "audit", replies=[
            fakes.Reply(model_reply(None, {"reason": "out_of_scope"}))])
        (record,) = audit.read(tmp_path / "audit" / "audit.jsonl")
        made.clear()
        code, lines = replaying(capsys, monkeypatch, tmp_path / "audit" / "audit.jsonl",
                                record["run_id"])
        assert code == 1 and made == []
        assert lines[1] == ("State: not_replayable: the record holds no specification "
                            "(outcome out_of_scope).")

    def test_a_record_no_pair_matches_is_not_comparable_before_anything_is_built(
            self, capsys, monkeypatch, recorded, made, tmp_path):
        _, path = recorded
        copy, run_id = rewritten(path, tmp_path, lambda r: r["provenance"].update(
            plan_file_sha256="0" * 64))
        code, lines = replaying(capsys, monkeypatch, copy, run_id)
        assert code == 1 and made == []
        assert "State: not_comparable: the kinds that differ are plan_file." in lines

    def test_a_changed_registry_hash_is_not_comparable(self, capsys, monkeypatch, recorded,
                                                        tmp_path):
        _, path = recorded
        copy, run_id = rewritten(path, tmp_path, lambda r: r["provenance"].update(
            registry_hash="0" * 64))
        code, lines = replaying(capsys, monkeypatch, copy, run_id)
        assert code == 1
        assert "State: not_comparable: the kinds that differ are registry." in lines

    def test_a_changed_result_hash_is_different_with_both_sets(self, capsys, monkeypatch,
                                                               recorded, tmp_path):
        _, path = recorded
        copy, run_id = rewritten(path, tmp_path, lambda r: r.update(result_sha256="0" * 64))
        code, lines = replaying(capsys, monkeypatch, copy, run_id)
        assert code == 1 and "State: different" in lines
        recorded_line = next(l for l in lines if l.startswith("  recorded: "))
        replayed_line = next(l for l in lines if l.startswith("  replayed: "))
        assert recorded_line.endswith("result SHA-256 " + "0" * 64)
        assert not replayed_line.endswith("0" * 64)

    def test_another_engine_version_is_printed_not_refused(self, capsys, monkeypatch,
                                                           recorded, tmp_path):
        from src import warehouse
        _, path = recorded
        copy, run_id = rewritten(path, tmp_path, lambda r: r["provenance"].update(
            engine_version="0.0.1"))
        code, lines = replaying(capsys, monkeypatch, copy, run_id)
        assert code == 0 and "State: same" in lines
        assert (f"Engine: recorded {warehouse.ENGINE} 0.0.1, now {warehouse.ENGINE} "
                f"{warehouse.ENGINE_VERSION}; reported, not refused.") in lines

    def test_a_file_that_does_not_verify_is_refused_with_its_lines(self, capsys, monkeypatch,
                                                                    recorded, made, tmp_path):
        _, path = recorded
        lines = path.read_bytes().split(b"\n")
        lines[1] = lines[1].replace(b'"outcome":"answered"', b'"outcome":"no_data"', 1)
        copy = tmp_path / "altered.jsonl"
        copy.write_bytes(b"\n".join(lines))
        code, said = replaying(capsys, monkeypatch, copy, "0" * 32)
        assert code == 1 and made == [] and len(said) == 1
        assert said[0] == "replay: altered.jsonl does not verify (line 2 hash); nothing was " \
                          "replayed."

    def test_a_run_id_in_no_line_or_in_two_is_refused(self, capsys, monkeypatch, recorded,
                                                       made, tmp_path):
        found, path = recorded
        code, said = replaying(capsys, monkeypatch, path, "f" * 32)
        assert (code, len(said)) == (1, 1) and said[0].startswith("replay: no line of ")
        twice = tmp_path / "twice.jsonl"
        first = path.read_bytes().split(b"\n")[0] + b"\n"
        twice.write_bytes(first + first)
        code, said = replaying(capsys, monkeypatch, twice, found[0][2])
        assert (code, len(said)) == (1, 1)
        assert said[0].startswith("replay: 2 lines of twice.jsonl have the run id ")
        assert made == []

    @pytest.mark.parametrize("argv", [[], ["only.jsonl"], ["a", "b", "c"], ["--file", "x"]],
                             ids=["nothing", "no run id", "three", "an option"])
    def test_a_wrong_argument_is_one_plain_line_and_exit_2(self, capsys, argv):
        import main
        assert main.main(["replay", *argv]) == 2
        assert capsys.readouterr().out.startswith("replay: ")

    def test_a_missing_file_is_one_plain_line(self, capsys, tmp_path):
        import main
        assert main.main(["replay", str(tmp_path / "absent.jsonl"), "0" * 32]) == 1
        assert capsys.readouterr().out == "replay: absent.jsonl: there is no such file.\n"


# -- In a child process, through the real client seam against the loopback stand-in ----------------

class TestAskAndReplayInAChildProcess:
    def test_no_key_in_a_child_process(self, tmp_path):
        done = run("ask", ASKED, "--pair", "sample", "--role", "group_controller", "--audit",
                   tmp_path / "audit", temp=tmp_path)
        import main
        assert (done.returncode, done.stdout, done.stderr) == (1, main.NO_KEY + "\n", "")
        assert sorted(p.name for p in tmp_path.iterdir()) == []

    def test_ask_then_replay_through_the_real_sdk(self, tmp_path):
        from src import audit
        temp = tmp_path / "temp"
        temp.mkdir()
        with fakes.StandInAPI(replies=[(200, fakes.message(model_reply())),
                                       (200, fakes.message(AT_WORDS))]) as api:
            done = subprocess.run(
                [sys.executable, str(MAIN), "ask", ASKED, "--pair", "sample", "--role",
                 "group_controller", "--acknowledge-all-as", "A. Reviewer", "--promote-as",
                 "P. Promoter", "--narrate", "model", "--audit", str(tmp_path / "audit")],
                capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=600,
                env=subprocess_env(TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp),
                                   ANTHROPIC_API_KEY=STAND_IN, ANTHROPIC_BASE_URL=api.url))
        assert done.returncode == 0, done.stdout + done.stderr
        assert [r["headers"]["x-api-key"] for r in api.received] == [STAND_IN, STAND_IN]
        assert "Narration, the model's wording:" in done.stdout and AT_WORDS in done.stdout
        (record,) = audit.read(tmp_path / "audit" / "audit.jsonl")
        assert [c["request_id"] for c in record["calls"]] == ["req_standin_1",
                                                              "req_standin_2"]
        assert "req_standin" not in done.stdout and STAND_IN not in done.stdout
        assert sorted(p.name for p in temp.iterdir()) == []
        replayed = run("replay", tmp_path / "audit" / "audit.jsonl", record["run_id"],
                       temp=temp)
        assert replayed.returncode == 0, replayed.stdout + replayed.stderr
        assert "State: same" in replayed.stdout.splitlines()
        assert sorted(p.name for p in temp.iterdir()) == []
        assert (tmp_path / "audit" / "audit.jsonl").read_bytes().count(b"\n") == 1


class TestTheUsageNamesAskAndReplay:
    def test_the_usage_and_its_closing_lines(self, capsys):
        import main
        assert main.main([]) == 0
        out = capsys.readouterr().out
        assert ('python main.py ask "<question>" --pair clean|sample --role '
                'group_controller|entity_controller [--entity ES] [--acknowledge-all-as "Name"] '
                '[--promote-as "Name"] [--narrate template|model] [--audit <folder>]') in out
        assert "python main.py replay <audit file> <run id>" in out
        flat = " ".join(out.split())
        assert ("Phase 3: a question in words on the command line, with your own key. The model "
                "writes a specification and nothing else; Python validates, compiles and runs "
                "it, narrates the answer and records every question. The web pages are still "
                "stubs.") in flat
        assert "No model is called yet." not in out
