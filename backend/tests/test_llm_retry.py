"""A model call must survive a rate limit or an overloaded endpoint.

A Vertex quota answer (429 RESOURCE_EXHAUSTED) or a 503 "overloaded" lasts tens
of seconds. async_chat used to try three times with 1 s and 2 s between them and
experiment.py re-ran the step once, immediately, so all of it failed inside ~5 s,
the exception reached the session's outer handler and the persona's whole
session ended 'error' with every remaining step and the survey lost.

Nothing here sleeps or touches the network: time, sleeping and randomness are the
module's own injection points (gpt._monotonic / _sleep / _uniform).
"""
import asyncio
import heapq
import inspect
import re
from types import SimpleNamespace

import httpx
import pytest
from litellm import exceptions as lx
from litellm.types.router import RouterRateLimitError

from simulated_web_agent.agent import gpt
from simulated_web_agent.main import experiment


class VirtualTime:
    """Stands in for gpt._monotonic / _sleep / _uniform.

    auto (default): sleep() advances the clock at once. With auto off, a sleeper
    parks until run() moves time to its wake-up, so coroutines running together
    see each other's waits as they would in real time.
    """

    def __init__(self):
        self.now = 1000.0
        self.frac = 0.5  # where in [a, b] uniform() lands: 0 = a, 1 = b
        self.auto = True
        self.sleeps: list[float] = []
        self.events: list[tuple] = []
        self._parked: list = []
        self._n = 0

    def monotonic(self):
        return self.now

    def uniform(self, a, b):
        return a + (b - a) * self.frac

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        assert len(self.sleeps) < 200, "runaway wait loop"  # fail, do not hang, if the code spins
        self.events.append(("sleep", seconds))
        if self.auto:
            self.now += seconds
            await asyncio.sleep(0)
            return
        fut = asyncio.get_running_loop().create_future()
        self._n += 1
        heapq.heappush(self._parked, (self.now + seconds, self._n, fut))
        await fut

    async def run(self, *coros):
        tasks = [asyncio.ensure_future(c) for c in coros]
        for _ in range(2000):
            if all(t.done() for t in tasks):
                break
            for _ in range(5):
                await asyncio.sleep(0)  # a real yield, so the tasks can run
            if not all(t.done() for t in tasks) and self._parked:
                wake, _, fut = heapq.heappop(self._parked)
                self.now = max(self.now, wake)
                fut.set_result(None)
        else:
            for t in tasks:
                t.cancel()
            raise AssertionError("coroutines did not finish")
        return await asyncio.gather(*tasks, return_exceptions=True)


@pytest.fixture
def clock(monkeypatch):
    vt = VirtualTime()
    monkeypatch.setattr(gpt, "_monotonic", vt.monotonic)
    monkeypatch.setattr(gpt, "_sleep", vt.sleep)
    monkeypatch.setattr(gpt, "_uniform", vt.uniform)
    monkeypatch.setattr(gpt, "_cooldown_until", 0.0)  # restored after the test
    monkeypatch.setattr(gpt, "_warned_env", set())
    for name in ("UXAGENT_LLM_RETRY_ATTEMPTS", "UXAGENT_LLM_RETRY_MAX_WAIT"):
        monkeypatch.delenv(name, raising=False)
    return vt


def scripted(clock, *outcomes):
    """An async function that raises or returns each outcome in turn; the last one repeats."""
    calls = []

    async def fn(*args, **kwargs):
        calls.append(clock.now)
        clock.events.append(("call", clock.now))
        out = outcomes[min(len(calls), len(outcomes)) - 1]
        if isinstance(out, BaseException):
            raise out
        return out

    fn.calls = calls
    return fn


def rate_limit(message="quota", headers=None):
    response = httpx.Response(429, headers=headers or {}, request=httpx.Request("POST", "https://x"))
    return lx.RateLimitError(message, "vertex_ai", "gemini", response=response)


def unavailable(message="the model is overloaded"):
    return lx.ServiceUnavailableError(message, "vertex_ai", "gemini")


class HttpError(Exception):
    def __init__(self, status_code, message="boom"):
        super().__init__(message)
        self.status_code = status_code


class ResponseError(Exception):
    """An exception whose status code lives only on .response, as with an httpx error."""

    def __init__(self, status_code, message="boom"):
        super().__init__(message)
        self.response = SimpleNamespace(status_code=status_code)


def retrying(fn, times=3):
    return gpt.async_retry(times=times)(fn)


# --- classification --------------------------------------------------------------

