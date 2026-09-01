"""
Tests for run_all_analyses() in analysis_runner.py.

Behavior under test:
  - run_survey, run_audit_analysis, and run_viewport_scan are all called with the store
  - generate_per_agent_report is called after the parallel phase completes
  - the returned value is the per-agent report dict
"""
import pytest
from unittest.mock import AsyncMock, call, patch

from simulated_web_agent.main.analysis_runner import run_all_analyses
from simulated_web_agent.main.trace_store import TraceStore

_PATCH = "simulated_web_agent.main.analysis_runner.{}"


@pytest.mark.asyncio
async def test_all_three_analyses_are_called_with_store(store: TraceStore):
    """survey, audit, and viewport scan must each be called once with the store."""
    survey_mock = AsyncMock(return_value={"q1": "tak"})
    audit_mock = AsyncMock(return_value={})
    viewport_mock = AsyncMock(return_value=[])
    report_mock = AsyncMock(return_value={"heuristic_findings": []})

    with (
        patch(_PATCH.format("run_survey"), survey_mock),
        patch(_PATCH.format("run_audit_analysis"), audit_mock),
        patch(_PATCH.format("run_viewport_scan"), viewport_mock),
        patch(_PATCH.format("generate_per_agent_report"), report_mock),
    ):
        await run_all_analyses(store, questionnaire={"q1": "?"}, headless=True)

    survey_mock.assert_called_once_with(store, {"q1": "?"})
    audit_mock.assert_called_once_with(store)
    viewport_mock.assert_called_once_with(store, headless=True)


@pytest.mark.asyncio
async def test_per_agent_report_generated_after_analyses(store: TraceStore):
    """generate_per_agent_report must be called exactly once, after the parallel phase."""
    call_order: list[str] = []

    async def survey_side(*_a, **_kw):
        call_order.append("survey")

    async def audit_side(*_a, **_kw):
        call_order.append("audit")

    async def viewport_side(*_a, **_kw):
        call_order.append("viewport")

    expected_report = {"first_impression": {}, "heuristic_findings": []}

    async def report_side(*_a, **_kw):
        call_order.append("report")
        return expected_report

    with (
        patch(_PATCH.format("run_survey"), side_effect=survey_side),
        patch(_PATCH.format("run_audit_analysis"), side_effect=audit_side),
        patch(_PATCH.format("run_viewport_scan"), side_effect=viewport_side),
        patch(_PATCH.format("generate_per_agent_report"), side_effect=report_side),
    ):
        result = await run_all_analyses(store, questionnaire={}, headless=True)

    # report must come after all three analyses
    assert "report" in call_order
    assert call_order.index("report") > call_order.index("survey")
    assert call_order.index("report") > call_order.index("audit")
    assert call_order.index("report") > call_order.index("viewport")
    assert result == expected_report
