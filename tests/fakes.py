"""Stand-ins for the Anthropic client: no key, no network, every call recorded.

FakeClient is the recording fake (the reviewer's Phase 3 brief, section 8). It serves scripted
replies in order, each with its stop reason, usage and request id, and it fails the call itself
when a request carries anything the project never sends: a key outside REQUEST_KEYS, an
output_config key outside OUTPUT_CONFIG_KEYS, a max_tokens other than config.MAX_TOKENS, or a
model other than the one it allows (config.MODEL unless the test names another). Every test
that uses it is therefore also the test that no sampling parameter is sent.

StandInAPI is a local stand-in for the API on loopback, for the real SDK. It answers with one
status and body, or with a list of them served in order (the last repeated), and sends a
request-id header with each, as the API does: the SDK reads a reply's request id from it.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Callable, Optional

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
OVERLOADED_BODY = {"type": "error", "error": {"type": "overloaded_error",
                                              "message": "Overloaded"}}
BAD_REQUEST_BODY = {"type": "error", "error": {"type": "invalid_request_error",
                                               "message": "output_config: not accepted"}}

# What a request may hold (the reviewer's Phase 3 brief, section 8).
REQUEST_KEYS = ("model", "max_tokens", "system", "messages", "output_config")
OUTPUT_CONFIG_KEYS = ("effort", "format")
# A thinking block, as a reply may hold one before its text.
THINKING_BLOCK = {"type": "thinking", "thinking": "The question asks for one metric.",
                  "signature": "c3RhbmQtaW4gc2lnbmF0dXJl"}


def status_error(cls, status: int, text: str = "stand-in failure") -> anthropic.APIStatusError:
    """An SDK error as the client raises it for an HTTP status."""
    return cls(text, response=httpx.Response(status, request=REQUEST), body=None)


def not_found() -> anthropic.NotFoundError:
    return status_error(anthropic.NotFoundError, 404, f"model: {config.MODEL}")


class FakeClientRefused(AssertionError):
    """A request the recording fake refuses: it holds something the project never sends."""


@dataclass(frozen=True)
class Reply:
    """One scripted reply: its text as one text block (no block when None), or explicit
    blocks (mappings with a type) in place of the text; its stop reason, usage and request id;
    and the model it names (the request's model when None)."""
    text: Optional[str] = "Ready."
    stop_reason: Optional[str] = "end_turn"
    input_tokens: int = 12
    output_tokens: int = 3
    request_id: Optional[str] = "req_fake"
    blocks: Optional[tuple] = None
    served_by: Optional[str] = None


def request_problems(request: dict, model: str) -> list:
    """What a request holds that the project never sends; [] for a good one."""
    problems = [f"the key {key} is not sent" for key in request if key not in REQUEST_KEYS]
    output_config = request.get("output_config", {})
    if not isinstance(output_config, dict):
        problems.append("output_config is not a mapping")
    else:
        problems += [f"the output_config key {key} is not sent" for key in output_config
                     if key not in OUTPUT_CONFIG_KEYS]
    if request.get("max_tokens") != config.MAX_TOKENS:
        problems.append(f"max_tokens is {request.get('max_tokens')!r}, not config.MAX_TOKENS")
    if request.get("model") != model:
        problems.append(f"the model is {request.get('model')!r}, not the one this fake allows")
    return problems


def _response(reply: Reply, request: dict) -> SimpleNamespace:
    blocks = reply.blocks if reply.blocks is not None else (
        () if reply.text is None else ({"type": "text", "text": reply.text},))
    return SimpleNamespace(**{
        "content": [SimpleNamespace(**block) for block in blocks],
        "model": reply.served_by or request.get("model"), "stop_reason": reply.stop_reason,
        "usage": SimpleNamespace(input_tokens=reply.input_tokens,
                                 output_tokens=reply.output_tokens),
        "_request_id": reply.request_id})


class FakeClient:
    """
    Stands in for anthropic.Anthropic. messages.create records its keyword arguments, checks
    them (request_problems; any problem fails the call with FakeClientRefused), runs on_create
    if given, then answers: with the next of `replies` (a Reply, or an exception to raise) when
    a script is given, and fails the call when the script has run out; otherwise it raises
    `error`, or returns `reply` as text (no text block at all when reply is None).
    """

    def __init__(self, reply: Optional[str] = "Ready.", error: Optional[BaseException] = None,
                 api_key: Optional[str] = None, *, replies: Optional[list] = None,
                 model: str = config.MODEL, on_create: Optional[Callable] = None):
        self.reply, self.error, self.api_key = reply, error, api_key
        self.model, self.on_create = model, on_create
        self._script = None if replies is None else list(replies)
        self.calls: list[dict] = []
        self.refused: list[list] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        problems = request_problems(kwargs, self.model)
        if problems:
            self.refused.append(problems)
            raise FakeClientRefused("; ".join(problems))
        if self.on_create is not None:
            self.on_create(kwargs)
        if self._script is None:
            if self.error is not None:
                raise self.error
            return _response(Reply(self.reply), kwargs)
        if not self._script:
            raise FakeClientRefused("the script has no reply left: one call more than the "
                                    "test expected")
        reply = self._script.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return _response(reply, kwargs)


def message(text: Optional[str] = "Ready.", *, stop_reason: Optional[str] = "end_turn",
            blocks: Optional[list] = None, model: str = config.MODEL, input_tokens: int = 1079,
            output_tokens: int = 93) -> dict:
    """A Messages API reply body, as the API sends it: the text as one text block (none when
    text is None), or the blocks given."""
    content = list(blocks) if blocks is not None else (
        [] if text is None else [{"type": "text", "text": text}])
    return {"id": "msg_standin", "type": "message", "role": "assistant", "model": model,
            "content": content, "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}}


class _StandInHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        api = self.server.stand_in
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        with api.lock:
            api.received.append({"path": self.path,
                                 "headers": {k.lower(): v for k, v in self.headers.items()},
                                 "body": json.loads(body)})
            number = len(api.received)
            if api.replies:
                status, payload = api.replies[min(number, len(api.replies)) - 1]
            else:
                status, payload = api.status, api.body
        reply = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(reply)))
        self.send_header("request-id", f"req_standin_{number}")
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *args):
        pass


class StandInAPI:
    """
    A local stand-in for the API, on loopback only (127.0.0.1, a free port). Every call is
    recorded and answered with one status and JSON body (404, model not found, unless the test
    sets status and body), or, given replies, with each (status, body) in turn, the last one
    repeated. Each answer carries the header request-id: req_standin_1 for the first request,
    and so on. The real SDK is pointed at it through ANTHROPIC_BASE_URL or a client's base_url,
    so no test leaves the machine.
    """

    def __init__(self, status: int = 404, body: Optional[dict] = None, *,
                 replies: Optional[list] = None):
        self.status = status
        self.body = NOT_FOUND_BODY if body is None else body
        self.replies = None if replies is None else list(replies)
        self.received: list[dict] = []
        self.lock = threading.Lock()
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
