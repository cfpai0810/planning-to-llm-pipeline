"""Suite-wide rules: the network guard and the generated test count.

The network guard. The suite blocks every connection that leaves this machine,
including during imports and fixtures. Connections to localhost are allowed
for local stand-in servers. The guard is installed in pytest_configure, which
runs before any test module is imported, and a session-scoped autouse fixture
checks it is still in place for every test. It patches socket.socket.connect,
connect_ex, sendto and sendmsg, socket.create_connection and
socket.getaddrinfo: any destination other than loopback (127.0.0.0/8, ::1,
localhost) raises NetworkBlocked at once. A child process started by a test
gets its environment from subprocess_env(), never the parent's.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
COUNTS_FILE = ROOT / "generated" / "test_counts.json"


# -- The network guard ----------------------------------------------------------

class NetworkBlocked(RuntimeError):
    """A test tried to reach a destination outside this machine."""


_REAL: dict = {}


def is_loopback(host) -> bool:
    """True for localhost and loopback addresses; None means the local machine."""
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    host = str(host)
    if host.lower().rstrip(".") == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False


def _allowed(sock, address) -> bool:
    if getattr(socket, "AF_UNIX", None) is not None and sock.family == socket.AF_UNIX:
        return True                       # a local socket file never leaves the machine
    return isinstance(address, tuple) and bool(address) and is_loopback(address[0])


def _refuse(what: str, destination) -> None:
    raise NetworkBlocked(f"tests must not leave this machine: {what} to {destination!r} "
                         "was blocked (tests/conftest.py)")


def _connect(self, address):
    if not _allowed(self, address):
        _refuse("connect", address)
    return _REAL["connect"](self, address)


def _connect_ex(self, address):
    if not _allowed(self, address):
        _refuse("connect_ex", address)
    return _REAL["connect_ex"](self, address)


def _sendto(self, data, *args):
    if not _allowed(self, args[-1]):
        _refuse("sendto", args[-1])
    return _REAL["sendto"](self, data, *args)


def _sendmsg(self, buffers, *args):
    if len(args) >= 3 and args[2] is not None and not _allowed(self, args[2]):
        _refuse("sendmsg", args[2])
    return _REAL["sendmsg"](self, buffers, *args)


def _create_connection(address, *args, **kwargs):
    if not (isinstance(address, tuple) and address and is_loopback(address[0])):
        _refuse("create_connection", address)
    return _REAL["create_connection"](address, *args, **kwargs)


def _getaddrinfo(host, *args, **kwargs):
    if not is_loopback(host):
        _refuse("getaddrinfo", host)
    return _REAL["getaddrinfo"](host, *args, **kwargs)


_PATCHES = (
    (socket.socket, "connect", _connect),
    (socket.socket, "connect_ex", _connect_ex),
    (socket.socket, "sendto", _sendto),
    (socket.socket, "sendmsg", _sendmsg),
    (socket, "create_connection", _create_connection),
    (socket, "getaddrinfo", _getaddrinfo),
)


def guard_installed() -> bool:
    return all(getattr(owner, name) is patched for owner, name, patched in _PATCHES
               if hasattr(owner, name))


def install_guard() -> None:
    for owner, name, patched in _PATCHES:
        if not hasattr(owner, name):      # sendmsg does not exist on every platform
            continue
        current = getattr(owner, name)
        if current is not patched:
            _REAL[name] = current
            setattr(owner, name, patched)


def uninstall_guard() -> None:
    for owner, name, _ in _PATCHES:
        if name in _REAL:
            setattr(owner, name, _REAL.pop(name))


def pytest_configure(config):
    # Before collection, so test modules are imported under the guard.
    install_guard()


def pytest_unconfigure(config):
    uninstall_guard()


@pytest.fixture(scope="session", autouse=True)
def network_guard():
    """Holds the guard for the whole session, shared fixtures included."""
    install_guard()
    assert guard_installed()
    yield


def subprocess_env(**extra: str) -> dict:
    """An explicit environment for a child process: only what Python needs, and no key."""
    keep = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP",
            "TMPDIR", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "LANG", "LC_ALL")
    env = {name: os.environ[name] for name in keep if name in os.environ}
    env.update({"ANTHROPIC_API_KEY": "", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                "PYTHONDONTWRITEBYTECODE": "1"})
    env.update(extra)
    return env




# -- The generated test count -------------------------------------------------------
# After a full, passing run of the whole suite, generated/test_counts.json is
# written, and the README's count block is rendered from the same numbers, so
# the README never holds a typed count. Anything less than the whole suite, or
# any failure, writes nothing (item 56: P6 counted a run of one node id beside
# whole files as full). When tests are added or removed, the README test that
# compares the count with --collect-only fails; python -m pytest --update-counts
# then skips the checks of the committed count so the full run can write the
# new one, and the next plain run must pass (docs/releasing.md).

COUNT_START = ("<!-- test-count: written by tests/conftest.py after a full, passing run, "
               "from generated/test_counts.json; never typed -->")
COUNT_END = "<!-- /test-count -->"
COUNT_LINE = ("The suite has **{total} tests**, counted by its last full, passing run "
              "(`generated/test_counts.json`).")

_deselected: list = []


def pytest_addoption(parser):
    parser.addoption("--update-counts", action="store_true", dest="update_counts",
                     help="accept a changed test count: skip the checks of the committed "
                          "count so a full, passing run can write the new one")


def pytest_deselected(items):
    _deselected.extend(items)


def _asks_for_the_whole_suite(config) -> bool:
    invocation = Path(config.invocation_params.dir)
    if not config.args:
        return False
    for arg in config.args:
        if "::" in arg:
            return False
        if (invocation / arg).resolve() not in (ROOT, TESTS):
            return False
    return True


def partial_reasons(session, exitstatus) -> list[str]:
    """Why this run may not write the count; an empty list means a full, passing run."""
    option = session.config.option
    on_disk = {p.relative_to(ROOT).as_posix() for p in TESTS.glob("test_*.py")}
    collected = {Path(str(item.fspath)).relative_to(ROOT).as_posix() for item in session.items}
    checks = {
        "the run did not pass": exitstatus != 0 or session.testsfailed > 0,
        "--collect-only": bool(option.collectonly),
        "-k": bool(option.keyword),
        "-m": bool(option.markexpr),
        "--lf": bool(getattr(option, "lf", False)),
        "--sw": bool(getattr(option, "stepwise", False)),
        "deselected tests": bool(_deselected or getattr(option, "deselect", None)),
        "--ignore": bool(getattr(option, "ignore", None) or getattr(option, "ignore_glob", None)),
        "not the whole suite asked for": not _asks_for_the_whole_suite(session.config),
        "a test file not collected": collected != on_disk,
        "fewer tests run than collected": session.testscollected != len(session.items),
    }
    return [reason for reason, failed in checks.items() if failed]


def render_count_block(counts: dict) -> str:
    """The README's count block, between COUNT_START and COUNT_END."""
    return COUNT_LINE.format(total=counts["total"])


def _write_if_changed(path: Path, text: str) -> None:
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def pytest_sessionfinish(session, exitstatus):
    if partial_reasons(session, exitstatus):
        return
    by_file = Counter(Path(str(item.fspath)).relative_to(ROOT).as_posix() for item in session.items)
    by_class = Counter(item.cls.__name__ if item.cls else "(no class)" for item in session.items)
    counts = {
        "written_by": "tests/conftest.py, after a full, passing run of the whole suite",
        "total": len(session.items),
        "by_file": dict(sorted(by_file.items())),
        "by_class": dict(sorted(by_class.items())),
    }
    _write_if_changed(COUNTS_FILE, json.dumps(counts, indent=2) + "\n")
    readme = ROOT / "README.md"
    if readme.is_file():
        text = readme.read_text(encoding="utf-8")
        start, end = text.find(COUNT_START), text.find(COUNT_END)
        if 0 <= start < end:
            head = text[:start + len(COUNT_START)]
            _write_if_changed(readme, f"{head}\n{render_count_block(counts)}\n{text[end:]}")
