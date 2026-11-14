"""The error mapping (src/errors.py): one short code and one plain line per failure.

The screen, the PDF and the audit use the same line, and the audit stores
the code. No exception's class name reaches any of them. The mapping imports
only anthropic and config, so the command line and the tests load it
without Streamlit.

Phase 0 verdict, finding 1: a visitor whose account has no credit was told
the fault was in this app. A 402, and a 400 whose message names the credit
balance, are now billing_problem, tested with built errors and through the
real SDK against the local stand-in API (tests/fakes.py).

Part 3b (the reviewer's Phase 3 brief, 4.4): two codes of a call that succeeded but gave no
specification, each with fixed wording: model_refused, for a reply that stopped on refusal,
and reply_cut_off, for one that stopped on max_tokens. There are ten codes now.
"""

import ast
import re
import subprocess
import sys
from pathlib import Path

import anthropic
import httpx
import pytest

import config
from src import errors, model_call
from streamlit_app.lib import claude_client
from tests import conftest
from tests.fakes import CREDIT_BODY, CREDIT_MESSAGE, StandInAPI

ROOT = Path(__file__).resolve().parent.parent
CLASS_NAME = re.compile(r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)*(?:Error|Exception)\b")
REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def status_error(cls, status: int) -> anthropic.APIStatusError:
    return cls("stand-in failure", response=httpx.Response(status, request=REQUEST), body=None)


STATUS_CASES = [
    (anthropic.AuthenticationError, 401, "key_rejected"),
    (anthropic.PermissionDeniedError, 403, "key_forbidden"),
    (anthropic.NotFoundError, 404, "model_not_found"),
    (anthropic.BadRequestError, 400, "bad_request"),
    (anthropic.RequestTooLargeError, 413, "bad_request"),
    (anthropic.UnprocessableEntityError, 422, "bad_request"),
    (anthropic.APIStatusError, 402, "billing_problem"),
    (anthropic.ConflictError, 409, "service_unavailable"),
    (anthropic.RateLimitError, 429, "rate_limited"),
    # The SDK raises InternalServerError for every 5xx it has no class for.
    (anthropic.InternalServerError, 500, "service_unavailable"),
    (anthropic.InternalServerError, 503, "service_unavailable"),
    (anthropic.InternalServerError, 504, "service_unavailable"),
    (anthropic.OverloadedError, 529, "service_unavailable"),
]
OTHER_CASES = [
    (anthropic.APIConnectionError(request=REQUEST), "service_unavailable"),
    (anthropic.APITimeoutError(request=REQUEST), "service_unavailable"),
    (anthropic.APIResponseValidationError(response=httpx.Response(200, request=REQUEST),
                                          body=None), "unreadable_reply"),
]


class TestTheMapping:
    @pytest.mark.parametrize("cls, status, code", STATUS_CASES,
                             ids=[f"{c.__name__}-{s}" for c, s, _ in STATUS_CASES])
    def test_each_status_gives_its_code(self, cls, status, code):
        assert errors.failure_code(status_error(cls, status)) == code

    @pytest.mark.parametrize("error, code", OTHER_CASES,
                             ids=[type(e).__name__ for e, _ in OTHER_CASES])
    def test_a_lost_connection_or_an_unreadable_reply_gives_its_code(self, error, code):
        assert errors.failure_code(error) == code

    @pytest.mark.parametrize("error", [RuntimeError("down"), ValueError("odd"), KeyError("x")])
    def test_an_error_that_is_not_the_apis_has_no_code(self, error):
        assert errors.failure_code(error) is None

    def test_an_error_wrapped_with_from_still_maps(self):
        try:
            try:
                raise status_error(anthropic.AuthenticationError, 401)
            except anthropic.APIError as exc:
                raise RuntimeError("wrapped") from exc
        except RuntimeError as wrapped:
            assert errors.failure_code(wrapped) == "key_rejected"

    def test_an_error_raised_while_handling_one_still_maps(self):
        try:
            try:
                raise status_error(anthropic.NotFoundError, 404)
            except anthropic.APIError:
                raise RuntimeError("raised while handling")
        except RuntimeError as later:
            assert later.__cause__ is None and errors.failure_code(later) == "model_not_found"

    def test_a_chain_that_loops_ends(self):
        first, second = RuntimeError("a"), RuntimeError("b")
        first.__context__, second.__context__ = second, first
        assert errors.failure_code(first) is None


