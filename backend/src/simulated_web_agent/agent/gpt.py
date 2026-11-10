import asyncio
import contextvars
import functools
import importlib
import json
import math
import os
import random
import re
import threading
import time
from typing import Any, Dict, Optional

from dotenv import load_dotenv

# from litellm import drop_params, token_counter
from litellm.router import Router

from . import context
from .prompt_registry import registry as _prompt_registry

provider = "openai"  # kept for backward-compat; use configure() to change

# Gemini 3.x on Vertex is served from the global endpoint only: every one of
# these models answered 404 in us-central1 (the VERTEXAI_LOCATION this project
# runs with) and 200 on "global" (measured 2026-09-27). Set per model, so the
# deployment's regional VERTEXAI_LOCATION keeps serving everything else.
# The 2.5 family they replace is shut down on 2026-10-16.
_GEMINI_LOCATION = "global"
_GEMINI_FLASH = "vertex_ai/gemini-3.8-flash"
_GEMINI_FLASH_LITE = "vertex_ai/gemini-3.5-flash-lite"
# Pro has no GA 3.x release yet: "gemini-3.1-pro" is a 404, the preview is not.
_GEMINI_PRO = "vertex_ai/gemini-3.1-pro-preview"


# Request labels on every Vertex call, so Google Cloud billing attributes the usage
# to this service (Billing -> Reports -> group by label "name-of-service"). Override
# with UXAGENT_VERTEX_LABELS='{"key": "value"}'; "{}" turns them off.
DEFAULT_VERTEX_LABELS = {"name-of-service": "csaapps_ux-agent_general"}
# Vertex's rule: lowercase letters, digits, "_" and "-"; a key starts with a letter;
# at most 63 characters. One bad label would make every request fail with a 400,
# so what does not fit is dropped (and said) rather than sent.
_LABEL_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
_LABEL_VALUE = re.compile(r"^[a-z0-9_-]{0,63}$")


def vertex_labels() -> Dict[str, str]:
    raw = os.environ.get("UXAGENT_VERTEX_LABELS")
    labels: Any = DEFAULT_VERTEX_LABELS
    if raw is not None and raw.strip():
        try:
            labels = json.loads(raw)
        except ValueError:
            print("UXAGENT_VERTEX_LABELS is not JSON; using the default label")
    if not isinstance(labels, dict):
        return dict(DEFAULT_VERTEX_LABELS)
    out = {}
    for k, v in labels.items():
        if isinstance(k, str) and isinstance(v, str) and _LABEL_KEY.match(k) and _LABEL_VALUE.match(v):
            out[k] = v
        else:
            print(f"Vertex label {k!r}={v!r} ignored: not a valid label")
    return out


