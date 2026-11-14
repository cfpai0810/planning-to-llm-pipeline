# =============================================================================
# src/audit.py: the record of every question, and its replay
# =============================================================================
"""
The audit and replay (the reviewer's Phase 3 brief, section 6, and the Stop 1
verdict, section 7).

The file. One JSON Lines file, audit.jsonl, append-only, in a folder the
caller gives (the command line's default will be audit_log/ in the current
directory, DEFAULT_FOLDER); the writer makes the folder, and any folder above
it, when it is missing. Each question writes one line, after everything else
has finished: src/ask.py hands its finished record to AuditLog.write, and an
OSError from making the folder, opening the file or writing it is ask's
audit_unavailable, with no figure and no narration shown. A lock covers the
threads of one process; two processes writing one file are not coordinated.
On a host whose disk is temporary the record lasts until the host restarts;
the Release phase decides where it lives.

The line. The canonical JSON of the record with its record_sha256: sorted
keys, separators "," and ":" with no spaces, ASCII escapes only (so a lone
surrogate, from a question or from a model's reply, is written as an escape
and never fails to encode, F29), no NaN or infinity, then one newline. The
record_sha256 is the SHA-256 of the canonical JSON of the record without that
field. The line is written as bytes in one write, so it holds no carriage
return on any system. The record is JSON-native: an answer's rows are not in
it, only their hash.

The closed set. A record's fields are ask.RECORD_FIELDS, and a line's are
those and record_sha256 (FIELDS). The mappings the door builds inside a
record are closed sets too: the role (ROLE_FIELDS), the prompt's three
hashes (PROMPT_FIELDS, or null), each model call (CALL_FIELDS, which holds no
text of the reply), the coverage (COVERAGE_FIELDS, or null), the narration
(NARRATION_FIELDS: the mode asked for and the mode shown, the text shown,
the model's own text when it was withheld, the flags and the failure code)
and each of its flags (FLAG_FIELDS: kind, text, figure and reason). The
provenance and the specification are other modules' shapes, kept as they
are. The writer refuses a record with a field missing or another field, at
the top or in one of those mappings, with RecordError: a fault in the code
that built it, never an OSError, so ask raises it. NaN and a value that is
not JSON (a Decimal) are faults too.

verify(path) reads every line and reports, never raises, what is wrong with
each, by a code of PROBLEMS: a last line with no newline (torn), an empty
line, a line that is not UTF-8, not JSON (too deep, a number too long) or not
an object, that holds a key twice (which the JSON reader would keep the last
of), that lacks a field or holds another, a mapping inside that is not its
closed set, a record_sha256 that does not match what the line holds, or bytes
that are not the canonical form of what they hold. The hash and the bytes
together catch any change to a line, including those that parse to the same
record (a space added, an escape for a plain letter, keys reordered) and
those that parse to another (1.0 for 1). verify detects an altered line, not
a deleted one: a line taken out of the file leaves no trace. Nor is the hash
a signature: it has no key, so whoever can write the file can rewrite a line
and recompute its hash. The audit is detective, not preventive. read(path)
gives the records of a file that verifies, and raises AuditError for one
that does not.

replay(record, session) makes no model call and does not narrate. It gives a
Replayed in one of four states (STATES):
- not_replayable, with the reason (the record's outcome), for a record that
  holds no specification: a refusal, a failure, question_invalid,
  session_ended, nothing_promoted, or a specification that validation
  refused. The session is not touched;
- not_comparable, naming the kinds that differ (KINDS: plan_file and
  ledger_file, the hashes of the planning workbook and of the ledger
  extract; registry; role_view; never a member), when the session's
  provenance under the record's role holds another planning workbook or
  ledger extract hash or another registry hash, or when the role rebuilt
  from the record's name and entity (roles.checked) is not of the two
  shapes or its view is not the record's. Nothing is asked;
- same, when session.answer, asked the record's canonical specification
  under that role, gives the record's outcome, SQL hash and result hash (a
  null equals a null, so an engine_error that fails the same way is same);
- different otherwise, with both sets of values.
A different engine version is reported (Replayed.engine), not refused. A
closed session raises SessionError, as the session does.

This module imports neither the database engine nor the query parser, and
calls no model (tests/test_boundaries.py).
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src import answer as answers
from src import ask, roles

__all__ = ["CALL_FIELDS", "COVERAGE_FIELDS", "DEFAULT_FOLDER", "FIELDS", "FILE_NAME",
           "FLAG_FIELDS", "HASH_FIELD", "KINDS", "NARRATION_FIELDS", "PROBLEMS", "PROMPT_FIELDS",
           "ROLE_FIELDS", "STATES", "AuditError", "AuditLog", "Problem", "RecordError",
           "Replayed", "Verified", "canonical", "line_of", "line_problems", "read", "replay",
           "verify"]

FILE_NAME = "audit.jsonl"
DEFAULT_FOLDER = "audit_log"
HASH_FIELD = "record_sha256"
FIELDS = ask.RECORD_FIELDS + (HASH_FIELD,)
ROLE_FIELDS = ("name", "entity", "view")
PROMPT_FIELDS = ("system_sha256", "schema_sha256", "request_sha256")
CALL_FIELDS = ("purpose", "model", "effort", "outcome", "stop_reason", "request_id",
               "served_by", "input_tokens", "output_tokens", "sdk_version")
COVERAGE_FIELDS = ("months_asked", "months_held", "compared_with")
NARRATION_FIELDS = ("mode_asked", "mode_shown", "text_shown", "model_text", "flags", "failure")
FLAG_FIELDS = ("kind", "text", "figure", "reason")

PROBLEMS = {
    "torn": "The line has no newline at its end: the file was cut while it was written.",
    "empty": "The line is empty.",
    "not_utf8": "The line is not UTF-8 text.",
    "not_json": "The line is not JSON that can be read.",
    "not_an_object": "The line is JSON but not an object.",
    "repeated_key": "The line holds a key twice.",
    "missing_field": "The line lacks a field of the record.",
    "unknown_field": "The line holds a field that is not one of the record's.",
    "inner_field": "A mapping inside the line lacks a key of its own or holds another.",
    "hash": "The line's record_sha256 is not the hash of what the line holds.",
    "not_canonical": "The line's bytes are not the canonical form of what they hold.",
}

STATES = ("same", "different", "not_comparable", "not_replayable")
KINDS = ("plan_file", "ledger_file", "registry", "role_view")
_COMPARED = (("plan_file", "plan_file_sha256"), ("ledger_file", "ledger_file_sha256"),
             ("registry", "registry_hash"))

_WRITING = threading.Lock()        # one line at a time, from every thread of the process


class RecordError(ValueError):
    """A record that is not of the closed set: a fault in the code that built it."""


class AuditError(ValueError):
    """An audit file that does not verify, told in one fixed sentence."""


# == The line ======================================================================================

def canonical(value) -> str:
    """The canonical JSON text: sorted keys, no spaces, ASCII escapes only, no NaN."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False)


