"""The one call function (src/model_call.py), tested with fake clients and the loopback stand-in.

It builds the request from config.py, sends text only, never sends
temperature, top_p or top_k (current models reject them) and refuses a
caller who asks, maps every API failure to its code, and returns a typed
result that records the model and the effort for the audit.

Phase 0 verdict, finding 7: the comparison model shared EFFORT. It now has
COMPARISON_EFFORT, and None leaves effort out of its request entirely.

Part 3b (the reviewer's Phase 3 brief, 4.4). The result gains the stop reason, the text as
received (kept even when it does not parse) and the request id. The stop reason is read first:
refusal is model_refused, max_tokens is reply_cut_off, and anything but end_turn is
unreadable_reply; only then is the text parsed. A reply nested too deeply to parse is
unreadable_reply, never an exception. A thinking block is passed over; a structured reply with
more than one text block is unreadable_reply. The recording fake allows one model: the tests of
the comparison model give it config.COMPARISON_MODEL. The real SDK is run against the loopback
stand-in for what the fake cannot show: how it sends output_config, and how it reads the stop
reason, the usage and the request id. Neither the fake nor the stand-in leaves the machine.

3b (text), decision 1 of the Stop 1 verdict: the seam's client has max_retries 0, so a 529 or a
lost connection is one request, shown through httpx.MockTransport and through the stand-in.
"""

import io
import json

import anthropic
import httpx
import pytest

import config
from src import model_call
from streamlit_app.lib import claude_client
from tests import fakes
from tests.fakes import REQUEST, FakeClient, status_error
from tests.test_errors import OTHER_CASES, STATUS_CASES

SAMPLING = ("temperature", "top_p", "top_k")
SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}},
          "required": ["answer"], "additionalProperties": False}
STAND_IN_KEY = "stand-in"


def _call(client, **kwargs):
    return model_call.call_model(client, system="You answer briefly.",
                                 user="Revenue: £2.48m; budget £2.59m.", **kwargs)


def _scripted(*replies, model=config.MODEL):
    return FakeClient(replies=list(replies), model=model)


class TestTheRequest:
    def test_the_request_is_built_from_config(self):
        client = FakeClient(reply="Ready.")
        result = _call(client)
        assert client.calls == [{
            "model": config.MODEL, "max_tokens": config.MAX_TOKENS,
            "system": "You answer briefly.",
            "messages": [{"role": "user", "content": "Revenue: £2.48m; budget £2.59m."}],
            "output_config": {"effort": config.EFFORT}}]
        assert (result.ok, result.text, result.code) == (True, "Ready.", None)

    def test_a_schema_asks_for_structured_output(self):
        client = FakeClient(reply=json.dumps({"answer": "ready"}))
        result = _call(client, schema=SCHEMA)
        assert client.calls[0]["output_config"] == {
            "effort": config.EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}}
        assert result.data == {"answer": "ready"}

    def test_the_comparison_model_is_the_only_other_model(self):
        # Changed on purpose in 3b: the recording fake allows one model, so this one is given
        # the comparison model.
        client = FakeClient(model=config.COMPARISON_MODEL)
        assert _call(client, model=config.COMPARISON_MODEL).model == config.COMPARISON_MODEL
        assert client.calls[0]["model"] == config.COMPARISON_MODEL
        with pytest.raises(ValueError):
            _call(client, model="some-other-model")
        assert len(client.calls) == 1

    def test_the_effort_is_a_known_level(self):
        client = FakeClient()
        assert _call(client, effort="low").effort == "low"
        with pytest.raises(ValueError):
            _call(client, effort="extreme")
        assert len(client.calls) == 1