chat_router = Router(
    model_list=[
        {
            "model_name": "openai",
            "litellm_params": {
                "model": "openai/gpt-5-mini",
                "reasoning_effort": "minimal",
            },
        },
        {
            "model_name": "gemini",
            "litellm_params": {
                # Thinking is intentionally ON. NOTE: it is not configured here
                # and must not be — `thinking={"type": "disabled"}` is Anthropic
                # syntax that litellm silently drops for Vertex, so this entry
                # used to claim thinking was off while measuring 935/972
                # completion tokens as reasoning. If you ever want it off, the
                # params that actually work are reasoning_effort="disable" or
                # thinkingConfig={"thinkingBudget": 0} — but that changes agent
                # behaviour everywhere, so make it a deliberate call.
                # On 3.8 Flash the default thinks about as much as 2.5 Flash did
                # (150-300 reasoning tokens on plan/act/feedback) but each call is
                # ~1.5 s slower; reasoning_effort="low" measured 0 reasoning tokens
                # and 2.5 Flash's latency.
                "model": _GEMINI_FLASH,
                "vertex_location": _GEMINI_LOCATION,
            },
        },
        {
            # Lite tier: high-volume mechanical tasks (page transcription in
            # perceive) — ~40% faster output than flash at lower cost.
            "model_name": "gemini_lite",
            "litellm_params": {
                # flash-lite does not think by default (measured: reasoning=None),
                # which is what perceive wants. No param needed — the
                # thinking={"type": "disabled"} that used to sit here was inert.
                # 3.5 Flash-Lite transcribed as much as 2.5 Flash-Lite on replayed
                # pages, ~10% faster; it fences its JSON in ```, which
                # _parse_json_response strips. 3.1 Flash-Lite truncated one page.
                "model": _GEMINI_FLASH_LITE,
                "vertex_location": _GEMINI_LOCATION,
            },
        },
        {
            "model_name": "aws",
            "litellm_params": {
                "model": "bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0",
                "thinking": {
                    "type": "disabled",
                },
            },
        },
        {
            "model_name": "anthropic",
            "litellm_params": {
                "model": "anthropic/claude-sonnet-4-20250514",
            },
        },
        {
            "model_name": "openai_thinking",
            "litellm_params": {
                "model": "openai/gpt-5-mini",
                "reasoning_effort": "high",
            },
        },
        {
            "model_name": "aws_thinking",
            "litellm_params": {
                "model": "bedrock/global.anthropic.claude-sonnet-4-5-20250929-v1:0",
                "thinking": {
                    "type": "enabled",
                    "budget_tokens": 32000,
                },
            },
        },
        {
            "model_name": "anthropic_thinking",
            "litellm_params": {
                "model": "anthropic/claude-sonnet-4-20250514",
                "thinking": {
                    "type": "enabled",
                    "budget_tokens": 32000,
                },
            },
        },
    ]
)

slow_chat_router = Router(
    model_list=[
        {
            "model_name": "openai",
            "litellm_params": {"model": "openai/gpt-5", "reasoning_effort": "minimal"},
        },
        {
            "model_name": "gemini",
            "litellm_params": {
                # Thinking is intentionally ON. NOTE: it is not configured here
                # and must not be — `thinking={"type": "disabled"}` is Anthropic
                # syntax that litellm silently drops for Vertex, so this entry
                # used to claim thinking was off while measuring 935/972
                # completion tokens as reasoning. If you ever want it off, the
                # params that actually work are reasoning_effort="disable" or
                # thinkingConfig={"thinkingBudget": 0} — but that changes agent
                # behaviour everywhere, so make it a deliberate call.
                # On 3.8 Flash the default thinks about as much as 2.5 Flash did
                # (150-300 reasoning tokens on plan/act/feedback) but each call is
                # ~1.5 s slower; reasoning_effort="low" measured 0 reasoning tokens
                # and 2.5 Flash's latency.
                "model": _GEMINI_FLASH,
                "vertex_location": _GEMINI_LOCATION,
            },
        },
        {
            # Report tier: one-shot final synthesis where quality > latency.
            "model_name": "gemini_report",
            "litellm_params": {
                "model": _GEMINI_PRO,
                "vertex_location": _GEMINI_LOCATION,
            },
        },
        {
            "model_name": "aws",
            "litellm_params": {
                "model": "bedrock/global.anthropic.claude-sonnet-4-5-20250929-v1:0",
                "thinking": {
                    "type": "disabled",
                },
            },
        },
        {
            "model_name": "anthropic",
            "litellm_params": {
                "model": "anthropic/claude-sonnet-4-20250514",
            },
        },
        {
            "model_name": "openai_thinking",
            "litellm_params": {"model": "openai/gpt-5", "reasoning_effort": "high"},
        },
        {
            "model_name": "aws_thinking",
            "litellm_params": {
                "model": "bedrock/global.anthropic.claude-sonnet-4-5-20250929-v1:0",
                "thinking": {
                    "type": "enabled",
                    "budget_tokens": 32000,
                },
            },
        },
        {
            "model_name": "anthropic_thinking",
            "litellm_params": {
                "model": "anthropic/claude-sonnet-4-20250514",
                "thinking": {
                    "type": "enabled",
                    "budget_tokens": 32000,
                },
            },
        },
    ]
)

