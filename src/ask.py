# =============================================================================
# src/ask.py: the one way in for a question in words
# =============================================================================
"""
The question path's door (the reviewer's Phase 3 brief, 4.1): the command
line uses it now and the pages will later, as the one pipeline module they
import.

    ask(client, session, role, question, *, audit, narrate="template")

returns an Asked: the run id; the result, an Answer or an Outcome (None when
a model call failed); the narration and its flags (part 3c fills them; none
yet); and the failure code (src/errors.py) when the model call failed or its
reply could not be used. The steps, in order:

1. the question (4.6): text that, after trimming, is 1 to 500 characters,
   with no control character but newline and tab, no format character
   (Unicode category Cf, the zero-width ones among them) and no surrogate
   (category Cs, which is not text and cannot be encoded to be sent).
   Anything else is question_invalid, before anything is sent. The trimmed
   text is the user message; the record keeps the question as received;
2. the role (roles.checked): a role of the wrong shape raises RoleError, a
   fault in the page's own code, before anything is read, sent or recorded,
   whatever the question. The provenance is read once, after this check;
3. the member lists (roles.members_for): nothing_promoted when nothing is;
4. the request: the schema and the prompt (src/prompt.py), from the
   registry and the versions the session keeps;
5. one call through model_call.call_model, whatever comes back: no retry;
6. the reply mapped: it must be an object of exactly refusal, specification
   and asks_for_a_cause, asks_for_a_cause true or false, exactly one of the
   other two not null, a refusal exactly {reason} with one of the four
   reasons (matched without regard to case), and a specification a JSON
   object. A refusal is its outcome; a specification goes to session.answer
   as the mapping it came as, where Phase 2 validates, compiles and runs it
   unchanged; anything else is unreadable_reply;
7. narration: part 3c;
8. the record, handed to the audit seam (an object whose one method,
   write(record), takes the finished record as a mapping) last of all;
9. the result.

It never raises for anything a visitor or the model can cause. A SessionError
from any session call (a closed session) is session_ended. If the record
cannot be written (an OSError from audit.write), the result is
audit_unavailable and no figure leaves. Every run is recorded, refusals and
failures included. Any other exception is a fault in the code and is raised.

The record (RECORD_FIELDS; part 3d closes the set and writes the line) holds
the run id, the time (UTC, to the second), the role (name, entity, view), the
question as received, the provenance (null when the session had already
ended; the answer's own when there is an answer, read under the same lock as
its query), the prompt's three hashes, the model calls (purpose, model,
effort, outcome, stop reason, request id, the model that served it, tokens
and the SDK's version), the specification call's text as received, the
canonical specification of an answer (or null), the outcome (a code, or
answered), the SQL and its hash, the result hash, the row count, truncation,
the coverage and gaps, the engine's message for an engine_error (never shown
to a reader), asks_for_a_cause, and the narration mode asked for. The parsed
reply is never in it: only the text as received and the validated
specification, so neither a question nor a reply can make it fail to encode.
Nothing in it is the key, a request header or an exception's class name.
The one text an exception gives that it keeps is the engine's message of an
engine_error, as engine_detail, for the audit alone; a failed model call is
kept as its code.

This module imports neither the database engine nor the query parser, and
calls the model through src/model_call.py alone (tests/test_boundaries.py).
"""

from __future__ import annotations

import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Union

from src import answer as answers
from src import model_call, outcomes, prompt, roles
from src.outcomes import Outcome
from src.session import SessionError

__all__ = ["MAX_QUESTION", "NARRATIONS", "RECORD_FIELDS", "Asked", "ask", "checked_question"]

MAX_QUESTION = 500
NARRATIONS = ("template", "model")
RECORD_FIELDS = ("run_id", "at", "role", "question", "provenance", "prompt", "calls",
                 "model_text_raw", "specification", "outcome", "sql", "sql_sha256",
                 "result_sha256", "row_count", "truncated", "coverage", "gaps", "engine_detail",
                 "asks_for_a_cause", "narration")
UNREADABLE = "unreadable_reply"


@dataclass(frozen=True)
class Asked:
    """What a question came to."""
    run_id: str
    result: Union[answers.Answer, Outcome, None]   # None only when failure is set
    narration: Optional[str] = None                # part 3c
    flags: tuple = ()                              # part 3c
    failure: Optional[str] = None                  # a code of src/errors.py


def checked_question(question) -> Optional[str]:
    """The question as it is sent, trimmed, or None when it fails the checks of 4.6."""
    if not isinstance(question, str):
        return None
    for character in question:
        category = unicodedata.category(character)
        if (category == "Cc" and character not in "\n\t") or category in ("Cf", "Cs"):
            return None
    trimmed = question.strip()
    return trimmed if 1 <= len(trimmed) <= MAX_QUESTION else None


