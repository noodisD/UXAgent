"""The plan fits the intent before it fits the persona.

planning.txt said "Always adhere to the preferences and characteristics of the
persona", unconditionally. Asked for room decorations, a persona whose taste ran
to the practical opened storage boxes and bedding that surfaced under a keyword
taken from the intent: taste had replaced the category instead of choosing
inside it. The intent's category binds; taste only chooses among what fits.
"""
import json
import re

import pytest

from simulated_web_agent.agent import agent as agent_mod
from simulated_web_agent.agent.agent import Agent
from simulated_web_agent.agent.prompt_registry import registry as _prompts


@pytest.fixture
def planning():
    return _prompts.get("planning")


def test_taste_is_no_longer_an_unconditional_rule(planning):
    assert "Always adhere to the preferences" not in planning
    assert "when choosing among options that satisfy the intent" in planning
    assert "taste never widens or replaces the category the intent asks for" in planning


def test_the_plan_names_the_category_before_a_product_is_opened(planning):
    assert "**Intent fit.**" in planning
    paragraph = planning[planning.index("**Intent fit.**"):].split("\n\n")[0]
    assert "name in the rationale the category the intent asks for" in paragraph
    assert "plainly belongs to it" in paragraph
    assert "keyword taken from the intent" in paragraph
    assert "change the search" in paragraph and "instead of substituting" in paragraph


def test_the_output_format_has_an_intent_check_placeholder_not_a_value(planning):
    fmt = planning[planning.index("Format your output as follows:"):].split("}")[0]
    assert re.search(r'"intent_check":\s*"<[^>]+>"', fmt), "a placeholder, not a value the model copies"
    # asked for before the step it is meant to constrain
    assert fmt.index('"intent_check"') < fmt.index('"next_step"')


def test_the_wording_is_generic(planning):
    paragraph = planning[planning.index("**Intent fit.**"):].split("\n\n")[0]
    assert "pepco" not in planning.lower()
    assert "pepco" not in paragraph.lower()


async def test_the_planner_ignores_the_extra_field(monkeypatch):
    """intent_check is there to make the model look before it chooses; the plan
    is read from the three keys it always had."""
    agent = Agent("persona", "intent")
    answer = {"rationale": "Szukam dekoracji do pokoju.", "intent_check": "Dekoracje; wazon do nich należy.",
              "plan": "1. (next) Otwórz wazon", "next_step": "Otwórz wazon"}

    async def fake_chat(messages, **kwargs):
        return json.dumps(answer)

    class _NoApiTrace:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(agent_mod, "async_chat", fake_chat)
    monkeypatch.setattr(agent_mod, "LogApiCall", _NoApiTrace)

    await agent.plan()

    assert agent.current_plan.next_step == "Otwórz wazon"
    assert agent.current_plan.content == "1. (next) Otwórz wazon"
