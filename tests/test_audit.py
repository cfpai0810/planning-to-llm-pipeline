"""The audit and replay (src/audit.py), part 3d (the reviewer's Phase 3 brief, sections 6 and 8,
and the Stop 1 verdict, sections 3, 5 and 7).

Every question leaves one line in one JSON Lines file, audit.jsonl, in the folder the caller
gives (made when it is missing). The line is the canonical JSON of the record with its
record_sha256 (sorted keys, no spaces, ASCII escapes only, no NaN) and one newline, written in
one write under a lock, in binary, so the file holds no carriage return on any system. The
record's fields are a closed set, src/ask.py's RECORD_FIELDS and record_sha256, and so are the
keys of every mapping the door builds inside it (the role, the prompt's hashes, each call, the
coverage, the narration and each flag): the writer refuses anything else as a fault in the
code, which ask raises, and verify reports it.

verify reads every line and reports, never raises: a line that is not UTF-8, not JSON, not an
object, that holds a key twice, that lacks a field or holds another, whose hash does not match
what it holds, or whose bytes are not the canonical form of what they hold. Changing any byte
of a line is detected; deleting a whole line is not (a known limit, said in the docstring).

The record keeps the first 2,000 characters of the question with its full length (F28), and
the engine's message cut at 2,000 characters with its full length (decision 3). The record of
an engine_error holds the specification, the SQL and its hash like an answer (decision 6). A
lone surrogate, a NUL and a very long model text are written, verify, read back and replay
(F29). A folder or a file that cannot be written is audit_unavailable, with no figure and no
narration; a session that ends mid-question still leaves a line.

The leak search of 4.3 runs over every field but the visitor's words and the model's (the
question, the model's raw text and the narration's model wording): an entity controller's line
names no other entity there, and with a question that names none the whole line is clean. A
recognisable key text in the client and in the environment is nowhere in the file, nor is a
request header.

replay(record, session) makes no model call and does not narrate. It gives one of four states:
not_replayable (with the reason) for a record that holds no specification, not_comparable
(naming the kinds that differ: the planning workbook's hash, the ledger extract's hash, the
registry, the role view)
when the session is not the record's, same when the outcome, the SQL hash and the result hash
all equal the record's, and different otherwise, with both sets of values. A different engine
version is reported, not refused. The gate: more than 100 answers, recorded through ask with
the fake replying with each canonical specification, written, read back and replayed, all
same; they span a smoke subset of the oracle's grid on both pairs and, under the group
controller and the ES, AT and DE controllers, every period kind, comparisons, sorts and filters
by entity, region and account.
"""

import copy
import dataclasses
import hashlib
import json
import re
import threading
import time
from datetime import datetime, timezone

import pytest

import config
from tests import fakes

NOON = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)
ENTITIES = {"ES": ("Calder Foods Spain", "Southern Europe"),
            "AT": ("Calder Foods Austria", "Central Europe"),
            "DE": ("Calder Foods Germany", "Central Europe")}
GOOD = {"metric": "revenue", "version": "Budget", "dimensions": [], "filters": [],
        "period": {"kind": "fy", "value": "FY2026"}, "comparison": "none", "sort": None,
        "limit": 100}
BY_ENTITY = {**GOOD, "version": "Actual", "dimensions": ["entity"],
             "period": {"kind": "ytd", "value": "2026-06"}, "comparison": "budget"}
QUESTION = "What is the plan's Budget revenue for FY2026?"
FIELDS = ("run_id", "at", "role", "question", "question_length", "provenance", "prompt", "calls",
          "model_text_raw", "specification", "outcome", "sql", "sql_sha256", "result_sha256",
          "row_count", "truncated", "coverage", "gaps", "engine_detail", "engine_detail_length",
          "asks_for_a_cause", "narration")
CALL_FIELDS = ("purpose", "model", "effort", "outcome", "stop_reason", "request_id",
               "served_by", "input_tokens", "output_tokens", "sdk_version")
NARRATION_FIELDS = ("mode_asked", "mode_shown", "text_shown", "model_text", "flags", "failure")
FLAG_FIELDS = ("kind", "text", "figure", "reason")
# A made-up key text, recognisable in any file; the recording fake never sends it anywhere.
KEY_TEXT = "stand-in-3d-KEY-TEXT-q7Zr2KxW"
NO_COLUMN = "SELECT no_such_column FROM dim_version"
LONG_COLUMN = "SELECT " + "x" * 3000 + " FROM dim_version"
# The smoke subset: every STRIDE-th question of the oracle's grid, in the grid's own order.
STRIDE = 487


# -- Helpers -----------------------------------------------------------------------------------------

def reply(specification=None, refusal=None, cause=False) -> str:
    return json.dumps({"refusal": refusal, "specification": specification,
                       "asks_for_a_cause": cause})


def client_for(*replies, **kwargs):
    """The recording fake, serving each reply in turn: a text, a Reply or an exception."""
    return fakes.FakeClient(replies=[fakes.Reply(r) if isinstance(r, str) else r
                                     for r in replies], **kwargs)


def role_of(name):
    from src import roles
    return roles.group_controller if name == "group" else roles.entity_controller(name)


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False)


def promote_sample(s):
    s.acknowledge_many("sample", [(b.break_id, "A. Reviewer", "accepted")
                                  for b in s.breaks("sample")], clock=lambda: NOON)
    s.promote("sample", "P. Promoter", clock=lambda: NOON)
    return s


class Keeping:
    """The audit seam with a copy of each record it was given, as ask built it."""

    def __init__(self, log):
        self.log, self.records = log, []

    def write(self, record):
        self.log.write(record)
        self.records.append(copy.deepcopy(record))

    @property
    def path(self):
        return self.log.path


def asked(ask_module, session, seam, text=None, *, role=None, question=QUESTION, client=None,
          narrate="template"):
    client = client if client is not None else client_for(
        reply(specification=GOOD) if text is None else text)
    result = ask_module.ask(client, session, role or role_of("group"), question, audit=seam,
                            narrate=narrate)
    return result, client


def other_patterns(code: str) -> list:
    """4.3's search: another entity's code as a whole word, its name and a region it does not
    share with code, in any case."""
    own = ENTITIES[code][1]
    found = []
    for other, (name, region) in ENTITIES.items():
        if other != code:
            found += [rf"\b{other}\b", rf"(?i){re.escape(name)}"]
            if region != own:
                found.append(rf"(?i){re.escape(region)}")
    return found


def python_words(record: dict) -> dict:
    """The record without the fields that hold a visitor's or the model's words (BRIEF_3D,
    Trap 7 and the addendum): the question, the model's raw text, the narration's text shown
    when the model's wording was shown, the model's own text, and each flag's text and
    reason."""
    kept = {k: v for k, v in record.items() if k not in ("question", "model_text_raw")}
    narration = dict(kept["narration"])
    if narration["mode_shown"] == "model":
        narration["text_shown"] = None
    narration["model_text"] = None
    narration["flags"] = [{**f, "text": None, "reason": None} for f in narration["flags"]]
    kept["narration"] = narration
    return kept


def lines_of(path) -> list:
    return path.read_bytes().split(b"\n")[:-1]


# -- Fixtures ----------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def audit():
    from src import audit
    return audit


@pytest.fixture(scope="module")
def ask_module():
    from src import ask
    return ask


