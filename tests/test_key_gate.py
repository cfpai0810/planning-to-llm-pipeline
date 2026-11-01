"""The key gate, the client seam and the on-screen failure line.

The gate is rendered once per app, by Home.py before the page runs, in a
closed sidebar expander with a live-mode line below it. The key is kept in
session state under a non-widget key, so it survives a page change (item 4).
Every sentence the gate says about the key is tested here (items 40 and 45),
with a distinctive stand-in key: a fake client, and the real SDK against a
local stand-in server that answers every call with "model not found".

The model call that shows the failure line on screen lives on the kit
gallery (P7_GALLERY=1): no page that ships makes a call in Phase 0, so the
proof needs no user-facing feature beyond the brief.
"""

import ast
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import anthropic
import pytest
from streamlit.testing.v1 import AppTest

import config
from src import errors
from streamlit_app.lib import claude_client, key_gate, nav, stand_in
from tests.fakes import FakeClient, not_found, status_error

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "streamlit_app"
GALLERY = "views/_gallery.py"
# Built from parts, so the whole key is written in no file: a scan for it
# finds only what the code under test wrote.
TEST_KEY = "-".join(["sk", "ant", "api03", "STANDIN", "q7Zr2KxW", "not", "a", "real", "key"])
SERVER_KEY = "-".join(["sk", "ant", "api03", "SERVER", "ENVIRONMENT", "never", "used"])
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "venv"}


def _home() -> AppTest:
    return AppTest.from_file(str(APP / "Home.py"), default_timeout=60).run()


def _with_key(at: AppTest, key: str = TEST_KEY) -> AppTest:
    at.sidebar.text_input(key=key_gate.WIDGET_KEY).input(key).run()
    return at


def _rendered(at: AppTest) -> str:
    """Everything the page shows as text: main area and sidebar, every kind of element."""
    texts = []
    for kind in ("title", "header", "subheader", "markdown", "caption", "text", "warning",
                 "error", "success", "info", "exception", "button", "expander"):
        for element in at.get(kind):
            texts.append(str(getattr(element, "value", "") or getattr(element, "label", "")))
    texts += [e.proto.body for e in at.get("html")]
    texts += [w.label for w in at.get("text_input")]
    return "\n".join(texts)


def _call_on_gallery(at: AppTest) -> AppTest:
    at.switch_page(GALLERY).run()
    at.button(key="gallery_call").click().run()
    assert not at.exception, at.exception
    return at


@pytest.fixture
def gallery(monkeypatch):
    monkeypatch.setenv("P7_GALLERY", "1")


@pytest.fixture
def fake_clients(monkeypatch):
    """build_client replaced: records the key each client was built from."""
    built = []

    def build(key, error=None):
        client = FakeClient(error=not_found(), api_key=key)
        built.append(client)
        return client

    monkeypatch.setattr(claude_client, "build_client", build)
    return built


class _StandInAPI(BaseHTTPRequestHandler):
    """A local stand-in for the API: every call is answered 404, model not found."""
    received: list = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        type(self).received.append({"path": self.path,
                                    "headers": {k.lower(): v for k, v in self.headers.items()},
                                    "body": json.loads(body)})
        reply = json.dumps({"type": "error", "error": {
            "type": "not_found_error", "message": f"model: {config.MODEL}"}}).encode()
        self.send_response(404)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *args):
        pass


@pytest.fixture
def stand_in_api(monkeypatch):
    _StandInAPI.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StandInAPI)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", SERVER_KEY)
    yield _StandInAPI.received
    server.shutdown()
    server.server_close()


@pytest.fixture
def every_log_line():
    """Every log record from every logger, at DEBUG, while the test runs."""
    records = []

    class Keep(logging.Handler):
        def emit(self, record):
            records.append(self.format(record))

    handler = Keep(level=logging.DEBUG)
    loggers = [logging.getLogger()] + [logging.getLogger(name)
                                       for name in list(logging.root.manager.loggerDict)]
    saved = [(lg, lg.level) for lg in loggers]
    for lg in loggers:
        lg.addHandler(handler)
        lg.setLevel(logging.DEBUG)
    yield records
    for lg, level in saved:
        lg.removeHandler(handler)
        lg.setLevel(level)


