"""The question path's door (src/ask.py), part 3b (the reviewer's Phase 3 brief, 4.1, 4.6 and 8).

ask(client, session, role, question, *, audit, narrate) is the one way in. Its steps, in
order: the question is checked (4.6); the role is checked (a role of the wrong shape raises
RoleError, with nothing sent and nothing recorded); the member lists (nothing_promoted when
nothing is promoted); the request (the schema and the prompt); one call through call_model;
the reply mapped (a refusal reason to its outcome, a specification to session.answer as the
mapping it came as, anything else unreadable_reply); narration (part 3c; none yet); the record,
handed to the audit seam last; and the result. It never raises for anything a visitor or the
model can cause: a closed session is session_ended, a record that cannot be written is
audit_unavailable and no figure leaves. It raises only for a fault in the code.

The client is the recording fake (tests/fakes.py), which fails any request that holds more than
the five keys, so every test here is also a test that no sampling parameter is sent. The audit
seam in these tests encodes each record as the audit line will (ASCII, no NaN), so a record
that could not be written fails the test.
"""

import json
import re
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
QUESTION = "What is the plan's Budget revenue for FY2026?"
FIELDS = ("run_id", "at", "role", "question", "provenance", "prompt", "calls", "model_text_raw",
          "specification", "outcome", "sql", "sql_sha256", "result_sha256", "row_count",
          "truncated", "coverage", "gaps", "engine_detail", "asks_for_a_cause", "narration")
CALL_FIELDS = ("purpose", "model", "effort", "outcome", "stop_reason", "request_id",
               "served_by", "input_tokens", "output_tokens", "sdk_version")


class Recorder:
    """The audit seam of part 3b: an object with one method, write(record). It encodes each
    record as the audit line will (ASCII, no NaN), then keeps it; or raises the error given."""

    def __init__(self, error=None):
        self.error, self.records, self.lines = error, [], []

    def write(self, record):
        line = json.dumps(record, ensure_ascii=True, allow_nan=False, sort_keys=True,
                          separators=(",", ":"))
        if self.error is not None:
            raise self.error
        self.records.append(record)
        self.lines.append(line)


def reply(specification=None, refusal=None, cause=False) -> str:
    return json.dumps({"refusal": refusal, "specification": specification,
                       "asks_for_a_cause": cause})


def client_for(*texts, stop_reason="end_turn", **kwargs):
    return fakes.FakeClient(replies=[fakes.Reply(t, stop_reason=stop_reason) for t in texts],
                            **kwargs)


@pytest.fixture(scope="module")
def ask_module():
    from src import ask
    return ask


@pytest.fixture(scope="module")
def pool(template, tmp_path_factory):
    from src import session
    sessions = session.Sessions(template, root=tmp_path_factory.mktemp("ask"), max_sessions=100)
    yield sessions
    sessions.close_all()


def promote_sample(s):
    s.acknowledge_many("sample", [(b.break_id, "A. Reviewer", "accepted")
                                  for b in s.breaks("sample")], clock=lambda: NOON)
    s.promote("sample", "P. Promoter", clock=lambda: NOON)
    return s


@pytest.fixture(scope="module")
def sample(pool):
    return promote_sample(pool.open())


@pytest.fixture
def own_pool(template, tmp_path):
    """A pool of the test's own, for a session the test closes or promotes again."""
    from src import session
    sessions = session.Sessions(template, root=tmp_path / "own")
    yield sessions
    sessions.close_all()


def group():
    from src import roles
    return roles.group_controller


def entity(code):
    from src import roles
    return roles.entity_controller(code)


def run(ask_module, session, text=None, *, role=None, question=QUESTION, audit=None,
        client=None, stop_reason="end_turn", narrate="template"):
    client = client if client is not None else client_for(
        reply(specification=GOOD) if text is None else text, stop_reason=stop_reason)
    audit = audit if audit is not None else Recorder()
    asked = ask_module.ask(client, session, role or group(), question, audit=audit,
                           narrate=narrate)
    return asked, client, audit