@pytest.fixture(scope="module")
def sessions(template, tmp_path_factory):
    """One session per pair, promoted, from the suite's one template."""
    from src import session
    pool = session.Sessions(template, root=tmp_path_factory.mktemp("audit"), max_sessions=100)
    clean = pool.open()
    clean.promote("clean", "P. Promoter", clock=lambda: NOON)
    yield {"clean": clean, "sample": promote_sample(pool.open())}
    pool.close_all()


@pytest.fixture
def sample(sessions):
    return sessions["sample"]


@pytest.fixture
def own_pool(template, tmp_path):
    """A pool of the test's own, for a session the test closes or leaves unpromoted."""
    from src import session
    pool = session.Sessions(template, root=tmp_path / "own")
    yield pool
    pool.close_all()


@pytest.fixture
def log(audit, tmp_path):
    return Keeping(audit.AuditLog(tmp_path / "audit_log"))


def broken_compile(monkeypatch, sql=NO_COLUMN):
    """The compiler's query with its SQL replaced, so the engine stops it: an engine_error."""
    from src import compiler
    real = compiler.compile
    monkeypatch.setattr(compiler, "compile", lambda *a, **k: compiler.CompiledQuery(
        **{**real(*a, **k).__dict__, "sql": sql}))


# -- The closed set (Trap 4) -------------------------------------------------------------------------

class TestTheClosedSet:
    def test_the_fields_are_the_doors_and_the_hash(self, audit, ask_module):
        assert ask_module.RECORD_FIELDS == FIELDS
        assert audit.FIELDS == FIELDS + ("record_sha256",)
        assert audit.CALL_FIELDS == CALL_FIELDS
        assert audit.NARRATION_FIELDS == NARRATION_FIELDS
        assert audit.FLAG_FIELDS == FLAG_FIELDS
        assert (audit.ROLE_FIELDS, audit.PROMPT_FIELDS, audit.COVERAGE_FIELDS) == (
            ("name", "entity", "view"), ("system_sha256", "schema_sha256", "request_sha256"),
            ("months_asked", "months_held", "compared_with"))

    def test_every_path_of_the_door_writes_exactly_the_closed_set(self, audit, ask_module,
                                                                    sample, own_pool, log):
        from src import narration
        cases = [(reply(specification=GOOD), "end_turn"),
                 (reply(refusal={"reason": "out_of_scope"}), "end_turn"),
                 ("nonsense", "end_turn"), (reply(specification=GOOD), "max_tokens"),
                 (reply(specification={**GOOD, "metric": "net_profit"}), "end_turn")]
        for text, stop in cases:
            asked(ask_module, sample, log, client=client_for(fakes.Reply(text,
                                                                         stop_reason=stop)))
        asked(ask_module, sample, log, question="")
        asked(ask_module, own_pool.open(), log)
        answer = sample.answer(BY_ENTITY, role_of("group"))
        name = narration.figure_name(answer, sample.registry(), _members(sample),
                                     answer.rows[0], "value")
        wrong = f"{name} was 1,234 EUR."
        asked(ask_module, sample, log, client=client_for(reply(specification=BY_ENTITY), wrong),
              narrate="model")
        verified = audit.verify(log.path)
        assert verified.ok and verified.lines == len(log.records) == 8
        for record in verified.records:
            assert tuple(sorted(record)) == tuple(sorted(audit.FIELDS))
            assert set(record["narration"]) == set(NARRATION_FIELDS)
            assert all(set(c) == set(CALL_FIELDS) for c in record["calls"])
        flagged = verified.records[-1]["narration"]["flags"]
        assert flagged and all(set(f) == set(FLAG_FIELDS) for f in flagged)

    @pytest.mark.parametrize("field", FIELDS)
    def test_the_writer_refuses_a_record_missing_a_field(self, audit, ask_module, sample, log,
                                                         field):
        asked(ask_module, sample, log)
        record = {k: v for k, v in log.records[0].items() if k != field}
        before = log.path.read_bytes()
        with pytest.raises(audit.RecordError) as raised:
            audit.AuditLog(log.log.folder).write(record)
        assert not isinstance(raised.value, OSError)
        assert log.path.read_bytes() == before

    @pytest.mark.parametrize("extra", ["record_sha256", "key", "headers", "exception"])
    def test_the_writer_refuses_a_record_with_an_unknown_field(self, audit, ask_module, sample,
                                                               log, extra):
        asked(ask_module, sample, log)
        before = log.path.read_bytes()
        with pytest.raises(audit.RecordError):
            audit.AuditLog(log.log.folder).write({**log.records[0], extra: "x"})
        assert log.path.read_bytes() == before

    @pytest.mark.parametrize("where", ["role", "prompt", "call", "coverage", "narration", "flag",
                                       "call text"])
    def test_the_writer_refuses_a_mapping_inside_that_is_not_its_closed_set(
            self, audit, ask_module, sample, log, where):
        asked(ask_module, sample, log, client=client_for(
            reply(specification=BY_ENTITY), "The figure was 1,234 EUR."), narrate="model")
        record = copy.deepcopy(log.records[0])
        assert record["narration"]["flags"]
        if where == "call text":
            # The narration call's entry holds no text: _call_entry drops raw_text.
            record["calls"][1]["raw_text"] = "The figure was 1,234 EUR."
        else:
            target = {"role": record["role"], "prompt": record["prompt"],
                      "call": record["calls"][1], "coverage": record["coverage"],
                      "narration": record["narration"],
                      "flag": record["narration"]["flags"][0]}[where]
            target.pop(next(iter(target)))
        with pytest.raises(audit.RecordError):
            audit.line_of(record)

    def test_a_fault_in_what_the_door_builds_is_raised_never_audit_unavailable(
            self, audit, ask_module, sample, tmp_path, monkeypatch):
        class Extra(audit.AuditLog):
            def write(self, record):
                super().write({**record, "extra": None})
        with pytest.raises(audit.RecordError):
            asked(ask_module, sample, Extra(tmp_path))
        monkeypatch.setattr(ask_module, "RECORD_FIELDS", FIELDS[:-2] + FIELDS[-1:])
        with pytest.raises(audit.RecordError):
            asked(ask_module, sample, audit.AuditLog(tmp_path), question="")
        assert not (tmp_path / audit.FILE_NAME).exists()

    def test_verify_reports_a_line_missing_a_field_or_holding_another(self, audit, ask_module,
                                                                      sample, log, tmp_path):
        asked(ask_module, sample, log)
        record = log.records[0]
        file = tmp_path / "edited.jsonl"
        missing = {k: v for k, v in record.items() if k != "gaps"}
        unknown = {**record, "headers": None}
        inner = copy.deepcopy(record)
        inner["narration"]["note"] = None
        lines = []
        for body in (missing, unknown, inner):
            lines.append(canonical({**body, "record_sha256": hashlib.sha256(
                canonical(body).encode("ascii")).hexdigest()}) + "\n")
        file.write_bytes("".join(lines).encode("ascii"))
        verified = audit.verify(file)
        assert [(p.line, p.code) for p in verified.problems] == [
            (1, "missing_field"), (2, "unknown_field"), (3, "inner_field")]
        assert verified.records == ()


def _members(session, role=None):
    from src import roles
    return roles.members_for(role or role_of("group"), session)


# -- The line: canonical, ASCII, one newline, no carriage return (Traps 1, 2 and 5) -----------------