class TestFinding7ComparisonEffort:
    def test_the_comparison_effort_is_configured_and_equals_effort_for_now(self):
        assert config.COMPARISON_EFFORT == config.EFFORT
        assert config.COMPARISON_EFFORT in model_call.EFFORTS

    def test_build_request_with_no_effort_leaves_the_key_out_entirely(self):
        request = model_call.build_request(system="s", user="u", effort=None)
        assert "output_config" not in request
        assert "effort" not in json.dumps(request)

    def test_build_request_with_no_effort_keeps_the_schema(self):
        request = model_call.build_request(system="s", user="u", schema=SCHEMA, effort=None)
        assert request["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}
        assert "effort" not in json.dumps(request)

    def test_the_comparison_model_uses_the_comparison_effort(self, monkeypatch):
        # A value unlike EFFORT, so the test can tell which one was sent. Changed on purpose
        # in 3b: one fake per model, since the recording fake allows one.
        monkeypatch.setattr(model_call, "COMPARISON_EFFORT", "low")
        comparison, main_client = FakeClient(model=config.COMPARISON_MODEL), FakeClient()
        compared = _call(comparison, model=config.COMPARISON_MODEL)
        main = _call(main_client)
        assert comparison.calls[0]["output_config"] == {"effort": "low"}
        assert main_client.calls[0]["output_config"] == {"effort": config.EFFORT}
        assert (compared.effort, main.effort) == ("low", config.EFFORT)

    def test_none_omits_effort_from_the_comparison_run_only(self, monkeypatch):
        # Changed on purpose in 3b: one fake per model. An output_config without effort (a
        # comparison effort of None) is allowed by the fake.
        monkeypatch.setattr(model_call, "COMPARISON_EFFORT", None)
        reply = json.dumps({"answer": "ready"})
        comparison = FakeClient(reply=reply, model=config.COMPARISON_MODEL)
        main_client = FakeClient(reply=reply)
        compared = _call(comparison, model=config.COMPARISON_MODEL)
        structured = _call(comparison, model=config.COMPARISON_MODEL, schema=SCHEMA)
        main = _call(main_client)
        assert "output_config" not in comparison.calls[0]
        assert "effort" not in json.dumps(comparison.calls[0]) + json.dumps(comparison.calls[1])
        assert comparison.calls[1]["output_config"] == {
            "format": {"type": "json_schema", "schema": SCHEMA}}
        assert main_client.calls[0]["output_config"] == {"effort": config.EFFORT}
        assert compared.ok and structured.data == {"answer": "ready"} and main.ok
        assert compared.audit_record()["effort"] is None

    def test_an_effort_for_a_comparison_model_that_takes_none_is_refused(self, monkeypatch):
        monkeypatch.setattr(model_call, "COMPARISON_EFFORT", None)
        client = FakeClient(model=config.COMPARISON_MODEL)
        with pytest.raises(ValueError, match="COMPARISON_EFFORT"):
            _call(client, model=config.COMPARISON_MODEL, effort="high")
        assert client.calls == []


class TestSampling:
    def test_no_call_carries_a_sampling_parameter(self):
        # Changed on purpose in 3b: one fake per model.
        client = FakeClient(reply=json.dumps({"answer": "ready"}))
        comparison = FakeClient(model=config.COMPARISON_MODEL)
        _call(client)
        _call(client, schema=SCHEMA)
        _call(comparison, model=config.COMPARISON_MODEL, effort="high")
        calls = client.calls + comparison.calls
        assert len(calls) == 3
        assert not [k for call in calls for k in call if k in SAMPLING]

    @pytest.mark.parametrize("name", SAMPLING)
    def test_a_caller_asking_for_sampling_is_refused_and_nothing_is_sent(self, name):
        client = FakeClient()
        with pytest.raises(model_call.SamplingRefused, match=name):
            _call(client, **{name: 0.5})
        assert client.calls == []

    def test_any_other_argument_is_refused(self):
        client = FakeClient()
        with pytest.raises(TypeError):
            _call(client, stream=True)
        assert client.calls == []


class TestTextOnly:
    @pytest.mark.parametrize("user", [b"member,value\nRevenue,1\n", io.BytesIO(b"x"), None])
    def test_only_text_is_sent_never_a_file(self, user):
        client = FakeClient()
        with pytest.raises(TypeError):
            model_call.call_model(client, system="s", user=user)
        assert client.calls == []


class TestFailures:
    @pytest.mark.parametrize("cls, status, code", STATUS_CASES,
                             ids=[f"{c.__name__}-{s}" for c, s, _ in STATUS_CASES])
    def test_a_failed_call_returns_its_code_never_an_exception(self, cls, status, code):
        result = _call(FakeClient(error=status_error(cls, status)))
        assert (result.ok, result.code, result.text) == (False, code, None)

    @pytest.mark.parametrize("error, code", OTHER_CASES,
                             ids=[type(e).__name__ for e, _ in OTHER_CASES])
    def test_a_lost_connection_returns_its_code(self, error, code):
        assert _call(FakeClient(error=error)).code == code

    def test_an_error_that_is_not_the_apis_is_raised(self):
        with pytest.raises(RuntimeError):
            _call(FakeClient(error=RuntimeError("a fault in this code")))

    def test_a_reply_without_text_is_unreadable(self):
        assert _call(FakeClient(reply=None)).code == "unreadable_reply"

    @pytest.mark.parametrize("reply", ["not json", "[1, 2]", ""])
    def test_a_structured_reply_that_is_not_an_object_is_unreadable(self, reply):
        assert _call(FakeClient(reply=reply), schema=SCHEMA).code == "unreadable_reply"


class TestTheAuditRecord:
    def test_a_call_records_its_model_and_effort(self):
        # Changed on purpose in 3b: the record carries the stop reason, the request id, the
        # text as received and the SDK's version.
        assert _call(FakeClient()).audit_record() == {
            "model": config.MODEL, "effort": config.EFFORT, "outcome": "ok",
            "stop_reason": "end_turn", "request_id": "req_fake", "served_by": config.MODEL,
            "input_tokens": 12, "output_tokens": 3, "raw_text": "Ready.",
            "sdk_version": anthropic.__version__}

    def test_a_failed_call_records_its_code_and_no_exception(self):
        record = _call(FakeClient(error=anthropic.APITimeoutError(request=REQUEST))).audit_record()
        assert record == {"model": config.MODEL, "effort": config.EFFORT,
                          "outcome": "service_unavailable", "stop_reason": None,
                          "request_id": None, "served_by": None, "input_tokens": None,
                          "output_tokens": None, "raw_text": None,
                          "sdk_version": anthropic.__version__}
        assert "Error" not in json.dumps(record)


# -- Part 3b: the stop reason, the text as received and the request id ---------------------------

GOOD_JSON = json.dumps({"answer": "ready"})


class TestTheStopReasonIsReadFirst:
    def test_refusal_is_model_refused_even_with_text_that_parses(self):
        result = _call(_scripted(fakes.Reply(GOOD_JSON, stop_reason="refusal")), schema=SCHEMA)
        assert (result.ok, result.code, result.data, result.text) == (
            False, "model_refused", None, None)
        assert (result.stop_reason, result.raw_text) == ("refusal", GOOD_JSON)

    def test_max_tokens_is_reply_cut_off_and_the_text_is_never_parsed(self):
        cut = '{"answer": "rea'
        result = _call(_scripted(fakes.Reply(cut, stop_reason="max_tokens")), schema=SCHEMA)
        assert (result.code, result.stop_reason, result.raw_text, result.data) == (
            "reply_cut_off", "max_tokens", cut, None)
        whole = _call(_scripted(fakes.Reply(GOOD_JSON, stop_reason="max_tokens")),
                      schema=SCHEMA)
        assert (whole.code, whole.data) == ("reply_cut_off", None)

    @pytest.mark.parametrize("stop", ["stop_sequence", "tool_use", "pause_turn", None,
                                      "something_new"])
    def test_any_other_stop_reason_is_unreadable(self, stop):
        result = _call(_scripted(fakes.Reply(GOOD_JSON, stop_reason=stop)), schema=SCHEMA)
        assert (result.code, result.data, result.stop_reason) == ("unreadable_reply", None, stop)

    def test_a_refusal_without_a_schema_is_model_refused_too(self):
        result = _call(_scripted(fakes.Reply("I would rather not.", stop_reason="refusal")))
        assert (result.code, result.text) == ("model_refused", None)

    def test_end_turn_is_parsed(self):
        result = _call(_scripted(fakes.Reply(GOOD_JSON)), schema=SCHEMA)
        assert (result.ok, result.data, result.stop_reason) == (True, {"answer": "ready"},
                                                                "end_turn")


class TestTheTextAsReceivedAndTheRequestId:
    @pytest.mark.parametrize("text", ["not json", "[1, 2]", '{"answer": "ready"} trailing',
                                      "﻿" + GOOD_JSON])
    def test_the_text_is_kept_when_it_does_not_parse(self, text):
        result = _call(_scripted(fakes.Reply(text)), schema=SCHEMA)
        assert (result.code, result.raw_text) == ("unreadable_reply", text)

    def test_the_request_id_and_usage_are_kept(self):
        result = _call(_scripted(fakes.Reply(GOOD_JSON, request_id="req_abc", input_tokens=1079,
                                             output_tokens=93)), schema=SCHEMA)
        assert (result.request_id, result.input_tokens, result.output_tokens) == (
            "req_abc", 1079, 93)
        assert result.audit_record()["request_id"] == "req_abc"

    def test_a_reply_with_no_request_id_records_none(self):
        assert _call(_scripted(fakes.Reply("Ready.", request_id=None))).request_id is None

    def test_a_failed_call_keeps_no_text(self):
        result = _call(FakeClient(error=status_error(anthropic.RateLimitError, 429)))
        assert (result.raw_text, result.stop_reason, result.request_id) == (None, None, None)


def _nested(depth: int) -> str:
    return '{"answer": ' + "[" * depth + "]" * depth + "}"


def _parses(text: str) -> bool:
    try:
        json.loads(text)
    except RecursionError:
        return False
    return True


class TestADeepReplyNeverRaises:
    @pytest.mark.parametrize("depth", [1_000, 10_000, 100_000])
    def test_a_deep_reply_is_data_or_unreadable_never_an_exception(self, depth):
        # How deep json.loads can go depends on the Python and the platform's stack, so the
        # test asks it first. Where it cannot parse, the reply is unreadable_reply; where it can,
        # the reply is data. Either way nothing is raised.
        text = _nested(depth)
        result = _call(_scripted(fakes.Reply(text)), schema=SCHEMA)
        if _parses(text):
            assert result.ok and isinstance(result.data, dict)
        else:
            assert (result.code, result.raw_text) == ("unreadable_reply", text)

    def test_a_recursion_error_while_parsing_is_unreadable(self, monkeypatch):
        def too_deep(text, *args, **kwargs):
            raise RecursionError("maximum recursion depth exceeded")
        monkeypatch.setattr(model_call.json, "loads", too_deep)
        assert _call(_scripted(fakes.Reply(GOOD_JSON)), schema=SCHEMA).code == "unreadable_reply"

    def test_a_number_too_long_to_read_is_unreadable(self):
        text = '{"answer": ' + "9" * 5000 + "}"
        assert _call(_scripted(fakes.Reply(text)), schema=SCHEMA).code == "unreadable_reply"


class TestTheTextBlocks:
    def test_a_thinking_block_before_the_text_is_passed_over(self):
        reply = fakes.Reply(blocks=(fakes.THINKING_BLOCK, {"type": "text", "text": GOOD_JSON}))
        result = _call(_scripted(reply), schema=SCHEMA)
        assert (result.ok, result.data, result.raw_text) == (True, {"answer": "ready"},
                                                             GOOD_JSON)

    def test_a_structured_reply_with_two_text_blocks_is_unreadable(self):
        reply = fakes.Reply(blocks=({"type": "text", "text": GOOD_JSON},
                                    {"type": "text", "text": GOOD_JSON}))
        result = _call(_scripted(reply), schema=SCHEMA)
        assert (result.code, result.raw_text) == ("unreadable_reply", GOOD_JSON + GOOD_JSON)

    def test_without_a_schema_the_first_text_block_is_the_text(self):
        reply = fakes.Reply(blocks=(fakes.THINKING_BLOCK, {"type": "text", "text": "One."},
                                    {"type": "text", "text": "Two."}))
        result = _call(_scripted(reply))
        assert (result.ok, result.text, result.raw_text) == (True, "One.", "One.Two.")

    def test_a_thinking_block_alone_is_unreadable(self):
        reply = fakes.Reply(blocks=(fakes.THINKING_BLOCK,))
        result = _call(_scripted(reply), schema=SCHEMA)
        assert (result.code, result.raw_text) == ("unreadable_reply", None)


# -- Part 3b: the real SDK against the loopback stand-in (4.4) --------------------------------------

@pytest.fixture
def through_the_sdk(monkeypatch):
    """A function that calls call_model through the real SDK against the stand-in serving the
    replies given, with the client the seam builds (since 3b (text), with max_retries=0, so each
    reply in the list is one request), or with the SDK's own default client when sdk_default is
    true. It returns (result, what the stand-in received)."""
    def run(replies, *, schema=SCHEMA, sdk_default=False):
        with fakes.StandInAPI(replies=replies) as api:
            monkeypatch.setenv("ANTHROPIC_BASE_URL", api.url)
            client = (anthropic.Anthropic(api_key=STAND_IN_KEY) if sdk_default
                      else claude_client.build_client(STAND_IN_KEY))
            result = model_call.call_model(client, system="You answer briefly.",
                                           user="What was revenue in June?", schema=schema)
        monkeypatch.delenv("ANTHROPIC_BASE_URL")
        return result, api.received
    return run


class TestThroughTheSDK:
    def test_a_good_reply(self, through_the_sdk):
        result, received = through_the_sdk([(200, fakes.message(GOOD_JSON))])
        assert (result.ok, result.data, result.stop_reason, result.request_id) == (
            True, {"answer": "ready"}, "end_turn", "req_standin_1")
        assert (result.input_tokens, result.output_tokens, result.served_by) == (
            1079, 93, config.MODEL)
        (request,) = received
        assert request["path"] == "/v1/messages"
        assert request["headers"]["x-api-key"] == STAND_IN_KEY

    def test_the_sdk_sends_the_five_keys_and_output_config_as_built(self, through_the_sdk):
        _, (request,) = through_the_sdk([(200, fakes.message(GOOD_JSON))])
        body = request["body"]
        assert set(body) == set(fakes.REQUEST_KEYS)
        assert body == model_call.build_request(system="You answer briefly.",
                                                user="What was revenue in June?",
                                                schema=SCHEMA)
        assert body["output_config"] == {"effort": config.EFFORT,
                                         "format": {"type": "json_schema", "schema": SCHEMA}}
        assert not set(SAMPLING) & set(body)

    def test_refusal(self, through_the_sdk):
        result, received = through_the_sdk([(200, fakes.message(None,
                                                                stop_reason="refusal"))])
        assert (result.code, result.stop_reason, result.request_id) == (
            "model_refused", "refusal", "req_standin_1")
        assert len(received) == 1

    def test_max_tokens(self, through_the_sdk):
        result, _ = through_the_sdk([(200, fakes.message('{"answer": "re',
                                                         stop_reason="max_tokens"))])
        assert (result.code, result.raw_text) == ("reply_cut_off", '{"answer": "re')

    def test_a_400(self, through_the_sdk):
        result, received = through_the_sdk([(400, fakes.BAD_REQUEST_BODY)])
        assert (result.ok, result.code, result.raw_text) == (False, "bad_request", None)
        assert len(received) == 1

    def test_a_529(self, through_the_sdk):
        result, received = through_the_sdk([(529, fakes.OVERLOADED_BODY)])
        assert (result.ok, result.code) == (False, "service_unavailable")
        assert len(received) == 1

    def test_a_thinking_block_before_the_text(self, through_the_sdk):
        body = fakes.message(blocks=[fakes.THINKING_BLOCK, {"type": "text", "text": GOOD_JSON}])
        result, _ = through_the_sdk([(200, body)])
        assert (result.ok, result.data) == (True, {"answer": "ready"})

    def test_the_seams_client_sends_one_request_for_a_529(self, through_the_sdk):
        # Changed on purpose in 3b (text), decision 1 of the Stop 1 verdict: the seam's client
        # no longer keeps the SDK's default, which makes three requests on a 529.
        result, received = through_the_sdk([(529, fakes.OVERLOADED_BODY)])
        assert (result.code, len(received)) == ("service_unavailable", 1)
        result, received = through_the_sdk([(529, fakes.OVERLOADED_BODY)], sdk_default=True)
        assert (result.code, len(received)) == ("service_unavailable", 3)

    def test_a_retried_529_takes_the_next_reply_in_the_list(self, through_the_sdk):
        # Changed on purpose in 3b (text): the stand-in's list, shown with the SDK's own default
        # client, since the seam's no longer retries.
        result, received = through_the_sdk([(529, fakes.OVERLOADED_BODY),
                                            (200, fakes.message(GOOD_JSON))], sdk_default=True)
        assert (result.ok, result.request_id, len(received)) == (True, "req_standin_2", 2)
        result, received = through_the_sdk([(529, fakes.OVERLOADED_BODY),
                                            (200, fakes.message(GOOD_JSON))])
        assert (result.code, len(received)) == ("service_unavailable", 1)


class TestDecision1TheSeamDoesNotRetry:
    """Decision 1 of the Stop 1 verdict: build_client gives a client with max_retries 0, so one
    call_model is one request and the record sees every request (rule 8: no retry anywhere).
    Shown through httpx.MockTransport and the real SDK, which open no socket."""

    def _one_call(self, respond):
        sent = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(request)
            return respond(request)
        client = claude_client.build_client(STAND_IN_KEY).with_options(
            http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        result = model_call.call_model(client, system="s", user="What was revenue in June?",
                                       schema=SCHEMA)
        return result, sent

    def test_a_client_from_build_client_has_max_retries_0(self):
        assert claude_client.build_client(STAND_IN_KEY).max_retries == 0
        assert claude_client.build_client(STAND_IN_KEY).with_options().max_retries == 0

    def test_a_529_sends_exactly_one_request(self):
        result, sent = self._one_call(
            lambda request: httpx.Response(529, json=fakes.OVERLOADED_BODY))
        assert (result.code, len(sent)) == ("service_unavailable", 1)

    def test_a_connection_error_sends_exactly_one_request(self):
        def refuse(request):
            raise httpx.ConnectError("stand-in: no connection", request=request)
        result, sent = self._one_call(refuse)
        assert (result.code, len(sent)) == ("service_unavailable", 1)

    def test_a_good_reply_is_still_one_request(self):
        result, sent = self._one_call(lambda request: httpx.Response(
            200, json=fakes.message(GOOD_JSON), headers={"request-id": "req_mock_1"}))
        assert (result.ok, result.request_id, len(sent)) == (True, "req_mock_1", 1)
