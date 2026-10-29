"""The persona is told that a tool error is not a fact about the site.

feedback.txt said that a set `last_action_error` means the action FAILED and the
persona should acknowledge "the failure and its cause". For "Error executing
action: Locator.click: Timeout ... intercepts pointer events" the only cause the
persona could name was the page: the thought became "the button is broken", then
a reflection, then a finding. The executor writes that field only when its own
tool could not do the job (attribution.py reads it the same way); a real problem
with a site arrives as page content.
"""
import re

import pytest

from simulated_web_agent.agent.prompt_registry import registry as _prompts
from simulated_web_agent.main import attribution

TOOL_ERRORS = ("Error executing action", "Invalid JSON action", "Missing required parameter")


@pytest.mark.parametrize("name", ["feedback", "action"])
def test_a_tool_error_is_said_to_tell_nothing_about_the_website(name):
    prompt = _prompts.get(name)
    for prefix in TOOL_ERRORS:
        assert f'"{prefix}"' in prompt, (name, prefix)
    assert "That says nothing about the website" in prompt
    assert "browser tool could not carry the action out" in prompt


def test_feedback_does_not_let_the_persona_call_the_site_broken_for_a_tool_error():
    prompt = _prompts.get("feedback")
    assert "Do not write that the site, the page, the search or the feature is broken" in prompt
    assert "I could not operate it with my tool" in prompt


def test_the_next_action_is_not_described_as_a_site_defect():
    prompt = _prompts.get("action")
    assert "Do not write in `description` that the site" in prompt


def test_a_reflection_is_not_drawn_from_a_tool_error():
    prompt = _prompts.get("reflect")
    assert "failed with a tool error" in prompt
    assert "is not an insight about the website" in prompt


@pytest.mark.parametrize("name", ["feedback", "action"])
def test_an_advisory_is_still_not_a_failure(name):
    """The hint about the persona's own path stays apart from the tool error: it
    says the action worked, the other that the tool could not do it."""
    prompt = _prompts.get(name)
    assert 'starts with "Advisory"' in prompt
    assert "did NOT fail" in prompt or "your previous action worked" in prompt
    assert prompt.index('starts with "Advisory"') != prompt.index('starts with "Error executing action"')


@pytest.mark.parametrize("name", ["feedback", "action"])
def test_the_rule_covers_the_errors_the_executor_writes_and_no_others(name):
    """The prefixes attribution treats as proof the tool failed. Not the repeat
    advisory or a refusal: the first can be a site that did not respond, the
    second is the audit's own rule."""
    prompt = _prompts.get(name)
    for marker in attribution._EXECUTOR_ERROR_MARKERS:
        words = re.sub(r"[^a-z ]", "", marker).split()[:3]
        assert " ".join(words) in prompt.lower(), marker
    rule = prompt[prompt.index('starts with "Error executing action"'):]
    assert "Repeated identical" not in rule
    assert "Navigation blocked" not in rule and "Input blocked" not in rule