class TestTheLine:
    def test_the_line_is_the_canonical_json_with_its_hash_and_one_newline(self, audit,
                                                                          ask_module, sample,
                                                                          log):
        asked(ask_module, sample, log)
        record = log.records[0]
        (line,) = lines_of(log.path)
        digest = hashlib.sha256(canonical(record).encode("ascii")).hexdigest()
        assert line == canonical({**record, "record_sha256": digest}).encode("ascii")
        assert audit.line_of(record) == line + b"\n"
        assert log.path.read_bytes() == line + b"\n"
        assert max(line) < 128 and b"\n" not in line
        parsed = json.loads(line)
        assert list(parsed) == sorted(parsed) and parsed["record_sha256"] == digest

    def test_the_file_holds_no_carriage_return_read_as_bytes(self, audit, ask_module, sample,
                                                             log):
        for question in (QUESTION, "Revenue\nby entity\tin June?", "x\r\ny"):
            asked(ask_module, sample, log, question=question)
        data = log.path.read_bytes()
        assert b"\r" not in data and data.count(b"\n") == 3
        assert data == b"".join(audit.line_of(r) for r in log.records)

    def test_nan_and_a_decimal_are_faults_of_the_code(self, audit, ask_module, sample, log):
        from decimal import Decimal
        asked(ask_module, sample, log)
        for value in (float("nan"), float("inf"), Decimal("1.5")):
            record = {**log.records[0], "row_count": value}
            with pytest.raises((ValueError, TypeError)) as raised:
                audit.AuditLog(log.log.folder).write(record)
            assert not isinstance(raised.value, OSError)

    def test_what_the_door_writes_reads_back_as_it_was_built(self, audit, ask_module, sample,
                                                             log):
        for text in (reply(specification=GOOD), reply(refusal={"reason": "out_of_scope"}),
                     reply(specification=BY_ENTITY, cause=True)):
            asked(ask_module, sample, log, text)
        read = audit.read(log.path)
        assert [{k: v for k, v in r.items() if k != "record_sha256"} for r in read] == \
            log.records

    def test_ascii_escapes_are_what_lets_a_lone_surrogate_be_written(self, ask_module, sample,
                                                                     log):
        # F29: a UTF-8 writer would raise on the record and lose the answer.
        asked(ask_module, sample, log, question="Revenue \udc80 in June?")
        record = log.records[0]
        with pytest.raises(UnicodeEncodeError):
            json.dumps(record, ensure_ascii=False).encode("utf-8")
        assert b"\\udc80" in log.path.read_bytes()


# -- Write, read and verify ---------------------------------------------------------------------------

class TestWriteReadVerify:
    def test_each_question_appends_one_line_and_the_lines_before_stay(self, audit, ask_module,
                                                                      sample, log):
        asked(ask_module, sample, log)
        first = log.path.read_bytes()
        asked(ask_module, sample, log, reply(refusal={"reason": "out_of_scope"}))
        data = log.path.read_bytes()
        assert data.startswith(first) and data.count(b"\n") == 2
        verified = audit.verify(log.path)
        assert verified.ok and verified.lines == 2
        assert [r["run_id"] for r in verified.records] == [r["run_id"] for r in log.records]

    def test_the_file_is_audit_jsonl_in_the_folder_given_made_when_missing(self, audit,
                                                                          ask_module, sample,
                                                                          tmp_path):
        folder = tmp_path / "a" / "b" / "c"
        made = audit.AuditLog(folder)
        assert made.path == folder / "audit.jsonl" and audit.FILE_NAME == "audit.jsonl"
        assert not folder.exists()
        asked(ask_module, sample, made)
        assert audit.verify(folder / "audit.jsonl").lines == 1
        assert audit.DEFAULT_FOLDER == "audit_log"

    def test_an_empty_file_verifies_with_no_line(self, audit, tmp_path):
        (tmp_path / "audit.jsonl").write_bytes(b"")
        verified = audit.verify(tmp_path / "audit.jsonl")
        assert (verified.ok, verified.lines, verified.records) == (True, 0, ())

    def test_read_refuses_a_file_that_does_not_verify(self, audit, ask_module, sample, log):
        asked(ask_module, sample, log)
        data = log.path.read_bytes()
        log.path.write_bytes(data.replace(b'"outcome":"answered"', b'"outcome":"no_data"', 1))
        assert not audit.verify(log.path).ok
        with pytest.raises(audit.AuditError):
            audit.read(log.path)


# -- An altered byte is detected (Trap 1) --------------------------------------------------------------

@pytest.fixture(scope="module")
def four_lines(audit, ask_module, sessions, tmp_path_factory):
    """Four lines of different paths: an answer narrated by the model and flagged, a refusal, a
    question with a lone surrogate, and a failed call."""
    from src import narration
    sample = sessions["sample"]
    seam = Keeping(audit.AuditLog(tmp_path_factory.mktemp("four")))
    answer = sample.answer(BY_ENTITY, role_of("AT"))
    name = narration.figure_name(answer, sample.registry(), _members(sample, role_of("AT")),
                                 answer.rows[0], "value")
    asked(ask_module, sample, seam, role=role_of("AT"), narrate="model", client=client_for(
        reply(specification=BY_ENTITY), f"{name} was 1,234 EUR, été."))
    asked(ask_module, sample, seam, reply(refusal={"reason": "needs_clarification"}))
    asked(ask_module, sample, seam, question="Revenue \ud800 in June? café")
    asked(ask_module, sample, seam, client=client_for(
        fakes.status_error(fakes.anthropic.RateLimitError, 429)))
    return seam.path, lines_of(seam.path)