def _sha256(record: dict) -> str:
    return hashlib.sha256(canonical(record).encode("ascii")).hexdigest()


def _closed(value, fields: tuple, *, nullable: bool = False) -> bool:
    if value is None:
        return nullable
    return isinstance(value, dict) and set(value) == set(fields)


def _inner_closed(record: dict) -> bool:
    """Whether every mapping the door builds inside the record is its closed set."""
    calls, narration = record.get("calls"), record.get("narration")
    return (_closed(record.get("role"), ROLE_FIELDS)
            and _closed(record.get("prompt"), PROMPT_FIELDS, nullable=True)
            and _closed(record.get("coverage"), COVERAGE_FIELDS, nullable=True)
            and isinstance(calls, list) and all(_closed(c, CALL_FIELDS) for c in calls)
            and _closed(narration, NARRATION_FIELDS)
            and isinstance(narration["flags"], list)
            and all(_closed(f, FLAG_FIELDS) for f in narration["flags"]))


def _field_problems(record: dict, fields: tuple) -> list:
    found = []
    if set(fields) - set(record):
        found.append("missing_field")
    if set(record) - set(fields):
        found.append("unknown_field")
    if not found and not _inner_closed(record):
        found.append("inner_field")
    return found


def line_of(record) -> bytes:
    """The record's line: the canonical JSON of it with its record_sha256, and one newline.
    A record that is not the closed set raises RecordError; NaN raises ValueError and a value
    that is not JSON TypeError: each a fault in the code, never an OSError."""
    if not isinstance(record, dict):
        raise RecordError("A record is a mapping of the closed set of fields.")
    problems = _field_problems(record, FIELDS[:-1])
    if problems:
        raise RecordError(" ".join(PROBLEMS[p] for p in problems))
    return (canonical({**record, HASH_FIELD: _sha256(record)}) + "\n").encode("ascii")


class AuditLog:
    """The audit seam ask writes to: audit.jsonl in the folder given, made when missing."""

    def __init__(self, folder: Path | str):
        self.folder = Path(folder)
        self.path = self.folder / FILE_NAME

    def write(self, record) -> None:
        """Appends the record's line in one write, under the lock. The line is built first, so
        a fault in the record raises before anything is opened; an OSError (the folder or the
        file cannot be made or written) is the caller's audit_unavailable."""
        line = line_of(record)
        with _WRITING:
            self.folder.mkdir(parents=True, exist_ok=True)
            with open(self.path, "ab") as file:
                file.write(line)


# == Verifying and reading =========================================================================

@dataclass(frozen=True)
class Problem:
    line: int                       # counted from 1
    code: str                       # a key of PROBLEMS

    @property
    def sentence(self) -> str:
        return PROBLEMS[self.code]