class TestTheWords:
    def test_the_ten_codes(self):
        # Changed on purpose in 3b: model_refused and reply_cut_off join the eight.
        assert errors.CODES == ("key_rejected", "key_forbidden", "model_not_found", "bad_request",
                                "rate_limited", "service_unavailable", "unreadable_reply",
                                "billing_problem", "model_refused", "reply_cut_off")
        assert set(errors.MESSAGES) == set(errors.CODES)

    def test_the_two_new_lines_are_the_proposed_wording(self):
        assert (errors.MODEL_REFUSED, errors.REPLY_CUT_OFF) == ("model_refused", "reply_cut_off")
        assert errors.message("model_refused") == (
            "The model declined to answer this question. Try asking it differently.")
        assert errors.message("reply_cut_off") == (
            "The model's reply was cut off before it finished. Try a shorter or simpler "
            "question.")

    def test_the_billing_and_bad_request_lines_are_the_approved_wording(self):
        assert errors.message("billing_problem") == (
            "Anthropic reports a problem with billing on this account. Check your plan and "
            "credits in your Anthropic account.")
        assert errors.message("bad_request") == (
            "Anthropic rejected the request. This is most often a fault in this app, so please "
            "report it. If your Anthropic account has run out of credit, that can cause it too.")

    @pytest.mark.parametrize("code", ["key_rejected", "key_forbidden", "model_not_found",
                                      "bad_request", "rate_limited", "service_unavailable",
                                      "unreadable_reply", "billing_problem", "model_refused",
                                      "reply_cut_off"])
    def test_each_code_has_one_plain_line_ending_with_its_code(self, code):
        line = errors.line(code)
        assert line == f"{errors.message(code)} Code: {code}"
        assert line.count(".") >= 1 and not CLASS_NAME.search(line)
        assert "`" not in line and "Traceback" not in line

    def test_a_missing_model_is_named_and_reported_not_waited_for(self):
        assert config.MODEL in errors.message("model_not_found")
        for code in ("bad_request", "model_not_found"):
            assert "temporary" not in errors.message(code)

    def test_an_unknown_code_is_refused(self):
        with pytest.raises(KeyError):
            errors.message("no_such_code")


def _api_error(status: int, message: str, *, in_body: bool = True) -> anthropic.APIStatusError:
    """An error as the SDK builds it: the API's words in its body, or in its text only."""
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": message}}
    response = httpx.Response(status, request=REQUEST, json=body)
    text = f"Error code: {status} - {body}" if in_body else message
    cls = anthropic.BadRequestError if status == 400 else anthropic.APIStatusError
    return cls(text, response=response, body=body if in_body else None)


class TestFinding1BillingProblem:
    """A 402, and a 400 whose message names the credit balance in any case, are billing_problem."""

    @pytest.mark.parametrize("message", [
        CREDIT_MESSAGE, CREDIT_MESSAGE.upper(), "Your Credit Balance is too low.",
        "credit balance exhausted"], ids=["the API's words", "upper case", "title case", "short"])
    @pytest.mark.parametrize("in_body", [True, False], ids=["in the body", "in the text only"])
    def test_a_400_naming_the_credit_balance(self, message, in_body):
        assert errors.failure_code(_api_error(400, message, in_body=in_body)) == "billing_problem"

    @pytest.mark.parametrize("message", ["stand-in failure", CREDIT_MESSAGE],
                             ids=["any message", "the credit message"])
    def test_a_402_whatever_its_message(self, message):
        assert errors.failure_code(_api_error(402, message)) == "billing_problem"

    @pytest.mark.parametrize("message", [
        "messages: field required", "max_tokens: 16000 is above the model's limit",
        "Your credit card was declined", "balance"])
    def test_a_400_with_any_other_message_stays_bad_request(self, message):
        assert errors.failure_code(_api_error(400, message)) == "bad_request"

    def test_a_wrapped_billing_error_still_maps(self):
        try:
            try:
                raise _api_error(400, CREDIT_MESSAGE)
            except anthropic.APIError as exc:
                raise RuntimeError("wrapped") from exc
        except RuntimeError as wrapped:
            assert errors.failure_code(wrapped) == "billing_problem"
        try:
            try:
                raise _api_error(402, "stand-in failure")
            except anthropic.APIError:
                raise RuntimeError("raised while handling")
        except RuntimeError as later:
            assert later.__cause__ is None and errors.failure_code(later) == "billing_problem"

    def test_the_apis_words_are_never_in_the_line(self):
        for code in ("billing_problem", "bad_request"):
            line = errors.line(code)
            assert CREDIT_MESSAGE not in line and "credit balance" not in line.lower()