# -- 4.6 The question --------------------------------------------------------------------------------

class TestTheQuestion:
    @pytest.mark.parametrize("question, sent", [
        ("Revenue in June?", "Revenue in June?"),
        ("  Revenue in June?\n", "Revenue in June?"),
        ("Revenue\tby entity,\nin June?", "Revenue\tby entity,\nin June?"),
        ("x" * 500, "x" * 500), ("  " + "x" * 500 + "\t", "x" * 500),
        ("Umsatz für Österreich?", "Umsatz für Österreich?")])
    def test_a_question_that_passes_is_sent_trimmed(self, ask_module, question, sent):
        assert ask_module.checked_question(question) == sent

    @pytest.mark.parametrize("question", [
        "", "   ", "\n\t", "x" * 501, " " + "x" * 501, "Revenue\x00", "Revenue\r\nin June",
        "Revenue \x1b[31m", "\x07", "Revenue\x85", "Rev\u200benue", "Revenue\ufeff",
        "Revenue \u202egnuJ", "Revenue \u2066x\u2069", "Revenue \ud800", None, b"Revenue",
        42, ["Revenue"]], ids=lambda q: ascii(q)[:24])
    def test_anything_else_is_question_invalid_before_anything_is_sent(self, ask_module,
                                                                        sample, question):
        from src import outcomes
        asked, client, audit = run(ask_module, sample, question=question)
        assert asked.result == outcomes.QUESTION_INVALID and asked.failure is None
        assert client.calls == []
        (record,) = audit.records
        assert record["outcome"] == "question_invalid" and record["calls"] == []
        assert record["prompt"] is None

    def test_the_trimmed_question_is_sent_and_the_one_received_is_recorded(self, ask_module,
                                                                          sample):
        asked, client, audit = run(ask_module, sample, question="  Revenue in June?\n")
        assert client.calls[0]["messages"] == [{"role": "user", "content": "Revenue in June?"}]
        assert audit.records[0]["question"] == "  Revenue in June?\n"

    def test_a_lone_surrogate_is_refused_and_still_recorded(self, ask_module, sample):
        # Category Cs is not text: it cannot be encoded to be sent, so it is refused with the
        # rest, and the record keeps it escaped.
        asked, client, audit = run(ask_module, sample, question="Revenue \udc80 in June?")
        assert asked.result.code == "question_invalid" and client.calls == []
        assert "\\udc80" in audit.lines[0]
        assert audit.records[0]["question"] == "Revenue \udc80 in June?"

    def test_a_question_that_is_not_text_is_recorded_as_none(self, ask_module, sample):
        _, _, audit = run(ask_module, sample, question=b"Revenue")
        assert audit.records[0]["question"] is None


# -- The role ---------------------------------------------------------------------------------------

class TestTheRole:
    @pytest.mark.parametrize("question", [QUESTION, ""])
    def test_a_role_of_the_wrong_shape_raises_and_nothing_is_sent_or_recorded(
            self, ask_module, sample, question):
        from src import roles
        bad = object.__new__(roles.Role)
        object.__setattr__(bad, "name", "entity_controller")
        object.__setattr__(bad, "entity", None)
        for role in (bad, "group_controller", None):
            client, audit = client_for(reply(specification=GOOD)), Recorder()
            with pytest.raises(roles.RoleError):
                ask_module.ask(client, sample, role, question, audit=audit)
            assert client.calls == [] and audit.records == []


# -- 4.1 The steps and each path --------------------------------------------------------------------

