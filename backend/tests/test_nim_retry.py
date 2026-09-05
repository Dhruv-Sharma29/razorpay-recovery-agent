"""Retry policy for NIM calls.

Measured against the live endpoint, roughly a third of calls at concurrency 5
came back 503 or timed out. Retrying those is the difference between a demo
where the model answers and one where it silently falls back — but retrying
the wrong things wastes a payment pipeline's time on a request that can never
succeed.
"""

from __future__ import annotations

import httpx
import pytest

from app.nim_retry import is_retryable, with_retries


def _status_error(code: int) -> httpx.HTTPStatusError:
    response = httpx.Response(code, request=httpx.Request("POST", "https://x"))
    return httpx.HTTPStatusError("boom", request=response.request, response=response)


class TestWhatIsWorthRetrying:
    @pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
    def test_load_shedding_and_server_faults_are_retried(self, code: int) -> None:
        assert is_retryable(_status_error(code)) is True

    @pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
    def test_a_bad_request_or_key_is_not_retried(self, code: int) -> None:
        """Sending the same wrong request again only wastes time."""
        assert is_retryable(_status_error(code)) is False

    def test_timeouts_and_connection_failures_are_retried(self) -> None:
        assert is_retryable(httpx.TimeoutException("slow")) is True
        assert is_retryable(httpx.ConnectError("refused")) is True

    def test_a_programming_error_is_not_retried(self) -> None:
        assert is_retryable(ValueError("bug")) is False


class TestRetryBehaviour:
    def test_a_successful_call_is_made_once(self) -> None:
        calls = []
        result = with_retries(
            lambda: calls.append(1) or "ok", label="t", sleep=lambda _: None
        )
        assert result == "ok"
        assert len(calls) == 1

    def test_a_transient_failure_is_retried_then_succeeds(self) -> None:
        attempts = []

        def flaky():
            attempts.append(1)
            if len(attempts) < 3:
                raise _status_error(503)
            return "recovered"

        assert (
            with_retries(flaky, label="t", max_retries=2, sleep=lambda _: None)
            == "recovered"
        )
        assert len(attempts) == 3

    def test_the_budget_is_respected_and_the_error_re_raised(self) -> None:
        """The caller keeps its own structured fallback, so we re-raise."""
        attempts = []

        def always_503():
            attempts.append(1)
            raise _status_error(503)

        with pytest.raises(httpx.HTTPStatusError):
            with_retries(always_503, label="t", max_retries=2, sleep=lambda _: None)
        assert len(attempts) == 3, "one attempt plus two retries"

    def test_a_non_retryable_failure_fails_immediately(self) -> None:
        attempts = []

        def bad_key():
            attempts.append(1)
            raise _status_error(401)

        with pytest.raises(httpx.HTTPStatusError):
            with_retries(bad_key, label="t", max_retries=5, sleep=lambda _: None)
        assert len(attempts) == 1

    def test_zero_retries_makes_one_attempt(self) -> None:
        attempts = []

        def fails():
            attempts.append(1)
            raise _status_error(503)

        with pytest.raises(httpx.HTTPStatusError):
            with_retries(fails, label="t", max_retries=0, sleep=lambda _: None)
        assert len(attempts) == 1

    def test_backoff_grows_and_is_jittered(self) -> None:
        """Lockstep retries are how a struggling endpoint is kept struggling."""
        delays: list[float] = []

        def fails():
            raise _status_error(503)

        with pytest.raises(httpx.HTTPStatusError):
            with_retries(fails, label="t", max_retries=3, sleep=delays.append)
        assert len(delays) == 3
        # Full jitter, so each delay is bounded by its doubling ceiling.
        assert delays[0] <= 0.75
        assert delays[1] <= 1.5
        assert delays[2] <= 3.0
        assert all(d >= 0 for d in delays)
