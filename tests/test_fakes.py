"""The stand-ins themselves (tests/fakes.py), part 3b (the reviewer's Phase 3 brief, 4.4 and 8).

The recording fake, FakeClient, serves scripted replies in order, each with its stop reason,
usage and request id, and fails the call itself when a request carries anything the project
never sends: a key outside model, max_tokens, system, messages and output_config; an
output_config key outside effort and format; a max_tokens other than config.MAX_TOKENS; or a
model other than the one it allows (config.MODEL unless a test names the comparison model).
So every test that uses it is also the test that no sampling parameter is sent.

The loopback stand-in, StandInAPI, can serve a list of replies in order, the last one repeated,
and sends a request id header with each, as the API does; the real SDK reads its request id
from that header.
"""

import json

import httpx
import pytest

import config
from tests import fakes

GOOD = {"model": config.MODEL, "max_tokens": config.MAX_TOKENS, "system": "s",
        "messages": [{"role": "user", "content": "u"}],
        "output_config": {"effort": config.EFFORT}}


def _create(client, **changes):
    request = {**GOOD, **changes}
    return client.messages.create(**{k: v for k, v in request.items() if v is not None})


class TestTheFakesRules:
    def test_a_request_of_the_five_keys_is_answered_and_recorded(self):
        client = fakes.FakeClient(reply="Ready.")
        reply = _create(client)
        assert client.calls == [GOOD] and client.refused == []
        assert [b.text for b in reply.content if b.type == "text"] == ["Ready."]

    @pytest.mark.parametrize("key", ["temperature", "top_p", "top_k", "stream", "metadata",
                                     "tools", "thinking", "stop_sequences"])
    def test_a_key_outside_the_five_fails_the_call_itself(self, key):
        client = fakes.FakeClient()
        with pytest.raises(fakes.FakeClientRefused, match=key):
            _create(client, **{key: 0.5})
        assert len(client.calls) == 1 and len(client.refused) == 1

    @pytest.mark.parametrize("key", ["temperature", "budget_tokens", "task_budget"])
    def test_an_output_config_key_outside_effort_and_format_fails(self, key):
        client = fakes.FakeClient()
        with pytest.raises(fakes.FakeClientRefused, match=key):
            _create(client, output_config={"effort": config.EFFORT, key: 1})

    def test_effort_and_format_together_or_alone_pass(self):
        client = fakes.FakeClient()
        schema = {"type": "object"}
        for output_config in ({"effort": "high"}, {"format": {"type": "json_schema",
                                                               "schema": schema}},
                              {"effort": "low", "format": {"type": "json_schema",
                                                           "schema": schema}}):
            _create(client, output_config=output_config)
        assert len(client.calls) == 3 and client.refused == []

    @pytest.mark.parametrize("tokens", [16, config.MAX_TOKENS + 1, None])
    def test_a_max_tokens_other_than_the_configured_one_fails(self, tokens):
        client = fakes.FakeClient()
        with pytest.raises(fakes.FakeClientRefused, match="max_tokens"):
            _create(client, max_tokens=tokens)

    def test_a_model_other_than_the_one_allowed_fails(self):
        with pytest.raises(fakes.FakeClientRefused, match="model"):
            _create(fakes.FakeClient(), model=config.COMPARISON_MODEL)
        with pytest.raises(fakes.FakeClientRefused, match="model"):
            _create(fakes.FakeClient(model=config.COMPARISON_MODEL))
        assert _create(fakes.FakeClient(model=config.COMPARISON_MODEL),
                       model=config.COMPARISON_MODEL).model == config.COMPARISON_MODEL

    def test_a_refused_call_is_refused_before_any_scripted_error(self):
        client = fakes.FakeClient(error=RuntimeError("scripted"))
        with pytest.raises(fakes.FakeClientRefused):
            _create(client, top_p=0.9)