RATE_LIMITED = [
    pytest.param(rate_limit(), id="litellm RateLimitError 429"),
    pytest.param(HttpError(429), id="status 429 on any exception"),
    pytest.param(Exception("429 RESOURCE_EXHAUSTED: Quota exceeded for online_prediction_requests"), id="RESOURCE_EXHAUSTED"),
    pytest.param(Exception("429 Too Many Requests"), id="Too Many Requests"),
    pytest.param(RuntimeError("provider rate limit hit"), id="rate limit wording"),
    pytest.param(RouterRateLimitError("gemini", 5.0, False, []), id="router cooldown"),
    pytest.param(ResponseError(429), id="429 only on .response"),
]
TRANSIENT_ONLY = [
    pytest.param(unavailable(), id="ServiceUnavailableError 503"),
    pytest.param(lx.InternalServerError("oops", "vertex_ai", "gemini"), id="InternalServerError"),
    pytest.param(lx.Timeout("slow", "gemini", "vertex_ai"), id="litellm Timeout"),
    pytest.param(lx.APIConnectionError("connection reset", "vertex_ai", "gemini"), id="APIConnectionError"),
    pytest.param(asyncio.TimeoutError(), id="asyncio.TimeoutError"),
    pytest.param(HttpError(502), id="status 502"),
    pytest.param(HttpError(503), id="status 503"),
    pytest.param(HttpError(504), id="status 504"),
    pytest.param(HttpError(500), id="status 500"),
    pytest.param(ResponseError(503), id="503 only on .response"),
    pytest.param(
        lx.APIConnectionError("google.auth.exceptions.RefreshError: Unable to fetch a token from the "
                              "metadata server", "vertex_ai", "gemini"),
        id="a plain RefreshError can be a flaky metadata server"),
    pytest.param(Exception("The model is overloaded. Please try again later."), id="overloaded"),
    pytest.param(Exception('{"status": "UNAVAILABLE"}'), id="UNAVAILABLE"),
    pytest.param(Exception("Quota exhausted for this minute"), id="quota"),
]
PERMANENT = [
    pytest.param(lx.AuthenticationError("bad key", "vertex_ai", "gemini"), id="AuthenticationError"),
    pytest.param(lx.BadRequestError("bad", "gemini", "vertex_ai"), id="BadRequestError"),
    pytest.param(lx.NotFoundError("no such model", "gemini", "vertex_ai"), id="NotFoundError 404"),
    pytest.param(lx.ContentPolicyViolationError("blocked", "gemini", "vertex_ai"), id="content policy"),
    pytest.param(ValueError("nope"), id="ValueError"),
    pytest.param(Exception("Invalid JSON in response"), id="JSON error"),
    pytest.param(Exception("No JSON object found in the response"), id="no JSON object"),
    pytest.param(HttpError(400), id="status 400"),
    pytest.param(HttpError(401), id="status 401"),
    pytest.param(lx.BadRequestError("requires a quota project, which is not set", "gemini", "vertex_ai"),
                 id="class beats a 'quota' message"),
    pytest.param(
        lx.APIConnectionError("Vertex_aiException\ngoogle.auth.exceptions.DefaultCredentialsError: "
                              "File x.json was not found.", "vertex_ai", "gemini"),
        id="bad credentials litellm wraps as a connection error"),
    pytest.param(
        lx.APIConnectionError("Vertex_aiException\ngoogle.auth.exceptions.RefreshError: Reauthentication is "
                              "needed. Please run `gcloud auth application-default login`.",
                              "vertex_ai", "gemini"),
        id="expired user credentials"),
    pytest.param(
        lx.APIConnectionError("Vertex_aiException\nFileNotFoundError: [Errno 2] No such file or directory: "
                              "'key.json'", "vertex_ai", "gemini"),
        id="credentials path that does not exist"),
]


@pytest.mark.parametrize("exc", RATE_LIMITED)
def test_rate_limit_answers_are_transient_and_rate_limited(exc):
    assert gpt.is_transient_error(exc)
    assert gpt._classify(exc) == gpt._RATE_LIMITED


@pytest.mark.parametrize("exc", TRANSIENT_ONLY)
def test_overload_and_upstream_errors_are_transient_but_not_a_rate_limit(exc):
    assert gpt.is_transient_error(exc)
    assert gpt._classify(exc) == gpt._TRANSIENT


@pytest.mark.parametrize("exc", PERMANENT)
def test_errors_waiting_cannot_fix_are_not_transient(exc):
    assert not gpt.is_transient_error(exc)