class TestAnAlteredByteIsDetected:
    def test_every_byte_of_four_lines_changed_in_turn_is_detected(self, audit, four_lines):
        _, lines = four_lines
        missed = []
        for number, line in enumerate(lines):
            assert audit.line_problems(line) == ()
            for i in range(len(line)):
                for flip in (0x01, 0x20):
                    altered = line[:i] + bytes([line[i] ^ flip]) + line[i + 1:]
                    if audit.line_problems(altered) == ():
                        missed.append((number, i, flip))
        assert missed == []
        assert sum(len(line) for line in lines) > 4000

    def test_a_sample_of_changes_in_the_file_is_found_on_its_line(self, audit, four_lines,
                                                                  tmp_path):
        path, lines = four_lines
        data = path.read_bytes()
        middle = len(lines[0]) + 1                     # the second line starts here
        positions = list(range(middle, middle + len(lines[1]) + 1, 7)) + [middle - 1]
        for i in positions:
            file = tmp_path / f"altered_{i}.jsonl"
            file.write_bytes(data[:i] + bytes([data[i] ^ 0x01]) + data[i + 1:])
            verified = audit.verify(file)
            assert verified.problems, i
            assert len(verified.records) < 4, i

    @pytest.mark.parametrize("change", [
        "a space after a colon", "an escape for a plain letter", "two keys in another order",
        "1.0 for 1", "an upper-case escape", "an escaped slash", "a key twice",
        "a trailing space", "a byte order mark", "a carriage return before the newline"])
    def test_changes_that_parse_to_the_same_record_or_close_to_it_are_detected(
            self, audit, ask_module, sample, log, tmp_path, change):
        asked(ask_module, sample, log, question="Café revenue / FY2026?")
        (line,) = lines_of(log.path)
        text = line.decode("ascii")
        record = json.loads(text)
        if change == "a space after a colon":
            altered = text.replace('"outcome":', '"outcome": ', 1)
        elif change == "an escape for a plain letter":
            altered = text.replace('"answered"', '"\\u0061nswered"', 1)
        elif change == "two keys in another order":
            altered = text.replace('{"asks_for_a_cause":', '{"at":"' + record["at"] + '",'
                                   '"asks_for_a_cause":', 1).replace(
                ',"at":"' + record["at"] + '"', "", 1)
        elif change == "1.0 for 1":
            altered = text.replace('"limit":100', '"limit":100.0', 1)
        elif change == "an upper-case escape":
            altered = text.replace("\\u00e9", "\\u00E9", 1)
        elif change == "an escaped slash":
            altered = text.replace(" / ", " \\/ ", 1)
        elif change == "a key twice":
            altered = text.replace('"outcome":"answered"',
                                   '"outcome":"answered","outcome":"answered"', 1)
        elif change == "a trailing space":
            altered = text + " "
        elif change == "a byte order mark":
            altered = "﻿" + text
        else:
            altered = text + "\r"
        assert altered != text
        assert json.loads(altered.lstrip("﻿")) == record     # 100.0 == 100 in Python
        file = tmp_path / "changed.jsonl"
        file.write_bytes(altered.encode("utf-8") + b"\n")
        verified = audit.verify(file)
        assert not verified.ok and verified.records == ()

    def test_a_deleted_line_is_not_detected_a_known_limit(self, audit, four_lines, tmp_path):
        path, lines = four_lines
        file = tmp_path / "deleted.jsonl"
        file.write_bytes(b"".join(line + b"\n" for i, line in enumerate(lines) if i != 1))
        verified = audit.verify(file)
        assert verified.ok and verified.lines == 3
        text = " ".join(audit.__doc__.split())
        assert "verify detects an altered line, not a deleted one" in text
        assert "detective, not preventive" in text

    def test_a_line_rewritten_with_a_new_hash_is_not_detected_a_known_limit(self, audit,
                                                                            four_lines,
                                                                            tmp_path):
        # The hash has no key: whoever can write the file can recompute it.
        _, lines = four_lines
        record = json.loads(lines[1])
        record.pop("record_sha256")
        record["outcome"] = "out_of_scope"
        file = tmp_path / "rewritten.jsonl"
        file.write_bytes(audit.line_of(record))
        assert audit.verify(file).ok
        assert "recompute" in " ".join(audit.__doc__.split())


# -- verify reports, never raises ---------------------------------------------------------------------

class TestVerifyReports:
    @pytest.mark.parametrize("raw, code", [
        (b"\xff\xfe{}\n", "not_utf8"), (b"not json\n", "not_json"), (b"[1,2]\n", "not_an_object"),
        (b"42\n", "not_an_object"), (b'"text"\n', "not_an_object"), (b"\n", "empty"),
        (b'{"a":1,"a":1}\n', "repeated_key"), (b"[" * 100000 + b"\n", "not_json"),
        (b'{"a":' + b"9" * 5000 + b"}\n", "not_json"), (b'{"a":NaN}\n', "not_canonical"),
        (b'{"a":1e999}\n', "not_canonical"), (b"{}", "torn"), (b"\x00\n", "not_json")],
        ids=lambda v: v if isinstance(v, str) else ascii(v[:12]))
    def test_a_line_that_is_not_a_record_is_reported(self, audit, tmp_path, raw, code):
        file = tmp_path / "bad.jsonl"
        file.write_bytes(raw)
        verified = audit.verify(file)
        assert code in [p.code for p in verified.problems]
        assert all(p.line == 1 for p in verified.problems) and not verified.ok
        assert all(p.code in audit.PROBLEMS for p in verified.problems)

    def test_a_torn_last_line_is_reported_and_the_lines_before_are_read(self, audit, ask_module,
                                                                        sample, log, tmp_path):
        for _ in range(2):
            asked(ask_module, sample, log)
        data = log.path.read_bytes()
        file = tmp_path / "torn.jsonl"
        file.write_bytes(data[:-40])
        verified = audit.verify(file)
        assert [(p.line, p.code) for p in verified.problems][0] == (2, "torn")
        assert len(verified.records) == 1

    def test_every_problem_has_a_fixed_sentence_naming_nothing_from_the_line(self, audit):
        for code, sentence in audit.PROBLEMS.items():
            assert sentence.endswith(".") and "{" not in sentence, code


# -- F29: a lone surrogate, a NUL and a very long model text (Trap 5) ---------------------------------

class TestF29:
    def test_a_lone_surrogate_a_nul_and_a_very_long_model_text_written_verified_read_replayed(
            self, audit, ask_module, sample, log):
        # The specification call's reply carries a key twice: the first value holds a lone
        # surrogate, a NUL written as a JSON escape and a million characters, and the last one
        # wins, so the reply is an answer. The model's narration holds a raw NUL, a lone
        # surrogate and a wrong figure, so it is withheld and kept.
        long = "\ud800" + "\\u0000" + "y" * 1_000_000
        text = reply(specification=BY_ENTITY).replace(
            '"metric": "revenue"', '"metric": "' + long + '", "metric": "revenue"', 1)
        narration_text = "Revenue was 1,234 EUR.\x00\udfff"
        result, _ = asked(ask_module, sample, log, narrate="model",
                          client=client_for(text, narration_text))
        from src import answer as answers
        assert isinstance(result.result, answers.Answer)
        record = log.records[0]
        assert record["model_text_raw"] == text and len(text) > 1_000_000
        assert "\ud800" in record["model_text_raw"]
        assert record["narration"]["model_text"] == narration_text
        data = log.path.read_bytes()
        assert max(data) < 128 and b"\\ud800" in data and b"\\u0000" in data
        assert b"\\udfff" in data and b"\x00" not in data
        verified = audit.verify(log.path)
        assert verified.ok and verified.lines == 1
        (back,) = audit.read(log.path)
        assert {k: v for k, v in back.items() if k != "record_sha256"} == record
        assert audit.replay(back, sample).state == "same"


# -- audit_unavailable, and a session that ends mid-question (Trap 6) ----------------------------------

class TestAuditUnavailable:
    def _no_figure_leaves(self, sample, result):
        from src import outcomes
        assert result.result == outcomes.AUDIT_UNAVAILABLE
        assert (result.narration, result.flags, result.failure) == (None, (), None)
        figures = [cell for row in sample.answer(GOOD, role_of("group")).rows for cell in row]
        assert figures and not [f for f in figures if f in repr(result)]

    def test_a_folder_that_cannot_be_made_because_a_file_stands_in_its_place(
            self, audit, ask_module, sample, tmp_path):
        (tmp_path / "audit_log").write_bytes(b"a file, not a folder")
        for folder in (tmp_path / "audit_log", tmp_path / "audit_log" / "inner"):
            result, client = asked(ask_module, sample, audit.AuditLog(folder))
            self._no_figure_leaves(sample, result)
            assert len(client.calls) == 1

    def test_a_file_that_cannot_be_written(self, audit, ask_module, sample, tmp_path):
        (tmp_path / "audit.jsonl").mkdir()
        result, client = asked(ask_module, sample, audit.AuditLog(tmp_path), narrate="model",
                               client=client_for(reply(specification=GOOD), "Revenue rose."))
        self._no_figure_leaves(sample, result)
        assert len(client.calls) == 2

    def test_a_session_closed_mid_question_is_session_ended_and_still_a_line(
            self, audit, ask_module, own_pool, log):
        from src import outcomes
        s = promote_sample(own_pool.open())
        client = client_for(reply(specification=GOOD), on_create=lambda request: s.close())
        result, _ = asked(ask_module, s, log, client=client)
        assert result.result == outcomes.SESSION_ENDED
        closed = own_pool.open()
        closed.close()
        result, _ = asked(ask_module, closed, log)
        assert result.result == outcomes.SESSION_ENDED
        verified = audit.verify(log.path)
        assert verified.ok and [r["outcome"] for r in verified.records] == ["session_ended"] * 2
        assert [len(r["calls"]) for r in verified.records] == [1, 0]