@dataclass(frozen=True)
class Verified:
    lines: int
    problems: tuple                 # Problem, in line order
    records: tuple                  # the record of each line with no problem, in order

    @property
    def ok(self) -> bool:
        return not self.problems


class _Repeated(Exception):
    pass


def _no_repeats(pairs: list) -> dict:
    if len({key for key, _ in pairs}) != len(pairs):
        raise _Repeated
    return dict(pairs)


def _parsed(line: bytes):
    """(the record, the problem codes) of one line without its newline."""
    if not line:
        return None, ("empty",)
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError:
        return None, ("not_utf8",)
    try:
        record = json.loads(text, object_pairs_hook=_no_repeats)
    except _Repeated:
        return None, ("repeated_key",)
    except (ValueError, RecursionError):
        return None, ("not_json",)
    if not isinstance(record, dict):
        return None, ("not_an_object",)
    found = _field_problems(record, FIELDS)
    body = {k: v for k, v in record.items() if k != HASH_FIELD}
    try:
        again = canonical(record).encode("ascii")
        if HASH_FIELD in record and record[HASH_FIELD] != _sha256(body):
            found.append("hash")
    except (ValueError, RecursionError):
        again = None
    if again != line:
        found.append("not_canonical")
    return record, tuple(found)


def line_problems(line: bytes) -> tuple:
    """The problem codes of one line, given without its newline; () for a good line."""
    return _parsed(line)[1]


def verify(path: Path | str) -> Verified:
    """Every line of the file checked, and every problem reported: see the module's notes.
    Nothing a line holds is raised; a file that cannot be opened raises its OSError."""
    problems, records, count = [], [], 0
    with open(path, "rb") as file:
        for count, raw in enumerate(file, start=1):
            torn = not raw.endswith(b"\n")
            record, found = _parsed(raw if torn else raw[:-1])
            found = (("torn",) if torn else ()) + found
            problems += [Problem(count, code) for code in found]
            if not found:
                records.append(record)
    return Verified(count, tuple(problems), tuple(records))


def read(path: Path | str) -> tuple:
    """The records of a file that verifies, in order; AuditError when any line does not."""
    verified = verify(path)
    if not verified.ok:
        failing = len({p.line for p in verified.problems})
        raise AuditError(f"The audit file does not verify: {failing} of its {verified.lines} "
                         "lines fail.")
    return verified.records


# == Replay ========================================================================================

@dataclass(frozen=True)
class Replayed:
    state: str                          # one of STATES
    reason: Optional[str] = None        # not_replayable: the record's outcome
    differs: tuple = ()                 # not_comparable: the kinds that differ, from KINDS
    recorded: Optional[dict] = None     # same and different: the record's outcome and hashes
    replayed: Optional[dict] = None     # same and different: the replay's
    engine: Optional[tuple] = None      # (the record's engine, the session's) when they differ


def _role(given) -> Optional[roles.Role]:
    """The role rebuilt from the record's name and entity, or None when it is no role."""
    if not isinstance(given, dict):
        return None
    try:
        return roles.checked(roles.Role(given.get("name"), given.get("entity")))
    except roles.RoleError:
        return None


def _engine(provenance: dict) -> str:
    return f"{provenance.get('engine')} {provenance.get('engine_version')}"


def _values(result, handed: list) -> dict:
    """The outcome and the two hashes of what the session gave."""
    if isinstance(result, answers.Answer):
        return {"outcome": "answered" if result.outcome is None else result.outcome.code,
                "sql_sha256": result.sql_sha256, "result_sha256": result.result_sha256}
    return {"outcome": result.code, "sql_sha256": handed[0].sql_sha256 if handed else None,
            "result_sha256": None}


def replay(record, session) -> Replayed:
    """The record's specification asked again of the session, with no model call: see the
    module's notes."""
    if record.get("specification") is None:
        return Replayed("not_replayable", reason=record.get("outcome"))
    given = record.get("role")
    role = _role(given)
    then = record.get("provenance") or {}
    now = session.provenance(role or roles.group_controller)
    differs = {kind for kind, key in _COMPARED if then.get(key) != now.get(key)}
    if role is None or given.get("view") != role.view() or then.get("view") != role.view():
        differs.add("role_view")
    engine = None if _engine(then) == _engine(now) else (_engine(then), _engine(now))
    if differs:
        return Replayed("not_comparable", differs=tuple(k for k in KINDS if k in differs),
                        engine=engine)
    handed: list = []
    result = session.answer(record["specification"], role, engine_detail=handed.append)
    recorded = {key: record.get(key) for key in ("outcome", "sql_sha256", "result_sha256")}
    replayed = _values(result, handed)
    return Replayed("same" if replayed == recorded else "different", recorded=recorded,
                    replayed=replayed, engine=engine)