class TestEachPath:
    def test_nothing_promoted(self, ask_module, own_pool):
        from src import outcomes
        asked, client, audit = run(ask_module, own_pool.open())
        assert asked.result == outcomes.NOTHING_PROMOTED and client.calls == []
        record = audit.records[0]
        assert (record["outcome"], record["prompt"], record["calls"]) == (
            "nothing_promoted", None, [])
        assert record["provenance"]["promoted"] is False

    def test_a_good_specification_is_answered_as_session_answer_answers_it(self, ask_module,
                                                                            sample):
        from src import answer
        asked, client, audit = run(ask_module, sample)
        direct = sample.answer(GOOD, group())
        assert isinstance(asked.result, answer.Answer)
        assert (asked.result.sql_sha256, asked.result.result_sha256, asked.result.rows) == (
            direct.sql_sha256, direct.result_sha256, direct.rows)
        # Changed on purpose in 3c: an answer with figures is narrated, by the template here.
        assert (asked.failure, asked.flags) == (None, ())
        assert asked.narration.startswith("Revenue for the group")
        assert len(client.calls) == 1

    @pytest.mark.parametrize("reason", ["out_of_scope", "needs_clarification",
                                        "change_not_possible", "currency_not_supported"])
    def test_each_refusal_reason_is_its_outcome(self, ask_module, sample, reason):
        from src import outcomes
        asked, client, audit = run(ask_module, sample, reply(refusal={"reason": reason}))
        assert asked.result == outcomes.outcome(reason) and asked.failure is None
        assert len(client.calls) == 1 and audit.records[0]["outcome"] == reason

    @pytest.mark.parametrize("stop, code", [("refusal", "model_refused"),
                                            ("max_tokens", "reply_cut_off"),
                                            ("pause_turn", "unreadable_reply")])
    def test_a_stop_reason_is_a_failure_code(self, ask_module, sample, stop, code):
        asked, client, audit = run(ask_module, sample, stop_reason=stop)
        assert (asked.result, asked.failure) == (None, code) and len(client.calls) == 1
        record = audit.records[0]
        assert record["outcome"] == code and record["calls"][0]["outcome"] == code
        assert record["calls"][0]["stop_reason"] == stop

    def test_a_failed_call_is_its_code(self, ask_module, sample):
        error = fakes.status_error(fakes.anthropic.RateLimitError, 429)
        asked, client, audit = run(ask_module, sample,
                                   client=fakes.FakeClient(replies=[error]))
        assert (asked.result, asked.failure) == (None, "rate_limited")
        assert audit.records[0]["calls"][0]["outcome"] == "rate_limited"
        assert audit.records[0]["model_text_raw"] is None

    @pytest.mark.parametrize("text", [
        reply(), reply(specification=GOOD, refusal={"reason": "out_of_scope"}),
        reply(refusal={"reason": "elsewhere"}), reply(specification=[GOOD]),
        reply(specification=json.dumps(GOOD)), json.dumps({"refusal": None}),
        reply(specification=GOOD).replace("false", '"false"'), "not json"])
    def test_any_other_reply_is_unreadable(self, ask_module, sample, text):
        asked, client, audit = run(ask_module, sample, text)
        assert (asked.result, asked.failure) == (None, "unreadable_reply")
        assert audit.records[0]["model_text_raw"] == text

    def test_a_validation_refusal_is_its_outcome(self, ask_module, sample):
        from src import outcomes
        asked, _, audit = run(ask_module, sample, reply(specification={**GOOD,
                                                                       "metric": "net_profit"}))
        assert asked.result == outcomes.outcome("unknown_metric")
        assert audit.records[0]["specification"] is None

    def test_a_session_closed_before_the_question(self, ask_module, own_pool):
        from src import outcomes
        s = promote_sample(own_pool.open())
        s.close()
        asked, client, audit = run(ask_module, s)
        assert asked.result == outcomes.SESSION_ENDED and client.calls == []
        assert audit.records[0]["provenance"] is None
        assert audit.records[0]["outcome"] == "session_ended"

    def test_a_session_closed_between_the_call_and_the_answer(self, ask_module, own_pool):
        from src import outcomes
        s = promote_sample(own_pool.open())
        client = fakes.FakeClient(replies=[fakes.Reply(reply(specification=GOOD))],
                                  on_create=lambda request: s.close())
        asked, client, audit = run(ask_module, s, client=client)
        assert asked.result == outcomes.SESSION_ENDED and len(client.calls) == 1
        (record,) = audit.records
        assert record["outcome"] == "session_ended" and record["sql"] is None
        assert record["provenance"]["promoted"] is True and len(record["calls"]) == 1

    def test_an_engine_error_keeps_the_engines_message_for_the_record_alone(
            self, ask_module, sample, monkeypatch):
        from src import compiler, outcomes
        real = compiler.compile
        monkeypatch.setattr(compiler, "compile", lambda *a, **k: compiler.CompiledQuery(
            **{**real(*a, **k).__dict__, "sql": "SELECT no_such_column FROM dim_version"}))
        asked, _, audit = run(ask_module, sample)
        assert asked.result is outcomes.ENGINE_ERROR
        detail = audit.records[0]["engine_detail"]
        assert "no_such_column" in detail and detail not in repr(asked)
        assert audit.records[0]["outcome"] == "engine_error"

    def test_a_promotion_during_the_call_records_the_pair_that_answered(self, ask_module,
                                                                         own_pool):
        # The provenance is read once, after the role check; when an answer comes back, the
        # record keeps the answer's own, read under the same lock as the query.
        s = own_pool.open()
        s.promote("clean", "P. Promoter", clock=lambda: NOON)
        client = fakes.FakeClient(replies=[fakes.Reply(reply(specification=GOOD))],
                                  on_create=lambda request: promote_sample(s))
        asked, _, audit = run(ask_module, s, client=client)
        record = audit.records[0]
        assert record["provenance"] == asked.result.provenance
        assert record["provenance"]["plan_file_name"] == "planning_model.xlsx"

    def test_the_narration_mode_is_template_or_model(self, ask_module, sample):
        # Changed on purpose in 3c: part 3b left narration to 3c; the mode is still checked.
        with pytest.raises(ValueError):
            run(ask_module, sample, narrate="poetry")


