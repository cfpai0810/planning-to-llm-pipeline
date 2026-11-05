"""The command line (main.py), part 1d, brief 6.5.

python main.py check <workbook> <ledger> lands both files into a temporary
warehouse, which is deleted afterwards; prints each batch record's summary,
external link targets shown as text, as data; runs the controls and
reconcile and prints the breaks as a plain table with their count; and never
promotes. A file that is missing or cannot be read gives one plain line, no
traceback, and a non-zero exit. The check is run here as a child process,
with the environment subprocess_env() gives it (no key, and a temporary
folder of the test's own, so the test can see it is left empty).
"""

import json
import subprocess
import sys

import pytest

from tests.conftest import subprocess_env
from tests.extractions import FIXTURES, ROOT, extraction

MAIN = ROOT / "main.py"
SAMPLE_BREAKS = ("FI-facc450f1682", "TO-417d669f6dd1", "HC-b04c76a19ed4", "FI-64e07c883fff",
                 "HC-d72689858228")


def run(*args, temp):
    """main.py in a child process, its temporary files in temp."""
    return subprocess.run([sys.executable, str(MAIN), *map(str, args)], capture_output=True,
                          text=True, encoding="utf-8", cwd=str(ROOT), timeout=180,
                          env=subprocess_env(TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp)))


@pytest.fixture(scope="module")
def checked(tmp_path_factory):
    temp = tmp_path_factory.mktemp("check_temp")
    done = run("check", "data/planning_model.xlsx", "data/erp_trial_balance.csv", temp=temp)
    return done, temp


class TestCheck:
    def test_it_runs_and_exits_zero(self, checked):
        done, _ = checked
        assert done.returncode == 0, done.stderr
        assert done.stderr == ""

    def test_it_prints_each_batch_record(self, checked):
        out = checked[0].stdout
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
            assert line in out.splitlines(), line

    def test_it_prints_the_breaks_as_a_table_and_counts_them(self, checked):
        out = checked[0].stdout
        lines = out.splitlines()
        header = next(l for l in lines if l.startswith("id "))
        assert header.split() == ["id", "control", "kind", "severity", "where", "message"]
        rows = [l for l in lines if l[:3] in ("FI-", "TO-", "HC-", "ST-", "CS-", "RC-")]
        assert len(rows) == 8
        for break_id in SAMPLE_BREAKS:
            assert any(r.startswith(break_id) for r in rows), break_id
        assert sum(r.startswith("RC-") for r in rows) == 3
        assert "8 breaks: 5 from the controls, 3 from reconcile." in lines
        assert "Nothing was promoted: the check never promotes." in lines

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
        done = run("check", "data/planning_model.xlsx", bad, temp=tmp_path)
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
