import asyncio
import json
import os
import re
import time
from typing import Any, Dict

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


def async_retry(times=10):
    def func_wrapper(f):
        async def wrapper(*args, **kwargs):
            wait = 1
            max_wait = 5
            last_exc = None
            for _ in range(times):
                # noinspection PyBroadException
                try:
                    return await f(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    print("got exc", exc)
                    await asyncio.sleep(wait)
                    wait = min(wait * 2, max_wait)
                    pass
            if last_exc:
                raise last_exc

        return wrapper

    return func_wrapper


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


async def embed_text(texts: list[str]) -> list[list[float]]:
    """
    Embed a list of texts using the provider configured in /src/simulated_web_agent/agent/gpt.py

    Returns:
        List of list[float] representing each of the embedded texts
    """
    try:
        response = await embed_router.aembedding(model=provider, input=texts)
        return [e["embedding"] for e in response.data]
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
