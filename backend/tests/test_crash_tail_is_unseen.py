"""The passes written after a crashed session are told its last action was never seen.

2026-07-09 pepco.pl run, Filip: the session was cut by an API rate limit right after
he clicked 'Wyszukaj sklep'. That click is the last thing in his memory and the last
line of his action trace, with nothing after it, and every model that read it
concluded it had failed: "the failure of the critical 'Wyszukaj sklep' function at
the end" (survey), "no feedback after clicking" (reflection, finding).
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from simulated_web_agent.agent.prompt_registry import registry
from simulated_web_agent.main import attribution, auditor
from simulated_web_agent.main.loop_phrases import KIND_LOOP_BREAKER, KIND_REFUSED, injected_action
from simulated_web_agent.main.recurring import route_summary
from simulated_web_agent.main.survey import run_survey
from simulated_web_agent.main.trace_store import TraceStore
from tests.test_unperceived_outcome import WYSZUKAJ, _filip

NOTE_START = "Sesja urwała się awarią narzędzia tuż po akcji [step 26]"


# --- the note ----------------------------------------------------------------------------

def test_session_end_note_only_for_error_sessions(tmp_path):
    cut = _filip(tmp_path)
    note = attribution.session_end_note(cut)
    assert note.startswith(NOTE_START) and "nigdy nie zobaczył" in note and "nie wnioskuj" in note

    finished = _filip(tmp_path, name="finished")
    finished.write_session_result({"steps_taken": 27, "max_steps": 30, "terminated": True,
                                   "ended_reason": "terminated"})
    assert attribution.session_end_note(finished) is None

    maxed = _filip(tmp_path, name="maxed")
    maxed.write_session_result({"steps_taken": 27, "max_steps": 27, "terminated": False,
                                "ended_reason": "max_steps"})
    assert attribution.session_end_note(maxed) is None


def test_an_error_session_that_read_its_last_page_has_no_note(tmp_path):
    """The crash was somewhere else: the last action's page was perceived."""
    store = _filip(tmp_path)
    store.write_observation_step_text(27, "Lokalizator sklepów. Wpisz miasto.")
    assert attribution.session_end_note(store) is None


def test_a_session_that_crashed_before_its_first_action_has_no_note(tmp_path):
    store = TraceStore(tmp_path / "early")
    store.append_observation({"tabs": [], "error": None})
    store.write_session_result({"steps_taken": 0, "ended_reason": "error"})
    assert attribution.unperceived_steps(store) == [0]
    assert attribution.session_end_note(store) is None


def test_the_unseen_action_is_named(tmp_path):
    assert attribution.unseen_last_action(_filip(tmp_path)) == WYSZUKAJ
    clean = TraceStore(tmp_path / "clean")
    clean.write_action_trace([json.dumps({"action": "click", "description": "x"})])
    clean.append_observation({"tabs": [], "error": None})
    clean.write_observation_step_text(0, "Strona")
    assert attribution.unseen_last_action(clean) is None


# --- the heuristic pass --------------------------------------------------------------------

async def _heuristic_prompt(store) -> str:
    chat = AsyncMock(return_value="[]")
    with patch("simulated_web_agent.main.auditor.async_chat", new=chat):
        await auditor._heuristic_pass(store)
    return chat.call_args.args[0][0]["content"]


@pytest.mark.asyncio
async def test_heuristic_prompt_omits_the_unperceived_step_and_carries_the_note(tmp_path):
    prompt = await _heuristic_prompt(_filip(tmp_path))
    observations = prompt.split("OBSERVATIONS:\n")[1].split("\n\nREFLECTIONS:")[0]
    actions = prompt.split("ACTIONS:\n")[1].split("\n\n## Output format")[0]
    assert "- [step 26] https://pepco.pl/p/26 :: Strona produktu 26." in observations
    assert "[step 27]" not in observations  # nobody read it; an empty line invites "the page was empty"
    assert actions.splitlines()[-2] == f"- [step 26] {WYSZUKAJ}"
    assert actions.splitlines()[-1].startswith(f"- {NOTE_START}")