# -- Narration through the door (part 3c) -----------------------------------------------------------

NARRATED = {"mode_asked", "mode_shown", "text_shown", "model_text", "flags", "failure"}
NO_NARRATION = {"mode_shown": None, "text_shown": None, "model_text": None, "flags": [],
                "failure": None}
BY_ENTITY = {**GOOD, "version": "Actual", "dimensions": ["entity"],
             "period": {"kind": "ytd", "value": "2026-06"}, "comparison": "budget"}


def _narration_text(ask_module, sample, role=None):
    """Wording a model might write for BY_ENTITY: the first row's value under its name."""
    from src import answer as answers
    from src import narration, roles
    role = role or group()
    answer = sample.answer(BY_ENTITY, role)
    name = narration.figure_name(answer, sample.registry(), roles.members_for(role, sample),
                                 answer.rows[0], "value")
    return f"{name} was {answers.shown(answer.rows[0][1], answer.columns[1][2])}.", answer


def _narrated_client(*narration_replies, cause=False):
    return fakes.FakeClient(replies=[fakes.Reply(reply(specification=BY_ENTITY, cause=cause)),
                                     *narration_replies])


class TestNarrationThroughTheDoor:
    def test_the_template_is_the_default_and_makes_no_call(self, ask_module, sample):
        from src import narration, roles
        asked, client, audit = run(ask_module, sample, reply(specification=BY_ENTITY))
        expected = narration.template(asked.result, sample.registry(),
                                      roles.members_for(group(), sample), group())
        assert (asked.narration, asked.flags, len(client.calls)) == (expected, (), 1)
        record = audit.records[0]["narration"]
        assert set(record) == NARRATED
        assert record == {"mode_asked": "template", "mode_shown": "template",
                          "text_shown": expected, "model_text": None, "flags": [],
                          "failure": None}

    def test_model_wording_with_no_flag_is_shown_after_one_more_call(self, ask_module, sample):
        text, _ = _narration_text(ask_module, sample)
        asked, client, audit = run(ask_module, sample, client=_narrated_client(
            fakes.Reply(text, request_id="req_narration")), narrate="model")
        assert (asked.narration, asked.flags, asked.failure) == (text, (), None)
        assert len(client.calls) == 2 and "output_config" in client.calls[1]
        assert "format" not in client.calls[1]["output_config"]
        record = audit.records[0]
        assert [c["purpose"] for c in record["calls"]] == ["specification", "narration"]
        assert record["calls"][1]["request_id"] == "req_narration"
        assert tuple(record["calls"][1]) == CALL_FIELDS
        assert record["narration"]["mode_shown"] == "model"
        assert record["narration"]["text_shown"] == text

    def test_flagged_wording_is_withheld_and_kept_for_the_record(self, ask_module, sample):
        from src import answer as answers
        from src import narration
        text, answer = _narration_text(ask_module, sample)
        written = answers.shown(answer.rows[0][1], answer.columns[1][2])
        wrong = text.replace(written, "1,234 EUR")
        asked, client, audit = run(ask_module, sample, client=_narrated_client(
            fakes.Reply(wrong)), narrate="model")
        assert asked.narration.endswith("\n" + narration.NOTICE_WITHHELD)
        assert asked.flags and asked.failure is None and isinstance(asked.result, answers.Answer)
        record = audit.records[0]["narration"]
        assert (record["mode_shown"], record["model_text"]) == ("template", wrong)
        assert [f["text"] for f in record["flags"]] == ["1,234 EUR"]

    def test_a_failed_narration_call_keeps_the_answer(self, ask_module, sample):
        from src import answer as answers
        from src import narration
        error = fakes.status_error(fakes.anthropic.RateLimitError, 429)
        asked, client, audit = run(ask_module, sample, client=_narrated_client(error),
                                   narrate="model")
        assert isinstance(asked.result, answers.Answer) and asked.failure is None
        assert asked.narration.endswith("\n" + narration.NOTICE_UNAVAILABLE)
        record = audit.records[0]
        assert record["narration"]["failure"] == "rate_limited"
        assert record["calls"][1]["outcome"] == "rate_limited"
        assert record["outcome"] == "answered"

    def test_the_why_sentence_follows_the_narration(self, ask_module, sample):
        from src import narration
        text, _ = _narration_text(ask_module, sample)
        asked, _, _ = run(ask_module, sample, client=_narrated_client(fakes.Reply(text),
                                                                      cause=True),
                          narrate="model")
        assert asked.narration == text + "\n" + narration.CAUSE
        asked, _, _ = run(ask_module, sample, reply(specification=BY_ENTITY, cause=True))
        assert asked.narration.endswith("\n" + narration.CAUSE)
        assert asked.narration.count(narration.CAUSE) == 1

    @pytest.mark.parametrize("text, stop", [
        (reply(refusal={"reason": "out_of_scope"}), "end_turn"), ("[]", "end_turn"),
        (reply(specification={**GOOD, "limit": 0}), "end_turn"),
        (reply(specification={**GOOD, "version": "Actual",
                              "period": {"kind": "month", "value": "2026-09"}}), "end_turn"),
        (reply(specification=GOOD), "refusal")])
    def test_no_narration_call_without_figures(self, ask_module, sample, text, stop):
        asked, client, audit = run(ask_module, sample, text, stop_reason=stop, narrate="model")
        assert len(client.calls) == 1 and asked.narration is None and asked.flags == ()
        assert {k: v for k, v in audit.records[0]["narration"].items()
                if k != "mode_asked"} == NO_NARRATION

    def test_no_call_at_all_before_the_question_can_be_asked(self, ask_module, sample,
                                                             own_pool):
        for session, question in ((sample, ""), (own_pool.open(), QUESTION)):
            asked, client, audit = run(ask_module, session, question=question,
                                       narrate="model")
            assert client.calls == [] and asked.narration is None

    def test_no_narration_leaves_when_the_record_cannot_be_written(self, ask_module, sample):
        text, _ = _narration_text(ask_module, sample)
        asked, client, _ = run(ask_module, sample, client=_narrated_client(fakes.Reply(text)),
                               narrate="model", audit=Recorder(error=OSError("disk full")))
        assert (asked.narration, asked.flags) == (None, ()) and len(client.calls) == 2

    @pytest.mark.parametrize("code", list(ENTITIES))
    def test_the_narration_input_names_no_other_entity(self, ask_module, sample, code):
        text, _ = _narration_text(ask_module, sample, role=entity(code))
        _, client, audit = run(ask_module, sample, role=entity(code),
                               client=_narrated_client(fakes.Reply(text)), narrate="model")
        message = client.calls[1]["messages"][0]["content"]
        shown = audit.records[0]["narration"]["text_shown"]
        for checked in (message, shown):
            assert [p for p in other_patterns(code) if re.search(p, checked)] == [], code
        assert QUESTION not in message