# -- Threads (Trap 3) -----------------------------------------------------------------------------------

class TestThreads:
    def test_threads_writing_at_once_give_no_torn_line(self, audit, ask_module, sample, log,
                                                       tmp_path):
        asked(ask_module, sample, log)
        base = log.records[0]
        shared = audit.AuditLog(tmp_path / "threads")
        ids = [[f"{t:02d}{n:030d}" for n in range(60)] for t in range(8)]

        def write(mine):
            for run_id in mine:
                shared.write({**base, "run_id": run_id, "model_text_raw": run_id * 500})

        threads = [threading.Thread(target=write, args=(mine,)) for mine in ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join(120)
        verified = audit.verify(shared.path)
        assert verified.ok and verified.lines == 480
        assert sorted(r["run_id"] for r in verified.records) == sorted(sum(ids, []))
        assert b"\r" not in shared.path.read_bytes()

    def test_questions_asked_in_threads_each_leave_one_whole_line(self, audit, ask_module,
                                                                  sample, tmp_path):
        shared = audit.AuditLog(tmp_path / "asked")
        found = []

        def ask_some(code):
            for _ in range(4):
                found.append(asked(ask_module, sample, shared, role=role_of(code))[0].run_id)

        threads = [threading.Thread(target=ask_some, args=(c,)) for c in ("group", "ES", "AT")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(300)
        verified = audit.verify(shared.path)
        assert verified.ok and sorted(r["run_id"] for r in verified.records) == sorted(found)
        assert len(found) == 12

    def test_the_docstring_says_two_processes_are_not_coordinated(self, audit):
        text = " ".join(audit.__doc__.split())
        assert "A lock covers the threads of one process; two processes writing one file are " \
               "not coordinated." in text
        assert "until the host restarts" in text


# -- The leak search on the line (Trap 7) ----------------------------------------------------------------

class TestTheLeakSearch:
    @pytest.mark.parametrize("code", list(ENTITIES))
    def test_only_the_visitors_and_the_models_words_can_name_another_entity(
            self, audit, ask_module, sample, log, code):
        from src import answer as answers
        from src import narration
        others = [c for c in ENTITIES if c != code]
        named = f"How did {ENTITIES[others[0]][0]} and {others[1]} compare?"
        answer = sample.answer(BY_ENTITY, role_of(code))
        own = narration.figure_name(answer, sample.registry(), _members(sample, role_of(code)),
                                    answer.rows[0], "value")
        shown_value = f"{own} was {answers.shown(answer.rows[0][1], answer.columns[1][2])}."
        raw = reply(specification=BY_ENTITY).replace(
            '"metric": "revenue"', f'"metric": "{ENTITIES[others[0]][0]}", "metric": "revenue"')
        for words in (f"{shown_value} {ENTITIES[others[1]][0]} did not report.",
                      f"{ENTITIES[others[0]][0]} was 1,234 EUR."):
            asked(ask_module, sample, log, role=role_of(code), question=named, narrate="model",
                  client=client_for(raw, words))
        modes = [r["narration"]["mode_shown"] for r in log.records]
        assert modes == ["model", "template"]
        for line, record in zip(lines_of(log.path), audit.read(log.path)):
            whole = line.decode("ascii")
            assert [p for p in other_patterns(code) if re.search(p, whole)], code
            rest = json.dumps(python_words(record), ensure_ascii=False)
            assert [p for p in other_patterns(code) if re.search(p, rest)] == [], code
            assert record["provenance"]["breaks_scope"] == "pair-wide"

    @pytest.mark.parametrize("code", list(ENTITIES))
    def test_with_a_question_that_names_no_other_entity_the_whole_line_is_clean(
            self, audit, ask_module, sample, log, code):
        from src import answer as answers
        from src import narration
        answer = sample.answer(BY_ENTITY, role_of(code))
        own = narration.figure_name(answer, sample.registry(), _members(sample, role_of(code)),
                                    answer.rows[0], "value")
        words = f"{own} was {answers.shown(answer.rows[0][1], answer.columns[1][2])}."
        for narrate, replies in (("model", (reply(specification=BY_ENTITY), words)),
                                 ("template", (reply(specification=BY_ENTITY),)),
                                 ("template", (reply(refusal={"reason": "out_of_scope"}),))):
            asked(ask_module, sample, log, role=role_of(code), narrate=narrate,
                  question="How did revenue compare with budget this year to June?",
                  client=client_for(*replies))
        for line in lines_of(log.path):
            assert [p for p in other_patterns(code) if re.search(p, line.decode("ascii"))] == []

    def test_the_narration_call_in_calls_holds_no_text(self, audit, ask_module, sample, log):
        words = "Revenue rose by 1,234 EUR, ahead of the plan."
        asked(ask_module, sample, log, narrate="model",
              client=client_for(reply(specification=BY_ENTITY), words))
        (record,) = audit.read(log.path)
        assert [c["purpose"] for c in record["calls"]] == ["specification", "narration"]
        assert [tuple(sorted(c)) for c in record["calls"]] == [tuple(sorted(CALL_FIELDS))] * 2
        calls = json.dumps(record["calls"])
        assert "1,234" not in calls and "ahead" not in calls and "metric" not in calls


# -- The key search (Trap 8) ------------------------------------------------------------------------------

class TestTheKeySearch:
    def _clean(self, data: bytes, key: str):
        assert key.encode("ascii") not in data
        assert not re.search(rb"(?i)x-api-key|authorization|api_key|bearer", data)

    def test_a_key_text_in_the_client_and_the_environment_is_nowhere_in_the_file(
            self, audit, ask_module, sample, log, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", KEY_TEXT)
        error = fakes.status_error(fakes.anthropic.AuthenticationError, 401,
                                   f"invalid x-api-key {KEY_TEXT}; Authorization: Bearer x")
        for replies, narrate in (((reply(specification=BY_ENTITY), "Revenue rose."), "model"),
                                 ((reply(specification=BY_ENTITY), error), "model"),
                                 ((error,), "template"),
                                 ((reply(refusal={"reason": "out_of_scope"}),), "template")):
            asked(ask_module, sample, log, narrate=narrate,
                  client=client_for(*replies, api_key=KEY_TEXT))
        assert audit.verify(log.path).lines == 4
        self._clean(log.path.read_bytes(), KEY_TEXT)

    def test_through_the_real_sdk_the_key_is_on_the_wire_and_not_in_the_file(
            self, audit, ask_module, sample, log, monkeypatch):
        from streamlit_app.lib import claude_client
        key = "stand-in"
        unauthorised = {"type": "error", "error": {"type": "authentication_error",
                                                   "message": f"invalid x-api-key: {key}"}}
        monkeypatch.setenv("ANTHROPIC_API_KEY", key)
        with fakes.StandInAPI(replies=[(200, fakes.message(reply(specification=BY_ENTITY))),
                                       (200, fakes.message("Revenue rose.")),
                                       (401, unauthorised)]) as api:
            monkeypatch.setenv("ANTHROPIC_BASE_URL", api.url)
            client = claude_client.build_client(key)
            asked(ask_module, sample, log, client=client, narrate="model")
            asked(ask_module, sample, log, client=client)
        monkeypatch.delenv("ANTHROPIC_BASE_URL")
        assert [r["headers"]["x-api-key"] for r in api.received] == [key] * 3
        records = audit.read(log.path)
        assert [r["outcome"] for r in records] == ["answered", "key_rejected"]
        assert records[0]["calls"][1]["request_id"] == "req_standin_2"
        self._clean(log.path.read_bytes(), key)


# -- F28: the question is cut in the record (Trap 12) ----------------------------------------------------

class TestF28TheQuestionIsCut:
    @pytest.mark.parametrize("length", [2000, 2001, 100_000])
    def test_a_long_question_is_refused_and_its_line_holds_2000_characters_and_the_length(
            self, audit, ask_module, sample, log, length):
        from src import outcomes
        question = "".join(chr(0x61 + i % 26) for i in range(length))
        result, client = asked(ask_module, sample, log, question=question)
        assert result.result == outcomes.QUESTION_INVALID and client.calls == []
        (record,) = audit.read(log.path)
        assert record["question"] == question[:2000] and record["question_length"] == length
        assert ask_module.RECORDED == 2000
        assert len(lines_of(log.path)[0]) < 12_000

    def test_the_cut_is_in_characters_and_a_lone_surrogate_in_it_is_kept_escaped(
            self, audit, ask_module, sample, log):
        question = "x" * 1999 + "\ud800" + "y" * 3000
        asked(ask_module, sample, log, question=question)
        (record,) = audit.read(log.path)
        assert record["question"] == "x" * 1999 + "\ud800"
        assert record["question_length"] == 5000
        assert lines_of(log.path)[0].count(b"\\ud800") == 1

    @pytest.mark.parametrize("question", [None, b"Revenue", 42, ["Revenue"]],
                             ids=lambda q: type(q).__name__)
    def test_a_question_that_is_not_text_has_no_length(self, audit, ask_module, sample, log,
                                                      question):
        asked(ask_module, sample, log, question=question)
        (record,) = audit.read(log.path)
        assert (record["question"], record["question_length"]) == (None, None)

    def test_a_question_that_passes_is_kept_whole_as_received(self, audit, ask_module, sample,
                                                              log):
        question = "  Revenue in June?\n"
        asked(ask_module, sample, log, question=question)
        (record,) = audit.read(log.path)
        assert (record["question"], record["question_length"]) == (question, 19)


# -- Decision 3: engine_detail cut at 2,000 characters (Trap 13) -------------------------------------------

class TestDecision3TheEnginesMessageIsCut:
    def test_a_long_message_is_cut_and_the_record_says_so(self, audit, ask_module, sample, log,
                                                          monkeypatch):
        from src import outcomes
        broken_compile(monkeypatch, LONG_COLUMN)
        handed = []
        sample.answer(GOOD, role_of("group"), engine_detail=handed.append)
        full = handed[0].message
        assert len(full) > 2000
        result, _ = asked(ask_module, sample, log)
        assert result.result is outcomes.ENGINE_ERROR
        (record,) = audit.read(log.path)
        assert record["engine_detail"] == full[:2000]
        assert record["engine_detail_length"] == len(full)

    def test_a_short_message_is_kept_whole(self, audit, ask_module, sample, log, monkeypatch):
        broken_compile(monkeypatch)
        asked(ask_module, sample, log)
        (record,) = audit.read(log.path)
        assert "no_such_column" in record["engine_detail"]
        assert record["engine_detail_length"] == len(record["engine_detail"]) < 2000

    def test_no_detail_is_null_and_null(self, audit, ask_module, sample, log):
        asked(ask_module, sample, log)
        asked(ask_module, sample, log, reply(refusal={"reason": "out_of_scope"}))
        asked(ask_module, sample, log, question="")
        for record in audit.read(log.path):
            assert (record["engine_detail"], record["engine_detail_length"]) == (None, None)

    def test_it_reaches_the_record_alone_never_a_reader(self, audit, ask_module, sample, log,
                                                        monkeypatch):
        from src import outcomes
        broken_compile(monkeypatch, LONG_COLUMN)
        result, _ = asked(ask_module, sample, log)
        (record,) = audit.read(log.path)
        shown = repr(result) + result.result.sentence
        assert record["engine_detail"][:40] not in shown and "xxxxxxxx" not in shown
        assert (result.narration, result.flags, result.failure) == (None, (), None)
        assert result.result == outcomes.Outcome("engine_error",
                                                 outcomes.SENTENCES["engine_error"])


# -- Decision 6: the record of an engine_error (Trap 14) ---------------------------------------------------

class TestDecision6TheRecordOfAnEngineError:
    def test_it_holds_the_specification_the_sql_and_its_hash_like_an_answer(
            self, audit, ask_module, sample, log, monkeypatch):
        from src import compiler, roles, spec
        text = reply(specification={**GOOD, "metric": "REVENUE", "version": "budget",
                                    "filters": [{"dimension": "entity",
                                                 "values": ["calder foods austria"]}]})
        canonical_spec = {**GOOD, "filters": [{"dimension": "entity", "values": ["AT"]}]}
        validated = spec.validate(spec.from_json(canonical_spec), sample.registry(), sample,
                                  roles.group_controller)
        compiled = compiler.compile(validated, sample.registry(), roles.group_controller)
        broken_compile(monkeypatch)
        result, _ = asked(ask_module, sample, log, text)
        (record,) = audit.read(log.path)
        assert record["outcome"] == "engine_error"
        assert record["specification"] == canonical_spec
        assert (record["sql"], record["sql_sha256"]) == (NO_COLUMN, compiled.sql_sha256)
        assert [record[k] for k in ("result_sha256", "row_count", "truncated", "coverage",
                                    "gaps")] == [None] * 5
        shown = repr(result)
        assert NO_COLUMN not in shown and compiled.sql_sha256 not in shown
        assert "calder" not in shown.lower()

    def test_it_replays_to_same_when_it_fails_the_same_way_and_different_when_not(
            self, audit, ask_module, sample, log, monkeypatch):
        broken_compile(monkeypatch)
        asked(ask_module, sample, log)
        (record,) = audit.read(log.path)
        again = audit.replay(record, sample)
        assert again.state == "same"
        assert again.replayed == again.recorded == {
            "outcome": "engine_error", "sql_sha256": record["sql_sha256"],
            "result_sha256": None}
        monkeypatch.undo()
        now = audit.replay(record, sample)
        assert now.state == "different" and now.replayed["outcome"] == "answered"
        assert now.recorded["outcome"] == "engine_error"


# -- Replay (Trap 9) ------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def recorded(audit, ask_module, sessions, template, tmp_path_factory):
    """One record of each kind, read back from the file: {name: record}."""
    from src import session
    sample = sessions["sample"]
    seam = audit.AuditLog(tmp_path_factory.mktemp("recorded"))
    pool = session.Sessions(template, root=tmp_path_factory.mktemp("recorded_pool"))
    closed = promote_sample(pool.open())
    closed.close()
    cases = {
        "answered": dict(),
        "answered_at": dict(role=role_of("AT"), text=reply(specification=BY_ENTITY)),
        "no_data": dict(text=reply(specification={**GOOD, "version": "Actual", "period": {
            "kind": "month", "value": "2026-09"}})),
        "out_of_scope": dict(text=reply(refusal={"reason": "out_of_scope"})),
        "rate_limited": dict(client=client_for(fakes.status_error(
            fakes.anthropic.RateLimitError, 429))),
        "unreadable_reply": dict(text="nonsense"),
        "question_invalid": dict(question=""),
        "session_ended": dict(session=closed),
        "unknown_metric": dict(text=reply(specification={**GOOD, "metric": "net_profit"})),
        "entity_not_permitted": dict(role=role_of("AT"), text=reply(specification={
            **GOOD, "filters": [{"dimension": "entity", "values": ["ES"]}]})),
        "nothing_promoted": dict(session=pool.open()),
    }
    for name, given in cases.items():
        asked(ask_module, given.pop("session", sample), seam, **given)
    pool.close_all()
    return dict(zip(cases, audit.read(seam.path)))


class Watching:
    """A session as replay sees it, counting what replay asks of it."""

    def __init__(self, session):
        self.session, self.asked = session, 0

    def provenance(self, role):
        return self.session.provenance(role)

    def answer(self, *args, **kwargs):
        self.asked += 1
        return self.session.answer(*args, **kwargs)


class Untouchable:
    def __getattr__(self, name):
        raise AssertionError(f"replay touched the session: {name}")


class TestReplay:
    def test_the_states_and_the_kinds(self, audit):
        assert audit.STATES == ("same", "different", "not_comparable", "not_replayable")
        assert audit.KINDS == ("plan_file", "ledger_file", "registry", "role_view")

    @pytest.mark.parametrize("name", ["out_of_scope", "rate_limited", "unreadable_reply",
                                      "question_invalid", "session_ended", "unknown_metric",
                                      "entity_not_permitted", "nothing_promoted"])
    def test_a_record_with_no_specification_is_not_replayable_with_its_reason(
            self, audit, recorded, name):
        record = recorded[name]
        assert record["specification"] is None and record["outcome"] == name
        replayed = audit.replay(record, Untouchable())
        assert (replayed.state, replayed.reason) == ("not_replayable", name)
        assert (replayed.differs, replayed.recorded, replayed.replayed) == ((), None, None)

    @pytest.mark.parametrize("name", ["answered", "answered_at", "no_data"])
    def test_an_answer_replays_to_same_with_both_sets_of_values(self, audit, recorded,
                                                                sessions, name):
        record = recorded[name]
        replayed = audit.replay(record, sessions["sample"])
        values = {"outcome": record["outcome"], "sql_sha256": record["sql_sha256"],
                  "result_sha256": record["result_sha256"]}
        assert (replayed.state, replayed.recorded, replayed.replayed) == ("same", values,
                                                                          values)
        assert replayed.engine is None

    @pytest.mark.parametrize("field, kind", [
        ("registry_hash", "registry"), ("plan_file_sha256", "plan_file"),
        ("ledger_file_sha256", "ledger_file")])
    def test_a_changed_hash_is_not_comparable_and_nothing_is_asked(self, audit, recorded,
                                                                   sessions, field, kind):
        record = copy.deepcopy(recorded["answered"])
        record["provenance"][field] = "0" * 64
        watching = Watching(sessions["sample"])
        replayed = audit.replay(record, watching)
        assert (replayed.state, replayed.differs) == ("not_comparable", (kind,))
        assert watching.asked == 0

    def test_another_pairs_session_is_not_comparable_by_its_plan_file(self, audit, recorded,
                                                                      sessions):
        replayed = audit.replay(recorded["answered"], sessions["clean"])
        assert (replayed.state, replayed.differs) == ("not_comparable", ("plan_file",))

    def test_an_unpromoted_session_is_not_comparable(self, audit, recorded, own_pool):
        replayed = audit.replay(recorded["answered"], own_pool.open())
        assert (replayed.state, replayed.differs) == ("not_comparable",
                                                      ("plan_file", "ledger_file"))

    @pytest.mark.parametrize("change", ["view", "entity", "shape", "provenance view"])
    def test_another_role_view_is_not_comparable(self, audit, recorded, sessions, change):
        record = copy.deepcopy(recorded["answered_at"])
        if change == "view":
            record["role"]["view"] = "entity view: ES"
        elif change == "entity":
            record["role"]["entity"] = "DE"
        elif change == "shape":
            record["role"] = {"name": "group_controller", "entity": "AT",
                              "view": "entity view: AT"}
        else:
            record["provenance"]["view"] = "group view"
        watching = Watching(sessions["sample"])
        replayed = audit.replay(record, watching)
        assert (replayed.state, replayed.differs) == ("not_comparable", ("role_view",))
        assert watching.asked == 0

    def test_not_comparable_names_kinds_never_a_member(self, audit, recorded, sessions):
        record = copy.deepcopy(recorded["answered_at"])
        record["role"]["entity"] = "ES"
        record["provenance"]["registry_hash"] = "0" * 64
        replayed = audit.replay(record, sessions["clean"])
        assert replayed.differs == ("plan_file", "registry", "role_view")
        text = repr(replayed)
        for code, (name, region) in ENTITIES.items():
            assert not re.search(rf"\b{code}\b", text) and name not in text
            assert region not in text

    def test_a_different_engine_version_is_reported_not_refused(self, audit, recorded,
                                                                sessions):
        from src import warehouse
        record = copy.deepcopy(recorded["answered"])
        record["provenance"]["engine_version"] = "0.0.1"
        replayed = audit.replay(record, sessions["sample"])
        assert replayed.state == "same"
        assert replayed.engine == (f"{warehouse.ENGINE} 0.0.1",
                                   f"{warehouse.ENGINE} {warehouse.ENGINE_VERSION}")

    @pytest.mark.parametrize("field, value", [
        ("result_sha256", "0" * 64), ("sql_sha256", "0" * 64), ("outcome", "no_data")])
    def test_any_value_that_differs_is_different_with_both_sets(self, audit, recorded,
                                                                sessions, field, value):
        record = copy.deepcopy(recorded["answered"])
        record[field] = value
        replayed = audit.replay(record, sessions["sample"])
        assert replayed.state == "different"
        assert replayed.recorded[field] == value
        assert replayed.replayed[field] == recorded["answered"][field]
        assert {k for k in replayed.recorded if replayed.recorded[k] != replayed.replayed[k]} \
            == {field}

    def test_replay_makes_no_model_call_and_does_not_narrate(self, audit, recorded, sessions,
                                                             monkeypatch):
        import inspect

        from src import model_call, narration

        def refuse(*args, **kwargs):
            raise AssertionError("replay called the model or narrated")
        for module, name in ((model_call, "call_model"), (narration, "narrate"),
                             (narration, "template"), (narration, "narration_input")):
            monkeypatch.setattr(module, name, refuse)
        assert audit.replay(recorded["answered_at"], sessions["sample"]).state == "same"
        assert list(inspect.signature(audit.replay).parameters) == ["record", "session"]

    def test_the_result_is_frozen(self, audit, recorded, sessions):
        replayed = audit.replay(recorded["answered"], sessions["sample"])
        with pytest.raises(dataclasses.FrozenInstanceError):
            replayed.state = "different"


# -- The replay gate (brief 8 and 9.8; Trap 9) --------------------------------------------------------------

def spec(metric, version="Budget", period=("fy", "FY2026"), dimensions=(), comparison="none",
         filters=(), sort=None, limit=100) -> dict:
    return {"metric": metric, "version": version, "dimensions": list(dimensions),
            "filters": [{"dimension": d, "values": list(v)} for d, v in filters],
            "period": {"kind": period[0], "value": period[1]}, "comparison": comparison,
            "sort": None if sort is None else {"by": sort[0], "direction": sort[1]},
            "limit": limit}


def smoke_subset() -> list:
    """Every STRIDE-th question of the oracle's grid, as the grid asks it (an entity set is an
    entity filter, asked by the group controller)."""
    from tests import oracle
    from tests.grid_worker import spec_of
    return [spec_of(*q) for q in list(oracle.questions())[::STRIDE]]


def role_specs(name: str) -> list:
    """Questions for one role, canonical as validation gives them: every period kind,
    comparisons, sorts, and filters by entity, region and account."""
    entity = "DE" if name == "group" else name
    region = ENTITIES[entity][1]
    return [
        spec("revenue", "Actual", ("ytd", "2026-06"), ("entity",), "budget",
             sort=("value", "desc"), limit=2),
        spec("ebit", "Budget", ("fy", "FY2026"), ("quarter",), sort=("quarter", "desc")),
        spec("gross_margin_pct", "Forecast", ("quarter", "Q3"), ("month",), "budget",
             sort=("month", "asc")),
        spec("operating_expenses", "Actual", ("month", "2026-04"), ("account",), "forecast",
             sort=("value", "asc"), limit=3),
        spec("revenue", "Budget", ("fy", "FY2026"), ("entity",),
             filters=(("region", (region,)),)),
        spec("volume", "Actual", ("month", "2026-02"), filters=(("entity", (entity,)),)),
        spec("headcount", "Budget", ("quarter", "Q2"), ("entity",)),
        spec("revenue_per_unit", "Actual", ("ytd", "2026-06"), ("region",), "forecast"),
        spec("operating_expenses", "Actual", ("ytd", "2026-06"), ("entity",), "budget",
             filters=(("account", ("6100",)),)),
        spec("marketing_pct_of_revenue", "Budget", ("month", "2026-12"), ("region",)),
        spec("ebit_margin_pct", "Actual", ("quarter", "Q1"), ("entity", "month"), "forecast",
             sort=("entity", "asc")),
        spec("gross_profit", "Forecast", ("ytd", "2026-09"), ("quarter",), "budget"),
    ]


@pytest.fixture(scope="module")
def gate(audit, ask_module, sessions, tmp_path_factory):
    """The gate's records, asked through ask with the fake replying with each canonical
    specification, written to one file, read back and replayed on the session that answered:
    (rows of (pair, role name, the specification sent, the record, the replay), the file, the
    seconds the asking took, the seconds the replaying took)."""
    seam = audit.AuditLog(tmp_path_factory.mktemp("gate"))
    sent = [(pair, "group", s) for s in smoke_subset() for pair in ("clean", "sample")]
    sent += [("sample", name, s) for name in ("group", "ES", "AT", "DE") for s in role_specs(name)]
    started = time.perf_counter()
    for pair, name, question in sent:
        asked(ask_module, sessions[pair], seam, reply(specification=question),
              role=role_of(name))
    asking = time.perf_counter() - started
    records = audit.read(seam.path)
    started = time.perf_counter()
    replays = [audit.replay(r, sessions[pair]) for (pair, _, _), r in zip(sent, records)]
    replaying = time.perf_counter() - started
    rows = [(pair, name, question, record, replayed)
            for (pair, name, question), record, replayed in zip(sent, records, replays)]
    return rows, seam.path, asking, replaying


class TestTheReplayGate:
    def test_more_than_100_answers_replay_and_every_one_is_same(self, gate):
        rows, _, asking, replaying = gate
        answered = [r for r in rows if r[3]["outcome"] == "answered"]
        assert len(answered) >= 100
        assert [r[3]["run_id"] for r in rows if r[4].state != "same"] == []
        print(f"\nThe replay gate: {len(rows)} records, {len(answered)} answers, all same; "
              f"asked in {asking:.1f} s, replayed in {replaying:.1f} s")

    def test_every_record_holds_a_specification_and_was_written_once(self, gate, audit):
        rows, path, _, _ = gate
        verified = audit.verify(path)
        assert verified.ok and verified.lines == len(rows)
        assert all(r[3]["specification"] is not None for r in rows)
        assert len({r[3]["run_id"] for r in rows}) == len(rows)

    def test_the_canonical_specification_is_the_one_sent_and_validates_back_to_itself(
            self, gate, ask_module, sessions):
        from src import spec as spec_module
        rows, _, _, _ = gate
        for pair, name, question, record, _ in rows:
            assert record["specification"] == question, question
            s = sessions[pair]
            validated = spec_module.validate(spec_module.from_json(record["specification"]),
                                             s.registry(), s, role_of(name))
            assert ask_module._canonical(validated.spec) == record["specification"]

    def test_what_the_gate_covers(self, gate):
        rows, _, _, _ = gate
        answered = [r for r in rows if r[3]["outcome"] == "answered"]
        specs = [r[2] for r in answered]
        assert {s["period"]["kind"] for s in specs} == {"month", "quarter", "ytd", "fy"}
        assert {s["comparison"] for s in specs} == {"none", "budget", "forecast"}
        assert {(s["sort"] or {}).get("by") for s in specs} >= {None, "value", "quarter",
                                                                  "month", "entity"}
        assert {(s["sort"] or {}).get("direction") for s in specs} >= {"asc", "desc"}
        assert {f["dimension"] for s in specs for f in s["filters"]} == {"entity", "region",
                                                                         "account"}
        assert {r[1] for r in answered} == {"group", "ES", "AT", "DE"}
        assert {r[0] for r in answered} == {"clean", "sample"}
        assert {tuple(s["dimensions"]) for s in specs} >= {(), ("entity",), ("region",),
                                                           ("account",), ("month",),
                                                           ("quarter",)}
        assert {s["metric"] for s in specs} == {
            "revenue", "cost_of_sales", "gross_profit", "operating_expenses", "ebit",
            "gross_margin_pct", "ebit_margin_pct", "marketing_pct_of_revenue", "volume",
            "headcount", "revenue_per_unit"}
        assert any(r[3]["truncated"] for r in answered)
        assert any(r[3]["gaps"] for r in answered)
        subset = smoke_subset()
        assert len([r for r in rows if r[1] == "group" and r[2] in subset]) >= 100

    def test_the_registry_hash_changed_in_every_gate_record_is_not_comparable(self, gate,
                                                                             audit,
                                                                             sessions):
        rows, _, _, _ = gate
        for pair, _, _, record, _ in rows[::10]:
            changed = copy.deepcopy(record)
            changed["provenance"]["registry_hash"] = "f" * 64
            replayed = audit.replay(changed, sessions[pair])
            assert (replayed.state, replayed.differs) == ("not_comparable", ("registry",))


# -- The docstring ------------------------------------------------------------------------------------------

class TestTheModulesNotes:
    def test_the_docstring_states_the_record_and_its_limits(self, audit):
        text = " ".join(audit.__doc__.split())
        for phrase in ("audit.jsonl", "sorted keys", "ASCII escapes", "record_sha256",
                       "no model call", "not_replayable", "not_comparable",
                       "the Release phase decides where it lives"):
            assert phrase in text, phrase
