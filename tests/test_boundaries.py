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
