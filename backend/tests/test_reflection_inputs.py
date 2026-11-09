"""What the objective-reflection model is given, and what it is told about it.

The persona's reflection is written from the same session as the findings, and the
report prints it as the participant's own blockers. Filip's listed, as a blocker, the
click the finding gate had already held back ("no feedback after 'Wyszukaj sklep'")
because the model was handed that finding as if it stood, and the tool's own failures
reached it as nothing at all.
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from simulated_web_agent.main.reporter import _REFLECTION_SYSTEM, generate_objective_reflection
from simulated_web_agent.main.trace_store import TraceStore
from tests.test_unperceived_outcome import WYSZUKAJ, _filip

REPLY = json.dumps({"outcome": "failed", "summary": "x", "blockers": [], "wins": [], "priorityFix": ""})
TIMEOUT = "Error executing action: Locator.click: Timeout 10000ms exceeded (locator)"


async def _payload(store) -> dict:
    chat = AsyncMock(return_value=REPLY)
    with patch("simulated_web_agent.main.reporter.async_chat", new=chat):
        await generate_objective_reflection(store)
    return json.loads(chat.call_args.args[0][1]["content"])


def _finding(evidence, **flags):
    return {"heuristic_id": 1, "severity": "poważny", "step": 3, "evidence": evidence,
            "recommendation": "Dodać wskaźnik.", **flags}


@pytest.mark.asyncio
async def test_held_findings_are_not_given_to_the_reflection_llm(tmp_path):
    store = TraceStore(tmp_path / "p")
    store.write_heuristic_findings([
        _finding("Strona nie podaje składu materiału.", harness_suspect=False),
        _finding("Po kliknięciu 'Wyszukaj sklep' brak informacji zwrotnej.", harness_suspect=True),
        _finding("Cytat z innej domeny.", off_site=True),
        _finding("Wniosek bez pokrycia w zapisie.", claim_verdict="unsupported"),
        _finding("Wniosek potwierdzony.", claim_verdict="supported")])
    payload = await _payload(store)
    assert [f["evidence"] for f in payload["heuristic_findings"]] == [
        "Strona nie podaje składu materiału.", "Wniosek potwierdzony."]


@pytest.mark.asyncio
async def test_tool_failures_and_unseen_action_are_given(tmp_path):
    store = _filip(tmp_path, errors={4: TIMEOUT})
    payload = await _payload(store)
    # observation 4 carries the error of action 3; the control is named by its id when the
    # description quotes no name
    assert payload["tool_failures"] == [{"control": "produkt 3", "what": "element nie zareagował w limicie czasu"}]
    assert payload["unseen_last_action"] == WYSZUKAJ
    assert "[step 26]" in payload["session_note"] and payload["session"]["ended_reason"] == "error"


@pytest.mark.asyncio
async def test_a_session_without_either_gives_the_model_nothing_to_misread(tmp_path):
    store = _filip(tmp_path)
    store.write_observation_step_text(27, "Lokalizator sklepów.")
    store.write_session_result({"steps_taken": 27, "ended_reason": "max_steps"})
    payload = await _payload(store)
    assert payload["tool_failures"] == []
    assert payload["unseen_last_action"] is None and payload["session_note"] is None


def test_the_system_prompt_carries_the_crash_rule():
    text = " ".join(_REFLECTION_SYSTEM.split())
    assert '"tool_failures"' in text and "nie opisuj jej jako wady strony" in text
    assert 'session.ended_reason to "error"' in text and '"unseen_last_action"' in text
    assert "nigdy nie wpisuj samej awarii sesji do blockers" in text
