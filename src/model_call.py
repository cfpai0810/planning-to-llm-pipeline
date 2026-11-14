# =============================================================================
# src/model_call.py: the one place that calls the model
# =============================================================================
"""
Every model call in the project goes through call_model, which:

- builds the request from config.py (MODEL with EFFORT, or COMPARISON_MODEL
  with COMPARISON_EFFORT for the Phase 4 run; MAX_TOKENS), with an optional
  JSON schema for structured output (output_config.format, json_schema),
  which Phase 3 uses; an effort of None is left out of the request
  entirely, for a model that does not take one;
- sends text only (the formatted figures and names a caller puts in the
  prompt), never a file or bytes;
- never sends temperature, top_p or top_k, which current models reject, and
  refuses a caller who asks for them before anything is sent;
- maps every API failure to its code (src/errors.py) and returns it in a
  typed result, never as an exception; an exception that is not the API's
  is a fault in this code and is raised as it is;
- records the model and the effort, which the audit keeps.

Part 3b (the reviewer's Phase 3 brief, 4.4). A reply is read in this order.
Its stop reason first: refusal is model_refused, max_tokens is reply_cut_off
(fixed wording, no retry), and anything other than end_turn is
unreadable_reply, since no tool is ever offered. Only then is the text read:
the text blocks, in order, a thinking block passed over. Without a schema the
first text block is the text. With a schema the reply must hold exactly one
text block, and it must parse as a JSON object; a reply nested too deeply to
parse (a RecursionError) or a number too long to read is unreadable_reply,
never an exception. The result keeps the stop reason, the text as received
(every text block, joined in order, kept even when it does not parse or the
reply stopped early) and the request id the SDK read from the reply's
request-id header (None when absent); audit_record() carries them, with the
SDK's version.

This module makes one request per call and has no retry loop. The client is
injected (built per session from the visitor's own key in the web app), so
the tests use a fake one and no test reaches the network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

import anthropic

from config import COMPARISON_EFFORT, COMPARISON_MODEL, EFFORT, MAX_TOKENS, MODEL
from src import errors

SAMPLING = ("temperature", "top_p", "top_k")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
# The stop reasons with a code of their own; any other but end_turn is unreadable_reply.
STOPPED = {"refusal": errors.MODEL_REFUSED, "max_tokens": errors.REPLY_CUT_OFF}


class SamplingRefused(ValueError):
    """A caller asked for a sampling parameter, which current models reject."""


@dataclass(frozen=True)
class CallResult:
    """What a call came to: its text (and data, for a schema), or a failure code."""
    model: str
    effort: Optional[str]                  # None: the request carried no effort
    text: Optional[str] = None
    data: Optional[dict] = None
    code: Optional[str] = None
    served_by: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    stop_reason: Optional[str] = None
    raw_text: Optional[str] = None         # the text as received, parsed or not
    request_id: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.code is None

    def audit_record(self) -> dict:
        """The call as the audit keeps it: model, effort and outcome, never an exception."""
        return {"model": self.model, "effort": self.effort, "outcome": self.code or "ok",
                "stop_reason": self.stop_reason, "request_id": self.request_id,
                "served_by": self.served_by, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "raw_text": self.raw_text,
                "sdk_version": anthropic.__version__}


def build_request(*, system: str, user: str, schema: Optional[dict] = None,
                  model: str = MODEL, effort: Optional[str] = EFFORT) -> dict:
    """The keyword arguments for client.messages.create, from config and the caller's text."""
    output_config: dict[str, Any] = {}
    if effort is not None:
        output_config["effort"] = effort
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    request = {"model": model, "max_tokens": MAX_TOKENS, "system": system,
               "messages": [{"role": "user", "content": user}]}
    if output_config:
        request["output_config"] = output_config
    return request


def configured_effort(model: str) -> Optional[str]:
    """The effort config.py sets for a model: COMPARISON_EFFORT or EFFORT. None means none."""
    return COMPARISON_EFFORT if model == COMPARISON_MODEL else EFFORT


def _refuse(extra: dict, system: Any, user: Any, model: str, effort: Optional[str]) -> None:
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
    if effort is None:
        return
    if effort not in EFFORTS:
        raise ValueError(f"effort is one of {', '.join(EFFORTS)}")
    if configured_effort(model) is None:
        raise ValueError("COMPARISON_EFFORT is None in config.py: this model is sent no effort")


def _parsed(text: str) -> Optional[dict]:
    """The JSON object a text holds, or None: not JSON, not an object, nested too deeply to
    parse, or holding a number too long to read."""
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def call_model(client, *, system: str, user: str, schema: Optional[dict] = None,
               model: str = MODEL, effort: Optional[str] = None, **extra) -> CallResult:
    """
    Call the model once and say what came of it. See the module's notes. With
    no effort given, the model's own from config.py is used (configured_effort).
    """
    _refuse(extra, system, user, model, effort)
    effort = configured_effort(model) if effort is None else effort
    request = build_request(system=system, user=user, schema=schema, model=model, effort=effort)
    try:
        response = client.messages.create(**request)
    except Exception as exc:
        code = errors.failure_code(exc)
        if code is None:
            raise
        return CallResult(model=model, effort=effort, code=code)

    usage = getattr(response, "usage", None)
    stop_reason = getattr(response, "stop_reason", None)
    texts = [block.text for block in (getattr(response, "content", None) or ())
             if getattr(block, "type", None) == "text"]
    seen = dict(model=model, effort=effort, served_by=getattr(response, "model", None),
                input_tokens=getattr(usage, "input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None), stop_reason=stop_reason,
                raw_text="".join(texts) if texts else None,
                request_id=getattr(response, "_request_id", None))
    if stop_reason in STOPPED:
        return CallResult(**seen, code=STOPPED[stop_reason])
    if stop_reason != "end_turn" or not texts or not texts[0]:
        return CallResult(**seen, code=errors.UNREADABLE_REPLY)
    if schema is None:
        return CallResult(**seen, text=texts[0])
    data = _parsed(texts[0]) if len(texts) == 1 else None
    if data is None:
        return CallResult(**seen, code=errors.UNREADABLE_REPLY)
    return CallResult(**seen, text=texts[0], data=data)
