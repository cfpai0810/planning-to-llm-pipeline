"""The repository's own rules: the pinned stack and its lock, line endings, CI and the docs."""

import re
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent.parent
DIRECT = ROOT / "requirements.in"
LOCK = ROOT / "requirements.txt"
DEV_LOCK = ROOT / "requirements-dev.txt"
RELEASING = ROOT / "docs" / "releasing.md"

# The series stack (brief, rule 3), with anthropic at the version the day-one
# question settled (0.116.0, the series version, decided at the pilot review).
SERIES = {"streamlit": "1.60.0", "starlette": "1.7.0", "python-dotenv": "1.2.2",
          "certifi": "2026.10.19", "anthropic": "0.116.0"}
# Phase 1 adds the workbook library with the sample build (part 1b) and the
# warehouse engine with the code that uses it (part 1c): 1.5.6, never 2.0.0.
# Phase 2 adds the query parser with the guard that uses it (part 2d): 30.24.0.
PHASE = SERIES | {"openpyxl": "3.1.5", "duckdb": "1.5.6", "sqlglot": "30.24.0"}
PIN = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[A-Za-z0-9.+!_-]+)"
                 r"(?:\s*;\s*\S.*)?$")
REBOOT_RULE = "The owner reboots the app after every push to a deployed branch."
TEXT_SUFFIXES = {".py", ".md", ".txt", ".in", ".toml", ".yml", ".json", ".gitattributes",
                 ".gitignore", ".css", ".html", ".csv"}
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "venv"}


def _requirement_lines(path: Path):
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if line and not line.startswith("#") and not line.startswith(("-r ", "-c ")):
            yield number, line


def _pins(path: Path) -> dict[str, str]:
    pins = {}
    for _, line in _requirement_lines(path):
        match = PIN.match(line)
        assert match, (path.name, line)
        pins[canonicalize_name(match["name"])] = match["version"]
    return pins


def _repo_text_files():
    for path in sorted(ROOT.rglob("*")):
        parts = set(path.relative_to(ROOT).parts)
        if path.is_file() and not parts & SKIP_DIRS and (
                path.suffix in TEXT_SUFFIXES or path.name in TEXT_SUFFIXES):
            yield path


class TestPinnedStack:
    def test_every_lock_line_is_an_exact_pin(self):
        bad = [(path.name, number, line) for path in (LOCK, DEV_LOCK)
               for number, line in _requirement_lines(path) if not PIN.match(line)]
        assert bad == []

    def test_the_series_stack_is_pinned_exactly(self):
        lock = _pins(LOCK)
        assert {name: lock.get(name) for name in PHASE} == PHASE
        assert _pins(DEV_LOCK)["pytest"] == "9.1.1"

    def test_the_direct_pins_are_the_phase_stack_and_nothing_else(self):
        # No data or spreadsheet library as a direct dependency until a phase
        # uses it.
        assert _pins(DIRECT) == PHASE

    def test_the_openpyxl_reason_is_kept(self):
        text = DIRECT.read_text(encoding="utf-8")
        lines = text.splitlines()
        reason = lines[lines.index("openpyxl==3.1.5") - 1]
        assert reason.startswith("# ") and "workbook" in reason

    def test_the_duckdb_reason_is_kept(self):
        lines = DIRECT.read_text(encoding="utf-8").splitlines()
        reason = lines[lines.index("duckdb==1.5.6") - 1]
        assert reason.startswith("# ") and "warehouse" in reason and "2.0.0" in reason

    def test_the_warehouse_engine_is_not_the_2_0_line(self):
        for path in (DIRECT, LOCK, DEV_LOCK):
            assert _pins(path)["duckdb"] == "1.5.6", path.name

    def test_the_lock_is_closed_over_dependencies(self):
        # Every dependency of every locked package (for this Python and
        # platform) is itself in the lock: transitive packages are pinned too.
        lock = _pins(LOCK)
        missing = set()
        for name in lock:
            try:
                requires = distribution(name).requires or []
            except PackageNotFoundError:
                continue          # a platform-only pin, such as watchdog off this platform
            for text in requires:
                requirement = Requirement(text)
                if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
                    continue
                if canonicalize_name(requirement.name) not in lock:
                    missing.add(f"{requirement.name} (needed by {name})")
        assert missing == set()

    def test_the_installed_packages_are_the_locked_versions(self):
        drift = {}
        for name, pinned in _pins(DEV_LOCK).items():
            try:
                installed = version(name)
            except PackageNotFoundError:
                continue
            if installed != pinned:
                drift[name] = (pinned, installed)
        assert drift == {}

    def test_the_dev_lock_holds_the_runtime_lock_exactly(self):
        lock, dev = _pins(LOCK), _pins(DEV_LOCK)
        assert {name: dev.get(name) for name in lock} == lock

    def test_the_lock_commands_are_the_documented_ones(self):
        releasing = RELEASING.read_text(encoding="utf-8")
        for path in (LOCK, DEV_LOCK):
            command = re.search(r"^#\s+(uv pip compile .+)$", path.read_text(encoding="utf-8"),
                                flags=re.MULTILINE).group(1)
            assert command in releasing, command

    def test_the_starlette_reason_is_kept(self):
        assert ("# Streamlit 1.60 cannot serve images with starlette 1.8 or later"
                in DIRECT.read_text(encoding="utf-8"))