# -- The audit ---------------------------------------------------------------------------------------

class TestTheAuditSeam:
    @pytest.mark.parametrize("error", [OSError("disk full"), PermissionError("read-only"),
                                       FileNotFoundError("gone")])
    def test_a_record_that_cannot_be_written_is_audit_unavailable_and_no_figure_leaves(
            self, ask_module, sample, error):
        from src import outcomes
        asked, client, _ = run(ask_module, sample, audit=Recorder(error=error))
        assert asked.result == outcomes.AUDIT_UNAVAILABLE
        assert (asked.narration, asked.flags, asked.failure) == (None, (), None)
        assert len(client.calls) == 1
        figures = [cell for row in sample.answer(GOOD, group()).rows for cell in row]
        assert figures and not [f for f in figures if f in repr(asked)]

    def test_refusals_and_failures_are_recorded_and_unavailable_too(self, ask_module, sample):
        from src import outcomes
        for text, stop in ((reply(refusal={"reason": "out_of_scope"}), "end_turn"),
                           (reply(specification=GOOD), "refusal")):
            asked, _, _ = run(ask_module, sample, text, stop_reason=stop,
                              audit=Recorder(error=OSError("disk full")))
            assert asked.result == outcomes.AUDIT_UNAVAILABLE and asked.failure is None

    def test_any_other_error_of_the_audit_is_a_fault_and_raised(self, ask_module, sample):
        with pytest.raises(ValueError):
            run(ask_module, sample, audit=Recorder(error=ValueError("a fault")))

    def test_the_record_is_written_once_and_last(self, ask_module, sample):
        audit = Recorder()
        asked, _, _ = run(ask_module, sample, audit=audit)
        (record,) = audit.records
        assert record["run_id"] == asked.run_id and record["outcome"] == "answered"
        assert record["result_sha256"] == asked.result.result_sha256


