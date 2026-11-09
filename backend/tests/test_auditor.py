"""
Tests for run_audit_analysis() in auditor.py.

Behavior under test:
  5. first_impression.json is saved when the LLM returns valid JSON for the vision call
  6. heuristic_findings.json is saved as a list, and accessibility_findings.json is saved,
     even when LLM returns findings with the correct schema

The LLM boundary (async_chat) is mocked so tests are fast and deterministic.
Screenshots and memory traces are written as fixtures into tmp_path.
"""
import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from simulated_web_agent.main.auditor import run_audit_analysis
from simulated_web_agent.main.trace_store import TraceStore

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _route_mock(messages: list) -> str:
    """
    Determine which audit call is being made by inspecting message content.
    - First-impression: messages contain an image_url entry
    - Heuristic: user message text contains 'Nielsen' or 'Heuristic'
    - Accessibility: user message text contains 'accessibility' or 'a11y'
    """
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image_url":
                    return "first_impression"
        if isinstance(content, str):
            low = content.lower()
            if "nielsen" in low or "heuristic" in low:
                return "heuristic"
            if "accessibility" in low or "a11y" in low or "a11y snapshots" in low:
                return "accessibility"
    return "heuristic"  # safe fallback


_FIRST_IMPRESSION_RESPONSE = json.dumps({
    "overall_score": 4.2,
    "clarity_summary": "Strona jest przejrzysta i czytelna.",
    "scores": {
        "purpose_clarity": 5,
        "navigation_visibility": 4,
        "call_to_action": 3,
        "visual_hierarchy": 5,
        "trustworthiness": 4,
    },
    "strengths": ["Wyraźny nagłówek"],
    "weaknesses": ["Słaby kontrast przycisku CTA"],
})

_HEURISTIC_RESPONSE = json.dumps([
    {
        "heuristic_id": 3,
        "heuristic_name": "Kontrola użytkownika i swoboda działania",
        "severity": "poważny",
        "url": "https://example.com/cart",
        "step": 4,
        "evidence": "Brak przycisku powrotu.",
        "recommendation": "Dodać przycisk powrotu.",
    }
])

_ACCESSIBILITY_RESPONSE = json.dumps({
    "overall_score": 3,
    "summary": "Brakuje opisów alternatywnych dla obrazów.",
    "findings": [
        {
            "category": "Brak tekstu alternatywnego",
            "severity": "poważny",
            "url": "https://example.com/",
            "step": 1,
            "detail": "5 obrazów bez atrybutu alt.",
            "recommendation": "Dodać alt do wszystkich obrazów.",
        }
    ],
    "font_size_issues": [],
})


def _setup_store(store: TraceStore) -> None:
    """Write minimal fixtures that auditor.py reads."""
    # 1px white PNG as a stub screenshot
    png_1px = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwADhQGAWjR9awAAAABJRU5ErkJggg=="
    )
    store.screenshot_path(0).write_bytes(png_1px)

    memory = [
        {"kind": "observation", "content": "Homepage loaded.", "timestamp": 1, "importance": 0.5},
        {"kind": "reflection", "content": "Navigation is unclear.", "timestamp": 2, "importance": 0.7},
    ]
    store.write_memory_trace(memory)

    actions = [{"description": "Clicked the cart button.", "action": "click"}]
    store.write_action_trace(actions)
    # One scanned page, or the accessibility pass has nothing to score.
    store.write_a11y_snapshot(0, '- button "Koszyk"')


# ---------------------------------------------------------------------------
# Slice 5: first_impression.json is saved from LLM output
# ---------------------------------------------------------------------------

def _make_routed_mock():
    async def mock_chat(messages, **kwargs):
        kind = _route_mock(messages)
        if kind == "first_impression":
            return _FIRST_IMPRESSION_RESPONSE
        if kind == "heuristic":
            return _HEURISTIC_RESPONSE
        return _ACCESSIBILITY_RESPONSE
    return AsyncMock(side_effect=mock_chat)


