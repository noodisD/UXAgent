"""A step the harness replaced with a 'back' says so, and says what it replaced.

Three places in experiment.py swap the persona's chosen action for a back: the
hard loop-breaker, the failed-action breaker and the audit's refusal. Until now
they could be told apart only by the wording of the sentence, only the
loop-breaker held the repeat bookkeeping, and the persona's memory still held
the step it had chosen, as if it had run: the next feedback narrated it, and a
real run's findings listed steps 18, 19, 20, 22 and 24 as typing attempts when
20, 22 and 24 were backs.
"""
import ast
import inspect
import json
import types

import pytest

from simulated_web_agent.agent import agent as agent_mod
from simulated_web_agent.agent.agent import Agent
from simulated_web_agent.agent.memory import Action, Plan, Thought
from simulated_web_agent.executor.env import WebAgentEnv
from simulated_web_agent.main import experiment
from simulated_web_agent.main.loop_guards import action_key, failure_key
from simulated_web_agent.main.loop_phrases import (
    FORCED_BACK_ELEMENT,
    FORCED_BACK_METHOD,
    KIND_FAILED_BREAKER,
    KIND_LOOP_BREAKER,
    KIND_REFUSED,
    REFUSED_DESCRIPTION,
    injected_action,
    injected_kind,
    intended_action,
    is_injected,
    is_tool_trouble_injection,
)

KINDS = [
    (KIND_LOOP_BREAKER, FORCED_BACK_METHOD),
    (KIND_FAILED_BREAKER, FORCED_BACK_ELEMENT),
    (KIND_REFUSED, REFUSED_DESCRIPTION),
]

CHOSEN = {"action": "type", "target": "wyszukaj_lok", "text": "Warszawa", "enter": True,
          "description": "Wpisuję Warszawę w wyszukiwarkę sklepów"}


def _forms(entry):
    """One trace entry as a dict, as a JSON string and as a one-element list."""
    return [entry, json.dumps(entry), [entry]]


# --- the injected action -----------------------------------------------------------

@pytest.mark.parametrize("kind, sentence", KINDS)
def test_an_injected_action_keeps_the_sentence_and_what_it_replaced(kind, sentence):
    for chosen in _forms(CHOSEN)[:2]:
        a = injected_action(kind, chosen)
        assert a["action"] == "back"
        assert a["description"] == sentence
        assert a["harness"] == kind
        assert a["intended"] == CHOSEN


def test_an_injection_replacing_an_injection_keeps_the_personas_choice():
    """The failed-action breaker looks at the action the loop-breaker already
    replaced: what is kept is what the persona chose, never a harness back."""
    first = injected_action(KIND_LOOP_BREAKER, CHOSEN)
    second = injected_action(KIND_FAILED_BREAKER, json.dumps(first))
    assert second["harness"] == KIND_FAILED_BREAKER
    assert second["intended"] == CHOSEN


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        injected_action("circuit_breaker", CHOSEN)


@pytest.mark.parametrize("kind, sentence", KINDS)
def test_the_readers_accept_a_dict_a_string_or_a_wrapped_entry(kind, sentence):
    for entry in _forms(injected_action(kind, CHOSEN)):
        assert injected_kind(entry) == kind
        assert is_injected(entry)
        assert intended_action(entry) == CHOSEN


def test_only_a_tool_failure_is_tool_trouble_a_refusal_is_the_audits_own_rule():
    verdicts = {kind: is_tool_trouble_injection(injected_action(kind, CHOSEN)) for kind, _ in KINDS}
    assert verdicts == {KIND_LOOP_BREAKER: True, KIND_FAILED_BREAKER: True, KIND_REFUSED: False}


@pytest.mark.parametrize("kind, sentence", KINDS)
def test_a_trace_written_before_the_marker_is_read_by_its_sentence(kind, sentence):
    legacy = {"action": "back", "target": None, "description": sentence}
    for entry in _forms(legacy):
        assert injected_kind(entry) == kind
        assert is_injected(entry)
        assert intended_action(entry) is None
    assert is_tool_trouble_injection(legacy) is (kind != KIND_REFUSED)


