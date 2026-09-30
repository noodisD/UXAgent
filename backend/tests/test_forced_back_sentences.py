"""Both sentences the harness writes when it forces the agent out of a loop are
recognised as the harness's own — and a persona's ordinary words are not.

The markers were narrowed to one sentence ("nie działał mimo kilku prób") to stop
"wybieram inną wersję wyposażenia" marking a step as a tool failure. There are
two sentences (experiment.py: the loop-breaker says "Ta metoda nie działała…",
the circuit breaker "Ten element nie działał…"), and the test fed only one, so
the narrowing silently stopped recognising the other: its step went unheld and
iso_metrics counted the harness's own "back" as the persona's backtrack."""
import json
from pathlib import Path

import pytest

from simulated_web_agent.main import iso_metrics
from simulated_web_agent.main.attribution import harness_suspect_steps
from simulated_web_agent.main.loop_phrases import FORCED_BACK_ELEMENT, FORCED_BACK_METHOD
from simulated_web_agent.main.trace_store import TraceStore

EXPERIMENT = (Path(__file__).resolve().parents[1] / "src" / "simulated_web_agent"
              / "main" / "experiment.py")

BOTH = [FORCED_BACK_METHOD, FORCED_BACK_ELEMENT]


def _store(tmp_path, actions):
    store = TraceStore(tmp_path / "s")
    store.write_action_trace([json.dumps(a) for a in actions])
    for _ in actions:
        store.append_observation({"tabs": [{"url": "https://x.pl/", "is_active": True}], "error": None})
    return store


@pytest.mark.parametrize("sentence", BOTH)
def test_each_harness_sentence_marks_its_step_and_the_one_before(tmp_path, sentence):
    actions = [{"action": "click", "target": "a", "description": "otwieram"},
               {"action": "scroll", "target": None, "description": "przewijam"},
               {"action": "back", "target": None, "description": sentence}]
    suspect = harness_suspect_steps(_store(tmp_path, actions))
    assert {1, 2} <= suspect and 0 not in suspect


@pytest.mark.parametrize("sentence", BOTH)
def test_the_injected_back_is_not_the_personas_own_backtrack(sentence):
    assert iso_metrics._injected_back({"action": "back", "description": sentence})


@pytest.mark.parametrize("text", [
    "Wersja Smart jest za droga, wybieram inną wersję wyposażenia.",
    "Ten filtr nie działał tak, jak oczekiwałem, więc szukam innej drogi do ofert.",
    "Wracam i wybieram inną drogę do konfiguratora.",
])
def test_a_persona_using_the_same_words_is_not_a_tool_failure(tmp_path, text):
    actions = [{"action": "click", "target": "a", "description": "otwieram"},
               {"action": "back", "target": None, "description": text}]
    assert harness_suspect_steps(_store(tmp_path, actions)) == set()
    assert not iso_metrics._injected_back({"action": "back", "description": text})


def test_experiment_writes_the_sentences_the_markers_read():
    """Drift guard: experiment.py must take the sentences from loop_phrases, not
    carry a copy that a later edit changes on one side only."""
    src = EXPERIMENT.read_text(encoding="utf-8")
    assert "FORCED_BACK_METHOD" in src and "FORCED_BACK_ELEMENT" in src
    assert "mimo kilku prób" not in src