class TestFilesAndEndings:
    def test_gitattributes_keep_inputs_byte_for_byte(self):
        lines = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
        for rule in ("* text=auto eol=lf", "*.csv -text", "*.xlsx binary"):
            assert rule in lines, rule

    def test_gitignore_keeps_secrets_and_output_out(self):
        lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        for rule in (".env", ".env.*", ".streamlit/secrets.toml", "__pycache__/", "output/"):
            assert rule in lines, rule

    def test_every_text_file_has_lf_endings(self):
        crlf = [p.relative_to(ROOT).as_posix() for p in _repo_text_files()
                if b"\r\n" in p.read_bytes()]
        assert crlf == []

    def test_no_em_or_en_dash_anywhere(self):
        # House style: colons or commas, never the long dashes.
        dashes = (chr(0x2014), chr(0x2013))
        found = [p.relative_to(ROOT).as_posix() for p in _repo_text_files()
                 if any(d in p.read_text(encoding="utf-8") for d in dashes)]
        assert found == []

    def test_the_layout(self):
        for part in ("config.py", "main.py", "README.md", "docs/releasing.md", "data",
                     "generated", "src/__init__.py", "streamlit_app/Home.py",
                     "streamlit_app/lib", "streamlit_app/views", "tests/conftest.py",
                     ".streamlit/config.toml", ".github/workflows/tests.yml", ".gitattributes",
                     ".gitignore", "requirements.in", "requirements.txt", "requirements-dev.txt"):
            assert (ROOT / part).exists(), part


class TestContinuousIntegration:
    def test_ci_runs_both_pythons_from_the_lock_with_no_secrets(self):
        text = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        assert re.search(r"^on:\n  push:\n  pull_request:\n", text, flags=re.MULTILINE)
        assert 'python-version: ["3.13", "3.14"]' in text
        assert "fail-fast: false" in text
        assert "python -m pip install -r requirements.txt -r requirements-dev.txt" in text
        assert "python -m pytest -q" in text
        assert "secrets." not in text


