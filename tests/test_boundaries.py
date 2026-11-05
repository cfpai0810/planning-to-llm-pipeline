"""The boundaries around the model: one configuration, one caller, one client builder.

- No source file but config.py holds a model identifier.
- Only src/model_call.py calls the client, and only lib/claude_client.py
  builds one. The calls are found as attribute calls (client.messages.create,
  anthropic.Anthropic): P6's boundary test read only calls by a bare name, so
  pipeline.render(...) passed it unseen (item 56).
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "venv"}
CODE = ("config.py", "main.py", "src", "streamlit_app", "scripts")
# Built at run time, so this file does not match its own pattern.
MODEL_ID = re.compile("claude" + r"-[a-z0-9]")
CLIENT_METHODS = {"create", "parse", "stream", "count_tokens"}


def _sources(parts=None):
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if set(rel.parts) & SKIP_DIRS:
            continue
        if parts is None or rel.parts[0] in parts:
            yield path


def _dotted(node) -> str:
    """'client.messages.create' for an attribute chain; '' when it starts elsewhere."""
    names = []
    while isinstance(node, ast.Attribute):
        names.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        names.append(node.id)
    elif isinstance(node, ast.Call):
        names.append("()")
    return ".".join(reversed(names))


def model_calls(source: str) -> list[str]:
    """Every call of a client's messages API in source, found through attribute chains."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            chain = _dotted(node.func).split(".")
            if chain[-1] in CLIENT_METHODS and "messages" in chain[:-1]:
                found.append(".".join(chain))
    return found


def client_builds(source: str) -> list[str]:
    """Every construction of an Anthropic client, by name or by attribute."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name.split(".")[-1] in ("Anthropic", "AsyncAnthropic"):
                found.append(name)
    return found


class TestTheWalker:
    def test_attribute_calls_are_found(self):
        source = ("result = client.messages.create(model=m)\n"
                  "parsed = self.client.messages.parse(model=m)\n"
                  "beta = client.beta.messages.create(model=m)\n"
                  "c = anthropic.Anthropic(api_key=k)\n")
        assert model_calls(source) == ["client.messages.create", "self.client.messages.parse",
                                       "client.beta.messages.create"]
        assert client_builds(source) == ["anthropic.Anthropic"]

    def test_a_bare_name_walker_would_miss_them(self):
        # P6's test: {n.func.id for calls whose func is an ast.Name}.
        tree = ast.parse("client.messages.create(model=m)\npipeline.render(pack)\n")
        bare = {n.func.id for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert bare == set()


class TestTheBoundaries:
    def test_no_model_identifier_outside_config(self):
        holding = [p.relative_to(ROOT).as_posix() for p in _sources()
                   if p.name != "config.py" or p.parent != ROOT
                   if MODEL_ID.search(p.read_text(encoding="utf-8"))]
        assert holding == []

    def test_only_model_call_calls_the_client(self):
        callers = {p.relative_to(ROOT).as_posix() for p in _sources(CODE)
                   if model_calls(p.read_text(encoding="utf-8"))}
        assert callers == {"src/model_call.py"}

    def test_only_the_client_seam_builds_a_client(self):
        builders = {p.relative_to(ROOT).as_posix() for p in _sources(CODE)
                    if client_builds(p.read_text(encoding="utf-8"))}
        assert builders == {"streamlit_app/lib/claude_client.py"}


# -- Part 1c: DuckDB opens in one function; the pages import no pipeline module --

# Names on the duckdb module that open nothing: exception classes and types.
DUCKDB_HARMLESS = re.compile(r"^duckdb\.(\w*(Error|Exception)|DuckDBPyConnection|__version__)$")
PIPELINE = ("extract", "ledger", "long_format", "names", "warehouse", "batch")


def duckdb_uses(source: str) -> list[tuple[str, str]]:
    """(enclosing function, what) for every way source reaches DuckDB.

    Every attribute chain on the duckdb module is listed (called or not, so an
    alias such as `open_ = duckdb.connect` is seen), but exception classes and
    types; so is any `from duckdb import ...` and any `import duckdb as ...`.
    """
    found = []

    def walk(node, function):
        for child in ast.iter_child_nodes(node):
            inner = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                else function
            if isinstance(child, ast.ImportFrom) and (child.module or "").split(".")[0] == "duckdb":
                found.append((inner, f"from {child.module} import"))
            elif isinstance(child, ast.Import):
                for alias in child.names:
                    if alias.name.split(".")[0] == "duckdb" and alias.asname:
                        found.append((inner, f"import {alias.name} as {alias.asname}"))
            elif isinstance(child, ast.Attribute) and not isinstance(child.value, ast.Attribute):
                chain = _dotted(child)
                if chain.startswith("duckdb.") and not DUCKDB_HARMLESS.match(chain):
                    found.append((inner, chain))
            walk(child, inner)

    walk(ast.parse(source), "<module>")
    return found


def imports_of(source: str) -> set[str]:
    """Every module source imports, in any form, at any depth, as dotted names."""
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


class TestTheWarehouseBoundary:
    def test_the_walker_sees_every_way_in(self):
        source = ("import duckdb\nimport duckdb as db\nfrom duckdb import connect\n"
                  "def connect_(p):\n    return duckdb.connect(p)\n"
                  "def other():\n    opener = duckdb.connect\n    duckdb.sql('SELECT 1')\n"
                  "    duckdb.default_connection().execute('x')\n"
                  "    try:\n        pass\n    except duckdb.Error:\n        pass\n")
        assert duckdb_uses(source) == [
            ("<module>", "import duckdb as db"), ("<module>", "from duckdb import"),
            ("connect_", "duckdb.connect"), ("other", "duckdb.connect"), ("other", "duckdb.sql"),
            ("other", "duckdb.default_connection")]

    def test_duckdb_opens_in_one_function(self):
        uses = {p.relative_to(ROOT).as_posix(): duckdb_uses(p.read_text(encoding="utf-8"))
                for p in _sources(CODE)}
        assert {path: found for path, found in uses.items() if found} == {
            "src/warehouse.py": [("connect", "duckdb.connect")]}

    def test_only_the_warehouse_imports_duckdb(self):
        importers = {p.relative_to(ROOT).as_posix() for p in _sources(CODE)
                     if any(n.split(".")[0] == "duckdb"
                            for n in imports_of(p.read_text(encoding="utf-8")))}
        assert importers == {"src/warehouse.py"}

    def test_the_pages_import_no_pipeline_module_yet(self):
        # The pages stay stubs in this phase: nothing in streamlit_app reaches
        # the extractor, the readers, the allow-list or the warehouse, nor the
        # libraries they read files with.
        banned = {f"src.{m}" for m in PIPELINE} | {"openpyxl", "duckdb"}
        found = {}
        for path in _sources(("streamlit_app",)):
            names = imports_of(path.read_text(encoding="utf-8"))
            hits = {n for n in names if any(n == b or n.startswith(b + ".") for b in banned)}
            if hits:
                found[path.relative_to(ROOT).as_posix()] = sorted(hits)
        assert found == {}

    def test_the_import_reader_sees_every_form(self):
        for source in ("from src import extract\n", "import src.warehouse as w\n",
                       "from src.ledger import read_ledger\n",
                       "def f():\n    from src import names\n", "import openpyxl\n"):
            names = imports_of(source)
            assert any(n in names for n in ("src.extract", "src.warehouse", "src.ledger",
                                             "src.names", "openpyxl")), source
