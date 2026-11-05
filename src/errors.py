# =============================================================================
# src/errors.py: one short code and one plain line for every failed model call
# =============================================================================
"""
A failed call is worded here, once, so the screen, the PDF and the audit say
the same thing: a plain line that ends with a short code. The audit stores
the code; no exception's class name or text reaches a reader. This module
imports only anthropic and config, so the command line and the tests load it
without Streamlit (streamlit_app/lib/errors.py is the pages' thin wrapper).

failure_code follows an exception's __cause__ and __context__, so an API
error that other code wrapped, or one raised while handling another, still
maps. An exception that is not the API's has no code: it is a fault in this
code and is raised as it is.

An account with no credit is billing_problem, not a fault in this app
(Phase 0 verdict, finding 1): a 402, and a 400 whose message names the
credit balance, in any case. The message is read from the error's body, or
its text when the body has none, and never shown.

Phase 3 adds two outcomes with fixed wording, for a refusal and for a reply
cut off at max_tokens. They are outcomes of a call that succeeded, so they
join CODES and MESSAGES there; nothing here needs to change shape.
"""

from __future__ import annotations

from typing import Optional

import anthropic

from config import MODEL

KEY_REJECTED = "key_rejected"
KEY_FORBIDDEN = "key_forbidden"
MODEL_NOT_FOUND = "model_not_found"
BAD_REQUEST = "bad_request"
RATE_LIMITED = "rate_limited"
SERVICE_UNAVAILABLE = "service_unavailable"
UNREADABLE_REPLY = "unreadable_reply"
BILLING_PROBLEM = "billing_problem"

CODES = (KEY_REJECTED, KEY_FORBIDDEN, MODEL_NOT_FOUND, BAD_REQUEST, RATE_LIMITED,
         SERVICE_UNAVAILABLE, UNREADABLE_REPLY, BILLING_PROBLEM)

# The series wording (P1 to P6), one line per code.
MESSAGES = {
    KEY_REJECTED: "Anthropic did not accept this key. Check that it was copied in full and "
                  "has not been deleted or expired.",
    KEY_FORBIDDEN: "This key is not allowed to use the model. Check the key's permissions in "
                   "your Anthropic account.",
    MODEL_NOT_FOUND: "The model {model} could not be found. It may have been retired. Please "
                     "report this on the project's GitHub page.",
    BAD_REQUEST: "Anthropic rejected the request. This is most often a fault in this app, so "
                 "please report it. If your Anthropic account has run out of credit, that can "
                 "cause it too.",
    RATE_LIMITED: "Anthropic is limiting requests on this key. Wait a minute and try again, "
                  "or check your plan limits.",
    SERVICE_UNAVAILABLE: "Anthropic could not be reached or is busy. This is usually "
                         "temporary, so try again in a moment.",
    UNREADABLE_REPLY: "The model replied, but not in the form this app asked for.",
    BILLING_PROBLEM: "Anthropic reports a problem with billing on this account. Check your plan "
                     "and credits in your Anthropic account.",
}

_BY_CLASS = (
    (anthropic.AuthenticationError, KEY_REJECTED),
    (anthropic.PermissionDeniedError, KEY_FORBIDDEN),
    (anthropic.NotFoundError, MODEL_NOT_FOUND),
    (anthropic.RateLimitError, RATE_LIMITED),
    (anthropic.APIConnectionError, SERVICE_UNAVAILABLE),     # a timeout included
    (anthropic.APIResponseValidationError, UNREADABLE_REPLY),
)
# Statuses the SDK retries before giving up: a lock or a timeout, not a fault.
_TRANSIENT = {408, 409}
_PAYMENT_REQUIRED = 402
_CREDIT = "credit balance"


def _names_the_credit_balance(exc: anthropic.APIStatusError) -> bool:
    """Whether the API's own message names the credit balance. Read here, never shown."""
    body = exc.body
    error = body.get("error") if isinstance(body, dict) else None
    said = (error.get("message") if isinstance(error, dict) else None,
            body if isinstance(body, str) else None, exc.message)
    return any(isinstance(text, str) and _CREDIT in text.casefold() for text in said)


def _code_of(exc: BaseException) -> Optional[str]:
    for cls, code in _BY_CLASS:
        if isinstance(exc, cls):
            return code
    if isinstance(exc, anthropic.APIStatusError):
        status = exc.status_code
        if status == _PAYMENT_REQUIRED or (status == 400 and _names_the_credit_balance(exc)):
            return BILLING_PROBLEM
        return SERVICE_UNAVAILABLE if status >= 500 or status in _TRANSIENT else BAD_REQUEST
    return None


def failure_code(exc: Optional[BaseException]) -> Optional[str]:
    """The code of a failed call, or None when no exception in the chain is the API's."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        code = _code_of(exc)
        if code:
            return code
        exc = exc.__cause__ or exc.__context__
    return None


def message(code: str) -> str:
    """The plain line for a code, without the code. An unknown code is a KeyError."""
    return MESSAGES[code].format(model=MODEL)


def line(code: str) -> str:
    """The one line the screen, the PDF and the audit show: the message, then the code."""
    return f"{message(code)} Code: {code}"