class TestDocs:
    def test_the_readme_paragraph_is_the_approved_wording(self):
        paragraph = (
            "A governed question-and-answer pipeline over planning data. A planning model "
            "built in a spreadsheet will be extracted, checked, landed in a warehouse, tied "
            "out against a second source, and then queried in plain English. The model will "
            "never write SQL and never calculate: it will turn a question into a structured "
            "query specification, and Python will validate that specification against "
            "finance-owned metric definitions, compile the query, run it under the user’s "
            "access role and return the figures with their provenance. The aim is to show "
            "that the path from a planning model to a language model can be governed.")
        readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
        assert paragraph in readme

    def test_the_readme_says_what_exists_and_that_nothing_answers_yet(self):
        readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
        assert "Phase 0" not in readme
        assert ("**Phase 1, not yet usable.** What exists: the sample workbook and its ledger "
                "extract, the extractor, the spreadsheet controls, reconcile and promotion. "
                "Nothing answers a question yet.") in readme
        assert "python main.py check" in readme

    def test_the_reboot_rule_is_stated(self):
        for path in (ROOT / "README.md", RELEASING):
            assert REBOOT_RULE in " ".join(path.read_text(encoding="utf-8").split()), path.name

    def test_no_in_app_workaround_for_a_pull_without_a_reboot(self):
        # P6's lib/fresh.py is not copied: the reboot is the rule.
        assert not (ROOT / "streamlit_app" / "lib" / "fresh.py").exists()
        sources = [p for p in ROOT.rglob("*.py") if not set(p.relative_to(ROOT).parts) & SKIP_DIRS]
        assert not [p for p in sources if re.search(r"^\s*(from|import)\s.*\bfresh\b",
                                                    p.read_text(encoding="utf-8"),
                                                    flags=re.MULTILINE)]


class TestCommandLine:
    def test_main_is_no_longer_a_stub(self):
        # The command line itself is tests/test_main.py's.
        assert "Phase 0, not yet usable" not in (ROOT / "main.py").read_text(encoding="utf-8")


# -- Part 2b: pandas, a direct test-only dependency ------------------------------------------------

DEV_DIRECT = ROOT / "requirements-dev.in"


class TestTheTestOnlyDependency:
    def test_pandas_is_pinned_for_the_tests_at_the_version_already_locked(self):
        lines = DEV_DIRECT.read_text(encoding="utf-8").splitlines()
        assert "pandas==3.1.0" in lines
        reason = lines[lines.index("pandas==3.1.0") - 1]
        assert reason.startswith("# ") and "test" in reason and "oracle" in reason
        assert _pins(DEV_LOCK)["pandas"] == _pins(LOCK)["pandas"] == "3.1.0"

    def test_pandas_is_not_a_runtime_pin(self):
        assert "pandas" not in _pins(DIRECT)

    def test_the_dev_lock_names_requirements_dev_in_as_a_source_of_pandas(self):
        text = DEV_LOCK.read_text(encoding="utf-8")
        block = text.split("\npandas==3.1.0", 1)[1].split("\n", 1)[1]
        sources = block.split("\n")[:4]
        assert any("-r requirements-dev.in" in s for s in sources), sources


# -- Part 2d: the query parser, the one runtime package Phase 2 adds -------------------------------

class TestTheQueryParser:
    def test_sqlglot_is_pinned_with_its_reason_and_no_extra(self):
        lines = DIRECT.read_text(encoding="utf-8").splitlines()
        assert "sqlglot==30.24.0" in lines
        reason = lines[lines.index("sqlglot==30.24.0") - 1]
        assert reason.startswith("# ") and "guard" in reason and "no extra" in reason
        assert not [l for l in lines if l.startswith("sqlglot[")]
        assert _pins(LOCK)["sqlglot"] == _pins(DEV_LOCK)["sqlglot"] == "30.24.0"

    def test_sqlglot_brings_no_other_package(self):
        # In the lock, sqlglot is needed by requirements.in alone, and nothing is needed by it.
        text = LOCK.read_text(encoding="utf-8")
        block = text.split("\nsqlglot==30.24.0\n", 1)[1].split("\n", 1)[0]
        assert block.strip() == "# via -r requirements.in"
        assert "# via sqlglot" not in text and "#   sqlglot" not in text