@pytest.mark.parametrize("entry", [
    CHOSEN,
    {"action": "back", "description": "Wracam do listy produktów."},
    {"action": "back", "description": "Ten filtr nie działał tak, jak oczekiwałem, więc szukam innej drogi."},
    {"action": "back", "description": "Ta ścieżka jest zamknięta regułami badania — wracam."},
    {"action": "click", "harness": "unknown_kind", "description": "Klikam"},
    "not json at all",
    {},
    None,
])
def test_an_ordinary_action_is_not_an_injection(entry):
    assert injected_kind(entry) is None
    assert not is_injected(entry)
    assert not is_tool_trouble_injection(entry)
    assert intended_action(entry) is None


def test_the_marker_decides_not_the_wording():
    """An entry with the marker is that kind whatever its description says."""
    a = injected_action(KIND_REFUSED, CHOSEN)
    a["description"] = "Wracam."
    assert injected_kind(a) == KIND_REFUSED


# --- what reads the action must not see the record -----------------------------------

@pytest.mark.parametrize("kind, sentence", KINDS)
def test_action_key_and_failure_key_ignore_the_marker_fields(kind, sentence):
    plain = '{"action": "back"}'
    injected = json.dumps(injected_action(kind, CHOSEN))
    assert action_key(injected) == action_key(plain)
    assert failure_key(injected) == failure_key(plain)


def test_the_replaced_action_does_not_make_two_backs_look_different():
    """Both are the same back to the repeat guard and the breaker, whatever each
    replaced; keyed on `intended`, a back that failed could never be counted."""
    a = json.dumps(injected_action(KIND_FAILED_BREAKER, CHOSEN))
    b = json.dumps(injected_action(KIND_FAILED_BREAKER, {"action": "click", "target": "x"}))
    assert action_key(a) == action_key(b)


async def test_the_executor_ignores_the_marker_fields():
    """env.step reads the fields it needs by name; a back with a record of what it
    replaced is still just a back."""
    calls = []

    async def back():
        calls.append("back")

    async def observation():
        return {"tabs": [], "error": None}

    env = WebAgentEnv.__new__(WebAgentEnv)
    env.config = types.SimpleNamespace(browser=types.SimpleNamespace(sleep_after_action=0))
    env.logger = types.SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None)
    env._allowed_domain = ""
    env.page = object()
    env.back = back
    env.observation = observation

    obs = await env.step(json.dumps(injected_action(KIND_LOOP_BREAKER, CHOSEN)))
    assert calls == ["back"]
    assert obs["error"] is None


# --- the persona's memory says what ran ----------------------------------------------

async def _agent_after_choosing(chosen=CHOSEN):
    """An agent whose act() just chose `chosen`: Thought, Plan, Action."""
    agent = Agent("persona", "intent")
    await agent.memory.add_memory_piece(Thought("Rationale", agent.memory))
    agent.current_plan = Plan("1. (next) Wpisz Warszawę", agent.memory, "Wpisz Warszawę")
    await agent.memory.add_memory_piece(agent.current_plan)
    await agent.memory.add_memory_piece(
        Action(chosen["description"], agent.memory, json.dumps(chosen)))
    return agent


async def test_replace_last_action_rewrites_in_place_and_keeps_length():
    agent = await _agent_after_choosing()
    pieces = list(agent.memory.memories)
    action = injected_action(KIND_LOOP_BREAKER, CHOSEN)

    agent.replace_last_action(action)

    assert len(agent.memory.memories) == len(pieces)
    assert all(m is p for m, p in zip(agent.memory.memories, pieces))
    last = agent.memory.memories[-1]
    assert last.content == FORCED_BACK_METHOD
    assert json.loads(last.raw_action)["action"] == "back"