# -- The record (what 3b knows of the closed set of 3d) ---------------------------------------------

class TestTheRecord:
    def test_every_path_writes_the_same_fields(self, ask_module, sample, own_pool):
        records = []
        for text, stop in ((reply(specification=GOOD), "end_turn"),
                           (reply(refusal={"reason": "out_of_scope"}), "end_turn"),
                           ("nonsense", "end_turn"), (reply(specification=GOOD), "max_tokens")):
            records += run(ask_module, sample, text, stop_reason=stop)[2].records
        records += run(ask_module, sample, question="")[2].records
        records += run(ask_module, own_pool.open())[2].records
        assert ask_module.RECORD_FIELDS == FIELDS
        assert [tuple(r) for r in records] == [FIELDS] * 6

    def test_an_answer_is_recorded_in_full(self, ask_module, sample):
        from src import prompt as prompt_module
        asked, client, audit = run(ask_module, sample, role=entity("AT"))
        record, a = audit.records[0], asked.result
        assert re.fullmatch(r"[0-9a-f]{32}", record["run_id"]) and record["run_id"] == asked.run_id
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", record["at"])
        assert record["role"] == {"name": "entity_controller", "entity": "AT",
                                  "view": "entity view: AT"}
        assert record["question"] == QUESTION
        assert record["provenance"] == a.provenance
        request = client.calls[0]
        assert record["prompt"] == {
            "system_sha256": prompt_module.sha256(request["system"]),
            "schema_sha256": prompt_module.sha256(prompt_module.canonical(
                request["output_config"]["format"]["schema"])),
            "request_sha256": prompt_module.sha256(prompt_module.canonical(request))}
        (call,) = record["calls"]
        assert tuple(call) == CALL_FIELDS
        assert (call["purpose"], call["model"], call["effort"], call["outcome"],
                call["stop_reason"], call["request_id"], call["served_by"]) == (
            "specification", config.MODEL, config.EFFORT, "ok", "end_turn", "req_fake",
            config.MODEL)
        assert record["model_text_raw"] == reply(specification=GOOD)
        assert record["specification"] == {
            "comparison": "none", "dimensions": [], "filters": [], "limit": 100,
            "metric": "revenue", "period": {"kind": "fy", "value": "FY2026"}, "sort": None,
            "version": "Budget"}
        assert list(record["specification"]) == sorted(record["specification"])
        assert (record["outcome"], record["sql"], record["sql_sha256"], record["result_sha256"],
                record["row_count"], record["truncated"]) == (
            "answered", a.sql, a.sql_sha256, a.result_sha256, a.row_count, a.truncated)
        assert record["coverage"] == {"months_asked": list(a.coverage.months_asked),
                                      "months_held": list(a.coverage.months_held),
                                      "compared_with": None}
        assert record["gaps"] == [list(g) for g in a.gaps]
        assert (record["engine_detail"], record["asks_for_a_cause"]) == (None, False)

    def test_the_specification_is_the_canonical_one(self, ask_module, sample):
        text = reply(specification={**GOOD, "metric": "REVENUE", "version": "budget",
                                    "filters": [{"dimension": "region",
                                                 "values": ["central europe"]}]}, cause=True)
        _, _, audit = run(ask_module, sample, text)
        record = audit.records[0]
        assert record["specification"]["metric"] == "revenue"
        assert record["specification"]["version"] == "Budget"
        assert record["specification"]["filters"] == [{"dimension": "region",
                                                       "values": ["Central Europe"]}]
        assert record["model_text_raw"] == text and record["asks_for_a_cause"] is True

    def test_a_reply_the_record_cannot_hold_as_data_is_kept_as_text(self, ask_module, sample):
        # NaN and a lone surrogate parse; the record holds the text alone, escaped.
        for text in (reply(specification=GOOD).replace('"limit": 100', '"limit": NaN'),
                     reply(specification={**GOOD, "filters": [{"dimension": "account",
                                                               "values": ["\ud800"]}]},
                           ).replace("\\ud800", "\ud800")):
            asked, _, audit = run(ask_module, sample, text)
            assert asked.result.code in ("specification_invalid", "unknown_member")
            assert audit.records[0]["model_text_raw"] == text

    def test_no_key_and_no_exception_text_in_a_record(self, ask_module, sample):
        error = fakes.status_error(fakes.anthropic.AuthenticationError, 401,
                                   "invalid x-api-key stand-in-KEY-TEXT")
        _, _, audit = run(ask_module, sample, client=fakes.FakeClient(replies=[error],
                                                                      api_key="stand-in"))
        line = audit.lines[0]
        assert "KEY-TEXT" not in line and "AuthenticationError" not in line
        assert "Traceback" not in line


