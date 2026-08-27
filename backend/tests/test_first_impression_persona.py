"""The 5-second first-impression test must be scored *as the persona*.

Grounded in a real defect: across 38 stored first_impressions spanning
pepco.pl and t-mobile.pl, 71% returned the exact dimension scores used as
the example in the prompt ([4,3,2,4,4] -> 3.4). Two causes, both covered here:

  1. the persona was never passed to the LLM, so every persona on a site was
     asked a byte-identical question and unsurprisingly got one answer;
  2. the prompt's output example carried plausible numbers, which the model
     copied instead of scoring the screenshot.
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from src.simulated_web_agent.agent.prompt_registry import registry as _prompts
from src.simulated_web_agent.main.auditor import _run_first_impression
from src.simulated_web_agent.main.trace_store import TraceStore

_REPLY = json.dumps({
    "overall_score": 2.6,
    "clarity_summary": "Podsumowanie.",
    "scores": {"purpose_clarity": 3, "navigation_visibility": 2,
               "call_to_action": 2, "visual_hierarchy": 3, "trustworthiness": 3},
    "strengths": ["a"], "weaknesses": ["b"],
})


def _store_with_screenshot(tmp_path, persona_card=None):
    store = TraceStore(tmp_path / "persona")
    shot = store.screenshot_path(0)
    shot.parent.mkdir(parents=True, exist_ok=True)
    shot.write_bytes(b"\x89PNG\r\n\x1a\n fake")
    store.write_basic_info({"site_context": "sklep"})
    if persona_card is not None:
        store.write_persona_card(persona_card)
    return store


def _sent_text(mock) -> str:
    """Concatenate the text parts of the user message actually sent."""
    messages = mock.await_args.args[0]
    parts = messages[0]["content"]
    return " ".join(p.get("text", "") for p in parts if isinstance(p, dict))


@pytest.mark.asyncio
async def test_persona_reaches_the_prompt(tmp_path):
    store = _store_with_screenshot(tmp_path, {
        "name": "Michał",
        "description": "Persona: Michał\nAge: 28\nProfession: marketer",
    })
    mock = AsyncMock(return_value=_REPLY)
    with patch("src.simulated_web_agent.main.auditor.async_chat", new=mock):
        await _run_first_impression(store)
    text = _sent_text(mock)
    assert "Michał" in text
    assert "marketer" in text


@pytest.mark.asyncio
async def test_two_personas_get_different_prompts(tmp_path):
    """The whole point of running N personas: they must not be asked the same
    question. Same site, same screenshot, different person."""
    sent = []

    async def capture(messages, **kw):
        sent.append(" ".join(p.get("text", "") for p in messages[0]["content"]
                             if isinstance(p, dict)))
        return _REPLY

    for name, desc in [("Michał", "Age: 28\nProfession: marketer"),
                       ("Marcin", "Age: 61\nProfession: emeryt")]:
        store = _store_with_screenshot(tmp_path / name,
                                       {"name": name, "description": desc})
        with patch("src.simulated_web_agent.main.auditor.async_chat", new=capture):
            await _run_first_impression(store)

    assert sent[0] != sent[1]


@pytest.mark.asyncio
async def test_missing_persona_card_still_scores(tmp_path):
    """Older runs have no persona_card.json; the pass must degrade, not crash."""
    store = _store_with_screenshot(tmp_path, persona_card=None)
    mock = AsyncMock(return_value=_REPLY)
    with patch("src.simulated_web_agent.main.auditor.async_chat", new=mock):
        result = await _run_first_impression(store)
    assert result["scores"]["purpose_clarity"] == 3


def test_safe_json_prefers_the_object_over_prose_arrays():
    """A long array in the model's reasoning must not beat the real object.
    Picking the longest match of any shape is how a bare weakness array got
    persisted as a first_impression and 500'd the report."""
    from src.simulated_web_agent.main.auditor import _safe_json

    raw = (
        'Reasoning. Weaknesses I considered: '
        '["bardzo dlugi element listy numer jeden", "bardzo dlugi element dwa", '
        '"bardzo dlugi element trzy", "bardzo dlugi element cztery"]\n'
        '{"overall_score": 2.6, "scores": {"purpose_clarity": 3}}'
    )
    assert _safe_json(raw, prefer=dict) == {
        "overall_score": 2.6, "scores": {"purpose_clarity": 3},
    }


@pytest.mark.asyncio
async def test_bare_array_reply_is_retried_then_accepted(tmp_path):
    """The bad shape is intermittent and does not reproduce on retry."""
    store = _store_with_screenshot(tmp_path, {"name": "X", "description": "d"})
    mock = AsyncMock(side_effect=['["tylko", "lista", "slabosci"]', _REPLY])
    with patch("src.simulated_web_agent.main.auditor.async_chat", new=mock):
        result = await _run_first_impression(store)
    assert mock.await_count == 2
    assert result["scores"]["purpose_clarity"] == 3


@pytest.mark.asyncio
async def test_persistently_bad_shape_degrades_rather_than_persisting(tmp_path):
    store = _store_with_screenshot(tmp_path, {"name": "X", "description": "d"})
    mock = AsyncMock(return_value='["tylko", "lista"]')
    with patch("src.simulated_web_agent.main.auditor.async_chat", new=mock):
        result = await _run_first_impression(store)
    assert mock.await_count == 2
    assert result["error"] == "unexpected_shape"


def test_prompt_example_carries_no_numeric_anchor():
    """The example must not hand the model a ready-made score to copy."""
    prompt = _prompts.get("first_impression")
    example = prompt[prompt.index("{{"):] if "{{" in prompt else prompt
    assert "3.4" not in example
    assert '"purpose_clarity": 4' not in example