async def test_replace_last_action_rewrites_only_the_newest_action():
    earlier = {"action": "click", "target": "a", "description": "Klikam a"}
    agent = await _agent_after_choosing(earlier)
    await agent.memory.add_memory_piece(Action(CHOSEN["description"], agent.memory, json.dumps(CHOSEN)))
    await agent.memory.add_memory_piece(Thought("Po akcji", agent.memory))

    agent.replace_last_action(injected_action(KIND_FAILED_BREAKER, CHOSEN))

    actions = [m for m in agent.memory.memories if isinstance(m, Action)]
    assert actions[0].content == "Klikam a" and json.loads(actions[0].raw_action) == earlier
    assert actions[1].content == FORCED_BACK_ELEMENT
    assert agent.memory.memories[-1].content == "Po akcji"


def test_replace_last_action_without_an_action_does_nothing():
    agent = Agent("persona", "intent")
    agent.replace_last_action(injected_action(KIND_REFUSED, CHOSEN))
    assert agent.memory.memories == []


@pytest.mark.parametrize("kind, sentence", KINDS)
async def test_the_next_feedback_sees_the_back_that_ran_not_the_step_that_did_not(
        kind, sentence, monkeypatch):
    agent = await _agent_after_choosing()
    seen = []

    async def fake_chat(messages, **kwargs):
        seen.append(json.loads(messages[1]["content"]))
        return json.dumps({"thoughts": ["ok"]})

    class _NoApiTrace:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(agent_mod, "async_chat", fake_chat)
    monkeypatch.setattr(agent_mod, "LogApiCall", _NoApiTrace)

    running = experiment._inject_back(agent, kind, json.dumps(CHOSEN))
    await agent.feedback("<html></html>", last_action_error=None)

    (payload,) = seen
    last = json.loads(payload["last_action"])
    assert last["action"] == "back" and last["description"] == sentence
    assert "Warszawa" not in payload["last_action"] and "wyszukaj_lok" not in payload["last_action"]
    assert "intended" not in last
    # while what the executor runs, and the trace, still carry what was chosen
    assert json.loads(running)["intended"] == CHOSEN


async def test_inject_back_returns_the_action_to_run_and_to_record():
    agent = await _agent_after_choosing()
    running = json.loads(experiment._inject_back(agent, KIND_REFUSED, json.dumps(CHOSEN)))
    assert running == injected_action(KIND_REFUSED, CHOSEN)


# --- one rule for all three injections ------------------------------------------------

def _injection_blocks():
    """(kind constant, the statements it sits among) for each `action = _inject_back(...)`."""
    tree = ast.parse(inspect.getsource(experiment))
    found = []
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if not isinstance(block, list):
                continue
            for stmt in block:
                call = stmt.value if isinstance(stmt, ast.Assign) else None
                if isinstance(call, ast.Await):
                    call = call.value
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                        and call.func.id == "_inject_back"):
                    found.append((call.args[1].id, block))
    return found


def _sets_forced_escape(block):
    return any(
        isinstance(s, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "forced_escape"
                                          for t in s.targets)
        and isinstance(s.value, ast.Constant) and s.value.value is True
        for s in block
    )


def test_all_three_injections_hold_the_repeat_state():
    """The repeat counter and prev_action_key are held for a harness back, not
    just after the loop-breaker's: a back the persona did not choose is neither a
    repeat nor progress, whichever guard injected it."""
    blocks = _injection_blocks()
    assert sorted(kind for kind, _ in blocks) == sorted(
        ["KIND_LOOP_BREAKER", "KIND_FAILED_BREAKER", "KIND_REFUSED"])
    for kind, block in blocks:
        assert _sets_forced_escape(block), f"{kind} does not set forced_escape"


def test_the_loop_builds_no_back_of_its_own():
    src = inspect.getsource(experiment)
    assert '"action": "back"' not in src