STAND_IN_KEY = "-".join(["sk", "ant", "STANDIN", "errors", "not", "a", "key"])
OTHER_400 = {"type": "error", "error": {"type": "invalid_request_error",
                                        "message": "messages: field required"}}
PAYMENT_402 = {"type": "error", "error": {"type": "billing_error",
                                          "message": "Payment required."}}


@pytest.fixture
def stand_in(monkeypatch):
    """The local stand-in API (tests/fakes.py); each test sets its status and body."""
    with StandInAPI() as api:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", api.url)
        yield api


def _through(api: StandInAPI, status: int, body: dict) -> anthropic.APIError:
    """One call through the real SDK to the stand-in: the exception the SDK raises."""
    api.status, api.body = status, body
    client = claude_client.build_client(STAND_IN_KEY)
    with pytest.raises(anthropic.APIError) as raised:
        client.messages.create(model=config.MODEL, max_tokens=16,
                               messages=[{"role": "user", "content": "stand-in"}])
    return raised.value


class TestFinding1ThroughTheStandInServer:
    """The real SDK against the local stand-in API, which answers as the API does."""

    def test_the_exact_400_body_is_a_billing_problem(self, stand_in):
        exc = _through(stand_in, 400, CREDIT_BODY)
        assert isinstance(exc, anthropic.BadRequestError) and exc.status_code == 400
        assert errors.failure_code(exc) == "billing_problem"
        assert len(stand_in.received) == 1          # a 400 is not retried

    def test_a_402_is_a_billing_problem(self, stand_in):
        exc = _through(stand_in, 402, PAYMENT_402)
        assert exc.status_code == 402 and errors.failure_code(exc) == "billing_problem"

    def test_a_400_with_another_message_stays_bad_request(self, stand_in):
        exc = _through(stand_in, 400, OTHER_400)
        assert isinstance(exc, anthropic.BadRequestError)
        assert errors.failure_code(exc) == "bad_request"

    def test_the_cause_chain_still_maps(self, stand_in):
        exc = _through(stand_in, 400, CREDIT_BODY)
        try:
            raise RuntimeError("wrapped") from exc
        except RuntimeError as wrapped:
            assert errors.failure_code(wrapped) == "billing_problem"

    @pytest.mark.parametrize("status, body, code", [
        (400, CREDIT_BODY, "billing_problem"), (402, PAYMENT_402, "billing_problem"),
        (400, OTHER_400, "bad_request")], ids=["400 credit balance", "402", "400 other"])
    def test_call_model_returns_the_code(self, stand_in, status, body, code):
        stand_in.status, stand_in.body = status, body
        result = model_call.call_model(claude_client.build_client(STAND_IN_KEY), system="s",
                                       user="Revenue: £2.48m")
        assert (result.ok, result.code) == (False, code)


class TestTheImports:
    def test_the_mapping_imports_only_anthropic_and_config(self):
        tree = ast.parse((ROOT / "src" / "errors.py").read_text(encoding="utf-8"))
        modules = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        modules |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        outside = {m.partition(".")[0] for m in modules} - set(sys.stdlib_module_names)
        assert outside == {"anthropic", "config"}

    def test_loading_the_mapping_loads_no_streamlit(self):
        code = "import sys; import src.errors; print('streamlit' in sys.modules)"
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                                text=True, env=conftest.subprocess_env(), timeout=120)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "False"
