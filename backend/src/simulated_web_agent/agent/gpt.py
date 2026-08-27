import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, cast

# anthropic import for claude computer use
import anthropic
import yaml
from anthropic.types.beta import (
    BetaContentBlockParam,
    BetaTextBlock,
    BetaTextBlockParam,
    BetaToolUseBlockParam,
)

# client and model for claude compute use tool
from dotenv import load_dotenv

# from litellm import drop_params, token_counter
from litellm.router import Router

from . import context
from .gateway import LLMGateway
from .prompt_registry import registry as _prompt_registry

provider = "openai"  # kept for backward-compat; use configure() to change

prompt_dir = Path(__file__).parent.absolute() / "shop_prompts"
audit_prompt_dir = Path(__file__).parent.parent.absolute() / "audit_prompts"

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
                "model": "vertex_ai/gemini-2.5-flash",
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
                "model": "vertex_ai/gemini-2.5-flash-lite",
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
                "model": "claude-sonnet-4-20250514",
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
                "model": "claude-sonnet-4-20250514",
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
                "model": "vertex_ai/gemini-2.5-flash",
            },
        },
        {
            # Report tier: one-shot final synthesis where quality > latency.
            "model_name": "gemini_report",
            "litellm_params": {
                "model": "vertex_ai/gemini-2.5-pro",
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
                "model": "claude-sonnet-4-20250514",
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
                "model": "claude-sonnet-4-20250514",
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
anthropic_client = anthropic.Anthropic()
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


def retry(times=10):
    def func_wrapper(f):
        def wrapper(*args, **kwargs):
            wait = 1
            max_wait = 5
            last_exc = None
            for _ in range(times):
                # noinspection PyBroadException
                try:
                    return f(*args, **kwargs)
                except Exception as exc:
                    print("got exc", exc)
                    last_exc = exc
                    # await asyncio.sleep(wait)
                    time.sleep(wait)
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
):  # noqa: D401 — shim; implementation lives in LLMGateway
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
    call_kwargs.pop("_length_retry", None)
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


@retry()
def chat(
    messages, model="small", enable_thinking=None, json_mode=False, **kwargs
) -> str:
    """
    Returns LLM text completion given list of formatted messages.

    Args:
        model: set to "small" to use the lightweight version of the chat model.
        messages: List of previous messages
        enable_thinking: Whether to enable thinking or not. Pass in an integer for custom thinking budget.
        json_mode: Whether to enable JSON mode or not.

    Returns:
        String output of the LLM model
    """

    router = chat_router if model == "small" else slow_chat_router
    call_kwargs: Dict[str, Any] = dict(**kwargs)
    call_kwargs.pop("_length_retry", None)
    if enable_thinking:
        call_kwargs["thinking"] = {
            "type": "enabled",
            "budget_tokens": enable_thinking
            if isinstance(enable_thinking, int)
            else 1024,
        }
    if json_mode and provider not in ("aws",):
        call_kwargs["response_format"] = {"type": "json_object"}

    try:
        response = router.completion(
            model=provider,
            messages=messages,
            drop_params=True,  # do not forward unused params, such as thinking for openai
            **call_kwargs,
        )
        return response.choices[0].message["content"]
    except Exception as e:
        print(messages)
        print(e)
        raise e


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


def chat_anthropic_computer_use(
    messages,
    system: BetaTextBlockParam,
    model=anthropic_model,
    screen_width: int = 1024,
    screen_height: int = 768,
) -> (list[BetaToolUseBlockParam], list[Dict, Any]):
    """
    Given a system block and JSON messages, return the tool use block generated by the computer use tool
    """
    response = anthropic_client.beta.messages.create(
        model=model,
        max_tokens=1024,
        tools=[
            {
                "type": "computer_20250124",
                "name": "computer",
                "display_width_px": screen_width,
                "display_height_px": screen_height,
                "display_number": 1,
            },
            {
                "name": "web_browser",
                "description": "High-level browser controls",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "switch_tab",
                                "forward",
                                "back",
                                "new_tab",
                                "goto_url",
                                "close_tab",
                                "terminate",
                            ],
                        },
                        "tab_index": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "Zero-based index for switch_tab and close_tab",
                        },
                        "url": {
                            "type": "string",
                            "description": "URL input, only required for goto_url and new_tab",
                        },
                    },
                    "required": ["action"],
                },
            },
        ],
        system=[system],
        messages=messages,
        betas=["computer-use-2025-01-24"],
    )

    return response


def load_prompt(prompt_name: str) -> str:
    return _prompt_registry.get(prompt_name)


def load_audit_prompt(prompt_name: str) -> str:
    return _prompt_registry.get(prompt_name)


# ------------------------------------------------------------------
# Gateway management
# ------------------------------------------------------------------

_gateway: LLMGateway | None = None


def configure(new_provider: str) -> None:
    """Set the active provider and wire up the module-level gateway.

    Call once at pipeline startup (replaces the old ``gpt.provider = ...``
    mutation scattered across experiment.py and persona.py).
    """
    global provider, _gateway
    provider = new_provider
    _gateway = LLMGateway(new_provider, chat_router, slow_chat_router, embed_router)


def _get_gateway() -> LLMGateway:
    if _gateway is None:
        configure(provider)
    return _gateway  # type: ignore[return-value]