@pytest.mark.asyncio
async def test_a_session_that_ended_normally_gets_no_note_and_keeps_every_step(tmp_path):
    store = _filip(tmp_path)
    store.write_observation_step_text(27, "Lokalizator sklepów.")
    store.write_session_result({"steps_taken": 27, "ended_reason": "max_steps"})
    prompt = await _heuristic_prompt(store)
    assert "Sesja urwała się" not in prompt.split("ACTIONS:\n")[1]
    assert "[step 27] https://pepco.pl/p/27 :: Lokalizator sklepów." in prompt


# --- the survey ----------------------------------------------------------------------------

async def _survey_request(store) -> list:
    chat = AsyncMock(return_value=json.dumps({"answers": []}))
    store.write_memory_trace([{"kind": "action", "content": WYSZUKAJ, "timestamp": 27, "importance": 5}])
    with patch("simulated_web_agent.main.survey.async_chat", new=chat):
        await run_survey(store, {"questionnaire_id": "q", "questions": []})
    return chat.call_args.args[0]


@pytest.mark.asyncio
async def test_survey_prompt_carries_the_note(tmp_path):
    user = (await _survey_request(_filip(tmp_path)))[1]["content"]
    assert "SESSION RESULT" in user and '"ended_reason": "error"' in user
    assert f"SESSION END NOTE: {NOTE_START}" in user
    assert user.index("SESSION END NOTE") < user.index(WYSZUKAJ)  # before the memories


@pytest.mark.asyncio
async def test_survey_prompt_has_no_note_after_a_normal_end(tmp_path):
    store = _filip(tmp_path)
    store.write_session_result({"steps_taken": 27, "ended_reason": "max_steps"})
    assert "SESSION END NOTE" not in (await _survey_request(store))[1]["content"]


def test_the_survey_prompt_grounds_a_crash():
    prompt = " ".join(registry.get("survey").split())
    assert 'ended_reason is "error"' in prompt
    assert "I do not say it failed and I do not say it worked" in prompt


# --- the route the aggregate narrator reads ---------------------------------------------------

def test_route_summary_marks_the_unseen_last_action(tmp_path):
    route = route_summary(_filip(tmp_path))
    assert route[-1].endswith("(wynik nieznany)") and "Wyszukaj sklep" in route[-1]
    assert not any("wynik nieznany" in step for step in route[:-1])


def test_route_summary_marks_a_forced_back(tmp_path):
    chosen = {"action": "type", "target": "q", "text": "x", "description": "Typing 'x' into search"}
    store = TraceStore(tmp_path / "forced")
    trace = [chosen, injected_action(KIND_LOOP_BREAKER, chosen),
             injected_action(KIND_REFUSED, chosen),
             {"action": "back", "description": "Ta metoda nie działała mimo kilku prób — wracam, by "
                                               "spróbować innej drogi."}]  # a trace from before the marker
    store.write_action_trace([json.dumps(a) for a in trace])
    for i in range(len(trace)):
        store.append_observation({"tabs": [], "error": None})
        store.write_observation_step_text(i, "Strona")
    route = route_summary(store)
    assert route[0] == "0: Typing 'x' into search"
    assert route[1] == "1: [cofnięcie wymuszone przez narzędzie]"
    assert route[2] == "2: [cofnięcie wynikające z reguł badania]"
    assert route[3] == "3: [cofnięcie wymuszone przez narzędzie]"
    assert "nie działała" not in " ".join(route)


def test_route_summary_marks_every_tool_failure_not_only_clicks(tmp_path):
    store = TraceStore(tmp_path / "failed")
    store.write_action_trace([json.dumps({"action": "type", "target": "q", "description": "Typing 'x'"}),
                              json.dumps({"action": "click", "target": "b", "description": "Clicking 'B'"})])
    store.append_observation({"tabs": [], "error": None})
    store.append_observation({"tabs": [], "error": "Invalid JSON action format: Expecting value"})
    store.append_observation({"tabs": [], "error": None})
    for i in range(3):
        store.write_observation_step_text(i, "Strona")
    assert route_summary(store) == ["0: Typing 'x' (nieudane)", "1: Clicking 'B'"]
