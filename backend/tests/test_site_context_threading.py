"""site_context must reach every judgement that assumes "this is a shop".

Grounded in run 2026-07-17_15-07-27: a panel of designers rated pepco.pl's
catalog-and-store-locator pages against an e-commerce frame the site was never
built to satisfy (purpose_clarity asks "what does the site SELL", worked
examples across three prompts are all cart/checkout bugs), and
objective_reflection — the pass that decides success/partial/failed — never
received site_context at all, so "no online checkout" could read as a failure
on a site that was never supposed to have one.

heuristic_evaluate and aggregate_synthesize already receive site_context
(auditor.py / reporter.py, pre-existing). These tests cover the two gaps:
first_impression and objective_reflection.
"""
from unittest.mock import AsyncMock, patch

import pytest

from src.simulated_web_agent.main.auditor import _run_first_impression
from src.simulated_web_agent.main.reporter import generate_objective_reflection
from src.simulated_web_agent.main.trace_store import TraceStore

_REPLY = '''{
  "overall_score": 3.0,
  "clarity_summary": "s",
  "scores": {"purpose_clarity": 3, "navigation_visibility": 3,
             "call_to_action": 3, "visual_hierarchy": 3, "trustworthiness": 3},
  "score_rationale": {"purpose_clarity": "r", "navigation_visibility": "r",
                       "call_to_action": "r", "visual_hierarchy": "r",
                       "trustworthiness": "r"},
  "persona_lens": "l", "strengths": ["a"], "weaknesses": ["b"]
}'''


def _store(tmp_path, site_context=""):
    store = TraceStore(tmp_path / "persona")
    shot = store.screenshot_path(0)
    shot.parent.mkdir(parents=True, exist_ok=True)
    shot.write_bytes(b"\x89PNG\r\n\x1a\n fake")
    store.write_basic_info({"site_context": site_context})
    store.write_persona_card({"name": "X", "description": "Persona: X\nAge: 30"})
    return store


def _sent_text(mock) -> str:
    messages = mock.await_args.args[0]
    return " ".join(p.get("text", "") for p in messages[0]["content"] if isinstance(p, dict))


@pytest.mark.asyncio
async def test_site_context_reaches_first_impression_prompt(tmp_path):
    store = _store(tmp_path, site_context="Katalog produktów bez zakupów online; wyszukiwarka sklepów stacjonarnych.")
    mock = AsyncMock(return_value=_REPLY)
    with patch("src.simulated_web_agent.main.auditor.async_chat", new=mock):
        await _run_first_impression(store)
    assert "bez zakupów online" in _sent_text(mock)


@pytest.mark.asyncio
async def test_missing_site_context_does_not_crash(tmp_path):
    store = _store(tmp_path, site_context="")
    mock = AsyncMock(return_value=_REPLY)
    with patch("src.simulated_web_agent.main.auditor.async_chat", new=mock):
        result = await _run_first_impression(store)
    assert result["scores"]["purpose_clarity"] == 3


# --- objective_reflection: decides success/partial/failed -----------------

_REFLECTION_REPLY = '{"outcome": "success", "summary": "s", "blockers": [], "wins": [], "priorityFix": "f"}'


def _reflection_store(tmp_path, site_context=""):
    store = TraceStore(tmp_path / "persona")
    store.write_basic_info({"site_context": site_context, "intent": "znajdz produkt"})
    store.write_persona_card({"name": "X", "objective": "znajdz produkt", "demographics": {}})
    store.write_heuristic_findings([])
    store.write_first_impression({"overall_score": 3, "clarity_summary": "s"})
    store.write_accessibility_findings({"overall_score": 3, "summary": "s"})
    store.write_survey_result({"answers": []})
    store.write_scroll_summary({})
    store.write_form_funnel({})
    return store


@pytest.mark.asyncio
async def test_site_context_reaches_reflection_prompt(tmp_path):
    """This pass decides success/partial/failed. Without site_context, a
    persona correctly stopping before a checkout that does not exist reads
    identically to one who got stuck — the model has no way to tell them apart."""
    store = _reflection_store(
        tmp_path, site_context="Katalog produktow bez zakupow online.")
    mock = AsyncMock(return_value=_REFLECTION_REPLY)
    with patch("src.simulated_web_agent.main.reporter.async_chat", new=mock):
        await generate_objective_reflection(store)
    sent = mock.await_args.args[0][1]["content"]
    assert "bez zakupow online" in sent


@pytest.mark.asyncio
async def test_missing_site_context_reflection_does_not_crash(tmp_path):
    store = _reflection_store(tmp_path, site_context="")
    mock = AsyncMock(return_value=_REFLECTION_REPLY)
    with patch("src.simulated_web_agent.main.reporter.async_chat", new=mock):
        result = await generate_objective_reflection(store)
    assert result["outcome"] == "success"