def test_cancellation_is_never_an_error_to_retry():
    assert not gpt.is_transient_error(asyncio.CancelledError())


# --- the schedule ------------------------------------------------------------------

@pytest.mark.parametrize("frac", [0.0, 0.37, 1.0])
async def test_a_transient_error_that_clears_on_the_third_try_succeeds(clock, frac):
    clock.frac = frac
    fn = scripted(clock, unavailable(), rate_limit(), "answer")
    assert await retrying(fn)() == "answer"
    assert len(fn.calls) == 3
    first, second = clock.sleeps
    assert 2.0 <= first <= 4.0  # ceiling 4 s: ceiling/2 + jitter up to ceiling/2
    assert 4.0 <= second <= 8.0  # then 8 s


async def test_each_wait_ceiling_doubles_and_stops_at_45_seconds(clock):
    clock.frac = 1.0
    last = unavailable("last")
    fn = scripted(clock, unavailable(), unavailable(), unavailable(), unavailable(), unavailable(), last)
    with pytest.raises(lx.ServiceUnavailableError) as raised:
        await retrying(fn)()
    assert raised.value is last  # the last exception, unchanged
    assert len(fn.calls) == 6  # default attempts for a transient error
    assert clock.sleeps == [4.0, 8.0, 16.0, 32.0, 45.0]


async def test_a_permanent_error_keeps_todays_schedule_three_tries_one_then_two_seconds(clock):
    errors = [ValueError("a"), ValueError("b"), ValueError("c")]
    fn = scripted(clock, *errors)
    with pytest.raises(ValueError) as raised:
        await retrying(fn)()
    assert raised.value is errors[2]
    assert len(fn.calls) == 3
    assert clock.sleeps == [1, 2]  # and no wait after the last try


async def test_a_json_error_is_not_waited_for(clock):
    fn = scripted(clock, Exception("Invalid JSON in response"))
    with pytest.raises(Exception, match="Invalid JSON"):
        await retrying(fn)()
    assert len(fn.calls) == 3 and clock.sleeps == [1, 2]


async def test_one_permanent_error_caps_the_call_even_if_the_next_answer_looks_transient(clock):
    """After an auth failure litellm's router answers 'no deployments available,
    try again' for a few seconds. That is not a reason to wait for minutes."""
    fn = scripted(clock, lx.AuthenticationError("bad key", "vertex_ai", "gemini"),
                  RouterRateLimitError("gemini", 5.0, False, []))
    with pytest.raises(RouterRateLimitError):
        await retrying(fn)()
    assert len(fn.calls) == 3


async def test_success_needs_no_waiting_at_all(clock):
    fn = scripted(clock, "ok")
    assert await retrying(fn)() == "ok"
    assert clock.sleeps == []


async def test_the_wrapper_keeps_the_function_identity():
    async def documented():
        """doc"""

    wrapped = gpt.async_retry(times=3)(documented)
    assert wrapped.__name__ == "documented" and wrapped.__doc__ == "doc"


# --- what the server tells us ----------------------------------------------------------

async def test_retry_after_header_is_honoured(clock):
    clock.frac = 0.0
    fn = scripted(clock, rate_limit(headers={"Retry-After": "30"}), "ok")
    await retrying(fn)()
    assert clock.sleeps == [30.0]  # computed minimum would have been 2 s


@pytest.mark.parametrize("message,expected", [
    ("429 RESOURCE_EXHAUSTED. Please retry in 12.5s.", 12.5),
    ('{"@type": "RetryInfo", "retryDelay": "20s"}', 20.0),
    ('{\\"retryDelay\\": \\"21s\\"}', 21.0),
    ("No deployments available for selected model, Try again in 7.0 seconds.", 7.0),
    ("Please retry in 9000ms", 9.0),
])
async def test_a_retry_hint_in_the_message_is_honoured(clock, message, expected):
    clock.frac = 0.0
    fn = scripted(clock, rate_limit(message), "ok")
    await retrying(fn)()
    assert clock.sleeps == [expected]


async def test_a_long_server_hint_is_capped_at_90_seconds(clock):
    fn = scripted(clock, rate_limit(headers={"retry-after": "600"}), "ok")
    await retrying(fn)()
    assert clock.sleeps == [90.0]


async def test_a_hint_never_shortens_the_computed_wait(clock):
    clock.frac = 0.0
    fn = scripted(clock, rate_limit(headers={"Retry-After": "0.5"}), "ok")
    await retrying(fn)()
    assert clock.sleeps == [2.0]