embed_router = Router(
    model_list=[
        {
            "model_name": "openai",
            "litellm_params": {"model": "openai/text-embedding-3-small"},
        },
        {
            "model_name": "gemini",
            "litellm_params": {"model": "vertex_ai/text-embedding-004"},
        },
        {
            "model_name": "aws",
            "litellm_params": {
                "model": "bedrock/cohere.embed-english-v3",
                "input_type": "search_document",
                "truncate": "END",
            },
        },
    ]
)


load_dotenv()  # load anthropic api key from .env
anthropic_model = "claude-sonnet-4-20250514"


# ------------------------------------------------------------------
# Retry, backoff and the shared rate-limit cooldown
# ------------------------------------------------------------------
# A Vertex quota answer (429 RESOURCE_EXHAUSTED) or a 503 "overloaded" lasts tens
# of seconds. The schedule this replaced (3 tries, 1 s then 2 s) gave up inside
# ~5 s, the exception reached experiment.py and ended the persona's whole
# session. Transient errors now back off for about a minute; everything else
# (auth, bad request, content policy, JSON that will not parse) keeps the short
# schedule, because waiting does not fix those.
#
# Defaults, overridable with UXAGENT_LLM_RETRY_ATTEMPTS / UXAGENT_LLM_RETRY_MAX_WAIT:
# 6 tries wait at most 4+8+16+32+45 s between them, which outlasts a per-minute
# quota window; the 150 s cap on all waiting in one call keeps a dead endpoint
# from stalling a persona (a server hint can be long) for hours.
_DEFAULT_TRANSIENT_ATTEMPTS = 6
_DEFAULT_MAX_TOTAL_WAIT = 150.0
_BACKOFF_START = 4.0  # ceiling of the first transient wait; doubles per attempt
_BACKOFF_CAP = 45.0  # no single wait ceiling above this
_HINT_CAP = 90.0  # longest server-suggested wait honoured
_RELEASE_JITTER = 1.5  # max extra seconds after waiting out the shared cooldown

# Injection points for tests/test_llm_retry.py (no real sleeping or waiting there).
_sleep = asyncio.sleep
_monotonic = time.monotonic
_uniform = random.uniform

_PERMANENT, _TRANSIENT, _RATE_LIMITED = "permanent", "transient", "rate-limited"
_TRANSIENT_STATUS = (429, 500, 502, 503, 504)


def _types(module: str, *names: str) -> tuple:
    """Exception classes by name; any this litellm/openai version lacks is skipped."""
    try:
        mod = importlib.import_module(module)
    except Exception:
        return ()
    return tuple(c for c in (getattr(mod, n, None) for n in names) if isinstance(c, type))


# The router's own "no deployments available, try again in N seconds" is what a
# 429 turns into for the next calls while litellm holds the deployment in cooldown.
_RATE_LIMIT_TYPES = (
    _types("litellm.exceptions", "RateLimitError")
    + _types("openai", "RateLimitError")
    + _types("litellm.types.router", "RouterRateLimitError", "RouterRateLimitErrorBasic")
)
_TRANSIENT_TYPES = (
    _RATE_LIMIT_TYPES
    + _types("litellm.exceptions", "ServiceUnavailableError", "InternalServerError",
             "BadGatewayError", "Timeout", "APIConnectionError")
    + _types("openai", "InternalServerError", "APITimeoutError", "APIConnectionError")
    + (asyncio.TimeoutError,)
)
# Checked first, so a message that happens to say "quota" (Google's "requires a
# quota project" is a permission error) can never turn one of these into a wait.
_PERMANENT_TYPES = (
    _types("litellm.exceptions", "AuthenticationError", "PermissionDeniedError", "NotFoundError",
           "BadRequestError", "UnprocessableEntityError", "ContentPolicyViolationError",
           "ContextWindowExceededError")
    + _types("openai", "AuthenticationError", "PermissionDeniedError", "NotFoundError",
             "BadRequestError", "UnprocessableEntityError")
)
# litellm turns every exception it cannot map into an APIConnectionError (status
# 500) with the original text inside, so a missing or invalid Vertex credentials
# file would otherwise be retried for minutes as if the network were flaky.
_PERMANENT_TEXT = re.compile(
    r"DefaultCredentialsError|Could not automatically determine credentials"
    r"|invalid_grant|UNAUTHENTICATED|PERMISSION_DENIED|API_KEY_INVALID|API key not valid"
)
_RATE_LIMIT_TEXT = re.compile(r"resource_exhausted|too many requests|rate[ _-]?limit", re.IGNORECASE)
_TRANSIENT_TEXT = re.compile(r"quota|overloaded", re.IGNORECASE)
_UNAVAILABLE_TEXT = re.compile(r"\bUNAVAILABLE\b")  # gRPC status name, so case-sensitive

