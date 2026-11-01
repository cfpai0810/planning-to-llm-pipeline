"""Neutrality: no planning, EPM, BI or warehouse vendor is named, unless the code depends on it.

The scan reads every text file in the repository (README.md, docs/,
streamlit_app/ and the rest; later the PDF text too), except this file, which
must hold the names. Matching is by whole word, so a name inside another word
("Vena" inside "provenance") never fires. A vendor may be named only when the
product code imports one of its modules: that is the one way the code can
depend on a product, so it is the one exception.

The list is the engineer's proposal, awaiting the reviewer's approval.
Ordinary English words that are also vendor names are listed in
EXCLUDED_WORDS and deliberately not scanned: they would fire on plain prose.
"""

import ast
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
THIS_FILE = Path(__file__).resolve()
CODE = ("config.py", "main.py", "src", "streamlit_app", "scripts")
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "venv"}


@dataclass(frozen=True)
class Vendor:
    name: str
    modules: tuple = ()          # importing any of these makes the name allowed
    case_sensitive: bool = False  # for names that are also words in lower case


def _v(*names, modules=(), case_sensitive=False):
    return [Vendor(n, modules, case_sensitive) for n in names]


PLANNING_EPM = (
    _v("Anaplan", "Adaptive Insights", "Adaptive Planning", "Vena", "Planful",
       "Host Analytics", "Jedox", "Prophix", "OneStream", "Hyperion", "Essbase", "Oracle EPM",
       "Tagetik", "TM1", "Cognos", "Kepion", "Datarails", "Abacum", "Centage",
       "Board International")
    + _v("SAP", case_sensitive=True)
)
BI = (
    _v("Power BI", "Qlik", "QlikView", "Domo", "Sisense", "ThoughtSpot", "MicroStrategy",
       "Metabase", "Apache Superset", "Redash", "Spotfire", "BusinessObjects", "Yellowfin",
       "Lightdash", "Holistics", "GoodData", "Zoho Analytics", "Pyramid Analytics",
       "Mode Analytics", "Sigma Computing", "Alteryx")
    + _v("Tableau", modules=("tableauhyperapi", "tableauserverclient"), case_sensitive=True)
    + _v("Looker", case_sensitive=True)
)
WAREHOUSE = (
    _v("Databricks", modules=("databricks",))
    + _v("BigQuery", modules=("google.cloud.bigquery",))
    + _v("DuckDB", modules=("duckdb",))
    + _v("dbt", modules=("dbt",))
    + _v("Azure Synapse", "Microsoft Fabric", "Teradata", "Vertica", "Greenplum", "ClickHouse",
         "Firebolt", "Dremio", "Trino", "Exasol", "SingleStore", "Netezza", "MotherDuck",
         "Fivetran", "Airbyte", "Informatica", "Talend", "Matillion")
    + _v("Snowflake", modules=("snowflake",), case_sensitive=True)
    + _v("Redshift", "Yellowbrick", case_sensitive=True)
)
VENDORS = PLANNING_EPM + BI + WAREHOUSE

# Vendor names that are ordinary words: not scanned, because they would fire
# on plain prose ("a board pack", "an OLAP cube", "the oracle in a test").
EXCLUDED_WORDS = {
    "Board": "board pack, board of directors",
    "Cube": "an OLAP cube",
    "Mode": "a mode of working",
    "Sigma": "sigma for a standard deviation",
    "Pigment": "the plain noun",
    "Causal": "causal language, which the commentary checks look for",
    "Runway": "cash runway",
    "Mosaic": "the plain noun",
    "Limelight": "the plain noun",
    "Solver": "a spreadsheet solver",
    "Workday": "a working day",
    "Oracle": "a test oracle",
    "Sage": "the plain noun",
    "Hex": "a hex colour",
    "Preset": "a preset value",
    "Superset": "a superset of a set",
    "Stitch": "the plain verb",
    "Presto": "the plain word",
    "Synapse": "the plain noun",
    "Fabric": "the plain noun",
    "Strategy": "the plain noun",
    "Starburst": "the plain noun",
    "Adaptive": "adaptive thinking, the model setting",
    "Planning Analytics": "planning analytics in general",
    "Business Objects": "business objects in general",
}


def _pattern(vendor: Vendor) -> re.Pattern:
    words = r"\s+".join(re.escape(w) for w in vendor.name.split())
    flags = 0 if vendor.case_sensitive else re.IGNORECASE
    return re.compile(rf"(?<![A-Za-z0-9_]){words}(?![A-Za-z0-9_])", flags)


def findings(text: str, imported: set[str], vendors=VENDORS) -> list[str]:
    """The vendor names text mentions that no imported module allows, in list order."""
    found = []
    for vendor in vendors:
        allowed = any(m == mod or m.startswith(mod + ".") for mod in vendor.modules
                      for m in imported)
        if not allowed and _pattern(vendor).search(text):
            found.append(vendor.name)
    return found


def imported_modules() -> set[str]:
    """Every absolute import in the product code, as dotted names."""
    names = set()
    for part in CODE:
        base = ROOT / part
        paths = [base] if base.is_file() else sorted(base.rglob("*.py")) if base.is_dir() else []
        for path in paths:
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    names.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names.add(node.module)
    return names


def scanned_files() -> list[Path]:
    files = []
    for path in sorted(ROOT.rglob("*")):
        if (path.is_file() and path.resolve() != THIS_FILE
                and not set(path.relative_to(ROOT).parts) & SKIP_DIRS):
            data = path.read_bytes()
            if b"\0" not in data:
                files.append(path)
    return files


class TestNeutrality:
    def test_no_vendor_is_named_anywhere(self):
        imported = imported_modules()
        named = {}
        for path in scanned_files():
            found = findings(path.read_text(encoding="utf-8"), imported)
            if found:
                named[path.relative_to(ROOT).as_posix()] = found
        assert named == {}

    def test_a_whole_word_fires_and_an_embedded_one_does_not(self):
        assert findings("The plan is exported from Vena.", set()) == ["Vena"]
        assert findings("Every figure keeps its provenance.", set()) == []
        assert findings("A Vena-style export.", set()) == ["Vena"]
        assert findings("Venable and Venafi are other words.", set()) == []
        assert findings("The dashboards live in Power\nBI.", set()) == ["Power BI"]

    def test_a_case_sensitive_name_ignores_the_plain_word(self):
        assert findings("A snowflake schema joins dimension tables.", set()) == []
        assert findings("The data sits in Snowflake.", set()) == ["Snowflake"]
        assert findings("The sap rises.", set()) == []

    def test_a_vendor_the_code_imports_may_be_named(self):
        text = "Actuals land in DuckDB."
        assert findings(text, set()) == ["DuckDB"]
        assert findings(text, {"duckdb"}) == []
        assert findings(text, {"duckdb.typing"}) == []
        assert findings(text, {"duckdb_extras"}) == ["DuckDB"]

    def test_the_import_reader_sees_the_code(self):
        assert "streamlit" in imported_modules()

    def test_the_scan_covers_the_readme_the_docs_and_the_pages(self):
        scanned = {p.relative_to(ROOT).as_posix() for p in scanned_files()}
        assert {"README.md", "docs/releasing.md", "streamlit_app/Home.py"} <= scanned
        assert any(name.startswith("streamlit_app/views/") for name in scanned)
        assert "tests/test_neutrality.py" not in scanned

    def test_no_excluded_word_is_scanned(self):
        scanned = {v.name.lower() for v in VENDORS}
        assert not {w.lower() for w in EXCLUDED_WORDS} & scanned