async def test_a_junk_retry_after_is_ignored(clock):
    clock.frac = 0.0
    fn = scripted(clock, rate_limit(headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}), "ok")
    await retrying(fn)()
    assert clock.sleeps == [2.0]


def _with_attr(exc, name, value):
    setattr(exc, name, value)
    return exc


@pytest.mark.parametrize("holder", [
    pytest.param(lambda: _with_attr(rate_limit(), "litellm_response_headers", {"retry-after": "21"}),
                 id="litellm_response_headers dict"),
    pytest.param(lambda: _with_attr(rate_limit(), "litellm_response_headers", httpx.Headers({"Retry-After": "21"})),
                 id="litellm_response_headers httpx.Headers"),
    pytest.param(lambda: _with_attr(rate_limit(), "headers", {"Retry-After": "21"}), id="headers on the exception"),
    pytest.param(lambda: rate_limit(headers={"Retry-After": "21"}), id="headers on the response"),
])
async def test_retry_after_is_read_wherever_litellm_keeps_the_headers(clock, holder):
    clock.frac = 0.0
    await retrying(scripted(clock, holder(), "ok"))()
    assert clock.sleeps == [21.0]  # litellm's own mapped 429 has no headers on .response: the real ones are on the exception


@pytest.mark.parametrize("header,message,expected", [
    ("10", "Please retry in 30s", 30.0),
    ("30", "Please retry in 10s", 30.0),
])
async def test_the_longest_of_several_hints_wins(clock, header, message, expected):
    clock.frac = 0.0
    await retrying(scripted(clock, rate_limit(message, headers={"Retry-After": header}), "ok"))()
    assert clock.sleeps == [expected]


# --- the wait budget -----------------------------------------------------------------------

async def test_the_total_wait_budget_ends_the_retrying_and_reraises_the_last_error(clock):
    errors = [rate_limit(headers={"Retry-After": "90"}) for _ in range(5)]
    fn = scripted(clock, *errors)
    with pytest.raises(lx.RateLimitError) as raised:
        await retrying(fn)()
    assert raised.value is errors[1]
    assert len(fn.calls) == 2
    assert clock.sleeps == [90.0]  # a second 90 s would pass the 150 s budget


async def test_the_budget_is_configurable(clock, monkeypatch):
    clock.frac = 0.0
    monkeypatch.setenv("UXAGENT_LLM_RETRY_MAX_WAIT", "10")
    fn = scripted(clock, unavailable())
    with pytest.raises(lx.ServiceUnavailableError):
        await retrying(fn)()
    assert clock.sleeps == [2.0, 4.0]  # 2 + 4 spent, the next 8 s would pass 10
    assert len(fn.calls) == 3


async def test_the_attempt_count_is_configurable(clock, monkeypatch):
    monkeypatch.setenv("UXAGENT_LLM_RETRY_ATTEMPTS", "2")
    fn = scripted(clock, unavailable())
    with pytest.raises(lx.ServiceUnavailableError):
        await retrying(fn)()
    assert len(fn.calls) == 2


async def test_a_wait_that_exactly_fits_the_budget_is_taken(clock, monkeypatch):
    clock.frac = 0.0
    monkeypatch.setenv("UXAGENT_LLM_RETRY_MAX_WAIT", "6")
    fn = scripted(clock, unavailable(), unavailable(), "ok")
    assert await retrying(fn)() == "ok"
    assert clock.sleeps == [2.0, 4.0]  # 2 spent, the 4 that follows is exactly what is left


# --- the wall-clock ceiling on one call ---------------------------------------------------

def hanging(clock, seconds, error=asyncio.TimeoutError):
    """A try that runs into its timeout: it takes `seconds`, then fails with a TimeoutError."""
    calls = []

    async def fn():
        calls.append(clock.now)
        clock.now += seconds
        raise error()

    fn.calls = calls
    return fn


async def test_a_hung_endpoint_costs_one_call_at_most_the_time_limit_not_six_timeouts(clock, capsys):
    """Each try of a hung call ends at the 180 s cut-off. The wait budget counts
    sleeping only, so six tries could have taken 6 x 180 s."""
    clock.frac = 0.0
    fn = hanging(clock, 180.0)
    with pytest.raises(asyncio.TimeoutError):
        await retrying(fn)()
    assert len(fn.calls) == 2
    assert clock.now - 1000.0 < gpt._MAX_CALL_SECONDS + 180.0
    last = [l for l in capsys.readouterr().out.splitlines() if l.startswith("[llm]")][-1]
    assert "giving up" in last and "360s limit on one call" in last