_HINT_PATTERNS = (
    re.compile(r"retry(?:ing)?\s+in\s+(\d+(?:\.\d+)?)\s*(ms|s)?", re.IGNORECASE),  # "Please retry in 12.3s"
    re.compile(r"try\s+again\s+in\s+(\d+(?:\.\d+)?)\s*(ms|s)", re.IGNORECASE),  # router cooldown
    re.compile(r"retryDelay\W{1,8}(\d+(?:\.\d+)?)\s*(ms|s)?", re.IGNORECASE),  # "retryDelay": "12s"
)


def _status_code(exc: BaseException) -> Optional[int]:
    for holder in (exc, getattr(exc, "response", None)):
        try:
            return int(getattr(holder, "status_code"))
        except (AttributeError, TypeError, ValueError):
            continue
    return None


def _classify(exc: Exception) -> str:
    if isinstance(exc, _PERMANENT_TYPES):
        return _PERMANENT
    text = str(exc)
    if _PERMANENT_TEXT.search(text):
        return _PERMANENT
    status = _status_code(exc)
    if isinstance(exc, _RATE_LIMIT_TYPES) or status == 429 or _RATE_LIMIT_TEXT.search(text):
        return _RATE_LIMITED
    if (
        isinstance(exc, _TRANSIENT_TYPES)
        or status in _TRANSIENT_STATUS
        or _TRANSIENT_TEXT.search(text)
        or _UNAVAILABLE_TEXT.search(text)
    ):
        return _TRANSIENT
    return _PERMANENT


def is_transient_error(exc: BaseException) -> bool:
    """True for rate limits, overload and transient upstream/connection errors:
    waiting can fix those. False for everything else, including cancellation."""
    return isinstance(exc, Exception) and _classify(exc) != _PERMANENT


def _server_hint(exc: Exception) -> Optional[float]:
    """Seconds the server asked us to wait: a Retry-After header, or "retry in
    12.3s" / retryDelay / "try again in 5 seconds" in the message."""
    found = []
    for holder in (getattr(exc, "response", None), exc):
        try:
            items = list(holder.headers.items())
        except Exception:
            continue
        for key, value in items:
            if str(key).lower() == "retry-after":
                try:
                    found.append(float(value))
                except (TypeError, ValueError):
                    pass  # an HTTP-date, or junk: no hint
    text = str(exc)
    for pattern in _HINT_PATTERNS:
        m = pattern.search(text)
        if m:
            seconds = float(m.group(1))
            found.append(seconds / 1000 if (m.group(2) or "").lower() == "ms" else seconds)
    found = [s for s in found if math.isfinite(s) and s >= 0]
    return max(found) if found else None


_warned_env: set = set()  # a bad value is said once, not on every call


def _env_number(name: str, default, cast, lo, hi):
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = cast(raw.strip())
        if not lo <= value <= hi:  # also False for NaN
            raise ValueError(raw)
        return value
    except ValueError:
        if (name, raw) not in _warned_env:
            _warned_env.add((name, raw))
            print(f"{name}={raw!r} is not usable; using {default}")
        return default


