"""The one call function (src/model_call.py), tested with fake clients only.

It builds the request from config.py, sends text only, never sends
temperature, top_p or top_k (current models reject them) and refuses a
caller who asks, maps every API failure to its code, and returns a typed
result that records the model and the effort for the audit.
"""

import io
import json

import anthropic
import pytest

import config
from src import model_call
from tests.fakes import REQUEST, FakeClient, status_error
from tests.test_errors import OTHER_CASES, STATUS_CASES

SAMPLING = ("temperature", "top_p", "top_k")
SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}},
          "required": ["answer"], "additionalProperties": False}


def _call(client, **kwargs):
    return model_call.call_model(client, system="You answer briefly.",
                                 user="Revenue: £2.48m; budget £2.59m.", **kwargs)


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
        client = FakeClient()
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


class TestSampling:
    def test_no_call_carries_a_sampling_parameter(self):
        client = FakeClient(reply=json.dumps({"answer": "ready"}))
        _call(client)
        _call(client, schema=SCHEMA)
        _call(client, model=config.COMPARISON_MODEL, effort="high")
        assert len(client.calls) == 3
        assert not [k for call in client.calls for k in call if k in SAMPLING]

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
        assert _call(FakeClient()).audit_record() == {
            "model": config.MODEL, "effort": config.EFFORT, "outcome": "ok",
            "served_by": config.MODEL, "input_tokens": 12, "output_tokens": 3}

    def test_a_failed_call_records_its_code_and_no_exception(self):
        record = _call(FakeClient(error=anthropic.APITimeoutError(request=REQUEST))).audit_record()
        assert record == {"model": config.MODEL, "effort": config.EFFORT,
                          "outcome": "service_unavailable", "served_by": None,
                          "input_tokens": None, "output_tokens": None}
        assert "Error" not in json.dumps(record)