@pytest.mark.parametrize("seconds,tries", [(358.0, 2), (359.0, 1)])
async def test_the_limit_counts_the_wait_that_would_follow(clock, seconds, tries):
    clock.frac = 0.0  # the first wait is 2 s: 358 + 2 still fits in 360, 359 + 2 does not
    fn = hanging(clock, seconds)
    with pytest.raises(asyncio.TimeoutError):
        await retrying(fn)()
    assert len(fn.calls) == tries


async def test_a_permanent_error_keeps_its_three_tries_however_long_each_took(clock):
    fn = hanging(clock, 359.0, error=ValueError)
    with pytest.raises(ValueError):
        await retrying(fn)()
    assert len(fn.calls) == 3


async def test_the_time_limit_does_not_cut_fast_failures(clock):
    clock.frac = 1.0
    fn = scripted(clock, unavailable(), unavailable(), unavailable(), unavailable(), unavailable(), "late")
    assert await retrying(fn)() == "late"  # 4+8+16+32+45 s of waiting, nowhere near 360


async def test_the_time_limit_is_shared_with_the_nested_length_retry(chat, clock):
    """The nested async_chat of finish_reason 'length' runs inside the outer call's
    clock, so recursing cannot buy a second 360 s."""
    clock.frac = 0.0
    router = chat(_reply())
    seen = []

    async def acompletion(**kwargs):
        seen.append(kwargs["max_tokens"])
        if len(seen) == 1:
            clock.now += 359.0  # a slow answer, cut off at the token limit
            return _reply('{"a": 1', finish="length")
        raise unavailable()

    router.acompletion = acompletion
    with pytest.raises(lx.ServiceUnavailableError):
        await gpt.async_chat([{"role": "user", "content": "x"}], json_mode=True, max_tokens=1000, log=False)
    assert seen == [1000, 2000]  # 359 s are gone: the nested call's 2 s wait would pass 360


def test_the_defaults():
    assert gpt._transient_attempts() == 6
    assert gpt._max_total_wait() == 150.0


@pytest.mark.parametrize("raw", ["abc", "0", "-3", "2.5", "nan", "inf", "99", "   ", ""])
def test_garbage_attempts_fall_back_to_the_default(monkeypatch, raw):
    monkeypatch.setenv("UXAGENT_LLM_RETRY_ATTEMPTS", raw)
    assert gpt._transient_attempts() == 6


@pytest.mark.parametrize("raw", ["abc", "-1", "nan", "inf", "99999", "", "1e999"])
def test_garbage_max_wait_falls_back_to_the_default(monkeypatch, raw):
    monkeypatch.setenv("UXAGENT_LLM_RETRY_MAX_WAIT", raw)
    assert gpt._max_total_wait() == 150.0


def test_sensible_overrides_are_read(monkeypatch):
    monkeypatch.setenv("UXAGENT_LLM_RETRY_ATTEMPTS", "3")
    monkeypatch.setenv("UXAGENT_LLM_RETRY_MAX_WAIT", "20.5")
    assert gpt._transient_attempts() == 3
    assert gpt._max_total_wait() == 20.5
    monkeypatch.setenv("UXAGENT_LLM_RETRY_MAX_WAIT", "0")
    assert gpt._max_total_wait() == 0.0


# --- the shared cooldown ----------------------------------------------------------------------

async def test_a_rate_limit_answer_sets_the_shared_cooldown(clock, monkeypatch):
    clock.frac = 0.0
    monkeypatch.setenv("UXAGENT_LLM_RETRY_ATTEMPTS", "1")  # this call gives up at once
    with pytest.raises(lx.RateLimitError):
        await retrying(scripted(clock, rate_limit()))()
    assert gpt._cooldown_until == clock.now + 2.0  # news for everyone, retried or not


async def test_an_overload_is_not_a_rate_limit_and_sets_no_cooldown(clock):
    await retrying(scripted(clock, unavailable(), "ok"))()
    assert gpt._cooldown_until == 0.0


async def test_the_next_call_waits_for_a_cooldown_another_call_set(clock):
    clock.frac = 0.0
    gpt.set_cooldown(clock.now + 7.0)
    fn = scripted(clock, "ok")
    assert await retrying(fn)() == "ok"
    assert clock.events == [("sleep", 7.0), ("call", 1007.0)]  # waited first, called after


@pytest.mark.parametrize("frac,extra", [(0.0, 0.0), (1.0, 1.5)])
async def test_the_release_after_a_cooldown_is_jittered_up_to_1_5_seconds(clock, frac, extra):
    clock.frac = frac
    gpt.set_cooldown(clock.now + 7.0)
    await retrying(scripted(clock, "ok"))()
    assert clock.sleeps == [7.0 + extra]