@pytest.mark.asyncio
async def test_first_impression_json_saved_when_llm_returns_valid_json(store: TraceStore):
    _setup_store(store)

    with patch("simulated_web_agent.main.auditor.async_chat", new=_make_routed_mock()):
        await run_audit_analysis(store)

    fi = store.read_first_impression()
    assert fi is not None, "first_impression.json must be created"
    assert fi["overall_score"] == 4.2
    assert "strengths" in fi
    assert "weaknesses" in fi


# ---------------------------------------------------------------------------
# Slice 6a: heuristic_findings.json is saved as a list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_heuristic_findings_json_is_a_list_with_required_fields(store: TraceStore):
    _setup_store(store)

    with patch("simulated_web_agent.main.auditor.async_chat", new=_make_routed_mock()):
        await run_audit_analysis(store)

    findings = store.read_heuristic_findings()
    assert isinstance(findings, list), "heuristic_findings.json must be a JSON array"
    assert len(findings) == 1

    f = findings[0]
    for field in ("heuristic_id", "heuristic_name", "severity", "url", "step", "evidence", "recommendation"):
        assert field in f, f"Finding must contain field '{field}'"


# ---------------------------------------------------------------------------
# Slice 6b: accessibility_findings.json is saved
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_accessibility_findings_json_is_saved(store: TraceStore):
    _setup_store(store)

    with patch("simulated_web_agent.main.auditor.async_chat", new=_make_routed_mock()):
        await run_audit_analysis(store)

    a11y = store.read_accessibility_findings()
    assert a11y is not None, "accessibility_findings.json must be created"
    assert "overall_score" in a11y
    assert "findings" in a11y


# ---------------------------------------------------------------------------
# A persona's habits are not findings; information the site withholds is
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_finding_about_missing_material_composition_is_not_suppressed(store: TraceStore):
    """The heuristic prompt tells the model a persona's preference and the limits of the
    medium are not findings. The carve-out is that the site withholding what it could say
    (here: the fabric) stays reportable — and no code path may drop it afterwards."""
    from simulated_web_agent.main.severity import excluded_from_report

    _setup_store(store)
    store.write_basic_info({"persona": "Julia woli sklepy stacjonarne.", "intent": "Kup poduszkę"})
    for i in range(3):
        store.append_observation({"tabs": [{"url": "https://pepco.pl/products/poduszka-1", "is_active": True}]})
        store.write_observation_step_text(
            i, "Poduszka dekoracyjna. Kolory: beżowy. Rozmiary: ONE. Szczegóły. 20 zł.")
    finding = {
        "heuristic_id": 2, "heuristic_name": "Zgodność systemu z rzeczywistością",
        "severity": "poważny", "url": "https://pepco.pl/products/poduszka-1", "step": 1,
        "evidence": "Strona produktu nie podaje składu materiału ani wymiarów poduszki.",
        "recommendation": "Dodać skład materiału i wymiary.", "confidence": "medium",
    }

    async def mock_chat(messages, **kwargs):
        kind = _route_mock(messages)
        if kind == "first_impression":
            return _FIRST_IMPRESSION_RESPONSE
        if kind == "heuristic":
            return json.dumps([finding])
        return _ACCESSIBILITY_RESPONSE

    mock = AsyncMock(side_effect=mock_chat)
    verifier = AsyncMock(return_value=json.dumps(
        {"verdicts": [{"id": 0, "verdict": "supported", "reason": "Obserwacja nie podaje składu."}]}))
    with patch("simulated_web_agent.main.auditor.async_chat", new=mock),             patch("simulated_web_agent.main.claim_verifier.async_chat", new=verifier):
        await run_audit_analysis(store)

    prompt = next(c.args[0][0]["content"] for c in mock.await_args_list
                  if isinstance(c.args[0][0]["content"], str) and "Nielsen" in c.args[0][0]["content"])
    assert "Julia woli sklepy stacjonarne." in prompt
    assert "persona's own preference" in prompt and "inherent to the medium" in prompt
    kept = store.read_heuristic_findings()
    assert len(kept) == 1 and "składu materiału" in kept[0]["evidence"]
    assert not excluded_from_report(kept[0])
    assert not kept[0].get("harness_suspect") and not kept[0].get("off_site")