class TestScriptedReplies:
    def test_replies_are_served_in_order_with_stop_reasons_usage_and_request_ids(self):
        client = fakes.FakeClient(replies=[
            fakes.Reply('{"a": 1}', stop_reason="end_turn", input_tokens=1079,
                        output_tokens=93, request_id="req_one"),
            fakes.Reply(None, stop_reason="refusal", input_tokens=5, output_tokens=0,
                        request_id="req_two")])
        first, second = _create(client), _create(client)
        assert (first.stop_reason, first.usage.input_tokens, first.usage.output_tokens,
                first._request_id) == ("end_turn", 1079, 93, "req_one")
        assert [b.text for b in first.content] == ['{"a": 1}']
        assert (second.stop_reason, second.content, second._request_id) == (
            "refusal", [], "req_two")
        assert first.model == config.MODEL

    def test_a_scripted_error_is_raised_in_its_turn(self):
        error = fakes.status_error(fakes.anthropic.RateLimitError, 429)
        client = fakes.FakeClient(replies=[error, fakes.Reply("Ready.")])
        with pytest.raises(fakes.anthropic.RateLimitError):
            _create(client)
        assert [b.text for b in _create(client).content] == ["Ready."]

    def test_a_script_that_has_run_out_fails_the_call(self):
        client = fakes.FakeClient(replies=[fakes.Reply("only one")])
        _create(client)
        with pytest.raises(fakes.FakeClientRefused, match="no reply left"):
            _create(client)
        assert len(client.calls) == 2

    def test_blocks_replace_the_text_and_keep_their_order(self):
        client = fakes.FakeClient(replies=[fakes.Reply(blocks=(fakes.THINKING_BLOCK,
                                                               {"type": "text", "text": "x"}))])
        reply = _create(client)
        assert [b.type for b in reply.content] == ["thinking", "text"]
        assert reply.content[1].text == "x"

    def test_the_hook_runs_before_the_reply(self):
        seen = []
        client = fakes.FakeClient(replies=[fakes.Reply("Ready.")],
                                  on_create=lambda request: seen.append(request["model"]))
        _create(client)
        assert seen == [config.MODEL]

    def test_the_plain_reply_keeps_its_old_shape(self):
        reply = _create(fakes.FakeClient(reply="Ready."))
        assert (reply.stop_reason, reply.usage.input_tokens, reply.usage.output_tokens) == (
            "end_turn", 12, 3)
        assert reply._request_id == "req_fake"
        assert _create(fakes.FakeClient(reply=None)).content == []


class TestTheStandInServesAList:
    def _post(self, api) -> httpx.Response:
        return httpx.post(f"{api.url}/v1/messages", json={"model": config.MODEL})

    def test_replies_are_served_in_order_and_the_last_is_repeated(self):
        replies = [(529, fakes.OVERLOADED_BODY), (200, fakes.message("one")),
                   (400, fakes.BAD_REQUEST_BODY)]
        with fakes.StandInAPI(replies=replies) as api:
            statuses = [self._post(api).status_code for _ in range(5)]
        assert statuses == [529, 200, 400, 400, 400]
        assert len(api.received) == 5

    def test_each_reply_carries_a_request_id_header(self):
        with fakes.StandInAPI(replies=[(200, fakes.message("one"))]) as api:
            ids = [self._post(api).headers.get("request-id") for _ in range(2)]
        assert ids == ["req_standin_1", "req_standin_2"]

    def test_a_single_status_and_body_still_work(self):
        with fakes.StandInAPI() as api:
            reply = self._post(api)
            api.status, api.body = 200, fakes.message("changed")
            changed = self._post(api)
        assert reply.status_code == 404
        assert changed.json()["content"] == [{"type": "text", "text": "changed"}]

    def test_the_message_body_is_the_apis_shape(self):
        body = fakes.message('{"a": 1}', stop_reason="max_tokens",
                             blocks=[fakes.THINKING_BLOCK, {"type": "text", "text": "y"}])
        assert set(body) == {"id", "type", "role", "model", "content", "stop_reason",
                             "stop_sequence", "usage"}
        assert (body["type"], body["role"], body["model"], body["stop_reason"]) == (
            "message", "assistant", config.MODEL, "max_tokens")
        assert [b["type"] for b in body["content"]] == ["thinking", "text"]
        assert json.loads(json.dumps(body)) == body