async def test_a_cooldown_that_has_passed_costs_nothing(clock):
    gpt.set_cooldown(clock.now - 1.0)
    await retrying(scripted(clock, "ok"))()
    assert clock.sleeps == []


async def test_a_second_coroutine_waits_for_the_cooldown_the_firsts_429_set(clock):
    clock.auto = False
    clock.frac = 1.0
    first = scripted(clock, rate_limit(), "first done")
    second = scripted(clock, "second done")
    results = await clock.run(retrying(first)(), retrying(second)())
    assert results == ["first done", "second done"]
    # The first call failed at t=1000 and set the cooldown (4 s wait). Without it
    # the second call, which started at the same moment, would have run at 1000.
    assert first.calls[0] == 1000.0
    assert second.calls[0] >= 1004.0


async def test_a_cooldown_pushed_later_while_waiting_is_waited_out_again(clock):
    clock.auto = False
    clock.frac = 0.0
    gpt.set_cooldown(clock.now + 10.0)
    fn = scripted(clock, "ok")

    async def another_429():
        gpt.set_cooldown(clock.now + 30.0)  # while the call above is parked at the gate

    await clock.run(retrying(fn)(), another_429())
    assert fn.calls == [1030.0]  # woke at 1010, saw the later cooldown, waited on


async def test_the_cooldown_only_ever_moves_later(clock):
    assert gpt.set_cooldown(2000.0) == 2000.0
    assert gpt.set_cooldown(1500.0) == 2000.0
    assert gpt._cooldown_until == 2000.0
    assert gpt.set_cooldown(2500.0) == 2500.0


async def test_a_shorter_429_does_not_pull_the_cooldown_back(clock, monkeypatch):
    clock.frac = 0.0
    monkeypatch.setenv("UXAGENT_LLM_RETRY_ATTEMPTS", "1")
    gpt.set_cooldown(clock.now + 500.0)
    monkeypatch.setenv("UXAGENT_LLM_RETRY_MAX_WAIT", "0")  # no waiting at the gate: straight to a short 429
    with pytest.raises(lx.RateLimitError):
        await retrying(scripted(clock, rate_limit()))()
    assert gpt._cooldown_until == 1500.0


async def test_waiting_out_a_cooldown_counts_against_the_call_budget(clock, monkeypatch):
    monkeypatch.setenv("UXAGENT_LLM_RETRY_MAX_WAIT", "20")
    clock.frac = 0.0
    gpt.set_cooldown(clock.now + 10_000.0)
    fn = scripted(clock, "ok")
    assert await retrying(fn)() == "ok"  # goes ahead once the budget is spent
    assert clock.sleeps == [20.0]
    assert len(fn.calls) == 1


# --- cancellation ----------------------------------------------------------------------------------

