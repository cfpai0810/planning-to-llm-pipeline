"""Stand-ins for the Anthropic client: no key, no network, every call recorded."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Optional

import anthropic
import httpx

import config

REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


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
