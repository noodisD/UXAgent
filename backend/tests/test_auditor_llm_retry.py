"""Every audit pass must survive one unusable LLM reply.

The auditor's three LLM passes each ask for JSON of a known shape, and the model
intermittently returns something else — a bare array instead of the object, or a
transport error that outlives async_chat's own retry. It does not reproduce.

This is not hypothetical: in run 2026-07-16_13-41-14 a single bad reply left
Marcin with `overall_score: None` and zero accessibility findings, and the same
class of failure 500'd the whole report. Re-running the pass by hand succeeded
first try.

Only first_impression retried. These tests pin the behaviour for all three.
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from simulated_web_agent.main.auditor import (
    _run_accessibility_summary,
    _run_heuristic_evaluation,
)
from simulated_web_agent.main.trace_store import TraceStore

_FINDINGS = json.dumps([
    {"heuristic_id": 2, "severity": "poważny", "step": 0,
     "evidence": "e", "recommendation": "r", "confidence": "medium"},
])
_A11Y = json.dumps({"overall_score": 2, "summary": "s", "findings": [],
                    "font_size_issues": []})


def _store(tmp_path):
    store = TraceStore(tmp_path / "persona")
    store.write_basic_info({"site_context": "sklep"})
    store.write_memory_trace([{"kind": "reflection", "content": "myśl"}])
    store.write_action_trace([json.dumps({"action": "click", "target": "t",
                                          "description": "[step 0] klik"})])
    store.append_observation({"tabs": [{"url": "https://pepco.pl/"}]})
    # One scanned page: with none, the accessibility pass records no_evidence
    # without asking the model at all.
    store.write_a11y_snapshot(0, '- button "Koszyk"')
    return store


@pytest.mark.asyncio
async def test_heuristics_survive_one_bad_shape(tmp_path):
    """A stray object where a list belongs used to silently drop every finding
    — no error, no findings, a clean-looking empty audit."""
    mock = AsyncMock(side_effect=['{"nie": "lista"}', _FINDINGS])
    with patch("simulated_web_agent.main.auditor.async_chat", new=mock):
        result = await _run_heuristic_evaluation(_store(tmp_path))
    assert mock.await_count == 2
    assert len(result) == 1


@pytest.mark.asyncio
async def test_heuristics_survive_one_transport_error(tmp_path):
    mock = AsyncMock(side_effect=[RuntimeError("503 upstream"), _FINDINGS])
    with patch("simulated_web_agent.main.auditor.async_chat", new=mock):
        result = await _run_heuristic_evaluation(_store(tmp_path))
    assert len(result) == 1


@pytest.mark.asyncio
async def test_heuristics_degrade_to_empty_when_reply_never_usable(tmp_path):
    mock = AsyncMock(return_value='{"nie": "lista"}')
    with patch("simulated_web_agent.main.auditor.async_chat", new=mock):
        result = await _run_heuristic_evaluation(_store(tmp_path))
    assert mock.await_count == 2
    assert result == []


@pytest.mark.asyncio
async def test_accessibility_survives_one_bad_shape(tmp_path):
    """The exact failure that cost Marcin his accessibility findings."""
    mock = AsyncMock(side_effect=['["bare", "array"]', _A11Y])
    with patch("simulated_web_agent.main.auditor.async_chat", new=mock):
        result = await _run_accessibility_summary(_store(tmp_path))
    assert mock.await_count == 2
    assert result["overall_score"] == 2


@pytest.mark.asyncio
async def test_accessibility_survives_one_transport_error(tmp_path):
    mock = AsyncMock(side_effect=[RuntimeError("503 upstream"), _A11Y])
    with patch("simulated_web_agent.main.auditor.async_chat", new=mock):
        result = await _run_accessibility_summary(_store(tmp_path))
    assert result["overall_score"] == 2


@pytest.mark.asyncio
async def test_accessibility_reports_the_error_when_never_usable(tmp_path):
    """Degrading is fine; degrading *silently* is what caused the cascade."""
    mock = AsyncMock(return_value='["bare", "array"]')
    with patch("simulated_web_agent.main.auditor.async_chat", new=mock):
        result = await _run_accessibility_summary(_store(tmp_path))
    assert mock.await_count == 2
    assert "error" in result
