"""
LLMGateway — single seam for all LLM calls.

Replaces the scattered gpt.provider global with a configured object.
Callers receive a gateway at construction time; they never read gpt.provider.

The gateway owns:
  - Router selection (small = chat_router, large = slow_chat_router)
  - Provider-specific parameter handling (thinking budgets, response_format)
  - Embedding calls

gpt.async_chat / gpt.chat remain as thin shims that delegate to the
module-level default gateway so existing call sites keep working.
"""
from __future__ import annotations

import json
from typing import Any

from litellm.router import Router


class LLMGateway:
    def __init__(self, provider: str, chat_router: Router, slow_chat_router: Router, embed_router: Router):
        self.provider = provider
        self._chat = chat_router
        self._slow = slow_chat_router
        self._embed = embed_router

    async def async_chat(
        self,
        messages: list,
        *,
        size: str = "small",
        json_mode: bool = False,
        enable_thinking: bool | int | None = None,
        log_manager: Any = None,
        max_tokens: int = 64000,
        **kwargs,
    ) -> str:
        from . import context

        if log_manager is None:
            log_manager = context.api_call_manager.get()

        if log_manager:
            log_manager.request.append(messages)

        router = self._chat if size == "small" else self._slow
        router_model = self.provider + ("_thinking" if enable_thinking else "")

        call_kwargs: dict[str, Any] = dict(**kwargs)
        if json_mode and self.provider == "openai":
            call_kwargs["response_format"] = {"type": "json_object"}

        response = await router.acompletion(
            model=router_model,
            messages=messages,
            max_tokens=max_tokens,
            drop_params=True,
            tools=None,
            **call_kwargs,
        )
        content = response.choices[0].message.get("content", "")

        if log_manager:
            log_manager.response.append(content)

        if json_mode:
            try:
                from .gpt import _extract_json_string
                json_str = _extract_json_string(content)
                _ = json.loads(json_str)
                return json_str
            except Exception as e:
                raise Exception("Invalid JSON in response") from e

        return content

    async def embed(self, texts: list[str]) -> list[list[float]]:
        response = await self._embed.aembedding(model=self.provider, input=texts)
        return [e["embedding"] for e in response.data]