class TestTheGate:
    def test_the_shell_renders_the_gate_once_and_no_page_does(self):
        home = ast.parse((APP / "Home.py").read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(home) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == "render_key_gate"]
        assert len(calls) == 1
        for page in nav.pages({"P7_GALLERY": "1"}):
            assert "render_key_gate" not in (APP / "views" / page.file).read_text(encoding="utf-8")

    def test_every_page_has_exactly_one_gate_in_a_closed_expander(self, gallery):
        for page in nav.pages({"P7_GALLERY": "1"}):
            at = _home()
            if page.url_path:
                at.switch_page(f"views/{page.file}").run()
            assert not at.exception, (page.title, at.exception)
            assert len(at.sidebar.get("text_input")) == 1, page.title
            (expander,) = at.sidebar.get("expander")
            assert expander.label == key_gate.GATE_LABEL
            assert expander.proto.expanded is False

    def test_the_live_mode_line_says_whether_a_key_is_held(self):
        at = _home()
        assert key_gate.LIVE_OFF in [c.value for c in at.sidebar.caption]
        _with_key(at)
        assert key_gate.LIVE_ON in [c.value for c in at.sidebar.caption]

    def test_the_mistaken_paste_check_is_gentle(self):
        at = _with_key(_home(), "my key is hunter2")
        assert [w.value for w in at.sidebar.warning] == [key_gate.MISTAKEN_PASTE]
        assert not at.sidebar.error and key_gate.LIVE_OFF in [c.value for c in at.sidebar.caption]

    def test_a_complete_key_is_checked_on_first_use(self):
        at = _with_key(_home())
        assert [s.value for s in at.sidebar.success] == [key_gate.KEY_LOOKS_COMPLETE]


class TestItem4TheKeySurvivesNavigation:
    def test_a_key_entered_on_home_is_used_on_another_page(self, gallery, fake_clients):
        at = _with_key(_home())
        at.switch_page("views/2_Ask_the_plan.py").run()
        assert at.sidebar.text_input(key=key_gate.WIDGET_KEY).value == TEST_KEY
        _call_on_gallery(at)
        assert [c.api_key for c in fake_clients] == [TEST_KEY]

    def test_a_key_outlives_every_page(self, gallery):
        at = _with_key(_home())
        for page in nav.pages({"P7_GALLERY": "1"})[1:]:
            at.switch_page(f"views/{page.file}").run()
            assert at.sidebar.text_input(key=key_gate.WIDGET_KEY).value == TEST_KEY, page.title
            assert key_gate.LIVE_ON in [c.value for c in at.sidebar.caption]

    def test_a_key_removed_on_one_page_is_gone_on_home(self):
        at = _with_key(_home())
        at.switch_page("views/3_Your_own_data.py").run()
        at.sidebar.text_input(key=key_gate.WIDGET_KEY).input("").run()
        at.switch_page("views/home.py").run()
        assert at.sidebar.text_input(key=key_gate.WIDGET_KEY).value == ""
        assert key_gate.LIVE_OFF in [c.value for c in at.sidebar.caption]


class TestBrief41AFailedCallIsVisible:
    def test_a_missing_model_shows_its_line_and_code_on_screen(self, gallery, fake_clients):
        at = _call_on_gallery(_with_key(_home()))
        assert [w.value for w in at.warning] == [errors.line("model_not_found")]
        assert "Code: model_not_found" in at.warning[0].value
        assert "NotFoundError" not in _rendered(at)

    def test_without_a_key_there_is_no_call_to_make(self, gallery):
        at = _home()
        at.switch_page(GALLERY).run()
        assert at.button(key="gallery_call").disabled
        assert not at.warning