# -- Call counts --------------------------------------------------------------------------------------

class TestCallCounts:
    def test_one_call_whatever_the_reply(self, ask_module, sample):
        for text, stop in ((reply(specification=GOOD), "end_turn"),
                           (reply(refusal={"reason": "out_of_scope"}), "end_turn"),
                           ("[]", "end_turn"), ("", "end_turn"),
                           (reply(specification={**GOOD, "limit": 0}), "end_turn"),
                           (reply(specification=GOOD), "refusal"),
                           (reply(specification=GOOD), "max_tokens")):
            _, client, _ = run(ask_module, sample, text, stop_reason=stop)
            assert len(client.calls) == 1, text

    def test_none_before_the_call_can_be_made(self, ask_module, sample, own_pool):
        assert run(ask_module, sample, question="")[1].calls == []
        assert run(ask_module, own_pool.open())[1].calls == []
        closed = own_pool.open()
        closed.close()
        assert run(ask_module, closed)[1].calls == []

    def test_a_fault_of_the_client_is_raised_after_one_call(self, ask_module, sample):
        client = fakes.FakeClient(replies=[RuntimeError("a fault in this code")])
        with pytest.raises(RuntimeError):
            ask_module.ask(client, sample, group(), QUESTION, audit=Recorder())
        assert len(client.calls) == 1