def _reply(data: dict):
    """(kind, value, asks_for_a_cause) for a parsed reply: ("refusal", the outcome),
    ("specification", the mapping), or (None, None) when its shape is not the schema's."""
    if not isinstance(data, dict) or set(data) != set(prompt.REPLY_KEYS):
        return None, None, None
    cause, refusal, specification = (data["asks_for_a_cause"], data["refusal"],
                                     data["specification"])
    if not isinstance(cause, bool) or (refusal is None) == (specification is None):
        return None, None, None
    if refusal is not None:
        reason = refusal.get("reason") if isinstance(refusal, dict) and set(refusal) == {
            "reason"} else None
        found = next((r for r in prompt.REFUSAL_REASONS
                      if isinstance(reason, str) and reason.casefold() == r), None)
        return (None, None, cause) if found is None else ("refusal", outcomes.outcome(found),
                                                          cause)
    if not isinstance(specification, dict):
        return None, None, cause
    return "specification", specification, cause


def _canonical(spec) -> dict:
    """A validated specification as the record keeps it: canonical names, sorted keys."""
    return {"comparison": spec.comparison, "dimensions": list(spec.dimensions),
            "filters": [{"dimension": f.dimension, "values": list(f.values)}
                        for f in spec.filters],
            "limit": spec.limit, "metric": spec.metric,
            "period": {"kind": spec.period.kind, "value": spec.period.value},
            "sort": None if spec.sort is None else {"by": spec.sort.by,
                                                     "direction": spec.sort.direction},
            "version": spec.version}


def _call_entry(call: model_call.CallResult, purpose: str) -> dict:
    entry = {"purpose": purpose, **call.audit_record()}
    entry.pop("raw_text")                  # the specification call's text is model_text_raw
    return entry


def ask(client, session, role, question, *, audit, narrate: str = "template") -> Asked:
    """One question in words, answered under the role, recorded, and never raised for what a
    visitor or the model can cause. See the module's notes."""
    if narrate not in NARRATIONS:
        raise ValueError(f"narrate is one of {', '.join(NARRATIONS)}")
    sent = checked_question(question)                                   # step 1
    role = roles.checked(role)                                          # step 2
    run_id = uuid.uuid4().hex
    record = dict.fromkeys(RECORD_FIELDS)
    record.update(run_id=run_id, at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  role={"name": role.name, "entity": role.entity, "view": role.view()},
                  question=question if isinstance(question, str) else None, calls=[],
                  narration={"mode_asked": narrate})
    ended = getattr(session, "closed", False)
    if not ended:
        try:
            record["provenance"] = session.provenance(role)
        except SessionError:
            ended = True
    result: Union[answers.Answer, Outcome, None] = None
    failure: Optional[str] = None
    if sent is None:
        result = outcomes.QUESTION_INVALID
    elif ended:
        result = outcomes.SESSION_ENDED
    else:
        result, failure = _asked(client, session, role, sent, record)
    _finish(record, result, failure)
    try:                                                                # step 8
        audit.write(record)
    except OSError:
        return Asked(run_id, outcomes.AUDIT_UNAVAILABLE)
    return Asked(run_id, result, failure=failure)                       # step 9


def _asked(client, session, role, sent: str, record: dict):
    """Steps 3 to 6: (the result, the failure code), with what they found put in the record."""
    try:
        members = roles.members_for(role, session)                      # step 3
        dims = members if isinstance(members, Outcome) else session.dimensions()
        if isinstance(dims, Outcome):
            return dims, None
        loaded = session.registry()
    except SessionError:
        return outcomes.SESSION_ENDED, None
    made = prompt.build(loaded, members, dims.versions, sent)           # step 4
    record["prompt"] = made.hashes()
    call = model_call.call_model(client, system=made.system, user=sent,  # step 5
                                 schema=made.schema)
    record["calls"].append(_call_entry(call, "specification"))
    record["model_text_raw"] = call.raw_text
    if not call.ok:
        return None, call.code
    kind, value, cause = _reply(call.data)                              # step 6
    record["asks_for_a_cause"] = cause
    if kind is None:
        return None, UNREADABLE
    if kind == "refusal":
        return value, None
    details: list = []
    try:
        result = session.answer(value, role, engine_detail=details.append)
    except SessionError:
        return outcomes.SESSION_ENDED, None
    record["engine_detail"] = details[0] if details else None
    return result, None


def _finish(record: dict, result, failure: Optional[str]) -> None:
    """The outcome and, for an answer, what it holds."""
    if not isinstance(result, answers.Answer):
        record["outcome"] = failure if result is None else result.code
        return
    record.update(
        provenance=result.provenance, specification=_canonical(result.spec),
        outcome="answered" if result.outcome is None else result.outcome.code,
        sql=result.sql, sql_sha256=result.sql_sha256, result_sha256=result.result_sha256,
        row_count=result.row_count, truncated=result.truncated,
        coverage={"months_asked": list(result.coverage.months_asked),
                  "months_held": list(result.coverage.months_held),
                  "compared_with": result.coverage.compared_with},
        gaps=[list(gap) for gap in result.gaps])
