"""The network guard: a connection that leaves this machine fails at once.

The guard is installed when pytest starts (tests/conftest.py), before any test
module is imported, and a session-scoped fixture holds it to the end. These
tests prove each claim the README makes: a public destination fails, loopback
stays open, and the guard is already in place while this module is imported
and while a module-scoped fixture runs.
"""

import socket
import threading
from pathlib import Path

import pytest

from tests import conftest

ROOT = Path(__file__).resolve().parent.parent

# TEST-NET-1 (RFC 5737) is never routed, so without the guard an attempt times
# out rather than reaching anything. The name is under .invalid (RFC 2606).
PUBLIC = ("192.0.2.10", 443)
PUBLIC_NAME = "planning-to-llm.invalid"
README_SENTENCE = (
    "The suite blocks every connection that leaves this machine, including during "
    "imports and fixtures. Connections to localhost are allowed for local stand-in servers."
)


def _attempt(action):
    """Run action; return the exception it raised, or None."""
    try:
        action()
    except Exception as error:  # the test inspects what was raised
        return error
    return None


def _create_connection():
    socket.create_connection(PUBLIC, timeout=0.5).close()


# Attempted while pytest imports this module, before any fixture or test runs.
AT_IMPORT = _attempt(_create_connection)


@pytest.fixture(scope="module")
def module_attempt():
    """A shared fixture that tries to leave the machine once for the whole module."""
    return _attempt(_create_connection)


def _connect():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        sock.connect(PUBLIC)


def _connect_ex():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        if sock.connect_ex(PUBLIC) != 0:
            raise OSError("connect_ex returned an error code")


def _sendto():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(b"x", (PUBLIC[0], 9))


def _getaddrinfo():
    socket.getaddrinfo(PUBLIC_NAME, 443)


# The older resolver functions do not go through getaddrinfo (finding 1).
def _gethostbyname():
    socket.gethostbyname(PUBLIC_NAME)


def _gethostbyname_ex():
    socket.gethostbyname_ex(PUBLIC_NAME)


def _gethostbyaddr():
    socket.gethostbyaddr(PUBLIC[0])


def _echo_once(family, host):
    """Start a one-shot echo listener on host; return its port and thread."""
    server = socket.socket(family, socket.SOCK_STREAM)
    server.bind((host, 0))
    server.listen(1)
    port = server.getsockname()[1]

    def serve():
        with server:
            conn, _ = server.accept()
            with conn:
                conn.sendall(conn.recv(16))

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return port, thread


class TestNetworkGuard:
    @pytest.mark.parametrize("action", [_connect, _connect_ex, _create_connection, _sendto,
                                        _getaddrinfo, _gethostbyname, _gethostbyname_ex,
                                        _gethostbyaddr], ids=lambda a: a.__name__.lstrip("_"))
    def test_a_public_destination_fails_at_once(self, action):
        assert isinstance(_attempt(action), conftest.NetworkBlocked)

    def test_loopback_stays_open(self):
        # The guard is on (a public address fails) and loopback still works,
        # by address and by the name localhost.
        assert isinstance(_attempt(_connect), conftest.NetworkBlocked)
        for host in ("127.0.0.1", "localhost"):
            port, thread = _echo_once(socket.AF_INET, "127.0.0.1")
            with socket.create_connection((host, port), timeout=5) as client:
                client.sendall(b"stand-in")
                assert client.recv(16) == b"stand-in"
            thread.join(5)

    def test_a_loopback_name_still_resolves(self):
        assert isinstance(_attempt(_gethostbyname), conftest.NetworkBlocked)
        assert socket.gethostbyname("localhost").startswith("127.")
        assert socket.gethostbyname_ex("localhost")[2]
        assert not isinstance(_attempt(lambda: socket.gethostbyaddr("127.0.0.1")),
                              conftest.NetworkBlocked)

    def test_ipv6_loopback_stays_open(self):
        assert isinstance(_attempt(_connect), conftest.NetworkBlocked)
        try:
            port, thread = _echo_once(socket.AF_INET6, "::1")
        except OSError:
            pytest.skip("this machine has no IPv6 loopback")
        with socket.create_connection(("::1", port), timeout=5) as client:
            client.sendall(b"v6")
            assert client.recv(16) == b"v6"
        thread.join(5)

    def test_the_guard_covers_a_module_scoped_fixture(self, module_attempt):
        assert isinstance(module_attempt, conftest.NetworkBlocked)

    def test_the_guard_covers_imports(self):
        assert isinstance(AT_IMPORT, conftest.NetworkBlocked)

    def test_the_readme_states_the_guard_exactly(self):
        readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
        assert README_SENTENCE in readme
