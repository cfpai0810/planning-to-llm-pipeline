# =============================================================================
# src/model_call.py: the one place that calls the model
# =============================================================================
"""
Every model call in the project goes through call_model, which:

- builds the request from config.py (MODEL, or COMPARISON_MODEL for the
  Phase 4 run; EFFORT; MAX_TOKENS), with an optional JSON schema for
  structured output (output_config.format, json_schema), which Phase 3 uses;
- sends text only (the formatted figures and names a caller puts in the
  prompt), never a file or bytes;
- never sends temperature, top_p or top_k, which current models reject, and
  refuses a caller who asks for them before anything is sent;
- maps every API failure to its code (src/errors.py) and returns it in a
  typed result, never as an exception; an exception that is not the API's
  is a fault in this code and is raised as it is;
- records the model and the effort, which the audit keeps.

The client is injected (built per session from the visitor's own key in the
web app), so the tests use a fake one and no test reaches the network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from config import COMPARISON_MODEL, EFFORT, MAX_TOKENS, MODEL
from src import errors

SAMPLING = ("temperature", "top_p", "top_k")
EFFORTS = ("low", "medium", "high", "xhigh", "max")


class SamplingRefused(ValueError):
    """A caller asked for a sampling parameter, which current models reject."""


@dataclass(frozen=True)
class CallResult:
    """What a call came to: its text (and data, for a schema), or a failure code."""
    model: str
    effort: str
    text: Optional[str] = None
    data: Optional[dict] = None
    code: Optional[str] = None
    served_by: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None

    @property
    def ok(self) -> bool:
        return self.code is None

    def audit_record(self) -> dict:
        """The call as the audit keeps it: model, effort and outcome, never an exception."""
        return {"model": self.model, "effort": self.effort, "outcome": self.code or "ok",
                "served_by": self.served_by, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens}


def build_request(*, system: str, user: str, schema: Optional[dict] = None,
                  model: str = MODEL, effort: str = EFFORT) -> dict:
    """The keyword arguments for client.messages.create, from config and the caller's text."""
    output_config: dict[str, Any] = {"effort": effort}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    return {"model": model, "max_tokens": MAX_TOKENS, "system": system,
            "messages": [{"role": "user", "content": user}], "output_config": output_config}


def _refuse(extra: dict, system: Any, user: Any, model: str, effort: str) -> None:
    sampling = [name for name in SAMPLING if name in extra]
    if sampling:
        raise SamplingRefused(
            f"{', '.join(sampling)} cannot be sent: current models reject sampling "
            f"parameters. The call's behaviour is set by EFFORT in config.py.")
    if extra:
        raise TypeError(f"call_model() takes no argument {', '.join(sorted(extra))}")
    if not isinstance(system, str) or not isinstance(user, str):
        raise TypeError("the model is sent text only: formatted figures and names, never a file")
    if model not in (MODEL, COMPARISON_MODEL):
        raise ValueError("the model is MODEL or COMPARISON_MODEL from config.py")
    if effort not in EFFORTS:
        raise ValueError(f"effort is one of {', '.join(EFFORTS)}")


def call_model(client, *, system: str, user: str, schema: Optional[dict] = None,
               model: str = MODEL, effort: str = EFFORT, **extra) -> CallResult:
    """Call the model once and say what came of it. See the module's notes."""
    _refuse(extra, system, user, model, effort)
    request = build_request(system=system, user=user, schema=schema, model=model, effort=effort)
    try:
        response = client.messages.create(**request)
    except Exception as exc:
        code = errors.failure_code(exc)
        if code is None:
            raise
        return CallResult(model=model, effort=effort, code=code)

    usage = getattr(response, "usage", None)
    seen = dict(model=model, effort=effort, served_by=getattr(response, "model", None),
                input_tokens=getattr(usage, "input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None))
    text = next((block.text for block in response.content
                 if getattr(block, "type", None) == "text"), None)
    if not text:
        return CallResult(**seen, code=errors.UNREADABLE_REPLY)
    if schema is None:
        return CallResult(**seen, text=text)
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return CallResult(**seen, code=errors.UNREADABLE_REPLY)
    return CallResult(**seen, text=text, data=data)