async def test_cancellation_is_not_retried(clock):
    fn = scripted(clock, asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await retrying(fn)()
    assert len(fn.calls) == 1 and clock.sleeps == []


async def test_cancellation_during_a_backoff_wait_propagates(clock, monkeypatch):
    async def cancelled_sleep(seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(gpt, "_sleep", cancelled_sleep)
    fn = scripted(clock, unavailable())
    with pytest.raises(asyncio.CancelledError):
        await retrying(fn)()
    assert len(fn.calls) == 1


async def test_a_real_task_cancel_ends_the_wait(clock):
    clock.auto = False  # the call parks in its backoff sleep
    task = asyncio.ensure_future(retrying(scripted(clock, unavailable()))())
    for _ in range(5):
        await asyncio.sleep(0)
    assert clock.sleeps  # it is waiting
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# --- the real entry points -----------------------------------------------------------------------------

class _Router:
    def __init__(self, *outcomes):
        self.model_list = []
        self.calls = []
        self._outcomes = outcomes

    async def acompletion(self, **kwargs):
        self.calls.append(kwargs)
        out = self._outcomes[min(len(self.calls), len(self._outcomes)) - 1]
        if isinstance(out, BaseException):
            raise out
        return out


def _reply(content="ok", finish="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(message={"content": content}, finish_reason=finish)])


@pytest.fixture
def chat(clock, monkeypatch):
    def install(*outcomes):
        router = _Router(*outcomes)
        monkeypatch.setattr(gpt, "chat_router", router)
        monkeypatch.setattr(gpt, "slow_chat_router", router)
        return router

    return install


async def test_async_chat_rides_out_a_rate_limit(chat, clock):
    router = chat(rate_limit("429 RESOURCE_EXHAUSTED"), rate_limit("429 RESOURCE_EXHAUSTED"), _reply("hej"))
    assert await gpt.async_chat([{"role": "user", "content": "x"}], log=False) == "hej"
    assert len(router.calls) == 3
    assert len(clock.sleeps) == 2 and all(s >= 2.0 for s in clock.sleeps)


async def test_async_chat_still_gives_a_permanent_error_three_tries(chat, clock):
    router = chat(lx.AuthenticationError("bad key", "vertex_ai", "gemini"))
    with pytest.raises(lx.AuthenticationError):
        await gpt.async_chat([{"role": "user", "content": "x"}], log=False)
    assert len(router.calls) == 3 and clock.sleeps == [1, 2]


async def test_async_chat_waits_for_the_shared_cooldown_before_calling(chat, clock):
    chat(_reply())
    clock.frac = 0.0
    gpt.set_cooldown(clock.now + 9.0)
    await gpt.async_chat([{"role": "user", "content": "x"}], log=False)
    assert clock.sleeps == [9.0]


async def test_a_stalled_chat_call_is_cut_off_at_the_hard_timeout_and_retried(chat, clock, monkeypatch):
    router = chat(_reply("hej"))

    async def stall_once(**kwargs):
        router.calls.append(kwargs)
        if len(router.calls) == 1:
            await asyncio.Event().wait()
        return _reply("hej")

    router.acompletion = stall_once
    monkeypatch.setattr(gpt, "_HARD_TIMEOUT", 0.05)
    answer = await asyncio.wait_for(gpt.async_chat([{"role": "user", "content": "x"}], log=False), 10)
    assert answer == "hej" and len(router.calls) == 2
    assert router.calls[0]["timeout"] == gpt._REQUEST_TIMEOUT


@pytest.mark.parametrize("name,call", [
    ("chat_router", lambda router: router.acompletion(model="gemini", messages=[{"role": "user", "content": "x"}],
                                                      timeout=120)),
    ("slow_chat_router", lambda router: router.acompletion(model="gemini", messages=[{"role": "user", "content": "x"}],
                                                           timeout=120)),
    ("embed_router", lambda router: router.aembedding(model="gemini", input=["a"], timeout=120)),
])
async def test_the_routers_do_not_retry_on_their_own(name, call, monkeypatch):
    """litellm's default of 2 router retries made each try up to 3 HTTP requests that
    the shared cooldown cannot hold back and the wait budget does not count."""
    import litellm

    requests = []

    async def refuse(*args, **kwargs):
        requests.append(kwargs)
        raise unavailable()

    monkeypatch.setattr(litellm, "acompletion", refuse)
    monkeypatch.setattr(litellm, "aembedding", refuse)
    router = getattr(gpt, name)
    assert router.num_retries == 0
    with pytest.raises(lx.ServiceUnavailableError):
        await asyncio.wait_for(call(router), 30)
    assert len(requests) == 1


async def test_the_length_retry_shares_one_wait_budget(chat, clock):
    """finish_reason 'length' calls async_chat again. If that call had a budget of
    its own, recursing would multiply the allowance."""
    clock.frac = 0.0
    hint = "Please retry in 60s"
    router = chat(
        rate_limit(hint),  # outer: waits 60
        _reply('{"a": 1', finish="length"),  # truncated: asks again with a bigger budget
        rate_limit(hint),  # nested: waits 60 (120 spent)
        rate_limit(hint),  # nested: 60 more would pass 150
    )
    with pytest.raises(lx.RateLimitError):
        await gpt.async_chat([{"role": "user", "content": "x"}], json_mode=True, max_tokens=1000, log=False)
    assert clock.sleeps == [60.0, 60.0]
    assert len(router.calls) == 4
    assert router.calls[2]["max_tokens"] == 2000  # it really was the nested call


async def test_the_budget_does_not_leak_into_the_next_call(chat, clock):
    hint = "Please retry in 60s"
    chat(rate_limit(hint), _reply("a"), rate_limit(hint), rate_limit(hint), _reply("b"))
    assert await gpt.async_chat([{"role": "user", "content": "x"}], log=False) == "a"
    assert gpt._wait_budget.get() is None
    # 60 s were spent above; this call's own 120 s must not be added to them (150 s)
    assert await gpt.async_chat([{"role": "user", "content": "x"}], log=False) == "b"


class _EmbedRouter:
    def __init__(self, *outcomes):
        self.calls = []
        self._outcomes = outcomes

    async def aembedding(self, **kwargs):
        self.calls.append(kwargs)
        out = self._outcomes[min(len(self.calls), len(self._outcomes)) - 1]
        if isinstance(out, BaseException):
            raise out
        return out


def _embedding(*vectors):
    return SimpleNamespace(data=[{"embedding": v} for v in vectors])


async def test_embed_text_rides_out_an_overload(clock, monkeypatch):
    router = _EmbedRouter(unavailable(), _embedding([1.0, 2.0]))
    monkeypatch.setattr(gpt, "embed_router", router)
    assert await gpt.embed_text(["a"]) == [[1.0, 2.0]]
    assert len(router.calls) == 2 and len(clock.sleeps) == 1


async def test_embed_text_waits_for_the_shared_cooldown(clock, monkeypatch):
    clock.frac = 0.0
    monkeypatch.setattr(gpt, "embed_router", _EmbedRouter(_embedding([1.0])))
    gpt.set_cooldown(clock.now + 5.0)
    await gpt.embed_text(["a"])
    assert clock.sleeps == [5.0]


async def test_embed_text_sets_the_cooldown_on_a_rate_limit(clock, monkeypatch):
    clock.frac = 0.0
    monkeypatch.setattr(gpt, "embed_router", _EmbedRouter(rate_limit(), _embedding([1.0])))
    await gpt.embed_text(["a"])
    assert gpt._cooldown_until == 1002.0


class _StallingEmbedRouter(_EmbedRouter):
    """The first call is accepted and then never answers: no error, nothing to retry."""

    async def aembedding(self, **kwargs):
        if not self.calls:
            self.calls.append(kwargs)
            await asyncio.Event().wait()
        return await super().aembedding(**kwargs)


async def test_a_stalled_embedding_call_is_cut_off_and_retried(clock, monkeypatch):
    router = _StallingEmbedRouter(_embedding([3.0]))
    monkeypatch.setattr(gpt, "embed_router", router)
    monkeypatch.setattr(gpt, "_HARD_TIMEOUT", 0.05)  # stands for the 180 s cut-off
    result = await asyncio.wait_for(gpt.embed_text(["a"]), 10)  # without a cut-off this never returns
    assert result == [[3.0]]
    assert len(router.calls) == 2 and len(clock.sleeps) == 1
    assert router.calls[0]["timeout"] == gpt._REQUEST_TIMEOUT  # and the router is told its own limit


async def test_embed_text_gives_up_with_the_original_error(clock, monkeypatch, capsys):
    err = lx.AuthenticationError("bad key", "vertex_ai", "gemini")
    monkeypatch.setattr(gpt, "embed_router", _EmbedRouter(err))
    with pytest.raises(lx.AuthenticationError) as raised:
        await gpt.embed_text(["a"])
    assert raised.value is err


# --- what the log says ------------------------------------------------------------------------------

async def test_each_retry_is_one_line_with_attempt_wait_class_and_cooldown(clock, capsys):
    clock.frac = 0.0
    await retrying(scripted(clock, rate_limit("quota"), unavailable(), "ok"))()
    lines = [l for l in capsys.readouterr().out.splitlines() if l.startswith("[llm]")]
    assert len(lines) == 2
    assert "RateLimitError" in lines[0] and "attempt 1/6" in lines[0]
    assert "retrying in 2.0s" in lines[0] and "shared cooldown set" in lines[0]
    assert "ServiceUnavailableError" in lines[1] and "attempt 2/6" in lines[1]
    assert "retrying in 4.0s" in lines[1] and "cooldown" not in lines[1]


# --- the step retry in experiment.py -------------------------------------------------------------------

def test_the_step_retry_pause_is_for_transient_errors_only(clock):
    clock.frac = 0.0
    assert gpt.step_retry_pause(unavailable()) == 5.0
    clock.frac = 1.0
    assert gpt.step_retry_pause(rate_limit()) == 10.0
    assert gpt.step_retry_pause(ValueError("bad")) == 0.0
    assert gpt.step_retry_pause(asyncio.CancelledError()) == 0.0


def test_experiment_pauses_before_it_re_runs_a_failed_step():
    src = inspect.getsource(experiment)
    first = src.index("action = await policy.forward(env, obs)")
    pause = src.index("gpt.step_retry_pause(step_exc)", first)
    rerun = src.index("action = await policy.forward(env, obs)", pause)
    assert re.search(r"await asyncio\.sleep\(gpt\.step_retry_pause\(step_exc\)\)", src[first:rerun])
