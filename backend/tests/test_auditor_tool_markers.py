"""The heuristic pass is told which steps were the tool's, not the participant's.

2026-07-09 pepco.pl run: a finding listed steps 18, 19, 20, 22 and 24 as the
participant's typing attempts when 20, 22 and 24 were backs the harness had put in
place of what the participant chose, and the sentence those backs carried ("this
method did not work, I go back") was read as the participant's own words. The
executor's failed clicks reached the auditor as plain action lines too.
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from simulated_web_agent.main import auditor
from simulated_web_agent.main.attribution import _ABSENCE_MARKERS as ATTRIBUTION_ABSENCE
from simulated_web_agent.main.claim_verifier import _ABSENCE_MARKERS, _BROKEN_MARKERS
from simulated_web_agent.main.loop_phrases import (
    FORCED_BACK_ELEMENT,
    FORCED_BACK_METHOD,
    KIND_FAILED_BREAKER,
    KIND_LOOP_BREAKER,
    KIND_REFUSED,
    REFUSED_DESCRIPTION,
    injected_action,
)
from simulated_web_agent.main.trace_store import TraceStore

TIMEOUT = "Error executing action: Locator.click: Timeout 10000ms exceeded"
TYPE_LOCATION = {"action": "type", "target": "wyszukaj_lok", "text": "Warszawa", "enter": True,
                 "description": "Typing 'Warszawa' into the store search box"}


def _store(tmp_path, actions, errors=None):
    """errors: {observation index: executor error}. One observation per action."""
    store = TraceStore(tmp_path / "p")
    store.write_action_trace([json.dumps(a) for a in actions])
    for i in range(len(actions)):
        store.append_observation({"tabs": [{"url": f"https://x.pl/{i}", "is_active": True}],
                                  "error": (errors or {}).get(i)})
        store.write_observation_step_text(i, f"Strona {i}.")
    return store


async def _prompt(store) -> str:
    chat = AsyncMock(return_value="[]")
    with patch("simulated_web_agent.main.auditor.async_chat", new=chat):
        await auditor._heuristic_pass(store)
    return chat.call_args.args[0][0]["content"]


def _actions_block(prompt: str) -> str:
    return prompt.split("ACTIONS:\n")[1].split("\n\n## Output format")[0]


@pytest.mark.asyncio
async def test_a_failed_action_line_carries_the_automation_marker(tmp_path):
    store = _store(tmp_path, [
        {"action": "click", "target": "a", "description": "Clicking 'Menu'"},
        {"action": "click", "target": "b", "description": "Clicking 'Szukaj'"},
        {"action": "click", "target": "c", "description": "Clicking 'Koszyk'"}], errors={2: TIMEOUT})
    lines = _actions_block(await _prompt(store)).splitlines()
    assert lines[0] == "- [step 0] Clicking 'Menu'"
    # observation 2 carries the error of action 1
    assert lines[1].startswith("- [step 1] Clicking 'Szukaj' [AUTOMATION ERROR")
    assert "says nothing about the site" in lines[1]
    assert lines[2] == "- [step 2] Clicking 'Koszyk'"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind, sentence", [(KIND_LOOP_BREAKER, FORCED_BACK_METHOD),
                                            (KIND_FAILED_BREAKER, FORCED_BACK_ELEMENT)])
async def test_a_forced_back_is_marked_as_the_harness_s(tmp_path, kind, sentence):
    store = _store(tmp_path, [TYPE_LOCATION, injected_action(kind, TYPE_LOCATION),
                              {"action": "click", "target": "c", "description": "Clicking 'Szukaj'"}])
    block = _actions_block(await _prompt(store))
    line = block.splitlines()[1]
    assert line.startswith("- [step 1] [HARNESS-INJECTED BACK")
    assert "did not choose this back" in line and "not an attempt" in line
    assert "Typing 'Warszawa' into the store search box" in line  # what it replaced
    # the sentence it used to carry is what read as the participant's own words
    assert sentence not in block and "nie działał" not in block
    assert block.splitlines()[0] == "- [step 0] Typing 'Warszawa' into the store search box"


@pytest.mark.asyncio
async def test_a_refusal_is_a_policy_not_a_tool_failure(tmp_path):
    store = _store(tmp_path, [TYPE_LOCATION, injected_action(KIND_REFUSED, TYPE_LOCATION)])
    line = _actions_block(await _prompt(store)).splitlines()[1]
    assert "[HARNESS-INJECTED BACK" in line
    assert "a policy of the study, not a tool failure" in line
    assert "repeated tool failures" not in line and REFUSED_DESCRIPTION not in line


@pytest.mark.asyncio
async def test_a_back_written_before_the_marker_is_read_by_its_sentence(tmp_path):
    old = {"action": "back", "description": FORCED_BACK_METHOD}
    store = _store(tmp_path, [TYPE_LOCATION, old])
    line = _actions_block(await _prompt(store)).splitlines()[1]
    assert line.startswith("- [step 1] [HARNESS-INJECTED BACK")
    assert FORCED_BACK_METHOD not in line


@pytest.mark.asyncio
async def test_a_clean_session_prompt_is_unchanged(tmp_path):
    store = _store(tmp_path, [
        {"action": "click", "target": "a", "description": "Clicking 'Menu'"},
        {"action": "back", "description": "Going back to the list."},
        {"action": "scroll", "direction": "down", "description": "Scrolling down."}])
    assert _actions_block(await _prompt(store)).splitlines() == [
        "- [step 0] Clicking 'Menu'", "- [step 1] Going back to the list.", "- [step 2] Scrolling down."]


@pytest.mark.asyncio
async def test_a_participants_own_back_is_not_marked(tmp_path):
    """Only what the harness injected: a persona who says 'wybieram inną drogę' is its own."""
    own = {"action": "back", "description": "Wracam, bo wybieram inną drogę do celu."}
    store = _store(tmp_path, [TYPE_LOCATION, own])
    assert "HARNESS-INJECTED" not in _actions_block(await _prompt(store))


@pytest.mark.asyncio
async def test_the_prompt_explains_the_markers(tmp_path):
    prompt = await _prompt(_store(tmp_path, [TYPE_LOCATION]))
    assert 'marked "AUTOMATION ERROR"' in prompt and 'marked "HARNESS-INJECTED BACK"' in prompt


def test_the_markers_stay_clear_of_the_words_the_gates_read_as_claims(tmp_path):
    """A model that copies a marker into a finding must not hand the deterministic overrides
    a defect to match: they read these phrases in a finding's own text."""
    vocabulary = tuple(ATTRIBUTION_ABSENCE) + tuple(_BROKEN_MARKERS) + tuple(_ABSENCE_MARKERS)
    lines = [auditor._TOOL_FAILED_MARKER]
    for kind in (KIND_LOOP_BREAKER, KIND_FAILED_BREAKER, KIND_REFUSED):
        lines.append(auditor._action_line(injected_action(kind, TYPE_LOCATION), False))
    for line in lines:
        assert not [m for m in vocabulary if m in line.lower()], line
