"""The test count: written by a full, passing run, read into the README, never typed.

tests/conftest.py writes generated/test_counts.json and the README's count
block at the end of a full, passing run, from the same numbers. A partial run
(a file, a node id, a -k or -m filter, a deselection, --lf, --ignore,
--collect-only) or a failing run writes nothing. The end-to-end tests below run
a small suite in a temporary folder with a copy of the real conftest.
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests import conftest

ROOT = Path(__file__).resolve().parent.parent
COUNTS = ROOT / "generated" / "test_counts.json"


def _readme_block() -> str:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(re.escape(conftest.COUNT_START) + r"\n(.*?)\n" + re.escape(conftest.COUNT_END),
                      text, flags=re.DOTALL)
    assert match, "the README has no generated count block"
    return match.group(1)


def _collect_only_count(root: Path) -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=root, env=conftest.subprocess_env(), capture_output=True, text=True,
        encoding="utf-8", timeout=600)
    match = re.search(r"^(\d+) tests? collected", result.stdout, flags=re.MULTILINE)
    assert match, result.stdout + result.stderr
    return int(match.group(1))


@pytest.fixture
def committed(request):
    """The committed count; a run with --update-counts rewrites it, so it is not checked then."""
    if request.config.getoption("update_counts"):
        pytest.skip("--update-counts: this run rewrites the committed count")
    return json.loads(COUNTS.read_text(encoding="utf-8"))


class TestCommittedCounts:
    def test_the_readme_block_is_the_generated_file(self, committed):
        # The README holds no typed number: its block is rendered from the file.
        assert _readme_block() == conftest.render_count_block(committed)

    def test_the_counts_add_up(self, committed):
        counts = committed
        assert counts["total"] == sum(counts["by_file"].values()) == sum(counts["by_class"].values())

    def test_the_readme_count_equals_collect_only(self, committed):
        stated = int(re.search(r"\*\*(\d+) tests\*\*", _readme_block()).group(1))
        assert stated == _collect_only_count(ROOT), (
            "The README count is stale. Run python -m pytest --update-counts once "
            "(docs/releasing.md).")


# -- End to end: a small suite with a copy of the real conftest --------------------

TOY_A = """
def test_one():
    assert True

def test_two():
    assert True
"""
TOY_B = """
import os

class TestB:
    def test_three(self):
        assert os.environ.get("TOY_FAIL") != "1"
"""
TOY_README = (f"# Toy\n\n{conftest.COUNT_START}\nThe suite has **0 tests**.\n"
              f"{conftest.COUNT_END}\n")


@pytest.fixture
def toy(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    shutil.copy(ROOT / "tests" / "conftest.py", tests / "conftest.py")
    (tests / "__init__.py").write_text("", encoding="utf-8")
    (tests / "test_a.py").write_text(TOY_A, encoding="utf-8")
    (tests / "test_b.py").write_text(TOY_B, encoding="utf-8")
    (tmp_path / "README.md").write_text(TOY_README, encoding="utf-8", newline="\n")
    return tmp_path


def _run(root: Path, *args: str, fail: bool = False) -> subprocess.CompletedProcess:
    env = conftest.subprocess_env()
    if fail:
        env["TOY_FAIL"] = "1"
    return subprocess.run([sys.executable, "-m", "pytest", "-q", *args], cwd=root, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=300)


class TestOnlyAFullPassingRunWrites:
    @pytest.mark.parametrize("args", [(), ("tests",)], ids=["no arguments", "the tests folder"])
    def test_a_full_passing_run_writes_the_file_and_the_readme(self, toy, args):
        result = _run(toy, "-p", "no:cacheprovider", *args)
        assert result.returncode == 0, result.stdout
        counts = json.loads((toy / "generated" / "test_counts.json").read_text(encoding="utf-8"))
        assert counts["total"] == 3
        assert counts["by_file"] == {"tests/test_a.py": 2, "tests/test_b.py": 1}
        readme = (toy / "README.md").read_text(encoding="utf-8")
        assert "The suite has **3 tests**" in readme and readme.startswith("# Toy\n")

    @pytest.mark.parametrize("args", [
        ("tests/test_a.py",),
        # Both files run, nothing is deselected and no filter is given, yet one
        # test is left out: P6's writer counted this as a full run (item 56).
        ("tests/test_a.py::test_one", "tests/test_b.py"),
        ("-k", "one or two or three"),
        ("-m", "not slow"),
        ("--deselect", "tests/test_a.py::test_one"),
        ("--ignore", "tests/test_b.py"),
        ("--collect-only",),
    ], ids=["one file", "a node id beside a whole file", "-k", "-m", "--deselect", "--ignore",
            "--collect-only"])
    def test_a_partial_run_writes_nothing(self, toy, args):
        result = _run(toy, "-p", "no:cacheprovider", *args)
        assert result.returncode == 0, result.stdout
        assert not (toy / "generated").exists()
        assert (toy / "README.md").read_text(encoding="utf-8") == TOY_README

    def test_a_last_failed_rerun_writes_nothing(self, toy):
        # --lf after a failure reruns only the failure: never a full count.
        assert _run(toy, fail=True).returncode == 1
        result = _run(toy, "--lf")
        assert result.returncode == 0, result.stdout
        assert not (toy / "generated").exists()

    def test_a_failing_run_writes_nothing(self, toy):
        result = _run(toy, "-p", "no:cacheprovider", fail=True)
        assert result.returncode == 1
        assert not (toy / "generated").exists()
        assert (toy / "README.md").read_text(encoding="utf-8") == TOY_README
