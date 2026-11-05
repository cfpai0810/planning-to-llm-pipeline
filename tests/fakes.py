"""Stand-ins for the Anthropic client: no key, no network, every call recorded."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Optional

import anthropic
import httpx

import config

REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
NOT_FOUND_BODY = {"type": "error", "error": {"type": "not_found_error",
                                             "message": f"model: {config.MODEL}"}}
# The API's own reply to a key whose account has no credit (finding 1).
CREDIT_MESSAGE = ("Your credit balance is too low to access the Anthropic API. Please go to "
                  "Plans & Billing to upgrade or purchase credits.")
CREDIT_BODY = {"type": "error", "error": {"type": "invalid_request_error",
                                          "message": CREDIT_MESSAGE}}


def status_error(cls, status: int, text: str = "stand-in failure") -> anthropic.APIStatusError:
    """An SDK error as the client raises it for an HTTP status."""
    return cls(text, response=httpx.Response(status, request=REQUEST), body=None)


def not_found() -> anthropic.NotFoundError:
    return status_error(anthropic.NotFoundError, 404, f"model: {config.MODEL}")


class FakeClient:
    """
    Stands in for anthropic.Anthropic. messages.create records its keyword
    arguments, then raises `error` or returns a reply holding `reply` as text
    (no text block at all when reply is None).
    """

    def __init__(self, reply: Optional[str] = "Ready.", error: Optional[BaseException] = None,
                 api_key: Optional[str] = None):
        self.reply, self.error, self.api_key = reply, error, api_key
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        content = [] if self.reply is None else [SimpleNamespace(type="text", text=self.reply)]
        return SimpleNamespace(content=content, model=kwargs.get("model"), stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=12, output_tokens=3))


class _StandInHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        api = self.server.stand_in
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        api.received.append({"path": self.path,
                             "headers": {k.lower(): v for k, v in self.headers.items()},
                             "body": json.loads(body)})
        reply = json.dumps(api.body).encode()
        self.send_response(api.status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *args):
        pass


class StandInAPI:
    """
    A local stand-in for the API, on loopback only (127.0.0.1, a free port).
    Every call is recorded and answered with one status and JSON body: 404,
    model not found, unless the test sets status and body. The real SDK is
    pointed at it through ANTHROPIC_BASE_URL, so no test leaves the machine.
    """

    def __init__(self, status: int = 404, body: Optional[dict] = None):
        self.status = status
        self.body = NOT_FOUND_BODY if body is None else body
        self.received: list[dict] = []
        self._server: Optional[ThreadingHTTPServer] = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    def __enter__(self) -> "StandInAPI":
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _StandInHandler)
        self._server.stand_in = self
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()