def _transient_attempts() -> int:
    return _env_number("UXAGENT_LLM_RETRY_ATTEMPTS", _DEFAULT_TRANSIENT_ATTEMPTS, int, 1, 20)


def _max_total_wait() -> float:
    return _env_number("UXAGENT_LLM_RETRY_MAX_WAIT", _DEFAULT_MAX_TOTAL_WAIT, float, 0.0, 1800.0)


# Process-wide "do not call before" time, a plain float of time.monotonic(). The
# personas of a run share one event loop, but a run has its own thread and loop
# and the wizard's endpoints call from request threads, so nothing here may be
# an asyncio primitive: callers just read the float and sleep in their own loop.
# The lock only guards the write (never held across an await).
_cooldown_until = 0.0
_cooldown_lock = threading.Lock()


def set_cooldown(until: float) -> float:
    """Ask every caller to hold off until `until` (time.monotonic()). Only ever
    moves the time later; returns the time now in force."""
    global _cooldown_until
    with _cooldown_lock:
        if until > _cooldown_until:
            _cooldown_until = until
        return _cooldown_until


class _WaitBudget:
    """Seconds one call has spent waiting. Shared by the nested async_chat of the
    finish_reason == 'length' retry, so recursing cannot multiply the allowance."""

    __slots__ = ("limit", "spent")

    def __init__(self, limit: float):
        self.limit = limit
        self.spent = 0.0

    @property
    def left(self) -> float:
        return self.limit - self.spent


_wait_budget: contextvars.ContextVar = contextvars.ContextVar("uxagent_llm_wait_budget", default=None)


async def _wait_out_cooldown(budget: _WaitBudget) -> None:
    """Hold until the shared cooldown has passed (re-checked after waking, since
    another call may have pushed it later), then a little random release jitter
    so the callers that waited do not all fire at the same instant. Bounded by
    what is left of this call's wait budget."""
    while True:
        remaining = _cooldown_until - _monotonic()
        if remaining <= 0 or budget.left <= 0:
            return
        pause = min(remaining + _uniform(0, _RELEASE_JITTER), budget.left)
        await _sleep(pause)
        budget.spent += pause


def _brief(exc: Exception) -> str:
    return " ".join(str(exc).split())[:160]


async def _call_with_retry(call, times: int, budget: _WaitBudget):
    transient_attempts = _transient_attempts()
    attempt = 0
    permanent_seen = False
    while True:
        attempt += 1
        await _wait_out_cooldown(budget)
        try:
            return await call()
        except Exception as exc:  # CancelledError is not an Exception: it propagates
            kind = _classify(exc)
            permanent_seen = permanent_seen or kind == _PERMANENT
            # One permanent error caps the call at `times` tries even if the next
            # answer looks transient: after an auth failure the router answers
            # "no deployments available" for a while, which is not a reason to
            # wait for minutes.
            limit = times if permanent_seen else transient_attempts
            if kind == _PERMANENT:
                wait = min(2 ** (attempt - 1), 5)  # 1 s, 2 s, ...
            else:
                ceiling = min(_BACKOFF_START * 2 ** (attempt - 1), _BACKOFF_CAP)
                wait = ceiling / 2 + _uniform(0, ceiling / 2)
                hint = _server_hint(exc)
                if hint is not None:
                    wait = max(wait, min(hint, _HINT_CAP))
            # A 429 is news for every caller, whether or not this one retries.
            cooled = kind == _RATE_LIMITED
            if cooled:
                set_cooldown(_monotonic() + wait)
            where = f"{type(exc).__name__} ({kind}) on attempt {attempt}/{limit}"
            note = ", shared cooldown set" if cooled else ""
            if attempt >= limit:
                print(f"[llm] {where}: giving up{note}: {_brief(exc)}")
                raise
            if kind != _PERMANENT and wait > budget.left:
                print(f"[llm] {where}: giving up, wait budget of {budget.limit:.0f}s spent{note}: {_brief(exc)}")
                raise
            print(f"[llm] {where}: retrying in {wait:.1f}s{note}: {_brief(exc)}")
            await _sleep(wait)
            if kind != _PERMANENT:
                budget.spent += wait


