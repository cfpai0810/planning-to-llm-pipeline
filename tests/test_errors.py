"""The error mapping (src/errors.py): one short code and one plain line per failure.

The screen, the PDF and the audit use the same line, and the audit stores
the code. No exception's class name reaches any of them. The mapping imports
only anthropic and config, so the command line and the tests load it
without Streamlit.
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
from src import errors
from tests import conftest

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
    (anthropic.APIStatusError, 402, "bad_request"),
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
    def test_the_seven_codes(self):
        assert errors.CODES == ("key_rejected", "key_forbidden", "model_not_found", "bad_request",
                                "rate_limited", "service_unavailable", "unreadable_reply")
        assert set(errors.MESSAGES) == set(errors.CODES)

    @pytest.mark.parametrize("code", ["key_rejected", "key_forbidden", "model_not_found",
                                      "bad_request", "rate_limited", "service_unavailable",
                                      "unreadable_reply"])
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