class TestEveryKeySentence:
    """Items 40 and 45: each sentence the gate says about the key, tested as written."""

    def test_the_gate_says_exactly_these_sentences(self):
        assert key_gate.KEY_SENTENCES == (
            "Your key is held in the server's memory for this session only.",
            "It is used to make the call, and it is never stored or logged.",
            "Only the formatted figures and names go to the model, never your files.",
            "A model run uses your own key, so your Anthropic account is billed for it.",
        )
        at = _home()
        assert " ".join(key_gate.KEY_SENTENCES) in [c.value for c in at.sidebar.caption]

    def test_held_in_the_servers_memory_for_this_session_only(self, gallery, fake_clients):
        at = _call_on_gallery(_with_key(_home()))
        held = [k for k, v in at.session_state.filtered_state.items() if v == TEST_KEY]
        assert sorted(held) == sorted([key_gate.KEPT_KEY, key_gate.WIDGET_KEY])
        assert TEST_KEY not in os.environ.values()
        fresh = _home()
        assert fresh.sidebar.text_input(key=key_gate.WIDGET_KEY).value == ""
        assert key_gate.KEPT_KEY not in fresh.session_state

    def test_used_to_make_the_call_by_the_real_client_and_nothing_else(self, gallery,
                                                                       stand_in_api):
        # The real SDK, against a local stand-in: the request carries the
        # visitor's key, never the key in the server's own environment, and so
        # the visitor's account is the one billed.
        at = _call_on_gallery(_with_key(_home()))
        (request,) = stand_in_api
        assert request["path"] == "/v1/messages"
        assert request["headers"].get("x-api-key") == TEST_KEY
        assert SERVER_KEY not in json.dumps(request)
        assert [w.value for w in at.warning] == [errors.line("model_not_found")]

    def test_the_client_is_built_only_from_the_pasted_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", SERVER_KEY)
        client = claude_client.build_client(f"  {TEST_KEY}  ")
        assert isinstance(client, anthropic.Anthropic) and client.api_key == TEST_KEY

    def test_never_stored_or_logged(self, gallery, stand_in_api, every_log_line):
        at = _call_on_gallery(_with_key(_home()))
        assert stand_in_api and every_log_line            # a call was made, and logged
        assert not [line for line in every_log_line if TEST_KEY in line]
        stored = [p for p in ROOT.rglob("*") if p.is_file()
                  and not set(p.relative_to(ROOT).parts) & SKIP_DIRS
                  and TEST_KEY.encode() in p.read_bytes()]
        assert stored == []
        assert TEST_KEY not in _rendered(at)
        for module in ("key_gate.py", "claude_client.py", "errors.py"):
            source = (APP / "lib" / module).read_text(encoding="utf-8")
            assert not any(w in source for w in ("open(", "write_text", "write_bytes",
                                                 "json.dump", "pickle", "shelve", "logging"))

    def test_only_the_formatted_figures_and_names_go_to_the_model(self, gallery,
                                                                  stand_in_api):
        _call_on_gallery(_with_key(_home()))
        (request,) = stand_in_api
        body = request["body"]
        assert body["messages"] == [{"role": "user", "content": stand_in.prompt()}]
        assert body["system"] == stand_in.SYSTEM
        assert stand_in.prompt() == "\n".join(f"{name}: {figure}"
                                              for name, figure in stand_in.FIGURES)
        assert not {"temperature", "top_p", "top_k"} & set(body)

    def test_no_message_carries_the_key(self, gallery, monkeypatch):
        # An SDK error whose own text holds the key: the page shows the plain
        # line and the code, never the error's text.
        leak = status_error(anthropic.AuthenticationError, 401, f"invalid x-api-key {TEST_KEY}")
        monkeypatch.setattr(claude_client, "build_client",
                            lambda key: FakeClient(error=leak, api_key=key))
        at = _call_on_gallery(_with_key(_home()))
        assert [w.value for w in at.warning] == [errors.line("key_rejected")]
        assert TEST_KEY not in _rendered(at) and "AuthenticationError" not in _rendered(at)