def async_retry(times=3):
    """Retry the wrapped coroutine function. `times` is the number of tries for an
    error that waiting cannot fix (1 s, 2 s apart); transient errors get more
    tries and longer waits (see _call_with_retry) and every try first waits out
    the shared rate-limit cooldown."""

    def func_wrapper(f):
        @functools.wraps(f)
        async def wrapper(*args, **kwargs):
            outer = _wait_budget.get()
            budget = outer if outer is not None else _WaitBudget(_max_total_wait())
            token = _wait_budget.set(budget) if outer is None else None
            try:
                return await _call_with_retry(lambda: f(*args, **kwargs), times, budget)
            finally:
                if token is not None:
                    _wait_budget.reset(token)

        return wrapper

    return func_wrapper


_STEP_RETRY_PAUSE = 10.0


def step_retry_pause(exc: BaseException) -> float:
    """Seconds experiment.py waits before it re-runs a failed step once. async_chat
    has just spent its own backoff on an overloaded endpoint, so an immediate
    re-run only meets the same answer. 0 for anything that is not transient."""
    if not is_transient_error(exc):
        return 0.0
    return _STEP_RETRY_PAUSE / 2 + _uniform(0, _STEP_RETRY_PAUSE / 2)


def _extract_json_string(text: str) -> str:
    import regex

    # Improved pattern to match JSON objects. Note: This is still not foolproof for deeply nested or complex JSON.
    json_pattern = r"\{(?:[^{}]*|(?R))*\}"
    matches = regex.findall(json_pattern, text, regex.DOTALL)
    if matches:
        return matches[0]
    else:
        raise Exception("No JSON object found in the response")