# -- The schema is the same for every role, and nothing leaks across roles ----------------------------

def other_patterns(code: str) -> list:
    own = ENTITIES[code][1]
    found = []
    for other, (name, region) in ENTITIES.items():
        if other != code:
            found += [rf"\b{other}\b", rf"(?i){re.escape(name)}"]
            if region != own:
                found.append(rf"(?i){re.escape(region)}")
    return found


class TestAcrossRoles:
    def test_the_schema_sent_is_the_same_bytes_and_hash_for_every_role(self, ask_module,
                                                                       sample):
        from src import prompt as prompt_module
        sent, hashes = set(), set()
        for role in (group(), entity("ES"), entity("AT"), entity("DE")):
            _, client, audit = run(ask_module, sample, role=role)
            sent.add(prompt_module.canonical(client.calls[0]["output_config"]["format"]
                                             ["schema"]))
            hashes.add(audit.records[0]["prompt"]["schema_sha256"])
        assert len(sent) == 1 and len(hashes) == 1

    @pytest.mark.parametrize("code", list(ENTITIES))
    def test_no_other_entity_in_the_request_or_the_record(self, ask_module, sample, code):
        _, client, audit = run(ask_module, sample, role=entity(code))
        request = client.calls[0]
        record = {k: v for k, v in audit.records[0].items()
                  if k not in ("question", "model_text_raw")}
        for text in (request["system"], json.dumps(request["output_config"]),
                     json.dumps(record, ensure_ascii=False)):
            assert [p for p in other_patterns(code) if re.search(p, text)] == [], code

    def test_the_request_is_the_prompts_own(self, ask_module, sample):
        from src import prompt as prompt_module
        from src import roles
        _, client, audit = run(ask_module, sample, role=entity("ES"))
        made = prompt_module.build(sample.registry(),
                                   roles.members_for(entity("ES"), sample),
                                   sample.dimensions().versions, QUESTION)
        assert client.calls == [made.request]
        assert audit.records[0]["prompt"] == made.hashes()


# -- Nothing the model writes reaches the engine as SQL ------------------------------------------------

class TestNoModelTextInTheSQL:
    def test_the_sql_is_the_canonical_specifications_byte_for_byte(self, ask_module, sample):
        text = reply(specification={**GOOD, "metric": "ReVeNuE", "version": "BUDGET",
                                    "filters": [{"dimension": "entity",
                                                 "values": ["calder foods austria"]}]})
        asked, _, _ = run(ask_module, sample, text)
        canonical = sample.answer({**GOOD, "filters": [{"dimension": "entity",
                                                        "values": ["AT"]}]}, group())
        assert asked.result.sql == canonical.sql
        for written in ("calder foods austria", "BUDGET", "ReVeNuE"):
            assert written not in asked.result.sql
