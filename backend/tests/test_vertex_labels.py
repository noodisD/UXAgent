"""Every Vertex call carries the billing label that attributes it to this service."""
import asyncio
from types import SimpleNamespace

import pytest

from simulated_web_agent.agent import gpt


class _Router:
    def __init__(self):
        self.model_list = []
        self.calls = []

    async def acompletion(self, **kwargs):
        self.calls.append(kwargs)
        msg = {"content": "ok"}
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])


@pytest.fixture
def router(monkeypatch):
    r = _Router()
    monkeypatch.setattr(gpt, "chat_router", r)
    monkeypatch.setattr(gpt, "slow_chat_router", r)
    monkeypatch.delenv("UXAGENT_VERTEX_LABELS", raising=False)
    return r


def _chat(provider, **kw):
    gpt.provider = provider
    try:
        return asyncio.run(gpt.async_chat([{"role": "user", "content": "x"}], log=False, **kw))
    finally:
        gpt.provider = "openai"


def test_the_service_label_is_sent_on_vertex_calls(router):
    _chat("gemini")
    assert router.calls[0]["labels"] == {"name-of-service": "csaapps_ux-agent_general"}


def test_other_providers_are_left_alone(router):
    for p in ("openai", "aws"):
        _chat(p)
    assert all("labels" not in c for c in router.calls)


def test_a_caller_supplied_label_wins(router):
    _chat("gemini", labels={"team": "x"})
    assert router.calls[0]["labels"] == {"team": "x"}


def test_the_label_can_be_overridden_or_switched_off(router, monkeypatch):
    monkeypatch.setenv("UXAGENT_VERTEX_LABELS", '{"name-of-service": "other_service"}')
    _chat("gemini")
    assert router.calls[-1]["labels"] == {"name-of-service": "other_service"}
    monkeypatch.setenv("UXAGENT_VERTEX_LABELS", "{}")
    _chat("gemini")
    assert "labels" not in router.calls[-1]


@pytest.mark.parametrize("raw, expected", [
    ('{"Bad-Key": "v", "ok-key": "fine_1"}', {"ok-key": "fine_1"}),   # uppercase key dropped
    ('{"k": "Upper"}', {}),                                            # uppercase value dropped
    ('{"1k": "v"}', {}),                                               # key must start with a letter
    ('{"k": 3}', {}),
    ("not json", {"name-of-service": "csaapps_ux-agent_general"}),
    ("[1, 2]", {"name-of-service": "csaapps_ux-agent_general"}),
])
def test_a_label_vertex_would_reject_is_never_sent(monkeypatch, raw, expected):
    monkeypatch.setenv("UXAGENT_VERTEX_LABELS", raw)
    assert gpt.vertex_labels() == expected