def _parse_json_response(content: str) -> str:
    """Return a valid JSON string from an LLM response, repairing malformed JSON
    rather than failing.

    Models — especially the cheap `lite` tier used for page transcription —
    intermittently emit invalid JSON: an unescaped quote or newline inside a
    transcribed product name breaks the string and json.loads raises
    "Expecting ',' delimiter". This call sits on the agent's perceive/plan/act
    path, so a raise here was FATAL — it killed the whole persona session
    (observed on dm.pl run 2026-07-20_16-43-51, and repeatedly before). We now
    repair the response and only raise if it is truly unsalvageable.
    """
    # Fast path: extract the JSON object and parse it strictly.
    try:
        json_str = _extract_json_string(content)
        json.loads(json_str)
        return json_str
    except Exception:
        pass
    # 3.5 Flash-Lite fences its JSON in ``` and, now and then, writes the last
    # string's closing quote escaped (\"]}). Left to json_repair, the string never
    # ends, so the fence and the brackets were stored as page text — two steps of
    # the 2026-09-27 run ended in '"]}```'. Strip the fence and un-escape that
    # one quote first; only what still fails goes to the repair below.
    import re

    body = re.sub(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$", "", content)
    for candidate in (body, re.sub(r'\\"(\s*[\]}][\]}\s]*)$', r'"\1', body)):
        try:
            json.loads(candidate)
            return candidate
        except Exception:
            continue
    # Repair path: json_repair locates and fixes the JSON (unescaped quotes,
    # trailing commas, truncation, ...). Re-validate before returning so callers
    # can always json.loads() the result.
    from json_repair import repair_json

    repaired = repair_json(content)
    json.loads(repaired)  # raises only if even repair produced nothing valid
    return repaired


@async_retry(times=3)
async def async_chat(
    messages,
    model="small",
    json_mode=False,
    log=True,
    max_tokens=64000,
    enable_thinking=None,
    **kwargs,
):
    """
    Async chat completion that returns the string LLM output.

    For model and provider information, check the head of /src/simulated_web_agent/agent/gpt.py.

    To add your own preferred LLM for chat completion, simply add the model into each of the liteLLM routers,
    and change the provider global variable to your custom provider.

    Args:
        model: "small" for lightweight version of the model
        json_mode: whether the result should be in json deserializable
        log: whether to log the output
        max_tokens: the maximum number of tokens
        enable_thinking: whether to enable thinking, if supported (supported by bedrock and anthropic, not supported by openai)

    Returns:
        A single string object outputted by the LLM.
    """

    if context.api_call_manager.get() and log:
        context.api_call_manager.get().request.append(messages)

    router = chat_router if model in ("small", "lite") else slow_chat_router
    call_kwargs: Dict[str, Any] = dict(**kwargs)
    if enable_thinking:
        router_model = provider + "_thinking"
    else:
        router_model = provider
    if model == "report":
        # Dedicated high-quality tier for final report synthesis; falls back to
        # the provider's default slow model when no _report entry is configured.
        candidate = provider + "_report"
        if any(m["model_name"] == candidate for m in router.model_list):
            router_model = candidate
    elif model == "lite":
        # Fast tier for mechanical high-volume tasks (e.g. perceive transcription);
        # falls back to the provider's default fast model when not configured.
        candidate = provider + "_lite"
        if any(m["model_name"] == candidate for m in router.model_list):
            router_model = candidate
    if provider == "gemini" and "labels" not in call_kwargs:
        labels = vertex_labels()
        if labels:
            call_kwargs["labels"] = labels
    if json_mode and provider not in ("aws",):
        call_kwargs["response_format"] = {"type": "json_object"}
    # Bounded call: LiteLLM's implicit request timeout is 600s, which let hung
    # streams stall an agent for minutes. 120s covers even the largest prompts;
    # the wait_for is a belt-and-braces cap in case the HTTP layer wedges.
    response = await asyncio.wait_for(
        router.acompletion(
            model=router_model,
            messages=messages,
            max_tokens=max_tokens,
            timeout=120,
            drop_params=True,  # do not forward unused params, such as thinking for openai
            **call_kwargs,
            tools=None,
        ),
        timeout=180,
    )
    content = response.choices[0].message.get("content", "") or ""

    finish_reason = response.choices[0].finish_reason
    if finish_reason != "stop":
        print("finish_reason:", finish_reason)
        print("content:", content)
        print("response:", response)
    if finish_reason == "length" and json_mode and max_tokens < 64000:
        # Truncated JSON is unparseable and retrying with the same cap fails
        # deterministically — double the output budget (bounded at 64k).
        return await async_chat(
            messages,
            model=model,
            json_mode=json_mode,
            log=log,
            max_tokens=min(max_tokens * 2, 64000),
            enable_thinking=enable_thinking,
        )
    # tokens_used = token_counter(model="openai/gpt-5-mini", text=content)
    # print("Output tokens:", tokens_used)

    if context.api_call_manager.get() and log:
        context.api_call_manager.get().response.append(content)

    if json_mode:
        try:
            return _parse_json_response(content)
        except Exception as e:
            print(e)
            print(content)
            raise Exception("Invalid JSON in response") from e
    return content


@async_retry(times=3)
async def _embed(texts: list[str]) -> list[list[float]]:
    response = await embed_router.aembedding(model=provider, input=texts)
    return [e["embedding"] for e in response.data]


async def embed_text(texts: list[str]) -> list[list[float]]:
    """
    Embed a list of texts using the provider configured in /src/simulated_web_agent/agent/gpt.py

    Returns:
        List of list[float] representing each of the embedded texts
    """
    try:
        return await _embed(texts)
    except Exception as e:
        print(texts)
        print(e)
        raise e


# ------------------------------------------------------------------
# Provider configuration
# ------------------------------------------------------------------

def configure(new_provider: str) -> None:
    """Set the active provider.

    Call once at pipeline startup (replaces the old ``gpt.provider = ...``
    mutation scattered across experiment.py and persona.py). `async_chat` /
    `chat` read the module-level ``provider`` to pick the router and its
    provider-specific parameters.
    """
    global provider
    provider = new_provider
